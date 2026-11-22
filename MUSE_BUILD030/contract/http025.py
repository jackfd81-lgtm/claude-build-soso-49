"""Loopback investigation API using the same authenticated calls as the CLI."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import signal
import sqlite3
from threading import BoundedSemaphore, Thread
from urllib.parse import unquote, urlsplit

from muse024 import call

MAX_BODY = 65536
PATH = re.compile(r"/v1/investigations/([^/]+)(?:/(evidence|expire|learn))?")
AGENT = re.compile(r"/v1/agent/(runs|streams)/([^/]+)(?:/(evidence|expire|learn|frame|close))?")
METHOD = re.compile(r"/v1/methods/([a-f0-9]{64})/(qualify|revoke)")


class Server(ThreadingHTTPServer):
    daemon_threads = False
    request_queue_size = 16

    def __init__(self, root, port):
        self.root = root
        self.slots = BoundedSemaphore(16)
        super().__init__(("127.0.0.1", port), Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def log_message(self, *_args):
        return

    def reply(self, code, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            raise ValueError("one Content-Length is required")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("application/json is required")
        length = int(self.headers["Content-Length"])
        if not 0 < length <= MAX_BODY:
            raise ValueError("invalid request length")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError("incomplete request body")
        value = json.loads(data, object_pairs_hook=unique_keys)
        if not isinstance(value, dict) or "operation" in value:
            raise ValueError("expected an object with no operation override")
        if "authority" not in value:
            raise ValueError("explicit authority is required")
        return value

    def route(self, method):
        path = urlsplit(self.path).path
        if path == "/health" and method == "GET":
            self.reply(200, {"status": "ready", "authority": 0})
            return
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or len(self.headers.get_all("Authorization", [])) != 1:
            self.reply(401, {"error": "bearer_required"})
            return
        try:
            match = PATH.fullmatch(path)
            stored = METHOD.fullmatch(path)
            agent = AGENT.fullmatch(path)
            if path == "/v1/agent/configuration" and method in ("GET", "POST"):
                task = ({"operation": "agent_configuration", "authority": 0} if method == "GET"
                        else dict(self.body(), operation="agent_configure"))
            elif path == "/v1/agent/inventory" and method == "GET":
                task = {"authority": 0, "operation": "agent_inventory"}
            elif method == "POST" and path in ("/v1/agent/runs", "/v1/agent/streams"):
                task = dict(self.body(), operation="agent_start" if path.endswith("/runs") else "agent_stream")
            elif agent and method == "GET" and agent[1] == "runs" and agent[3] is None:
                task = {"operation": "agent_show", "authority": 0, "run": unquote(agent[2], errors="strict")}
            elif agent and method == "POST" and agent[3] in (
                    ("evidence", "expire", "learn") if agent[1] == "runs" else ("frame", "close")):
                body = self.body()
                field = "run" if agent[1] == "runs" else "stream"
                if field in body:
                    raise ValueError("agent identity belongs in the URL")
                op = "resume" if agent[3] == "evidence" else agent[3]
                task = dict(body, operation="agent_" + op, **{field: unquote(agent[2], errors="strict")})
            elif method == "GET" and match and match[2] is None:
                task = {"operation": "show025", "authority": 0, "run": unquote(match[1], errors="strict")}
            elif method == "POST" and path == "/v1/investigations":
                task = dict(self.body(), operation="start025")
            elif method == "POST" and match and match[2] is not None:
                body = self.body()
                if "run" in body:
                    raise ValueError("run identity belongs in the URL")
                op = {"evidence": "resume025", "expire": "expire025", "learn": "learn025"}[match[2]]
                task = dict(body, operation=op, run=unquote(match[1], errors="strict"))
            elif method == "POST" and stored:
                body = self.body()
                if "method" in body:
                    raise ValueError("method identity belongs in the URL")
                task = dict(body, operation=stored[2] + "025", method=stored[1])
            else:
                self.reply(404, {"error": "unknown_route"})
                return
            result = call(self.server.root, header[7:], task)
        except PermissionError:
            self.reply(403, {"error": "denied"})
        except KeyError:
            self.reply(404, {"error": "not_found"})
        except (ValueError, TypeError, OverflowError, RecursionError, TimeoutError):
            self.reply(400, {"error": "invalid_request"})
        except (sqlite3.Error, OSError):
            self.reply(503, {"error": "state_unavailable"})
        else:
            self.reply(200, result)

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")


def unique_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate object key")
        data[key] = value
    return data


def serve(root, port):
    if not 0 <= port <= 65535:
        raise ValueError("invalid TCP port")
    if not (root / "observations.sqlite3").is_file():
        raise ValueError("initialize the state before serving it")
    with Server(root, port) as server:
        def stop(_signal, _frame):
            Thread(target=server.shutdown, daemon=True).start()

        original = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            print(json.dumps({"listening": server.server_address[1], "host": "127.0.0.1"}), flush=True)
            server.serve_forever()
        finally:
            for sig, handler in original.items():
                signal.signal(sig, handler)
