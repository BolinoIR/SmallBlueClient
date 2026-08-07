"""Optional localhost receiver for sessions discovered by the Chrome extension."""
from __future__ import annotations

import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..core.session import SBCSession

class SessionBridge:
    """Receive extension session captures at ``http://127.0.0.1:<port>``.

    The random token prevents another local web page from silently importing a
    session. Start it with ``bridge.start()`` and paste ``bridge.endpoint`` into
    the extension's Session panel.
    """
    def __init__(self, directory: str | Path = "sessions", *, host: str = "127.0.0.1", port: int = 8765, token: str | None = None):
        self.directory, self.host, self.port = Path(directory), host, port
        self.token = token or secrets.token_urlsafe(24)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.sessions: list[Path] = []
        self._commands: dict[str, list[dict[str, Any]]] = {}
        self._commands_lock = threading.Lock()

    @property
    def endpoint(self) -> str: return f"http://{self.host}:{self.port}/sessions?token={self.token}"
    def start(self) -> "SessionBridge":
        bridge = self
        class Handler(BaseHTTPRequestHandler):
            def _headers(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
            def do_OPTIONS(self):
                self.send_response(HTTPStatus.NO_CONTENT); self._headers()
            def do_GET(self):
                from urllib.parse import parse_qs, urlparse
                parsed = urlparse(self.path); query = parse_qs(parsed.query)
                if parsed.path == "/health": self.send_response(HTTPStatus.OK); self._headers(); self.wfile.write(b'{"ok":true}'); return
                if parsed.path == "/commands" and query.get("token", [""])[0] == bridge.token:
                    identity = query.get("identity", [""])[0]
                    with bridge._commands_lock: commands = bridge._commands.pop(identity, [])
                    self.send_response(HTTPStatus.OK); self._headers(); self.wfile.write(json.dumps({"commands": commands}).encode()); return
                self.send_error(HTTPStatus.NOT_FOUND)
            def do_POST(self):
                from urllib.parse import parse_qs, urlparse
                parsed = urlparse(self.path); query = parse_qs(parsed.query)
                if query.get("token", [""])[0] != bridge.token:
                    self.send_error(HTTPStatus.FORBIDDEN); return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 60_000_000: raise ValueError("invalid request size")
                    data = json.loads(self.rfile.read(size))
                    if parsed.path == "/commands":
                        identity = data.pop("identity", "")
                        command = data.pop("command", None)
                        if not identity or not isinstance(command, dict): raise ValueError("identity and command are required")
                        with bridge._commands_lock: bridge._commands.setdefault(identity, []).append(command)
                        self.send_response(HTTPStatus.ACCEPTED); self._headers(); self.wfile.write(b'{"queued":true}'); return
                    if parsed.path != "/sessions": self.send_error(HTTPStatus.NOT_FOUND); return
                    session = SBCSession.from_dict(data)
                    session.metadata["bridge_endpoint"] = bridge.endpoint
                    session.metadata["sbc_identity"] = session.metadata.get("sbc_identity") or f"{session.server}|{session.meeting_id or ''}|{session.user_id or ''}"
                    filename = f"{(session.meeting_name or session.meeting_id or 'meeting').replace('/', '-')}.sbc"
                    saved = session.save(bridge.directory / filename)
                    bridge.sessions.append(saved)
                    self.send_response(HTTPStatus.CREATED); self._headers(); self.wfile.write(json.dumps({"path": str(saved)}).encode())
                except Exception as exc:
                    self.send_response(HTTPStatus.BAD_REQUEST); self._headers(); self.wfile.write(json.dumps({"error": str(exc)}).encode())
            def log_message(self, *_): pass
        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="sbc-bridge")
        self._thread.start(); return self
    def close(self) -> None:
        if self._server: self._server.shutdown(); self._server.server_close()
        if self._thread: self._thread.join(timeout=2)
        self._server = self._thread = None
