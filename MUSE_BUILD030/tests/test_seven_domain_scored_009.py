import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from eval009.run_seven_domain_eval import run_seven_domain_suite, run_transfer_ablation  # noqa: E402


class SevenDomainScored009Tests(unittest.TestCase):
    def test_01_scored_seven_domain_suite_via_real_cli(self):
        with tempfile.TemporaryDirectory() as td:
            summary = run_seven_domain_suite(Path(td))
            self.assertEqual(summary["total_cases"], 21)
            self.assertEqual(summary["counts"]["INCORRECT"], 0, summary)
            self.assertEqual(summary["counts"]["UNSUPPORTED"], 3, summary)
            self.assertEqual(summary["counts"]["JUSTIFIED_ABSTENTION"], 3, summary)
            self.assertEqual(summary["counts"]["CORRECT"], 15, summary)
            per_case = Path(td) / "seven_domain_per_case.jsonl"
            rows = [json.loads(line) for line in per_case.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 21)
            for r in rows:
                for key in ("problem_id", "domain", "input", "expected_outcome", "actual_response",
                            "classification", "evidence_requested", "evidence_received",
                            "functions_invoked", "memory_reads", "memory_writes",
                            "disposition", "duration_ms", "trace_ref", "scoring_rationale"):
                    self.assertIn(key, r)

    def test_02_four_arm_learning_and_method_transfer_across_os_restart(self):
        with tempfile.TemporaryDirectory() as td:
            rep = run_transfer_ablation(Path(td))
            self.assertFalse(rep["arm_1_no_memory"]["resolved"])
            self.assertFalse(rep["arm_2_memory_no_transfer"]["resolved"])
            self.assertTrue(rep["arm_3_method_transfer_across_os_restart"]["resolved"])
            self.assertTrue(rep["arm_4_misleading_prior_rejected"]["misleading_p1_blocked"])


if __name__ == "__main__":
    unittest.main()
