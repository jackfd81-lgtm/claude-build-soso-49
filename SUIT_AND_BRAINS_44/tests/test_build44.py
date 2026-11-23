import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_build43 as baseline
from test_build43 import ADMIN, HYPOTHESES, _matching_obs, local_runtime
from build040_runtime.online_ai import AIResponse, OnlineAIGateway
from build040_runtime.precision_315 import BlockedGenerationError, Precision315Constructor


class Build44Tests(unittest.TestCase):
    def test_failed_return_crossing_cannot_complete(self):
        with tempfile.TemporaryDirectory() as tmp, local_runtime(Path(tmp), ADMIN) as (_, closet, _):
            inv = baseline.JoinedHigherFirstTests()._setup(closet)
            inv.execute_mode("jhf-1", hypotheses=copy.deepcopy(HYPOTHESES), observed={})
            with patch.object(inv, "_higher_route", return_value={"synthesized": None}):
                out = inv.receive_observation(
                    "jhf-1", _matching_obs("tox_screen", "med:late", "positive_opioid"))
            self.assertEqual(out["join"]["status"], "FAILED")
            self.assertEqual(out["case"]["status"], "UNRESOLVED")

    def test_higher_first_without_work_cannot_complete(self):
        with tempfile.TemporaryDirectory() as tmp, local_runtime(Path(tmp), ADMIN) as (_, closet, _):
            inv = baseline.JoinedHigherFirstTests()._setup(closet)
            case = inv.execute_mode("jhf-1", hypotheses={}, observed={})
            self.assertEqual(case["status"], "UNRESOLVED")

    def test_higher_first_all_315_sections(self):
        fixture = json.loads((Path(__file__).resolve().parents[1]
                              / "fixtures/PRECISION315_BLIND_V2.json").read_text())
        evidence = []
        for segment in fixture["segments"]:
            first, selected, _ = segment["locked_positions"]
            earlier = fixture["positions"][first - 1]["text"]
            incoming = fixture["positions"][selected - 1]["text"]
            feature = f"position-{selected}"
            source = f"fixture:{selected}"
            case_id = f"higher-first-{segment['segment']:02d}"
            hypotheses = {"previous": {feature: earlier}, "incoming": {feature: incoming}}
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with local_runtime(root, ADMIN) as (_, closet, _):
                    inv = baseline._investigation(closet)
                    inv.start_case(
                        case_id, mode="joined_higher_first", goal=segment["objective"],
                        scope=[feature], acceptable_sources=[source],
                        evidence_snapshot={f"fixture:{first}": {"key": feature, "value": earlier}},
                        hypotheses=hypotheses, observed={})
                    held = inv.execute_mode(case_id, hypotheses=hypotheses, observed={})
                    self.assertEqual(held["status"], "HOLD")
                    wrong = inv.receive_observation(
                        case_id, _matching_obs("wrong-position", source, incoming, obs_id="wrong"))
                    self.assertEqual(wrong["outcome"], "WRONG_FEATURE")
                with local_runtime(root, ADMIN) as (_, closet, _):
                    closet.suit.authorize_agent(
                        "L01", {"memory.read", "memory.write", "task.run"},
                        allowed_block_ids={baseline.case_block_id(0)}, max_payload_bytes=65536)
                    inv = baseline.Investigation(closet, "L01")
                    inv.load_case(case_id, 0)
                    reading = _matching_obs(feature, source, incoming, obs_id="matching")
                    result = inv.receive_observation(case_id, reading)
                    case = copy.deepcopy(result["case"])
                    self.assertEqual(case["status"], "COMPLETED")
                    self.assertEqual([join["status"] for join in case["joins"]],
                                     ["CONSUMED", "CONSUMED"])
                    self.assertEqual(case["decision"]["status"], "CONFLICT")
                    for join in case["joins"]:
                        slot = next(row for row in case["invocations"]
                                    if row["output_ref"] == join["consumed_output_ref"])
                        self.assertEqual(slot["input_ref"], join["producer"]["output_ref"])
                    repeat = inv.receive_observation(case_id, reading)
                    self.assertEqual(repeat["case"]["revision"], case["revision"])
                    self.assertEqual(len(repeat["case"]["invocations"]), len(case["invocations"]))
                    evidence.append({"segment": segment["segment"], "fixture_positions": [first, selected],
                                     "case": case, "duplicate_outcome": repeat["outcome"]})
        if output := os.environ.get("BUILD441_HIGHER_FIRST_TRACE"):
            Path(output).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")

    def test_native_315_provider_and_storage(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures/PRECISION315_BLIND_V2.json"
        provider = Mock()
        def generate(prompt, **kwargs):
            request = json.loads(prompt)
            lines = dict.fromkeys(request["open_positions"], "Synthetic test text exercises each open position.")
            return AIResponse("test", "synthetic", json.dumps(lines), True, 0, 0, 0, "test",
                              from_replay=True)
        provider.generate.side_effect = generate
        gateway = OnlineAIGateway(provider)
        with tempfile.TemporaryDirectory() as tmp, local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
            for address in range(315):
                ctrl.register_block_owner(address, "L01", ADMIN)
            closet.suit.authorize_agent("L01", {"memory.write", "memory.read"},
                                        allowed_block_ids=set(range(315)), max_steps=630)
            with self.assertRaises(BlockedGenerationError):
                Precision315Constructor(closet.suit).construct(fixture)
            self.assertEqual(ctrl.inspect(ADMIN)["committed"], 0)
            result = Precision315Constructor(closet.suit, gateway).construct(fixture)
            self.assertEqual(provider.generate.call_count, 15)
            self.assertEqual(len(result["positions"]), 315)
            self.assertEqual(ctrl.inspect(ADMIN)["committed"], 315)
            self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)
            self.assertEqual(closet.suit.execute_memory_read("L01", "BIG49", 0)["payload"]["text"],
                             result["positions"][0]["text"])


if __name__ == "__main__":
    unittest.main()
