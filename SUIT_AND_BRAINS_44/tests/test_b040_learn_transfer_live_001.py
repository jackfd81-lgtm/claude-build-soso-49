"""Tests for B040-LEARN-TRANSFER-105-LIVE-001 4-condition learning, ablation, unseen transfer, and coherence."""
from __future__ import annotations

import unittest

from build040_runtime.run_qualification_b040 import run_qualification


class TestB040LearnTransferLive001(unittest.TestCase):
    def test_four_condition_learning_transfer_and_coherence(self) -> None:
        rep = run_qualification(v2_challenge_path=None)
        p2 = rep["phases"]["phase_2_cold_baseline"]
        p4 = rep["phases"]["phase_4_retention_and_ablation"]
        p5 = rep["phases"]["phase_5_unseen_transfer"]
        p7 = rep["phases"]["phase_7_coherence"]

        self.assertEqual(p2["optimal_hits"], 0)
        self.assertEqual(p4["learned_memory_gemini_off_hits"], "3/3")
        self.assertEqual(p4["memory_disabled_gemini_off_hits"], "0/3")
        self.assertEqual(p5["unseen_with_memory_hits"], "2/2")
        self.assertEqual(p5["unseen_without_memory_hits"], "0/2")
        self.assertTrue(p5["inappropriate_method_rejected"])
        self.assertEqual(p7["loopback_http_grader"], "PASS")
        self.assertTrue(p7["unauthorized_write_denied"])


if __name__ == "__main__":
    unittest.main()
