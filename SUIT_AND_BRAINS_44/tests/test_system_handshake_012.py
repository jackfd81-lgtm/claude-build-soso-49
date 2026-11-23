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


class SystemHandshake012Tests(unittest.TestCase):
    def test_01_soso_app_cli_handshake(self):
        with tempfile.TemporaryDirectory() as td:
            p = subprocess.run(
                [sys.executable, "soso_app.py", "--mode", "cli", "--state-dir", td, "--handshake"],
                cwd=_ROOT, capture_output=True, text=True, check=True,
            )
            res = json.loads(p.stdout)
            self.assertEqual(res["handshake_status"], "ALL_UNITS_COHERENT")
            self.assertEqual(len(res["units"]), 14)
            self.assertEqual(res["mandatory_passed"], 12)
            self.assertEqual(res["lifecycle_state"], "READY")

    def test_02_realcog_cli_and_http_handshake(self):
        with tempfile.TemporaryDirectory() as td:
            p = subprocess.run(
                [sys.executable, "realcog_cli.py", "--data-dir", td, "handshake"],
                cwd=_ROOT, capture_output=True, text=True, check=True,
            )
            res = json.loads(p.stdout)
            self.assertEqual(res["handshake_status"], "ALL_UNITS_COHERENT")

        port = _free_port()
        with tempfile.TemporaryDirectory() as td:
            proc = subprocess.Popen(
                [sys.executable, "soso_app.py", "--mode", "http", "--host", "127.0.0.1", "--port", str(port), "--state-dir", td],
                cwd=_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                for _ in range(30):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/handshake", timeout=1.0) as resp:
                            hres = json.loads(resp.read().decode("utf-8"))
                            break
                    except Exception:
                        time.sleep(0.1)
                else:
                    out, err = proc.communicate(timeout=1)
                    self.fail(f"HTTP server failed: stdout={out!r} stderr={err!r}")
                self.assertEqual(hres["handshake_status"], "ALL_UNITS_COHERENT")
                self.assertEqual(hres["lifecycle_state"], "READY")
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
