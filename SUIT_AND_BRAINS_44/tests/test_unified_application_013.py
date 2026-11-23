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


class UnifiedApplication013Tests(unittest.TestCase):
    def test_01_behavioral_14_unit_handshake_and_status(self):
        with tempfile.TemporaryDirectory() as td:
            p_st = subprocess.run(
                [sys.executable, "soso_app.py", "--mode", "status", "--state-dir", td],
                cwd=_ROOT, capture_output=True, text=True, check=True,
            )
            st = json.loads(p_st.stdout)
            self.assertEqual(st["lifecycle_state"], "READY")
            self.assertEqual(st["handshake_status"], "ALL_UNITS_COHERENT")
            self.assertEqual(st["mandatory_passed"], 12)
            self.assertEqual(st["optional_passed"], 2)
            self.assertEqual(st["native_vision_status"], "UNSUPPORTED")
            self.assertEqual(st["memory_inventory"]["big49_configured_capacity"], 1600)
            self.assertEqual(st["memory_inventory"]["small49_configured_capacity"], 608)

            p_hs = subprocess.run(
                [sys.executable, "soso_app.py", "--mode", "cli", "--state-dir", td, "--handshake"],
                cwd=_ROOT, capture_output=True, text=True, check=True,
            )
            hs = json.loads(p_hs.stdout)
            self.assertEqual(len(hs["units"]), 14)
            for u in hs["units"]:
                self.assertTrue(u["behavioral_test_passed"], u)
                for field in ("present", "initialized", "executable_vs_spec", "capability", "dependency_status", "behavioral_test_passed", "last_error"):
                    self.assertIn(field, u)

    def test_02_fault_injection_blocks_all_units_coherent(self):
        with tempfile.TemporaryDirectory() as td:
            # Mandatory unit failure -> FAILED, exit code 1, NEVER ALL_UNITS_COHERENT
            p_fail = subprocess.run(
                [sys.executable, "soso_app.py", "--mode", "cli", "--state-dir", td, "--handshake", "--inject-fault", "realcog.ph6_brains_2208.Big49_Small49"],
                cwd=_ROOT, capture_output=True, text=True,
            )
            self.assertEqual(p_fail.returncode, 1)
            res_fail = json.loads(p_fail.stdout)
            self.assertEqual(res_fail["handshake_status"], "FAILED")
            self.assertEqual(res_fail["lifecycle_state"], "FAILED")
            self.assertNotEqual(res_fail["handshake_status"], "ALL_UNITS_COHERENT")
            self.assertIn("realcog.ph6_brains_2208.Big49_Small49", res_fail["mandatory_failed"])

            # Optional unit failure -> DEGRADED, exit code 0, unrelated mandatory units pass
            p_deg = subprocess.run(
                [sys.executable, "soso_app.py", "--mode", "cli", "--state-dir", td, "--handshake", "--optional-fault", "brain49_path_explain"],
                cwd=_ROOT, capture_output=True, text=True, check=True,
            )
            res_deg = json.loads(p_deg.stdout)
            self.assertEqual(res_deg["handshake_status"], "DEGRADED")
            self.assertEqual(res_deg["lifecycle_state"], "DEGRADED")
            self.assertEqual(res_deg["mandatory_passed"], 12)
            self.assertIn("brain49_path_explain", res_deg["optional_failed"])

    def test_03_mode_run_single_shot_and_http_equivalence(self):
        with tempfile.TemporaryDirectory() as td:
            p_run = subprocess.run(
                [sys.executable, "soso_app.py", "--mode", "run", "--state-dir", td, "--task-json", json.dumps({
                    "task_id": "RUN-01", "signature": "sig-run", "family": "num", "tags": ["num"],
                    "cognitive_domain": "number", "text": "10 * 5 + 2",
                })],
                cwd=_ROOT, capture_output=True, text=True, check=True,
            )
            out = json.loads(p_run.stdout)
            self.assertEqual(out["lifecycle_state"], "READY")
            self.assertEqual(out["result"]["cognitive_result"]["answer"], 52)

        port = _free_port()
        with tempfile.TemporaryDirectory() as td:
            proc = subprocess.Popen(
                [sys.executable, "soso_app.py", "--mode", "run", "--host", "127.0.0.1", "--port", str(port), "--state-dir", td],
                cwd=_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                for _ in range(30):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/status", timeout=1.0) as resp:
                            st = json.loads(resp.read().decode("utf-8"))
                            break
                    except Exception:
                        time.sleep(0.1)
                else:
                    out, err = proc.communicate(timeout=1)
                    self.fail(f"HTTP server failed: stdout={out!r} stderr={err!r}")
                self.assertEqual(st["lifecycle_state"], "READY")

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/handshake?inject_fault=contract.disposition_engine", timeout=2.0) as resp:
                    hs_fail = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(hs_fail["handshake_status"], "FAILED")
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
