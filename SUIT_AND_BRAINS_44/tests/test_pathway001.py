"""PATHWAY-001 (experiment half) — parity harness, 20 frozen cases,
matched four-arm experiment.

Builder-reported results: every number asserted here comes from
this file's own runs of the experiment code in this repo. Nothing
here is independently verified — an independent re-run by
someone else, from the source, is still outstanding.

The four-arm numbers below are the MEASURED outcome of
run_pathway_experiment(), frozen after the standalone run
(2026-11-18): A 0, B 10, C 15, D 15 on the frozen endpoint.
They are asserted as measured truth, not as a prediction — if
the implementation changes, the test fails and the numbers are
re-measured, never silently adjusted.
"""
import copy
import json
import tempfile
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.evidence import FrozenEvidenceSource
from contract.harness import Harness, TRAINING, TRANSFER_TASKS
from contract.pathway_experiment import (
    CASES, LEARNING_TASKS, parity_check, run_pathway_experiment,
    score_case)
from contract.registry import procedure_identity
from contract.trace import trace_events
from contract.trace002 import MATCHED_TASKS
from contract.trace003 import DOMAIN_TASKS

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _check(h, task):
    """Run one task and parity-check it immediately (latest_per_pair
    is read right after the run, so the attempt's committed
    record is the one the check reads). Returns (report, parity)."""
    task_dict = {k: v for k, v in task.items() if k != "correct"}
    rep = h.pipe.run(task_dict)
    latest = h.pipe.experiences.latest_per_pair()
    parity = parity_check(
        rep, h.pipe.trace, task_dict=task_dict, policy=h.policy,
        trial_candidates_fn=lambda t, _p=h.pipe: _p._trial_candidates(t),
        latest_per_pair=latest)
    return rep, parity


def _assert_clean(parity):
    assert parity["ok"], parity["defects"]
    assert parity["defects"] == []
    assert parity["scope_limits"] == []
    assert parity["compared"], "checker compared nothing"


# ------------------------------------------------------------------
# Parity: zero defects across a spread of existing fixtures
# ------------------------------------------------------------------

def test_parity_zero_defects_baseline_fixtures():
    """The checker is validated FIRST on baseline arm-A runs over
    existing fixtures — training tasks (re-presented), transfer
    tasks, matched TRACE-002 tasks and domain TRACE-003 tasks —
    before it gates anything. Zero defects on all of them."""
    fixtures = ([dict(t) for t in TRAINING]
                + [dict(x) for x in TRANSFER_TASKS.values()]
                + [dict(t) for t in MATCHED_TASKS[:4]]
                + [dict(t) for t in DOMAIN_TASKS["mechanical"]]
                + [dict(t) for t in DOMAIN_TASKS["general"][:1]])
    h = Harness(tasks=fixtures)
    try:
        h.train_baseline()
        for task in fixtures:
            _rep, parity = _check(h, task)
            _assert_clean(parity)
    finally:
        h.close()


def _evidence_task(task_id, correct, value):
    return {"task_id": task_id, "signature": "disk_full",
            "family": "valve",
            "tags": ["valve", "reseat", "stuck", "pressure"],
            "conditions": {},
            "unknown_conditions": ("firmware",),
            "requested_operation": "repair",
            "provenance": ("int:pathway-001",), "correct": correct,
            "evidence_value": value}


def test_parity_zero_defects_evidence_runs():
    """Zero defects on answered evidence runs: a resolving run
    (v1 -> P1) and a justified-abstention run (v3 contradicts
    both pointed-at procedures). The expectation/observation
    re-derivation and the pointed_blocks re-derivation are both
    exercised here."""
    tasks = [_evidence_task("pw-ev-parity-res", "P1", "v1"),
             _evidence_task("pw-ev-parity-abs", None, "v3")]
    table = {(t["task_id"], "firmware"): t["evidence_value"]
             for t in tasks}
    h = Harness(tasks=tasks,
                evidence_source=FrozenEvidenceSource(table))
    try:
        h.train_baseline()
        rep_res, parity_res = _check(h, tasks[0])
        assert rep_res["state"] == "RESOLVED"
        assert rep_res["selected"] == "SOSO-P1"
        _assert_clean(parity_res)
        rep_abs, parity_abs = _check(h, tasks[1])
        assert rep_abs["state"] == "ESCALATED"
        assert rep_abs["information_requests"][0]["outcome"] == \
            "justified_abstention"
        _assert_clean(parity_abs)
    finally:
        h.close()


def test_parity_tampered_report_is_caught():
    """A hand-tampered copy of a report — one rank changed, one
    validation flipped — is caught as a defect. The checker
    compares against the trace, not against the report."""
    h = Harness(tasks=[dict(t) for t in MATCHED_TASKS[:2]])
    try:
        h.train_baseline()
        task = MATCHED_TASKS[0]
        rep, parity = _check(h, task)
        _assert_clean(parity)
        tampered = copy.deepcopy(rep)
        assert tampered["proposed"], "fixture run proposed nothing"
        tampered["proposed"][0]["rank"] = 999
        bad = parity_check(
            tampered, h.pipe.trace,
            task_dict={k: v for k, v in task.items() if k != "correct"},
            policy=h.policy,
            trial_candidates_fn=lambda t, _p=h.pipe: (
                _p._trial_candidates(t)),
            latest_per_pair=h.pipe.experiences.latest_per_pair())
        assert not bad["ok"]
        assert any("proposed" in d for d in bad["defects"]), bad["defects"]
        tampered2 = copy.deepcopy(rep)
        assert tampered2["attempts"], "fixture run attempted nothing"
        tampered2["attempts"][0]["validation"] = (
            "FAIL" if tampered2["attempts"][0]["validation"] != "FAIL"
            else "PASS")
        bad2 = parity_check(
            tampered2, h.pipe.trace,
            task_dict={k: v for k, v in task.items() if k != "correct"},
            policy=h.policy,
            trial_candidates_fn=lambda t, _p=h.pipe: (
                _p._trial_candidates(t)),
            latest_per_pair=h.pipe.experiences.latest_per_pair())
        assert not bad2["ok"]
        assert any("attempts" in d for d in bad2["defects"]), bad2["defects"]
    finally:
        h.close()


# ------------------------------------------------------------------
# The matched four-arm experiment (measured numbers, frozen)
# ------------------------------------------------------------------

_EXPERIMENT = None


def _experiment():
    global _EXPERIMENT
    if _EXPERIMENT is None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "pathway001_results.json")
            _EXPERIMENT = run_pathway_experiment(save_path=path)
            with open(path) as stream:
                assert json.load(stream) == _EXPERIMENT
    return _EXPERIMENT


def test_four_arm_experiment_measured_numbers():
    """The frozen endpoint (design §5): correct evidence-grounded
    resolution or justified abstention, over the 20 cases.
    Measured (standalone run, 2026-11-18): A 0, B 10, C 15,
    D 15. C gains exactly the 10 different-answer tasks over B
    and loses the 5 abstentions to the known replay design gap
    (replayed v3 yields UNSOLVED, not justified_abstention —
    not endpoint-counted, not a false success)."""
    r = _experiment()
    assert len(CASES) == 20
    assert len(LEARNING_TASKS) == 20
    assert r["store"]["n_pathways"] == 20
    assert all(v["state"] == "RESOLVED" for v in r["learning"].values())
    summaries = {arm: r["arms"][arm]["summary"] for arm in "ABCD"}
    assert summaries["A"]["endpoint"] == 0
    assert summaries["B"]["endpoint"] == 10
    assert summaries["C"]["endpoint"] == 15
    assert summaries["D"]["endpoint"] == 15
    assert summaries["A"]["endpoint_resolutions"] == 0
    assert summaries["B"]["endpoint_resolutions"] == 5
    assert summaries["C"]["endpoint_resolutions"] == 15
    assert summaries["D"]["endpoint_resolutions"] == 15
    assert summaries["A"]["endpoint_abstentions"] == 0
    assert summaries["B"]["endpoint_abstentions"] == 5
    assert summaries["C"]["endpoint_abstentions"] == 0
    assert summaries["D"]["endpoint_abstentions"] == 0
    # Raw correct resolutions (no evidence requirement): the
    # same counts here — no resolution without evidence occurs.
    assert summaries["A"]["raw_correct_resolutions"] == 0
    assert summaries["B"]["raw_correct_resolutions"] == 5
    assert summaries["C"]["raw_correct_resolutions"] == 15
    assert summaries["D"]["raw_correct_resolutions"] == 15
    # Secondary: wrong attempts and evidence acquisition cost.
    assert summaries["A"]["wrong_attempts"] == 80
    assert summaries["B"]["wrong_attempts"] == 80
    assert summaries["C"]["wrong_attempts"] == 58
    assert summaries["D"]["wrong_attempts"] == 58
    assert summaries["A"]["evidence_requests_total"] == 10
    assert summaries["B"]["evidence_requests_total"] == 10
    assert summaries["C"]["evidence_requests_total"] == 20
    assert summaries["D"]["evidence_requests_total"] == 20
    # Nothing excluded, nothing defected.
    for arm in "ABCD":
        assert summaries[arm]["n_scored"] == 20
        assert summaries[arm]["parity_blocked"] == []
        assert summaries[arm]["case_defects"] == {}
    # The replay design gap: 5 runs per replay arm.
    assert summaries["A"]["replay_contradiction_unsolved"] == 0
    assert summaries["B"]["replay_contradiction_unsolved"] == 0
    assert summaries["C"]["replay_contradiction_unsolved"] == 5
    assert summaries["D"]["replay_contradiction_unsolved"] == 5
    # Per-case behavior vs intended class.
    for case in CASES:
        tid = case["task_id"]
        a = r["arms"]["A"]["cases"][tid]
        b = r["arms"]["B"]["cases"][tid]
        c = r["arms"]["C"]["cases"][tid]
        if case["kind"] == "different_answer":
            assert a["state"] == "UNSOLVED", tid
            assert b["state"] == "UNSOLVED", tid
            assert b["information_requests"] == [], tid
            assert c["state"] == "RESOLVED", tid
            assert c["selected_native"] == case["correct"], tid
            assert c["endpoint_counts"] is True, tid
        else:
            assert a["state"] == "ESCALATED", tid
            assert a["n_attempts"] == 0, tid
            if case["correct"] is not None:
                assert b["state"] == "RESOLVED", tid
                assert b["selected_native"] == case["correct"], tid
                assert c["state"] == "RESOLVED", tid
            else:
                assert b["endpoint_kind"] == "abstention", tid
                assert c["state"] == "UNSOLVED", tid
                assert c["endpoint_counts"] is False, tid


def test_four_arm_shuffle_is_a_genuine_assignment():
    """Arm D's association is a genuine shuffle, not a copy of
    C: every learning pathway is offered exactly once, every
    association names a DIFFERENT task's pathway, and the
    assignment differs from C's earliest-pick replay (arm C
    always replays the earliest pathway in the group)."""
    r = _experiment()
    assoc = r["arms"]["D"]["association"]
    assert assoc is not None
    assert sorted(assoc) == sorted(c["task_id"] for c in CASES)
    pathway_ids = [rec["pathway_id"] for rec in r["store"]["records"]]
    assert sorted(assoc.values()) == sorted(pathway_ids)
    source_of = {rec["pathway_id"]: rec["source_task_id"]
                 for rec in r["store"]["records"]}
    for task_id, pathway_id in assoc.items():
        assert source_of[pathway_id] != task_id
    # C replays the earliest pathway per group (store.find tie
    # rule); D must differ from that on at least one task.
    by_group = {}
    for rec in r["store"]["records"]:
        by_group.setdefault((rec["signature"], rec["family"]), []).append(
            rec["pathway_id"])
    earliest = {tid: min(by_group[(c["signature"], c["family"])])
                for tid, c in ((c["task_id"], c) for c in CASES)}
    assert any(assoc[tid] != earliest[tid] for tid in assoc)


def test_integrity_no_false_success_no_prohibited_execution():
    """Mandatory integrity (design §5): no false success and no
    prohibited execution in any arm, whatever the endpoint
    says. A single violation fails the arm's result."""
    r = _experiment()
    for arm in "ABCD":
        summary = r["arms"][arm]["summary"]
        assert summary["false_successes"] == 0, arm
        assert summary["prohibited_executions"] == 0, arm
        assert summary["integrity_violations"] == [], arm
        assert summary["integrity_ok"] is True, arm
        for tid, case in r["arms"][arm]["cases"].items():
            assert case["false_success"] is False, (arm, tid)
            assert case["integrity_ok"] is True, (arm, tid)
            if case["correct"] is None:
                assert case["state"] != "RESOLVED", (arm, tid)


def test_parity_defect_blocks_scoring_unit():
    """The gating function, unit-tested: a case whose parity
    check failed is excluded from scoring (endpoint not
    counted, excluded_reason names the parity defect) even
    though the live report resolved correctly; the same case
    with a clean parity check scores. The whole experiment is
    NOT re-run for this."""
    case = {"task_id": "pw-me-a-res", "kind": "missing_evidence",
            "group": "me_valve_a", "correct": "P1",
            "evidence_value": "v1"}
    report = {"task_id": "pw-me-a-res", "state": "RESOLVED",
              "stage": "higher-trial", "selected": "SOSO-P1",
              "attempts": [{"procedure": "SOSO-P1"}],
              "information_requests": [{"answered": True,
                                        "observation": {"firmware": "v1"},
                                        "outcome": "resumed"}]}
    events = [(1, {"event_type": "EVIDENCE_EXPECTATION",
                   "data": {"expectation_id": "ev-0001"}}),
              (2, {"event_type": "EVIDENCE_OBSERVED",
                   "data": {"expectation_id": "ev-0001"}}),
              (3, {"event_type": "TASK_END",
                   "data": {"outcome": "RESOLVED"}})]
    clean_parity = {"ok": True, "defects": [], "compared": ["proposed"],
                    "scope_limits": []}
    clean_integrity = {"ok": True, "violations": []}
    scored = score_case(case, report, events, clean_parity, clean_integrity)
    assert scored["scored"] is True
    assert scored["endpoint_counts"] is True
    bad_parity = {"ok": False, "defects": ["proposed mismatch: tampered"],
                  "compared": ["proposed"], "scope_limits": []}
    blocked = score_case(case, report, events, bad_parity, clean_integrity)
    assert blocked["scored"] is False
    assert blocked["endpoint_counts"] is False
    assert "parity defect" in (blocked["excluded_reason"] or "")
    assert blocked["parity_defects"] == ["proposed mismatch: tampered"]
    # The raw resolution is still visible in the record — the
    # gate excludes it from scoring, it does not erase it.
    assert blocked["raw_correct_resolution"] is True
