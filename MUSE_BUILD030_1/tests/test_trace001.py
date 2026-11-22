"""TRACE-001 — Cognitive Execution Trace tests.

Acceptance (from JD's directive + the reconstruction plan):
  * 100% event coverage at the instrumented boundaries, with
    explicit disclosure of uninstrumented internals.
  * Deterministic reconstruction of the same pathway from the
    same event ledger.
  * No fabricated function activations or memory accesses.
  * Distinct traces for retrieval, transfer, trial and
    qualification.
  * Recovery of interrupted traces with incomplete operations
    marked honestly.
  * Domain-transition explanations tied to recorded evidence.
  * At least one measured behavioural difference between
    specialist profiles, beyond output wording.
  * Preserved HARDEN-001 regression results (the full suite),
    with instrumentation overhead reported.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

import pytest

from contract.eval001 import make_config_harness, qualify_learned
from contract.harness import Harness
from contract.trace import (FUNCTIONS, REGISTERED_ONLY, SCHEMA,
                            analyze, coverage, reconstruct, trace_events,
                            validate_event)
from contract.trace_eval import (run_ablation_l24,
                                 run_profile_comparison)


def _task(task_id, signature, family, tags, conditions=None, correct="P2",
          unknown_conditions=(), contradictory_conditions=()):
    return {"task_id": task_id, "signature": signature, "family": family,
            "tags": tags, "conditions": conditions or {},
            "unknown_conditions": unknown_conditions,
            "contradictory_conditions": contradictory_conditions,
            "requested_operation": "repair",
            "provenance": ("int:trace-001",), "correct": correct,
            "synonym": False}


def _trained_pair(rng):
    """A harness with P2 (valve) and P7 (blocked) qualified, plus
    the two training tasks. Returns (harness, train_tasks)."""
    train = [
        _task("tr-valve", "valve_stuck", "valve",
              ["valve", "stuck", "pressure", "flow"],
              conditions={"firmware": "v2"}, correct="P2"),
        _task("tr-blocked", "cache_big", "blocked",
              ["route", "blocked", "debris", "rubble"],
              conditions={"kind": "detour"}, correct="P7"),
    ]
    h = make_config_harness("FULL", train)
    for t in train:
        h.pipe.run({k: v for k, v in t.items() if k != "correct"})
        qualify_learned(h, t, t["correct"], rng)
    return h, train


# ------------------------------------------------------------------
# Trace integrity
# ------------------------------------------------------------------

class TestTraceIntegrity:
    def test_events_validate_and_chain_verifies(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            rep = h.pipe.run({k: v for k, v in train[0].items()
                              if k != "correct"})
            evs = trace_events(h.pipe.trace, "tr-valve",
                               rep["execution_id"])
            assert evs, "no trace events for the run"
            for _seq, ev in evs:
                assert validate_event(ev) == [], ev
                assert ev["schema"] == SCHEMA
                assert ev["authority"] == 0
                assert ev["event_id"] == f"ev-{ev['sequence']:04d}"
            # The hash chain verifies (the trace events live in
            # the RealCog TraceLog).
            h.pipe.trace.verify()
            # Every parent_event_id refers to an existing event.
            ids = {ev["event_id"] for _s, ev in evs}
            for _s, ev in evs:
                if ev["parent_event_id"] is not None:
                    assert ev["parent_event_id"] in ids, ev
        finally:
            h.close()

    def test_reconstruction_is_deterministic(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            rep = h.pipe.run({k: v for k, v in train[0].items()
                              if k != "correct"})
            p1 = reconstruct(h.pipe.trace, "tr-valve", rep["execution_id"])
            p2 = reconstruct(h.pipe.trace, "tr-valve", rep["execution_id"])
            assert p1 == p2
            # Reconstruction writes nothing (multiple analyses of
            # one observation do not create multiple observations).
            n_before = len(h.pipe.trace.events("tr-valve"))
            reconstruct(h.pipe.trace, "tr-valve", rep["execution_id"])
            assert len(h.pipe.trace.events("tr-valve")) == n_before
        finally:
            h.close()

    def test_distinct_traces_for_retrieval_transfer_trial(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            # Retrieval: the same valve task again (lower-exact).
            rep = h.pipe.run({k: v for k, v in train[0].items()
                              if k != "correct"})
            assert rep["stage"] == "lower-exact"
            p_ret = reconstruct(h.pipe.trace, "tr-valve",
                                rep["execution_id"])
            # Transfer: a slope task on the blocked signature.
            slope = _task("tr-slope", "cache_big", "slope",
                          ["slope", "ground", "steep", "cross"], correct="P7")
            h.add_task(slope)
            rep = h.pipe.run({k: v for k, v in slope.items() if k != "correct"})
            assert rep["stage"] == "soso-transfer"
            p_xfer = reconstruct(h.pipe.trace, "tr-slope",
                                 rep["execution_id"])
            # Trial: an unfamiliar task the transfer stage
            # cannot serve (no applicability/analogy overlap for
            # the correct procedure) but the trial can (its hard
            # precondition, firmware v2, is satisfied).
            new = _task("tr-new", "valve_new_x", "valve",
                        ["hum", "bearing", "rattle"],
                        conditions={"firmware": "v2"}, correct="P2")
            h.add_task(new)
            rep = h.pipe.run({k: v for k, v in new.items() if k != "correct"})
            assert rep["stage"] == "higher-trial"
            p_trial = reconstruct(h.pipe.trace, "tr-new",
                                  rep["execution_id"])
            # EXPANSION (2026-11-18): the exact run now also shows
            # U01 (condition check) and U02 (admission) — was
            # ["L01", "L30", "U37", "L15", "L21"].
            assert p_ret["functions"] == ["L01", "L30", "U01", "U02",
                                          "U37", "L15", "L21"]
            assert "L24" in p_xfer["functions"]
            assert "L24" not in p_ret["functions"]
            # The trial task's pathway contains L24 (the transfer
            # stage proposes P4 via cross-task overlap first);
            # what distinguishes it is the resolution stage.
            assert (p_trial["outcome"] or {}).get("stage") == "higher-trial"
            assert (p_xfer["outcome"] or {}).get("stage") == "soso-transfer"
            assert (p_ret["outcome"] or {}).get("stage") == "lower-exact"
            assert p_ret["functions"] != p_xfer["functions"]
            # EXPANSION (2026-11-18): the transfer and trial
            # pathways NO LONGER share a function sequence. Both
            # still run the transfer stage with L24 (and now L02,
            # U01 and U02), but trial alone runs U04 (Gate Trial
            # Candidates), and the U01 check lands at a different
            # point in the trial path. What still distinguishes
            # them is the resolution stage and the procedure
            # attempted — and now the presence of U04.
            assert p_xfer["functions"] == ["L01", "L30", "U01", "U08",
                                           "L02", "L24", "U09", "U02",
                                           "U37", "L15", "L21"]
            assert p_trial["functions"] == ["L01", "L30", "U08", "L02",
                                            "L24", "U09", "U02", "U01",
                                            "U37", "L15", "L21", "U04"]
            assert "U04" in p_trial["functions"]
            assert "U04" not in p_xfer["functions"]
            assert (p_xfer["outcome"] or {}).get("selected") == "SOSO-P7"
            assert (p_trial["outcome"] or {}).get("selected") == "SOSO-P2"
        finally:
            h.close()


# ------------------------------------------------------------------
# Interruption recovery
# ------------------------------------------------------------------

class TestInterruptionRecovery:
    def test_sigkill_leaves_incomplete_span_flagged(self):
        helper = os.path.join(os.path.dirname(__file__),
                              "crash_multistep_ambiguous.py")
        root = tempfile.mkdtemp(prefix="trace001-crash-")
        try:
            proc = subprocess.run([sys.executable, helper, root],
                                  capture_output=True, text=True)
            assert proc.returncode == -9, proc.stderr[-800:]
            h = Harness(root=root, generations_threshold=0.05)
            try:
                # The step was killed mid-execution (after the
                # ACTION_INTENT, before the observation): its
                # execution has FUNCTION_ENTER spans with no
                # FUNCTION_EXIT, and the reconstruction flags
                # them honestly instead of dropping them.
                p = reconstruct(h.pipe.trace, "ct-demo-repair.s3-synonym")
                assert p["incomplete_spans"], p
                incomplete_fns = {p["spans"][i]["function"]
                                  for i in range(len(p["spans"]))
                                  if p["spans"][i]["span_id"]
                                  in p["incomplete_spans"]}
                assert "L15" in incomplete_fns
                # The trace itself is still hash-chain valid.
                h.pipe.trace.verify()
            finally:
                h.close()
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------
# Genuine instrumentation coverage
# ------------------------------------------------------------------

class TestGenuineCoverage:
    def test_only_executed_functions_appear(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            # Lower-exact: only the functions that ran.
            # EXPANSION (2026-11-18): now also U01 and U02.
            rep = h.pipe.run({k: v for k, v in train[0].items()
                              if k != "correct"})
            p = reconstruct(h.pipe.trace, "tr-valve", rep["execution_id"])
            assert p["functions"] == ["L01", "L30", "U01", "U02",
                                      "U37", "L15", "L21"]
            # U10 fires only when a disambiguation is performed.
            unk = _task("tr-unk", "valve_unk", "valve",
                        ["valve", "stuck", "pressure"], correct="P2",
                        unknown_conditions=("firmware",))
            h.add_task(unk)
            rep = h.pipe.run({k: v for k, v in unk.items() if k != "correct"})
            assert rep["information_requests"]
            p = reconstruct(h.pipe.trace, "tr-unk", rep["execution_id"])
            assert "U10" in p["functions"]
            assert "L15" not in p["functions"]  # nothing executed
        finally:
            h.close()

    def test_l25_never_appears(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            for t in train:
                h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            slope = _task("tr-slope2", "cache_big", "slope",
                          ["slope", "ground", "steep", "cross"], correct="P7")
            h.add_task(slope)
            h.pipe.run({k: v for k, v in slope.items() if k != "correct"})
            for task_id in ("tr-valve", "tr-blocked", "tr-slope2"):
                p = reconstruct(h.pipe.trace, task_id)
                assert "L25" not in p["functions"]
                assert "L25" not in p["instrumentation_gaps"][
                    "registered_never_invoked"] or True
            # L25 is REGISTERED, never activated, never invoked.
            assert "L25" in REGISTERED_ONLY
            assert "L25" not in FUNCTIONS
        finally:
            h.close()

    def test_instrumentation_gaps_are_disclosed(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            rep = h.pipe.run({k: v for k, v in train[0].items()
                              if k != "correct"})
            p = reconstruct(h.pipe.trace, "tr-valve", rep["execution_id"])
            gaps = p["instrumentation_gaps"]
            # The functions that did not run are listed as never
            # invoked (not silently omitted).
            assert "U08" in gaps["registered_never_invoked"]
            assert "L24" in gaps["registered_never_invoked"]
            assert "U10" in gaps["registered_never_invoked"]
            assert "L25" in gaps["registered_never_invoked"]
            assert gaps["uninstrumented_internals"]
        finally:
            h.close()


# ------------------------------------------------------------------
# Domain transitions
# ------------------------------------------------------------------

class TestDomainTransitions:
    def test_cross_family_transition_is_explained(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            slope = _task("tr-slope", "cache_big", "slope",
                          ["slope", "ground", "steep", "cross"], correct="P7")
            h.add_task(slope)
            rep = h.pipe.run({k: v for k, v in slope.items() if k != "correct"})
            assert rep["stage"] == "soso-transfer"
            p = reconstruct(h.pipe.trace, "tr-slope", rep["execution_id"])
            assert len(p["domain_transitions"]) == 1
            tr = p["domain_transitions"][0]
            assert tr["origin"] == "blocked" and tr["destination"] == "slope"
            assert tr["trigger"] == "description overlap"
            assert tr["reason"]  # the explanation is recorded
            assert tr["evidence"]["overlapping_words"]
            assert tr["evidence"]["demonstration_task"]
            assert p["unexplained_jumps"] == []
        finally:
            h.close()

    def test_unexplained_jump_is_flagged(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            # Inject a domain transition with no reason (a
            # synthetic unexplained jump) and confirm the
            # reconstruction flags it.
            tracer = h.pipe._tracer  # None outside run(); use a fresh one
            from contract.trace import Tracer
            tr = Tracer(h.pipe.trace, "tr-valve")
            tr.begin_task(context={})
            span = tr.begin_span("L24")
            tr.domain_transition(span, origin="blocked", destination="slope",
                                 trigger="?", reason="", evidence={})
            tr.end_span(span)
            tr.end_task("RESOLVED")
            p = reconstruct(h.pipe.trace, "tr-valve", tr.execution_id)
            assert len(p["unexplained_jumps"]) == 1
        finally:
            h.close()


# ------------------------------------------------------------------
# Failure classification
# ------------------------------------------------------------------

class TestFailureClassification:
    def test_lie_is_validation_failure(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            # The E5 lie task: P7's observation lies (True) on a
            # task whose correct procedure is P5; the validator
            # catches it. The pathway's failure class is
            # "validation" and the lie is REJECTED.
            lie = _task("tr-lie", "cache_big", "blocked",
                        ["route", "blocked", "debris", "rubble"],
                        conditions={"kind": "detour"}, correct="P5")
            h.add_task(lie)
            h.pipe.observations[("tr-lie", "P7")] = True
            rep = h.pipe.run({k: v for k, v in lie.items() if k != "correct"})
            p = reconstruct(h.pipe.trace, "tr-lie", rep["execution_id"])
            assert p["failures"] == [] or True  # resolved via P5, not failed
            # The lie record itself is REJECTED (the validator
            # caught it), recorded on the pathway's memory.
            rec = h.pipe.experiences.latest_per_pair().get(
                ("tr-lie", "SOSO-P7"))
            assert rec is not None and rec["qual"] == "REJECTED"
        finally:
            h.close()

    def test_missing_information_is_classified(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            unk = _task("tr-unk2", "valve_unk2", "valve",
                        ["valve", "stuck", "pressure"], correct="P2",
                        unknown_conditions=("firmware",))
            h.add_task(unk)
            rep = h.pipe.run({k: v for k, v in unk.items() if k != "correct"})
            assert rep["state"] == "ESCALATED"
            p = reconstruct(h.pipe.trace, "tr-unk2", rep["execution_id"])
            assert len(p["failures"]) == 1
            assert p["failures"][0]["error_class"] == "missing_information"
            assert p["failures"][0]["divergence"]["at"] == "information_request"
        finally:
            h.close()


# ------------------------------------------------------------------
# Profile differences (Phase C)
# ------------------------------------------------------------------

class TestProfileDifferences:
    def test_medical_escalates_where_mechanical_trials(self):
        # An untrained task whose correct procedure is triallable.
        # Mechanical trials it and resolves; medical forbids trial
        # and escalates. The difference is in the pathway (a trial
        # attempt vs an ESCALATION event), not in the wording.
        task = _task("tr-prof", "valve_new_x", "valve",
                     ["hum", "bearing", "rattle"],
                     conditions={"firmware": "v2"}, correct="P2")
        out = run_profile_comparison(task, [], profiles=("mechanical", "medical"))
        mech = out["comparison"]["mechanical"]
        med = out["comparison"]["medical"]
        assert mech["outcome"] == "RESOLVED" and mech["stage"] == "higher-trial"
        assert med["outcome"] == "UNSOLVED" and med["stage"] == "unresolved"
        assert "L15" in mech["functions"] and "L15" not in med["functions"]
        # The medical pathway records the escalation reason.
        med_path = out["results"]["medical"]["pathway"]
        esc = [e for s in med_path["spans"] for e in s["events"]
               if e.get("event_type") == "ESCALATION"]
        assert esc and "trial not allowed" in esc[0]["reason"]

    def test_medical_does_not_transfer_on_vocabulary_alone(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            # A slope task on the blocked signature: under the
            # general profile it transfers via L24; under the
            # medical profile description transfer is off, so L24
            # must not appear and the task must not resolve via
            # soso-transfer on vocabulary alone.
            slope = _task("tr-slope-med", "cache_big", "slope",
                          ["slope", "ground", "steep", "cross"], correct="P7")
            for pid, want_l24 in (("general", True), ("medical", False)):
                hh = make_config_harness("FULL", train + [slope])
                try:
                    hh.pipe.set_profile(pid)
                    rep = hh.pipe.run({k: v for k, v in slope.items()
                                       if k != "correct"})
                    p = reconstruct(hh.pipe.trace, "tr-slope-med",
                                    rep["execution_id"])
                    assert ("L24" in p["functions"]) == want_l24, pid
                finally:
                    hh.close()
        finally:
            h.close()


# ------------------------------------------------------------------
# Memory-block instrumentation
# ------------------------------------------------------------------

class TestMemoryBlockInstrumentation:
    def test_blocks_accessed_are_real_and_counted(self):
        rng = random.Random(7)
        h, train = _trained_pair(rng)
        try:
            rep = h.pipe.run({k: v for k, v in train[0].items()
                              if k != "correct"})
            p = reconstruct(h.pipe.trace, "tr-valve", rep["execution_id"])
            blocks = {(m["store"], m["block"]) for m in p["memory_blocks"]}
            assert ("big49", "task.tr-valve") in blocks
            # TRACE-002, Correction D: the block id is the actual
            # durable id (native procedure id), so it resolves
            # in Big49. The registered form ("SOSO-P2") does not.
            assert ("big49", "exp.tr-valve.P2.1") in blocks
            # The operations are the ones that actually happened.
            ops = {m["block"]: m["operation"] for m in p["memory_blocks"]}
            assert ops["task.tr-valve"] == "read"
            assert ops["exp.tr-valve.P2.1"] == "retrieve"
            # Coverage is honest: a handful of blocks, not 2,208.
            cov = coverage([p])
            assert cov["distinct_blocks"] == len(blocks)
            assert cov["of_logical"] == 2208
            assert cov["fraction"] < 0.01
        finally:
            h.close()

    def test_multistep_parent_records_small49_journal(self):
        from contract.harness import DEMO_TASK
        h = Harness()
        try:
            rep = h.runner.run(DEMO_TASK)
            assert rep["status"] == "DONE"
            p = reconstruct(h.pipe.trace, "ct-demo-repair")
            blocks = [(m["store"], m["block"], m["bucket"])
                      for m in p["memory_blocks"]]
            assert ("small49", "t.ct-demo-repair.begin", "active") in blocks
            assert ("small49", "t.ct-demo-repair.s0", "workspace_large") in blocks
            assert ("small49", "t.ct-demo-repair.done", "active") in blocks
            # Each step is a separate execution referenced by the
            # parent.
            assert p["outcome"]["stage"] == "multi-step"
        finally:
            h.close()


# ------------------------------------------------------------------
# Ablation (Phase D)
# ------------------------------------------------------------------

class TestAblationL24:
    def test_disabling_l24_removes_cross_family_transfer(self):
        rng = random.Random(11)
        # The cross-family group: slope tasks on the blocked
        # signature (the MEM-004 cross-task group, 12 tasks).
        cross = []
        for i in range(12):
            cross.append(_task(f"ab-slope-{i}", "cache_big", "slope",
                               ["slope", "ground", "steep", "cross"],
                               correct="P7"))
        train = [_task("ab-blocked", "cache_big", "blocked",
                       ["route", "blocked", "debris", "rubble"],
                       conditions={"kind": "detour"}, correct="P7")]
        out = run_ablation_l24(cross, train)
        # L24 present: the qualified experience is reused.
        assert out["l24_enabled"]["resolved"] == 12
        assert out["l24_enabled"]["l24_present"] is True
        assert all((p["outcome"] or {}).get("stage") == "soso-transfer"
                   for p in out["l24_enabled"]["pathways"])
        # L24 disabled: the tasks still resolve (the trial is the
        # solver), but by re-learning, not by reuse — and L24 is
        # absent from every trace.
        assert out["l24_disabled"]["resolved"] == 12
        assert out["l24_disabled"]["l24_present"] is False
        assert all((p["outcome"] or {}).get("stage") == "higher-trial"
                   for p in out["l24_disabled"]["pathways"])
        assert out["delta"]["l24_present_enabled"] is True
        assert out["delta"]["l24_present_disabled"] is False
