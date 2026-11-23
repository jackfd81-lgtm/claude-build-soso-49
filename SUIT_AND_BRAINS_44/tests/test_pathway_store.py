"""PATHWAY-001 (pathway-store half) — tests for contract/pathway.py.

These tests do NOT depend on the pipeline's evidence machinery
(built in parallel): the hand-built trace cases use the brief's
EVIDENCE_* payload shapes directly, and the one live-pipeline
case is a plain trial task with no evidence source — it honestly
has no evidence requests and discriminators [].
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from contract.harness import Harness  # noqa: E402 (inserts RealCog path)
from ph6_brains_2208 import Big49  # noqa: E402
from contract.pathway import (  # noqa: E402
    PathwayRecorder, PathwayReplayProvider, PathwayStore,
    ShuffledPathwayProvider, validate_record)
from contract.task import canon  # noqa: E402
from contract.trace import trace_events  # noqa: E402


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _record(task_id="ct-pw-a", seq=12, signature="disk_full",
            family="valve", tags=("valve", "stuck"),
            discriminators=("firmware",), evidence_requests=None,
            attempts=(("SOSO-P2", True, "PASS"),),
            state="RESOLVED", stage="higher-trial", selected="SOSO-P2"):
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


def _store(tmpdir=None):
    tmpdir = tmpdir or tempfile.mkdtemp(prefix="pw-store-")
    return PathwayStore(Big49(os.path.join(tmpdir, "big.db")))


def _hand_events(task_id="ct-pw-a", execution_id="run-ct-pw-a-01234567",
                 with_observation=True, attempt=("SOSO-P2", True, "PASS")):
    """(seq, event) pairs in the shape trace_events() returns."""
    def ev(seq, etype, data):
        return (seq, {"event_type": etype, "data": data,
                      "execution_id": execution_id,
                      "event_id": f"ev-{seq:04d}", "sequence": seq,
                      "task_id": task_id})
    events = [
        ev(1, "TASK_BEGIN", {"task_id": task_id}),
        ev(10, "EVIDENCE_EXPECTATION", {
            "expectation_id": "ev-0010", "condition": "firmware",
            "hypotheses": [{"procedure": "SOSO-P2", "requires": "v2"},
                           {"procedure": "SOSO-P1", "requires": "v1"}],
            "predicted_classes": ["v1", "v2", "other"],
            "implications": {
                "v2": {"admits": ["SOSO-P2"], "contradicts": ["SOSO-P1"],
                       "next": "admit P2"},
                "v1": {"admits": ["SOSO-P1"], "contradicts": ["SOSO-P2"],
                       "next": "admit P1"}},
            "source": "src-test",
            "measurement": "what firmware version is installed?"}),
    ]
    if with_observation:
        events.append(ev(11, "EVIDENCE_OBSERVED", {
            "expectation_id": "ev-0010", "condition": "firmware",
            "value": "v2",
            "provenance": {"source": "src-test", "task_id": task_id,
                           "condition": "firmware"}}))
    proc, observed, validation = attempt
    events += [
        ev(20, "OBSERVATION", {"procedure": proc, "observed": observed}),
        ev(21, "VALIDATION", {"procedure": proc, "validation": validation}),
        ev(30, "TASK_END", {"outcome": "RESOLVED", "selected": proc,
                            "stage": "higher-trial"}),
    ]
    return events


def _hand_report(task_id="ct-pw-a", execution_id="run-ct-pw-a-01234567",
                 attempt=("SOSO-P2", True, "PASS"),
                 with_info_outcome=True):
    proc, observed, validation = attempt
    info = {"missing": "firmware",
            "question": "what firmware version is installed?",
            "procedures": ["SOSO-P2", "SOSO-P1"],
            "source": "targeted_investigation"}
    if with_info_outcome:
        info.update({"expectation_id": "ev-0010", "observation": "v2",
                     "provenance": {"source": "src-test",
                                    "task_id": task_id,
                                    "condition": "firmware"},
                     "outcome": "admitted SOSO-P2"})
    return {"task_id": task_id, "execution_id": execution_id,
            "state": "RESOLVED", "stage": "higher-trial",
            "selected": proc,
            "attempts": [{"procedure": proc, "native": "P2",
                          "observed": observed, "validation": validation,
                          "qualification_after": "CANDIDATE"}],
            "information_requests": [info],
            "rejected": [], "proposed": [], "unattempted": [],
            "pathway_cost": {}}


def _task(task_id="ct-pw-a", signature="disk_full", family="valve",
          tags=("valve", "stuck")):
    return {"task_id": task_id, "signature": signature, "family": family,
            "tags": list(tags), "conditions": {"firmware": "v2"},
            "requested_operation": "repair",
            "provenance": ("int:contract-training",)}


# ------------------------------------------------------------------
# Store: write / read / find
# ------------------------------------------------------------------

def test_store_write_read_round_trip_two_records():
    store = _store()
    r1 = _record("ct-pw-a", seq=12)
    r2 = _record("ct-pw-b", seq=15, tags=("valve", "jammed"))
    assert validate_record(r1) == []
    assert validate_record(r2) == []
    assert store.write(r1) == "pathway.ct-pw-a.12"
    assert store.write(r2) == "pathway.ct-pw-b.15"
    assert store.count() == 2
    got = store.all()
    assert [r["pathway_id"] for r in got] == [
        "pathway.ct-pw-a.12", "pathway.ct-pw-b.15"]
    assert got[0] == r1
    assert got[1] == r2
    assert store.get("pathway.ct-pw-a.12") == r1
    assert store.get("pathway.nope.1") is None
    # The full-key form is ~615 chars, over the 512-char bucket;
    # the compact block encoding is what fits (see pathway.py).
    from contract.pathway import decode_record, encode_record
    assert len(canon(r1)) > 512
    assert len(canon(encode_record(r1))) <= 512
    assert len(canon(encode_record(r2))) <= 512
    assert decode_record(encode_record(r1)) == r1
    assert decode_record(canon(encode_record(r1))) == r1


def test_store_write_duplicate_id_raises_first_write_wins():
    # Big49 blocks are write-once: the brief's "overwrite" cannot
    # be built on this store, so a duplicate raises and the first
    # write stands (see pathway.py module docstring).
    store = _store()
    rec = _record("ct-pw-a", seq=12)
    store.write(rec)
    with pytest.raises(ValueError):
        store.write(rec)
    assert store.count() == 1
    assert store.get("pathway.ct-pw-a.12") == rec


def test_store_write_invalid_and_oversize_raise():
    store = _store()
    bad = _record("ct-pw-a", seq=12)
    bad["discriminators"] = []
    with pytest.raises(ValueError):
        store.write(bad)
    huge = _record("ct-pw-a", seq=12)
    huge["tags"] = [f"tag-{i:03d}-{'x' * 40}" for i in range(20)]
    assert len(canon(huge)) > 512
    with pytest.raises(ValueError):
        store.write(huge)
    assert store.count() == 0


def test_find_tag_gate():
    store = _store()
    store.write(_record("ct-pw-a", seq=12, tags=("valve", "stuck")))
    # No shared tag -> None (the HARDEN-001 tag gate, applied to
    # pathways), even with an exact (signature, family) match.
    assert store.find("disk_full", "valve", ["sensor", "drift"]) is None
    # One shared tag -> the record.
    got = store.find("disk_full", "valve", ["stuck", "other"])
    assert got is not None and got["pathway_id"] == "pathway.ct-pw-a.12"
    # (signature, family) mismatch -> None, even with shared tags.
    assert store.find("svc_hung", "valve", ["valve", "stuck"]) is None
    assert store.find("disk_full", "sensor", ["valve", "stuck"]) is None


def test_find_tie_break_most_shared_tags_then_earliest_id():
    store = _store()
    store.write(_record("ct-pw-a", seq=12, tags=("valve", "stuck")))
    store.write(_record("ct-pw-b", seq=15, tags=("valve", "stuck", "flow")))
    # Two shared tags beat one.
    got = store.find("disk_full", "valve", ["valve", "stuck", "flow"])
    assert got["pathway_id"] == "pathway.ct-pw-b.15"
    # Tie on shared tags -> earliest pathway_id.
    got = store.find("disk_full", "valve", ["valve", "stuck"])
    assert got["pathway_id"] == "pathway.ct-pw-a.12"


# ------------------------------------------------------------------
# Recorder: hand-built trace + report
# ------------------------------------------------------------------

def test_recorder_builds_correct_record_from_hand_built_trace():
    rec = PathwayRecorder().record(
        _task(), _hand_report(), _hand_events(), "/tmp/pw-test")
    # PATHWAY-002 §3: the recorder now returns the v2 record — the 11
    # v1 keys plus the 5 additive method fields (conditions, hypotheses,
    # measurements, branching, corrections). Coordinator-authorized
    # update of this key-set literal only; every other assertion in
    # this test is unchanged, and the v1 field values below are
    # identical to before.
    assert set(rec) == {"pathway_id", "source_task_id",
                        "source_execution_id", "signature", "family",
                        "tags", "discriminators", "evidence_requests",
                        "attempts", "outcome", "root",
                        "conditions", "hypotheses", "measurements",
                        "branching", "corrections"}
    assert rec["pathway_id"] == "pathway.ct-pw-a.30"
    assert rec["source_task_id"] == "ct-pw-a"
    assert rec["source_execution_id"] == "run-ct-pw-a-01234567"
    assert rec["signature"] == "disk_full"
    assert rec["family"] == "valve"
    assert rec["tags"] == ["stuck", "valve"]
    assert rec["discriminators"] == ["firmware"]
    assert rec["evidence_requests"] == [{
        "condition": "firmware", "expectation_id": "ev-0010",
        "expectation_seq": 10, "observation_seq": 11,
        "observation": "v2",
        "provenance": {"source": "src-test", "task_id": "ct-pw-a",
                       "condition": "firmware"},
        "outcome": "admitted SOSO-P2"}]
    assert rec["attempts"] == [{"procedure": "SOSO-P2", "observed": True,
                                "validation": "PASS"}]
    assert rec["outcome"] == {"state": "RESOLVED", "stage": "higher-trial",
                              "selected": "SOSO-P2"}
    assert rec["root"] == "/tmp/pw-test"
    assert validate_record(rec) == []


def test_recorder_unanswered_expectation_is_honest_data():
    events = _hand_events(with_observation=False)
    rec = PathwayRecorder().record(_task(), _hand_report(), events,
                                   "/tmp/pw-test")
    assert rec["discriminators"] == ["firmware"]
    req = rec["evidence_requests"][0]
    assert req["observation"] is None
    assert req["observation_seq"] is None
    assert req["provenance"] is None
    assert req["expectation_seq"] == 10
    assert validate_record(rec) == []


def test_recorder_report_trace_attempt_mismatch_raises():
    report = _hand_report()
    report["attempts"][0]["validation"] = "FAIL"
    with pytest.raises(ValueError, match="attempt mismatch"):
        PathwayRecorder().record(_task(), report, _hand_events(),
                                 "/tmp/pw-test")
    # Trace-only divergence: an extra OBSERVATION with no report attempt.
    events = _hand_events() + [
        (22, {"event_type": "OBSERVATION",
              "data": {"procedure": "SOSO-P3", "observed": False},
              "execution_id": "run-ct-pw-a-01234567",
              "event_id": "ev-0022", "sequence": 22,
              "task_id": "ct-pw-a"})]
    with pytest.raises(ValueError, match="attempt mismatch"):
        PathwayRecorder().record(_task(), _hand_report(), events,
                                 "/tmp/pw-test")


def test_recorder_no_task_end_raises():
    events = [e for e in _hand_events() if e[1]["event_type"] != "TASK_END"]
    with pytest.raises(ValueError, match="no TASK_END"):
        PathwayRecorder().record(_task(), _hand_report(), events,
                                 "/tmp/pw-test")


def test_recorder_collision_counter():
    rec = PathwayRecorder().record(
        _task(), _hand_report(), _hand_events(), "/tmp/pw-test",
        existing_ids={"pathway.ct-pw-a.30"})
    assert rec["pathway_id"] == "pathway.ct-pw-a.30.2"
    rec2 = PathwayRecorder().record(
        _task(), _hand_report(), _hand_events(), "/tmp/pw-test",
        existing_ids={"pathway.ct-pw-a.30", "pathway.ct-pw-a.30.2"})
    assert rec2["pathway_id"] == "pathway.ct-pw-a.30.3"


def test_recorder_dedups_discriminators_preserving_order():
    events = _hand_events()
    # A second expectation about the same condition (later seq).
    events = events + [
        (25, {"event_type": "EVIDENCE_EXPECTATION",
              "data": {"expectation_id": "ev-0025",
                       "condition": "firmware",
                       "hypotheses": [], "predicted_classes": [],
                       "implications": {}, "source": "src-test",
                       "measurement": "firmware again?"},
              "execution_id": "run-ct-pw-a-01234567",
              "event_id": "ev-0025", "sequence": 25,
              "task_id": "ct-pw-a"})]
    events.sort(key=lambda p: p[0])
    # The TASK_END at seq 30 still closes the execution; the
    # second expectation is unanswered and must not duplicate the
    # discriminator.
    rec = PathwayRecorder().record(_task(), _hand_report(), events,
                                   "/tmp/pw-test")
    assert rec["discriminators"] == ["firmware"]
    assert len(rec["evidence_requests"]) == 2
    assert rec["evidence_requests"][1]["observation"] is None
    assert validate_record(rec) == []


# ------------------------------------------------------------------
# Recorder: end-to-end from a REAL pipeline run (plain trial task,
# no evidence source — honestly no evidence requests)
# ------------------------------------------------------------------

def test_recorder_end_to_end_real_trial_run():
    task = {"task_id": "pw-t", "signature": "disk_full", "family": "valve",
            "tags": ["valve", "firmware_v2", "stuck"],
            "conditions": {"firmware": "v2"},
            "requested_operation": "repair",
            "provenance": ("int:contract-training",), "correct": "P2"}
    h = Harness(tasks=[task])
    try:
        report = h.pipe.run({k: v for k, v in task.items()
                             if k != "correct"})
        assert report["state"] == "RESOLVED"
        events = trace_events(h.pipe.trace, "pw-t", report["execution_id"])
        rec = PathwayRecorder().record(
            {k: v for k, v in task.items() if k != "correct"},
            report, events, h.root)
        # Honest shape for a plain trial task: no evidence source
        # was consulted, so no evidence requests and no
        # discriminators to replay.
        assert rec["evidence_requests"] == []
        assert rec["discriminators"] == []
        assert rec["attempts"] == [
            {"procedure": a["procedure"], "observed": a["observed"],
             "validation": a["validation"]} for a in report["attempts"]]
        assert rec["outcome"] == {"state": report["state"],
                                  "stage": report["stage"],
                                  "selected": report["selected"]}
        assert rec["root"] == h.root
        assert validate_record(rec) == []
        # And it stores and retrieves through the real Big49.
        store = PathwayStore(h.pipe.big)
        pid = store.write(rec)
        assert pid == rec["pathway_id"]
        assert store.count() == 1
        found = store.find("disk_full", "valve", ["valve", "other"])
        assert found is not None
        assert found["pathway_id"] == pid
        assert store.find("disk_full", "valve", ["unrelated"]) is None
    finally:
        h.close()


# ------------------------------------------------------------------
# Providers
# ------------------------------------------------------------------

def _seeded_store():
    """Three pathways: A and B share (disk_full, valve); C is alone
    in (svc_hung, sensor). All carry a firmware discriminator."""
    store = _store()
    for tid, seq, sig, fam, tags in [
            ("ct-pw-a", 12, "disk_full", "valve", ("valve", "stuck")),
            ("ct-pw-b", 15, "disk_full", "valve", ("valve", "jammed")),
            ("ct-pw-c", 18, "svc_hung", "sensor", ("sensor", "drift"))]:
        store.write(_record(tid, seq=seq, signature=sig, family=fam,
                            tags=tags))
    return store


def test_replay_provider_empty_store_and_match():
    empty = PathwayReplayProvider(_store())
    assert empty.discriminators_for(_task()) == []
    store = _seeded_store()
    provider = PathwayReplayProvider(store)
    assert provider.discriminators_for(_task()) == ["firmware"]
    # No shared tag -> no replay, even with an exact (sig, family).
    assert provider.discriminators_for(
        _task(tags=("sensor", "drift"))) == []
    # Unknown (signature, family) -> no replay.
    assert provider.discriminators_for(
        _task(signature="cache_big", family="blocked")) == []


def test_shuffled_provider_determinism_and_never_own():
    store = _seeded_store()
    tasks = [_task("ct-pw-a", tags=("valve", "stuck")),
             _task("ct-pw-b", tags=("valve", "jammed")),
             _task("ct-pw-c", "svc_hung", "sensor", ("sensor", "drift"))]
    p1 = ShuffledPathwayProvider(store, tasks, seed=20261118)
    p2 = ShuffledPathwayProvider(store, tasks, seed=20261118)
    assert p1.association == p2.association
    # A and B are compatible: each gets the other's pathway, never
    # its own. C is alone in its group: no replay.
    assert p1.association["ct-pw-a"] == "pathway.ct-pw-b.15"
    assert p1.association["ct-pw-b"] == "pathway.ct-pw-a.12"
    assert p1.association["ct-pw-c"] is None
    assert p1.discriminators_for(tasks[0]) == ["firmware"]
    assert p1.discriminators_for(tasks[1]) == ["firmware"]
    assert p1.discriminators_for(tasks[2]) == []
    # A different seed may shuffle differently, but the
    # never-own rule holds for any seed.
    for seed in (1, 7, 20261118):
        p = ShuffledPathwayProvider(store, tasks, seed=seed)
        for tid, pid in p.association.items():
            if pid is not None:
                assert not pid.startswith(f"pathway.{tid}.")
    # A task with no compatible pathway at all gets None.
    p3 = ShuffledPathwayProvider(
        store, tasks + [_task("ct-pw-z", "cache_big", "blocked",
                              ("route", "blocked"))], seed=20261118)
    assert p3.association["ct-pw-z"] is None
    assert p3.discriminators_for(
        _task("ct-pw-z", "cache_big", "blocked",
              ("route", "blocked"))) == []


# ------------------------------------------------------------------
# validate_record
# ------------------------------------------------------------------

def test_validate_record_problems():
    assert validate_record(_record()) == []
    # Empty discriminators are valid only with no evidence requests.
    no_ev = _record(discriminators=[], evidence_requests=[])
    assert validate_record(no_ev) == []
    bad = _record(discriminators=[], evidence_requests=None)
    assert validate_record(bad) != []
    # Missing key, malformed id, bad seq order, bad validation.
    missing = _record()
    del missing["root"]
    assert any("missing key 'root'" in p for p in validate_record(missing))
    bad_id = _record()
    bad_id["pathway_id"] = "not-a-pathway"
    assert validate_record(bad_id) != []
    bad_seq = _record()
    bad_seq["evidence_requests"][0]["observation_seq"] = 10
    assert any("expectation_seq" in p for p in validate_record(bad_seq))
    bad_val = _record()
    bad_val["attempts"][0]["validation"] = "MAYBE"
    assert validate_record(bad_val) != []
    extra = _record()
    extra["surprise"] = 1
    assert any("unexpected key" in p for p in validate_record(extra))


def test_validate_record_unknown_procedure_raises():
    bad = _record(attempts=(("SOSO-P99", True, "PASS"),))
    with pytest.raises(KeyError):
        validate_record(bad)
    bad_outcome = _record(selected="SOSO-P99")
    with pytest.raises(KeyError):
        validate_record(bad_outcome)
