"""Directive 018: Full 98-Function (Lower 49 + Higher 49) Wiring & Coherent Runtime Tests."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract.brains_wiring import BrainsWiringRegistry  # noqa: E402
from soso_app import run_cli_realcog_status, run_cli_task  # noqa: E402


class Full98WiringTests(unittest.TestCase):
    def test_01_all_98_functions_wired_and_auth0_enforced(self) -> None:
        traced: list[str] = []
        reg = BrainsWiringRegistry(trace_fn=lambda fid, res: traced.append(fid))
        summary = reg.summary()
        self.assertEqual(summary["total_wired"], 98)
        self.assertEqual(summary["lower_49_wired"], 49)
        self.assertEqual(summary["higher_49_wired"], 49)
        self.assertEqual(summary["core_specialized"], 15)
        self.assertEqual(summary["region_routed"], 83)
        self.assertEqual(summary["regions"], 7)

        for i in range(1, 50):
            r_l = reg.invoke(f"L{i:02d}", {"task_id": "wire_018", "authority": 0})
            r_u = reg.invoke(f"U{i:02d}", {"task_id": "wire_018", "authority": 0})
            self.assertEqual(r_l["status"], "OK")
            self.assertEqual(r_u["status"], "OK")
            self.assertEqual(r_l["region_id"], r_u["region_id"])

        self.assertEqual(len(traced), 98)
        with self.assertRaises(PermissionError):
            reg.invoke("L01", {"task_id": "wire_018", "authority": 1})

    def test_02_soso_app_status_exposes_complete_wiring_and_memory_topology(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "wired_state"
            out = run_cli_task(
                state,
                json.dumps({
                    "task_id": "full_wire_check_018",
                    "description": "Verify unified 98-function wiring through soso_app",
                    "domain": "integration",
                    "signature": "full_wire_check_018",
                    "family": "wiring_verification",
                    "tags": ["wiring", "lower49", "higher49"],
                }),
            )
            self.assertTrue(out["trace_certified"])
            status = run_cli_realcog_status(state)
            self.assertEqual(status["brains_wiring"]["total_wired"], 98)
            self.assertEqual(status["memory_slots"]["total"], 2208)
            self.assertTrue(status["iou_db_exists"])


if __name__ == "__main__":
    unittest.main()
