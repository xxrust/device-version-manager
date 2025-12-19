from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse


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
    args = p.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), MockDeviceHandler)
    httpd.cfg = {  # type: ignore[attr-defined]
        "device_id": args.device_id,
        "vendor": args.vendor,
        "model": args.model,
        "main_version": args.main_version,
        "firmware": args.firmware,
        "token": args.token,
    }
    httpd.serve_forever()


if __name__ == "__main__":
    main()

