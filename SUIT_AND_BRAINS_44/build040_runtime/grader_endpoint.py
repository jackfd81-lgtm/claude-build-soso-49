"""Bounded HTTP Grader Endpoint for Orange Pi Zero 2W -> Raspberry Pi 5 dual-node testing.

Exposes only scoped agent workload submission and status checks authenticated via
`X-Grader-Token`. Never exposes admin_token, raw SQLite, or Suit capability grants.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import json
from typing import Any

from build040_runtime.closet import SoSoCloset
from crampu.controller import AuthorizationError, ConflictError


class BoundedGraderServer(HTTPServer):
    def __init__(self, server_address: tuple[str, int], closet: SoSoCloset, grader_token: str) -> None:
        self.closet = closet
        self.grader_token = grader_token
        super().__init__(server_address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, code: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _auth_ok(self) -> bool:
        tok = self.headers.get("X-Grader-Token", "")
        return bool(tok) and hmac.compare_digest(tok, self.server.grader_token)

    def do_GET(self) -> None:
        if self.path == "/grader/health":
            self._send(200, {"status": "ready", "authority_class": "ADVISORY_AUTH_0"})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self._auth_ok():
            self._send(401, {"error": "unauthorized_grader_token"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.path == "/grader/submit":
            try:
                self.server.closet.submit_task(data["session_id"], data.get("goal", "grader-task"))
                res = self.server.closet.invoke_agent(
                    data["session_id"],
                    data["agent_id"],
                    data["family"],
                    int(data["index"]),
                    data["payload"],
                )
                self._send(200, {"status": "OK", "result": res})
            except (AuthorizationError, ConflictError) as exc:
                self._send(403, {"status": "REJECTED", "error": str(exc)})
            return
        self._send(404, {"error": "not_found"})
