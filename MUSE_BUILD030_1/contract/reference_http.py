"""Standard-library HTTP transport for the portable reference runtime."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .authority import require_task
from .reference_runtime import ReferenceRuntime


class ReferenceRequestHandler(BaseHTTPRequestHandler):
    runtime: ReferenceRuntime | None = None
    server_version = "MUSE-RealCog-Reference/0.2"

    def _send(self, status: int, payload):
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def _dispatch(self, method: str, path: str, payload: dict, query: dict | None = None):
        rt = self.runtime
        if rt is None: raise RuntimeError("runtime is not configured")
        require_task(payload)
        parts = [p for p in path.split("/") if p]
        if path == "/health" and method == "GET": return 200, rt.health()
        if path == "/v1/task" and method == "POST": return 200, rt.task(payload.get("task", payload), payload.get("profile", "auto"), payload.get("evidence"), payload.get("run_id"))
        if path == "/v1/pipeline/run" and method == "POST": return 200, rt.pipeline_run(payload.get("task", payload), use_mac002=payload.get("use_mac002", True))
        if path == "/v1/realcog/status" and method == "GET": return 200, rt.realcog_status()
        if path == "/v1/handshake" and method == "GET":
            q = query or {}
            return 200, rt.handshake(inject_fault=(q.get("inject_fault") or [None])[0], optional_fault=(q.get("optional_fault") or [None])[0])
        if path == "/v1/status" and method == "GET":
            q = query or {}
            return 200, rt.status(inject_fault=(q.get("inject_fault") or [None])[0], optional_fault=(q.get("optional_fault") or [None])[0])
        if path == "/v1/disposition/evidence" and method == "POST": return 200, rt.deliver_evidence(payload.get("evidence", payload), payload.get("run_id"), payload.get("authority", 0))
        if path == "/v1/disposition/holds" and method == "GET": return 200, rt.list_holds()
        if len(parts) == 3 and parts[:2] == ["v1", "observe"] and method == "POST": return 200, rt.observe(parts[2], payload)
        if len(parts) == 3 and parts[:2] == ["v1", "finish"] and method == "POST": return 200, rt.finish(parts[2], payload.get("success", False), payload.get("termination"))
        if path == "/v1/relationship/construct" and method == "POST": return 200, rt.relationship_construct(payload.get("observations", []), payload.get("run_id"), payload.get("disabled_functions", []))
        if path == "/v1/relationship/qualify" and method == "POST": return 200, rt.relationship_qualify(payload.get("candidates", []), payload.get("validation", []), payload.get("run_id"), payload.get("disabled_functions", []))
        if path == "/v1/relationship/store" and method == "POST": return 200, rt.relationship_store(payload.get("relationships", []))
        if path == "/v1/relationship/retrieve" and method == "POST": return 200, rt.relationship_retrieve(payload.get("qualification", "VALID"), payload.get("input_keys", []))
        if path == "/v1/relationship/apply" and method == "POST": return 200, rt.relationship_apply(payload["relationship"], payload["observation"], payload.get("run_id"), payload.get("disabled_functions", []))
        if len(parts) == 3 and parts[:2] == ["v1", "trace"] and method == "GET": return 200, rt.trace(parts[2])
        if len(parts) == 3 and parts[:2] == ["v1", "replay"] and method == "POST": return 200, rt.replay(parts[2])
        raise KeyError("unknown endpoint")

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            status, result = self._dispatch("GET", parsed.path, {}, parse_qs(parsed.query))
            self._send(status, result)
        except KeyError as exc: self._send(404, {"error": str(exc)})
        except PermissionError as exc: self._send(403, {"error": str(exc)})
        except Exception as exc: self._send(400, {"error": str(exc)})

    def do_POST(self):
        try:
            status, result = self._dispatch("POST", urlparse(self.path).path, self._body())
            self._send(status, result)
        except KeyError as exc: self._send(404, {"error": str(exc)})
        except PermissionError as exc: self._send(403, {"error": str(exc)})
        except Exception as exc: self._send(400, {"error": str(exc)})

    def log_message(self, *_args):
        return


def serve(host: str = "127.0.0.1", port: int = 8787, data_dir: str = ".realcog-data"):
    runtime = ReferenceRuntime(data_dir)
    handler = type("RuntimeHandler", (ReferenceRequestHandler,), {"runtime": runtime})
    server = ThreadingHTTPServer((host, int(port)), handler)
    print(json.dumps({"listening": f"http://{host}:{port}", **runtime.health()}), flush=True)
    server.serve_forever()
