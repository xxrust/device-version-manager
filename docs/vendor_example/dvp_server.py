from __future__ import annotations

import base64
import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import dvp_config as cfg


def _guess_content_type(path: str) -> str:
    p = path.lower()
    if p.endswith(".json"):
        return "application/json"
    if p.endswith(".yml") or p.endswith(".yaml"):
        return "text/yaml"
    if p.endswith(".toml"):
        return "application/toml"
    if p.endswith(".ini") or p.endswith(".cfg") or p.endswith(".conf") or p.endswith(".txt"):
        return "text/plain"
    return "application/octet-stream"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _send_json(handler: BaseHTTPRequestHandler, status: int, obj) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _auth_ok(handler: BaseHTTPRequestHandler) -> bool:
    token = str(getattr(cfg, "TOKEN", "") or "")
    if not token:
        return True
    auth = handler.headers.get("Authorization", "")
    xdev = handler.headers.get("X-Device-Token", "")
    return auth == f"Bearer {token}" or xdev == token


def _read_file_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None


def _build_files_payload() -> list[dict]:
    paths = getattr(cfg, "CONTROLLED_PATHS", []) or []
    inline = bool(getattr(cfg, "INLINE_FILE_CONTENT", False))
    items: list[dict] = []
    for path in paths:
        p = str(path).strip()
        if not p:
            continue
        raw = _read_file_bytes(p)
        exists = os.path.exists(p)
        if raw is None:
            # If the file can't be read (permissions/locks), still report path + size/mtime when available,
            # so the server can at least see the path and use size+mtime as a weak fingerprint.
            if not exists:
                continue
            items.append(
                {
                    "path": p,
                    "checksum": None,
                    "size": int(os.path.getsize(p)) if exists else None,
                    "mtime": int(os.path.getmtime(p)) if exists else None,
                }
            )
            continue

        item = {
            "path": p,
            # 指纹由程序自动生成（管理器用它判断是否变化）
            "checksum": "sha256:" + _sha256_hex(raw),
            "size": len(raw),
            "mtime": int(os.path.getmtime(p)) if os.path.exists(p) else None,
        }

        if inline:
            item["content_type"] = _guess_content_type(p)
            item["encoding"] = "utf-8"
            item["content_b64"] = base64.b64encode(raw).decode("ascii")

        items.append(item)
    return items


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)

        if u.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        if u.path == "/.well-known/device-version":
            if not _auth_ok(self):
                return _send_json(self, 401, {"error": "unauthorized"})

            payload = {
                "protocol": "dvp",
                "protocol_version": 1,
                "device": {
                    "id": str(cfg.DEVICE_ID),
                    "supplier": str(cfg.SUPPLIER),
                    "device_type": str(cfg.DEVICE_TYPE),
                },
                "versions": {"main": str(cfg.MAIN_VERSION)},
            }

            if getattr(cfg, "CONTROLLED_PATHS", None):
                payload["files"] = _build_files_payload()
            else:
                # Always include key if CONTROLLED_PATHS is present in config (even empty),
                # so it's easier to debug whether the device supports files reporting.
                if hasattr(cfg, "CONTROLLED_PATHS"):
                    payload["files"] = []

            return _send_json(self, 200, payload)

        if u.path == "/.well-known/device-version/file":
            if not bool(getattr(cfg, "ENABLE_FILE_ENDPOINT", True)):
                return _send_json(self, 404, {"error": "not_found"})
            if not _auth_ok(self):
                return _send_json(self, 401, {"error": "unauthorized"})
            qs = parse_qs(u.query)
            path = (qs.get("path") or [None])[0]
            if not path:
                return _send_json(self, 400, {"error": "missing_path"})
            raw = _read_file_bytes(str(path))
            if raw is None:
                return _send_json(self, 404, {"error": "not_found"})
            return _send_json(
                self,
                200,
                {
                    "path": str(path),
                    "content_type": _guess_content_type(str(path)),
                    "encoding": "utf-8",
                    "content_b64": base64.b64encode(raw).decode("ascii"),
                },
            )

        return _send_json(self, 404, {"error": "not_found"})


def main() -> None:
    host = str(getattr(cfg, "HOST", "0.0.0.0") or "0.0.0.0")
    port = int(getattr(cfg, "PORT", 18080) or 18080)
    print(f"listening: http://{host}:{port}/.well-known/device-version")
    ThreadingHTTPServer((host, port), H).serve_forever()


if __name__ == "__main__":
    main()
