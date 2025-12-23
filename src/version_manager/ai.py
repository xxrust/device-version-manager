from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, TypedDict


class AiNotAvailable(RuntimeError):
    pass


class ModelError(RuntimeError):
    pass


class AnalyzeState(TypedDict, total=False):
    context: Dict[str, Any]
    provider: str
    model: str
    analysis: Dict[str, Any]


def _http_json(url: str, *, headers: Dict[str, str], body: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, method="POST", headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
            msg = raw.decode("utf-8", errors="replace")
        except Exception:
            msg = str(e)
        raise ModelError(f"http_error:{getattr(e, 'code', '')}:{msg}") from e
    except Exception as e:  # noqa: BLE001
        if isinstance(e, TimeoutError):
            raise ModelError(f"request_failed:{type(e).__name__}:{e}:timeout_s={timeout_s}") from e
        raise ModelError(f"request_failed:{type(e).__name__}:{e}") from e
    try:
        out = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        raise ModelError(f"invalid_json:{type(e).__name__}:{e}") from e
    if not isinstance(out, dict):
        raise ModelError("invalid_response_type")
    return out


def _call_openai_chat(
    *,
    model: str,
    messages: List[Dict[str, str]],
    timeout_s: float,
    max_tokens: int = 1200,
) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ModelError("missing_env:OPENAI_API_KEY")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
    url = f"{base}/v1/chat/completions"
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": int(max_tokens),
    }
    data = _http_json(url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, body=body, timeout_s=timeout_s)
    try:
        return str(data["choices"][0]["message"]["content"])
    except Exception as e:  # noqa: BLE001
        raise ModelError(f"unexpected_openai_response:{e}") from e


def _call_ollama_chat(
    *,
    model: str,
    messages: List[Dict[str, str]],
    timeout_s: float,
) -> str:
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    url = f"{host}/api/chat"
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }
    data = _http_json(url, headers={"Content-Type": "application/json"}, body=body, timeout_s=timeout_s)
    try:
        msg = data.get("message") or {}
        return str(msg.get("content") or "")
    except Exception as e:  # noqa: BLE001
        raise ModelError(f"unexpected_ollama_response:{e}") from e


def _call_model(
    *,
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    timeout_s: float,
    max_tokens: int,
) -> str:
    p = str(provider or "").strip().lower()
    if p in ("openai", "remote", "cloud"):
        return _call_openai_chat(model=model, messages=messages, timeout_s=timeout_s, max_tokens=max_tokens)
    if p in ("ollama", "local"):
        return _call_ollama_chat(model=model, messages=messages, timeout_s=timeout_s)
    raise ModelError(f"unsupported_provider:{provider}")


def analyze_version_state(
    *,
    context: Dict[str, Any],
    provider: str,
    model: str,
    timeout_s: float = 120.0,
    max_tokens: int = 1200,
) -> Dict[str, Any]:
    try:
        from langgraph.graph import END, StateGraph  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise AiNotAvailable("langgraph_not_installed") from e

    sys_prompt = (
        "你是工厂设备版本管理专家。请基于给定 JSON 上下文，分析设备当前版本状态、与基线差异、风险点、建议动作。\n"
        "要求：只输出严格 JSON（不要 markdown、不要解释文字），字段必须包含：\n"
        "summary, status, risks, recommended_actions, evidence, confidence。\n"
        "- summary: 1-3 句中文摘要\n"
        "- status: 对当前状态的判断（ok/mismatch/offline/no_baseline/files_changed/unknown 等）\n"
        "- risks: 数组，每项包含 {title, level, detail}\n"
        "- recommended_actions: 数组，每项包含 {action, priority, detail}\n"
        "- evidence: 数组，引用上下文字段路径或具体值\n"
        "- confidence: 0-1 之间的小数\n"
        "如果信息不足，请在 recommended_actions 中给出需要补充的采集项。"
    )

    user_prompt = "上下文如下（JSON）：\n" + json.dumps(context, ensure_ascii=False)

    def llm_node(state: AnalyzeState) -> AnalyzeState:
        content = _call_model(
            provider=state["provider"],
            model=state["model"],
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            timeout_s=timeout_s,
            max_tokens=max_tokens,
        )
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                state["analysis"] = parsed
            else:
                state["analysis"] = {"summary": content, "status": "unknown", "risks": [], "recommended_actions": [], "evidence": [], "confidence": 0.2}
        except Exception:
            state["analysis"] = {"summary": content, "status": "unknown", "risks": [], "recommended_actions": [], "evidence": [], "confidence": 0.2}
        return state

    g = StateGraph(AnalyzeState)
    g.add_node("llm", llm_node)
    g.set_entry_point("llm")
    g.add_edge("llm", END)
    app = g.compile()
    final = app.invoke({"context": context, "provider": provider, "model": model})
    return final.get("analysis") or {}
