import json
import unittest
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from eval022.eval004_audit005 import run_audit005, run_procedural_learning_4arm  # noqa: E402


class Eval004Audit005Tests(unittest.TestCase):
    def test_four_arm_procedural_learning_b_beats_a_and_c(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            res = run_procedural_learning_4arm(Path(td))
        self.assertEqual(res["A_no_memory"]["correct"], 0)
        self.assertEqual(res["B_correct_method_after_restart"]["correct"], 2)
        self.assertEqual(res["C_shuffled_method_after_restart"]["correct"], 0)
        self.assertEqual(res["D_oracle_control"]["correct"], 2)
        self.assertEqual(res["B_correct_method_after_restart"]["useful_requests"], 6)
        self.assertEqual(res["C_shuffled_method_after_restart"]["invalid_requests"], 4)

    def test_audit005_explanations_and_ablations(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            out = run_audit005(Path(td))
            self.assertTrue((Path(td) / "SOSO_49_BRAINS_EXECUTION_EXPLANATION.json").exists())
            self.assertTrue((Path(td) / "SOSO_49_BRAINS_EXECUTION_EXPLANATION.md").exists())
        inv = out["explanation"]["function_inventory"]
        self.assertEqual(inv["registered_positions"], 147)
        self.assertEqual(inv["governance_not_cognitive"], ["U02"])
        self.assertTrue(out["ablations"]["U09"]["changed_outcome"])
        self.assertTrue(out["ablations"]["U37"]["changed_outcome"])


if __name__ == "__main__":
    unittest.main()
