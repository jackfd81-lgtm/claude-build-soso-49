"""Integration Contract 001 — mapping tests + acceptance tests.

Mapping tests prove the interface is what the contract says it
is (registry, schema, identity, trial safety, admission).
Acceptance tests prove the nine behaviors JD's directive
requires, on the same frozen conditions and observation draws.

Every test uses a fresh trained harness (train_baseline() runs
the real lifecycle: trial -> CANDIDATE -> UNDER_TEST ->
QUALIFIED). Nothing in these tests writes to either baseline.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.admission import Policy, admit
from contract.harness import Harness, SOSO_MODULES, TRANSFER_TASKS
from contract.registry import (REGISTRY, TRIAL_FORBIDDEN,
                               TRIAL_REQUIRES_AUTHORIZATION, TRIAL_SAFE,
                               by_native, check_hard_preconditions, get,
                               trial_eligible)
from contract.task import make_task, validate_task


def _task(**kw):
    base = {"schema_version": "contract-task/1",
            "task_id": "ct-test-1", "signature": "disk_full",
            "family": "valve", "tags": ["valve", "firmware_v2", "stuck"],
            "conditions": {"firmware": "v2"},
            "requested_operation": "repair",
            "provenance": ("int:contract-test",)}
    base.update(kw)
    return base


def _trained():
    h = Harness()
    h.train_baseline()
    return h


# ------------------------------------------------------------------
# Mapping tests
# ------------------------------------------------------------------

class TestRegistryMapping:
    def test_every_native_procedure_is_registered_once(self):
        natives = ["clear_cache", "restart_svc", "rotate_logs", "wipe_disk",
                   "reset_cfg", "P1", "P2", "P3", "P4", "P5", "P6", "P7",
                   "P8", "P9", "P10", "P11"]
        assert len(REGISTRY) == 16
        for n in natives:
            e = by_native(n)
            assert e.native_id == n
            assert e.procedure_id  # unique by dict construction
        assert len({e.procedure_id for e in REGISTRY.values()}) == 16

    def test_no_cross_mapping_between_the_systems(self):
        rc = [e for e in REGISTRY.values() if e.origin == "realcog-0.4"]
        soso = [e for e in REGISTRY.values() if e.origin == "soso-rev1.0"]
        assert len(rc) == 5 and len(soso) == 11
        assert not ({e.native_id for e in rc} & {e.native_id for e in soso})
        # No registry entry claims an equivalence to the other system.
        for e in REGISTRY.values():
            assert "equivalent" not in e.description.lower()

    def test_every_entry_carries_the_full_contract(self):
        for e in REGISTRY.values():
            assert e.version and e.origin and e.native_id
            assert e.description and e.expected_effects
            assert e.reversibility in ("reversible", "bounded", "irreversible")
            assert e.trial_safety in (TRIAL_SAFE, TRIAL_REQUIRES_AUTHORIZATION,
                                      TRIAL_FORBIDDEN)
            assert e.validation_criteria and e.adapter in ("realcog", "soso")
            assert e.capabilities

    def test_trial_safety_classification(self):
        assert get("RC-FIX-WIPE-DISK").trial_safety == TRIAL_FORBIDDEN
        assert get("RC-FIX-RESET-CFG").trial_safety == TRIAL_REQUIRES_AUTHORIZATION
        assert get("RC-FIX-ROTATE-LOGS").trial_safety == TRIAL_SAFE
        for p in ("P8", "P9", "P10", "P11"):
            assert by_native(p).trial_safety == TRIAL_REQUIRES_AUTHORIZATION
        assert by_native("P2").trial_safety == TRIAL_SAFE

    def test_hard_vs_soft_preconditions(self):
        p2 = by_native("P2")
        assert p2.preconditions["firmware"] == ("v2", True)  # hard
        p7 = by_native("P7")
        assert p7.preconditions["kind"] == ("detour", False)  # soft
        # A soft mismatch never changes the hard check.
        assert check_hard_preconditions(p7, {"kind": "traverse"}) == "satisfied"
        assert check_hard_preconditions(p2, {"firmware": "v1"}) == "contradicted"
        assert check_hard_preconditions(p2, {}, ("firmware",)) == "unknown"
        assert check_hard_preconditions(p2, {}, (), ("firmware",)) == "contradicted"

    def test_trial_eligibility_needs_all_four_conditions(self):
        p2 = by_native("P2")
        assert trial_eligible(p2, {"firmware": "v2"}, (), True)
        assert not trial_eligible(p2, {}, ("firmware",), True)       # unknown
        assert not trial_eligible(p2, {"firmware": "v1"}, (), True)  # contradicted
        assert not trial_eligible(p2, {"firmware": "v2"}, (), False)  # policy
        assert not trial_eligible(by_native("wipe_disk"), {}, (), True)


class TestTaskSchema:
    def test_valid_task_validates(self):
        assert validate_task(_task()) == []

    def test_invalid_records_are_refused(self):
        bad = _task(schema_version="contract-task/99")
        assert validate_task(bad)
        bad = _task(task_id="bad id!")
        assert validate_task(bad)
        bad = _task(provenance=("http://example.com",))
        assert validate_task(bad)

    def test_identity_digest_covers_identity_bearing_fields_only(self):
        t = make_task(**_task())
        assert t.identity_digest() == make_task(**_task()).identity_digest()
        assert t.identity_digest() != make_task(**_task(tags=["valve"])).identity_digest()
        assert t.identity_digest() != make_task(**_task(signature="svc_hung")).identity_digest()
        assert t.identity_digest() != make_task(
            **_task(requested_operation="inspect")).identity_digest()
        # Observed environmental conditions are NOT identity-bearing.
        assert t.identity_digest() == make_task(
            **_task(observed={"pressure_kpa": 88})).identity_digest()


class TestAdmission:
    def test_four_admission_outcomes(self):
        pol = Policy("t-v1", forbidden=("SOSO-P2",))
        task = make_task(**_task())
        ok, status, _ = admit(by_native("P2"), task, pol)
        assert not ok and status == "policy_denied"
        pol = Policy("t-v1")
        ok, status, _ = admit(by_native("P2"), make_task(**_task(conditions={})), pol)
        assert not ok and status == "unknown_preconditions"
        ok, status, _ = admit(by_native("P2"), make_task(
            **_task(conditions={"firmware": "v1"})), pol)
        assert not ok and status == "contradicted"
        ok, status, _ = admit(by_native("P2"), task, pol)
        assert ok and status == "admitted"


# ------------------------------------------------------------------
# Acceptance tests (JD's nine proofs)
# ------------------------------------------------------------------

class TestAcceptance:
    def test_1_exact_repeat_is_one_step(self):
        h = _trained()
        try:
            h.add_task({**_task(task_id="ct-repeat-1"), "correct": "P2"})
            r = h.pipe.run(_task(task_id="ct-repeat-1"))
            assert r["state"] == "RESOLVED"
            assert r["stage"] == "lower-exact"
            assert r["selected"] == "SOSO-P2"
            assert len(r["attempts"]) == 1
        finally:
            h.close()

    def test_2_cross_family_transfer_is_first_attempt(self):
        h = _trained()
        try:
            t = {"task_id": "ct-slope-1", "signature": "disk_almost_full",
                 "family": "slope", "tags": ["slope", "ground", "steep", "cross"],
                 "conditions": {}, "requested_operation": "repair",
                 "provenance": ("int:contract-test",), "correct": "P7"}
            h.add_task(t)
            r = h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            assert r["state"] == "RESOLVED"
            assert r["stage"] == "soso-transfer"
            assert r["selected"] == "SOSO-P7"
            assert len(r["attempts"]) == 1  # first-attempt advantage kept
        finally:
            h.close()

    def test_3_synonyms_resolve_through_permitted_trial(self):
        h = _trained()
        try:
            t = {"task_id": "ct-syn-1", "signature": "diskfull",
                 "family": "slope",
                 "tags": ["hillside", "terrain", "traverse", "ascend"],
                 "conditions": {}, "requested_operation": "repair",
                 "provenance": ("int:contract-test",), "correct": "P7"}
            h.add_task(t)
            r = h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            assert r["state"] == "RESOLVED"
            assert r["stage"] == "higher-trial"
            assert r["selected"] == "SOSO-P7"
            # The trial success is CANDIDATE: stored, never trusted.
            latest = h.pipe.experiences.latest_per_pair()
            rec = latest[("ct-syn-1", "SOSO-P7")]
            assert rec["qual"] == "CANDIDATE" and rec["src"] == "higher-trial"
            proj = h.pipe.experiences.rebuild_projections(SOSO_MODULES)
            assert "diskfull" not in proj["exact"]
            # A second identical task still cannot transfer from it:
            # the candidate did not enter the trusted index.
            t2 = dict(t, task_id="ct-syn-2")
            h.add_task(t2)
            r2 = h.pipe.run({k: v for k, v in t2.items() if k != "correct"})
            assert r2["stage"] == "higher-trial"
        finally:
            h.close()

    def test_4_unknown_and_contradictory_prerequisites_never_execute(self):
        h = _trained()
        try:
            # (a) firmware unknown -> information request, 0 executions.
            t = _task(task_id="ct-unk-1", conditions={},
                      unknown_conditions=("firmware",),
                      tags=["valve", "stuck", "pressure"])
            h.add_task({**t, "correct": "P2"})
            r = h.pipe.run(t)
            assert len(r["attempts"]) == 0
            assert r["information_requests"]
            assert "firmware" in r["information_requests"][0]["missing"]
            assert r["state"] == "ESCALATED"
            # (b) both firmwares present -> contradicted, 0 executions.
            t = _task(task_id="ct-con-1", conditions={},
                      contradictory_conditions=("firmware",),
                      tags=["valve", "firmware_v1", "firmware_v2", "stuck"])
            h.add_task({**t, "correct": "P2"})
            r = h.pipe.run(t)
            assert len(r["attempts"]) == 0
            assert r["state"] == "UNSOLVED"
            assert r["iou"] is not None
        finally:
            h.close()

    def test_5_forbidden_and_unavailable_procedures_are_explicitly_unresolved(self):
        h = _trained()
        try:
            # Forbidden: the only fix is wipe_disk.
            t = {"task_id": "ct-wipe-1", "signature": "wipe_trap",
                 "family": "disk", "tags": ["disk", "full", "wipe"],
                 "conditions": {}, "requested_operation": "repair",
                 "provenance": ("int:contract-test",), "correct": "wipe_disk"}
            h.add_task(t)
            r = h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            assert r["state"] == "UNSOLVED"
            assert r["iou"] is not None
            assert not any(a["procedure"] == "RC-FIX-WIPE-DISK"
                           for a in r["attempts"])
            st = h.pipe.ious.state()
            assert st["ious"][r["iou"]][0] == "PENDING"
            # Unavailable: reset_cfg needs separate authorization.
            t = {"task_id": "ct-cfg-1", "signature": "net_down",
                 "family": "config", "tags": ["config", "bad", "network"],
                 "conditions": {}, "requested_operation": "repair",
                 "provenance": ("int:contract-test",), "correct": "reset_cfg"}
            h.add_task(t)
            r = h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            assert r["state"] == "UNSOLVED"
            assert not any(a["procedure"] == "RC-FIX-RESET-CFG"
                           for a in r["attempts"])
        finally:
            h.close()

    def test_6_qualified_experience_survives_restart(self):
        import tempfile
        import shutil
        root = tempfile.mkdtemp(prefix="contract001-restart-")
        h = Harness(root=root)
        h.train_baseline()
        h.close()  # explicit root: close() does not remove it
        h2 = Harness(root=root)
        try:
            h2.add_task({**_task(task_id="ct-repeat-2"), "correct": "P2"})
            r = h2.pipe.run(_task(task_id="ct-repeat-2"))
            assert r["state"] == "RESOLVED"
            assert r["stage"] == "lower-exact"
            assert len(r["attempts"]) == 1
            rep = h2.pipe.recover()
            assert rep["running"] == [] and rep["ambiguous"] == []
        finally:
            h2.close()
            shutil.rmtree(root, ignore_errors=True)

    def test_7_crash_during_learning_cannot_create_conflicting_projections(self):
        helper = os.path.join(os.path.dirname(__file__), "crash_contract.py")
        root = None
        import tempfile
        root = tempfile.mkdtemp(prefix="contract001-crash-")
        try:
            # The helper runs one task by trial, then SIGKILLs
            # itself before any projection rebuild.
            proc = subprocess.run(
                [sys.executable, helper, root], capture_output=True, text=True)
            assert proc.returncode == -9, proc.stderr[-500:]
            # A fresh process rebuilds both projections from the
            # same Big49 blocks: they must agree exactly.
            h2 = Harness(root=root)
            try:
                proj = h2.pipe.experiences.rebuild_projections(SOSO_MODULES)
                recs = h2.pipe.experiences.read_all()
                assert len(recs) == 1
                assert recs[0]["qual"] == "CANDIDATE"
                assert "disk_full" not in proj["exact"]
                succ = SOSO_MODULES["cognitive_layer"].success_sets(
                    proj["transfer_conn"])
                assert "P2" not in succ
                # And the pipeline still works on the recovered root.
                h2.add_task({**_task(task_id="ct-after-crash"), "correct": "P2"})
                r = h2.pipe.run(_task(task_id="ct-after-crash"))
                assert r["state"] == "RESOLVED"
            finally:
                h2.close()
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_8_task_identity_is_immutable(self):
        h = _trained()
        try:
            h.add_task({**_task(task_id="ct-id-1"), "correct": "P2"})
            r = h.pipe.run(_task(task_id="ct-id-1"))
            assert r["state"] == "RESOLVED"
            # Same id, different signature -> REJECTED.
            r = h.pipe.run(_task(task_id="ct-id-1", signature="svc_hung"))
            assert r["state"] == "REJECTED"
            assert any("identity mismatch" in x["reason"] for x in r["rejected"])
            assert len(r["attempts"]) == 0
            # Same id, different tags -> REJECTED.
            r = h.pipe.run(_task(task_id="ct-id-1", tags=["valve", "stuck"]))
            assert r["state"] == "REJECTED"
            # The original identity still stands.
            r = h.pipe.run(_task(task_id="ct-id-1"))
            assert r["state"] == "RESOLVED"
            assert r["stage"] == "lower-exact"
        finally:
            h.close()

    def test_9_source_and_qualification_states_survive_adapter_conversion(self):
        h = _trained()
        try:
            # (a) A CANDIDATE trial outcome, converted both ways.
            t = {"task_id": "ct-conv-1", "signature": "diskfull",
                 "family": "slope",
                 "tags": ["hillside", "terrain", "traverse", "ascend"],
                 "conditions": {}, "requested_operation": "repair",
                 "provenance": ("int:contract-test",), "correct": "P7"}
            h.add_task(t)
            r = h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            assert r["stage"] == "higher-trial"
            rec = h.pipe.experiences.latest_per_pair()[("ct-conv-1", "SOSO-P7")]
            assert rec["qual"] == "CANDIDATE" and rec["src"] == "higher-trial"
            # RealCog adapter: read the block back through the store.
            from ph6_realbridge import pointer_for
            ptr = pointer_for(h.pipe.big, "exp.ct-conv-1.P7.1", "L30")
            back = h.pipe.big.read(ptr, principal="L30")
            import json
            assert json.loads(back["content"])["qual"] == "CANDIDATE"
            # SoSo adapter: the transfer layer excludes it.
            proj = h.pipe.experiences.rebuild_projections(SOSO_MODULES)
            succ = SOSO_MODULES["cognitive_layer"].success_sets(
                proj["transfer_conn"])
            assert "P7" not in succ or all(
                "hillside" not in s for s in succ.get("P7", []))
            # (b) A QUALIFIED record appears in BOTH projections.
            qrec = h.pipe.experiences.latest_per_pair()[("ct-train-valve-a", "SOSO-P2")]
            assert qrec["qual"] == "QUALIFIED" and qrec["src"] == "qualification"
            assert "SOSO-P2" in proj["exact"]["disk_full"]
            assert proj["exact"]["disk_full"]["SOSO-P2"]["qual"] == "QUALIFIED"
            succ2 = SOSO_MODULES["cognitive_layer"].success_sets(
                proj["transfer_conn"])
            assert "P2" in succ2
        finally:
            h.close()

    def test_rejected_candidates_are_preserved_and_deduped(self):
        h = _trained()
        try:
            # Valve v1: P2 is contradicted, P1 resolves via the live
            # path's general knowledge in SoSo — but the contract's
            # selection path has no P1 experience, so P2's refusal
            # must be recorded and no procedure may be attempted twice.
            t = _task(task_id="ct-rej-1",
                      tags=["valve", "firmware_v1", "stuck", "pressure"],
                      conditions={"firmware": "v1"})
            h.add_task({**t, "correct": "P1"})
            r = h.pipe.run(t)
            assert any(x["procedure"] == "SOSO-P2" and "firmware" in x["reason"]
                       for x in r["rejected"])
            attempted = [a["procedure"] for a in r["attempts"]]
            assert len(attempted) == len(set(attempted))
        finally:
            h.close()
