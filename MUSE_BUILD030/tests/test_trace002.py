"""TRACE-002 — causal contribution, correction, domain
appropriateness, provenance, replay, completeness.

JD's TRACE-001 review + self-audit (2026-11-18). The
experiments themselves live in contract/trace002.py; these
tests pin the measured results and the two genuine bugs
TRACE-002 found and fixed (truthful-failure re-qualification
and the trace memory-reference resolution, Correction D).
"""
from pathlib import Path
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.harness import (Harness, TRAINING, TRANSFER_TASKS,
                              register_demo, DEMO_TASK)
from contract.trace import (ALL_REGISTERED_IDS, IMPLEMENTED_IDS,
                            certify, child_executions, function_states,
                            reconstruct, trace_events, verify_receipt)
from contract.trace002 import (ABLATION_FUNCTIONS, MATCHED_TASKS,
                               run_ablation, run_completeness,
                               run_domain_stress, run_replay,
                               run_wrong_decision, save_replay_inputs,
                               verify_provenance)


def _trained(**kw):
    h = Harness(**kw)
    h.train_baseline()
    return h


def _reopened(**kw):
    """Reopen a root that already holds the training: restore
    the frozen criteria / observations (add_task) WITHOUT
    re-running the training — the store already holds the
    QUALIFIED records, so re-training would resolve via
    lower-exact and fail its own trial assertion."""
    h = Harness(**kw)
    for t in TRAINING:
        h.add_task(t)
    for x in TRANSFER_TASKS.values():
        h.add_task(x)
    return h


@pytest.fixture(scope="module")
def ablation():
    """The full multi-function ablation, run once for the
    module (9 arms x 12 matched tasks)."""
    return run_ablation()


# ---- Correction A: three function states --------------------

def test_function_states_three_levels():
    """REGISTERED (147) / IMPLEMENTED (15) / INVOKED are
    distinct counts; the other 132 registered functions are
    neither implemented nor invoked. (EXPANSION 2026-11-18:
    implemented was 9; the six new functions U01, U02, U03,
    U04, U05, L02 bring it to 15 — 14 brain + 1 admission.)"""
    h = _trained()
    try:
        task = MATCHED_TASKS[0]  # an exact re-presentation
        h.add_task(task)
        rep = h.pipe.run({k: v for k, v in task.items() if k != "correct"})
        assert rep["trace_certified"]
        invoked = reconstruct(h.pipe.trace, task["task_id"],
                              rep["execution_id"])["functions"]
        states = function_states(invoked)
        assert states["registered"] == 147
        assert len(ALL_REGISTERED_IDS) == 147
        assert states["implemented"] == 15
        assert set(IMPLEMENTED_IDS) == {
            "U08", "U09", "U37", "L01", "L15", "L21", "L30", "L24", "U10",
            "U01", "U02", "U03", "U04", "U05", "L02"}
        assert states["invoked"] == len(invoked)
        assert states["registered_only_count"] == 147 - 15
        assert states["implemented_not_invoked"] == (
            sorted(set(IMPLEMENTED_IDS) - set(invoked)))
    finally:
        h.close()


# ---- Priority 2: multi-function ablation -------------------

def test_ablation_control_and_arms(ablation):
    """Control resolves all 12 matched tasks. Disabling U37
    or L15 resolves none (the decision / execution functions
    are on every pathway). Disabling U09 loses transfer and
    trial (it wraps both). Disabling L30 loses exact
    retrieval but the tasks still resolve by transfer/trial.
    Disabling L24 shifts transfer to trial without losing
    a resolution."""
    control = ablation["control"]["summary"]
    assert control["resolved"] == 12 and control["false_resolved"] == 0
    assert control["stages"] == {"lower-exact": 5, "soso-transfer": 5,
                                 "higher-trial": 2}
    assert ablation["U37"]["summary"]["resolved"] == 0
    assert ablation["U37"]["summary"]["escalated"] == 12
    assert ablation["L15"]["summary"]["resolved"] == 0
    assert ablation["L15"]["summary"]["escalated"] == 12
    assert ablation["U09"]["summary"]["resolved"] == 5  # lower-exact only
    assert ablation["U09"]["summary"]["escalated"] == 7
    assert ablation["L30"]["summary"]["resolved"] == 12
    assert ablation["L30"]["summary"]["stages"].get("lower-exact", 0) == 0
    assert ablation["L24"]["summary"]["resolved"] == 12
    assert ablation["L24"]["summary"]["stages"]["soso-transfer"] == 2
    assert ablation["L24"]["summary"]["stages"]["higher-trial"] == 5
    assert ablation["U08"]["summary"]["resolved"] == 12
    assert ablation["U08"]["summary"]["stages"].get("soso-transfer", 0) == 0


def test_l24_three_levels(ablation):
    """Correction B: disabling the L24 FUNCTION, removing the
    information source (descriptions), and disabling the whole
    transfer STRATEGY (U08) are three distinct interventions.
    The function and information-source arms measure
    identically on this workload — the two levels are
    distinguishable ONLY in the trace context (descriptions
    'none' vs disabled_functions ['L24']), an honest finding.
    The strategy arm removes transfer entirely."""
    f = ablation["l24_function"]["summary"]
    i = ablation["l24_info_source"]["summary"]
    s = ablation["l24_strategy"]["summary"]
    assert f["resolved"] == i["resolved"] == 12
    assert f["stages"] == i["stages"]
    assert s["stages"].get("soso-transfer", 0) == 0
    assert s["stages"]["higher-trial"] == 7
    # The trace distinguishes the two levels.
    f_pathway = ablation["l24_function"]["results"][0]["pathway"]
    i_pathway = ablation["l24_info_source"]["results"][0]["pathway"]
    assert f_pathway["disabled_functions"] == ["L24"]
    assert f_pathway["descriptions"] == "default"
    assert i_pathway["disabled_functions"] == []
    assert i_pathway["descriptions"] == "none"


# ---- Priority 3: wrong-decision reconstruction --------------

def test_wrong_decision_reconstruction():
    """A misleading qualified memory (P3 for svc_hung with
    pump tags) is proposed first, fails validation, is
    demoted by a CORRECTION event (QUALIFIED -> INCONCLUSIVE),
    and the correct P4 then resolves the task. The correction
    is per (task, procedure) pair — P3 records for other
    svc_hung tasks are not withdrawn (HARDEN-001 scope-aware
    withdrawal, not procedure-wide)."""
    result = run_wrong_decision()
    assert result["test_report"]["state"] == "RESOLVED"
    assert result["test_report"]["stage"] == "soso-transfer"
    attempts = [(a["procedure"], a["observed"], a["validation"],
                 a["qualification_after"])
                for a in result["test_report"]["attempts"]]
    assert attempts[0] == ("SOSO-P3", False, "PASS", "INCONCLUSIVE")
    assert attempts[1] == ("SOSO-P4", True, "PASS", "QUALIFIED")
    assert len(result["corrections"]) == 1
    c = result["corrections"][0]
    assert c["procedure"] == "SOSO-P3"
    assert c["previous_qualification"] == "QUALIFIED"
    assert c["new_qualification"] == "INCONCLUSIVE"
    assert c["evidence"]["demonstration_task"] == "ct-train-sensor-a"
    assert c["evidence"]["demonstration_signature"] == "svc_hung"
    assert c["evidence"]["observed"] is False
    assert c["evidence"]["validation"] == "PASS"
    # The trace records the wrong candidate, the validation,
    # the correction, and the revised candidate in order.
    funcs = result["pathway"]["functions"]
    for fid in ("L01", "L30", "U08", "U09", "U37", "L15", "L21"):
        assert fid in funcs
    # The misleading source is still trusted for its own
    # demonstration task (per-pair correction, not a
    # procedure-wide withdrawal).
    assert any(t == "ct-train-sensor-a" and q == "QUALIFIED"
               for t, q in result["trusted_after"])


def test_truthful_failure_demotes_not_requalifies():
    """The TRACE-002 bug fix: a qualified procedure that
    truthfully fails within its conditions drops to
    INCONCLUSIVE (it does not get re-qualified on a PASS
    validation alone — the validator returns PASS for a
    wrong procedure that truthfully fails)."""
    result = run_wrong_decision()
    assert result["test_report"]["attempts"][0]["qualification_after"] == (
        "INCONCLUSIVE")


# ---- Priority 4: domain-transition stress -------------------

def test_domain_stress():
    result = run_domain_stress()
    legit = result["legitimate"]
    assert legit["report"]["state"] == "RESOLVED"
    assert legit["report"]["stage"] == "soso-transfer"
    assert legit["transition_helped"] is True
    assert [a["procedure"] for a in legit["report"]["attempts"]] == ["SOSO-P7"]
    assert len(legit["transitions"]) == 1
    t = legit["transitions"][0]
    assert t["origin"] == "blocked" and t["destination"] == "slope"
    assert t["trigger"] == "description overlap"
    assert "ground" in t["reason"]
    misleading = result["misleading_overlap"]
    assert misleading["report"]["state"] == "RESOLVED"
    assert misleading["report"]["stage"] == "higher-trial"
    assert misleading["transition_helped"] is False
    attempts = [a["procedure"] for a in misleading["report"]["attempts"]]
    assert attempts[0] == "SOSO-P7"  # the trap, tried first
    assert attempts[-1] == "SOSO-P6"  # the correct one
    detour = result["inappropriate_detour"]
    assert detour["report"]["state"] == "RESOLVED"
    assert detour["report"]["stage"] == "higher-trial"
    assert detour["transition_helped"] is False
    assert [a["procedure"] for a in detour["report"]["attempts"]] == [
        "SOSO-P4", "SOSO-P3"]


# ---- Priority 1: provenance (Correction D) ------------------

def test_provenance_exact_task():
    """Every trace memory reference resolves to the same
    durable record it names. The trace's experience-block ids
    use the durable native id (exp.<task>.P2.1), not the
    registered id — the Correction D fix."""
    h = _trained()
    try:
        task = {"task_id": "pv-valve", "signature": "disk_full",
                "family": "valve",
                "tags": ["valve", "firmware_v2", "stuck", "flow"],
                "conditions": {"firmware": "v2"},
                "requested_operation": "repair",
                "provenance": ("int:trace-002",), "correct": "P2"}
        h.add_task(task)
        rep = h.pipe.run({k: v for k, v in task.items() if k != "correct"})
        assert rep["state"] == "RESOLVED" and rep["stage"] == "lower-exact"
        pathway = reconstruct(h.pipe.trace, "pv-valve", rep["execution_id"])
        results = verify_provenance(h, pathway, task)
        assert results, "no memory references in the pathway"
        assert all(r["resolved"] and r["matches"] for r in results)
        identity = [r for r in results if r["block"] == "task.pv-valve"]
        assert len(identity) == 1
        assert identity[0]["detail"]["digest"] == (
            identity[0]["detail"]["expected_digest"])
        exp_blocks = [r["block"] for r in results
                      if r["store"] == "big49" and r["block"].startswith("exp.")]
        assert exp_blocks, "no experience blocks in the pathway"
        assert any(b.endswith(".P2.1") for b in exp_blocks)
        assert not any("SOSO-P2" in b for b in exp_blocks)
    finally:
        h.close()


def test_provenance_multistep_after_rollover_and_restart():
    """All 7 Small49 journal blocks (begin + 5 steps + done)
    resolve and match after a generation rollover and a
    fresh-process restart."""
    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, "root")
    h = _trained(root=root, generations_threshold=0.05)
    try:
        register_demo(h)
        rep = h.runner.run(DEMO_TASK)
        assert rep["status"] == "DONE" and rep["result"] == "PARTIAL"
    finally:
        h.close()
    h = _reopened(root=root, generations_threshold=0.05)
    try:
        parent = reconstruct(h.pipe.trace, "ct-demo-repair")
        results = verify_provenance(h, parent)
        journal = [r for r in results if r["store"] == "small49"]
        assert len(journal) == 7
        assert all(r["resolved"] and r["matches"] for r in journal)
        assert [r["block"] for r in journal] == [
            "t.ct-demo-repair.begin",
            "t.ct-demo-repair.s0", "t.ct-demo-repair.s1",
            "t.ct-demo-repair.s2", "t.ct-demo-repair.s3",
            "t.ct-demo-repair.s4", "t.ct-demo-repair.done"]
    finally:
        h.close()


def test_provenance_threshold_mismatch_fails_explicitly():
    """HARDEN-001 fix 4: reopening a root with a different
    generations threshold fails explicitly (it does not
    silently use the wrong threshold)."""
    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, "root")
    h = _trained(root=root, generations_threshold=0.05)
    h.close()
    with pytest.raises(ValueError):
        _trained(root=root, generations_threshold=0.8)


# ---- Priority 1: independent replay + receipt --------------

def test_replay_receipt_and_reconstruction():
    """A fresh process reopens the ledger, verifies the
    independently stored receipt (the receipt commits to
    the ledger prefix — opening the ledger appends a
    SYSTEM_OPEN event, so the current head moves; comparing
    the head would fail on every reopen), reconstructs each
    step's pathway, and matches the committed reports."""
    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, "root")
    h = _trained(root=root)
    try:
        register_demo(h)
        rep = h.runner.run(DEMO_TASK)
        assert rep["status"] == "DONE"
        tasks = {}
        for s in rep["steps"]:
            tid = f"ct-demo-repair.{s['step_id']}"
            evs = trace_events(h.pipe.trace, tid)
            begins = [e for _, e in evs if e["event_type"] == "TASK_BEGIN"]
            ends = [e for _, e in evs if e["event_type"] == "TASK_END"]
            tasks[tid] = {
                "state": "RESOLVED" if s["outcome"] == "SUCCESS" else "UNSOLVED",
                "selected": s["procedure"] or None,
                "stage": (ends[0]["data"].get("stage") if ends else None),
                "execution_id": begins[0]["execution_id"] if begins else None}
        saved = save_replay_inputs(
            h, tasks, os.path.join(tmp, "committed.json"),
            os.path.join(tmp, "receipt.json"))
        saved["committed"]["parent_execution_id"] = rep["execution_id"]
        with open(os.path.join(tmp, "committed.json"), "w") as f:
            json.dump(saved["committed"], f, indent=2, sort_keys=True)
    finally:
        h.close()
    result = run_replay(root, os.path.join(tmp, "receipt.json"),
                        os.path.join(tmp, "committed.json"))
    assert result["receipt_ok"], result["receipt"]
    assert result["match"], result["mismatches"]
    assert len(result["child_executions"]) == 5
    assert all(r["certified"] for r in result["reconstructed"].values())


def test_receipt_detects_tampering():
    """The receipt is a commitment: changing the stored
    head hash makes verification fail."""
    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, "root")
    h = _trained(root=root)
    try:
        register_demo(h)
        h.runner.run(DEMO_TASK)
        receipt_path = os.path.join(tmp, "receipt.json")
        from contract.trace import write_receipt
        write_receipt(h.pipe.trace, receipt_path)
        with open(receipt_path) as f:
            receipt = json.load(f)
        receipt["head_hash"] = "0" * 64
        with open(receipt_path, "w") as f:
            json.dump(receipt, f)
        result = verify_receipt(h.pipe.trace, receipt_path)
        assert not result["ok"]
        assert "hash" in result["mismatch"]
    finally:
        h.close()


# ---- Priority 1: trace completeness -------------------------

def test_completeness_crash_boundaries():
    """A SIGKILL at each of the five fault boundaries leaves
    an accurately incomplete trace: the open spans are named,
    certification fails (never silently complete), no IOU is
    opened (IOUs are written only on unresolved completion),
    and the ACTION_INTENT / TASK_DONE pattern gives the
    correct recovery classification."""
    task = {"task_id": "cp-valve", "signature": "disk_full",
            "family": "valve",
            "tags": ["valve", "firmware_v2", "stuck", "flow"],
            "conditions": {"firmware": "v2"},
            "requested_operation": "repair",
            "provenance": ("int:trace-002",), "correct": "P2"}
    result = run_completeness(task)
    # after_decide: killed inside the U37 span, before admission.
    d = result["after_decide"]
    assert d["incomplete_functions"] == ["L30", "U37"]
    assert not d["certified"] and not d["has_iou"] and not d["ambiguous_intent"]
    # after_authorization: admitted, killed before the intent.
    a = result["after_authorization"]
    assert "L15" in a["incomplete_functions"]
    assert not a["certified"] and not a["has_iou"] and not a["ambiguous_intent"]
    # after_intent: the intent exists with no result -> AMBIGUOUS.
    i = result["after_intent"]
    assert i["ambiguous_intent"] and not i["certified"] and not i["has_iou"]
    # after_validation: validated, killed before the record.
    v = result["after_validation"]
    assert v["ambiguous_intent"] and not v["certified"]
    assert "L21" not in v["incomplete_functions"]  # no L21 span yet
    # after_record: recorded, killed before the task ends. The
    # RECORDED event proves the action executed, so the
    # Contract 002 AMBIGUOUS classification here is
    # conservative (no TASK_DONE), not a missing execution.
    r = result["after_record"]
    assert r["ambiguous_intent"] and not r["certified"]
    assert "L21" in r["incomplete_functions"]


# ---- Correction C: recording modes --------------------------

def test_recording_modes_recorded_and_never_silent():
    """The recording mode is recorded in TASK_BEGIN and in
    the report, and a complete run certifies in either mode.
    The certification check is mode-agnostic: an incomplete
    trajectory is never certified, in either mode — the mode
    changes what the operator is told, not whether a gap is
    hidden (the completeness test above is the best_effort
    case; certify() is the same function in both modes)."""
    for mode in ("best_effort", "evidence_grade"):
        h = _trained(recording_mode=mode)
        try:
            task = {"task_id": "md-valve", "signature": "disk_full",
                    "family": "valve",
                    "tags": ["valve", "firmware_v2", "stuck", "flow"],
                    "conditions": {"firmware": "v2"},
                    "requested_operation": "repair",
                    "provenance": ("int:trace-002",), "correct": "P2"}
            h.add_task(task)
            rep = h.pipe.run({k: v for k, v in task.items()
                              if k != "correct"})
            assert rep["recording_mode"] == mode
            assert rep["trace_certified"]
            evs = trace_events(h.pipe.trace, "md-valve")
            begins = [e for _, e in evs if e["event_type"] == "TASK_BEGIN"]
            assert begins[0]["data"]["recording_mode"] == mode
        finally:
            h.close()


# ---- Correction E: parent/child linkage ---------------------

def test_parent_child_linkage():
    """Each step execution carries the parent's execution_id
    in its TASK_BEGIN, and the ledger returns exactly the
    five child executions for that parent — no task-name
    parsing."""
    h = _trained()
    try:
        register_demo(h)
        rep = h.runner.run(DEMO_TASK)
        assert rep["status"] == "DONE"
        children = child_executions(h.pipe.trace, rep["execution_id"])
        assert len(children) == 5
        assert rep["trace_certified"]
        for s in rep["steps"]:
            tid = f"ct-demo-repair.{s['step_id']}"
            evs = trace_events(h.pipe.trace, tid)
            begins = [e for _, e in evs if e["event_type"] == "TASK_BEGIN"]
            assert begins[0]["data"]["parent_execution_id"] == rep["execution_id"]
        # The parent's reconstruction carries the parent's
        # execution id (None — it is the root) and the ledger
        # returns its five children.
        parent = reconstruct(h.pipe.trace, "ct-demo-repair",
                             rep["execution_id"])
        assert parent["parent_execution_id"] is None
        assert parent["outcome"]["outcome"] == "DONE"
        assert [c["task_id"] for c in children] == [
            f"ct-demo-repair.{s['step_id']}" for s in rep["steps"]]
    finally:
        h.close()


# ---- Priority 5: integrated continuous learning -------------

def test_integrated_continuous_learning_demo():
    """The demo script runs end to end in a fresh process:
    Higher investigation -> validated storage -> Lower reuse
    -> Small49 multi-step context + IOU -> fresh-process
    continuation with a verified receipt and a resumed
    multi-step task."""
    import subprocess
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    demo = os.path.join(base, "demo_trace002.py")
    env = dict(os.environ)
    env["PYTHONPATH"] = base
    proc = subprocess.run(
        [sys.executable,
         demo], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "1. investigation: RESOLVED via higher-trial" in proc.stdout
    assert "2. qualification: RESOLVED via soso-transfer" in proc.stdout
    assert "3. reuse: RESOLVED via lower-exact" in proc.stdout
    assert "4. multi-step: DONE/PARTIAL" in proc.stdout
    assert "6. fresh-process continuation: receipt_ok=True" in proc.stdout
    assert "slope task after restart: RESOLVED via lower-exact" in proc.stdout
    # The demo's trace export is a sequence-sorted list.
    with open(os.path.join(base, "demo_trace002_trace.json")) as f:
        exported = json.load(f)
    assert isinstance(exported, list) and len(exported) > 100
    seqs = [e["sequence"] for e in exported]
    assert seqs == sorted(seqs)
    assert len({e["event_id"] for e in exported}) == len(exported)
