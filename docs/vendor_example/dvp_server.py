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


def _read_text_or_file(value) -> bytes | None:
    v = str(value or "").strip()
    if not v:
        return None
    if v.startswith("@") and len(v) > 1:
        try:
            return open(v[1:], "rb").read()
        except Exception:
            return None
    return v.encode("utf-8")


def _build_main_version_info() -> dict | None:
    info: dict = {}
    changelog = getattr(cfg, "MAIN_CHANGELOG_MD", None)
    if isinstance(changelog, str) and changelog.strip():
        info["changelog_md"] = changelog
    released_at = getattr(cfg, "MAIN_RELEASED_AT", None)
    if isinstance(released_at, str) and released_at.strip():
        info["released_at"] = released_at
    checksum = getattr(cfg, "MAIN_CHECKSUM", None)
    if isinstance(checksum, str) and checksum.strip():
        info["checksum"] = checksum.strip()
    return info or None


def _build_docs_payload() -> list[dict]:
    docs = getattr(cfg, "DOCS", None)
    if docs is None:
        return []
    if not isinstance(docs, list):
        return []
    items: list[dict] = []
    for spec in docs:
        name = None
        raw = None
        content_type = "text/markdown"
        encoding = "utf-8"
        if isinstance(spec, str):
            p = spec.strip()
            if not p:
                continue
            name = os.path.basename(p)
            raw = _read_file_bytes(p)
        elif isinstance(spec, dict):
            name = str(spec.get("name") or "").strip() or None
            if not name:
                p = str(spec.get("path") or "").strip()
                name = os.path.basename(p) if p else None
            pth = str(spec.get("path") or "").strip()
            txt = spec.get("text")
            if pth:
                raw = _read_file_bytes(pth)
            elif txt is not None:
                raw = _read_text_or_file(txt)
            ct = spec.get("content_type")
            if isinstance(ct, str) and ct.strip():
                content_type = ct.strip()
            enc = spec.get("encoding")
            if isinstance(enc, str) and enc.strip():
                encoding = enc.strip()
        else:
            continue
        if not name or raw is None:
            continue
        items.append(
            {
                "name": name,
                "content_type": content_type,
                "encoding": encoding,
                "checksum": "sha256:" + _sha256_hex(raw),
                "content_b64": base64.b64encode(raw).decode("ascii"),
            }
        )
    return items


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

            mvi = _build_main_version_info()
            if mvi:
                payload["main_version_info"] = mvi

            if getattr(cfg, "CONTROLLED_PATHS", None):
                payload["files"] = _build_files_payload()
            else:
                # Always include key if CONTROLLED_PATHS is present in config (even empty),
                # so it's easier to debug whether the device supports files reporting.
                if hasattr(cfg, "CONTROLLED_PATHS"):
                    payload["files"] = []

            docs = _build_docs_payload()
            if docs:
                payload["docs"] = docs
            else:
                if hasattr(cfg, "DOCS"):
                    payload["docs"] = []

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
