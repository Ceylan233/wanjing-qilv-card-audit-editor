#!/usr/bin/env python3
"""Private JSON sync service for the card audit editor.

The service stores only the audit JSON. Card images and game resources remain
on each device. It is intended to run behind the existing HTTPS reverse proxy.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DATA_FILE = Path(os.environ.get("CARD_AUDIT_DATA_FILE", "/opt/wanjing-card-audit/data/manual_card_audit.json"))
PUBLIC_BASE_URL = os.environ.get("CARD_AUDIT_PUBLIC_BASE_URL", "https://syncinema.pw/wanjing-card-audit").rstrip("/")
SECURITY_CODE = os.environ.get("CARD_AUDIT_SECURITY_CODE", "")
HOST = os.environ.get("CARD_AUDIT_BIND_HOST", "127.0.0.1")
PORT = int(os.environ.get("CARD_AUDIT_BIND_PORT", "19287"))
MAX_BODY = 128 * 1024 * 1024


def etag_for(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f'"{digest}"'


class Handler(BaseHTTPRequestHandler):
    server_version = "WanjingCardAuditSync/1.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    def authorized(self) -> bool:
        if not SECURITY_CODE:
            return False
        return self.headers.get("X-Card-Audit-Code", "") == SECURITY_CODE

    def send_json(self, status: int, payload: object, etag: str = "") -> None:
        body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)

    def deny(self) -> None:
        self.send_json(401, {"error": "unauthorized"})

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.send_json(200, {"ok": True})
            return
        if not self.authorized():
            self.deny()
            return
        if self.path == "/wanjing-card-audit/config.json":
            self.send_json(200, {
                "远程校对": {
                    "document_url": f"{PUBLIC_BASE_URL}/manual_card_audit.json",
                    "upload_url": f"{PUBLIC_BASE_URL}/manual_card_audit.json",
                    "method": "PUT",
                    "auth_header": "X-Card-Audit-Code",
                    "认证方式": "窗口输入安全码",
                    "auto_sync": True,
                }
            })
            return
        if self.path == "/wanjing-card-audit/manual_card_audit.json":
            if not DATA_FILE.exists():
                self.send_json(404, {"error": "data file not found"})
                return
            body = DATA_FILE.read_bytes()
            etag = etag_for(DATA_FILE)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_json(404, {"error": "not found"})

    def do_PUT(self) -> None:
        self.write_document()

    def do_POST(self) -> None:
        self.write_document()

    def write_document(self) -> None:
        if self.path != "/wanjing-card-audit/manual_card_audit.json":
            self.send_json(404, {"error": "not found"})
            return
        if not self.authorized():
            self.deny()
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "invalid content length"})
            return
        if length <= 0 or length > MAX_BODY:
            self.send_json(413, {"error": "payload too large"})
            return
        if DATA_FILE.exists():
            expected = self.headers.get("If-Match", "").strip()
            if expected and expected != etag_for(DATA_FILE):
                self.send_json(412, {"error": "document changed on server"})
                return
        raw = self.rfile.read(length)
        try:
            document = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(document, dict) or not isinstance(document.get("卡牌"), list):
                raise ValueError("missing 卡牌 array")
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": f"invalid audit JSON: {exc}"})
            return
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=DATA_FILE.parent, delete=False) as temp:
            temp.write(raw)
            temp_path = Path(temp.name)
        temp_path.replace(DATA_FILE)
        self.send_json(200, {"ok": True}, etag_for(DATA_FILE))


def main() -> None:
    if not SECURITY_CODE:
        raise SystemExit("CARD_AUDIT_SECURITY_CODE is required")
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
