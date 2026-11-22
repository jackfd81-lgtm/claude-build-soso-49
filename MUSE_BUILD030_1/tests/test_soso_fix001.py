import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "realcog")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters import cognitive_layer as real_cl, durable_memory as real_dm  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SosoFix001Tests(unittest.TestCase):
    def test_contract_harness_imports_portably(self):
        import contract.harness as h
        self.assertGreaterEqual(len(h.TRAINING), 1)
        self.assertIn("durable_memory", h.SOSO_MODULES)
        self.assertIn("cognitive_layer", h.SOSO_MODULES)

    def test_http_mode_starts_and_serves_health_and_task(self):
        port = _free_port()
        with tempfile.TemporaryDirectory() as td:
            proc = subprocess.Popen(
                [sys.executable, "soso_app.py", "--mode", "http", "--host", "127.0.0.1", "--port", str(port), "--state-dir", td],
                cwd=_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                url_health = f"http://127.0.0.1:{port}/health"
                for _ in range(30):
                    try:
                        with urllib.request.urlopen(url_health, timeout=0.5) as resp:
                            health = json.loads(resp.read().decode("utf-8"))
                            break
                    except Exception:
                        time.sleep(0.1)
                else:
                    out, err = proc.communicate(timeout=1)
                    self.fail(f"HTTP server failed to start: stdout={out!r} stderr={err!r}")

                self.assertEqual(health.get("api"), "v1", health)

                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/task",
                    data=json.dumps({"task": {"task_id": "HTTP_SMOKE_01", "text": "diagnose", "tags": ["linear"]}}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    task_out = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(task_out["state"]["task_id"], "HTTP_SMOKE_01")
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

    def test_cognitive_layer_span_emitter_fires_on_abstention_and_selection(self):
        import sqlite3; conn = sqlite3.connect(":memory:")
        try:
            real_dm.ensure_tables(conn)
            emitted = []
            # 1. Abstention / escalation when nothing is learned
            res_abs = real_cl.select_with_policy(
                conn,
                frozenset({"unknown_tag"}),
                policy="LOWER_FIRST",
                span_emitter=lambda d: emitted.append(("abstain", d)),
                span_tags=frozenset({"unknown_tag"}),
            )
            self.assertEqual(res_abs["stage"], "escalated")
            self.assertEqual(res_abs["procedures"], [])
            self.assertEqual(len(emitted), 1)
            self.assertEqual(emitted[0][0], "abstain")
            self.assertTrue(emitted[0][1])

            # 2. Selection after learning a procedure
            real_cl.learn_attempts(conn, "c1", ["disk_full", "linux"], [("clear_cache", "SUCCESS")], validator=lambda *a: True)
            res_sel = real_cl.select_with_policy(
                conn,
                frozenset({"disk_full", "linux"}),
                policy="LOWER_FIRST",
                span_emitter=lambda d: emitted.append(("select", d)),
                span_tags=frozenset({"disk_full", "linux"}),
            )
            self.assertIn(res_sel["stage"], {"lower", "higher"})
            self.assertIn("clear_cache", res_sel["procedures"])
            self.assertEqual(len(emitted), 2)
            self.assertNotEqual(emitted[0][1], emitted[1][1])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
