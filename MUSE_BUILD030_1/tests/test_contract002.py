"""Integration Contract 002 — tests.

Requirements: (1) Small49 generations, (2) crash/restart, (3)
supersession/withdrawal, (4) family-ranked trial, (5) shared
evidence, (6) policy changes, (7) independent validation.
Plus the end-to-end demonstration (run as a subprocess).
"""
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.admission import Policy, Validator
from contract.harness import (DEMO_TASK, Harness, SOSO_MODULES,
                              register_demo)
from contract.registry import by_native, get
from contract.task import make_task
from contract.trace import trace_events


def _trained(threshold=0.8, **kw):
    h = Harness(generations_threshold=threshold, **kw)
    h.train_baseline()
    return h


def _task(**kw):
    base = {"schema_version": "contract-task/1",
            "task_id": "ct-test-1", "signature": "disk_full",
            "family": "valve", "tags": ["valve", "firmware_v2", "stuck"],
            "conditions": {"firmware": "v2"},
            "requested_operation": "repair",
            "provenance": ("int:contract-test",)}
    base.update(kw)
    return base


# ------------------------------------------------------------------
# (1) Small49 task generations
# ------------------------------------------------------------------

class TestGenerations:
    def test_journal_begin_record_done_round_trip(self):
        h = _trained()
        try:
            register_demo(h)
            rep = h.runner.run(DEMO_TASK)
            assert rep["status"] == "DONE"
            j, gen = h.generations.journal_for(
                "ct-demo-repair", 5, rep["identity_digest"])
            st = j.state("ct-demo-repair")
            assert st["status"] == "DONE"
            assert st["done_steps"] == 5
            assert st["plan"] == ["s1-valve", "s2-config", "s3-synonym",
                                  "s4-valve-v1", "s5-blocked"]
            assert st["identity_digest"] == rep["identity_digest"]
            assert st["policy_version"] == "contract-001-v1"
            outs = {r["s"]: r for r in st["outcomes"]}
            assert outs["s1-valve"]["a"] == "SOSO-P2"
            assert outs["s1-valve"]["o"] == "SUCCESS"
            assert outs["s1-valve"]["v"] == "PASS"
            assert outs["s2-config"]["i"] == "iou.ct-demo-repair.s2-config"
            assert outs["s2-config"]["o"] == "FAILURE"
            # Idempotent re-run: a DONE task is not re-executed.
            # TRACE-002: the re-run returns the STORED step
            # outcomes (so a resume reports what the task did)
            # without running any step again — the journal's
            # done_steps and the trace's step executions are
            # unchanged, and the resume's own parent execution
            # is certified.
            n_step_events = len(trace_events(
                h.pipe.trace, "ct-demo-repair.s1-valve"))
            rep2 = h.runner.run(DEMO_TASK)
            assert rep2["status"] == "DONE"
            assert [s["step_id"] for s in rep2["steps"]] == [
                "s1-valve", "s2-config", "s3-synonym",
                "s4-valve-v1", "s5-blocked"]
            assert all(s["classification"] == "COMPLETED"
                       for s in rep2["steps"])
            assert rep2["trace_certified"]
            assert len(trace_events(
                h.pipe.trace, "ct-demo-repair.s1-valve")) == n_step_events
            st2 = j.state("ct-demo-repair")
            assert st2["done_steps"] == 5
        finally:
            h.close()

    def test_rollover_preserves_prior_generations(self):
        h = _trained(threshold=0.05)
        try:
            register_demo(h)
            rep = h.runner.run(DEMO_TASK)
            assert rep["generation"] == 0
            # Filler tasks fill generation 0; the next one rolls over.
            gens_seen = set()
            for i in range(4):
                tid = f"ct-fill-{i}"
                step = {"task_id": f"{tid}.s1", "step_id": "s1",
                        "signature": "disk_full", "family": "valve",
                        "tags": ["valve", "firmware_v2", "stuck"],
                        "conditions": {"firmware": "v2"},
                        "requested_operation": "repair",
                        "provenance": ("int:contract-test",),
                        "correct": "P2"}
                h.add_task(step)
                spec = {"task_id": tid, "signature": "disk_full",
                        "family": "valve",
                        "tags": ["valve", "firmware_v2", "stuck"],
                        "conditions": {"firmware": "v2"},
                        "requested_operation": "repair",
                        "provenance": ("int:contract-test",),
                        "steps": [step]}
                r = h.runner.run(spec)
                gens_seen.add(r["generation"])
            assert 1 in gens_seen, gens_seen
            h.generations.reconcile()
            table = dict(h.generations.generation_table())
            assert table[0] == 1, table  # gen 0 sealed
            tasks = dict(h.generations.task_table())
            assert tasks["ct-demo-repair"] == 0
            # Every filler landed in a later generation (rollover
            # happened at least once); the exact generation each
            # filler lands in depends on the capacity counts.
            assert all(tasks[f"ct-fill-{i}"] >= 1 for i in range(4))
            # The demo task's journal survives the rollover intact.
            j, gen = h.generations.journal_for(
                "ct-demo-repair", 5, rep["identity_digest"])
            assert gen == 0
            st = j.state("ct-demo-repair")
            assert st["status"] == "DONE" and st["done_steps"] == 5
            # A fresh adapter rebuilds the same index from the files.
            from contract.generations import GenerationsAdapter
            g2 = GenerationsAdapter(os.path.join(h.root, "generations"),
                                    threshold=0.05)
            try:
                assert dict(g2.task_table()) == tasks
            finally:
                g2.close()
        finally:
            h.close()


# ------------------------------------------------------------------
# (2) Crash and restart
# ------------------------------------------------------------------

class TestCrashAndRestart:
    def _crash_run(self, helper, root):
        proc = subprocess.run([sys.executable, helper, root],
                              capture_output=True, text=True)
        assert proc.returncode == -9, proc.stderr[-800:]

    def test_sigkill_during_step_leaves_ambiguous_step(self):
        helper = os.path.join(os.path.dirname(__file__),
                              "crash_multistep_ambiguous.py")
        root = tempfile.mkdtemp(prefix="contract002-crash-")
        try:
            self._crash_run(helper, root)
            h = Harness(root=root, generations_threshold=0.05)
            try:
                # The parent process needs the demo's criteria and
                # frozen observations (they were registered in the
                # crashed process, not on disk).
                register_demo(h)
                # Recovery BEFORE the resume: the crash is visible.
                rec = h.pipe.recover()
                assert "ct-demo-repair.s3-synonym" in rec["running"]
                assert ["ct-demo-repair.s3-synonym", None] in rec["ambiguous"]
                # The journal: step 1 completed, step 2 pending + IOU.
                j, gen = h.generations.journal_for(
                    "ct-demo-repair", 5,
                    h.runner.begin(DEMO_TASK)["identity_digest"])
                st = j.state("ct-demo-repair")
                assert st["status"] == "RUNNING" and st["done_steps"] == 2
                # Resume: step 1 is NOT re-executed (one intent only).
                intents_before = len([1 for _, k, t, _, _ in
                                      h.pipe.trace.events("ct-demo-repair.s1-valve")
                                      if k == "ACTION_INTENT"])
                rep = h.runner.run(DEMO_TASK)
                intents_after = len([1 for _, k, t, _, _ in
                                     h.pipe.trace.events("ct-demo-repair.s1-valve")
                                     if k == "ACTION_INTENT"])
                assert intents_before == intents_after == 1
                by_id = {s["step_id"]: s for s in rep["steps"]}
                assert by_id["s1-valve"]["classification"] == "COMPLETED"
                assert by_id["s2-config"]["classification"] == "COMPLETED"
                assert by_id["s2-config"]["iou"] == "iou.ct-demo-repair.s2-config"
                # Step 3 was ambiguous: re-run (at-least-once: two intents).
                assert by_id["s3-synonym"]["classification"] == "AMBIGUOUS"
                assert by_id["s3-synonym"]["outcome"] == "SUCCESS"
                s3_intents = len([1 for _, k, t, _, _ in
                                  h.pipe.trace.events("ct-demo-repair.s3-synonym")
                                  if k == "ACTION_INTENT"])
                assert s3_intents == 2
                assert by_id["s4-valve-v1"]["classification"] == "UNCOMMITTED"
                assert by_id["s5-blocked"]["classification"] == "UNCOMMITTED"
                assert rep["status"] == "DONE" and rep["result"] == "PARTIAL"
            finally:
                h.close()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_crash_after_step_run_is_reconstructable_not_reexecuted(self):
        helper = os.path.join(os.path.dirname(__file__),
                              "crash_multistep_reconstruct.py")
        root = tempfile.mkdtemp(prefix="contract002-recon-")
        try:
            self._crash_run(helper, root)
            h = Harness(root=root)
            try:
                register_demo(h)
                rep = h.runner.run(DEMO_TASK)
                by_id = {s["step_id"]: s for s in rep["steps"]}
                # Step 3's run finished (trace has TASK_DONE); only
                # the journal record was missing. It is rebuilt
                # from the trace, NOT re-executed.
                assert by_id["s3-synonym"]["classification"] == "RECONSTRUCTABLE"
                s3_intents = len([1 for _, k, t, _, _ in
                                  h.pipe.trace.events("ct-demo-repair.s3-synonym")
                                  if k == "ACTION_INTENT"])
                assert s3_intents == 1
                assert by_id["s3-synonym"]["outcome"] == "SUCCESS"
            finally:
                h.close()
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------
# (3) Supersession / withdrawal
# ------------------------------------------------------------------

class TestQualificationLifecycle:
    def test_withdrawal_removes_trust_and_preserves_history(self):
        h = _trained()
        try:
            # Withdraw every trusted pair for P2.
            pairs = [("ct-train-valve-a", "SOSO-P2"),
                     ("ct-train-valve-b", "SOSO-P2"),
                     ("ct-train-valve-c", "SOSO-P2"),
                     ("ct-xfer-valve", "SOSO-P2")]
            for tid, proc in pairs:
                h.pipe.experiences.withdraw(
                    task_id=tid, procedure_id=proc,
                    observed_outcome="FAILURE", validator_result="INCONCLUSIVE",
                    conditions={"firmware": "v2"}, source="qualification-review",
                    why="P2 failed under firmware v2 on a retest")
            proj = h.pipe.experiences.rebuild_projections(SOSO_MODULES)
            assert "SOSO-P2" not in proj["exact"].get("disk_full", {})
            succ = SOSO_MODULES["cognitive_layer"].success_sets(
                proj["transfer_conn"])
            assert "P2" not in succ
            hist = h.pipe.experiences.history("ct-train-valve-a", "SOSO-P2")
            assert [r["qual"] for r in hist] == ["CANDIDATE", "UNDER_TEST",
                                                 "QUALIFIED", "WITHDRAWN"]
            assert hist[-1]["why"].startswith("P2 failed")
            # A re-presentation no longer reuses it exactly; it can
            # still be re-learned by trial (withdrawal removes
            # trust, not the procedure from the registry).
            h.add_task({**_task(task_id="ct-relearn"), "correct": "P2"})
            r = h.pipe.run(_task(task_id="ct-relearn"))
            assert r["stage"] != "lower-exact"
            assert r["selected"] == "SOSO-P2" and r["state"] == "RESOLVED"
        finally:
            h.close()

    def test_supersession_replaces_one_qualification_with_another(self):
        h = _trained()
        try:
            sup = h.pipe.experiences.supersede(
                old_task_id="ct-train-valve-a", procedure_id="SOSO-P2",
                new_task_id="ct-valve-new", source="qualification-review",
                why="re-qualified under firmware v2 on a new task")
            assert sup["qual"] == "SUPERSEDED" and sup["sup"] == "ct-valve-new"
            hist = h.pipe.experiences.history("ct-train-valve-a", "SOSO-P2")
            assert hist[-1]["qual"] == "SUPERSEDED"
            # The old pair is out of the trusted set, but the
            # procedure's standing for the old signature survives
            # via the other pairs (valve-b, valve-c are still
            # QUALIFIED) — supersession replaces one pair's
            # qualification, it does not revoke the procedure.
            proj = h.pipe.experiences.rebuild_projections(SOSO_MODULES)
            assert proj["exact"]["disk_full"]["SOSO-P2"]["task"] in (
                "ct-train-valve-b", "ct-train-valve-c")
            # The new pair earns its own standing under a new
            # signature, through the normal lifecycle
            # (trial -> under test -> transfer).
            # Tags disjoint from every trusted P2 record (so
            # the under-test check cannot propose it either).
            t = {"task_id": "ct-valve-new", "signature": "disk_full_2",
                 "family": "valve",
                 "tags": ["seized", "line", "rising", "restricted"],
                 "conditions": {"firmware": "v2"},
                 "requested_operation": "repair",
                 "provenance": ("int:contract-test",), "correct": "P2"}
            h.add_task(t)
            r = h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            assert r["stage"] == "higher-trial"
            xfer = {"task_id": "ct-valve-new-xfer",
                    "signature": "disk_full_2", "family": "valve",
                    "tags": ["valve", "firmware_v2", "stuck", "pressure"],
                    "conditions": {"firmware": "v2"},
                    "requested_operation": "repair",
                    "provenance": ("int:contract-test",),
                    "correct": "P2"}
            h.add_task(xfer)
            q = h.pipe.qualify("ct-valve-new", "SOSO-P2",
                               {k: v for k, v in xfer.items() if k != "correct"})
            assert q["state"] == "RESOLVED"
            proj = h.pipe.experiences.rebuild_projections(SOSO_MODULES)
            # The new pair is QUALIFIED for the new signature (the
            # exact index holds the latest QUALIFIED record for
            # that (signature, procedure) — the transfer task's own
            # record, written with the procedure's standing at the
            # time it ran; the original pair's QUALIFIED record is
            # in its history).
            assert proj["exact"]["disk_full_2"]["SOSO-P2"]["qual"] == "QUALIFIED"
            hist = h.pipe.experiences.history("ct-valve-new", "SOSO-P2")
            assert [r["qual"] for r in hist] == ["CANDIDATE", "UNDER_TEST",
                                                 "QUALIFIED"]
        finally:
            h.close()


# ------------------------------------------------------------------
# (4) Family-ranked trial
# ------------------------------------------------------------------

class TestFamilyRanking:
    def test_ranking_reduces_synonym_fallback_without_losing_resolutions(self):
        h = _trained()
        try:
            # Contract 001 measured 8 attempts for the synonym
            # fallback; the family rank must cut it.
            t = {"task_id": "ct-syn-2", "signature": "diskfull",
                 "family": "slope",
                 "tags": ["hillside", "terrain", "traverse", "ascend"],
                 "conditions": {}, "requested_operation": "repair",
                 "provenance": ("int:contract-test",), "correct": "P7"}
            h.add_task(t)
            r = h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            assert r["state"] == "RESOLVED" and r["selected"] == "SOSO-P7"
            assert len(r["attempts"]) == 1, [a["procedure"] for a in r["attempts"]]
            # The training tasks still resolve in one attempt each.
            for sig, proc, tags in (
                    ("disk_full", "P2", ["valve", "firmware_v2", "stuck", "pressure"]),
                    ("svc_hung", "P3", ["sensor", "temperature", "drift", "readings"]),
                    ("cfg_bad", "P4", ["pump", "intermittent", "vibration", "bearing"]),
                    ("cache_big", "P7", ["route", "blocked", "debris", "rubble"])):
                tid = f"ct-rank-{sig}"
                cond = {"firmware": "v2"} if proc == "P2" else (
                    {"kind": "detour"} if proc == "P7" else {})
                tt = {"task_id": tid, "signature": sig,
                      "family": "valve" if proc == "P2" else "sensor" if proc == "P3"
                      else "pump" if proc == "P4" else "blocked",
                      "tags": tags, "conditions": cond,
                      "requested_operation": "repair",
                      "provenance": ("int:contract-test",), "correct": proc}
                h.add_task(tt)
                rr = h.pipe.run({k: v for k, v in tt.items() if k != "correct"})
                assert rr["state"] == "RESOLVED", (sig, rr)
                assert rr["selected"] == "SOSO-" + proc
                assert len(rr["attempts"]) == 1
        finally:
            h.close()

    def test_cross_family_candidates_stay_eligible(self):
        h = _trained()
        try:
            # A slope task: P6 (family "narrow") and the RealCog
            # fixes (families disk/service/config/cache) must still
            # be in the trial list — the family rank never filters.
            task = make_task(**_task(task_id="ct-xfam", signature="diskfull",
                                     family="slope",
                                     tags=["hillside", "terrain", "traverse", "ascend"],
                                     conditions={}))
            cands = h.pipe._trial_candidates(task)["candidates"]
            ids = [e.procedure_id for e in cands]
            assert "SOSO-P6" in ids and "RC-FIX-ROTATE-LOGS" in ids
            # And the family-ranked procedure is first.
            assert ids[0] == "SOSO-P7"
            # A wholly new family still transfers by description.
            t = {"task_id": "ct-flood-1", "signature": "flood_1",
                 "family": "flood",
                 "tags": ["flood", "water", "detour", "rising"],
                 "conditions": {}, "requested_operation": "repair",
                 "provenance": ("int:contract-test",), "correct": "P7"}
            h.add_task(t)
            r = h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            assert r["state"] == "RESOLVED" and r["selected"] == "SOSO-P7"
            assert len(r["attempts"]) == 1
        finally:
            h.close()


# ------------------------------------------------------------------
# (5) Shared evidence
# ------------------------------------------------------------------

class TestSharedEvidence:
    def test_history_and_projections_survive_restart_and_agree(self):
        import tempfile
        root = tempfile.mkdtemp(prefix="contract002-evidence-")
        h = Harness(root=root)
        h.train_baseline()
        h.close()
        h2 = Harness(root=root)
        try:
            hist = h2.pipe.experiences.history("ct-train-valve-a", "SOSO-P2")
            assert [r["qual"] for r in hist] == ["CANDIDATE", "UNDER_TEST",
                                                 "QUALIFIED"]
            assert hist[0]["src"] == "higher-trial"
            assert hist[-1]["src"] == "qualification"
            proj = h2.pipe.experiences.rebuild_projections(SOSO_MODULES)
            # Both projections are views of the same records: every
            # QUALIFIED pair is in the exact index and its procedure
            # is in the transfer layer's success sets.
            for sig, procs in proj["exact"].items():
                for proc_id, rec in procs.items():
                    assert rec["qual"] == "QUALIFIED"
            succ = SOSO_MODULES["cognitive_layer"].success_sets(
                proj["transfer_conn"])
            for proc_id in ("P2", "P3", "P4", "P7"):
                assert proc_id in succ
        finally:
            h2.close()
            shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------
# (6) Policy changes
# ------------------------------------------------------------------

class TestPolicyChanges:
    def test_procedure_admitted_under_v1_cannot_execute_under_v2(self):
        h = _trained()
        try:
            # Step 1 under policy v1 (P2 admitted).
            spec = {"task_id": "ct-pol-1", "signature": "disk_full",
                    "family": "valve",
                    "tags": ["valve", "firmware_v2", "stuck"],
                    "conditions": {"firmware": "v2"},
                    "requested_operation": "repair",
                    "provenance": ("int:contract-test",),
                    "steps": [
                        {"task_id": "ct-pol-1.s1", "step_id": "s1",
                         "signature": "disk_full", "family": "valve",
                         "tags": ["valve", "firmware_v2", "stuck", "pressure"],
                         "conditions": {"firmware": "v2"},
                         "requested_operation": "repair",
                         "provenance": ("int:contract-test",), "correct": "P2"},
                        {"task_id": "ct-pol-1.s2", "step_id": "s2",
                         "signature": "disk_full", "family": "valve",
                         "tags": ["valve", "firmware_v2", "stuck", "jammed"],
                         "conditions": {"firmware": "v2"},
                         "requested_operation": "repair",
                         "provenance": ("int:contract-test",), "correct": "P2"},
                    ]}
            for s in spec["steps"]:
                h.add_task(s)

            def _stop_after_step_1(point, idx, classification):
                if point == "after_step_record" and idx == 0:
                    raise RuntimeError("stop after step 1")

            with pytest.raises(RuntimeError, match="stop after step 1"):
                h.runner.run(spec, crash_hook=_stop_after_step_1)
            # Step 1 ran under policy v1 (P2 admitted).
            j, _g = h.generations.journal_for(
                "ct-pol-1", 2, h.runner.begin(spec)["identity_digest"])
            st = j.state("ct-pol-1")
            assert st["outcomes"][0]["a"] == "SOSO-P2"
            assert st["outcomes"][0]["p"] == "contract-001-v1"
            # The policy changes: P2 is now forbidden.
            h.pipe.set_policy(Policy("contract-002-v1",
                                     forbidden=("RC-FIX-WIPE-DISK", "SOSO-P2")))
            # Resume: step 2 must NOT execute P2.
            r2 = h.runner.run(spec)
            by_id = {x["step_id"]: x for x in r2["steps"]}
            assert by_id["s1"]["classification"] == "COMPLETED"
            assert by_id["s1"]["procedure"] == "SOSO-P2"
            assert by_id["s2"]["procedure"] != "SOSO-P2"
            assert by_id["s2"]["outcome"] == "FAILURE"
            assert by_id["s2"]["policy_version"] == "contract-002-v1"
            # The refusal is recorded: no P2 execution after the change.
            p2_after = [1 for _, k, t, _, d in h.pipe.trace.events()
                        if k == "ACTION_INTENT" and (d or {}).get("action") == "P2"
                        and t == "ct-pol-1.s2"]
            assert p2_after == []
            kinds = [k for _, k, _, _, _ in h.pipe.trace.events()]
            assert "POLICY_CHANGE" in kinds
        finally:
            h.close()

    def test_mid_run_revalidation_refuses_after_a_policy_flip(self):
        h = Harness()
        try:
            h.train_baseline()

            flipped = []

            def flip(task, entry):
                # Flip the policy between admission and execution,
                # once, only for the test task.
                if task.task_id == "ct-flip" and not flipped:
                    flipped.append(1)
                    h.pipe.set_policy(Policy(
                        "contract-002-v1",
                        forbidden=("RC-FIX-WIPE-DISK", "SOSO-P2")))

            h.pipe.pre_execute_hook = flip
            h.add_task({**_task(task_id="ct-flip"), "correct": "P2"})
            r = h.pipe.run(_task(task_id="ct-flip"))
            # P2 was admitted, then the policy flipped mid-run: the
            # revalidation must refuse it, and nothing executes it.
            assert not any(a["procedure"] == "SOSO-P2" for a in r["attempts"])
            assert any(x["procedure"] == "SOSO-P2" and "policy" in x["reason"]
                       for x in r["rejected"])
            kinds = [k for _, k, _, _, _ in h.pipe.trace.events()]
            assert "REVALIDATION_DENIED" in kinds
        finally:
            h.close()


# ------------------------------------------------------------------
# (7) Independent validation
# ------------------------------------------------------------------

class TestIndependentValidation:
    def test_validator_rules_in_isolation(self):
        v = Validator({"ct-v": "P7"})
        task = make_task(**_task(task_id="ct-v", signature="cache_big",
                                 family="blocked",
                                 tags=["route", "blocked"]))
        assert v.validate(task, "P7", True) == "PASS"
        assert v.validate(task, "P5", True) == "FAIL"   # false success caught
        assert v.validate(task, "P7", False) == "INCONCLUSIVE"
        assert v.validate(task, "P5", False) == "PASS"  # confirmed negative
        assert Validator({}).validate(task, "P7", True) == "INCONCLUSIVE"

    def test_misleading_success_claim_is_caught_not_learned(self):
        h = _trained()
        try:
            # The executor LIES: P5 (the wrong procedure) "succeeds"
            # on a blocked task. The criteria independently say P7.
            # A NEW signature (so the exact index does not hit),
            # tags that share one word with P7's applicability but
            # not two, no description word with P7, and a kind that
            # contradicts P7's demonstrated kind (so the under-test
            # check does not propose it). The transfer stage then
            # proposes nothing and the trial tries P5 first
            # (family "blocked" + the word "route") — where it lies.
            t = {"task_id": "ct-lie-1", "signature": "cache_big_2",
                 "family": "blocked",
                 "tags": ["route", "crates", "shut", "passage"],
                 "conditions": {"kind": "traverse"},
                 "requested_operation": "repair",
                 "provenance": ("int:contract-test",), "correct": "P7"}
            h.add_task(t)
            # Override the frozen observations for this task: P5 lies.
            h.pipe.observations[("ct-lie-1", "P5")] = True
            h.pipe.observations[("ct-lie-1", "P7")] = True
            r = h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            assert r["state"] == "RESOLVED" and r["selected"] == "SOSO-P7"
            p5 = [a for a in r["attempts"] if a["procedure"] == "SOSO-P5"]
            assert len(p5) == 1
            assert p5[0]["observed"] is True and p5[0]["validation"] == "FAIL"
            # The false success is recorded REJECTED, never learned.
            rec = h.pipe.experiences.latest_per_pair()[("ct-lie-1", "SOSO-P5")]
            assert rec["obs"] == "SUCCESS" and rec["val"] == "FAIL"
            assert rec["qual"] == "REJECTED"
            proj = h.pipe.experiences.rebuild_projections(SOSO_MODULES)
            succ = SOSO_MODULES["cognitive_layer"].success_sets(
                proj["transfer_conn"])
            assert all("debris" not in s or True for s in succ.get("P5", []))
            assert "P5" not in succ or not any(
                s == frozenset({"route", "blocked", "debris", "rubble"})
                for s in succ["P5"])
        finally:
            h.close()

    def test_liar_executor_cannot_certify_itself(self):
        h = _trained()
        try:
            # The executor claims success for EVERY procedure. The
            # validator must pass only the criteria-correct one.
            t = {"task_id": "ct-liar-1", "signature": "flood_9",
                 "family": "flood",
                 "tags": ["flood", "water", "rising"],
                 "conditions": {},
                 "requested_operation": "repair",
                 "provenance": ("int:contract-test",), "correct": "P7"}
            h.add_task(t)
            for native in ("P1", "P2", "P3", "P4", "P5", "P6", "P7",
                           "clear_cache", "restart_svc", "rotate_logs"):
                h.pipe.observations[("ct-liar-1", native)] = True
            r = h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            assert r["state"] == "RESOLVED" and r["selected"] == "SOSO-P7"
            # Every wrong procedure that claimed success was caught;
            # only the criteria-correct one passed.
            wrong = [a for a in r["attempts"] if a["procedure"] != "SOSO-P7"]
            assert len(wrong) >= 5, [a["procedure"] for a in r["attempts"]]
            for a in r["attempts"]:
                if a["procedure"] == "SOSO-P7":
                    assert a["validation"] == "PASS"
                else:
                    assert a["validation"] == "FAIL", a
        finally:
            h.close()


# ------------------------------------------------------------------
# End-to-end demonstration (subprocess)
# ------------------------------------------------------------------

class TestEndToEndDemo:
    def test_demo_script_passes(self):
        demo = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "demo_contract002.py")
        proc = subprocess.run([sys.executable, demo],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
        assert "DEMO COMPLETE" in proc.stdout
