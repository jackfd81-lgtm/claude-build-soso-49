"""PATHWAY-002 (Worker 3) — replay-contradiction abstention repro.

Reproduces the PATHWAY-001 logged gap: a replayed observation
that contradicts every pointed-at procedure ends UNSOLVED with
no justified-abstention record. Design SS4 gates the fix behind
Pipeline/Harness param replay_contradiction_abstention (default
False = PATHWAY-001 behavior verbatim).

Expected results BEFORE the fix:
- test_replay_contradiction_flag_off_is_unsolved PASSES
  (documents current behavior; must keep passing after the fix).
- test_replay_contradiction_flag_on_is_justified_abstention FAILS
  (TypeError: Harness takes no replay_contradiction_abstention
  param yet).
- test_harness_passes_new_params_through FAILS (same TypeError).

Setup mirrors contract/pathway_experiment.py arm C, minimal:
one missing-evidence abstention case (pw2-me-a-abs, firmware v3
contradicts P1 and P2) plus its two learning tasks, whose
pathways are recorded into the store and replayed by a
PathwayReplayProvider.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.evidence import FrozenEvidenceSource
from contract.harness import Harness
from contract.pathway import PathwayRecorder, PathwayReplayProvider, PathwayStore
from contract.trace import trace_events


def _case(task_id, correct, evidence_value):
    return {"task_id": task_id, "signature": "me_valve_a",
            "family": "valve",
            "tags": ["valve", "stuck", "seal", "pressure"],
            "conditions": {}, "unknown_conditions": ("firmware",),
            "requested_operation": "repair",
            "provenance": ("int:pathway-002",), "correct": correct,
            "evidence_value": evidence_value}


ABS_CASE = _case("pw2-me-a-abs", None, "v3")
LEARN_1 = _case("pw2-me-a-l1", "P1", "v1")
LEARN_2 = _case("pw2-me-a-l2", "P2", "v2")


def _public(case):
    return {k: v for k, v in case.items()
            if k not in ("correct", "evidence_value")}


def _setup_replay_harness(**harness_kwargs):
    """Train the baseline, record the two learning pathways into
    the store, wire a PathwayReplayProvider, return (harness,
    abs_task_dict)."""
    tasks = [ABS_CASE, LEARN_1, LEARN_2]
    table = {(t["task_id"], "firmware"): t["evidence_value"] for t in tasks}
    h = Harness(tasks=tasks, evidence_source=FrozenEvidenceSource(table),
                **harness_kwargs)
    h.train_baseline()
    store = PathwayStore(h.pipe.big)
    recorder = PathwayRecorder()
    for t in (LEARN_1, LEARN_2):
        task_dict = _public(t)
        rep = h.pipe.run(task_dict)
        assert rep["state"] == "RESOLVED", (t["task_id"], rep)
        events = trace_events(h.pipe.trace, t["task_id"],
                              rep["execution_id"])
        record = recorder.record(
            task_dict, rep, events, root="pw2-repro",
            existing_ids={r["pathway_id"] for r in store.all()})
        assert record["discriminators"] == ["firmware"], record
        store.write(record)
    assert store.count() == 2
    h.pipe.pathway_provider = PathwayReplayProvider(store)
    return h, _public(ABS_CASE)


def _justified_abstentions(report):
    return [r for r in report["information_requests"]
            if r.get("outcome") == "justified_abstention"]


def test_replay_contradiction_flag_off_is_unsolved():
    """Current behavior (PATHWAY-001, frozen): the replayed v3
    contradicts P1/P2, the replay stops, escalate_because fires,
    and the run ends UNSOLVED with NO justified-abstention
    record. Must keep passing after the fix (flag defaults off)."""
    h, task_dict = _setup_replay_harness()
    try:
        rep = h.pipe.run(task_dict)
        assert rep["state"] == "UNSOLVED", rep
        assert rep["stage"] == "unresolved"
        assert _justified_abstentions(rep) == []
        assert rep["attempts"] == []
        # The replay did ask and did observe v3.
        replay_reqs = [r for r in rep["information_requests"]
                       if r.get("source") == "pathway-replay"]
        assert len(replay_reqs) == 1
        assert replay_reqs[0]["observation"]["firmware"]["value"] == "v3"
        assert rep["trace_certified"] is True, rep["trace_certification_gaps"]
    finally:
        h.close()


def test_replay_contradiction_flag_on_is_justified_abstention():
    """The SS4 fix: with replay_contradiction_abstention=True
    the same run must record the justified-abstention
    disposition EXACTLY as the direct U10 path does."""
    h, task_dict = _setup_replay_harness(
        replay_contradiction_abstention=True)
    try:
        rep = h.pipe.run(task_dict)
        assert rep["state"] == "ESCALATED", rep
        assert rep["stage"] == "unresolved"
        assert rep["attempts"] == [], "abstention attempts nothing"
        abstentions = _justified_abstentions(rep)
        assert len(abstentions) == 1, rep["information_requests"]
        req = abstentions[0]
        # Observation + contradicted + provenance, as the direct path.
        assert req["observation"]["firmware"]["value"] == "v3"
        assert req["observation"]["firmware"]["provenance"]["source"] == \
            "frozen-evidence"
        assert sorted(req["contradicted"]) == ["SOSO-P1", "SOSO-P2"]
        # ESCALATION trace event naming the contradiction.
        events = trace_events(h.pipe.trace, task_dict["task_id"],
                              rep["execution_id"])
        escalations = [(seq, ev) for seq, ev in events
                       if ev["event_type"] == "ESCALATION"]
        assert escalations, "no ESCALATION event"
        assert any(
            "contradict" in str((ev.get("data") or {}).get("reason", "")).lower()
            and sorted((ev.get("data") or {}).get("contradicted") or [])
            == ["SOSO-P1", "SOSO-P2"]
            for _seq, ev in escalations), escalations
        # INFORMATION_REQUEST trace entry present.
        assert any(ev["event_type"] == "INFORMATION_REQUEST"
                   for _seq, ev in events), "no INFORMATION_REQUEST event"
        assert rep["trace_certified"] is True, rep["trace_certification_gaps"]
    finally:
        h.close()


def test_harness_passes_new_params_through():
    """Harness plumbing (pass-through only): the three new
    params reach the pipeline."""
    from contract.selection import MeasurementSelector
    src = FrozenEvidenceSource({})
    selector = MeasurementSelector(sources=[src])
    costs = {("frozen-evidence", "firmware"): 2}
    h = Harness(measurement_selector=selector,
                measurement_costs=costs,
                replay_contradiction_abstention=True)
    try:
        assert h.pipe.measurement_selector is selector
        assert h.pipe.measurement_costs == costs
        assert h.pipe.replay_contradiction_abstention is True
    finally:
        h.close()
    # Defaults preserve PATHWAY-001 behavior verbatim.
    h2 = Harness()
    try:
        assert h2.pipe.measurement_selector is None
        assert h2.pipe.measurement_costs is None
        assert h2.pipe.replay_contradiction_abstention is False
    finally:
        h2.close()
