from pathlib import Path
"""PATHWAY-001 (evidence half) — the evidence source + answered U10.

Tests for contract/evidence.py, the pipeline's answered
evidence-request flow (U10 blocked branch), the pathway replay
call site, and the certify() expectation-precedes-observation
check. The frozen baseline (132 tests) is untouched by these
tests; the no-source control below asserts current behavior
verbatim.

The evidence task: a valve task whose firmware is UNKNOWN.
P1 (requires v1) and P2 (requires v2) are the only procedures
with hard preconditions, and the tags point at both — so the
trial blocks on the unknown firmware and U10 asks. P1 is NOT
qualified by train_baseline(), so a firmware-v1 task resolves
via trial (never via trusted reuse).
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "realcog"))

import contract.evidence as evidence_mod
from contract.evidence import EvidenceSource, FrozenEvidenceSource
from contract.harness import Harness
from contract.trace import TRACE_KIND, certify, trace_events


def _evidence_task(task_id="pw-ev1"):
    return {"task_id": task_id, "signature": "disk_full",
            "family": "valve",
            "tags": ["valve", "reseat", "stuck", "pressure"],
            "conditions": {},
            "unknown_conditions": ("firmware",),
            "requested_operation": "repair",
            "provenance": ("int:pathway-evidence",), "correct": "P1"}


def _run_evidence_task(h, task):
    h.add_task(task)
    return h.pipe.run({k: v for k, v in task.items() if k != "correct"})


def _events_of(h, rep, event_type):
    return [(seq, ev) for seq, ev in
            trace_events(h.pipe.trace, rep["task_id"], rep["execution_id"])
            if ev["event_type"] == event_type]


# ------------------------------------------------------------------
# The frozen source
# ------------------------------------------------------------------

def test_frozen_source_answer_provenance_and_keyerror():
    src = FrozenEvidenceSource({("t1", "firmware"): "v1"})
    value, prov = src.answer("t1", "firmware")
    assert value == "v1"
    assert prov == {"source": "frozen-evidence", "task_id": "t1",
                    "condition": "firmware"}
    src2 = FrozenEvidenceSource({("t1", "firmware"): "v1"},
                                source_id="bench-source")
    assert src2.answer("t1", "firmware") == (
        "v1", {"source": "bench-source", "task_id": "t1",
               "condition": "firmware"})
    for task_id, condition in (("t1", "kind"), ("other", "firmware")):
        try:
            src.answer(task_id, condition)
        except KeyError:
            pass
        else:
            raise AssertionError(
                f"absent {(task_id, condition)} must raise KeyError")
    assert isinstance(src, EvidenceSource)


def test_source_independence_no_criteria_access():
    """E5 INDEPENDENCE: the source holds no criteria, no
    validator, and no correct procedure — it cannot leak what
    it never sees. This is checked on the module's CODE (via
    ast, so the docstring's discussion of the rule does not
    count): no import of any contract module, and no code
    reference to criteria / validator / a correct procedure.
    The answer for a task must also be the same regardless of
    which procedure is correct for that task."""
    import ast
    tree = ast.parse(pathlib.Path(evidence_mod.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    for mod in imported:
        assert not any(s in mod for s in
                       ("admission", "registry", "pipeline", "validator")), mod
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert names.isdisjoint({"criteria", "validator", "correct"}), names
    src = FrozenEvidenceSource({("pw-ev1", "firmware"): "v1"})
    assert "criteria" not in vars(src)
    assert not hasattr(src, "validator")
    assert not hasattr(src, "correct")
    assert not hasattr(src, "procedure")
    # Two worlds that differ ONLY in which procedure is correct
    # for the task: the source is built from the evidence table
    # alone, so both give the identical answer.
    world_p1_correct = FrozenEvidenceSource({("pw-ev1", "firmware"): "v1"})
    world_p2_correct = FrozenEvidenceSource({("pw-ev1", "firmware"): "v1"})
    assert (world_p1_correct.answer("pw-ev1", "firmware")
            == world_p2_correct.answer("pw-ev1", "firmware")
            == ("v1", {"source": "frozen-evidence",
                       "task_id": "pw-ev1", "condition": "firmware"}))


# ------------------------------------------------------------------
# One complete evidence request: expectation -> observation ->
# resumption -> resolution
# ------------------------------------------------------------------

def test_complete_evidence_request_resolves():
    h = Harness(evidence_source=FrozenEvidenceSource(
        {("pw-ev1", "firmware"): "v1"}))
    try:
        h.train_baseline()
        rep = _run_evidence_task(h, _evidence_task())
        assert rep["state"] == "RESOLVED", rep
        assert rep["selected"] == "SOSO-P1"
        assert rep["stage"] == "higher-trial"
        assert len(rep["attempts"]) == 1
        assert rep["attempts"][0]["procedure"] == "SOSO-P1"
        # The request record carries the full decision.
        assert len(rep["information_requests"]) == 1
        req = rep["information_requests"][0]
        assert req["outcome"] == "resumed"
        assert req["answered"] is True
        assert req["source"] == "targeted_investigation"
        assert req["missing"] == ["firmware"]
        assert req["procedures"] == ["SOSO-P1", "SOSO-P2"]
        assert req["observation"]["firmware"]["value"] == "v1"
        assert req["observation"]["firmware"]["provenance"] == {
            "source": "frozen-evidence", "task_id": "pw-ev1",
            "condition": "firmware"}
        assert req["expectation"]["firmware"]["condition"] == "firmware"
        assert req["expectation"]["firmware"]["predicted_classes"] == [
            "v1", "v2", "other"]
        assert {hh["procedure"] for hh in
                req["expectation"]["firmware"]["hypotheses"]} == {
                    "SOSO-P1", "SOSO-P2"}
        assert req["expectation"]["firmware"]["hypotheses"] == [
            {"procedure": "SOSO-P1", "requires": "v1"},
            {"procedure": "SOSO-P2", "requires": "v2"}]
        assert req["expectation_id"]["firmware"] == \
            req["expectation"]["firmware"]["expectation_id"]
        # A procedure admitted after the answer was never
        # rejected: P1 (admitted post-answer) appears nowhere
        # in the rejected list. P2's two entries predate the
        # answer — the exact stage and the transfer stage each
        # refused it for the then-unknown firmware — and the
        # U10 branch appended no pointed_blocks on resumption.
        assert "SOSO-P1" not in {r["procedure"]
                                 for r in rep["rejected"]}
        assert [r for r in rep["rejected"]
                if r["procedure"] == "SOSO-P2"] == [
            {"procedure": "SOSO-P2",
             "reason": "SOSO-P2 needs ['firmware'], which the task "
                       "does not state"}] * 2
        # The ledger: the expectation precedes the observation
        # and the ids match.
        exp = _events_of(h, rep, "EVIDENCE_EXPECTATION")
        obs = _events_of(h, rep, "EVIDENCE_OBSERVED")
        assert len(exp) == 1 and len(obs) == 1
        assert exp[0][0] < obs[0][0]
        assert obs[0][1]["data"]["expectation_id"] == exp[0][1]["event_id"]
        assert exp[0][1]["data"]["expectation_id"] == exp[0][1]["event_id"]
        assert exp[0][1]["data"]["condition"] == "firmware"
        assert exp[0][1]["data"]["source"] == "frozen-evidence"
        assert obs[0][1]["data"]["value"] == "v1"
        assert rep["trace_certified"] is True, \
            rep["trace_certification_gaps"]
    finally:
        h.close()


def test_contradicted_all_justified_abstention():
    h = Harness(evidence_source=FrozenEvidenceSource(
        {("pw-ev1", "firmware"): "v3"}))
    try:
        h.train_baseline()
        rep = _run_evidence_task(h, _evidence_task())
        assert rep["state"] == "ESCALATED", rep
        assert rep["stage"] == "unresolved"
        assert len(rep["attempts"]) == 0
        assert len(rep["information_requests"]) == 1
        req = rep["information_requests"][0]
        assert req["outcome"] == "justified_abstention"
        assert req["answered"] is True
        assert req["observation"]["firmware"]["value"] == "v3"
        # The pointed-at procedures were rejected (they were
        # pointed at and contradicted), and the abstention
        # escalation carries the observation.
        assert {r["procedure"] for r in rep["rejected"]} >= {
            "SOSO-P1", "SOSO-P2"}
        esc = _events_of(h, rep, "ESCALATION")
        assert any(
            e["data"].get("reason")
            == "evidence contradicts every pointed-at procedure"
            and e["data"]["observation"]["firmware"]["value"] == "v3"
            and set(e["data"]["contradicted"]) == {"SOSO-P1", "SOSO-P2"}
            for _, e in esc)
        # The expectation still precedes the observation.
        exp = _events_of(h, rep, "EVIDENCE_EXPECTATION")
        obs = _events_of(h, rep, "EVIDENCE_OBSERVED")
        assert len(exp) == 1 and len(obs) == 1
        assert exp[0][0] < obs[0][0]
        assert obs[0][1]["data"]["expectation_id"] == exp[0][1]["event_id"]
        assert rep["trace_certified"] is True, \
            rep["trace_certification_gaps"]
    finally:
        h.close()


def test_unanswered_escalates():
    h = Harness(evidence_source=FrozenEvidenceSource({}))
    try:
        h.train_baseline()
        rep = _run_evidence_task(h, _evidence_task())
        assert rep["state"] == "ESCALATED", rep
        assert rep["stage"] == "unresolved"
        assert len(rep["attempts"]) == 0
        assert len(rep["information_requests"]) == 1
        req = rep["information_requests"][0]
        assert req["outcome"] == "unanswered"
        assert req["answered"] is False
        assert req["observation"] is None
        # Honest: the expectation is frozen, no observation
        # event exists.
        exp = _events_of(h, rep, "EVIDENCE_EXPECTATION")
        obs = _events_of(h, rep, "EVIDENCE_OBSERVED")
        assert len(exp) == 1 and len(obs) == 0
        assert exp[0][1]["data"]["condition"] == "firmware"
        assert rep["trace_certified"] is True, \
            rep["trace_certification_gaps"]
    finally:
        h.close()


def test_no_source_control_current_behavior():
    h = Harness()
    try:
        h.train_baseline()
        rep = _run_evidence_task(h, _evidence_task())
        assert rep["state"] == "ESCALATED", rep
        assert rep["stage"] == "unresolved"
        assert len(rep["attempts"]) == 0
        assert len(rep["information_requests"]) == 1
        req = rep["information_requests"][0]
        assert req["missing"] == ["firmware"]
        assert req["procedures"] == ["SOSO-P1", "SOSO-P2"]
        assert req["source"] == "targeted_investigation"
        # No evidence machinery ran at all.
        assert "outcome" not in req
        assert "answered" not in req
        assert _events_of(h, rep, "EVIDENCE_EXPECTATION") == []
        assert _events_of(h, rep, "EVIDENCE_OBSERVED") == []
        assert rep["trace_certified"] is True, \
            rep["trace_certification_gaps"]
    finally:
        h.close()


# ------------------------------------------------------------------
# Pathway replay call site
# ------------------------------------------------------------------

class _ListProvider:
    """A minimal duck-typed pathway provider for the replay
    call site: it names discriminators, never answers."""

    def __init__(self, discriminators):
        self._discriminators = list(discriminators)

    def discriminators_for(self, task):
        return list(self._discriminators)


def test_replay_asks_before_trial_ranking():
    """The replay call site: with a provider naming firmware
    and a source answering v1, the run asks (pathway-replay)
    before the trial ranking, so the U10 blocked branch never
    fires — no targeted_investigation request is recorded."""
    h = Harness(
        evidence_source=FrozenEvidenceSource(
            {("pw-ev1", "firmware"): "v1"}),
        pathway_provider=_ListProvider(["firmware"]))
    try:
        h.train_baseline()
        rep = _run_evidence_task(h, _evidence_task())
        assert rep["state"] == "RESOLVED", rep
        assert rep["selected"] == "SOSO-P1"
        assert rep["stage"] == "higher-trial"
        assert len(rep["attempts"]) == 1
        assert len(rep["information_requests"]) == 1
        req = rep["information_requests"][0]
        assert req["source"] == "pathway-replay"
        assert req["outcome"] == "resumed"
        assert req["observation"]["firmware"]["value"] == "v1"
        assert req["missing"] == ["firmware"]
        assert req["procedures"] == ["SOSO-P1", "SOSO-P2"]
        exp = _events_of(h, rep, "EVIDENCE_EXPECTATION")
        obs = _events_of(h, rep, "EVIDENCE_OBSERVED")
        assert len(exp) == 1 and len(obs) == 1
        assert exp[0][0] < obs[0][0]
        assert rep["trace_certified"] is True, \
            rep["trace_certification_gaps"]
    finally:
        h.close()


def test_replay_contradiction_stops_replay():
    """A replayed observation contradicting every procedure
    requiring the condition stops the replay and lets the
    normal flow decide (here: every pointed-at procedure
    blocked -> UNSOLVED via escalate_because, no invented
    second abstention path)."""
    h = Harness(
        evidence_source=FrozenEvidenceSource(
            {("pw-ev1", "firmware"): "v3"}),
        pathway_provider=_ListProvider(["firmware", "kind"]))
    try:
        h.train_baseline()
        rep = _run_evidence_task(h, _evidence_task())
        assert rep["state"] == "UNSOLVED", rep
        assert len(rep["attempts"]) == 0
        # Only the firmware replay ran; "kind" was never asked
        # (replay stopped after the contradiction).
        assert len(rep["information_requests"]) == 1
        req = rep["information_requests"][0]
        assert req["source"] == "pathway-replay"
        assert req["missing"] == ["firmware"]
        assert req["observation"]["firmware"]["value"] == "v3"
        assert rep["trace_certified"] is True, \
            rep["trace_certification_gaps"]
    finally:
        h.close()


def test_replay_skips_known_conditions():
    """A discriminator the task already states is not asked
    again: no replayed request, no evidence events, and the
    run proceeds normally."""
    h = Harness(
        evidence_source=FrozenEvidenceSource(
            {("pw-ev1", "firmware"): "v1"}),
        pathway_provider=_ListProvider(["firmware"]))
    try:
        h.train_baseline()
        task = _evidence_task()
        task = {**task, "conditions": {"firmware": "v1"},
                "unknown_conditions": ()}
        rep = _run_evidence_task(h, task)
        assert rep["state"] == "RESOLVED", rep
        assert rep["selected"] == "SOSO-P1"
        assert rep["information_requests"] == []
        assert _events_of(h, rep, "EVIDENCE_EXPECTATION") == []
        assert _events_of(h, rep, "EVIDENCE_OBSERVED") == []
        assert rep["trace_certified"] is True, \
            rep["trace_certification_gaps"]
    finally:
        h.close()


# ------------------------------------------------------------------
# certify(): expectation precedes observation
# ------------------------------------------------------------------

def _raw_event(event_type, execution_id, data=None):
    return {"schema": "soso-cognitive-event/0.1",
            "execution_id": execution_id, "event_id": "ev-pending",
            "parent_event_id": None, "sequence": None,
            "task_id": "pw-cert", "function_namespace": None,
            "function_id": None, "function_version": None,
            "event_type": event_type, "domain": None,
            "domain_transition_id": None, "input_refs": [],
            "memory_refs": [], "status": "RECORDED", "authority": 0,
            "span_id": "root", "data": data or {}}


def test_certify_flags_observation_without_expectation():
    from ph6_trace import TraceLog
    root = tempfile.mkdtemp(prefix="pw-cert-")
    log = TraceLog(f"{root}/trace.db")
    try:
        log.append(TRACE_KIND, "pw-cert", None,
                   _raw_event("TASK_BEGIN", "run-pw-cert"))
        log.append(TRACE_KIND, "pw-cert", None,
                   _raw_event("EVIDENCE_OBSERVED", "run-pw-cert",
                              {"expectation_id": "ev-9999",
                               "condition": "firmware", "value": "v1"}))
        log.append(TRACE_KIND, "pw-cert", None,
                   _raw_event("TASK_END", "run-pw-cert"))
        result = certify(log, "pw-cert", "run-pw-cert")
        assert result["certified"] is False
        assert result["checks"][
            "evidence_expectations_precede_observations"] is False
        assert any("evidence observations without a preceding "
                   "expectation" in g for g in result["gaps"])
    finally:
        log.close()


def test_certify_accepts_preceding_expectation_and_unanswered():
    from ph6_trace import TraceLog
    root = tempfile.mkdtemp(prefix="pw-cert-")
    log = TraceLog(f"{root}/trace.db")
    try:
        # Answered pair: expectation (seq 2, id ev-0002) precedes
        # the observation that references it.
        log.append(TRACE_KIND, "pw-cert", None,
                   _raw_event("TASK_BEGIN", "run-pw-cert"))
        log.append(TRACE_KIND, "pw-cert", None,
                   _raw_event("EVIDENCE_EXPECTATION", "run-pw-cert",
                              {"expectation_id": "ev-0002",
                               "condition": "firmware"}))
        log.append(TRACE_KIND, "pw-cert", None,
                   _raw_event("EVIDENCE_OBSERVED", "run-pw-cert",
                              {"expectation_id": "ev-0002",
                               "condition": "firmware", "value": "v1"}))
        log.append(TRACE_KIND, "pw-cert", None,
                   _raw_event("TASK_END", "run-pw-cert"))
        result = certify(log, "pw-cert", "run-pw-cert")
        assert result["certified"] is True, result["gaps"]
        assert result["checks"][
            "evidence_expectations_precede_observations"] is True
        # Unanswered: an expectation with no observation is
        # honest, not a gap.
        log2 = TraceLog(f"{root}/trace2.db")
        try:
            log2.append(TRACE_KIND, "pw-cert", None,
                        _raw_event("TASK_BEGIN", "run-pw-cert-2"))
            log2.append(TRACE_KIND, "pw-cert", None,
                        _raw_event("EVIDENCE_EXPECTATION", "run-pw-cert-2",
                                   {"expectation_id": "ev-0002",
                                    "condition": "firmware"}))
            log2.append(TRACE_KIND, "pw-cert", None,
                        _raw_event("TASK_END", "run-pw-cert-2"))
            result2 = certify(log2, "pw-cert", "run-pw-cert-2")
            assert result2["certified"] is True, result2["gaps"]
        finally:
            log2.close()
    finally:
        log.close()
