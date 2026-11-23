import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Test020Acceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        completed = subprocess.run([sys.executable, "selection_work/test020.py"], cwd=ROOT,
                                   capture_output=True, text=True, check=True)
        cls.output = json.loads(completed.stdout)
        with open(ROOT / "selection_work/TEST020_RESULTS.json", encoding="utf-8") as stream:
            cls.results = json.load(stream)

    def test_controls_have_expected_transfer_pattern(self):
        controls = self.results["summary"]["controls"]
        self.assertFalse(controls["A_no_relationship_memory"]["success"])
        self.assertTrue(controls["B_correct_memory"]["success"])
        self.assertFalse(controls["C_shuffled_memory"]["success"])
        self.assertFalse(controls["D_direct_evidence_only"]["success"])

    def test_candidates_are_competing_and_validation_discriminates(self):
        qualified = self.results["controls"]["B_correct_memory"]["qualified"]
        self.assertGreaterEqual(len(qualified), 2)
        states = {candidate["qualification"] for candidate in qualified}
        self.assertIn("VALID", states)
        self.assertIn("INVALID", states)

    def test_restart_retrieval_and_recording(self):
        self.assertEqual(self.results["summary"]["restart_retrieval_count"], 1)
        for control in self.results["controls"].values():
            self.assertTrue(control["complete_recording"])
            self.assertGreater(control["event_count"], 0)

    def test_six_ablations_are_reported(self):
        rows = self.results["ablations"]
        self.assertEqual({row["function"] for row in rows}, {"L24", "L30", "U08", "U09", "U10", "U37"})
        self.assertTrue(all(row["outcome_difference"] for row in rows))


if __name__ == "__main__":
    unittest.main()
