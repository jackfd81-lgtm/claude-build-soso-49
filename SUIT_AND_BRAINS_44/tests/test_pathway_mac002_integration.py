import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from contract.cognitive_recorder import CognitiveRecorder
from contract.mac002_adapter import Mac002PathwayAdapter, qualify_record_against_observations
from contract.pipeline import Pipeline, Policy
from contract.relationship_store import RelationshipStore


from adapters import durable_memory as _real_dm, cognitive_layer as _real_cl
_TEST_SOSO = {"durable_memory": _real_dm, "cognitive_layer": _real_cl}

def _obs_store():
    return {
        "obs-1": {"observation_id": "obs-1", "input": {"value": 10}, "output": {"result": 13}},
        "obs-2": {"observation_id": "obs-2", "input": {"value": 20}, "output": {"result": 23}},
        "obs-dup": {"observation_id": "obs-dup", "input": {"value": 10}, "output": {"result": 13}},
        "obs-stale": {"observation_id": "obs-stale", "input": {"value": 30}, "output": {"result": 33}, "stale": True},
        "obs-deleted": {"observation_id": "obs-deleted", "input": {"value": 40}, "output": {"result": 43}, "deleted": True},
        "obs-m1": {"observation_id": "obs-m1", "input": {"value": 5}, "output": {"result": 10}},
        "obs-m2": {"observation_id": "obs-m2", "input": {"value": 6}, "output": {"result": 12}},
        "obs-bad": {"observation_id": "obs-bad", "input": {"value": 10}, "output": {"result": 999}},
    }


def _records():
    return {
        "valid_add3": {
            "relationship_id": "rel-valid-add3",
            "structure": {"kind": "add", "input_key": "value", "output_key": "result", "delta": 3},
            "evidence_ids": ["obs-1", "obs-2"],
            "provenance": ["alpha-observed"],
            "qualification": "VALID",
            "confidence": 1.0,
            "applicability": {"input_keys": ["value"], "output_keys": ["result"], "relation_kind": "linear", "tags": ["linear"]},
        },
        "competing_mul2": {
            "relationship_id": "rel-competing-mul2",
            "structure": {"kind": "multiply", "input_key": "value", "output_key": "result", "factor": 2},
            "evidence_ids": ["obs-m1", "obs-m2"],
            "provenance": ["beta-observed"],
            "qualification": "VALID",
            "confidence": 0.95,
            "applicability": {"input_keys": ["value"], "output_keys": ["result"], "relation_kind": "linear", "tags": ["linear"]},
        },
        "forged_prov": {
            "relationship_id": "rel-forged",
            "structure": {"kind": "add", "input_key": "value", "output_key": "result", "delta": 3},
            "evidence_ids": ["obs-1", "obs-2"],
            "provenance": ["fake:forged-99"],
            "qualification": "VALID",
            "confidence": 0.99,
            "applicability": {"input_keys": ["value"], "output_keys": ["result"], "relation_kind": "linear", "tags": ["linear"]},
        },
        "unresolvable_ev": {
            "relationship_id": "rel-unresolvable",
            "structure": {"kind": "add", "input_key": "value", "output_key": "result", "delta": 3},
            "evidence_ids": ["obs-1", "obs-does-not-exist"],
            "provenance": ["alpha-observed"],
            "qualification": "VALID",
            "confidence": 0.99,
            "applicability": {"input_keys": ["value"], "output_keys": ["result"], "relation_kind": "linear", "tags": ["linear"]},
        },
        "duplicate_obs": {
            "relationship_id": "rel-dup-obs",
            "structure": {"kind": "add", "input_key": "value", "output_key": "result", "delta": 3},
            "evidence_ids": ["obs-1", "obs-dup"],
            "provenance": ["alpha-observed"],
            "qualification": "VALID",
            "confidence": 0.99,
            "applicability": {"input_keys": ["value"], "output_keys": ["result"], "relation_kind": "linear", "tags": ["linear"]},
        },
        "stale_obs": {
            "relationship_id": "rel-stale-obs",
            "structure": {"kind": "add", "input_key": "value", "output_key": "result", "delta": 3},
            "evidence_ids": ["obs-1", "obs-stale"],
            "provenance": ["alpha-observed"],
            "qualification": "VALID",
            "confidence": 0.99,
            "applicability": {"input_keys": ["value"], "output_keys": ["result"], "relation_kind": "linear", "tags": ["linear"]},
        },
    }


class PathwayMac002IntegrationTests(unittest.TestCase):
    def test_evidence_reference_qualification_rejects_forged_unresolvable_duplicate_and_stale(self):
        obs = _obs_store()
        recs = _records()
        self.assertEqual(qualify_record_against_observations(recs["valid_add3"], obs), (True, []))
        self.assertFalse(qualify_record_against_observations(recs["forged_prov"], obs)[0])
        self.assertFalse(qualify_record_against_observations(recs["unresolvable_ev"], obs)[0])
        self.assertFalse(qualify_record_against_observations(recs["duplicate_obs"], obs)[0])
        self.assertFalse(qualify_record_against_observations(recs["stale_obs"], obs)[0])

    def test_five_arm_frozen_ab_comparison(self):
        obs = _obs_store()
        recs = _records()
        cases = [
            ("valid_transfer", {"case_id": "c1", "input": {"value": 100}, "relation_kind": "linear", "tags": ["linear"]}, [recs["valid_add3"]], ("RESOLVE", 103)),
            ("competing_same_io", {"case_id": "c2", "input": {"value": 5}, "relation_kind": "linear", "tags": ["linear"]}, [recs["valid_add3"], recs["competing_mul2"]], ("ABSTAIN", None)),
            ("forged_provenance", {"case_id": "c3", "input": {"value": 100}, "relation_kind": "linear", "tags": ["linear"]}, [recs["forged_prov"]], ("ABSTAIN", None)),
            ("unresolvable_evidence", {"case_id": "c4", "input": {"value": 100}, "relation_kind": "linear", "tags": ["linear"]}, [recs["unresolvable_ev"]], ("ABSTAIN", None)),
            ("duplicate_evidence", {"case_id": "c5", "input": {"value": 100}, "relation_kind": "linear", "tags": ["linear"]}, [recs["duplicate_obs"]], ("ABSTAIN", None)),
            ("stale_evidence", {"case_id": "c6", "input": {"value": 100}, "relation_kind": "linear", "tags": ["linear"]}, [recs["stale_obs"]], ("ABSTAIN", None)),
        ]

        # ORIGINAL ablation controls (unchanged expectations 5/4/1/0): they run with the
        # shared apply-boundary provenance gate DISABLED (enforce_provenance=False) so the
        # adapter's own screening / qualification gates are measured in isolation.
        arms = {
            "Arm1_adapter_only": Mac002PathwayAdapter(enable_screening=False, enable_qualification=False, enforce_provenance=False),
            "Arm2_adapter_screening": Mac002PathwayAdapter(enable_screening=True, enable_qualification=False, enforce_provenance=False),
            "Arm3_adapter_qualification": Mac002PathwayAdapter(enable_screening=False, enable_qualification=True, enforce_provenance=False),
            "Arm4_combined_integrated": Mac002PathwayAdapter(enable_screening=True, enable_qualification=True, enforce_provenance=False),
        }
        # MUSE-FUNCTIONAL-009: the same four arms with the shared gate ON (the shipped default).
        arms_gated = {
            "Arm1_adapter_only+gate": Mac002PathwayAdapter(enable_screening=False, enable_qualification=False),
            "Arm2_adapter_screening+gate": Mac002PathwayAdapter(enable_screening=True, enable_qualification=False),
            "Arm3_adapter_qualification+gate": Mac002PathwayAdapter(enable_screening=False, enable_qualification=True),
            "Arm4_combined_integrated+gate": Mac002PathwayAdapter(enable_screening=True, enable_qualification=True),
        }

        summary = {}
        with tempfile.TemporaryDirectory() as td:
            for arm_name, adapter in {**arms, **arms_gated}.items():
                rec = CognitiveRecorder(str(Path(td) / f"{arm_name}.jsonl"), run_id=arm_name, task_id="AB")
                correct = 0
                false_res = 0
                for cid, ctx, cand_list, (exp_disp, exp_pred) in cases:
                    out = adapter.evaluate(ctx, cand_list, obs, recorder=rec)
                    ok = out["disposition"] == exp_disp and (exp_disp != "RESOLVE" or out["prediction"] == exp_pred)
                    if ok:
                        correct += 1
                    elif out["disposition"] == "RESOLVE":
                        false_res += 1
                rec.close_run(outcome={"arm": arm_name}, validation={"cases": len(cases)})
                summary[arm_name] = {"correct": correct, "total": len(cases), "false_resolutions": false_res}

        self.assertEqual(summary["Arm1_adapter_only"]["false_resolutions"], 5)
        self.assertEqual(summary["Arm2_adapter_screening"]["false_resolutions"], 4)
        self.assertEqual(summary["Arm3_adapter_qualification"]["false_resolutions"], 1)
        self.assertEqual(summary["Arm4_combined_integrated"]["correct"], 6)
        self.assertEqual(summary["Arm4_combined_integrated"]["false_resolutions"], 0)
        # gate ON: never worse than the ablated control, and strictly better for the
        # ungated arms (the forged-provenance case can no longer resolve in ANY arm).
        for k in ("Arm1_adapter_only", "Arm2_adapter_screening", "Arm3_adapter_qualification", "Arm4_combined_integrated"):
            self.assertLessEqual(summary[k + "+gate"]["false_resolutions"], summary[k]["false_resolutions"], k)
        self.assertEqual(summary["Arm4_combined_integrated+gate"]["false_resolutions"], 0)
        print("FIVE_ARM_GATE_ON", {k: v for k, v in summary.items() if k.endswith("+gate")})

    def test_pipeline_opt_in_preserves_baseline_and_runs_adapter(self):
        obs = _obs_store()
        recs = _records()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p_base = Pipeline(root / "base", Policy("v1"), soso_modules=_TEST_SOSO)
            self.assertIsNone(p_base.mac002_adapter)
            res_base = p_base.run({"task_id": "PIPE_BASE_01", "family": "math", "signature": "add3", "tags": ["linear"]})
            self.assertNotIn("mac002_report", res_base)
            p_int = Pipeline(root / "int", Policy("v1"), soso_modules=_TEST_SOSO, mac002_adapter=Mac002PathwayAdapter())
            task = {
                "task_id": "PIPE_MAC_01",
                "family": "math",
                "signature": "add3",
                "input": {"value": 100},
                "relation_kind": "linear",
                "tags": ["linear"],
                "mac002_records": [recs["valid_add3"]],
                "mac002_observation_store": obs,
            }
            res = p_int.run(task)
            self.assertEqual(res["mac002_report"]["disposition"], "RESOLVE")
            self.assertEqual(res["mac002_report"]["prediction"], 103)


if __name__ == "__main__":
    unittest.main()
