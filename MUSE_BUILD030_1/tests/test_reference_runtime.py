import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from contract.reference_http import ReferenceRequestHandler
from contract.reference_runtime import ReferenceRuntime
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]


class ReferenceRuntimeTests(unittest.TestCase):
    def test_library_task_observe_trace_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ReferenceRuntime(directory)
            created = runtime.task({"task_id": "api-1", "family": "fault", "tags": ["repair"]}, profile="auto", run_id="run-api")
            self.assertEqual(created["run_id"], "run-api")
            runtime.observe("run-api", {"function": "L01", "success": True, "resolved": True, "provenance": "simulated"})
            runtime.finish("run-api", True)
            trace = runtime.trace("run-api")
            replay = runtime.replay("run-api")
            self.assertTrue(trace["events"])
            self.assertEqual(trace["digest"], replay["digest"])
            self.assertFalse(replay["reexecuted"])

    def test_relationship_api_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ReferenceRuntime(directory)
            # MUSE-FUNCTIONAL-009 fixture update: training observations name a source.
            # Without one the engine stamps provenance "unknown" and the shared
            # apply boundary now (correctly) refuses the relationship.
            train = [{"input": {"value": 1}, "output": {"result": 4}, "source": "fixture:train-1"},
                     {"input": {"value": 2}, "output": {"result": 5}, "source": "fixture:train-2"}]
            validation = [{"input": {"value": 10}, "output": {"result": 13}}, {"input": {"value": 20}, "output": {"result": 23}}]
            candidates = runtime.relationship_construct(train)["candidates"]
            qualified = runtime.relationship_qualify(candidates, validation)["relationships"]
            valid = [r for r in qualified if r["qualification"] == "VALID"]
            self.assertTrue(valid)
            runtime.relationship_store(valid)
            retrieved = runtime.relationship_retrieve(input_keys=["value"])["relationships"]
            result = runtime.relationship_apply(retrieved[0], {"input": {"value": 100}, "expected_output": {"result": 103}})
            self.assertTrue(result["success"])

    def test_cli_health(self):
        completed = subprocess.run([sys.executable, "realcog_cli.py", "--data-dir", tempfile.mkdtemp(), "health"], cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(completed.stdout)["api"], "v1")

    def test_http_health_and_task(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ReferenceRuntime(directory)
            handler = type("TestHandler", (ReferenceRequestHandler,), {"runtime": runtime})
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(base + "/health") as response:
                    self.assertEqual(json.loads(response.read())["api"], "v1")
                body = json.dumps({"task": {"task_id": "http-1", "family": "fault", "tags": ["repair"]}, "run_id": "http-run"}).encode()
                request = urllib.request.Request(base + "/v1/task", data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(json.loads(response.read())["run_id"], "http-run")
            finally:
                server.shutdown(); thread.join(timeout=2); server.server_close()


if __name__ == "__main__":
    unittest.main()
