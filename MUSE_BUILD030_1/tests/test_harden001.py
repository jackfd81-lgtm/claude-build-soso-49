from pathlib import Path
"""HARDEN-001 — targeted hardening fixes: adversarial tests.

Four corrections on the frozen Contract 002 + EVAL-001 baseline:
  1. Demonstrated-condition enforcement at exact retrieval and
     transfer selection (hard / soft / unknown / contradicted
     distinguished; valid cross-family transfer preserved).
  2. Consistent false-success classification (REJECTED in both
     trial and transfer paths).
  3. Scope-aware withdrawal (scoped by procedure + demonstrated
     conditions; global only when globally justified; history
     preserved).
  4. Persisted generation threshold (a restarted process recovers
     the stored setting; an incompatible configuration fails
     explicitly).

Every test is adversarial: it presents the exact input that the
old code got wrong (or silently tolerated) and asserts the new
behaviour. All tasks use frozen deterministic observations.
"""
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "realcog"))

from contract.eval001 import (check_projections, make_config_harness,
                              registered_id)
from contract.generations import GenerationsAdapter
from contract.harness import DEMO_TASK, Harness, register_demo


def _run(h, t):
    return h.pipe.run({k: v for k, v in t.items() if k != "correct"})


def _slope_task_on_blocked_sig(task_id="h1-slope-blocked-sig"):
    # A slope task carrying the BLOCKED signature. The exact index
    # holds P7 for cache_big demonstrated on blocked tags; this
    # task shares no tag with that demonstration, so the exact
    # index must refuse it — but the transfer stage must still
    # resolve it cross-family (description overlap "ground").
    return {"task_id": task_id, "signature": "cache_big",
            "family": "slope",
            "tags": ["slope", "ground", "steep", "cross"],
            "conditions": {},
            "requested_operation": "repair",
            "provenance": ("int:harden-001",), "correct": "P7",
            "synonym": False}


class TestFix1UnknownConditions:
    """Missing evidence must not be silently treated as satisfied:
    a task presenting a condition the procedure was never
    demonstrated with is UNKNOWN at the recognition stages —
    refused at exact and transfer — and the task still resolves
    via the trial, which is the investigation path."""

    def test_unknown_kind_refused_at_recognition_resolves_by_trial(self):
        # P7 qualified for slope with NO demonstrated conditions
        # (the slope task carried none). A second slope task that
        # PRESENTS a kind condition: P7's registry requires kind
        # detour (soft) but P7 was never demonstrated with any
        # kind, so the kind is UNKNOWN at the recognition stages —
        # refused at exact and transfer — and the task still
        # resolves via the trial, which is the investigation path.
        h = make_config_harness("FULL", [])
        try:
            slope = {"task_id": "h1-slope-plain",
                     "signature": "disk_almost_full", "family": "slope",
                     "tags": ["slope", "ground", "steep", "cross"],
                     "conditions": {},
                     "requested_operation": "repair",
                     "provenance": ("int:harden-001",), "correct": "P7",
                     "synonym": False}
            h.add_task(slope)
            rep = _run(h, slope)
            assert rep["state"] == "RESOLVED" and rep["selected"] == "SOSO-P7"
            t = {"task_id": "h1-slope-kind",
                 "signature": "disk_almost_full", "family": "slope",
                 "tags": ["slope", "ground", "loose", "incline"],
                 "conditions": {"kind": "traverse"},
                 "requested_operation": "repair",
                 "provenance": ("int:harden-001",), "correct": "P7",
                 "synonym": False}
            h.add_task(t)
            rep = _run(h, t)
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P7"
            # P7 was refused at recognition (unknown), not trusted.
            assert any(r["procedure"] == "SOSO-P7"
                       and "unknown" in r["reason"]
                       for r in rep["rejected"])
            # And it was reached by trial, not by exact/transfer.
            assert rep["stage"] == "higher-trial"
            assert len(rep["attempts"]) == 1
            assert rep["attempts"][0]["procedure"] == "SOSO-P7"
            assert rep["attempts"][0]["qualification_after"] == "CANDIDATE"
            assert check_projections(h) == []
        finally:
            h.close()

    def test_cross_family_transfer_preserved_when_tags_do_not_overlap(self):
        t = _slope_task_on_blocked_sig()
        h = make_config_harness("FULL", [t])
        try:
            rep = _run(h, t)
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P7"
            # The exact index refused (tag gate) — P7 was never
            # served on signature alone.
            assert any(r["procedure"] == "SOSO-P7"
                       and "tags" in r["reason"]
                       for r in rep["rejected"])
            # But the transfer stage still resolved it cross-family.
            assert rep["stage"] == "soso-transfer"
            assert rep["attempts"][0]["procedure"] == "SOSO-P7"
            assert check_projections(h) == []
        finally:
            h.close()


class TestFix1ContradictionAtTransfer:
    """An explicit contradiction refuses the candidate at the
    transfer stage too — not only at exact retrieval."""

    def test_kind_contradiction_refused_at_transfer_but_tried(self):
        # A blocked task (blocked tags, so the transfer stage
        # proposes P7) with kind traverse, contradicting P7's
        # demonstrated kind detour. P7 is refused at exact AND at
        # transfer — recognition trusts only demonstrated
        # conditions — but the trial still tries it (a soft
        # contradiction does not block investigation: the trial is
        # how the system learns whether P7 works for traverse
        # too), and it resolves.
        t = {"task_id": "h1-traverse", "signature": "cache_big",
             "family": "blocked",
             "tags": ["route", "blocked", "debris", "rubble"],
             "conditions": {"kind": "traverse"},
             "requested_operation": "repair",
             "provenance": ("int:harden-001",), "correct": "P7",
             "synonym": False}
        h = make_config_harness("FULL", [t])
        try:
            rep = _run(h, t)
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P7"
            assert rep["stage"] == "higher-trial"
            # Refused at recognition (contradicted) ...
            assert any(r["procedure"] == "SOSO-P7"
                       and "contradicted" in r["reason"]
                       for r in rep["rejected"])
            # ... but tried by the trial, which is the
            # investigation path.
            assert any(a["procedure"] == "SOSO-P7"
                       for a in rep["attempts"])
            assert check_projections(h) == []
        finally:
            h.close()


class TestFix2LieLabelling:
    """A proven false success is REJECTED in both paths, with the
    observation, validator decision and evidence preserved."""

    def test_transfer_lie_is_rejected_with_evidence_preserved(self):
        # A NEW signature, so the exact index cannot serve P7 —
        # the lie must be caught at the transfer stage (P7 is
        # proposed there by applicability), not at exact retrieval.
        t = {"task_id": "h2-lie", "signature": "cache_big_3",
             "family": "blocked",
             "tags": ["route", "blocked", "debris", "rubble"],
             "conditions": {"kind": "detour"},
             "requested_operation": "repair",
             "provenance": ("int:harden-001",), "correct": "P5",
             "synonym": False}
        h = make_config_harness("FULL", [t])
        try:
            h.pipe.observations[(t["task_id"], "P7")] = True
            rep = _run(h, t)
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P5"
            rec = h.pipe.experiences.latest_per_pair()[(t["task_id"], "SOSO-P7")]
            assert rec["obs"] == "SUCCESS" and rec["val"] == "FAIL"
            assert rec["qual"] == "REJECTED"
            # The evidence is preserved in the record itself.
            assert rec["src"] == "soso-transfer"
            assert check_projections(h) == []
        finally:
            h.close()

    def test_trial_lie_is_rejected(self):
        # The Contract 002 P5 lie, reached by trial: same label.
        t = {"task_id": "h2-lie-trial", "signature": "cache_big_2",
             "family": "blocked",
             "tags": ["route", "crates", "shut", "passage"],
             "conditions": {"kind": "traverse"},
             "requested_operation": "repair",
             "provenance": ("int:harden-001",), "correct": "P7",
             "synonym": False}
        h = make_config_harness("FULL", [t])
        try:
            h.pipe.observations[(t["task_id"], "P5")] = True
            rep = _run(h, t)
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P7"
            rec = h.pipe.experiences.latest_per_pair()[(t["task_id"], "SOSO-P5")]
            assert rec["obs"] == "SUCCESS" and rec["val"] == "FAIL"
            assert rec["qual"] == "REJECTED"
            assert check_projections(h) == []
        finally:
            h.close()


class TestFix3ScopeAwareWithdrawal:
    """Withdrawal is scoped by procedure + demonstrated conditions.
    A local contradiction must not invalidate unrelated valid
    experience; global withdrawal is reserved for evidence the
    procedure itself is defective or unsafe across all scopes."""

    def _harness_with_p7_two_scopes(self):
        # FULL harness: P7 qualified for blocked (kind detour).
        # Then a slope task (signature disk_almost_full, no kind)
        # resolves via soso-transfer and earns a QUALIFIED record
        # for P7 under the slope scope (no demonstrated kind).
        # Every task is registered (add_task) so its frozen
        # observation exists.
        h = make_config_harness("FULL", [])
        slope = {"task_id": "h3-slope", "signature": "disk_almost_full",
                 "family": "slope",
                 "tags": ["slope", "ground", "steep", "cross"],
                 "conditions": {},
                 "requested_operation": "repair",
                 "provenance": ("int:harden-001",), "correct": "P7",
                 "synonym": False}
        h.add_task(slope)
        rep = _run(h, slope)
        assert rep["state"] == "RESOLVED" and rep["selected"] == "SOSO-P7"
        return h

    def test_scoped_withdrawal_leaves_other_scope_standing(self):
        h = self._harness_with_p7_two_scopes()
        try:
            ex = h.pipe.experiences
            before = sorted((t, r["qual"]) for (t, p), r in
                            ex.latest_per_pair().items() if p == "SOSO-P7")
            assert ("ct-train-blocked-a", "QUALIFIED") in before
            assert ("h3-slope", "QUALIFIED") in before
            withdrawn = ex.withdraw_procedure(
                procedure_id="SOSO-P7", scope={"kind": "detour"},
                observed_outcome="FAILURE", validator_result="FAIL",
                conditions={"kind": "detour"}, source="harden-001",
                why="P7 fails under detour")
            # The blocked scope is withdrawn (including the
            # UNDER_TEST transfer record); the slope scope is not.
            assert ("ct-train-blocked-a", "SOSO-P7") in withdrawn
            assert ("ct-xfer-blocked", "SOSO-P7") in withdrawn
            assert ("h3-slope", "SOSO-P7") not in withdrawn
            latest = ex.latest_per_pair()
            assert latest[("ct-train-blocked-a", "SOSO-P7")]["qual"] == "WITHDRAWN"
            assert latest[("h3-slope", "SOSO-P7")]["qual"] == "QUALIFIED"
            # History is preserved: the withdrawn pair's record
            # trail ends in WITHDRAWN, never deleted.
            hist = ex.history("ct-train-blocked-a", "SOSO-P7")
            assert [r["qual"] for r in hist] == [
                "CANDIDATE", "UNDER_TEST", "QUALIFIED", "WITHDRAWN"]
            assert check_projections(h) == []
            # The unaffected scope is still available: a new slope
            # task still resolves via lower-exact P7.
            slope2 = {"task_id": "h3-slope-2",
                      "signature": "disk_almost_full", "family": "slope",
                      "tags": ["slope", "ground", "loose", "incline"],
                      "conditions": {},
                      "requested_operation": "repair",
                      "provenance": ("int:harden-001",), "correct": "P7",
                      "synonym": False}
            h.add_task(slope2)
            rep = _run(h, slope2)
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P7"
            assert rep["stage"] == "lower-exact"
            # And a blocked task no longer trusts P7: it is refused
            # (contradicted/unknown) and re-learns by trial.
            blocked = {"task_id": "h3-blocked-2",
                       "signature": "cache_big", "family": "blocked",
                       "tags": ["route", "blocked", "debris", "rubble"],
                       "conditions": {"kind": "detour"},
                       "requested_operation": "repair",
                       "provenance": ("int:harden-001",), "correct": "P7",
                       "synonym": False}
            h.add_task(blocked)
            rep = _run(h, blocked)
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P7"
            assert rep["stage"] == "higher-trial"
            assert check_projections(h) == []
        finally:
            h.close()

    def test_global_withdrawal_removes_every_scope(self):
        h = self._harness_with_p7_two_scopes()
        try:
            ex = h.pipe.experiences
            withdrawn = ex.withdraw_procedure(
                procedure_id="SOSO-P7", scope=None,
                observed_outcome="FAILURE", validator_result="FAIL",
                conditions={}, source="harden-001",
                why="P7 defective across all scopes")
            assert ("ct-train-blocked-a", "SOSO-P7") in withdrawn
            assert ("h3-slope", "SOSO-P7") in withdrawn
            latest = ex.latest_per_pair()
            assert all(r["qual"] == "WITHDRAWN"
                       for (t, p), r in latest.items() if p == "SOSO-P7")
            # No P7 in either projection.
            proj = ex.rebuild_projections(h.pipe.soso)
            assert "SOSO-P7" not in proj["exact"].get("cache_big", {})
            assert "SOSO-P7" not in proj["exact"].get("disk_almost_full", {})
            assert check_projections(h) == []
            # A blocked task re-learns P7 by trial.
            blocked = {"task_id": "h3-blocked-g",
                       "signature": "cache_big", "family": "blocked",
                       "tags": ["route", "blocked", "debris", "rubble"],
                       "conditions": {"kind": "detour"},
                       "requested_operation": "repair",
                       "provenance": ("int:harden-001",), "correct": "P7",
                       "synonym": False}
            h.add_task(blocked)
            rep = _run(h, blocked)
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P7"
            assert rep["stage"] == "higher-trial"
        finally:
            h.close()

    def test_scoped_withdrawal_with_no_trusted_pair_is_a_noop(self):
        h = make_config_harness("FULL", [])
        try:
            ex = h.pipe.experiences
            # No P7 record was demonstrated under firmware v2.
            withdrawn = ex.withdraw_procedure(
                procedure_id="SOSO-P7", scope={"firmware": "v2"},
                observed_outcome="FAILURE", validator_result="FAIL",
                conditions={"firmware": "v2"}, source="harden-001",
                why="no such scope")
            assert withdrawn == []
            assert check_projections(h) == []
        finally:
            h.close()


class TestFix4ThresholdPersistence:
    """A restarted process recovers the stored threshold; an
    incompatible configuration fails explicitly."""

    def test_second_process_adopts_stored_threshold(self):
        root = tempfile.mkdtemp()
        a = GenerationsAdapter(root, threshold=0.05)
        assert a.threshold == 0.05
        a.close()
        b = GenerationsAdapter(root, threshold=0.05)
        assert b.threshold == 0.05
        b.close()

    def test_conflicting_threshold_errors_explicitly(self):
        root = tempfile.mkdtemp()
        a = GenerationsAdapter(root, threshold=0.05)
        a.close()
        try:
            GenerationsAdapter(root, threshold=0.8)
        except ValueError as e:
            assert "0.05" in str(e) and "0.8" in str(e)
        else:
            raise AssertionError("conflicting threshold did not raise")

    def test_harness_restart_adopts_stored_threshold(self):
        root = tempfile.mkdtemp()
        h = Harness(root=root, generations_threshold=0.05)
        try:
            register_demo(h)
            rep = h.runner.run(DEMO_TASK)
            assert rep["status"] in ("DONE", "PARTIAL")
        finally:
            h.close()
        # A restarted process on the same root with the same
        # threshold recovers the generations and the demo task.
        h2 = Harness(root=root, generations_threshold=0.05)
        try:
            assert h2.generations.threshold == 0.05
            assert h2.generations.locate("ct-demo-repair") == 0
        finally:
            h2.close()
        # And a conflicting threshold fails explicitly on restart.
        try:
            Harness(root=root, generations_threshold=0.8)
        except ValueError as e:
            assert "0.05" in str(e) and "0.8" in str(e)
        else:
            raise AssertionError("conflicting threshold did not raise")


class TestNoStaleTrustedRetrieval:
    """Zero stale trusted retrieval under changed conditions: after
    a firmware flip, no trusted retrieval serves the stale
    procedure — it is refused with the contradiction named, never
    attempted."""

    def test_firmware_flip_serves_no_stale_procedure(self):
        # Learn P1 under v1 (trial -> CANDIDATE -> qualified via a
        # held-out transfer task), then present a v2 task. P1 must
        # be refused with the contradiction named — at exact AND
        # at transfer — never attempted; P2 resolves the task.
        import random
        from contract.eval001 import make_task, qualify_learned
        h = make_config_harness("FULL", [])
        try:
            rng = random.Random(20261118)
            learn = make_task(rng, "valve", "h4-v1-learn")
            learn["tags"] = ["valve", "firmware_v1", "stuck", "pressure"]
            learn["conditions"] = {"firmware": "v1"}
            learn["correct"] = "P1"
            h.add_task(learn)
            rep = _run(h, learn)
            assert rep["state"] == "RESOLVED" and rep["selected"] == "SOSO-P1"
            q = qualify_learned(h, learn, "P1", rng)
            assert q is not None and q["stage"] == "soso-transfer"
            v2 = make_task(rng, "valve", "h4-v2")
            h.add_task(v2)
            rep = _run(h, v2)
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P2"
            assert all(a["procedure"] != "SOSO-P1"
                       for a in rep["attempts"])
            assert any(r["procedure"] == "SOSO-P1"
                       and "contradicted" in r["reason"]
                       for r in rep["rejected"])
            assert check_projections(h) == []
        finally:
            h.close()
