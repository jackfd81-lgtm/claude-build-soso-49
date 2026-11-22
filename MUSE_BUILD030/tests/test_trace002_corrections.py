"""TRACE-002 corrections (JD's correction directive, 2026-11-18).

A focused correction pass on the TRACE-002 evidence, before
expanding TRACE-003. The 93-test baseline and the original
artifacts are preserved; these tests pin the corrections.

  1. Export/receipt reconciliation (857 vs 991/992, 133 omitted).
  2. Receipt prefix / truncation / tampering / appended events.
  3+8. Replay fidelity: IOU identity/state, five states, journal
       comparison, exact fields, no "independent verification".
  4. Procedure identity (native vs registered), both preserved.
  5. Honest instrumentation coverage: four kinds of absence.
  6. Candidate rank, score components, rejection reasons,
     actual attempts, pathway cost.
  7. L24 is pathway redistribution, not cost reduction.
  9. Relationship terminology: chronological vs nested vs causal.
  10. Blocked-family P5-first regression fixture + revised ranker.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.harness import (Harness, TRAINING, TRANSFER_TASKS,
                              register_demo, DEMO_TASK)
from contract.registry import procedure_identity
from contract.trace import (COUNTING_CONVENTION, certify, export_full,
                            export_summary, export_trace, function_states,
                            omitted_events, reconstruct, trace_events,
                            verify_export, verify_receipt, write_full_export,
                            write_omitted_export, write_receipt,
                            write_trace_export)
from contract.trace002 import (MATCHED_TASKS, run_ablation,
                               run_replay, run_wrong_decision,
                               save_replay_inputs)


def _trained(**kw):
    h = Harness(**kw)
    h.train_baseline()
    return h


def _reopened(**kw):
    h = Harness(**kw)
    for t in TRAINING:
        h.add_task(t)
    for x in TRANSFER_TASKS.values():
        h.add_task(x)
    return h


# The 134 omitted sequences from the ORIGINAL demo ledger
# (seq 1..991), enumerated by actual sequence from the ledger
# (item 1). The original filtered export's sequences are exactly
# {2..991} minus these. 133 are operational (PROBLEM_ARRIVED,
# ACTION_INTENT, RECORDED, TASK_DONE, IOU_OPENED); seq 1 is the
# SYSTEM_OPEN metadata row.
ORIGINAL_OMITTED_SEQS = [
    1, 6, 27, 28, 35, 43, 64, 65, 72, 80, 101, 102, 109, 117, 128,
    129, 136, 144, 150, 151, 158, 166, 172, 173, 180, 188, 208, 209,
    216, 224, 244, 245, 252, 260, 280, 281, 288, 296, 307, 308, 315,
    323, 329, 330, 337, 345, 351, 352, 359, 367, 387, 388, 395, 403,
    423, 424, 431, 439, 459, 460, 467, 475, 486, 487, 494, 502, 508,
    509, 516, 524, 530, 531, 538, 546, 566, 567, 578, 579, 586, 594,
    614, 615, 626, 627, 634, 642, 662, 663, 674, 675, 682, 690, 701,
    702, 709, 717, 723, 724, 731, 739, 745, 746, 753, 761, 781, 782,
    789, 797, 808, 809, 816, 824, 830, 831, 838, 848, 854, 855, 862,
    871, 881, 882, 892, 912, 913, 920, 929, 954, 955, 962, 971, 977,
    978, 985]


# ---- Item 1: the export boundary, reconciled ----------------

def test_export_boundary_reconciled():
    """The filtered export + the omitted enumeration == the
    full-fidelity export, row for row. The omitted rows are
    enumerated by actual sequence and kind, not inferred from
    position. The manifests state the counting convention."""
    tmp = tempfile.mkdtemp()
    h = _trained()
    try:
        task = MATCHED_TASKS[0]
        h.add_task(task)
        h.pipe.run({k: v for k, v in task.items() if k != "correct"})
        filt_path = os.path.join(tmp, "filtered.json")
        full_path = os.path.join(tmp, "full.json")
        om_path = os.path.join(tmp, "omitted.json")
        n_filt = write_trace_export(h.pipe.trace, filt_path)
        n_full = write_full_export(h.pipe.trace, full_path)
        n_om = write_omitted_export(h.pipe.trace, om_path)
        assert n_filt + n_om == n_full
        with open(full_path) as f:
            full = json.load(f)
        with open(filt_path) as f:
            filt = json.load(f)
        with open(om_path) as f:
            om = json.load(f)
        # The full export is dense 1..N and holds every kind.
        assert [r["sequence"] for r in full] == list(range(1, n_full + 1))
        assert set(r["kind"] for r in full) >= {"TRACE_EVENT", "SYSTEM_OPEN"}
        # The omitted rows are exactly the full rows minus the
        # filtered rows, by sequence — and each carries its kind.
        filt_seqs = {e["sequence"] for e in filt}
        assert sorted(r["sequence"] for r in om) == sorted(
            set(range(1, n_full + 1)) - filt_seqs)
        assert all(r["kind"] != "TRACE_EVENT" for r in om)
        assert all(r["sequence"] and r["kind"] for r in om)
        # The manifests state the counting convention.
        with open(full_path + ".manifest.json") as f:
            manifest = json.load(f)
        assert manifest["counting"]["genesis"].startswith(
            "the chain origin")
        assert manifest["counting"]["system_open"].startswith(
            "a stored row and counted")
        # The summary's excluded kinds are enumerated, never silent.
        summary = export_summary(h.pipe.trace)
        assert summary["exported"] == n_filt
        assert summary["total_ledger_events"] == n_full
        assert summary["excluded_total"] == n_om
    finally:
        h.close()


def test_original_857_export_preserved():
    """The artifact JD inspected (demo_trace002_trace_ORIGINAL_857.json)
    is preserved: 857 TRACE_EVENT rows, sequences exactly
    {2..991} minus the 134 omitted sequences enumerated from
    the actual ledger. 991 rows + GENESIS (not a stored row) =
    992; 134 omitted = 133 operational + 1 SYSTEM_OPEN."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "demo_trace002_trace_ORIGINAL_857.json")
    with open(path) as f:
        orig = json.load(f)
    assert len(orig) == 857
    seqs = sorted(e["sequence"] for e in orig)
    assert seqs == sorted(set(range(2, 992)) - set(ORIGINAL_OMITTED_SEQS))
    assert len(set(seqs)) == 857
    assert all(e.get("authority") == 0 for e in orig)
    # The counting convention reconciles the three figures.
    assert COUNTING_CONVENTION["genesis"].startswith("the chain origin")
    assert 991 + 1 == 992  # rows + GENESIS
    assert len(ORIGINAL_OMITTED_SEQS) == 134  # 133 operational + SYSTEM_OPEN


# ---- Item 2: receipt prefix / truncation / tampering / append --

def _run_one_task(h, task):
    h.add_task(task)
    return h.pipe.run({k: v for k, v in task.items() if k != "correct"})


def test_receipt_prefix_truncation_tamper_append():
    """The receipt commits to a PREFIX (item 2). Appended events
    do not invalidate it; a truncation or tampering WITHIN the
    prefix does. GENESIS is not counted; SYSTEM_OPEN is."""
    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, "root")
    task = MATCHED_TASKS[0]
    # Build a ledger and write a receipt for its head.
    h = Harness(root=root, tasks=[task])
    try:
        h.train_baseline()
        _run_one_task(h, task)
        receipt_path = os.path.join(tmp, "receipt.json")
        receipt = write_receipt(h.pipe.trace, receipt_path)
        head_seq = receipt["head_seq"]
        n_events = receipt["n_events"]
        assert n_events == head_seq  # dense ledger: rows == head_seq
        assert head_seq >= 1
    finally:
        h.close()
    # Appended events: reopen, run another task, the receipt
    # still verifies (the prefix is intact) and the head moved.
    h2 = _reopened(root=root)
    try:
        _run_one_task(h2, MATCHED_TASKS[1])
        res = verify_receipt(h2.pipe.trace, receipt_path)
        assert res["ok"], res
        assert res["head"]["n_events"] > n_events
    finally:
        h2.close()
    # Tampering WITHIN the prefix: modify a row's data below the
    # head while the ledger is open, then verify — the chain
    # verification fails and verify_receipt returns ok=False
    # (it does not raise). Reopening a tampered ledger raises
    # at open (TraceLog verifies on construction), which is the
    # same detection one step earlier.
    import sqlite3
    h3 = _reopened(root=root)
    try:
        con = sqlite3.connect(os.path.join(root, "trace.db"))
        con.execute("UPDATE events SET data='{}' WHERE seq=?", (head_seq,))
        con.commit()
        con.close()
        res = verify_receipt(h3.pipe.trace, receipt_path)
        assert not res["ok"], res
        assert "chain" in res["mismatch"].lower() or "hash" in res["mismatch"].lower()
    finally:
        h3.close()
    with pytest.raises(ValueError):
        _reopened(root=root)
    # Truncation WITHIN the prefix: delete a row below the head
    # (on a fresh ledger, so the tamper above does not interfere).
    import shutil
    root2 = os.path.join(tmp, "root2")
    h4 = Harness(root=root2, tasks=[task])
    try:
        h4.train_baseline()
        _run_one_task(h4, task)
        receipt2_path = os.path.join(tmp, "r2.json")
        receipt2 = write_receipt(h4.pipe.trace, receipt2_path)
        head2 = receipt2["head_seq"]
        con = sqlite3.connect(os.path.join(root2, "trace.db"))
        con.execute("DELETE FROM events WHERE seq=?", (head2,))
        con.commit()
        con.close()
        res = verify_receipt(h4.pipe.trace, receipt2_path)
        assert not res["ok"], res
    finally:
        h4.close()


# ---- Items 3+8: replay fidelity ------------------------------

def test_replay_reconstructs_iou_and_journal():
    """The replay reconstructs the IOU identity and state for
    the unresolved s2-config step, distinguishes the five
    states in the source terminology, and compares the
    reconstruction against the durable Small49 journal (read
    back in the fresh process) as well as the committed
    reports. The exact fields compared are reported, and the
    verification limit is stated."""
    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, "root")
    h = _trained(root=root)
    try:
        register_demo(h)
        rep = h.runner.run(DEMO_TASK)
        assert rep["status"] == "DONE" and rep["result"] == "PARTIAL"
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
                "execution_id": begins[0]["execution_id"] if begins else None,
                "iou": s["iou"]}
        saved = save_replay_inputs(
            h, tasks, os.path.join(tmp, "committed.json"),
            os.path.join(tmp, "receipt.json"),
            parent_report=rep, parent_task=DEMO_TASK)
        saved["committed"]["parent_execution_id"] = rep["execution_id"]
        with open(os.path.join(tmp, "committed.json"), "w") as f:
            json.dump(saved["committed"], f, indent=2, sort_keys=True)
    finally:
        h.close()
    result = run_replay(root, os.path.join(tmp, "receipt.json"),
                        os.path.join(tmp, "committed.json"))
    assert result["receipt_ok"], result["receipt"]
    assert result["match"], result["mismatches"]
    assert "verification_limit" in result
    assert "not independent correctness verification" in result["verification_limit"]
    # The five states for s2-config, in the source terminology.
    s2 = result["reconstructed"]["ct-demo-repair.s2-config"]
    assert s2["outcome"] == "UNSOLVED"
    assert s2["unresolved"] is True
    assert s2["iou"]["id"] == "iou.ct-demo-repair.s2-config"
    assert s2["iou"]["state"] == "OPEN"
    assert s2["executed"] == []  # nothing was executed for this step
    assert s2["validated"] == []  # nothing was validated
    assert s2["certified"] is True
    # The durable journal (read back in the fresh process) agrees
    # with the committed step record: FAILURE, no procedure, the
    # same IOU, and the journal classification COMPLETED (a journal
    # record exists — the failure + IOU were committed — which is
    # not the same as the step succeeding).
    j = result["journal_steps"]["s2-config"]
    assert j["outcome"] == "FAILURE"
    assert j["procedure"] == ""
    assert j["iou"] == "iou.ct-demo-repair.s2-config"
    assert j["classification"] == "COMPLETED"
    assert result["journal_state"]["result"] == "PARTIAL"
    # The exact fields compared are reported, with no mismatches.
    assert result["fields_compared"]
    assert all(f["match"] for f in result["fields_compared"])
    fields = {(f["task_id"], f["field"]) for f in result["fields_compared"]}
    assert ("ct-demo-repair.s2-config", "iou") in fields
    assert ("ct-demo-repair.s2-config", "journal_outcome") in fields
    assert ("ct-demo-repair.s2-config", "journal_iou") in fields


# ---- Item 4: procedure identity ------------------------------

def test_procedure_identity_both_forms():
    """A procedure id in either form normalises to both, with
    the origin and adapter. Unknown ids raise. Attempts carry
    both ids and agree."""
    for native, registered in (("P2", "SOSO-P2"), ("P7", "SOSO-P7"),
                               ("clear_cache", "RC-FIX-CLEAR-CACHE"),
                               ("rotate_logs", "RC-FIX-ROTATE-LOGS")):
        a = procedure_identity(native)
        b = procedure_identity(registered)
        assert a == b
        assert a["native"] == native and a["registered"] == registered
        assert a["origin"] in ("soso-rev1.0", "realcog-0.4")
    with pytest.raises(KeyError):
        procedure_identity("NO-SUCH-PROCEDURE")
    # End to end: every attempt carries both ids, and they agree.
    h = _trained()
    try:
        task = MATCHED_TASKS[5]  # a trial task
        h.add_task(task)
        rep = h.pipe.run({k: v for k, v in task.items() if k != "correct"})
        assert rep["attempts"]
        for a in rep["attempts"]:
            ident = procedure_identity(a["procedure"])
            assert ident["native"] == a["native"]
            assert ident["registered"] == a["procedure"]
    finally:
        h.close()


# ---- Item 5: honest instrumentation coverage -----------------

def test_coverage_reasons_distinguish_absence():
    """The four kinds of absence are distinct (item 5): a
    disabled function, an information source removed, a
    strategy ablated, and a function this task simply did not
    need. A function that never appears is never counted as
    exercised cognition."""
    # Direct execution: L24 runs on a cross-task transfer task.
    h = _trained()
    try:
        slope = [t for t in MATCHED_TASKS if t["task_id"].startswith("ab-xfer")][0]
        rep = h.pipe.run({k: v for k, v in slope.items() if k != "correct"})
        p = reconstruct(h.pipe.trace, slope["task_id"], rep["execution_id"])
        assert "L24" in p["functions"]
        assert p["function_states"]["not_invoked_reasons"].get("L24") is None
    finally:
        h.close()
    # Capability suppression: L24 disabled -> reason "disabled".
    h = _trained()
    try:
        h.pipe.set_disabled_functions({"L24"})
        rep = h.pipe.run({k: v for k, v in slope.items() if k != "correct"})
        p = reconstruct(h.pipe.trace, slope["task_id"], rep["execution_id"])
        assert "L24" not in p["functions"]
        assert p["function_states"]["not_invoked_reasons"]["L24"] == "disabled"
    finally:
        h.close()
    # Information-source removal: descriptions=None -> L24's
    # reason is "information_source_removed" (L24 itself was not
    # disabled; its input was removed).
    h = _trained()
    try:
        h.pipe.set_descriptions(None)
        rep = h.pipe.run({k: v for k, v in slope.items() if k != "correct"})
        p = reconstruct(h.pipe.trace, slope["task_id"], rep["execution_id"])
        assert "L24" not in p["functions"]
        assert p["function_states"]["not_invoked_reasons"]["L24"] == (
            "information_source_removed")
        assert "L24" not in p["disabled_functions"]
    finally:
        h.close()
    # Strategy ablation: U08 disabled -> the transfer stage is
    # skipped, so L24 (transfer-only) never had a chance; U09
    # still runs, because it is the shared comparison/ranking
    # function and trial ranking needs it too.
    h = _trained()
    try:
        h.pipe.set_disabled_functions({"U08"})
        rep = h.pipe.run({k: v for k, v in slope.items() if k != "correct"})
        p = reconstruct(h.pipe.trace, slope["task_id"], rep["execution_id"])
        reasons = p["function_states"]["not_invoked_reasons"]
        assert reasons.get("L24") == "strategy_ablated"
        assert reasons.get("U08") == "disabled"
        assert "U09" in p["functions"]
        assert reasons.get("U09") is None
    finally:
        h.close()
    # Not needed by this task: U10 (targeted investigation) does
    # not run on a task with no unknown mandatory condition.
    h = _trained()
    try:
        task = MATCHED_TASKS[0]
        rep = h.pipe.run({k: v for k, v in task.items() if k != "correct"})
        p = reconstruct(h.pipe.trace, task["task_id"], rep["execution_id"])
        assert "U10" not in p["functions"]
        assert p["function_states"]["not_invoked_reasons"]["U10"] == (
            "not_invoked_by_this_task")
    finally:
        h.close()


def test_demo_coverage_is_eleven_functions(tmp_path, monkeypatch):
    """The demo exercises 11 of the 15 implemented functions.
    (EXPANSION 2026-11-18: was 7 of 9; the demo now also shows
    U01, U02, L02 and U04. U03 and U05 still do not appear —
    the demo configures no evidence source or measurement
    selector — and L24 and U10 still do not appear as executed
    functions in the supplied demo, and neither is counted as
    exercised cognition.)"""
    import shutil
    import demo_trace002

    for name in ("ROOT", "TRACE_JSON", "TRACE_FULL_JSON", "TRACE_OMITTED_JSON", "RECON_JSON"):
        monkeypatch.setattr(demo_trace002, name, str(tmp_path / name))
    generated, _, _ = demo_trace002._run_demo()
    try:
        with open(demo_trace002.RECON_JSON) as stream:
            recon = json.load(stream)
    finally:
        shutil.rmtree(generated)
    all_funcs = set()
    for p in recon["executions"].values():
        all_funcs |= set(p["functions"])
    all_funcs |= set(recon["parent"]["functions"])
    assert all_funcs == {"L01", "L15", "L21", "L30", "U08", "U09", "U37",
                         "U01", "U02", "L02", "U04"}
    assert "L24" not in all_funcs and "U10" not in all_funcs


# ---- Item 6: candidate rank, scores, rejections, cost --------

def test_candidate_rank_and_score_components():
    """CANDIDATE_PROPOSED events carry the rank, the score
    components and both procedure ids (item 6). The report
    carries the full proposed list, the unattempted tail with
    its reason, and the pathway cost. Proposals are not
    executed branches."""
    h = _trained()
    try:
        # A flood task: transfer proposes P7 (misleading overlap),
        # then the trial proposes the ranked list; the task
        # resolves on the last attempt, so proposals > attempts.
        task = {"task_id": "corr-flood", "signature": "flood_x",
                "family": "flood",
                "tags": ["flood", "ground", "water"],
                "conditions": {},
                "requested_operation": "repair",
                "provenance": ("int:trace-002",), "correct": "P6"}
        h.add_task(task)
        rep = h.pipe.run({k: v for k, v in task.items() if k != "correct"})
        assert rep["state"] == "RESOLVED"
        # The proposed list is longer than the attempts.
        assert len(rep["proposed"]) > len(rep["attempts"])
        # Every proposal carries rank, components and both ids.
        for p in rep["proposed"]:
            assert "rank" in p and "rank_components" in p
            assert "native" in p and "procedure" in p
            assert procedure_identity(p["procedure"])["native"] == p["native"]
        # The trial proposals carry the family/shared/soft scores.
        trial = [p for p in rep["proposed"] if p["source"] == "trial"]
        assert trial
        assert trial[0]["rank"] == 0
        assert set(trial[0]["rank_components"]) == {"family", "shared", "soft"}
        # The unattempted tail has an explicit reason.
        for u in rep["unattempted"]:
            assert u["reason"].startswith("not reached:")
            assert u["procedure"] not in [a["procedure"] for a in rep["attempts"]]
        # The pathway cost is explicit.
        cost = rep["pathway_cost"]
        assert cost["n_proposed"] == len(rep["proposed"])
        assert cost["n_attempted"] == len(rep["attempts"])
        assert cost["n_unattempted"] == len(rep["unattempted"])
        assert cost["n_proposed_procedures"] >= cost["n_attempted"]
        # The trace events match the report's proposals.
        proposed_events = [
            ev for _, ev in trace_events(h.pipe.trace, "corr-flood")
            if ev["event_type"] == "CANDIDATE_PROPOSED"]
        assert len(proposed_events) == len(rep["proposed"])
        for ev in proposed_events:
            assert "rank" in ev["data"] and "rank_components" in ev["data"]
            assert "native" in ev["data"]
    finally:
        h.close()


# ---- Item 7: L24 is pathway redistribution -------------------

@pytest.fixture(scope="module")
def ablation():
    return run_ablation()


def test_l24_is_pathway_redistribution_not_cost_reduction(ablation):
    """The attempt count is IDENTICAL across the control, L24,
    information-source and strategy arms (19 each) — L24 does
    not reduce the attempt count. The span count is not reduced
    either (control 175, L24 170 — the 5 fewer spans are the
    L24 span itself being absent plus the spans of the three
    transfer stages that flipped to trial, not less work).
    (EXPANSION 2026-11-18: was control 103, L24 100 — the new
    spans per run come from U01, U02, L02 and U04.) What changes
    is the PATHWAY: the L24 arm's soso-transfer count drops and
    its higher-trial count rises. Claim redistribution, not
    cost reduction."""
    control = ablation["control"]["summary"]
    l24 = ablation["L24"]["summary"]
    info = ablation["l24_info_source"]["summary"]
    strategy = ablation["l24_strategy"]["summary"]
    assert control["attempts"] == l24["attempts"] == 19
    assert info["attempts"] == 19 and strategy["attempts"] == 19
    assert control["spans"] == 175
    assert l24["spans"] == 170
    assert control["stages"].get("soso-transfer", 0) == 5
    assert l24["stages"].get("soso-transfer", 0) == 2
    assert l24["stages"].get("higher-trial", 0) > control["stages"].get("higher-trial", 0)
    assert control["resolved"] == l24["resolved"] == 12


# ---- Item 9: relationship terminology ------------------------

def test_relationship_terminology():
    """FOLLOWED_BY is chronological only (and says so);
    NESTED_IN is structural containment (INVOKED_BY is gone);
    CAUSED_BY is asserted only for a correction and the failed
    attempt it demotes. No causal dependence is inferred from
    sequence order."""
    wrong = run_wrong_decision()
    p = wrong["pathway"]
    rels = p["relationships"]
    assert p["corrections"], "the wrong-decision pathway must have a correction"
    assert not any(r["relation"] == "INVOKED_BY" for r in rels)
    followed = [r for r in rels if r["relation"] == "FOLLOWED_BY"]
    assert followed
    assert all("not causal" in r.get("note", "") for r in followed)
    nested = [r for r in rels if r["relation"] == "NESTED_IN"]
    assert nested
    # The correction is caused by the failed attempt: the
    # CAUSED_BY edge points at the L15 span that ran the
    # demoted procedure and observed the failure.
    caused = [r for r in rels if r["relation"] == "CAUSED_BY"]
    assert caused
    assert all("correction of" in r.get("note", "") for r in caused)
    # The corrected procedure is the one that failed.
    assert wrong["corrections"][0]["procedure"] == "SOSO-P3"


# ---- Item 10: blocked-family P5-first fixture ----------------

# The regression fixture: a blocked-family task whose tags share
# NO word with any procedure description or demonstration tag, so
# neither the transfer nor the pointed-at rule fires and the
# trial runs. P5 (the trap for the blocked family, eval001) and
# P7 (the correct procedure) tie on (family 1, shared 0, soft
# 0); the existing ranker's stable sort falls back to registry
# order, and P5 is tried first.
P5_FIXTURE = {"task_id": "pf-tie", "signature": "blocked_shut_new",
              "family": "blocked",
              "tags": ["shut", "jammed", "wedged"], "conditions": {},
              "requested_operation": "repair",
              "provenance": ("int:trace-002",), "correct": "P7"}


def _run_fixture(train, ranker):
    h = Harness(tasks=[P5_FIXTURE], trial_ranker=ranker)
    try:
        if train:
            h.train_baseline()
        return h.pipe.run({k: v for k, v in P5_FIXTURE.items()
                           if k != "correct"})
    finally:
        h.close()


def test_p5_first_regression_fixture():
    """The existing ranker tries P5 (the trap) first on the
    blocked-family fixture: 2 attempts. The revised ranker —
    which ranks a procedure with a demonstrated success in the
    task's family first — tries P7 first: 1 attempt. The
    no-learning control (revised ranker, no training) still
    tries P5 first: the improvement comes from the learning,
    not from a change in the base ranking."""
    existing = _run_fixture(train=True, ranker="existing")
    assert existing["state"] == "RESOLVED"
    assert [a["procedure"] for a in existing["attempts"]] == ["SOSO-P5", "SOSO-P7"]
    assert len(existing["attempts"]) == 2
    revised = _run_fixture(train=True, ranker="revised")
    assert revised["state"] == "RESOLVED"
    assert [a["procedure"] for a in revised["attempts"]] == ["SOSO-P7"]
    assert len(revised["attempts"]) == 1
    control = _run_fixture(train=False, ranker="revised")
    assert control["state"] == "RESOLVED"
    assert [a["procedure"] for a in control["attempts"]] == ["SOSO-P5", "SOSO-P7"]
    assert len(control["attempts"]) == 2


def test_revised_ranker_does_not_degrade_matched_tasks():
    """The revised ranker (opt-in) leaves the 12 matched tasks
    unchanged: 12/12 resolved, 19 attempts — the same as the
    existing ranker."""
    h = Harness(tasks=list(MATCHED_TASKS), trial_ranker="revised")
    try:
        h.train_baseline()
        tot, res = 0, 0
        for t in MATCHED_TASKS:
            rep = h.pipe.run({k: v for k, v in t.items() if k != "correct"})
            tot += len(rep["attempts"])
            res += 1 if rep["state"] == "RESOLVED" else 0
        assert res == 12 and tot == 19
    finally:
        h.close()
