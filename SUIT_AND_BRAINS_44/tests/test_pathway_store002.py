"""PATHWAY-002 (pathway-store half) — tests for the v2 transferable
investigative METHOD in contract/pathway.py (design §3).

Step 1 of the brief: these tests are written FIRST, against the
v1 recorder, and the gap tests MUST FAIL before the fix (the v1
recorder does not preserve hypotheses / measurements / branching /
corrections, has no method_for(), and encodes v1 only).

In-memory v2 shapes (the contract the implementation must meet):
- conditions: {"known": {cond: value}, "unknown": [cond, ...]}
  from the source task's conditions + unknown_conditions.
- hypotheses: [{"condition", "hypotheses": [{"procedure": native,
  "requires": value}]}] — one entry per evidence expectation,
  in expectation order; procedure ids in NATIVE short form.
- measurements: [{"condition", "source", "distinguishes": [native],
  "expected_classes": [...], "implications": {class: {"admits",
  "contradicts", "ranks_up", "ranks_down": [native]}}}]
  — implication values are code lists ONLY; the natural-language
  "next" sentences from the expectation are NEVER stored.
- branching: [{"step": "ask:<cond>" | "try:<native>", "decision",
  "reason"}] — the investigation sequence: evidence asks and
  attempts interleaved in trace-seq order, decisions from the
  report's information_requests outcomes (asks) and the attempt
  validations (tries).
- corrections: [{"procedure": native, "previous", "new", "reason",
  "validation"}] — one entry per CORRECTION event, in seq order;
  "validation" is the validation recorded in the event's evidence
  (None when the event carries none).
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from contract.harness import Harness  # noqa: E402 (inserts RealCog path)
from ph6_brains_2208 import Big49  # noqa: E402
from contract.evidence import FrozenEvidenceSource  # noqa: E402
from contract.pathway import (  # noqa: E402
    PathwayRecorder, PathwayReplayProvider, PathwayStore,
    ShuffledPathwayProvider, decode_record, encode_record,
    validate_record, MAX_CONTENT_CHARS)
from contract.task import canon  # noqa: E402
from contract.trace import trace_events  # noqa: E402

V2_KEYS = ("conditions", "hypotheses", "measurements", "branching",
           "corrections")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _record(task_id="ct-pw-a", seq=12, signature="disk_full",
            family="valve", tags=("valve", "stuck"),
            discriminators=("firmware",), evidence_requests=None,
            attempts=(("SOSO-P2", True, "PASS"),),
            state="RESOLVED", stage="higher-trial", selected="SOSO-P2"):
    """A v1 record (11 keys), mirroring tests/test_pathway_store.py."""
    if evidence_requests is None:
        evidence_requests = [{
            "condition": "firmware", "expectation_id": "ev-0010",
            "expectation_seq": 10, "observation_seq": 11,
            "observation": "v2",
            "provenance": {"source": "src-test", "task_id": task_id,
                           "condition": "firmware"},
            "outcome": "admitted SOSO-P2"}]
    return {"pathway_id": f"pathway.{task_id}.{seq}",
            "source_task_id": task_id,
            "source_execution_id": f"run-{task_id}-01234567",
            "signature": signature, "family": family,
            "tags": sorted(tags), "discriminators": list(discriminators),
            "evidence_requests": evidence_requests,
            "attempts": [{"procedure": p, "observed": o, "validation": v}
                         for p, o, v in attempts],
            "outcome": {"state": state, "stage": stage,
                        "selected": selected},
            "root": "/tmp/pw-test"}


def _v2_record(task_id="ct-pw-a", seq=12, **kw):
    """A v2 record (16 keys): the v1 record plus the 5 method fields."""
    rec = _record(task_id, seq=seq, **kw)
    rec["conditions"] = {"known": {"firmware": "v2"}, "unknown": []}
    rec["hypotheses"] = [{
        "condition": "firmware",
        "hypotheses": [{"procedure": "P1", "requires": "v1"},
                       {"procedure": "P2", "requires": "v2"}]}]
    rec["measurements"] = [{
        "condition": "firmware", "source": "src-test",
        "distinguishes": ["P1", "P2"],
        "expected_classes": ["v1", "v2", "other"],
        "implications": {
            "v1": {"admits": ["P1"], "contradicts": ["P2"],
                   "ranks_up": [], "ranks_down": []},
            "v2": {"admits": ["P2"], "contradicts": ["P1"],
                   "ranks_up": [], "ranks_down": []},
            "other": {"admits": [], "contradicts": ["P1", "P2"],
                      "ranks_up": [], "ranks_down": []}}}]
    rec["branching"] = [
        {"step": "ask:firmware", "decision": "admitted SOSO-P2",
         "reason": "observation v2"},
        {"step": "try:P2", "decision": "PASS", "reason": "observed"}]
    rec["corrections"] = []
    return rec


def _store(tmpdir=None):
    tmpdir = tmpdir or tempfile.mkdtemp(prefix="pw2-store-")
    return PathwayStore(Big49(os.path.join(tmpdir, "big.db")))


def _ev(seq, etype, data, task_id="ct-pw-a",
        execution_id="run-ct-pw-a-01234567", **extra):
    event = {"event_type": etype, "data": data,
             "execution_id": execution_id,
             "event_id": f"ev-{seq:04d}", "sequence": seq,
             "task_id": task_id}
    event.update(extra)
    return (seq, event)


def _hand_events(task_id="ct-pw-a",
                 execution_id="run-ct-pw-a-01234567",
                 with_correction=True):
    """Hand trace: one full EVIDENCE_EXPECTATION (hypotheses,
    predicted classes, implications WITH natural-language "next"
    sentences, source, measurement), its observation, one attempt,
    one CORRECTION event, TASK_END."""
    events = [
        _ev(1, "TASK_BEGIN", {"task_id": task_id}, task_id, execution_id),
        _ev(10, "EVIDENCE_EXPECTATION", {
            "expectation_id": "ev-0010", "condition": "firmware",
            "hypotheses": [{"procedure": "SOSO-P2", "requires": "v2"},
                           {"procedure": "SOSO-P1", "requires": "v1"}],
            "predicted_classes": ["v1", "v2", "other"],
            "implications": {
                "v2": {"admits": ["SOSO-P2"], "contradicts": ["SOSO-P1"],
                       "next": "admit P2"},
                "v1": {"admits": ["SOSO-P1"], "contradicts": ["SOSO-P2"],
                       "next": "admit P1"},
                "other": {"admits": [], "contradicts": ["SOSO-P1",
                                                         "SOSO-P2"],
                          "next": ("justified abstention: escalate with "
                                   "the observation recorded")}},
            "source": "src-test",
            "measurement": "what firmware version is installed?"},
            task_id, execution_id),
        _ev(11, "EVIDENCE_OBSERVED", {
            "expectation_id": "ev-0010", "condition": "firmware",
            "value": "v2",
            "provenance": {"source": "src-test", "task_id": task_id,
                           "condition": "firmware"}}, task_id, execution_id),
        _ev(20, "OBSERVATION", {"procedure": "SOSO-P2", "observed": True},
            task_id, execution_id),
        _ev(21, "VALIDATION", {"procedure": "SOSO-P2", "validation": "PASS"},
            task_id, execution_id),
    ]
    if with_correction:
        events.append(_ev(
            22, "CORRECTION",
            {"procedure": "SOSO-P2", "previous_qualification": "QUALIFIED",
             "new_qualification": "INCONCLUSIVE",
             "reason": "trusted procedure failed",
             "evidence": {"demonstration_task": task_id,
                          "demonstration_signature": "disk_full",
                          "observed": False, "validation": "FAIL"}},
            task_id, execution_id, span_id="span-L21-abcdef"))
    events.append(_ev(30, "TASK_END", {"outcome": "RESOLVED",
                                       "selected": "SOSO-P2",
                                       "stage": "higher-trial"},
                      task_id, execution_id))
    return events


def _hand_report(task_id="ct-pw-a",
                 execution_id="run-ct-pw-a-01234567"):
    return {"task_id": task_id, "execution_id": execution_id,
            "state": "RESOLVED", "stage": "higher-trial",
            "selected": "SOSO-P2",
            "attempts": [{"procedure": "SOSO-P2", "native": "P2",
                          "observed": True, "validation": "PASS",
                          "qualification_after": "CANDIDATE"}],
            "information_requests": [{
                "missing": "firmware",
                "question": "what firmware version is installed?",
                "procedures": ["SOSO-P2", "SOSO-P1"],
                "source": "targeted_investigation",
                "expectation_id": "ev-0010", "observation": "v2",
                "provenance": {"source": "src-test", "task_id": task_id,
                               "condition": "firmware"},
                "outcome": "admitted SOSO-P2"}],
            "rejected": [], "proposed": [], "unattempted": [],
            "pathway_cost": {}}


def _task(task_id="ct-pw-a", signature="disk_full", family="valve",
          tags=("valve", "stuck"), conditions=None, unknown=()):
    return {"task_id": task_id, "signature": signature, "family": family,
            "tags": list(tags),
            "conditions": {} if conditions is None else dict(conditions),
            "unknown_conditions": tuple(unknown),
            "requested_operation": "repair",
            "provenance": ("int:contract-training",)}


# ------------------------------------------------------------------
# GAP TESTS — must FAIL before the fix, PASS after it
# ------------------------------------------------------------------

def test_gap_recorder_preserves_full_method():
    """The v1 recorder reads only condition + expectation_id from
    EVIDENCE_EXPECTATION and ignores hypotheses / predicted
    classes / implications / CORRECTION events / branching order.
    The v2 recorder must preserve all of them."""
    rec = PathwayRecorder().record(
        _task(unknown=("firmware",)), _hand_report(), _hand_events(),
        "/tmp/pw-test")
    assert set(rec) == {"pathway_id", "source_task_id",
                        "source_execution_id", "signature", "family",
                        "tags", "discriminators", "evidence_requests",
                        "attempts", "outcome", "root"} | set(V2_KEYS)
    assert rec["conditions"] == {"known": {}, "unknown": ["firmware"]}
    # Hypotheses are preserved AS IN the expectation (P2 first in
    # this hand event), in native short form — the recorder does
    # not re-sort them.
    assert rec["hypotheses"] == [{
        "condition": "firmware",
        "hypotheses": [{"procedure": "P2", "requires": "v2"},
                       {"procedure": "P1", "requires": "v1"}]}]
    assert rec["measurements"] == [{
        "condition": "firmware", "source": "src-test",
        "distinguishes": ["P2", "P1"],
        "expected_classes": ["v1", "v2", "other"],
        "implications": {
            "v1": {"admits": ["P1"], "contradicts": ["P2"],
                   "ranks_up": [], "ranks_down": []},
            "v2": {"admits": ["P2"], "contradicts": ["P1"],
                   "ranks_up": [], "ranks_down": []},
            "other": {"admits": [], "contradicts": ["P1", "P2"],
                      "ranks_up": [], "ranks_down": []}}}]
    assert rec["branching"] == [
        {"step": "ask:firmware", "decision": "admitted SOSO-P2",
         "reason": "observation v2"},
        {"step": "try:P2", "decision": "PASS", "reason": "observed"}]
    assert rec["corrections"] == [{
        "procedure": "P2", "previous": "QUALIFIED",
        "new": "INCONCLUSIVE", "reason": "trusted procedure failed",
        "validation": "FAIL"}]
    assert validate_record(rec) == []
    # The recorder never reads the validator / criteria: no
    # evaluator-owned key leaks into the record, and no
    # natural-language implication sentence is stored.
    assert "correct" not in rec
    assert "next" not in json.dumps(rec["measurements"])
    assert "admit P2" not in json.dumps(rec["measurements"])


def test_gap_method_for_returns_full_record():
    """method_for(task) is new in PATHWAY-002: the FULL matched
    record (not just discriminators) for the selector, or None."""
    store = _store()
    rec = _v2_record("ct-pw-a", seq=12)
    store.write(rec)
    replay = PathwayReplayProvider(store)
    got = replay.method_for(_task())
    assert got is not None and got == rec
    assert got["measurements"][0]["distinguishes"] == ["P1", "P2"]
    assert replay.method_for(
        _task(tags=("sensor", "drift"))) is None
    # Mutating the returned record must not corrupt the store.
    got["tags"].append("tampered")
    assert replay.method_for(_task()) == rec

    shuffled = ShuffledPathwayProvider(
        store, [_task("ct-pw-a"), _task("ct-pw-b", tags=("valve", "jammed"))],
        seed=20261118)
    store.write(_v2_record("ct-pw-b", seq=15, tags=("valve", "jammed")))
    shuffled = ShuffledPathwayProvider(
        store, [_task("ct-pw-a"), _task("ct-pw-b", tags=("valve", "jammed"))],
        seed=20261118)
    assoc = shuffled.method_for(_task("ct-pw-a"))
    assert assoc is not None
    assert assoc["source_task_id"] == "ct-pw-b"
    assert assoc["measurements"][0]["distinguishes"] == ["P1", "P2"]
    assert shuffled.method_for(
        _task("ct-pw-z", "cache_big", "blocked",
              ("route", "blocked"))) is None


# ------------------------------------------------------------------
# Recorder: branching order + corrections
# ------------------------------------------------------------------

def _interleaved_events():
    """Two expectations and two attempts interleaved by seq:
    ask firmware (10/11), try P2 (20/21), ask kind (25/26),
    try P7 (30/31), TASK_END (40)."""
    task_id = "ct-pw-a"
    execution_id = "run-ct-pw-a-01234567"
    events = [
        _ev(1, "TASK_BEGIN", {"task_id": task_id}, task_id, execution_id),
        _ev(10, "EVIDENCE_EXPECTATION", {
            "expectation_id": "ev-0010", "condition": "firmware",
            "hypotheses": [{"procedure": "SOSO-P2", "requires": "v2"},
                           {"procedure": "SOSO-P1", "requires": "v1"}],
            "predicted_classes": ["v1", "v2", "other"],
            "implications": {
                "v2": {"admits": ["SOSO-P2"], "contradicts": ["SOSO-P1"],
                       "next": "admit P2"},
                "v1": {"admits": ["SOSO-P1"], "contradicts": ["SOSO-P2"],
                       "next": "admit P1"},
                "other": {"admits": [], "contradicts": ["SOSO-P1",
                                                         "SOSO-P2"],
                          "next": "abstain"}},
            "source": "src-test",
            "measurement": "what firmware version is installed?"},
            task_id, execution_id),
        _ev(11, "EVIDENCE_OBSERVED", {
            "expectation_id": "ev-0010", "condition": "firmware",
            "value": "v2",
            "provenance": {"source": "src-test", "task_id": task_id,
                           "condition": "firmware"}}, task_id, execution_id),
        _ev(20, "OBSERVATION", {"procedure": "SOSO-P2", "observed": True},
            task_id, execution_id),
        _ev(21, "VALIDATION", {"procedure": "SOSO-P2", "validation": "PASS"},
            task_id, execution_id),
        _ev(25, "EVIDENCE_EXPECTATION", {
            "expectation_id": "ev-0025", "condition": "kind",
            "hypotheses": [{"procedure": "SOSO-P7", "requires": "detour"}],
            "predicted_classes": ["detour", "other"],
            "implications": {
                "detour": {"admits": ["SOSO-P7"], "contradicts": [],
                           "next": "admit P7"},
                "other": {"admits": [], "contradicts": ["SOSO-P7"],
                          "next": "abstain"}},
            "source": "src-test", "measurement": "what kind?"},
            task_id, execution_id),
        _ev(26, "EVIDENCE_OBSERVED", {
            "expectation_id": "ev-0025", "condition": "kind",
            "value": "detour",
            "provenance": {"source": "src-test", "task_id": task_id,
                           "condition": "kind"}}, task_id, execution_id),
        _ev(30, "OBSERVATION", {"procedure": "SOSO-P7", "observed": True},
            task_id, execution_id),
        _ev(31, "VALIDATION", {"procedure": "SOSO-P7", "validation": "PASS"},
            task_id, execution_id),
        _ev(40, "TASK_END", {"outcome": "RESOLVED",
                             "selected": "SOSO-P7",
                             "stage": "higher-trial"},
            task_id, execution_id),
    ]
    return events


def _interleaved_report():
    report = _hand_report()
    report["selected"] = "SOSO-P7"
    report["attempts"].append(
        {"procedure": "SOSO-P7", "native": "P7", "observed": True,
         "validation": "PASS", "qualification_after": "CANDIDATE"})
    report["information_requests"].append({
        "missing": "kind", "question": "what kind?",
        "procedures": ["SOSO-P7"], "source": "targeted_investigation",
        "expectation_id": "ev-0025", "observation": "detour",
        "provenance": {"source": "src-test", "task_id": "ct-pw-a",
                       "condition": "kind"},
        "outcome": "resumed"})
    return report


def test_recorder_branching_interleaves_asks_and_attempts_in_seq_order():
    rec = PathwayRecorder().record(
        _task(unknown=("firmware", "kind")), _interleaved_report(),
        _interleaved_events(), "/tmp/pw-test")
    assert rec["discriminators"] == ["firmware", "kind"]
    assert rec["conditions"] == {"known": {},
                                 "unknown": ["firmware", "kind"]}
    assert [h["condition"] for h in rec["hypotheses"]] == [
        "firmware", "kind"]
    assert [m["condition"] for m in rec["measurements"]] == [
        "firmware", "kind"]
    assert rec["measurements"][1] == {
        "condition": "kind", "source": "src-test",
        "distinguishes": ["P7"],
        "expected_classes": ["detour", "other"],
        "implications": {
            "detour": {"admits": ["P7"], "contradicts": [],
                       "ranks_up": [], "ranks_down": []},
            "other": {"admits": [], "contradicts": ["P7"],
                      "ranks_up": [], "ranks_down": []}}}
    assert rec["branching"] == [
        {"step": "ask:firmware", "decision": "admitted SOSO-P2",
         "reason": "observation v2"},
        {"step": "try:P2", "decision": "PASS", "reason": "observed"},
        {"step": "ask:kind", "decision": "resumed",
         "reason": "observation detour"},
        {"step": "try:P7", "decision": "PASS", "reason": "observed"}]
    assert rec["corrections"] == []
    assert validate_record(rec) == []


def test_recorder_unanswered_expectation_branching_and_no_correction():
    events = _hand_events(with_correction=False)
    events = [e for e in events if e[1]["event_type"] != "EVIDENCE_OBSERVED"]
    rec = PathwayRecorder().record(
        _task(unknown=("firmware",)), _hand_report(), events,
        "/tmp/pw-test")
    assert rec["evidence_requests"][0]["observation"] is None
    assert rec["branching"][0] == {
        "step": "ask:firmware", "decision": "admitted SOSO-P2",
        "reason": "no observation"}
    assert rec["corrections"] == []
    assert validate_record(rec) == []


# ------------------------------------------------------------------
# Encoding: v1 preserved, v2 round-trip, validation, cap
# ------------------------------------------------------------------

def test_v1_records_still_encode_v1():
    rec = _record()
    assert validate_record(rec) == []
    block = encode_record(rec)
    assert block["v"] == 1
    assert decode_record(block) == rec
    assert decode_record(canon(block)) == rec
    assert len(canon(block)) <= MAX_CONTENT_CHARS


def test_v2_round_trip_and_fit():
    rec = _v2_record()
    assert validate_record(rec) == []
    block = encode_record(rec)
    assert block["v"] == 2
    assert decode_record(block) == rec
    assert decode_record(canon(block)) == rec
    assert len(canon(block)) <= MAX_CONTENT_CHARS
    # The store round-trips the full method, not just the v1 keys.
    store = _store()
    assert store.write(rec) == "pathway.ct-pw-a.12"
    assert store.get("pathway.ct-pw-a.12") == rec
    assert store.find("disk_full", "valve", ["stuck"]) == rec


def test_v2_round_trip_with_deviating_classes_and_ranks():
    """Implications / classes that do NOT follow from the
    hypotheses alone (reordered classes, a non-empty ranks_up)
    must be stored explicitly and round-trip exactly — the
    compact form may only derive what the derivation proves."""
    rec = _v2_record()
    rec["measurements"][0]["expected_classes"] = ["v2", "v1", "other"]
    rec["measurements"][0]["implications"]["v1"]["ranks_up"] = ["P2"]
    assert validate_record(rec) == []
    block = encode_record(rec)
    assert block["v"] == 2
    assert decode_record(block) == rec
    assert decode_record(canon(block)) == rec
    assert len(canon(block)) <= MAX_CONTENT_CHARS


def test_validate_record_v1_v2_unknown_keys_and_partial_v2():
    assert validate_record(_record()) == []
    assert validate_record(_v2_record()) == []
    # An unknown key is still a problem, in either version.
    bad = _v2_record()
    bad["surprise"] = 1
    assert any("unexpected key" in p for p in validate_record(bad))
    bad1 = _record()
    bad1["surprise"] = 1
    assert any("unexpected key" in p for p in validate_record(bad1))
    # v2 is all-or-none: a record carrying only SOME of the 5 new
    # keys is not a valid v1 record and not a valid v2 record.
    partial = _record()
    partial["conditions"] = {"known": {}, "unknown": []}
    assert validate_record(partial) != []
    partial2 = _v2_record()
    del partial2["corrections"]
    assert validate_record(partial2) != []
    # Unknown procedure ids in the new fields raise, as for
    # attempts / outcome.selected.
    bad_proc = _v2_record()
    bad_proc["hypotheses"][0]["hypotheses"][0]["procedure"] = "SOSO-P99"
    with pytest.raises(KeyError):
        validate_record(bad_proc)
    bad_impl = _v2_record()
    bad_impl["measurements"][0]["implications"]["v1"]["admits"] = ["P99"]
    with pytest.raises(KeyError):
        validate_record(bad_impl)
    # An implication naming a procedure outside the measurement's
    # hypotheses is a problem (the compact code lists are indices
    # into the hypotheses; an outsider cannot be encoded).
    outsider = _v2_record()
    outsider["measurements"][0]["implications"]["v1"]["admits"] = ["P7"]
    assert validate_record(outsider) != []
    # Branching must interleave the evidence asks and the attempts
    # in order: a try step naming the wrong procedure is a problem.
    bad_branch = _v2_record()
    bad_branch["branching"][1]["step"] = "try:P7"
    assert validate_record(bad_branch) != []


def test_decode_record_rejects_unknown_versions():
    with pytest.raises(ValueError, match="unknown pathway block version"):
        decode_record({"v": 3, "pid": "pathway.ct-pw-a.12"})
    with pytest.raises(ValueError, match="unknown pathway block version"):
        decode_record({"v": 0})
    with pytest.raises(ValueError, match="unknown pathway block version"):
        decode_record({})
    with pytest.raises(ValueError, match="unknown pathway block version"):
        decode_record(canon({"v": 99}))


def test_v2_over_cap_raises_never_truncates():
    store = _store()
    huge = _v2_record()
    huge["tags"] = [f"tag-{i:03d}-{'x' * 40}" for i in range(20)]
    assert validate_record(huge) == []
    assert len(canon(encode_record(huge))) > MAX_CONTENT_CHARS
    with pytest.raises(ValueError, match="too large"):
        store.write(huge)
    assert store.count() == 0


# ------------------------------------------------------------------
# Recorder: end-to-end from a REAL pipeline run (evidence task)
# ------------------------------------------------------------------

def test_recorder_end_to_end_real_evidence_run_fits():
    """One learning-style run through the real pipeline: unknown
    firmware answered from the frozen source, then trial. The
    recorded v2 method must fit the 512-char bucket and store."""
    task = {"task_id": "pw2-t", "signature": "disk_full",
            "family": "valve",
            "tags": ["valve", "firmware_v2", "stuck"],
            "conditions": {}, "unknown_conditions": ("firmware",),
            "requested_operation": "repair",
            "provenance": ("int:contract-training",), "correct": "P2"}
    source = FrozenEvidenceSource({("pw2-t", "firmware"): "v2"})
    h = Harness(tasks=[task], evidence_source=source)
    try:
        public = {k: v for k, v in task.items() if k != "correct"}
        report = h.pipe.run(public)
        events = trace_events(h.pipe.trace, "pw2-t", report["execution_id"])
        rec = PathwayRecorder().record(public, report, events, h.root)
        assert rec["discriminators"] == ["firmware"]
        assert len(rec["evidence_requests"]) == 1
        assert rec["conditions"] == {"known": {}, "unknown": ["firmware"]}
        assert rec["hypotheses"] == [{
            "condition": "firmware",
            "hypotheses": [{"procedure": "P1", "requires": "v1"},
                           {"procedure": "P2", "requires": "v2"}]}]
        assert rec["measurements"][0]["condition"] == "firmware"
        assert rec["measurements"][0]["source"] == "frozen-evidence"
        assert rec["measurements"][0]["distinguishes"] == ["P1", "P2"]
        assert rec["measurements"][0]["expected_classes"] == [
            "v1", "v2", "other"]
        assert rec["branching"][0]["step"] == "ask:firmware"
        assert rec["branching"][0]["decision"] in (
            "resumed", "justified_abstention", "unanswered")
        assert rec["corrections"] == []
        assert validate_record(rec) == []
        block = encode_record(rec)
        assert block["v"] == 2
        assert len(canon(block)) <= MAX_CONTENT_CHARS
        assert decode_record(block) == rec
        store = PathwayStore(h.pipe.big)
        pid = store.write(rec)
        assert pid == rec["pathway_id"]
        assert store.get(pid) == rec
        assert store.find("disk_full", "valve", ["valve"]) == rec
        provider = PathwayReplayProvider(store)
        assert provider.method_for(public) == rec
    finally:
        h.close()
