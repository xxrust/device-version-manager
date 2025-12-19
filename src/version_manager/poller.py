from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class PollResult:
    success: bool
    http_status: Optional[int]
    latency_ms: Optional[int]
    error: Optional[str]
    protocol_version: Optional[int]
    main_version: Optional[str]
    firmware_version: Optional[str]
    payload: Optional[Dict[str, Any]]


def _auth_headers(auth_type: str, auth_token: Optional[str]) -> Dict[str, str]:
    if not auth_type or auth_type == "none":
        return {}
    token = auth_token or ""
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {token}"}
    if auth_type == "x-device-token":
        return {"X-Device-Token": token}
    return {}


def _poll_dvp1_http(
    *,
    ip: str,
    port: int,
    path: str,
    auth_type: str,
    auth_token: Optional[str],
    timeout_s: float,
) -> PollResult:
    url = f"http://{ip}:{int(port)}{path}"
    headers = {"Accept": "application/json", **_auth_headers(auth_type, auth_token)}
    req = urllib.request.Request(url=url, method="GET", headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = getattr(resp, "status", None)
            raw = resp.read()
    except urllib.error.HTTPError as e:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return PollResult(
            success=False,
            http_status=int(getattr(e, "code", 0) or 0),
            latency_ms=latency_ms,
            error=f"http_error:{getattr(e, 'code', '')}",
            protocol_version=None,
            main_version=None,
            firmware_version=None,
            payload=None,
        )
    except urllib.error.URLError as e:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return PollResult(
            success=False,
            http_status=None,
            latency_ms=latency_ms,
            error=f"url_error:{getattr(e, 'reason', e)}",
            protocol_version=None,
            main_version=None,
            firmware_version=None,
            payload=None,
        )
    except Exception as e:  # noqa: BLE001
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return PollResult(
            success=False,
            http_status=None,
            latency_ms=latency_ms,
            error=f"exception:{type(e).__name__}:{e}",
            protocol_version=None,
            main_version=None,
            firmware_version=None,
            payload=None,
        )

    latency_ms = int((time.perf_counter() - t0) * 1000)
    if status != 200:
        return PollResult(
            success=False,
            http_status=int(status) if status is not None else None,
            latency_ms=latency_ms,
            error=f"http_status:{status}",
            protocol_version=None,
            main_version=None,
            firmware_version=None,
            payload=None,
        )
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        return PollResult(
            success=False,
            http_status=int(status) if status is not None else None,
            latency_ms=latency_ms,
            error=f"invalid_json:{type(e).__name__}:{e}",
            protocol_version=None,
            main_version=None,
            firmware_version=None,
            payload=None,
        )

    protocol = str(payload.get("protocol", ""))
    protocol_version = payload.get("protocol_version", None)
    if protocol != "dvp" or protocol_version != 1:
        return PollResult(
            success=False,
            http_status=int(status) if status is not None else None,
            latency_ms=latency_ms,
            error="unsupported_protocol",
            protocol_version=int(protocol_version) if isinstance(protocol_version, int) else None,
            main_version=None,
            firmware_version=None,
            payload=payload if isinstance(payload, dict) else None,
        )

    versions = payload.get("versions", {}) if isinstance(payload, dict) else {}
    main_version = None
    firmware_version = None
    if isinstance(versions, dict):
        mv = versions.get("main")
        if isinstance(mv, str) and mv.strip():
            main_version = mv.strip()
        fv = versions.get("firmware")
        if isinstance(fv, str) and fv.strip():
            firmware_version = fv.strip()
    if not main_version:
        return PollResult(
            success=False,
            http_status=int(status) if status is not None else None,
            latency_ms=latency_ms,
            error="missing_versions.main",
            protocol_version=1,
            main_version=None,
            firmware_version=firmware_version,
            payload=payload if isinstance(payload, dict) else None,
        )

    return PollResult(
        success=True,
        http_status=int(status) if status is not None else None,
        latency_ms=latency_ms,
        error=None,
        protocol_version=1,
        main_version=main_version,
        firmware_version=firmware_version,
        payload=payload if isinstance(payload, dict) else None,
    )


def poll_device(device: Dict[str, Any], *, timeout_s: float = 2.0) -> PollResult:
    protocol = str(device.get("protocol") or "")
    ip = str(device.get("ip") or "")
    port = int(device.get("port") or 80)
    path = str(device.get("path") or "/.well-known/device-version")
    auth_type = str(device.get("auth_type") or "none")
    auth_token = device.get("auth_token")

    if protocol == "dvp1-http":
        return _poll_dvp1_http(
            ip=ip, port=port, path=path, auth_type=auth_type, auth_token=auth_token, timeout_s=timeout_s
        )

    return PollResult(
        success=False,
        http_status=None,
        latency_ms=None,
        error=f"unsupported_device_protocol:{protocol}",
        protocol_version=None,
        main_version=None,
        firmware_version=None,
        payload=None,
    )

