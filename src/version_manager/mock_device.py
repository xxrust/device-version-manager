from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class MockDeviceHandler(BaseHTTPRequestHandler):
    server_version = "MockDevice/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    @property
    def cfg(self) -> Dict[str, Any]:
        return getattr(self.server, "cfg")  # type: ignore[attr-defined]

    def _auth_ok(self) -> bool:
        required = self.cfg.get("token")
        if not required:
            return True
        auth = self.headers.get("Authorization", "")
        xdev = self.headers.get("X-Device-Token", "")
        return auth == f"Bearer {required}" or xdev == required

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        if parsed.path == "/.well-known/device-version/file":
            if not self._auth_ok():
                self.send_response(401)
                self.end_headers()
                return
            qs = parse_qs(parsed.query)
            path = (qs.get("path") or [None])[0]
            if not path:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"error":"missing_path"}')
                return
            files = self.cfg.get("file_contents") or {}
            item = files.get(str(path))
            if not isinstance(item, dict):
                self.send_response(404)
                self.end_headers()
                return
            payload = {
                "path": str(path),
                "checksum": item.get("checksum"),
                "encoding": item.get("encoding"),
                "content_type": item.get("content_type"),
                "content_b64": item.get("content_b64"),
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parsed.path != "/.well-known/device-version":
            self.send_response(404)
            self.end_headers()
            return

        if not self._auth_ok():
            self.send_response(401)
            self.end_headers()
            return

        payload: Dict[str, Any] = {
            "protocol": "dvp",
            "protocol_version": 1,
            "timestamp": _utc_now_iso(),
            "device": {
                "id": self.cfg["device_id"],
                "vendor": self.cfg["vendor"],
                "model": self.cfg["model"],
                "serial": self.cfg.get("serial") or self.cfg["device_id"],
            },
            "versions": {"main": self.cfg["main_version"]},
        }
        if self.cfg.get("firmware"):
            payload["versions"]["firmware"] = self.cfg["firmware"]
        if self.cfg.get("components"):
            payload["components"] = self.cfg["components"]
        if self.cfg.get("files"):
            payload["files"] = self.cfg["files"]

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    p = argparse.ArgumentParser(description="Mock device implementing DVP v1")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", default=18080, type=int)
    p.add_argument("--id", dest="device_id", default="MOCK-001")
    p.add_argument("--vendor", default="VendorX")
    p.add_argument("--model", default="MockModel")
    p.add_argument("--version", dest="main_version", default="1.0.0")
    p.add_argument("--firmware", default=None)
    p.add_argument("--token", default=None, help="Optional token (accepts Bearer or X-Device-Token)")
    p.add_argument(
        "--file",
        dest="files",
        action="append",
        default=[],
        help="Optional reported file fingerprint: <path>=<checksum> (checksum recommended sha256:<hex>)",
    )
    p.add_argument(
        "--file-content",
        dest="file_contents",
        action="append",
        default=[],
        help="Optional file content for inline/fetch: <path>=<text> or <path>=@<localfile>",
    )
    p.add_argument(
        "--inline-file-content",
        action="store_true",
        help="Include content_b64 in the main /.well-known/device-version response (default: only expose via /file endpoint).",
    )
    args = p.parse_args()

    def guess_content_type(path: str) -> str:
        pth = path.lower()
        if pth.endswith(".json"):
            return "application/json"
        if pth.endswith(".yml") or pth.endswith(".yaml"):
            return "text/yaml"
        if pth.endswith(".toml"):
            return "application/toml"
        if pth.endswith(".ini") or pth.endswith(".cfg"):
            return "text/plain"
        return "application/octet-stream"

    file_contents: Dict[str, Dict[str, Any]] = {}
    for spec in args.file_contents or []:
        s = str(spec or "").strip()
        if not s or "=" not in s:
            continue
        path, value = s.split("=", 1)
        path = path.strip()
        value = value.strip()
        if not path:
            continue
        if value.startswith("@") and len(value) > 1:
            try:
                raw = open(value[1:], "rb").read()
            except Exception:
                raw = value.encode("utf-8")
        else:
            raw = value.encode("utf-8")
        checksum = "sha256:" + hashlib.sha256(raw).hexdigest()
        file_contents[path] = {
            "checksum": checksum,
            "encoding": "utf-8",
            "content_type": guess_content_type(path),
            "content_b64": base64.b64encode(raw).decode("ascii"),
        }

    files = []
    for spec in args.files or []:
        s = str(spec or "").strip()
        if not s:
            continue
        if "=" in s:
            path, checksum = s.split("=", 1)
            path = path.strip()
            checksum = checksum.strip()
        else:
            path, checksum = s, ""
        if not path:
            continue
        if not checksum:
            checksum = "sha256:" + hashlib.sha256(path.encode("utf-8")).hexdigest()
        files.append({"path": path, "checksum": checksum})

    # merge file_contents into files list (ensure checksum present)
    for path, meta in file_contents.items():
        exists = next((x for x in files if x.get("path") == path), None)
        if exists is None:
            files.append({"path": path, "checksum": meta.get("checksum")})
        else:
            exists["checksum"] = exists.get("checksum") or meta.get("checksum")

    if args.inline_file_content:
        for item in files:
            meta = file_contents.get(str(item.get("path") or ""))
            if not meta:
                continue
            item["encoding"] = meta.get("encoding")
            item["content_type"] = meta.get("content_type")
            item["content_b64"] = meta.get("content_b64")

    httpd = ThreadingHTTPServer((args.host, args.port), MockDeviceHandler)
    httpd.cfg = {  # type: ignore[attr-defined]
        "device_id": args.device_id,
        "vendor": args.vendor,
        "model": args.model,
        "main_version": args.main_version,
        "firmware": args.firmware,
        "token": args.token,
        "files": files,
        "file_contents": file_contents,
    }
    httpd.serve_forever()


if __name__ == "__main__":
    main()
