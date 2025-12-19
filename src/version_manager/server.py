from __future__ import annotations

import argparse
import json
import os
import time
import ipaddress
import threading
import urllib.request
import urllib.error
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .db import Database, DeviceAuth
from .poller import poll_device


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid_json:{e}") from e


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _send_html(handler: BaseHTTPRequestHandler, status: int, html: str) -> None:
    data = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)

def _redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
    handler.send_response(302)
    handler.send_header("Location", location)
    handler.end_headers()


def _path_parts(path: str) -> List[str]:
    return [p for p in path.split("/") if p]

def _parse_cookie(header: Optional[str]) -> Dict[str, str]:
    if not header:
        return {}
    out: Dict[str, str] = {}
    for part in header.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out

def _set_cookie(handler: BaseHTTPRequestHandler, name: str, value: str, *, max_age: Optional[int] = None) -> None:
    parts = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Strict"]
    if max_age is not None:
        parts.append(f"Max-Age={int(max_age)}")
    handler.send_header("Set-Cookie", "; ".join(parts))


def _parse_dvp_url(url: str) -> Optional[Dict[str, Any]]:
    try:
        p = urlparse(url)
    except Exception:
        return None
    if p.scheme not in ("http",):
        return None
    if not p.hostname:
        return None
    port = int(p.port or 80)
    path = p.path or "/.well-known/device-version"
    return {"ip": p.hostname, "port": port, "path": path, "protocol": "dvp1-http"}


def _infer_from_dvp(payload: Dict[str, Any]) -> Dict[str, str]:
    device_obj = payload.get("device", {}) if isinstance(payload, dict) else {}
    out: Dict[str, str] = {}
    if isinstance(device_obj, dict):
        did = device_obj.get("id")
        if isinstance(did, str) and did.strip():
            out["device_id"] = did.strip()
        vendor = device_obj.get("vendor")
        if isinstance(vendor, str) and vendor.strip():
            out["vendor"] = vendor.strip()
        model = device_obj.get("model")
        if isinstance(model, str) and model.strip():
            out["model"] = model.strip()
    return out


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>设备版本管理器</title>
  <style>
    :root{
      --bg:#f6f7fb;
      --surface:#ffffff;
      --surface2:#fbfcff;
      --text:#1b2430;
      --muted:#5a677a;
      --border:rgba(16,24,40,.10);
      --shadow:0 10px 28px rgba(16,24,40,.08);
      --ok:#2e7d32;
      --warn:#ed6c02;
      --bad:#d32f2f;
      --info:#3f51b5;
      --primary:#3b82f6;
      --primary2:#60a5fa;
      --btn:#ffffff;
      --btnText:#1b2430;
      --focus:rgba(59,130,246,.18);
    }
    *{ box-sizing:border-box; }
    body{
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      margin: 0; padding: 0;
      background:
        radial-gradient(1200px 800px at 20% 0%, rgba(96,165,250,.25) 0%, rgba(246,247,251,0) 55%),
        radial-gradient(900px 600px at 90% 10%, rgba(59,130,246,.16) 0%, rgba(246,247,251,0) 60%),
        var(--bg);
      color: var(--text);
    }
    a{ color: inherit; }
    /* Dashboard is operational UI: prefer using available screen width */
    .wrap{ width: calc(100% - 24px); max-width: none; margin: 0 auto; padding: 18px; }
    .top{
      display:flex; align-items:center; justify-content:space-between; gap:12px;
      padding: 14px 16px; border:1px solid var(--border); border-radius:14px;
      background: rgba(255,255,255,.72);
      box-shadow: var(--shadow);
      position: sticky; top: 12px; backdrop-filter: blur(8px);
    }
    .title{ display:flex; flex-direction:column; gap:2px; }
    h1{ margin:0; font-size:18px; letter-spacing:.2px; }
    .meta{ color:var(--muted); font-size:12px; }
    .right{ display:flex; align-items:center; gap:8px; }
    .pill{
      padding:6px 10px; border:1px solid var(--border); border-radius:999px;
      background: rgba(255,255,255,.9); color: var(--muted); font-size:12px;
    }
    .btn{
      padding:8px 12px; border-radius:10px;
      border:1px solid var(--border);
      background: var(--btn);
      color: var(--btnText); cursor:pointer;
      transition: transform .05s ease, border-color .15s ease;
    }
    .btn:active{ transform: translateY(1px); }
    .btn:focus{ outline:none; box-shadow:0 0 0 6px var(--focus); }
    .btn.primary{ border-color: rgba(59,130,246,.30); background: linear-gradient(180deg, rgba(59,130,246,.12), rgba(59,130,246,.06)); }
    .btn.danger{ border-color: rgba(211,47,47,.25); background: linear-gradient(180deg, rgba(211,47,47,.10), rgba(211,47,47,.04)); }
    .btn.ghost{ background: rgba(255,255,255,.85); }
    input, select{
      padding:8px 10px; border-radius:10px; border:1px solid var(--border);
      background: rgba(255,255,255,.95); color: var(--text);
    }
    input:focus, select:focus{ outline:none; box-shadow:0 0 0 6px var(--focus); }
    .grid{ display:grid; grid-template-columns: 1fr; gap:14px; margin-top:14px; }
    @media(min-width: 980px){ .grid{ grid-template-columns: 2fr 1fr; } }
    .card{
      border:1px solid var(--border); border-radius:14px;
      background: var(--surface);
      box-shadow: var(--shadow);
      overflow:hidden;
    }
    .card h2{ margin:0; font-size:14px; color: var(--text); }
    .card .hd{
      display:flex; align-items:center; justify-content:space-between; gap:10px;
      padding: 12px 14px; border-bottom:1px solid var(--border);
    }
    .card .bd{ padding: 12px 14px; }
    table{ width:100%; border-collapse: collapse; }
    th, td{ padding:10px 10px; border-bottom: 1px solid var(--border); font-size:13px; vertical-align: top; }
    th{ color: var(--muted); font-weight:600; text-align:left; }
    tr:hover td{ background: rgba(59,130,246,.04); }
    .scroll-x{ overflow:auto; }
    .device-table{ min-width: 1160px; }
    .device-table th, .device-table td{ white-space: nowrap; }
    .device-table td.err{ white-space: normal; max-width: 360px; }
    .badge{ display:inline-block; padding:3px 10px; border-radius:999px; font-size:12px; border:1px solid var(--border); background: rgba(255,255,255,.7); }
    .b-ok{ border-color: rgba(46,125,50,.20); background: rgba(46,125,50,.08); color: var(--ok); }
    .b-mismatch{ border-color: rgba(211,47,47,.20); background: rgba(211,47,47,.08); color: var(--bad); }
    .b-offline{ border-color: rgba(237,108,2,.20); background: rgba(237,108,2,.08); color: var(--warn); }
    .b-no_baseline, .b-never_polled, .b-unknown{ border-color: rgba(63,81,181,.20); background: rgba(63,81,181,.08); color: var(--info); }
    .mono{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .row{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .small{ font-size:12px; color: var(--muted); }
    .muted{ color: var(--muted); }
    .kpi{ display:flex; gap:10px; flex-wrap:wrap; }
    .kpi .box{ padding:10px 12px; border:1px solid var(--border); border-radius:12px; background: var(--surface2); }
    .kpi .n{ font-size:18px; font-weight:700; }
    .kpi .l{ font-size:12px; color: var(--muted); }
    dialog{
      width: min(720px, calc(100% - 24px));
      border:1px solid var(--border); border-radius:14px;
      background: var(--surface);
      color: var(--text); box-shadow: var(--shadow);
      padding: 0;
    }
    dialog::backdrop{ background: rgba(16,24,40,.35); }
    .dlg-hd{ padding: 12px 14px; border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; }
    .dlg-bd{ padding: 12px 14px; }
    .dlg-ft{ padding: 12px 14px; border-top:1px solid var(--border); display:flex; justify-content:flex-end; gap:8px; }
    .field{ display:flex; flex-direction:column; gap:6px; margin-bottom:10px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="title">
        <h1>设备版本管理器</h1>
        <div class="meta">拉取设备版本（DVP v1），按集群基线判定一致性，并记录事件。</div>
      </div>
      <div class="right">
        <span class="pill" id="who"></span>
        <button class="btn ghost" id="logoutBtn">退出</button>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="hd">
          <h2>设备状态</h2>
          <div class="row">
            <button class="btn" id="pollAll">拉取全部</button>
            <span class="pill mono" id="lastUpdate"></span>
          </div>
        </div>
        <div class="bd">
          <div class="kpi" id="kpi"></div>
          <div class="scroll-x" style="margin-top:10px;">
            <table class="device-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>DeviceKey</th>
                  <th>产线号</th>
                  <th>IP</th>
                  <th>状态</th>
                  <th>基线</th>
                  <th>当前</th>
                  <th>错误</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody id="rows"></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="hd">
          <h2>配置</h2>
          <span class="small">管理员可修改</span>
        </div>
        <div class="bd">
          <div class="field">
            <label class="small">当前集群</label>
            <div class="row">
              <select id="clusterSelect"></select>
              <button class="btn ghost" id="reloadBtn">刷新</button>
            </div>
          </div>

          <div class="field">
            <label class="small">新建集群</label>
            <div class="row">
              <input id="newClusterName" placeholder="集群名称" />
              <button class="btn" id="createCluster">新建</button>
            </div>
          </div>

          <div class="field">
            <label class="small">自动发现（hosts 或 CIDR）</label>
            <input id="discoverHosts" class="mono" placeholder="hosts 逗号分隔，如 192.168.10.21,192.168.10.22" />
            <input id="discoverCidr" class="mono" placeholder="CIDR 如 192.168.10.0/24" />
            <div class="row">
              <input id="discoverPort" class="mono" placeholder="port" value="80" style="width:100px;" />
              <input id="discoverLineNo" placeholder="产线号(可选)" style="width:160px;" />
              <button class="btn" id="discoverBtn">发现并写入</button>
              <span class="small" id="discoverOut"></span>
            </div>
          </div>

          <div class="field">
            <div class="row" style="justify-content:space-between;">
              <div>
                <div style="font-weight:700; margin-bottom:4px;">基线</div>
                <div class="small muted">按 cluster + vendor + model 管理，可设置允许范围（如 1.8.*）。</div>
              </div>
              <button class="btn" id="addBaselineBtn">新增/修改</button>
            </div>
            <div style="overflow:auto; margin-top:10px;">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Vendor/Model</th>
                    <th>期望</th>
                    <th>允许范围</th>
                    <th>备注</th>
                  </tr>
                </thead>
                <tbody id="baselines"></tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:14px;">
      <div class="hd">
        <h2>最近事件</h2>
        <span class="small">状态变化会记录为事件（可选 Webhook 推送）</span>
      </div>
      <div class="bd" style="overflow:auto;">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>设备ID</th>
              <th>类型</th>
              <th>旧状态</th>
              <th>新状态</th>
              <th>信息</th>
            </tr>
          </thead>
          <tbody id="events"></tbody>
        </table>
      </div>
    </div>
  </div>

  <dialog id="baselineDlg">
    <div class="dlg-hd">
      <div style="font-weight:700;">新增/修改基线</div>
      <button class="btn ghost" id="closeBaselineDlg">关闭</button>
    </div>
    <div class="dlg-bd">
      <div class="field">
        <label class="small">集群</label>
        <select id="baselineCluster"></select>
      </div>
      <div class="field">
        <label class="small">Vendor</label>
        <input id="baselineVendor" placeholder="VendorX" />
      </div>
      <div class="field">
        <label class="small">Model</label>
        <input id="baselineModel" placeholder="VisionStation-3" />
      </div>
      <div class="field">
        <label class="small">期望主版本</label>
        <input id="baselineExpected" class="mono" placeholder="1.8.2" />
      </div>
      <div class="field">
        <label class="small">允许范围（可选，逗号分隔，如 1.8.*）</label>
        <input id="baselineGlobs" class="mono" placeholder="1.8.*" />
      </div>
      <div class="field">
        <label class="small">备注</label>
        <input id="baselineNote" placeholder="灰度/原因/生效说明" />
      </div>
      <div class="small" id="baselineDlgOut"></div>
    </div>
    <div class="dlg-ft">
      <button class="btn ghost" id="baselineCancel">取消</button>
      <button class="btn" id="baselineSave">保存</button>
    </div>
  </dialog>

  <dialog id="deviceDlg">
    <div class="dlg-hd">
      <div style="font-weight:700;">设备详情</div>
      <button class="btn ghost" id="closeDeviceDlg">关闭</button>
    </div>
    <div class="dlg-bd">
      <div class="row" style="justify-content:space-between;">
        <div>
          <div style="font-weight:700;" id="dTitle"></div>
          <div class="small muted" id="dSub"></div>
        </div>
        <div class="pill mono" id="dState"></div>
      </div>
      <div style="height:10px;"></div>
      <div class="field">
        <label class="small">产线号（可编辑）</label>
        <input id="dLineNo" placeholder="例如 Line-01 / A1" />
      </div>
      <div class="row">
        <div class="pill mono" id="dIp"></div>
        <div class="pill mono" id="dProto"></div>
        <div class="pill mono" id="dPath"></div>
      </div>
      <div style="height:10px;"></div>
      <div class="field">
        <label class="small">Vendor / Model</label>
        <div class="pill" id="dVendorModel"></div>
      </div>
      <div class="field">
        <label class="small">最近拉取</label>
        <div class="pill mono" id="dObservedAt"></div>
      </div>
      <div class="field">
        <label class="small">基线</label>
        <div class="pill mono" id="dBaseline"></div>
      </div>
      <div class="field">
        <label class="small">当前版本</label>
        <div class="pill mono" id="dObserved"></div>
      </div>
      <div class="field">
        <label class="small">错误</label>
        <div class="pill" id="dErr"></div>
      </div>
      <details>
        <summary class="small">原始返回 JSON</summary>
        <pre class="mono" id="dRaw" style="white-space:pre-wrap; border:1px solid var(--border); border-radius:12px; padding:10px; background: var(--surface2);"></pre>
      </details>
      <div class="small" id="dOut"></div>
    </div>
    <div class="dlg-ft">
      <button class="btn ghost" id="deviceCancel">取消</button>
      <button class="btn primary" id="deviceSave">保存</button>
    </div>
  </dialog>
<script>
const badge = (state) => {
  const cls = "badge b-" + state;
  return `<span class="${cls}">${state}</span>`;
}
const fmt = (s) => s ? s : "";
const esc = (s) => String(s ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");
let currentDetailId = null;
async function apiFetch(url, opts){
  const res = await fetch(url, opts);
  if(res.status === 401){
    location.href = "/login";
    throw new Error("unauthorized");
  }
  return res;
}
async function load() {
  await loadClusters();
  const res = await apiFetch("/api/v1/status");
  const data = await res.json();
  const tbody = document.getElementById("rows");
  tbody.innerHTML = "";
  let counts = {};
  for (const row of data.items) {
    counts[row.state] = (counts[row.state] || 0) + 1;
    const dev = row.device;
    const base = row.baseline;
    const snap = row.latest_snapshot;
    const expected = base ? base.expected_main_version : "";
    const observed = snap ? (snap.main_version || "") : "";
    const err = snap ? (snap.error || "") : "";
    const tr = document.createElement("tr");
    tr.className = row.state;
    tr.innerHTML = `
      <td class="mono">${esc(dev.id)}</td>
      <td>${esc(fmt(dev.device_key))}</td>
      <td class="mono">${esc(fmt(dev.line_no || ""))}</td>
      <td class="mono">${esc(fmt(dev.ip))}:${esc(fmt(dev.port))}</td>
      <td>${badge(row.state)}</td>
      <td class="mono">${esc(expected)}</td>
      <td class="mono">${esc(observed)}</td>
      <td class="err" title="${esc(err)}">${esc(fmt(err))}</td>
      <td>
        <button class="btn ghost" data-act="detail" data-id="${dev.id}">详情</button>
        <button class="btn ghost" data-act="toggle" data-id="${dev.id}" data-enabled="${dev.enabled}">${dev.enabled ? "停用" : "启用"}</button>
        <button class="btn danger" data-act="delete" data-id="${dev.id}">删除</button>
      </td>
    `;
    tbody.appendChild(tr);
  }
  const parts = Object.entries(counts).map(([k,v]) => `${k}:${v}`).join("  ");
  renderKpi(counts);
  document.getElementById("lastUpdate").textContent = new Date().toISOString();
  await loadEvents();
  await loadBaselines();
  await loadMe();
}

function renderKpi(counts){
  const el = document.getElementById("kpi");
  const mk = (label, value) => `<div class="box"><div class="n">${value||0}</div><div class="l">${label}</div></div>`;
  el.innerHTML = [
    mk("ok", counts.ok),
    mk("mismatch", counts.mismatch),
    mk("offline", counts.offline),
    mk("no_baseline", counts.no_baseline),
    mk("never_polled", counts.never_polled),
  ].join("");
}

async function loadMe(){
  try{
    const res = await apiFetch("/api/v1/me");
    if(!res.ok){ document.getElementById("who").textContent = "未登录"; return; }
    const data = await res.json();
    const u = data.user || {};
    document.getElementById("who").textContent = `${u.username||''} (${u.role||''})`;
  }catch{
    document.getElementById("who").textContent = "未登录";
  }
}

async function loadEvents() {
  const tbody = document.getElementById("events");
  const res = await apiFetch("/api/v1/events?limit=30");
  const data = await res.json();
  tbody.innerHTML = "";
  for (const ev of data.items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${fmt(ev.created_at)}</td>
      <td class="mono">${fmt(ev.device_id)}</td>
      <td>${fmt(ev.event_type)}</td>
      <td>${fmt(ev.old_state)}</td>
      <td>${fmt(ev.new_state)}</td>
      <td>${fmt(ev.message)}</td>
    `;
    tbody.appendChild(tr);
  }
}

async function toggleDevice(id, enabled) {
  await apiFetch(`/api/v1/devices/${id}`, {
    method: "PUT",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({enabled: !enabled}),
  });
}

async function deleteDevice(id) {
  await apiFetch(`/api/v1/devices/${id}`, { method: "DELETE" });
}

async function openDeviceDlg(id){
  currentDetailId = id;
  document.getElementById("dOut").textContent = "";
  document.getElementById("dRaw").textContent = "";
  const dlg = document.getElementById("deviceDlg");
  dlg.showModal();
  const res = await apiFetch(`/api/v1/devices/${id}`);
  const data = await res.json();
  const dev = data.device || {};
  const base = data.baseline || null;
  const snap = data.latest_snapshot || null;
  document.getElementById("dTitle").textContent = `${dev.device_key || ""} (#${dev.id || ""})`;
  document.getElementById("dSub").textContent = `集群 ${dev.cluster_id || ""}`;
  document.getElementById("dState").textContent = dev.last_state || "unknown";
  document.getElementById("dLineNo").value = dev.line_no || "";
  document.getElementById("dIp").textContent = `${dev.ip || ""}:${dev.port || ""}`;
  document.getElementById("dProto").textContent = dev.protocol || "";
  document.getElementById("dPath").textContent = dev.path || "";
  document.getElementById("dVendorModel").textContent = `${dev.vendor || ""} / ${dev.model || ""}`;
  document.getElementById("dObservedAt").textContent = snap ? (snap.observed_at || "") : "";
  const expected = base ? (base.expected_main_version || "") : "";
  const globs = base ? ((base.allowed_main_globs || []).join(", ")) : "";
  document.getElementById("dBaseline").textContent = expected ? (globs ? `${expected} (允许: ${globs})` : expected) : "未设置";
  document.getElementById("dObserved").textContent = snap ? (snap.main_version || "") : "";
  document.getElementById("dErr").textContent = snap ? (snap.error || "") : "";
  const raw = snap && snap.payload ? snap.payload : null;
  document.getElementById("dRaw").textContent = raw ? JSON.stringify(raw, null, 2) : "";
}

async function saveDeviceDetail(){
  const out = document.getElementById("dOut");
  out.textContent = "";
  const id = currentDetailId;
  if(!id){ return; }
  const line_no = document.getElementById("dLineNo").value.trim();
  const res = await apiFetch(`/api/v1/devices/${id}`, { method:"PUT", headers:{"Content-Type":"application/json"}, body: JSON.stringify({line_no}) });
  const data = await res.json();
  if(!res.ok){ out.textContent = data.error || "保存失败"; return; }
  document.getElementById("deviceDlg").close();
  await load();
}

async function loadClusters() {
  const sel = document.getElementById("clusterSelect");
  const sel2 = document.getElementById("baselineCluster");
  const res = await apiFetch("/api/v1/clusters");
  const data = await res.json();
  const current = sel.value;
  sel.innerHTML = "";
  sel2.innerHTML = "";
  for (const c of data.items) {
    const opt = document.createElement("option");
    opt.value = String(c.id);
    opt.textContent = `${c.id} - ${c.name}`;
    sel.appendChild(opt);
    const opt2 = document.createElement("option");
    opt2.value = String(c.id);
    opt2.textContent = `${c.id} - ${c.name}`;
    sel2.appendChild(opt2);
  }
  if (current) sel.value = current;
}

async function createCluster() {
  const name = document.getElementById("newClusterName").value.trim();
  if (!name) return;
  document.getElementById("createCluster").disabled = true;
  try {
    const res = await apiFetch("/api/v1/clusters", { method:"POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({name}) });
    if(!res.ok){ const data = await res.json(); alert(data.error || "新建失败"); return; }
    document.getElementById("newClusterName").value = "";
    await load();
  } finally {
    document.getElementById("createCluster").disabled = false;
  }
}

async function discover() {
  const out = document.getElementById("discoverOut");
  const clusterId = Number(document.getElementById("clusterSelect").value || "0");
  const hostsRaw = document.getElementById("discoverHosts").value.trim();
  const cidr = document.getElementById("discoverCidr").value.trim();
  const port = Number(document.getElementById("discoverPort").value || "80");
  const lineNo = document.getElementById("discoverLineNo").value.trim();
  let body = { cluster_id: clusterId, port };
  if (lineNo) body.line_no = lineNo;
  if (hostsRaw) body.hosts = hostsRaw.split(",").map(s => s.trim()).filter(Boolean);
  else body.cidr = cidr;
  if (!body.hosts && !body.cidr) { out.textContent = "请填 hosts 或 CIDR"; return; }
  document.getElementById("discoverBtn").disabled = true;
  out.textContent = "扫描中...";
  try {
    const res = await apiFetch("/api/v1/discover", { method:"POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) { out.textContent = data.error || "失败"; return; }
    out.textContent = `targets:${data.targets} created:${data.created} updated:${data.updated}`;
    await load();
  } finally {
    document.getElementById("discoverBtn").disabled = false;
  }
}

async function loadBaselines(){
  const tbody = document.getElementById("baselines");
  const clusterId = document.getElementById("clusterSelect").value;
  const res = await apiFetch(`/api/v1/baselines?cluster_id=${encodeURIComponent(clusterId)}`);
  const data = await res.json();
  tbody.innerHTML = "";
  for (const b of data.items) {
    const globs = (b.allowed_main_globs || []).join(", ");
    const tr = document.createElement("tr");
    tr.setAttribute("data-baseline", JSON.stringify(b));
    tr.innerHTML = `
      <td class="mono">${b.id}</td>
      <td>${fmt(b.vendor)}/${fmt(b.model)}</td>
      <td class="mono">${fmt(b.expected_main_version)}</td>
      <td class="mono">${fmt(globs)}</td>
      <td>${fmt(b.note)}</td>
    `;
    tbody.appendChild(tr);
  }
}

function openBaselineDlg(b){
  document.getElementById("baselineDlgOut").textContent = "";
  document.getElementById("baselineCluster").value = document.getElementById("clusterSelect").value;
  document.getElementById("baselineVendor").value = b ? (b.vendor || "") : "";
  document.getElementById("baselineModel").value = b ? (b.model || "") : "";
  document.getElementById("baselineExpected").value = b ? (b.expected_main_version || "") : "";
  document.getElementById("baselineGlobs").value = b ? ((b.allowed_main_globs || []).join(", ")) : "";
  document.getElementById("baselineNote").value = b ? (b.note || "") : "";
  document.getElementById("baselineDlg").showModal();
}

async function saveBaseline(){
  const out = document.getElementById("baselineDlgOut");
  const cluster_id = Number(document.getElementById("baselineCluster").value || "0");
  const vendor = document.getElementById("baselineVendor").value.trim();
  const model = document.getElementById("baselineModel").value.trim();
  const expected_main_version = document.getElementById("baselineExpected").value.trim();
  const globsRaw = document.getElementById("baselineGlobs").value.trim();
  const note = document.getElementById("baselineNote").value.trim();
  const allowed_main_globs = globsRaw ? globsRaw.split(",").map(s => s.trim()).filter(Boolean) : [];
  const body = {cluster_id, vendor, model, expected_main_version, allowed_main_globs, note};
  const res = await apiFetch("/api/v1/baselines", { method:"POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) });
  const data = await res.json();
  if(!res.ok){ out.textContent = data.error || "保存失败"; return; }
  document.getElementById("baselineDlg").close();
  await load();
}

async function logout(){
  await apiFetch("/api/v1/logout", { method:"POST", headers: {"Content-Type":"application/json"}, body:"{}" });
  location.href = "/login";
}

async function pollAll() {
  document.getElementById("pollAll").disabled = true;
  try {
    await fetch("/api/v1/poll", { method:"POST", headers: {"Content-Type":"application/json"}, body: "{}" });
  } finally {
    document.getElementById("pollAll").disabled = false;
    await load();
  }
}
document.getElementById("pollAll").addEventListener("click", pollAll);
document.getElementById("createCluster").addEventListener("click", createCluster);
document.getElementById("discoverBtn").addEventListener("click", discover);
document.getElementById("reloadBtn").addEventListener("click", load);
document.getElementById("addBaselineBtn").addEventListener("click", () => openBaselineDlg(null));
document.getElementById("closeBaselineDlg").addEventListener("click", () => document.getElementById("baselineDlg").close());
document.getElementById("baselineCancel").addEventListener("click", () => document.getElementById("baselineDlg").close());
document.getElementById("baselineSave").addEventListener("click", saveBaseline);
document.getElementById("logoutBtn").addEventListener("click", logout);
document.getElementById("closeDeviceDlg").addEventListener("click", () => document.getElementById("deviceDlg").close());
document.getElementById("deviceCancel").addEventListener("click", () => document.getElementById("deviceDlg").close());
document.getElementById("deviceSave").addEventListener("click", saveDeviceDetail);
document.getElementById("baselines").addEventListener("click", (ev) => {
  const tr = ev.target.closest("tr");
  if(!tr) return;
  const raw = tr.getAttribute("data-baseline");
  if(!raw) return;
  try { openBaselineDlg(JSON.parse(raw)); } catch {}
});
document.getElementById("rows").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button");
  if (!btn) return;
  const act = btn.getAttribute("data-act");
  const id = btn.getAttribute("data-id");
  if (!act || !id) return;
  if (act === "detail") {
    btn.disabled = true;
    try { await openDeviceDlg(id); } finally { btn.disabled = false; }
  } else if (act === "toggle") {
    const enabled = btn.getAttribute("data-enabled") === "1";
    btn.disabled = true;
    try { await toggleDevice(id, enabled); } finally { await load(); }
  } else if (act === "delete") {
    if (!confirm(`确认删除设备 ${id}？（会同时删除历史拉取记录）`)) return;
    btn.disabled = true;
    try { await deleteDevice(id); } finally { await load(); }
  }
});
load();
setInterval(load, 10000);
</script>
</body>
</html>"""


def _setup_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>初始化 - 设备版本管理器</title>
  <style>
    body{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin:0; min-height:100vh;
      background:
        radial-gradient(1000px 700px at 20% 0%, rgba(96,165,250,.30) 0%, rgba(246,247,251,0) 55%),
        radial-gradient(900px 600px at 90% 10%, rgba(59,130,246,.18) 0%, rgba(246,247,251,0) 60%),
        #f6f7fb;
      color:#1b2430; display:flex; align-items:center; justify-content:center; }
    .card{ width:min(520px, calc(100% - 24px)); border:1px solid rgba(16,24,40,.10); border-radius:16px;
      background: rgba(255,255,255,.86); backdrop-filter: blur(10px);
      box-shadow:0 10px 28px rgba(16,24,40,.08); padding:16px; }
    h1{ margin:0 0 6px 0; font-size:18px; }
    .muted{ color:#5a677a; font-size:12px; margin-bottom:12px; }
    input{ width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(16,24,40,.10); background: rgba(255,255,255,.95); color:#1b2430; }
    input:focus{ outline:none; box-shadow:0 0 0 6px rgba(59,130,246,.18); }
    .row{ display:flex; gap:10px; }
    .btn{ padding:10px 12px; border-radius:12px; border:1px solid rgba(16,24,40,.10);
      background: linear-gradient(180deg, rgba(59,130,246,.12), rgba(59,130,246,.06)); color:#1b2430; cursor:pointer; }
    .btn:focus{ outline:none; box-shadow:0 0 0 6px rgba(59,130,246,.18); }
    .field{ display:flex; flex-direction:column; gap:6px; margin-bottom:10px; }
    .label{ font-size:12px; color:#5a677a; }
  </style>
</head>
<body>
  <div class="card">
    <h1>初始化管理员账号</h1>
    <div class="muted">首次使用需要创建管理员账号（密码至少 8 位）。</div>
    <div class="field">
      <div class="label">用户名</div>
      <input id="u" value="admin" />
    </div>
    <div class="field">
      <div class="label">密码</div>
      <input id="p" type="password" placeholder="至少 8 位" />
    </div>
    <div class="row">
      <button class="btn" id="go">创建并进入登录</button>
      <div id="out" class="muted" style="align-self:center;"></div>
    </div>
  </div>
<script>
document.getElementById("go").addEventListener("click", async () => {
  const out = document.getElementById("out");
  out.textContent = "";
  const username = document.getElementById("u").value.trim();
  const password = document.getElementById("p").value;
  const res = await fetch("/api/v1/setup", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({username, password})});
  const data = await res.json();
  if(!res.ok){ out.textContent = data.error || "失败"; return; }
  location.href = "/login";
});
</script>
</body>
</html>"""


def _login_html(*, setup_needed: bool) -> str:
    hint = "先访问 /setup 初始化管理员" if setup_needed else "请输入账号密码"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>登录 - 设备版本管理器</title>
  <style>
    body{{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin:0; min-height:100vh;
      background:
        radial-gradient(1000px 700px at 20% 0%, rgba(96,165,250,.30) 0%, rgba(246,247,251,0) 55%),
        radial-gradient(900px 600px at 90% 10%, rgba(59,130,246,.18) 0%, rgba(246,247,251,0) 60%),
        #f6f7fb;
      color:#1b2430; display:flex; align-items:center; justify-content:center; }}
    .card{{ width:min(520px, calc(100% - 24px)); border:1px solid rgba(16,24,40,.10); border-radius:16px;
      background: rgba(255,255,255,.86); backdrop-filter: blur(10px);
      box-shadow:0 10px 28px rgba(16,24,40,.08); padding:16px; }}
    h1{{ margin:0 0 6px 0; font-size:18px; }}
    .muted{{ color:#5a677a; font-size:12px; margin-bottom:12px; }}
    input{{ width:100%; padding:10px 12px; border-radius:12px; border:1px solid rgba(16,24,40,.10); background: rgba(255,255,255,.95); color:#1b2430; }}
    input:focus{{ outline:none; box-shadow:0 0 0 6px rgba(59,130,246,.18); }}
    .row{{ display:flex; gap:10px; }}
    .btn{{ padding:10px 12px; border-radius:12px; border:1px solid rgba(16,24,40,.10);
      background: linear-gradient(180deg, rgba(59,130,246,.12), rgba(59,130,246,.06)); color:#1b2430; cursor:pointer; }}
    .btn:focus{{ outline:none; box-shadow:0 0 0 6px rgba(59,130,246,.18); }}
    .field{{ display:flex; flex-direction:column; gap:6px; margin-bottom:10px; }}
    .label{{ font-size:12px; color:#5a677a; }}
    a{{ color:#3b82f6; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>登录</h1>
    <div class="muted">{hint}</div>
    <div class="field">
      <div class="label">用户名</div>
      <input id="u" placeholder="admin" />
    </div>
    <div class="field">
      <div class="label">密码</div>
      <input id="p" type="password" placeholder="密码" />
    </div>
    <div class="row">
      <button class="btn" id="go">登录</button>
      <div id="out" class="muted" style="align-self:center;"></div>
    </div>
    <div class="muted" style="margin-top:10px;">首次使用：<a href="/setup">去初始化管理员</a></div>
  </div>
<script>
document.getElementById("go").addEventListener("click", async () => {{
  const out = document.getElementById("out");
  out.textContent = "";
  const username = document.getElementById("u").value.trim();
  const password = document.getElementById("p").value;
  const res = await fetch("/api/v1/login", {{method:"POST", headers:{{"Content-Type":"application/json"}}, body: JSON.stringify({{username, password}})}});
  const data = await res.json();
  if(!res.ok){{ out.textContent = data.error || "失败"; return; }}
  location.href = "/";
}});
</script>
</body>
</html>"""


class App:
    def __init__(
        self,
        db: Database,
        *,
        poll_workers: int = 10,
        registration_token: Optional[str] = None,
        default_cluster_id: Optional[int] = None,
        poll_interval_s: float = 0.0,
        webhook_url: Optional[str] = None,
        api_token: Optional[str] = None,
    ):
        self.db = db
        self.poll_workers = poll_workers
        self.registration_token = registration_token
        self.default_cluster_id = default_cluster_id
        self.poll_interval_s = float(poll_interval_s)
        self.webhook_url = webhook_url
        self.api_token = api_token
        self._stop_event = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None

    def poll_and_record(self, device: Dict[str, Any], *, timeout_s: float = 2.0) -> Dict[str, Any]:
        device_id = int(device["id"])
        started = time.perf_counter()
        res = poll_device(device, timeout_s=timeout_s)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        latency_ms = res.latency_ms if res.latency_ms is not None else elapsed_ms
        self.db.record_snapshot(
            device_id=device_id,
            success=res.success,
            http_status=res.http_status,
            latency_ms=latency_ms,
            error=res.error,
            protocol_version=res.protocol_version,
            main_version=res.main_version,
            firmware_version=res.firmware_version,
            payload=res.payload,
        )
        old_state = device.get("last_state")
        new_state, message = self._compute_state_and_message(device=device, poll_result=res)
        if new_state:
            self.db.update_device_state(device_id, new_state)
        if new_state and (old_state != new_state):
            event_id = self.db.create_event(
                device_id=device_id,
                event_type="state_change",
                old_state=str(old_state) if old_state is not None else None,
                new_state=new_state,
                message=message,
                payload={
                    "device_id": device_id,
                    "device_key": device.get("device_key"),
                    "vendor": device.get("vendor"),
                    "model": device.get("model"),
                    "ip": device.get("ip"),
                    "port": device.get("port"),
                    "observed_main_version": res.main_version,
                    "http_status": res.http_status,
                    "error": res.error,
                },
            )
            self._notify_webhook(
                {
                    "event_id": event_id,
                    "event_type": "state_change",
                    "old_state": old_state,
                    "new_state": new_state,
                    "message": message,
                    "timestamp": _utc_now_iso(),
                }
            )
        return {
            "device_id": device_id,
            "success": res.success,
            "http_status": res.http_status,
            "latency_ms": latency_ms,
            "error": res.error,
            "main_version": res.main_version,
        }

    def _compute_state_and_message(self, *, device: Dict[str, Any], poll_result: Any) -> tuple[Optional[str], Optional[str]]:
        if not getattr(poll_result, "success", False):
            return "offline", str(getattr(poll_result, "error", None) or "offline")
        baseline = self.db.get_baseline(
            cluster_id=int(device["cluster_id"]),
            vendor=str(device["vendor"]),
            model=str(device["model"]),
        )
        observed = str(getattr(poll_result, "main_version", None) or "")
        if baseline is None:
            return "no_baseline", "no_baseline"
        if self.db.baseline_allows(baseline, observed):
            return "ok", f"ok observed={observed}"
        expected = str(baseline.get("expected_main_version") or "")
        return "mismatch", f"mismatch expected={expected} observed={observed}"

    def _notify_webhook(self, payload: Dict[str, Any]) -> None:
        if not self.webhook_url:
            return

        def send() -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url,
                method="POST",
                data=data,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            try:
                urllib.request.urlopen(req, timeout=2.0).read()
            except Exception:
                return

        threading.Thread(target=send, daemon=True).start()

    def start_scheduler(self) -> None:
        if self.poll_interval_s <= 0:
            return
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return

        def loop() -> None:
            while not self._stop_event.is_set():
                try:
                    devices = self.db.list_devices(enabled_only=True)
                    with ThreadPoolExecutor(max_workers=self.poll_workers) as ex:
                        futures = [ex.submit(self.poll_and_record, d, timeout_s=2.0) for d in devices]
                        for f in as_completed(futures):
                            try:
                                f.result()
                            except Exception:
                                pass
                finally:
                    self._stop_event.wait(self.poll_interval_s)

        self._scheduler_thread = threading.Thread(target=loop, daemon=True)
        self._scheduler_thread.start()

    def stop_scheduler(self) -> None:
        self._stop_event.set()

class VersionManagerHandler(BaseHTTPRequestHandler):
    server_version = "VersionManager/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    @property
    def app(self) -> App:
        return getattr(self.server, "app")  # type: ignore[attr-defined]

    def _auth(self) -> Optional[Dict[str, Any]]:
        # API token bypass (admin)
        if self.app.api_token:
            hdr = self.headers.get("X-Api-Token")
            if hdr and secrets.compare_digest(hdr, self.app.api_token):
                return {"username": "api-token", "role": "admin"}
        cookies = _parse_cookie(self.headers.get("Cookie"))
        tok = cookies.get("vm_session")
        if not tok:
            return None
        return self.app.db.get_session_user(token=tok)

    def _require_login(self) -> Optional[Dict[str, Any]]:
        u = self._auth()
        if not u:
            _send_json(self, 401, {"error": "unauthorized"})
            return None
        return u

    def _require_admin(self) -> Optional[Dict[str, Any]]:
        u = self._require_login()
        if not u:
            return None
        if str(u.get("role")) != "admin":
            _send_json(self, 403, {"error": "forbidden"})
            return None
        return u

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = _path_parts(parsed.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/api/v1/healthz":
            return _send_json(self, 200, {"ok": True})

        if parsed.path == "/api/v1/info":
            return _send_json(
                self,
                200,
                {
                    "service": "version-manager",
                    "version": "0.1",
                    "cwd": os.getcwd(),
                    "db_path": os.path.abspath(self.app.db.db_path),
                    "timestamp": _utc_now_iso(),
                },
            )

        if parsed.path == "/login":
            if self._auth():
                return _redirect(self, "/")
            return _send_html(self, 200, _login_html(setup_needed=not self.app.db.has_any_user()))

        if parsed.path == "/setup":
            if self.app.db.has_any_user():
                return _send_json(self, 404, {"error": "not_found"})
            return _send_html(self, 200, _setup_html())

        if parsed.path == "/":
            if not self._auth():
                return _redirect(self, "/login")
            return _send_html(self, 200, _dashboard_html())

        if parts[:3] == ["api", "v1", "clusters"] and len(parts) == 3:
            if not self._require_login():
                return
            return _send_json(self, 200, {"items": self.app.db.list_clusters()})

        if parts[:3] == ["api", "v1", "devices"] and len(parts) == 3:
            if not self._require_login():
                return
            cluster_id = qs.get("cluster_id", [None])[0]
            enabled_only = (qs.get("enabled_only", ["0"])[0] or "0") in ("1", "true", "True")
            items = self.app.db.list_devices(cluster_id=int(cluster_id) if cluster_id else None, enabled_only=enabled_only)
            return _send_json(self, 200, {"items": items})

        if parts[:3] == ["api", "v1", "devices"] and len(parts) == 4:
            if not self._require_login():
                return
            try:
                device_id = int(parts[3])
            except ValueError:
                return _send_json(self, 400, {"error": "invalid_device_id"})
            dev = self.app.db.get_device(device_id)
            if not dev:
                return _send_json(self, 404, {"error": "not_found"})
            snap = self.app.db.get_latest_snapshot(device_id)
            base = self.app.db.get_baseline(cluster_id=int(dev["cluster_id"]), vendor=str(dev["vendor"]), model=str(dev["model"]))
            return _send_json(self, 200, {"device": dev, "baseline": base, "latest_snapshot": snap})

        if parts[:3] == ["api", "v1", "baselines"] and len(parts) == 3:
            if not self._require_login():
                return
            cluster_id = qs.get("cluster_id", [None])[0]
            items = self.app.db.list_baselines(cluster_id=int(cluster_id) if cluster_id else None)
            return _send_json(self, 200, {"items": items})

        if parts[:3] == ["api", "v1", "version-catalog"] and len(parts) == 3:
            if not self._require_login():
                return
            vendor = qs.get("vendor", [None])[0]
            model = qs.get("model", [None])[0]
            items = self.app.db.list_version_catalog(vendor=vendor, model=model)
            return _send_json(self, 200, {"items": items})

        if parts[:3] == ["api", "v1", "events"] and len(parts) == 3:
            if not self._require_login():
                return
            limit = qs.get("limit", ["50"])[0]
            device_id = qs.get("device_id", [None])[0]
            try:
                lim = int(limit) if limit else 50
            except Exception:
                lim = 50
            did = None
            if device_id:
                try:
                    did = int(device_id)
                except Exception:
                    return _send_json(self, 400, {"error": "invalid_device_id"})
            items = self.app.db.list_events(limit=lim, device_id=did)
            return _send_json(self, 200, {"items": items, "timestamp": _utc_now_iso()})

        if parts[:3] == ["api", "v1", "status"] and len(parts) == 3:
            if not self._require_login():
                return
            return _send_json(self, 200, {"items": self.app.db.list_status(), "timestamp": _utc_now_iso()})

        if parsed.path == "/api/v1/me":
            u = self._auth()
            if not u:
                return _send_json(self, 401, {"error": "unauthorized"})
            return _send_json(self, 200, {"user": u})

        return _send_json(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = _path_parts(parsed.path)

        if parts[:3] == ["api", "v1", "setup"] and len(parts) == 3:
            if self.app.db.has_any_user():
                return _send_json(self, 409, {"error": "already_initialized"})
            try:
                body = _read_json(self)
            except ValueError as e:
                return _send_json(self, 400, {"error": str(e)})
            username = str(body.get("username") or "admin").strip()
            password = str(body.get("password") or "")
            if len(password) < 8:
                return _send_json(self, 400, {"error": "password_too_short"})
            try:
                uid = self.app.db.create_user(username=username, password=password, role="admin")
            except Exception as e:  # noqa: BLE001
                return _send_json(self, 400, {"error": f"setup_failed:{e}"})
            return _send_json(self, 201, {"ok": True, "user_id": uid})

        if parts[:3] == ["api", "v1", "login"] and len(parts) == 3:
            try:
                body = _read_json(self)
            except ValueError as e:
                return _send_json(self, 400, {"error": str(e)})
            username = str(body.get("username") or "").strip()
            password = str(body.get("password") or "")
            u = self.app.db.verify_user(username=username, password=password)
            if not u:
                return _send_json(self, 401, {"error": "invalid_credentials"})
            token = self.app.db.create_session(user_id=int(u["id"]))
            payload = {"ok": True, "user": {"username": u["username"], "role": u["role"]}}
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            _set_cookie(self, "vm_session", token, max_age=12 * 3600)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parts[:3] == ["api", "v1", "logout"] and len(parts) == 3:
            u = self._auth()
            cookies = _parse_cookie(self.headers.get("Cookie"))
            tok = cookies.get("vm_session")
            if tok:
                self.app.db.delete_session(token=tok)
            payload = {"ok": True, "user": u}
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            _set_cookie(self, "vm_session", "", max_age=0)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parts[:3] == ["api", "v1", "register"] and len(parts) == 3:
            try:
                body = _read_json(self)
            except ValueError as e:
                return _send_json(self, 400, {"error": str(e)})

            required_token = self.app.registration_token
            if required_token:
                supplied = self.headers.get("X-Registration-Token") or str(body.get("registration_token") or "")
                if supplied != required_token:
                    # allow admin user to register without registration token
                    u = self._auth()
                    if not u or str(u.get("role")) != "admin":
                        return _send_json(self, 401, {"error": "invalid_registration_token"})
            else:
                # no registration token => require admin
                if not self._require_admin():
                    return

            cluster_id: Optional[int] = None
            cluster_obj = body.get("cluster") or {}
            if isinstance(cluster_obj, dict):
                if cluster_obj.get("id") is not None:
                    try:
                        cluster_id = int(cluster_obj.get("id"))
                    except Exception:
                        return _send_json(self, 400, {"error": "invalid_cluster_id"})
                elif cluster_obj.get("name"):
                    name = str(cluster_obj.get("name") or "").strip()
                    c = self.app.db.get_cluster_by_name(name) if name else None
                    if not c:
                        return _send_json(self, 404, {"error": "cluster_not_found"})
                    cluster_id = int(c["id"])

            if cluster_id is None:
                if self.app.default_cluster_id is not None:
                    cluster_id = int(self.app.default_cluster_id)
                else:
                    return _send_json(self, 400, {"error": "missing_cluster"})

            if not self.app.db.get_cluster(int(cluster_id)):
                return _send_json(self, 404, {"error": "cluster_not_found"})

            device_key = str(body.get("device_key") or "").strip()
            vendor = str(body.get("vendor") or "").strip()
            model = str(body.get("model") or "").strip()
            device_key_prefix = str(body.get("device_key_prefix") or "").strip()
            line_no = body.get("line_no")

            auth_obj = body.get("auth") or {"type": "none"}
            auth_type = str(auth_obj.get("type") or "none").strip()
            auth_token = auth_obj.get("token")

            protocol = "dvp1-http"
            ip = str(body.get("ip") or "").strip()
            port = int(body.get("port") or 80)
            path = str(body.get("path") or "/.well-known/device-version").strip()

            dvp_url = body.get("dvp_url")
            if isinstance(dvp_url, str) and dvp_url.strip():
                parsed_url = _parse_dvp_url(dvp_url.strip())
                if not parsed_url:
                    return _send_json(self, 400, {"error": "invalid_dvp_url"})
                ip = parsed_url["ip"]
                port = int(parsed_url["port"])
                path = parsed_url["path"]
                protocol = parsed_url["protocol"]

            prefer_remote_ip = bool(body.get("prefer_remote_ip", False))
            if prefer_remote_ip or not ip:
                ip = str(self.client_address[0])

            verify = bool(body.get("verify", True))
            timeout_s = float(body.get("timeout_s") or 1.5)

            pre_poll_payload: Optional[Dict[str, Any]] = None
            pre_poll_result: Optional[Dict[str, Any]] = None
            if not device_key or not vendor or not model:
                probe_dev = {
                    "ip": ip,
                    "port": port,
                    "protocol": protocol,
                    "path": path,
                    "auth_type": auth_type,
                    "auth_token": auth_token,
                }
                res = poll_device(probe_dev, timeout_s=timeout_s)
                pre_poll_result = {
                    "success": res.success,
                    "http_status": res.http_status,
                    "latency_ms": res.latency_ms,
                    "error": res.error,
                    "main_version": res.main_version,
                }
                if res.success and isinstance(res.payload, dict):
                    pre_poll_payload = res.payload
                    inferred = _infer_from_dvp(res.payload)
                    if not vendor and inferred.get("vendor"):
                        vendor = inferred["vendor"]
                    if not model and inferred.get("model"):
                        model = inferred["model"]
                    if not device_key and inferred.get("device_id"):
                        device_key = device_key_prefix + inferred["device_id"]

            if not device_key or not vendor or not model:
                return _send_json(
                    self,
                    400,
                    {
                        "error": "missing_fields",
                        "required": ["device_key", "vendor", "model"],
                        "hint": "provide dvp_url (or ip/port/path) and let server infer fields, or provide fields directly",
                        "pre_poll": pre_poll_result,
                    },
                )

            device_id, action = self.app.db.upsert_device_by_key(
                cluster_id=int(cluster_id),
                device_key=device_key,
                vendor=vendor,
                model=model,
                line_no=str(line_no).strip() if line_no is not None else None,
                ip=ip,
                port=port,
                protocol=protocol,
                path=path,
                auth=DeviceAuth(type=auth_type, token=auth_token),
                enabled=True,
            )

            verification: Optional[Dict[str, Any]] = None
            if verify:
                if pre_poll_payload is not None and pre_poll_result is not None:
                    self.app.db.record_snapshot(
                        device_id=int(device_id),
                        success=True,
                        http_status=pre_poll_result.get("http_status"),
                        latency_ms=pre_poll_result.get("latency_ms"),
                        error=None,
                        protocol_version=1,
                        main_version=pre_poll_result.get("main_version"),
                        firmware_version=None,
                        payload=pre_poll_payload,
                    )
                    verification = pre_poll_result
                else:
                    dev = self.app.db.get_device(int(device_id))
                    if dev:
                        verification = self.app.poll_and_record(dev, timeout_s=timeout_s)

            return _send_json(
                self,
                200,
                {"device_id": device_id, "action": action, "ip": ip, "port": port, "path": path, "verification": verification},
            )

        if parts[:3] == ["api", "v1", "clusters"] and len(parts) == 3:
            if not self._require_admin():
                return
            try:
                body = _read_json(self)
            except ValueError as e:
                return _send_json(self, 400, {"error": str(e)})
            name = str(body.get("name") or "").strip()
            if not name:
                return _send_json(self, 400, {"error": "missing_name"})
            description = body.get("description")
            try:
                cluster_id = self.app.db.create_cluster(name=name, description=description)
            except Exception as e:  # noqa: BLE001
                return _send_json(self, 409, {"error": f"create_cluster_failed:{e}"})
            return _send_json(self, 201, {"id": cluster_id})

        if parts[:3] == ["api", "v1", "devices"] and len(parts) == 3:
            if not self._require_admin():
                return
            try:
                body = _read_json(self)
            except ValueError as e:
                return _send_json(self, 400, {"error": str(e)})
            try:
                cluster_id = int(body.get("cluster_id"))
                device_key = str(body.get("device_key") or "").strip()
                vendor = str(body.get("vendor") or "").strip()
                model = str(body.get("model") or "").strip()
                line_no = body.get("line_no")
                ip = str(body.get("ip") or "").strip()
                port = int(body.get("port") or 80)
                protocol = str(body.get("protocol") or "dvp1-http").strip()
                path = str(body.get("path") or "/.well-known/device-version").strip()
                auth_obj = body.get("auth") or {"type": "none"}
                auth_type = str(auth_obj.get("type") or "none").strip()
                auth_token = auth_obj.get("token")
                enabled = bool(body.get("enabled", True))
            except Exception as e:  # noqa: BLE001
                return _send_json(self, 400, {"error": f"invalid_request:{e}"})
            if not device_key or not vendor or not model or not ip:
                return _send_json(self, 400, {"error": "missing_fields"})
            try:
                device_id = self.app.db.create_device(
                    cluster_id=cluster_id,
                    device_key=device_key,
                    vendor=vendor,
                    model=model,
                    line_no=str(line_no).strip() if line_no is not None else None,
                    ip=ip,
                    port=port,
                    protocol=protocol,
                    path=path,
                    auth=DeviceAuth(type=auth_type, token=auth_token),
                    enabled=enabled,
                )
            except Exception as e:  # noqa: BLE001
                return _send_json(self, 409, {"error": f"create_device_failed:{e}"})
            return _send_json(self, 201, {"id": device_id})

        if parts[:3] == ["api", "v1", "baselines"] and len(parts) == 3:
            if not self._require_admin():
                return
            try:
                body = _read_json(self)
            except ValueError as e:
                return _send_json(self, 400, {"error": str(e)})
            try:
                cluster_id = int(body.get("cluster_id"))
                vendor = str(body.get("vendor") or "").strip()
                model = str(body.get("model") or "").strip()
                expected_main_version = str(body.get("expected_main_version") or "").strip()
                allowed_main_globs = body.get("allowed_main_globs")
                note = body.get("note")
                effective_from = body.get("effective_from")
            except Exception as e:  # noqa: BLE001
                return _send_json(self, 400, {"error": f"invalid_request:{e}"})
            if not vendor or not model or not expected_main_version:
                return _send_json(self, 400, {"error": "missing_fields"})
            self.app.db.upsert_baseline(
                cluster_id=cluster_id,
                vendor=vendor,
                model=model,
                expected_main_version=expected_main_version,
                allowed_main_globs=allowed_main_globs if isinstance(allowed_main_globs, list) else None,
                note=note,
                effective_from=effective_from,
            )
            return _send_json(self, 201, {"ok": True})

        if parts[:3] == ["api", "v1", "version-catalog"] and len(parts) == 3:
            if not self._require_admin():
                return
            try:
                body = _read_json(self)
            except ValueError as e:
                return _send_json(self, 400, {"error": str(e)})
            try:
                vendor = str(body.get("vendor") or "").strip()
                model = str(body.get("model") or "").strip()
                main_version = str(body.get("main_version") or "").strip()
            except Exception as e:  # noqa: BLE001
                return _send_json(self, 400, {"error": f"invalid_request:{e}"})
            if not vendor or not model or not main_version:
                return _send_json(self, 400, {"error": "missing_fields"})
            self.app.db.upsert_version_catalog(
                vendor=vendor,
                model=model,
                main_version=main_version,
                changelog_md=body.get("changelog_md"),
                released_at=body.get("released_at"),
                risk_level=body.get("risk_level"),
                checksum=body.get("checksum"),
            )
            return _send_json(self, 201, {"ok": True})

        if parts[:3] == ["api", "v1", "poll"] and len(parts) == 3:
            if not self._require_admin():
                return
            try:
                body = _read_json(self)
            except ValueError as e:
                return _send_json(self, 400, {"error": str(e)})
            device_ids = body.get("device_ids")
            timeout_s = float(body.get("timeout_s") or 2.0)
            devices = self.app.db.list_devices(enabled_only=True)
            if isinstance(device_ids, list):
                allow = {int(x) for x in device_ids if isinstance(x, (int, str))}
                devices = [d for d in devices if int(d["id"]) in allow]
            started_at = _utc_now_iso()
            results: List[Dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=self.app.poll_workers) as ex:
                futures = {ex.submit(self.app.poll_and_record, d, timeout_s=timeout_s): d for d in devices}
                for fut in as_completed(futures):
                    try:
                        results.append(fut.result())
                    except Exception as e:  # noqa: BLE001
                        dev = futures[fut]
                        results.append({"device_id": int(dev["id"]), "success": False, "error": f"poll_exception:{e}"})
            ok = sum(1 for r in results if r.get("success"))
            fail = len(results) - ok
            return _send_json(
                self,
                200,
                {"started_at": started_at, "finished_at": _utc_now_iso(), "ok": ok, "fail": fail, "results": results},
            )

        if parts[:3] == ["api", "v1", "discover"] and len(parts) == 3:
            if not self._require_admin():
                return
            try:
                body = _read_json(self)
            except ValueError as e:
                return _send_json(self, 400, {"error": str(e)})

            try:
                cluster_id = int(body.get("cluster_id"))
            except Exception:
                return _send_json(self, 400, {"error": "missing_or_invalid_cluster_id"})
            if not self.app.db.get_cluster(cluster_id):
                return _send_json(self, 404, {"error": "cluster_not_found"})

            cidr = body.get("cidr")
            hosts = body.get("hosts")
            port = int(body.get("port") or 80)
            path = str(body.get("path") or "/.well-known/device-version")
            protocol = str(body.get("protocol") or "dvp1-http")
            timeout_s = float(body.get("timeout_s") or 0.8)
            max_hosts = int(body.get("max_hosts") or 1024)
            line_no = body.get("line_no")
            auth_obj = body.get("auth") or {"type": "none"}
            auth_type = str(auth_obj.get("type") or "none").strip()
            auth_token = auth_obj.get("token")

            targets: List[str] = []
            if isinstance(hosts, list):
                for h in hosts:
                    if isinstance(h, str) and h.strip():
                        targets.append(h.strip())
            elif isinstance(cidr, str) and cidr.strip():
                try:
                    net = ipaddress.ip_network(cidr.strip(), strict=False)
                except Exception as e:  # noqa: BLE001
                    return _send_json(self, 400, {"error": f"invalid_cidr:{e}"})
                for i, ip in enumerate(net.hosts()):
                    if i >= max_hosts:
                        break
                    targets.append(str(ip))
            else:
                return _send_json(self, 400, {"error": "missing_cidr_or_hosts"})

            started_at = _utc_now_iso()
            discovered: List[Dict[str, Any]] = []
            created = 0
            updated = 0

            def probe(ip: str) -> Tuple[str, Dict[str, Any]]:
                dev = {
                    "ip": ip,
                    "port": port,
                    "protocol": protocol,
                    "path": path,
                    "auth_type": auth_type,
                    "auth_token": auth_token,
                }
                res = poll_device(dev, timeout_s=timeout_s)
                return ip, {"poll": res}

            with ThreadPoolExecutor(max_workers=min(self.app.poll_workers, 32)) as ex:
                futures = {ex.submit(probe, ip): ip for ip in targets}
                for fut in as_completed(futures):
                    ip = futures[fut]
                    try:
                        ip2, out = fut.result()
                        res = out["poll"]
                    except Exception as e:  # noqa: BLE001
                        discovered.append({"ip": ip, "success": False, "error": f"probe_exception:{e}"})
                        continue

                    if not res.success or not isinstance(res.payload, dict):
                        discovered.append({"ip": ip2, "success": False, "error": res.error})
                        continue

                    payload = res.payload
                    device_obj = payload.get("device", {}) if isinstance(payload, dict) else {}
                    vendor = str(device_obj.get("vendor") or "").strip()
                    model = str(device_obj.get("model") or "").strip()
                    device_id = str(device_obj.get("id") or "").strip()
                    if not vendor or not model or not device_id:
                        discovered.append({"ip": ip2, "success": False, "error": "missing_device_fields"})
                        continue

                    device_key = str(body.get("device_key_prefix") or "").strip() + device_id
                    dev_id, action = self.app.db.upsert_device_by_key(
                        cluster_id=cluster_id,
                        device_key=device_key,
                        vendor=vendor,
                        model=model,
                        line_no=str(line_no).strip() if line_no is not None else None,
                        ip=ip2,
                        port=port,
                        protocol=protocol,
                        path=path,
                        auth=DeviceAuth(type=auth_type, token=auth_token),
                        enabled=True,
                    )
                    if action == "created":
                        created += 1
                    else:
                        updated += 1

                    self.app.db.record_snapshot(
                        device_id=dev_id,
                        success=True,
                        http_status=res.http_status,
                        latency_ms=res.latency_ms,
                        error=None,
                        protocol_version=res.protocol_version,
                        main_version=res.main_version,
                        firmware_version=res.firmware_version,
                        payload=res.payload,
                    )
                    discovered.append(
                        {
                            "ip": ip2,
                            "success": True,
                            "device_id": dev_id,
                            "device_key": device_key,
                            "vendor": vendor,
                            "model": model,
                            "main_version": res.main_version,
                            "action": action,
                        }
                    )

            return _send_json(
                self,
                200,
                {
                    "started_at": started_at,
                    "finished_at": _utc_now_iso(),
                    "targets": len(targets),
                    "created": created,
                    "updated": updated,
                    "items": discovered,
                },
            )

        return _send_json(self, 404, {"error": "not_found"})

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = _path_parts(parsed.path)

        if parts[:3] == ["api", "v1", "devices"] and len(parts) == 4:
            if not self._require_admin():
                return
            try:
                device_id = int(parts[3])
            except ValueError:
                return _send_json(self, 400, {"error": "invalid_device_id"})
            if not self.app.db.get_device(device_id):
                return _send_json(self, 404, {"error": "not_found"})
            try:
                body = _read_json(self)
            except ValueError as e:
                return _send_json(self, 400, {"error": str(e)})

            auth = None
            if "auth" in body:
                auth_obj = body.get("auth") or {"type": "none"}
                auth = DeviceAuth(type=str(auth_obj.get("type") or "none"), token=auth_obj.get("token"))

            cluster_id = body.get("cluster_id")
            port = body.get("port")
            enabled = body.get("enabled")
            line_no = body.get("line_no")
            try:
                cluster_id = int(cluster_id) if cluster_id is not None else None
                port = int(port) if port is not None else None
                enabled = bool(enabled) if enabled is not None else None
            except Exception as e:  # noqa: BLE001
                return _send_json(self, 400, {"error": f"invalid_request:{e}"})

            self.app.db.update_device(
                device_id,
                cluster_id=cluster_id,
                device_key=body.get("device_key"),
                vendor=body.get("vendor"),
                model=body.get("model"),
                line_no=str(line_no).strip() if line_no is not None else None,
                ip=body.get("ip"),
                port=port,
                protocol=body.get("protocol"),
                path=body.get("path"),
                auth=auth,
                enabled=enabled,
            )
            return _send_json(self, 200, {"ok": True})

        return _send_json(self, 404, {"error": "not_found"})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = _path_parts(parsed.path)

        if parts[:3] == ["api", "v1", "baselines"] and len(parts) == 4:
            if not self._require_admin():
                return
            try:
                baseline_id = int(parts[3])
            except ValueError:
                return _send_json(self, 400, {"error": "invalid_baseline_id"})
            if not self.app.db.delete_baseline(baseline_id):
                return _send_json(self, 404, {"error": "not_found"})
            return _send_json(self, 200, {"ok": True})

        if parts[:3] == ["api", "v1", "devices"] and len(parts) == 4:
            if not self._require_admin():
                return
            try:
                device_id = int(parts[3])
            except ValueError:
                return _send_json(self, 400, {"error": "invalid_device_id"})
            if not self.app.db.get_device(device_id):
                return _send_json(self, 404, {"error": "not_found"})
            self.app.db.delete_device(device_id)
            return _send_json(self, 200, {"ok": True})

        return _send_json(self, 404, {"error": "not_found"})


def serve(
    *,
    host: str,
    port: int,
    db_path: str,
    poll_workers: int,
    registration_token: Optional[str] = None,
    default_cluster_id: Optional[int] = None,
    default_cluster_name: Optional[str] = None,
    poll_interval_s: float = 0.0,
    webhook_url: Optional[str] = None,
    api_token: Optional[str] = None,
) -> None:
    db = Database(db_path)
    if default_cluster_name and default_cluster_id is None:
        name = default_cluster_name.strip()
        if name:
            existing = db.get_cluster_by_name(name)
            if existing:
                default_cluster_id = int(existing["id"])
            else:
                default_cluster_id = int(db.create_cluster(name=name))
    app = App(
        db,
        poll_workers=poll_workers,
        registration_token=registration_token,
        default_cluster_id=default_cluster_id,
        poll_interval_s=poll_interval_s,
        webhook_url=webhook_url,
        api_token=api_token,
    )
    httpd = ThreadingHTTPServer((host, port), VersionManagerHandler)
    httpd.app = app  # type: ignore[attr-defined]
    print(
        json.dumps(
            {
                "service": "version-manager",
                "version": "0.1",
                "listen": f"http://{host}:{port}/",
                "db_path": os.path.abspath(db_path),
                "registration_token_enabled": bool(registration_token),
                "default_cluster_id": default_cluster_id,
                "poll_interval_s": poll_interval_s,
                "webhook_url": webhook_url,
                "api_token_enabled": bool(api_token),
                "timestamp": _utc_now_iso(),
            },
            ensure_ascii=False,
        )
    )
    app.start_scheduler()
    try:
        httpd.serve_forever()
    finally:
        app.stop_scheduler()
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Device Version Manager (MVP)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", default=8080, type=int)
    p.add_argument("--db", dest="db_path", default=os.path.join("data", "vm.sqlite3"))
    p.add_argument("--poll-workers", default=10, type=int)
    p.add_argument("--registration-token", default=None, help="Optional token required by /api/v1/register")
    p.add_argument(
        "--default-cluster-id",
        default=None,
        type=int,
        help="Used by /api/v1/register when cluster is omitted",
    )
    p.add_argument(
        "--default-cluster-name",
        default=None,
        help="Auto-create/use cluster by name; used by /api/v1/register when cluster is omitted",
    )
    p.add_argument("--poll-interval", default=0, type=float, help="Auto poll interval seconds (0 disables)")
    p.add_argument("--webhook-url", default=None, help="POST events to webhook URL (optional)")
    p.add_argument("--api-token", default=None, help="Optional admin API token via X-Api-Token header")
    args = p.parse_args()
    serve(
        host=args.host,
        port=args.port,
        db_path=args.db_path,
        poll_workers=args.poll_workers,
        registration_token=args.registration_token,
        default_cluster_id=args.default_cluster_id,
        default_cluster_name=args.default_cluster_name,
        poll_interval_s=args.poll_interval,
        webhook_url=args.webhook_url,
        api_token=args.api_token,
    )


if __name__ == "__main__":
    main()
