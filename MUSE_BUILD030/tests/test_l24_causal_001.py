import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "realcog")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters import cognitive_layer as cl, durable_memory as dm  # noqa: E402
from contract.disposition_engine import HoldStore  # noqa: E402
from contract.pipeline import Pipeline, Policy  # noqa: E402
from eval_l24.run_l24_causal_experiment import run_l24_experiment  # noqa: E402


class L24Causal001Tests(unittest.TestCase):
    def test_01_four_arm_c_strictly_outperforms_d_a_b_on_different_answer_transfer(self):
        with tempfile.TemporaryDirectory() as td:
            res = run_l24_experiment(Path(td))
            arms = res["arms"]
            self.assertTrue(res["c_outperforms_d"], res)
            self.assertTrue(res["c_outperforms_a_and_b"], res)
            self.assertEqual(arms["C"]["correct_decisions"], 6)
            self.assertEqual(arms["D"]["correct_decisions"], 0)
            self.assertEqual(arms["A"]["correct_decisions"], 0)
            self.assertEqual(arms["B"]["correct_decisions"], 0)
            self.assertEqual(arms["C"]["u10_consumptions"], 6)
            self.assertEqual(arms["C"]["u09_consumptions"], 6)
            self.assertEqual(arms["C"]["decision_changes_caused_by_l24"], 6)
            self.assertTrue(arms["C"]["persistent_method_retrieval"])

    def test_02_l24_hold_persists_when_evidence_absent_and_drop_on_incompatible(self):
        with tempfile.TemporaryDirectory() as td:
            pipe = Pipeline(td, Policy("v1"), soso_modules={"durable_memory": dm, "cognitive_layer": cl})
            try:
                res_hold = pipe.run({
                    "task_id": "L24-HOLD-01",
                    "signature": "sig-hold",
                    "family": "pump",
                    "tags": ["pump"],
                    "l24_features": ["high_current", "zero_flow", "unknown_supply_condition"],
                    "l24_method_records": [{
                        "source_case_id": "MOTOR-001",
                        "source_features": ["high_current", "zero_rotation", "unknown_supply_condition"],
                        "method": "discriminate_supply_failure_from_load_failure",
                        "discriminator": "pump_supply_voltage",
                        "candidate_map": {"abnormal": "SOSO-P2"},
                        "differences": ["motor_vs_pump"],
                    }],
                })
                rep = res_hold["l24_report"]
                self.assertEqual(rep["l24_outputs"][0]["disposition"], "HOLD")
                self.assertTrue(rep["u10_consumed"])
                holds = HoldStore(Path(td) / "disposition_holds.json").list_holds(status="PENDING")
                self.assertEqual(len(holds), 1)
            finally:
                pipe.close()

        conn = sqlite3.connect(":memory:")
        try:
            dm.ensure_tables(conn)
            with self.assertRaises(PermissionError):
                cl.recognize_cross_task_structure(conn, target_case_id="T1", target_features=["a"], authority=1)
            drops = cl.recognize_cross_task_structure(
                conn,
                target_case_id="T-DROP",
                target_features=["high_current"],
                target_conditions={"domain_mode": "pneumatic"},
                method_records=[{
                    "source_case_id": "M1",
                    "source_features": ["high_current"],
                    "method": "m1",
                    "discriminator": "v",
                    "candidate_map": {"x": "SOSO-P1"},
                    "required_context": {"domain_mode": "hydraulic"},
                }],
            )
            self.assertEqual(drops[0]["disposition"], "DROP")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
