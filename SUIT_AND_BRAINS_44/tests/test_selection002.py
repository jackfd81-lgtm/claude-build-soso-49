"""PATHWAY-002 (Worker 3) — MeasurementSelector reproduction tests.

Written FIRST, before any contract/*.py change (PATHWAY-002 design
SS2 + coordinator order). Expected results BEFORE the fix:

- Every selector test FAILS with ImportError (contract/selection.py
  does not exist yet).
- test_certify_selection_after_expectation_is_a_gap FAILS
  (certify() has no EVIDENCE_SELECTION check yet, so the run
  certifies).
- test_certify_selection_before_expectation_ok and
  test_certify_legacy_expectation_without_selection_ok PASS
  (they pin the behavior the fix must preserve).

The selector API under test (defined by the coordinator brief +
design SS2; the implementation must match it):

    MeasurementSelector(sources, costs=None, allowed_sources=None)
        sources: EvidenceSource objects; the selector may call ONLY
            .source_id and .available(task_id, condition).
        costs: {(source_id, condition): number}, default 1.
        allowed_sources: set of admissible source_ids, or None
            (every source admissible).
    .select(hypotheses, task, known=None, method=None) -> dict
        hypotheses: [(procedure_id,
                      {condition: (required_value, is_hard)})]
            derived from registry preconditions (HARD and SOFT).
        task: object with .task_id, .conditions, .unknown_conditions.
        known: {condition: value} already known.
        method: optional pathway record dict; its "measurements"
            ([{"condition": ...}] entries) are CANDIDATES, not orders.
        Returns {"task_id", "evaluations", "selected"} where each
        evaluation records all six dimensions:
            condition, source_id, distinguishes (procedures),
            distinguishing_power (distinct required values),
            expected_classes (distinct values + "other"),
            implications {class: {admits/contradicts (HARD) or
                ranks_up/ranks_down (SOFT), next}},
            available, admissible, cost, already_known,
            method_candidate, useful.
        "selected" is the ranked useful subset, one entry per
        condition (cheapest admissible available source wins),
        ranked by (distinguishing power desc, cost asc,
        condition name asc).

Invariants pinned here: zero answer() calls during selection;
identical selections when ground-truth table values are swapped;
a soft mismatch is NEVER worded as a contradiction.
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _import_selector():
    from contract.selection import MeasurementSelector
    return MeasurementSelector


class RecordingSource:
    """Evidence-source stand-in: available() is key presence
    only; answer() records every call (the selector must make
    none)."""

    def __init__(self, source_id, table):
        self.source_id = source_id
        self.table = dict(table)
        self.answer_calls = []
        self.available_calls = []

    def available(self, task_id, condition):
        self.available_calls.append((task_id, condition))
        return (task_id, condition) in self.table

    def answer(self, task_id, condition):
        self.answer_calls.append((task_id, condition))
        value = self.table[(task_id, condition)]
        return value, {"source": self.source_id, "task_id": task_id,
                       "condition": condition}


TASK_ID = "pw2-sel-1"


def _task(conditions=None, unknown=("firmware", "kind")):
    return SimpleNamespace(task_id=TASK_ID, conditions=dict(conditions or {}),
                           unknown_conditions=tuple(unknown))


# P1/P2 differ in hard firmware; P3/P7 differ in soft kind.
HYPS = [
    ("SOSO-P1", {"firmware": ("v1", True)}),
    ("SOSO-P2", {"firmware": ("v2", True)}),
    ("SOSO-P3", {"kind": ("sensor", False)}),
    ("SOSO-P7", {"kind": ("detour", False)}),
]


def _sources():
    src_a = RecordingSource("src-a", {(TASK_ID, "firmware"): "v1",
                                      (TASK_ID, "kind"): "sensor"})
    src_b = RecordingSource("src-b", {(TASK_ID, "firmware"): "v2"})
    return src_a, src_b


def _eval_by(result, condition, source_id):
    for ev in result["evaluations"]:
        if ev["condition"] == condition and ev["source_id"] == source_id:
            return ev
    raise AssertionError(
        f"no evaluation for {(condition, source_id)} in {result['evaluations']}")


# ------------------------------------------------------------------
# Evaluation: all six dimensions
# ------------------------------------------------------------------

def test_evaluation_records_all_six_dimensions():
    MeasurementSelector = _import_selector()
    src_a, src_b = _sources()
    costs = {("src-a", "firmware"): 5, ("src-a", "kind"): 1,
             ("src-b", "firmware"): 1}
    sel = MeasurementSelector(sources=[src_a, src_b], costs=costs)
    result = sel.select(HYPS, _task())
    assert result["task_id"] == TASK_ID
    # 1. distinguishes / distinguishing power
    fw = _eval_by(result, "firmware", "src-a")
    assert sorted(fw["distinguishes"]) == ["SOSO-P1", "SOSO-P2"]
    assert fw["distinguishing_power"] == 2
    # 2. expected classes: distinct required values + "other"
    assert fw["expected_classes"] == ["v1", "v2", "other"]
    # 3. implications (hard: admits / contradicts)
    assert fw["implications"]["v1"]["admits"] == ["SOSO-P1"]
    assert fw["implications"]["v1"]["contradicts"] == ["SOSO-P2"]
    assert fw["implications"]["other"]["admits"] == []
    assert fw["implications"]["other"]["contradicts"] == ["SOSO-P1", "SOSO-P2"]
    # 4. availability / admissibility
    assert fw["available"] is True
    assert fw["admissible"] is True
    # 5. cost
    assert fw["cost"] == 5
    # 6. already known
    assert fw["already_known"] is False
    assert fw["useful"] is True
    assert fw["method_candidate"] is False
    # kind via src-a: same six dimensions, soft implications.
    kd = _eval_by(result, "kind", "src-a")
    assert sorted(kd["distinguishes"]) == ["SOSO-P3", "SOSO-P7"]
    assert kd["expected_classes"] == ["detour", "sensor", "other"]
    assert kd["available"] is True
    assert kd["cost"] == 1
    # kind is not answerable by src-b: unavailable, not useful.
    assert _eval_by(result, "kind", "src-b")["available"] is False
    assert _eval_by(result, "kind", "src-b")["useful"] is False


def test_ranking_power_then_cost_then_condition_name():
    MeasurementSelector = _import_selector()
    src_a, src_b = _sources()
    # Both conditions distinguish 2 hypotheses. Cheapest admissible
    # source wins per condition, so firmware costs 1 via src-b and
    # kind costs 1 via src-a; same power, same cost, so condition
    # name ascending breaks the tie and firmware ranks first.
    # (Coordinator adjudication: the original comment claimed kind
    # was cheaper, but under cheapest-wins firmware costs 1, not 5
    # — the expectation contradicted both its own comment and the
    # block-2 tie-break rule frozen in design §2. The documented
    # rule governs, so the expectation is corrected here.)
    sel = MeasurementSelector(
        sources=[src_a, src_b],
        costs={("src-a", "firmware"): 5, ("src-a", "kind"): 1,
               ("src-b", "firmware"): 1})
    result = sel.select(HYPS, _task())
    assert [(s["condition"], s["source_id"]) for s in result["selected"]] == [
        ("firmware", "src-b"), ("kind", "src-a")]
    # Same power, same cost: condition name ascending breaks the tie.
    sel2 = MeasurementSelector(
        sources=[src_a, src_b],
        costs={("src-a", "firmware"): 1, ("src-a", "kind"): 1,
               ("src-b", "firmware"): 1})
    result2 = sel2.select(HYPS, _task())
    assert [s["condition"] for s in result2["selected"]] == [
        "firmware", "kind"]
    # Higher distinguishing power beats lower cost: kind
    # distinguishes 3 hypotheses once P5 (kind=route) is added.
    hyps3 = HYPS + [("SOSO-P5", {"kind": ("route", False)})]
    sel3 = MeasurementSelector(
        sources=[src_a, src_b],
        costs={("src-a", "firmware"): 1, ("src-a", "kind"): 9,
               ("src-b", "firmware"): 1})
    result3 = sel3.select(hyps3, _task())
    assert [s["condition"] for s in result3["selected"]] == [
        "kind", "firmware"]
    assert _eval_by(result3, "kind", "src-a")["distinguishing_power"] == 3


def test_useful_rule_excludes_non_distinguishing_unavailable_known():
    MeasurementSelector = _import_selector()
    src_a, src_b = _sources()
    sel = MeasurementSelector(sources=[src_a, src_b])
    # Only one hypothesis requires firmware: power 1, not useful.
    result = sel.select([("SOSO-P1", {"firmware": ("v1", True)}),
                         ("SOSO-P3", {"kind": ("sensor", False)}),
                         ("SOSO-P7", {"kind": ("detour", False)})],
                        _task())
    assert _eval_by(result, "firmware", "src-a")["useful"] is False
    assert _eval_by(result, "firmware", "src-a")["distinguishing_power"] == 1
    assert [s["condition"] for s in result["selected"]] == ["kind"]
    # Already known: firmware is known, so it is evaluated but
    # not selected even though it distinguishes.
    result2 = sel.select(HYPS, _task(), known={"firmware": "v1"})
    fw = _eval_by(result2, "firmware", "src-a")
    assert fw["already_known"] is True
    assert fw["useful"] is False
    assert [s["condition"] for s in result2["selected"]] == ["kind"]
    # Admissibility: only src-a is allowed, so firmware via
    # src-b is inadmissible. Both conditions cost the default 1
    # and distinguish 2 hypotheses, so condition name ascending
    # breaks the tie and firmware ranks first.
    # (Coordinator adjudication: the original comment called the
    # src-a firmware "pricier", but this block sets no costs, so
    # both conditions cost 1 — the expectation contradicted the
    # tie-break rule frozen in design §2. The documented rule
    # governs, so the expectation is corrected here.)
    sel3 = MeasurementSelector(sources=[src_a, src_b],
                               allowed_sources={"src-a"})
    result3 = sel3.select(HYPS, _task())
    assert _eval_by(result3, "firmware", "src-b")["admissible"] is False
    assert _eval_by(result3, "firmware", "src-b")["useful"] is False
    assert [(s["condition"], s["source_id"]) for s in result3["selected"]] == [
        ("firmware", "src-a"), ("kind", "src-a")]


def test_default_cost_is_one():
    MeasurementSelector = _import_selector()
    src_a, _src_b = _sources()
    sel = MeasurementSelector(sources=[src_a])
    result = sel.select(HYPS, _task())
    assert _eval_by(result, "firmware", "src-a")["cost"] == 1
    assert _eval_by(result, "kind", "src-a")["cost"] == 1


# ------------------------------------------------------------------
# Ground-truth independence
# ------------------------------------------------------------------

def test_zero_answer_calls_during_selection():
    MeasurementSelector = _import_selector()
    src_a, src_b = _sources()
    sel = MeasurementSelector(sources=[src_a, src_b])
    sel.select(HYPS, _task())
    assert src_a.answer_calls == []
    assert src_b.answer_calls == []
    # available() is the only source call selection may make.
    assert src_a.available_calls, "selection never consulted availability"


def test_selection_invariant_to_ground_truth_values():
    MeasurementSelector = _import_selector()
    table_a = {(TASK_ID, "firmware"): "v1", (TASK_ID, "kind"): "sensor"}
    # Same keys, every value swapped: v1<->v2, sensor<->pump.
    table_b = {(TASK_ID, "firmware"): "v2", (TASK_ID, "kind"): "pump"}
    src_a = RecordingSource("src-a", table_a)
    src_b = RecordingSource("src-a", table_b)
    costs = {("src-a", "firmware"): 3, ("src-a", "kind"): 2}
    sel_a = MeasurementSelector(sources=[src_a], costs=costs)
    sel_b = MeasurementSelector(sources=[src_b], costs=costs)
    result_a = sel_a.select(HYPS, _task())
    result_b = sel_b.select(HYPS, _task())
    assert result_a == result_b
    assert src_a.answer_calls == [] and src_b.answer_calls == []
    # Determinism: the same selector, run twice, agrees exactly.
    assert sel_a.select(HYPS, _task()) == result_a


# ------------------------------------------------------------------
# Soft vs hard implication wording
# ------------------------------------------------------------------

def test_soft_implications_never_say_contradicts():
    MeasurementSelector = _import_selector()
    src_a, _src_b = _sources()
    sel = MeasurementSelector(sources=[src_a])
    result = sel.select(HYPS, _task())
    kd = _eval_by(result, "kind", "src-a")
    for cls, impl in kd["implications"].items():
        assert "contradicts" not in impl, cls
        assert "contradict" not in impl.get("next", ""), cls
        assert set(impl) >= {"ranks_up", "ranks_down", "next"}
    assert kd["implications"]["sensor"]["ranks_up"] == ["SOSO-P3"]
    assert kd["implications"]["sensor"]["ranks_down"] == ["SOSO-P7"]
    assert kd["implications"]["other"]["ranks_up"] == []
    assert kd["implications"]["other"]["ranks_down"] == ["SOSO-P3", "SOSO-P7"]
    # Hard keeps the admission wording.
    fw = _eval_by(result, "firmware", "src-a")
    for cls, impl in fw["implications"].items():
        assert set(impl) >= {"admits", "contradicts", "next"}
        assert "ranks_up" not in impl and "ranks_down" not in impl


# ------------------------------------------------------------------
# Method record: candidates, not orders
# ------------------------------------------------------------------

def test_method_measurements_are_candidates_not_orders():
    MeasurementSelector = _import_selector()
    src_a, src_b = _sources()
    sel = MeasurementSelector(sources=[src_a, src_b])
    method = {"measurements": [{"condition": "firmware"},
                               {"condition": "kind"}]}
    with_method = sel.select(HYPS, _task(), method=method)
    without_method = sel.select(HYPS, _task())
    # The method's candidates are exactly the useful ones; the
    # selection is unchanged by the method's say-so.
    assert with_method["selected"] == without_method["selected"]
    assert _eval_by(with_method, "firmware", "src-a")["method_candidate"] is True
    assert _eval_by(without_method, "firmware", "src-a")["method_candidate"] is False
    # A method measurement that does not distinguish the NEW
    # task's hypotheses is not selected, however successful it
    # was before: kind has a single required value here.
    hyps_single_kind = [
        ("SOSO-P1", {"firmware": ("v1", True)}),
        ("SOSO-P2", {"firmware": ("v2", True)}),
        ("SOSO-P3", {"kind": ("sensor", False)}),
    ]
    result = sel.select(hyps_single_kind, _task(), method=method)
    assert _eval_by(result, "kind", "src-a")["method_candidate"] is True
    assert _eval_by(result, "kind", "src-a")["useful"] is False
    assert [s["condition"] for s in result["selected"]] == ["firmware"]
    # A method measurement for a condition no hypothesis
    # requires is ignored entirely.
    result2 = sel.select(HYPS, _task(),
                         method={"measurements": [{"condition": "pressure"}]})
    assert [e["condition"] for e in result2["evaluations"]] == [
        e["condition"] for e in without_method["evaluations"]]
    assert result2["selected"] == without_method["selected"]


# ------------------------------------------------------------------
# Structural: the selector never reaches ground truth or the pipeline
# ------------------------------------------------------------------

def test_selector_never_imports_pipeline_validator_or_criteria():
    import contract.selection as selection_mod
    src = open(selection_mod.__file__).read()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "pipeline" not in stripped, stripped
            assert "validator" not in stripped, stripped
            assert "criteria" not in stripped, stripped
    assert ".answer(" not in src, "selector must never call answer()"
    assert "contract.pipeline" not in src
    assert "contract.validator" not in src


# ------------------------------------------------------------------
# trace.certify(): EVIDENCE_SELECTION must precede covered expectations
# ------------------------------------------------------------------

def _trace_run(harness, task_id, events_in_order):
    """Emit the given (event_type, data) pairs on a fresh tracer
    over the harness trace log, inside one TASK_BEGIN/TASK_END,
    and return the certification dict."""
    from contract.trace import Tracer
    tr = Tracer(harness.pipe.trace, task_id)
    tr.begin_task(context={})
    span = tr.begin_span("U10")
    for event_type, data in events_in_order:
        tr.event(event_type, span, data)
    tr.end_span(span)
    tr.end_task("ESCALATED")
    return tr.certify()


def _selection_data(conditions):
    return {"selection_id": "sel-1", "task_id": "pw2-cert-1",
            "selected": [{"condition": c, "source_id": "frozen-evidence"}
                         for c in conditions],
            "evaluations": []}


def _expectation_data(condition):
    return {"expectation_id": "ev-0001", "condition": condition,
            "hypotheses": [{"procedure": "SOSO-P1", "requires": "v1"},
                           {"procedure": "SOSO-P2", "requires": "v2"}],
            "predicted_classes": ["v1", "v2", "other"],
            "implications": {}, "source": "frozen-evidence",
            "measurement": "What is firmware for this task?"}


def test_certify_selection_after_expectation_is_a_gap():
    """REPRO for the certify extension: an EVIDENCE_EXPECTATION
    for firmware that is covered by a selection (the selection
    selects firmware) but NOT preceded by it must fail
    certification. Before the fix this certifies (no check)."""
    from contract.harness import Harness
    h = Harness()
    try:
        cert = _trace_run(h, "pw2-cert-1", [
            ("EVIDENCE_EXPECTATION", _expectation_data("firmware")),
            ("EVIDENCE_SELECTION", _selection_data(["firmware"])),
        ])
        assert cert["certified"] is False, cert
        assert any("EVIDENCE_SELECTION" in g for g in cert["gaps"]), cert
    finally:
        h.close()


def test_certify_selection_before_expectation_ok():
    from contract.harness import Harness
    h = Harness()
    try:
        cert = _trace_run(h, "pw2-cert-1", [
            ("EVIDENCE_SELECTION", _selection_data(["firmware"])),
            ("EVIDENCE_EXPECTATION", _expectation_data("firmware")),
        ])
        assert cert["certified"] is True, cert
        assert cert["gaps"] == []
    finally:
        h.close()


def test_certify_legacy_expectation_without_selection_ok():
    """Legacy (PATHWAY-001) runs emit no EVIDENCE_SELECTION at
    all; their expectations must still certify."""
    from contract.harness import Harness
    h = Harness()
    try:
        cert = _trace_run(h, "pw2-cert-1", [
            ("EVIDENCE_EXPECTATION", _expectation_data("firmware")),
        ])
        assert cert["certified"] is True, cert
        assert cert["gaps"] == []
    finally:
        h.close()
