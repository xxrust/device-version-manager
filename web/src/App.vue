<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { apiJson, jsonBody } from "./api";
import type { DeviceState, StatusRow } from "./types";
import DeviceDetailModal from "./components/DeviceDetailModal.vue";

type Cluster = { id: number; name: string };

const loading = ref(true);
const error = ref<string | null>(null);
const statusItems = ref<StatusRow[]>([]);
const clusters = ref<Cluster[]>([]);
const lastUpdate = ref<string>("");

const filterState = ref<string>("");
const filterQuery = ref<string>("");
const filterClusterId = ref<string>("");

const selectedDeviceId = ref<number | null>(null);

const counts = computed<Record<string, number>>(() => {
  const out: Record<string, number> = {};
  for (const it of statusItems.value) out[String(it.state)] = (out[String(it.state)] || 0) + 1;
  return out;
});

const filtered = computed(() => {
  const q = filterQuery.value.trim().toLowerCase();
  const state = filterState.value;
  const cid = filterClusterId.value;
  return statusItems.value.filter((row) => {
    if (state && String(row.state) !== state) return false;
    if (cid && String(row.device.cluster_id) !== cid) return false;
    if (!q) return true;
    const dev = row.device;
    const snap = row.latest_snapshot;
    const base = row.baseline;
    const parts = [
      dev.id,
      dev.device_serial,
      dev.line_no || "",
      dev.ip,
      dev.supplier,
      dev.device_type,
      base?.expected_main_version || "",
      snap?.main_version || "",
      row.state,
    ]
      .map((x) => String(x || "").toLowerCase())
      .join(" | ");
    return parts.includes(q);
  });
});

function stateLabel(s: DeviceState): string {
  const x = String(s || "");
  return (
    {
      ok: "ok",
      mismatch: "mismatch",
      offline: "offline",
      no_baseline: "no_baseline",
      never_polled: "never_polled",
      files_changed: "files_changed",
    }[x] || x
  );
}

function stateClass(s: DeviceState): string {
  return `state-${String(s || "").replaceAll(" ", "_")}`;
}

async function loadAll() {
  loading.value = true;
  error.value = null;
  try {
    const c = await apiJson<{ items: Cluster[] }>("/api/v1/clusters");
    clusters.value = c.items || [];

    const st = await apiJson<{ items: StatusRow[] }>("/api/v1/status");
    statusItems.value = st.items || [];
    lastUpdate.value = new Date().toISOString();
  } catch (e: any) {
    error.value = String(e?.message || e || "load_failed");
  } finally {
    loading.value = false;
  }
}

async function pollAll() {
  error.value = null;
  try {
    await apiJson("/api/v1/poll", { method: "POST", ...jsonBody({}) });
    await loadAll();
  } catch (e: any) {
    error.value = String(e?.message || e || "poll_failed");
  }
}

async function logout() {
  try {
    await apiJson("/api/v1/logout", { method: "POST", ...jsonBody({}) });
  } finally {
    window.location.href = "/login";
  }
}

function openDevice(id: number) {
  selectedDeviceId.value = id;
}

function setKpiFilter(st: string) {
  filterState.value = st;
}

onMounted(loadAll);
</script>

<template>
  <div class="wrap">
    <div class="top">
      <div class="title">
        <h1>设备版本管理器</h1>
        <div class="meta">
          <span v-if="lastUpdate">last_update={{ lastUpdate }}</span>
          <span v-if="filtered.length !== statusItems.length"> · filtered {{ filtered.length }}/{{ statusItems.length }}</span>
        </div>
      </div>
      <div class="right">
        <select class="select" v-model="filterClusterId" title="cluster">
          <option value="">全部集群</option>
          <option v-for="c in clusters" :key="c.id" :value="String(c.id)">{{ c.id }} - {{ c.name }}</option>
        </select>
        <select class="select" v-model="filterState" title="state">
          <option value="">全部状态</option>
          <option value="ok">ok</option>
          <option value="mismatch">mismatch</option>
          <option value="files_changed">files_changed</option>
          <option value="offline">offline</option>
          <option value="no_baseline">no_baseline</option>
          <option value="never_polled">never_polled</option>
        </select>
        <input class="input" v-model="filterQuery" placeholder="搜索：序列号 / IP / 版本 / ..." style="min-width: 280px" />
        <button class="btn" @click="loadAll" :disabled="loading">刷新</button>
        <button class="btn primary" @click="pollAll">轮询全部</button>
        <button class="btn" @click="logout">退出</button>
      </div>
    </div>

    <div class="grid">
      <div v-if="error" class="card" style="padding: 12px">
        <div class="muted">Error: {{ error }}</div>
      </div>

      <div class="card">
        <div class="kpi">
          <div class="kpiItem" @click="setKpiFilter('')" title="all">
            <div class="kpiLabel">总数</div>
            <div class="kpiValue">{{ statusItems.length }}</div>
          </div>
          <div class="kpiItem" @click="setKpiFilter('ok')" title="ok">
            <div class="kpiLabel">ok</div>
            <div class="kpiValue">{{ counts.ok || 0 }}</div>
          </div>
          <div class="kpiItem" @click="setKpiFilter('mismatch')" title="mismatch">
            <div class="kpiLabel">mismatch</div>
            <div class="kpiValue">{{ counts.mismatch || 0 }}</div>
          </div>
          <div class="kpiItem" @click="setKpiFilter('files_changed')" title="files_changed">
            <div class="kpiLabel">files_changed</div>
            <div class="kpiValue">{{ counts.files_changed || 0 }}</div>
          </div>
          <div class="kpiItem" @click="setKpiFilter('offline')" title="offline">
            <div class="kpiLabel">offline</div>
            <div class="kpiValue">{{ counts.offline || 0 }}</div>
          </div>
        </div>
      </div>

      <div class="card" style="overflow: auto; max-height: calc(100vh - 230px)">
        <table>
          <thead>
            <tr>
              <th style="width: 70px">ID</th>
              <th>序列号</th>
              <th style="width: 140px">产线号</th>
              <th style="width: 140px">状态</th>
              <th style="width: 130px">期望</th>
              <th style="width: 130px">当前</th>
              <th>错误</th>
              <th style="width: 120px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in filtered" :key="row.device.id">
              <td class="mono">{{ row.device.id }}</td>
              <td>{{ row.device.device_serial }}</td>
              <td class="mono">{{ row.device.line_no || "" }}</td>
              <td>
                <span class="badge" :class="stateClass(row.state)">
                  <span class="dot" />
                  <span>{{ stateLabel(row.state) }}</span>
                </span>
              </td>
              <td class="mono">{{ row.baseline?.expected_main_version || "" }}</td>
              <td class="mono">{{ row.latest_snapshot?.main_version || "" }}</td>
              <td class="mono" style="color: var(--muted)">{{ row.latest_snapshot?.error || "" }}</td>
              <td>
                <button class="btn" @click="openDevice(row.device.id)">详情</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <DeviceDetailModal
      v-if="selectedDeviceId !== null"
      :device-id="selectedDeviceId"
      @close="selectedDeviceId = null"
      @updated="loadAll"
    />
  </div>
</template>
