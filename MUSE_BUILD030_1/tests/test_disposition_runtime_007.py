import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "realcog")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters import cognitive_layer as real_cl, durable_memory as real_dm  # noqa: E402
from contract.pipeline import Pipeline, Policy  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DispositionRuntime007Tests(unittest.TestCase):
    def test_01_cli_hold_and_resume_across_os_process_restart(self):
        with tempfile.TemporaryDirectory() as td:
            task_payload = {
                "task_id": "CLI-DISP-001",
                "signature": "sig-disp-valve", "family": "valve",
                "tags": ["valve", "linear"],
                "disposition_observation": {
                    "obs_id": "OBS-CLI-1",
                    "facts": {"current": "high", "rotation": 0},
                },
                "disposition_gate": True,
            }
            p1 = subprocess.run(
                [sys.executable, "soso_app.py", "--mode", "cli", "--state-dir", td, "--task-json", json.dumps(task_payload)],
                cwd=_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            res1 = json.loads(p1.stdout)
            dr1 = res1["disposition_report"]
            self.assertEqual([e["disposition"] for e in dr1["evaluation"]["events"]], ["PASS", "DROP", "HOLD"])
            self.assertEqual(len(dr1["evaluation"]["propagation"]["persisted_holds"]), 1)
            hold_id = dr1["evaluation"]["propagation"]["persisted_holds"][0]
            self.assertIn("cognitive_gate", dr1)

            # Second OS process delivers evidence and resumes the suspended HOLD
            p2 = subprocess.run(
                [sys.executable, "soso_app.py", "--mode", "cli", "--state-dir", td, "--deliver-evidence-json", json.dumps({"supply_voltage": "0V_open_circuit"})],
                cwd=_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            res2 = json.loads(p2.stdout)
            react = res2["reactivation"]
            self.assertEqual(len(react["reactivated"]), 1)
            self.assertEqual(react["reactivated"][0]["hold_id"], hold_id)
            self.assertEqual(react["reactivated"][0]["events"][0]["disposition"], "PASS")
            self.assertTrue(react["reactivated"][0]["causal_parent_id"])
            self.assertTrue(any(ev.get("kind") == "VALIDATE" for ev in res2["causal_trace"]))

            # Third OS process verifies duplicate release is blocked
            p3 = subprocess.run(
                [sys.executable, "soso_app.py", "--mode", "cli", "--state-dir", td, "--deliver-evidence-json", json.dumps({"supply_voltage": "12V_sag"})],
                cwd=_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            res3 = json.loads(p3.stdout)
            self.assertEqual(res3["reactivation"]["reactivated"], [])
            self.assertIn(hold_id, res3["reactivation"]["duplicate_blocked"])

    def test_02_http_hold_resume_and_auth0_rejection(self):
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
                self.assertIn("disposition_hold_store", health)

                # 1. Submit task that triggers HOLD
                req1 = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/task",
                    data=json.dumps({
                        "task": {
                            "task_id": "HTTP-DISP-01",
                            "text": "motor stalled",
                            "tags": ["motor"],
                            "disposition_observation": {"obs_id": "OBS-HTTP-1", "facts": {"current": "high", "rotation": 0}},
                        }
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req1, timeout=2.0) as resp:
                    task_out = json.loads(resp.read().decode("utf-8"))
                holds = task_out["disposition"]["propagation"]["persisted_holds"]
                self.assertEqual(len(holds), 1)

                # 2. List holds
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/disposition/holds", timeout=2.0) as resp:
                    holds_out = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(holds_out["holds"][0]["status"], "PENDING")

                # 3. AUTH:0 violation returns 403
                req_auth = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/disposition/evidence",
                    data=json.dumps({"authority": 1, "evidence": {"supply_voltage": "0V"}}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req_auth, timeout=2.0)
                self.assertEqual(ctx.exception.code, 403)

                # 4. Deliver valid evidence and resume HOLD
                req2 = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/disposition/evidence",
                    data=json.dumps({"evidence": {"supply_voltage": "0V_open_circuit"}}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req2, timeout=2.0) as resp:
                    resumed = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(resumed["reactivation"]["reactivated"]), 1)
                self.assertEqual(resumed["reactivation"]["reactivated"][0]["events"][0]["disposition"], "PASS")
                self.assertTrue(resumed["causal_trace"])
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

    def test_03_pipeline_direct_run_and_cognitive_layer_gate(self):
        with tempfile.TemporaryDirectory() as td:
            p1 = Pipeline(td, Policy("v1"), soso_modules={"durable_memory": real_dm, "cognitive_layer": real_cl})
            try:
                r1 = p1.run({
                    "task_id": "PIPE-DISP-01",
                    "signature": "sig-disp-valve", "family": "valve",
                    "tags": ["valve"],
                    "disposition_observation": {"obs_id": "OBS-PIPE-1", "facts": {"current": "high", "rotation": 0}},
                })
                self.assertEqual(len(r1["disposition_report"]["evaluation"]["propagation"]["persisted_holds"]), 1)
                with self.assertRaises(PermissionError):
                    p1.run({"task_id": "PIPE-AUTH-FAIL", "authority": 2, "disposition_observation": {"obs_id": "OBS-X", "facts": {}}})
            finally:
                p1.close()

            p2 = Pipeline(td, Policy("v1"), soso_modules={"durable_memory": real_dm, "cognitive_layer": real_cl})
            try:
                r2 = p2.run({
                    "task_id": "PIPE-DISP-02",
                    "signature": "sig-disp-valve", "family": "valve",
                    "tags": ["valve"],
                    "deliver_evidence": {"supply_voltage": "0V_open_circuit"},
                })
                self.assertEqual(len(r2["disposition_report"]["reactivation"]["reactivated"]), 1)
            finally:
                p2.close()

        conn = sqlite3.connect(":memory:")
        try:
            real_dm.ensure_tables(conn)
            sel = real_cl.select_with_policy(conn, frozenset({"unknown"}), policy="LOWER_FIRST")
            gated_hold = real_cl.apply_disposition_gate(sel, execution_id="cl-1", obs_id="OBS-CL-1", missing_conditions=["firmware"])
            self.assertEqual(gated_hold["event"]["disposition"], "HOLD")
            gated_drop = real_cl.apply_disposition_gate(sel, execution_id="cl-2", obs_id="OBS-CL-2")
            self.assertEqual(gated_drop["event"]["disposition"], "DROP")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
