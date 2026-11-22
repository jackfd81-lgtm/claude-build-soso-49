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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class UnifiedRealCog008Tests(unittest.TestCase):
    def test_01_realcog_cli_unified_pipeline_hold_resume_and_status(self):
        with tempfile.TemporaryDirectory() as td:
            h = subprocess.run(
                [sys.executable, "realcog_cli.py", "--data-dir", td, "health"],
                cwd=_ROOT, capture_output=True, text=True, check=True,
            )
            health = json.loads(h.stdout)
            self.assertEqual(health["execution_boundary"], "UNIFIED_REALCOG_SOSO_STANDALONE")
            self.assertTrue(health["unified_subsystems"]["realcog_2208_and_iou"])

            task_payload = {
                "task_id": "REALCOG-UNIFIED-01",
                "signature": "sig-unified",
                "family": "valve",
                "tags": ["valve", "linear"],
                "disposition_observation": {"obs_id": "OBS-RC-1", "facts": {"current": "high", "rotation": 0}},
                "disposition_gate": True,
            }
            p1 = subprocess.run(
                [sys.executable, "realcog_cli.py", "--data-dir", td, "pipeline-run", "--json", json.dumps(task_payload)],
                cwd=_ROOT, capture_output=True, text=True, check=True,
            )
            r1 = json.loads(p1.stdout)
            self.assertTrue(r1["trace_certified"])
            self.assertEqual(len(r1["disposition_report"]["evaluation"]["propagation"]["persisted_holds"]), 1)

            p2 = subprocess.run(
                [sys.executable, "realcog_cli.py", "--data-dir", td, "deliver-evidence", "--json", json.dumps({"supply_voltage": "0V_open_circuit"})],
                cwd=_ROOT, capture_output=True, text=True, check=True,
            )
            r2 = json.loads(p2.stdout)
            self.assertEqual(len(r2["reactivation"]["reactivated"]), 1)
            self.assertEqual(r2["reactivation"]["reactivated"][0]["events"][0]["disposition"], "PASS")

            p3 = subprocess.run(
                [sys.executable, "realcog_cli.py", "--data-dir", td, "realcog-status"],
                cwd=_ROOT, capture_output=True, text=True, check=True,
            )
            st = json.loads(p3.stdout)
            self.assertTrue(st["iou_db_exists"])
            self.assertEqual(st["holds"][0]["status"], "REACTIVATED")

    def test_02_http_unified_pipeline_and_realcog_status(self):
        port = _free_port()
        with tempfile.TemporaryDirectory() as td:
            proc = subprocess.Popen(
                [sys.executable, "realcog_cli.py", "--data-dir", td, "serve", "--host", "127.0.0.1", "--port", str(port)],
                cwd=_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                for _ in range(30):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as resp:
                            health = json.loads(resp.read().decode("utf-8"))
                            break
                    except Exception:
                        time.sleep(0.1)
                else:
                    out, err = proc.communicate(timeout=1)
                    self.fail(f"HTTP server failed to start: stdout={out!r} stderr={err!r}")
                self.assertTrue(health["unified_subsystems"]["pipeline"])

                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/pipeline/run",
                    data=json.dumps({
                        "task": {
                            "task_id": "HTTP-UNIFIED-01",
                            "signature": "sig-http-u",
                            "family": "valve",
                            "tags": ["valve"],
                            "disposition_observation": {"obs_id": "OBS-HU-1", "facts": {"current": "high", "rotation": 0}},
                        }
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    pr = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(pr["disposition_report"]["evaluation"]["propagation"]["persisted_holds"]), 1)

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/realcog/status", timeout=2.0) as resp:
                    st = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(st["iou_db_exists"])
                self.assertEqual(st["holds"][0]["status"], "PENDING")
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

    def test_03_soso_app_cli_realcog_status(self):
        with tempfile.TemporaryDirectory() as td:
            p = subprocess.run(
                [sys.executable, "soso_app.py", "--mode", "cli", "--state-dir", td, "--realcog-status"],
                cwd=_ROOT, capture_output=True, text=True, check=True,
            )
            st = json.loads(p.stdout)
            self.assertEqual(st["state_dir"], td)
            self.assertEqual(st["holds"], [])


if __name__ == "__main__":
    unittest.main()
