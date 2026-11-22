import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from contract.cognitive_recorder import CognitiveRecorder  # noqa: E402
from contract.disposition_engine import (  # noqa: E402
    HoldStore,
    deliver_evidence_and_reactivate,
    evaluate_motor_branches,
    explain_disposition_pathway,
    make_disposition_event,
    propagate_authorized_results,
)


class DispositionEngine006Tests(unittest.TestCase):
    AUTH_EDGES = {
        ("U09", "U37_MECH"),
        ("U09", "L24_THERMAL"),
        ("U09", "U10_ELECTRICAL"),
        ("U10", "U37_ELECTRICAL"),
    }

    def test_01_historical_motor_baseline_vs_label_only_control(self):
        obs = {"obs_id": "OBS-001", "facts": {"current": "high", "rotation": 0}}
        evs = evaluate_motor_branches(obs)
        self.assertEqual([e["disposition"] for e in evs], ["PASS", "DROP", "HOLD"])

        res_enabled = propagate_authorized_results(evs, self.AUTH_EDGES, observation=obs, label_only_control=False)
        res_control = propagate_authorized_results(evs, self.AUTH_EDGES, observation=obs, label_only_control=True)
        self.assertEqual(len(res_enabled["evidence_requests"]), 2)
        self.assertEqual(len(res_control["evidence_requests"]), 0)
        self.assertEqual(len(res_enabled["downstream_actions"]), 1)
        self.assertFalse(any(e["payload_forwarded"] for e in evs if e["disposition"] in ("DROP", "HOLD")))

    def test_02_pass_rejected_on_unauthorized_edge(self):
        obs = {"obs_id": "OBS-002", "facts": {"current": "high", "rotation": 0}}
        evs = evaluate_motor_branches(obs)
        res = propagate_authorized_results(evs, set(), observation=obs)  # no authorized edges
        self.assertEqual(len(res["downstream_actions"]), 0)
        self.assertEqual(len(res["rejected_edges"]), 3)

    def test_03_auth0_strict_enforcement_rejects_nonzero_authority(self):
        with self.assertRaises(PermissionError):
            make_disposition_event("E-BAD", "U09", "U37", "OBS-1", "PASS", "OK", {}, authority=1)
        with self.assertRaises(PermissionError):
            make_disposition_event("E-BAD2", "U09", "U37", "OBS-1", "PASS", "OK", {}, authority=True)

    def test_04_hold_persists_across_os_restart_rejects_unrelated_reactivates_and_blocks_duplicate(self):
        obs = {"obs_id": "OBS-001", "facts": {"current": "high", "rotation": 0}}
        evs = evaluate_motor_branches(obs, execution_id="exec-restart-01")
        with tempfile.TemporaryDirectory() as td:
            hold_path = Path(td) / "holds.json"
            rec_path = Path(td) / "trace.jsonl"
            hs = HoldStore(hold_path)
            rec = CognitiveRecorder(str(rec_path), run_id="disp-006", task_id="OBS-001")
            prop = propagate_authorized_results(evs, self.AUTH_EDGES, observation=obs, hold_store=hs, recorder=rec)
            self.assertEqual(len(prop["persisted_holds"]), 1)

            # 1. Unrelated evidence does NOT unblock HOLD
            unrel = deliver_evidence_and_reactivate(hs, {"ambient_humidity": 40}, recorder=rec)
            self.assertEqual(len(unrel["reactivated"]), 0)
            self.assertEqual(len(unrel["still_pending"]), 1)
            rec.close_run(outcome={"pending": len(unrel["still_pending"])}, validation={"ok": True})

            # 2. OS subprocess restart -> deliver qualifying abnormal supply_voltage -> actual second invocation -> PASS
            p = subprocess.run([
                sys.executable, "-c",
                f"import json, sys; sys.path.insert(0, {str(_ROOT)!r}); "
                f"from contract.disposition_engine import HoldStore, deliver_evidence_and_reactivate; "
                f"hs = HoldStore({str(hold_path)!r}); "
                f"r1 = deliver_evidence_and_reactivate(hs, {{'supply_voltage': '11V_brownout'}}, authorized_edges={self.AUTH_EDGES!r}); "
                f"r2 = deliver_evidence_and_reactivate(hs, {{'supply_voltage': '11V_brownout'}}, authorized_edges={self.AUTH_EDGES!r}); "
                f"print(json.dumps({{'r1': r1, 'r2': r2}}))"
            ], capture_output=True, text=True, check=True)
            out = json.loads(p.stdout.strip())
            self.assertEqual(len(out["r1"]["reactivated"]), 1)
            self.assertEqual(out["r1"]["reactivated"][0]["events"][0]["disposition"], "PASS")
            self.assertEqual(out["r1"]["reactivated"][0]["events"][0]["causal_parent_id"], "exec-restart-01-EVT-003")
            # Duplicate delivery blocked!
            self.assertEqual(len(out["r2"]["reactivated"]), 0)
            self.assertEqual(len(out["r2"]["duplicate_blocked"]), 1)

    def test_05_contradictory_evidence_produces_drop_and_multiple_simultaneous_holds(self):
        with tempfile.TemporaryDirectory() as td:
            hs = HoldStore(Path(td) / "multi_holds.json")
            ev1 = make_disposition_event("H1", "U09", "U10_ELECTRICAL", "OBS-1", "HOLD", "PENDING", {"pending_conditions": ["supply_voltage"]})
            ev2 = make_disposition_event("H2", "U09", "L24_THERMAL", "OBS-1", "HOLD", "PENDING", {"pending_conditions": ["bearing_temp"]})
            propagate_authorized_results([ev1, ev2], {("U09", "U10_ELECTRICAL"), ("U09", "L24_THERMAL")}, observation={"obs_id": "OBS-1", "facts": {}}, hold_store=hs)
            self.assertEqual(len(hs.list_holds("PENDING")), 2)

            # Deliver contradictory supply_voltage -> reactivates H1 as DROP, leaves H2 PENDING
            res = deliver_evidence_and_reactivate(hs, {"supply_voltage": "contradictory"})
            self.assertEqual(len(res["reactivated"]), 1)
            self.assertEqual(res["reactivated"][0]["events"][0]["disposition"], "DROP")
            self.assertEqual(res["reactivated"][0]["events"][0]["reason_code"], "CONTRADICTORY_EVIDENCE")
            self.assertEqual(res["still_pending"], ["HOLD-H2"])

    def test_06_deterministic_replay_and_explanation(self):
        obs = {"obs_id": "OBS-001", "facts": {"current": "high", "rotation": 0}}
        evs = evaluate_motor_branches(obs, execution_id="exec-det")
        with tempfile.TemporaryDirectory() as td:
            hs = HoldStore(Path(td) / "h.json")
            rec = CognitiveRecorder(str(Path(td) / "t.jsonl"), run_id="det", task_id="OBS-001")
            prop = propagate_authorized_results(evs, self.AUTH_EDGES, observation=obs, hold_store=hs, recorder=rec)
            reac = deliver_evidence_and_reactivate(hs, {"supply_voltage": "24V_nominal"}, recorder=rec)
            rec.close_run(outcome={"ok": True}, validation={"ok": True})
            replayed = CognitiveRecorder.load(str(Path(td) / "t.jsonl"))
            self.assertTrue(len(replayed) >= 4)
            expl = explain_disposition_pathway(evs, prop, reac)
            self.assertIn("U09", expl["functions_executed"])
            self.assertIn("U10", expl["functions_executed"])
            self.assertEqual(reac["reactivated"][0]["events"][0]["disposition"], "DROP")


if __name__ == "__main__":
    unittest.main()
