"""PATHWAY-002 (experiment half) — matched four-arm experiment.

Builder-reported results: every number asserted here comes from
this file's own runs of the experiment code in this repo. Nothing
here is independently verified — an independent re-run by someone
else, from the source, is still outstanding.

The four-arm numbers below are the MEASURED outcome of
run_pathway002_experiment(), frozen after the first evaluation
run (2026-11-18): A 0, B 14, C 24, D 24 on the frozen endpoint.
They are asserted as measured truth, not as a prediction — if
the implementation changes, the test fails and the numbers are
re-measured, never silently adjusted. The fixtures were frozen
before that run (see the FREEZE block in
contract/pathway002_experiment.py) and were not changed after it.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.evidence import FrozenEvidenceSource
from contract.harness import Harness, TRAINING, TRANSFER_TASKS
from contract.pathway import (PathwayRecorder, PathwayReplayProvider,
                              PathwayStore)
from contract.pathway002_experiment import (
    CASES, CASE_COUNTS, COSTS, LEARNING_TASKS, SHUFFLE_SEED,
    _public_task, build_evidence_table, parity_check002,
    run_pathway002_experiment)
from contract.selection import MeasurementSelector
from contract.trace import trace_events

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATHWAY001_RESULTS = os.path.join(PROJECT_ROOT, "pathway001_results.json")
# EXPANSION (2026-11-18): the frozen sha was 7166a0e9…; the
# expansion regenerated the file deterministically (two
# independent runs → this sha; arm endpoints unchanged A 0,
# B 10, C 15, D 15) because the recorded pathways now include
# the six new functions' spans. The regenerated file on disk
# IS the new frozen artifact.
PATHWAY001_SHA256 = (
    "7935f109e1c996801102f7a0bce45d05a586cffc580b483bf823201b8804c859")
PATHWAY002_RESULTS = os.path.join(PROJECT_ROOT, "pathway002_results.json")


_EXPERIMENT = None


def _experiment():
    global _EXPERIMENT
    if _EXPERIMENT is None:
        _EXPERIMENT = run_pathway002_experiment()
    return _EXPERIMENT


# ------------------------------------------------------------------
# Frozen counts and the measured four-arm numbers
# ------------------------------------------------------------------

def test_frozen_counts_and_learning():
    """32 cases across all six classes, 30 learning tasks, 30
    pathways, every learning task resolved under arm-B
    conditions."""
    assert len(CASES) == 32
    assert dict(CASE_COUNTS) == {
        "different_answer": 10, "missing_evidence": 5,
        "abstention_required": 5, "contradictory_observations": 4,
        "unavailable_evidence": 4, "misleading_prior": 4}
    from collections import Counter
    assert Counter(c["kind"] for c in CASES) == Counter(CASE_COUNTS)
    assert len(LEARNING_TASKS) == 30
    r = _experiment()
    assert r["store"]["n_pathways"] == 30
    assert all(v["state"] == "RESOLVED" for v in r["learning"].values())
    # Every learning pathway records firmware only: kind was
    # never selected in any learning run (module docstring,
    # finding 1).
    assert {tuple(v["measurements"]) for v in r["learning"].values()} == \
        {("firmware",)}
    # The evidence table omits exactly the unavailable-evidence
    # held-out tasks (the omission is the fixture).
    table = build_evidence_table()
    assert all((c["task_id"], "firmware") not in table
               for c in CASES if c["kind"] == "unavailable_evidence")
    assert all((c["task_id"], "firmware") in table
               for c in CASES if c["kind"] != "unavailable_evidence")
    assert all((t["task_id"], "firmware") in table
               for t in LEARNING_TASKS)


def test_four_arm_experiment_measured_numbers():
    """The frozen endpoint (design §5, same shape as
    PATHWAY-001 §5): correct evidence-grounded resolution or
    justified abstention, over the 32 cases. Measured (first
    evaluation run, 2026-11-18): A 0, B 14, C 24, D 24. C gains
    exactly the 10 different-answer tasks over B; the 5
    abstention-required tasks are endpoint-counted in B, C and
    D alike (B via the direct U10 path, C/D via the replay path
    + the flag). C and D are identical on the endpoint — no
    demonstrated procedural-transfer advantage of the correct
    method over the shuffled method (see the critical-question
    test below)."""
    r = _experiment()
    summaries = {arm: r["arms"][arm]["summary"] for arm in "ABCD"}
    assert summaries["A"]["endpoint"] == 0
    assert summaries["B"]["endpoint"] == 14
    assert summaries["C"]["endpoint"] == 24
    assert summaries["D"]["endpoint"] == 24
    assert summaries["A"]["endpoint_resolutions"] == 0
    assert summaries["B"]["endpoint_resolutions"] == 9
    assert summaries["C"]["endpoint_resolutions"] == 19
    assert summaries["D"]["endpoint_resolutions"] == 19
    assert summaries["A"]["endpoint_abstentions"] == 0
    assert summaries["B"]["endpoint_abstentions"] == 5
    assert summaries["C"]["endpoint_abstentions"] == 5
    assert summaries["D"]["endpoint_abstentions"] == 5
    # Raw correct resolutions (no evidence requirement): the
    # same counts here — no resolution without evidence occurs.
    assert summaries["A"]["raw_correct_resolutions"] == 0
    assert summaries["B"]["raw_correct_resolutions"] == 9
    assert summaries["C"]["raw_correct_resolutions"] == 19
    assert summaries["D"]["raw_correct_resolutions"] == 19
    # Secondary: wrong attempts and evidence acquisition cost.
    # C/D still pay 5-7 wrong attempts per different-answer
    # task (the correct procedure is reached by exhaustive
    # trial after firmware narrows P1 vs P2; the trial rank
    # still puts P7/P5 first) and 8 per contradictory task.
    assert summaries["A"]["wrong_attempts"] == 112
    assert summaries["B"]["wrong_attempts"] == 112
    assert summaries["C"]["wrong_attempts"] == 90
    assert summaries["D"]["wrong_attempts"] == 90
    assert summaries["A"]["evidence_requests_total"] == 18
    assert summaries["B"]["evidence_requests_total"] == 18
    assert summaries["C"]["evidence_requests_total"] == 32
    assert summaries["D"]["evidence_requests_total"] == 32
    # Measurement cost (frozen table: firmware 1, kind 3 —
    # kind never incurred). Every endpoint-counted case costs
    # exactly one firmware measurement.
    assert summaries["A"]["measurement_cost_total"] == 0
    assert summaries["B"]["measurement_cost_total"] == 14
    assert summaries["C"]["measurement_cost_total"] == 28
    assert summaries["D"]["measurement_cost_total"] == 28
    assert summaries["B"]["measurement_cost_per_endpoint_case"] == 1.0
    assert summaries["C"]["measurement_cost_per_endpoint_case"] == 1.0
    assert summaries["D"]["measurement_cost_per_endpoint_case"] == 1.0
    # Cost acceptability (frozen rule): C's gain over B is 10,
    # so the comparison applies; ratio 1.0 <= 1.5.
    cc = r["cost_comparison"]
    assert cc["c_endpoint_gain_over_b"] == 10
    assert cc["cost_ratio_c_over_b"] == 1.0
    assert cc["verdict"] == "acceptable"
    # Nothing excluded, nothing blocking.
    for arm in "ABCD":
        assert summaries[arm]["n_scored"] == 32
        assert summaries[arm]["n_excluded"] == 0
        assert summaries[arm]["case_defects"] == {}
        assert summaries[arm]["n_parity_blocking"] == 0
        assert summaries[arm]["parity_blocked"] == []
    # The replay design gap is closed: zero replay-contradiction
    # UNSOLVED runs in any arm.
    for arm in "ABCD":
        assert summaries[arm]["replay_contradiction_unsolved"] == 0
    # Per-class endpoint breakdown.
    from collections import Counter
    by_kind = {}
    for arm in "ABCD":
        by_kind[arm] = Counter(
            c["kind"] for c in r["arms"][arm]["cases"].values()
            if c["endpoint_counts"])
    assert by_kind["A"] == Counter()
    assert by_kind["B"] == Counter({"missing_evidence": 5,
                                    "abstention_required": 5,
                                    "misleading_prior": 4})
    assert by_kind["C"] == Counter({"different_answer": 10,
                                    "missing_evidence": 5,
                                    "abstention_required": 5,
                                    "misleading_prior": 4})
    assert by_kind["D"] == by_kind["C"]
    # The results file was written and agrees with the return.
    assert os.path.exists(PATHWAY002_RESULTS)
    with open(PATHWAY002_RESULTS) as f:
        saved = json.load(f)
    for arm in "ABCD":
        assert saved["arms"][arm]["summary"]["endpoint"] == \
            summaries[arm]["endpoint"]


def test_per_class_behavior():
    """Each class behaves as its mechanism requires, in every
    arm — not just in the totals."""
    r = _experiment()
    for case in CASES:
        tid = case["task_id"]
        a = r["arms"]["A"]["cases"][tid]
        b = r["arms"]["B"]["cases"][tid]
        c = r["arms"]["C"]["cases"][tid]
        d = r["arms"]["D"]["cases"][tid]
        kind = case["kind"]
        if kind == "different_answer":
            assert a["state"] == "UNSOLVED", tid
            assert b["state"] == "UNSOLVED", tid
            assert b["information_requests"] == [], tid
            assert c["state"] == "RESOLVED", tid
            assert c["selected_native"] == case["correct"], tid
            assert c["endpoint_counts"] is True, tid
            assert d["state"] == "RESOLVED", tid
            assert d["selected_native"] == case["correct"], tid
        elif kind == "missing_evidence":
            assert a["state"] == "ESCALATED", tid
            assert a["n_attempts"] == 0, tid
            for arm_case in (b, c, d):
                assert arm_case["state"] == "RESOLVED", tid
                assert arm_case["selected_native"] == case["correct"], tid
                assert arm_case["n_attempts"] == 1, tid
                assert arm_case["measurement_cost"] == 1, tid
        elif kind == "abstention_required":
            assert a["state"] == "ESCALATED", tid
            assert a["n_attempts"] == 0, tid
            for arm_case in (b, c, d):
                assert arm_case["state"] == "ESCALATED", tid
                assert arm_case["n_attempts"] == 0, tid
                assert arm_case["endpoint_kind"] == "abstention", tid
                assert arm_case["endpoint_counts"] is True, tid
            # C/D justify it via the replay path (the flag); B
            # via the direct path.
            assert b["information_requests"][0]["source"] == \
                "targeted_investigation", tid
            assert c["information_requests"][0]["source"] == \
                "pathway-replay", tid
            assert d["information_requests"][0]["source"] == \
                "pathway-replay", tid
        elif kind == "contradictory_observations":
            # No abstention is manufactured: the pointed-at
            # procedures (P5/P7, kind only) are not contradicted
            # by the v3 observation, so every arm trials and
            # ends UNSOLVED with zero false resolutions.
            for arm_case in (a, b, c, d):
                assert arm_case["state"] == "UNSOLVED", tid
                assert arm_case["endpoint_counts"] is False, tid
                assert arm_case["false_success"] is False, tid
                assert arm_case["n_attempts"] == 8, tid
            assert b["information_requests"] == [], tid
            assert len(c["information_requests"]) == 1, tid
            assert c["information_requests"][0]["source"] == \
                "pathway-replay", tid
            assert c["information_requests"][0]["outcome"] == "resumed", tid
        elif kind == "unavailable_evidence":
            # The selector selects nothing; every evidence arm
            # escalates unanswered with 0 attempts and 0 cost.
            for arm_case in (a, b, c, d):
                assert arm_case["state"] == "ESCALATED", tid
                assert arm_case["n_attempts"] == 0, tid
                assert arm_case["endpoint_counts"] is False, tid
                assert arm_case["measurement_cost"] == 0, tid
            assert a["n_selections"] == 0, tid
            for arm_case in (b, c, d):
                assert arm_case["n_selections"] >= 1, tid
            for arm_case in (b, c, d):
                assert len(arm_case["information_requests"]) == 1, tid
                assert arm_case["information_requests"][0]["outcome"] == \
                    "unanswered", tid
                assert arm_case["selected_conditions"] == [], tid
        elif kind == "misleading_prior":
            # The replayed method's source procedure is the
            # OPPOSITE of the held-out task's correct procedure,
            # yet arm C still resolves to the correct one: the
            # fresh answer wins over the stored method.
            assert b["state"] == "RESOLVED", tid
            assert b["selected_native"] == case["correct"], tid
            assert c["state"] == "RESOLVED", tid
            assert c["selected_native"] == case["correct"], tid
            assert d["state"] == "RESOLVED", tid
            assert d["selected_native"] == case["correct"], tid
            src = c["pathway_retrieval"]["method_source_task_id"]
            learning = {t["task_id"]: t for t in LEARNING_TASKS}
            assert learning[src]["correct"] != case["correct"], tid
            # Arm D gets no replay at all for the second task in
            # each misleading group (one pathway, two tasks).
            if tid.endswith("b"):
                assert d["pathway_retrieval"]["method_retrieved"] is False
                assert d["information_requests"][0]["source"] == \
                    "targeted_investigation", tid
            else:
                assert d["pathway_retrieval"]["method_retrieved"] is True


def test_critical_question_c_equals_d():
    """The critical question (design §5): C does outperform B
    (endpoint 24 vs 14, at cost ratio 1.0 — acceptable), but C
    does NOT outperform D. C and D are identical on the
    endpoint, on wrong attempts (90), on evidence requests
    (32) and on measurement cost (28) — because every pathway
    in a group names the same single discriminator
    ("firmware"), so the shuffle changes which pathway is
    offered, not which measurement is asked. No claim of
    demonstrated procedural-transfer advantage is made: the
    association-independence finding from PATHWAY-001 stands.
    The only C/D differences are retrieval bookkeeping: C
    retrieves a method for all 32 cases, D for 30 (the two
    misleading-prior second tasks get no replay)."""
    r = _experiment()
    c = r["arms"]["C"]["summary"]
    d = r["arms"]["D"]["summary"]
    assert c["endpoint"] == d["endpoint"] == 24
    assert c["wrong_attempts"] == d["wrong_attempts"] == 90
    assert c["evidence_requests_total"] == d["evidence_requests_total"] == 32
    assert c["measurement_cost_total"] == d["measurement_cost_total"] == 28
    assert c["n_method_retrieved"] == 32
    assert d["n_method_retrieved"] == 30
    # ... and the kind leg is unexercised in every arm: no
    # selected condition anywhere is anything but firmware.
    for arm in "ABCD":
        for case in r["arms"][arm]["cases"].values():
            assert set(case["selected_conditions"]) <= {"firmware"}, arm


def test_four_arm_shuffle_is_a_genuine_assignment():
    """Arm D's association is a genuine shuffle: every learning
    pathway is offered exactly once, every non-None association
    names a DIFFERENT task's pathway, and the two
    misleading-prior second tasks get no replay (one pathway
    cannot be shuffled to two tasks)."""
    r = _experiment()
    assoc = r["arms"]["D"]["association"]
    assert assoc is not None
    assert sorted(assoc) == sorted(c["task_id"] for c in CASES)
    pathway_ids = [rec["pathway_id"] for rec in r["store"]["records"]]
    non_none = [v for v in assoc.values() if v is not None]
    assert sorted(non_none) == sorted(pathway_ids)
    assert sorted(t for t, v in assoc.items() if v is None) == \
        ["pw2-mp-x-p1b", "pw2-mp-y-p2b"]
    source_of = {rec["pathway_id"]: rec["source_task_id"]
                 for rec in r["store"]["records"]}
    for task_id, pathway_id in assoc.items():
        if pathway_id is not None:
            assert source_of[pathway_id] != task_id
    # C replays the earliest pathway per group (store.find tie
    # rule); D must differ from that on at least one task.
    by_group = {}
    for rec in r["store"]["records"]:
        by_group.setdefault((rec["signature"], rec["family"]), []).append(
            rec["pathway_id"])
    earliest = {c["task_id"]: min(by_group[(c["signature"], c["family"])])
                for c in CASES}
    assert any(assoc[tid] != earliest[tid] for tid in assoc
               if assoc[tid] is not None)


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


# ------------------------------------------------------------------
# Parity: the R1/R2 reconciliations, exactly as measured
# ------------------------------------------------------------------

def test_parity_reconciliation_exact():
    """The frozen PATHWAY-001 checker reports raw defects only
    on the two new pipeline behaviors, and parity_check002
    reconciles every one of them by independent trace
    re-derivation. Measured: B 4 raw defects (R2 x4), C 24
    (R1 x20 + R2 x4), D 24 (same as C), A 0. Zero blocking
    defects anywhere; every case scored. If the implementation
    changes, these counts change and the test fails — the
    reconciliation is asserted as measured truth, not assumed."""
    r = _experiment()
    summaries = {arm: r["arms"][arm]["summary"] for arm in "ABCD"}
    assert summaries["A"]["n_parity_raw_defects"] == 0
    assert summaries["B"]["n_parity_raw_defects"] == 4
    assert summaries["C"]["n_parity_raw_defects"] == 24
    assert summaries["D"]["n_parity_raw_defects"] == 24
    assert summaries["B"]["parity_reconciled_by_pattern"] == {"R2": 4}
    assert summaries["C"]["parity_reconciled_by_pattern"] == \
        {"R1": 20, "R2": 4}
    assert summaries["D"]["parity_reconciled_by_pattern"] == \
        {"R1": 20, "R2": 4}
    for arm in "ABCD":
        s = summaries[arm]
        assert s["n_parity_reconciled"] == s["n_parity_raw_defects"], arm
        assert s["n_parity_blocking"] == 0, arm
        for tid, case in r["arms"][arm]["cases"].items():
            assert case["parity_blocking_defects"] == [], (arm, tid)
            assert case["selection_ok"] is True, (arm, tid)
            assert case["disposition_ok"] is True, (arm, tid)
            for entry in case["parity_reconciled"]:
                assert entry["pattern"] in ("R1", "R2"), (arm, tid)
                assert entry["note"], (arm, tid)
                assert entry["defect"] in case["parity_raw_defects"], \
                    (arm, tid)
    # The R1 cases are exactly the abstention-required cases in
    # the replay arms; the R2 cases are exactly the
    # unavailable-evidence cases in the evidence arms.
    for arm in ("C", "D"):
        r1 = sorted(t for t, c in r["arms"][arm]["cases"].items()
                    if any(e["pattern"] == "R1"
                           for e in c["parity_reconciled"]))
        assert r1 == sorted(c["task_id"] for c in CASES
                            if c["kind"] == "abstention_required")
        assert all(len(c["parity_reconciled"]) == 4
                   for c in (r["arms"][arm]["cases"][t] for t in r1))
    for arm in ("B", "C", "D"):
        r2 = sorted(t for t, c in r["arms"][arm]["cases"].items()
                    if any(e["pattern"] == "R2"
                           for e in c["parity_reconciled"]))
        assert r2 == sorted(c["task_id"] for c in CASES
                            if c["kind"] == "unavailable_evidence")
        assert all(len(c["parity_reconciled"]) == 1
                   for c in (r["arms"][arm]["cases"][t] for t in r2))


def test_pathway001_results_untouched():
    """PATHWAY-001's frozen numbers are unchanged by this work:
    pathway001_results.json is byte-identical (sha256) and its
    arm summaries still read A 0, B 10, C 15, D 15."""
    with open(PATHWAY001_RESULTS, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    assert digest == PATHWAY001_SHA256
    with open(PATHWAY001_RESULTS) as f:
        saved = json.load(f)
    assert saved["arms"]["A"]["summary"]["endpoint"] == 0
    assert saved["arms"]["B"]["summary"]["endpoint"] == 10
    assert saved["arms"]["C"]["summary"]["endpoint"] == 15
    assert saved["arms"]["D"]["summary"]["endpoint"] == 15


# ------------------------------------------------------------------
# The contradiction fix (design §6): reproduce the PATHWAY-001
# gap first, then show the flag closes it
# ------------------------------------------------------------------

def _mini_root_with_pathway(tmp, flag):
    """A minimal root: train, run one missing-evidence learning
    task, record its pathway. Returns (root, source) with the
    pathway in the store. The caller copies the root per flag
    value and runs the abstention task in each copy."""
    all_tasks = [dict(t) for t in LEARNING_TASKS] + \
        [dict(c) for c in CASES]
    source = FrozenEvidenceSource(build_evidence_table())
    learn = next(t for t in LEARNING_TASKS
                 if t["task_id"] == "me2-a-l1")
    h = Harness(root=os.path.join(tmp, "root"), tasks=all_tasks,
                evidence_source=source,
                measurement_selector=MeasurementSelector(
                    sources=[source], costs=COSTS),
                measurement_costs=COSTS,
                replay_contradiction_abstention=flag)
    try:
        h.train_baseline()
        store = PathwayStore(h.pipe.big)
        recorder = PathwayRecorder()
        rep = h.pipe.run(_public_task(learn))
        assert rep["state"] == "RESOLVED"
        events = trace_events(h.pipe.trace, learn["task_id"],
                              rep["execution_id"])
        record = recorder.record(_public_task(learn), rep, events,
                                 root="pw2-mini", existing_ids=set())
        store.write(record)
        assert store.count() == 1
    finally:
        h.close()
    return os.path.join(tmp, "root"), source


def test_replay_contradiction_flag_off_vs_on():
    """Reproduce the PATHWAY-001 gap first: with the flag off, a
    replayed v3 observation yields UNSOLVED (the known gap —
    not endpoint-counted, not a false success). With the flag
    on, the same run records justified_abstention (ESCALATED)
    and is endpoint-counted. The flag-on run's four raw parity
    defects are exactly the R1 pattern and reconcile cleanly;
    the flag-off run is parity-clean (PATHWAY-001 behavior)."""
    tmp = tempfile.mkdtemp(prefix="pw2-flag-")
    try:
        base_root, source = _mini_root_with_pathway(tmp, flag=True)
        abs_case = next(c for c in CASES
                        if c["task_id"] == "pw2-me-a-abs")
        results = {}
        for flag in (False, True):
            arm_root = os.path.join(tmp, f"flag-{flag}")
            shutil.copytree(base_root, arm_root)
            all_tasks = [dict(t) for t in LEARNING_TASKS] + \
                [dict(c) for c in CASES]
            h = Harness(root=arm_root, tasks=all_tasks,
                        evidence_source=source,
                        measurement_selector=MeasurementSelector(
                            sources=[source], costs=COSTS),
                        measurement_costs=COSTS,
                        replay_contradiction_abstention=flag)
            try:
                for t in TRAINING:
                    h.add_task(t)
                for x in TRANSFER_TASKS.values():
                    h.add_task(x)
                store = PathwayStore(h.pipe.big)
                h.pipe.pathway_provider = PathwayReplayProvider(store)
                task_dict = _public_task(abs_case)
                rep = h.pipe.run(task_dict)
                latest = h.pipe.experiences.latest_per_pair()
                events = trace_events(h.pipe.trace, abs_case["task_id"],
                                      rep["execution_id"])
                parity = parity_check002(
                    rep, h.pipe.trace, task_dict=task_dict, policy=h.policy,
                    trial_candidates_fn=lambda t, _p=h.pipe: (
                        _p._trial_candidates(t)),
                    latest_per_pair=latest)
                results[flag] = (rep, parity)
            finally:
                h.close()
        rep_off, parity_off = results[False]
        assert rep_off["state"] == "UNSOLVED"
        assert rep_off["information_requests"][0]["outcome"] == "resumed"
        assert parity_off["ok"] is True
        assert parity_off["raw"]["defects"] == []
        rep_on, parity_on = results[True]
        assert rep_on["state"] == "ESCALATED"
        req = rep_on["information_requests"][0]
        assert req["source"] == "pathway-replay"
        assert req["outcome"] == "justified_abstention"
        assert req["contradicted"] == ["SOSO-P1", "SOSO-P2"]
        assert len(parity_on["raw"]["defects"]) == 4
        assert len(parity_on["reconciled"]) == 4
        assert all(e["pattern"] == "R1"
                   for e in parity_on["reconciled"])
        assert parity_on["blocking_defects"] == []
        assert parity_on["ok"] is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_selection_parity_r2_standalone():
    """Pattern R2 standalone: an unavailable-evidence task under
    arm-B conditions. The frozen checker reports exactly one
    raw defect (information_requests); parity_check002
    reconciles it (the selector selected nothing, the missing
    condition was unavailable, no expectation was frozen, the
    run escalated unanswered) and nothing blocks."""
    tmp = tempfile.mkdtemp(prefix="pw2-r2-")
    try:
        all_tasks = [dict(t) for t in LEARNING_TASKS] + \
            [dict(c) for c in CASES]
        source = FrozenEvidenceSource(build_evidence_table())
        case = next(c for c in CASES if c["task_id"] == "pw2-un-a-p1")
        h = Harness(root=os.path.join(tmp, "root"), tasks=all_tasks,
                    evidence_source=source,
                    measurement_selector=MeasurementSelector(
                        sources=[source], costs=COSTS),
                    measurement_costs=COSTS,
                    replay_contradiction_abstention=True)
        try:
            h.train_baseline()
            task_dict = _public_task(case)
            rep = h.pipe.run(task_dict)
            latest = h.pipe.experiences.latest_per_pair()
            parity = parity_check002(
                rep, h.pipe.trace, task_dict=task_dict, policy=h.policy,
                trial_candidates_fn=lambda t, _p=h.pipe: (
                    _p._trial_candidates(t)),
                latest_per_pair=latest)
            assert rep["state"] == "ESCALATED"
            assert rep["information_requests"][0]["outcome"] == "unanswered"
            assert rep["information_requests"][0]["answered"] is True
            assert len(parity["raw"]["defects"]) == 1
            assert parity["raw"]["defects"][0].startswith(
                "information_requests mismatch")
            assert len(parity["reconciled"]) == 1
            assert parity["reconciled"][0]["pattern"] == "R2"
            assert parity["blocking_defects"] == []
            assert parity["ok"] is True
            # And the selection record itself: one selection,
            # nothing selected, firmware unavailable.
            assert parity["cost"]["measurement_cost"] == 0
            assert parity["cost"]["selected_conditions"] == []
        finally:
            h.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------
# Restart + persistence
# ------------------------------------------------------------------

def test_restart_persistence_retrieval_identical():
    """Reopen the trained+learned base root in a fresh process
    state (a new Harness over the same directory) and re-run:
    the pathways survive (same 30 ids), the evidence table
    answers identically, pathway retrieval is identical, and
    the re-runs reproduce the experiment's arm-C decisions
    exactly (state / selected / attempts / request outcomes)
    for one case of each class. The base root is used — not an
    arm root — because the arm roots already carry the first
    runs' experience writes, which would change the transfer
    stage on re-run."""
    tmp = tempfile.mkdtemp(prefix="pw2-restart-")
    try:
        r = run_pathway002_experiment(root=tmp, save_path=False)
        base_root = os.path.join(tmp, "root")
        source = FrozenEvidenceSource(build_evidence_table())
        h = Harness(root=base_root, tasks=[dict(t) for t in LEARNING_TASKS]
                    + [dict(c) for c in CASES],
                    evidence_source=source,
                    measurement_selector=MeasurementSelector(
                        sources=[source], costs=COSTS),
                    measurement_costs=COSTS,
                    replay_contradiction_abstention=True)
        try:
            for t in TRAINING:
                h.add_task(t)
            for x in TRANSFER_TASKS.values():
                h.add_task(x)
            store = PathwayStore(h.pipe.big)
            assert store.count() == 30
            assert [rec["pathway_id"] for rec in store.all()] == \
                r["store"]["pathway_ids"]
            provider = PathwayReplayProvider(store)
            h.pipe.pathway_provider = provider
            # The evidence table answers identically after reopen.
            for task in CASES + LEARNING_TASKS:
                want = (task["task_id"], "firmware") in \
                    build_evidence_table()
                assert source.available(task["task_id"], "firmware") == want
            one_per_class = {}
            for case in CASES:
                one_per_class.setdefault(case["kind"], case)
            assert sorted(one_per_class) == sorted(CASE_COUNTS)
            for kind, case in sorted(one_per_class.items()):
                task_dict = _public_task(case)
                method = provider.method_for(task_dict)
                original = r["arms"]["C"]["cases"][case["task_id"]]
                assert (method["pathway_id"] if method else None) == \
                    original["pathway_retrieval"]["method_pathway_id"], kind
                rep = h.pipe.run(task_dict)
                assert rep["state"] == original["state"], kind
                assert rep["selected"] == original["selected"], kind
                assert [a["procedure"] for a in rep["attempts"]] == \
                    original["attempts"], kind
                assert [(x.get("source"), x.get("outcome")) for x in
                        rep["information_requests"]] == \
                    [(x.get("source"), x.get("outcome")) for x in
                     original["information_requests"]], kind
        finally:
            h.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
