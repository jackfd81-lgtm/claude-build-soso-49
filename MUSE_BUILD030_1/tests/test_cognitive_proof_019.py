"""Directive 019: Seven-Domain Cognitive Proof & Video Observation Recording Tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract.cognitive_recorder import CognitiveRecorder  # noqa: E402
from eval009.run_seven_domain_eval import run_seven_domain_suite  # noqa: E402


class CognitiveProof019Tests(unittest.TestCase):
    def test_01_seven_domain_zero_incorrect(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            res = run_seven_domain_suite(Path(td))
            self.assertEqual(res["total_cases"], 21)
            by_dom = res["by_domain"]
            for dom, counts in by_dom.items():
                self.assertEqual(counts["INCORRECT"], 0, f"Unexpected incorrect in {dom}")
            self.assertEqual(by_dom["mathematics"]["CORRECT"], 3)
            self.assertEqual(by_dom["grammar"]["CORRECT"], 2)
            self.assertEqual(by_dom["time"]["CORRECT"], 2)
            self.assertEqual(by_dom["pictures"]["UNSUPPORTED"], 3)

    def test_02_video_caption_observation_records_as_pass(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rec = CognitiveRecorder(str(Path(td) / "obs_rec.jsonl"), run_id="obs_019", task_id="vid_caption_obs")
            rec.record(
                "OBSERVE",
                payload={
                    "task_type": "OBSERVATION_RECORDING",
                    "displayed_caption": "MCQUEEN PULLED UP TO ANGEL FERNANDEZ'S HOUSE AFTER SHE WAS HANGING OUT WITH NLE CHOPPA AND WENT OFF ON HIS CHAT",
                    "observation_status": "OBSERVATION_PASS",
                    "off_camera_claim_status": "NOT_INVESTIGATED_OUT_OF_SCOPE_FOR_RECORDING",
                },
            )
            rec.record("VALIDATE", payload={"run_complete": True, "disposition": "PASS"})
            events = rec.replay()
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["payload"]["observation_status"], "OBSERVATION_PASS")
            self.assertEqual(events[1]["payload"]["disposition"], "PASS")


if __name__ == "__main__":
    unittest.main()
