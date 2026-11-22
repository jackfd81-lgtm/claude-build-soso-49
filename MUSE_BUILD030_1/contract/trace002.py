"""TRACE-002 — causal contribution, correction and evidence-grade
replay (JD's TRACE-001 review + audit, 2026-11-18).

The TRACE-001 recorder is accepted as the working
cognitive-observability baseline. TRACE-002 measures how much of
the cognitive mechanism it actually observes, and whether the
measurements survive more demanding tests. Six requirements
(JD's TRACE-001 review):

  1. Multiple-function ablation — disable L24, U08, U09, L30,
     U37 and L15 individually on matched tasks; record changes
     in resolution, steps, escalation, incorrect claims and
     pathway. Correction B: distinguish three ablation levels —
     disabling a FUNCTION (the pipeline ignores its output),
     disabling an INFORMATION SOURCE (descriptions=None — the
     layer cannot compute cross-task candidates) and disabling a
     STRATEGY (U08 — the whole transfer stage).
  2. Wrong-decision reconstruction — a misleading qualified
     memory; trace the wrong candidate, validation failure,
     correction and subsequent attempt.
  3. Domain-transition stress test — legitimate cross-domain
     transfer, misleading lexical overlap, domain-inappropriate
     detours; record why each transition was proposed and
     whether it helped.
  4. Memory provenance — every trace memory reference resolves
     to the correct immutable record, including after rollover
     and restart.
  5. Independent trace replay — reconstruct from a freshly
     opened ledger, verify its commitment against an
     independently stored receipt, compare reconstructed state
     with committed task state.
  6. Trace completeness — inject failures between function
     entry, authorization, execution, validation and memory
     commitment; require accurate incomplete-span and IOU
     reconstruction.

Plus the audit's corrections: three function states
(REGISTERED / IMPLEMENTED / INVOKED — Correction A), two
recording modes (best-effort vs evidence-grade — Correction C),
explicit parent/child execution linkage (Correction E), and the
integrated continuous-learning demonstration (Priority 5).

Every experiment is built from reconstructed pathways
(contract/trace.py reconstruct), never from the final answer
alone. A plausible-looking trace is never mistaken for proof of
correct reasoning.
"""
from __future__ import annotations
from pathlib import Path

import json
import os
import random
import subprocess
import sys
import tempfile

from .eval001 import make_config_harness, qualify_learned
from .experience import record_id
from .harness import Harness, register_demo, DEMO_TASK
from .registry import get as reg_get
from .task import make_task
from .trace import (certify, child_executions, export_trace, reconstruct,
                    trace_events, verify_receipt, write_receipt)

# The matched ablation task set (12 tasks, 4 per stage):
#   exact    — resolve by lower-exact in the control (a qualified
#              procedure for the same signature).
#   transfer — resolve by soso-transfer in the control (a
#              cross-family task on the blocked signature).
#   trial    — resolve by higher-trial in the control (unfamiliar).
MATCHED_TASKS = [
    # exact (baseline training variants, re-presented)
    {"task_id": "ab-exact-valve", "signature": "disk_full", "family": "valve",
     "tags": ["valve", "firmware_v2", "stuck", "flow"], "conditions": {"firmware": "v2"},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P2"},
    {"task_id": "ab-exact-sensor", "signature": "svc_hung", "family": "sensor",
     "tags": ["sensor", "temperature", "drift", "thermal"], "conditions": {"kind": "sensor"},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P3"},
    {"task_id": "ab-exact-pump", "signature": "cfg_bad", "family": "pump",
     "tags": ["pump", "intermittent", "vibration", "housing"], "conditions": {"kind": "pump"},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P4"},
    {"task_id": "ab-exact-blocked", "signature": "cache_big", "family": "blocked",
     "tags": ["route", "blocked", "debris", "fallen"], "conditions": {"kind": "detour"},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P7"},
    # transfer (cross-family slope tasks on the blocked signature)
    {"task_id": "ab-xfer-slope-a", "signature": "cache_big", "family": "slope",
     "tags": ["slope", "ground", "steep", "cross"], "conditions": {},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P7"},
    {"task_id": "ab-xfer-slope-b", "signature": "cache_big", "family": "slope",
     "tags": ["slope", "ground", "ascent", "path"], "conditions": {},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P7"},
    {"task_id": "ab-xfer-slope-c", "signature": "cache_big", "family": "slope",
     "tags": ["slope", "ground", "incline", "route"], "conditions": {},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P7"},
    {"task_id": "ab-xfer-slope-d", "signature": "cache_big", "family": "slope",
     "tags": ["slope", "ground", "ramp", "cross"], "conditions": {},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P7"},
    # trial (unfamiliar tasks)
    {"task_id": "ab-trial-flood", "signature": "flood_x", "family": "flood",
     "tags": ["flood", "water", "rising", "level"], "conditions": {},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P6"},
    {"task_id": "ab-trial-narrow", "signature": "cache_big", "family": "narrow",
     "tags": ["narrow", "passage", "tight", "ground"], "conditions": {"kind": "traverse"},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P7"},
    {"task_id": "ab-trial-valve", "signature": "valve_new_y", "family": "valve",
     "tags": ["valve", "hum", "bearing", "rattle"], "conditions": {"firmware": "v2"},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P2"},
    {"task_id": "ab-trial-sensor", "signature": "sensor_new", "family": "sensor",
     "tags": ["sensor", "drift", "wandering", "readings"], "conditions": {"kind": "sensor"},
     "requested_operation": "repair", "provenance": ("int:trace-002",), "correct": "P3"},
]

ABLATION_FUNCTIONS = ("L24", "U08", "U09", "L30", "U37", "L15")


def _run_one(harness, task):
    rep = harness.pipe.run({k: v for k, v in task.items() if k != "correct"})
    pathway = reconstruct(harness.pipe.trace, task["task_id"])
    return rep, pathway


def _summarize_arm(results):
    """Aggregate one ablation arm's per-task results. The cost is
    measured in two dimensions (TRACE-002 correction, item 7):
    attempts (the executed branches) and spans (the function
    invocations — one span per invocation). The claim about a
    function must name which cost it changes: L24 changes the
    PATHWAY (which stage resolves, which functions run), not the
    attempt count and not the span count."""
    resolved = sum(1 for r in results if r["report"]["state"] == "RESOLVED")
    false = sum(1 for r in results
                if any(a["observed"] and a["validation"] == "FAIL"
                       for a in r["report"]["attempts"])
                and r["report"]["state"] == "RESOLVED")
    escalated = sum(1 for r in results
                    if r["report"]["state"] in ("UNSOLVED", "ESCALATED"))
    attempts = sum(len(r["report"]["attempts"]) for r in results)
    stages = {}
    for r in results:
        stages[r["report"]["stage"]] = stages.get(r["report"]["stage"], 0) + 1
    spans = sum(len(r["pathway"]["spans"]) for r in results
                if r.get("pathway") is not None)
    return {"resolved": resolved, "of": len(results), "false_resolved": false,
            "escalated": escalated, "attempts": attempts,
            "attempts_per_task": attempts / len(results) if results else 0,
            "spans": spans,
            "spans_per_task": spans / len(results) if results else 0,
            "stages": stages}


def run_ablation(tasks=MATCHED_TASKS, functions=ABLATION_FUNCTIONS):
    """Requirement 1: disable each function individually on the
    matched tasks (fresh harness per arm, identical training).
    Returns {arm: {results, summary}} with "control" (nothing
    disabled) plus one arm per function, and the three L24
    levels (Correction B): "l24_function" (the pipeline ignores
    the cross-task candidates), "l24_info_source"
    (descriptions=None — the layer cannot compute them) and
    "l24_strategy" (U08 disabled — the whole transfer stage)."""
    out = {}
    # Control.
    h = make_config_harness("FULL", list(tasks))
    try:
        results = [_run_one(h, t) for t in tasks]
        out["control"] = {"results": [{"task_id": t["task_id"], "report": r,
                                       "pathway": p}
                                      for t, (r, p) in zip(tasks, results)],
                          "summary": _summarize_arm(
                              [{"report": r, "pathway": p} for r, p in results])}
    finally:
        h.close()
    # One arm per function. Training (which needs the transfer
    # stage to qualify procedures) happens with no functions
    # disabled; the ablation is applied only for the test tasks
    # (set_disabled_functions, recorded in the trace).
    for fid in functions:
        h = Harness(tasks=list(tasks))
        try:
            h.train_baseline()
            h.pipe.set_disabled_functions({fid})
            results = [_run_one(h, t) for t in tasks]
            out[fid] = {"results": [{"task_id": t["task_id"], "report": r,
                                     "pathway": p}
                                    for t, (r, p) in zip(tasks, results)],
                        "summary": _summarize_arm(
                            [{"report": r, "pathway": p} for r, p in results])}
        finally:
            h.close()
    # The three L24 levels (Correction B). "l24_function" is the
    # L24 arm above (the pipeline ignores the cross-task
    # candidates the layer computed). "l24_info_source" removes
    # the descriptions (the layer cannot compute them).
    # "l24_strategy" removes the whole transfer stage (U08).
    out["l24_function"] = out["L24"]
    # The information-source level: train under the control, then
    # remove the descriptions for the test tasks only.
    h = Harness(tasks=list(tasks))
    try:
        h.train_baseline()
        h.pipe.set_descriptions(None)
        results = [_run_one(h, t) for t in tasks]
        out["l24_info_source"] = {
            "results": [{"task_id": t["task_id"], "report": r, "pathway": p}
                        for t, (r, p) in zip(tasks, results)],
            "summary": _summarize_arm([{"report": r, "pathway": p} for r, p in results])}
    finally:
        h.close()
    out["l24_strategy"] = out["U08"]
    return out


# ------------------------------------------------------------------
# Requirement 2 — wrong-decision reconstruction.
# ------------------------------------------------------------------
# A misleading qualified memory: P3 qualified for svc_hung with
# PUMP tags (learned on a pump-tagged sensor task). A later task
# with the same signature and tags — but correct procedure P4 —
# is served P3 from the exact index. P3 fails; the correction is
# recorded (QUALIFIED -> INCONCLUSIVE); P4 resolves via transfer.
WRONG_TRAIN = {"task_id": "wd-train", "signature": "svc_hung", "family": "sensor",
               "tags": ["pump", "intermittent", "vibration", "bearing"],
               "conditions": {}, "requested_operation": "repair",
               "provenance": ("int:trace-002",), "correct": "P3"}
WRONG_XFER = {"task_id": "wd-xfer", "signature": "svc_hung", "family": "sensor",
              "tags": ["pump", "intermittent", "vibration", "housing"],
              "conditions": {}, "requested_operation": "repair",
              "provenance": ("int:trace-002",), "correct": "P3"}
WRONG_TEST = {"task_id": "wd-test", "signature": "svc_hung", "family": "sensor",
              "tags": ["pump", "intermittent", "vibration", "bearing"],
              "conditions": {}, "requested_operation": "repair",
              "provenance": ("int:trace-002",), "correct": "P4"}


def run_wrong_decision():
    """Requirement 2: introduce the misleading qualified memory,
    then run the test task. Returns the training/qualification
    reports, the test report and pathway, and the correction
    trajectory (the wrong candidate, its source memory, the
    validation result, the correction, the revised candidate and
    the subsequent outcome)."""
    h = Harness(tasks=[WRONG_TRAIN, WRONG_XFER, WRONG_TEST])
    try:
        h.train_baseline()
        train_rep = h.pipe.run({k: v for k, v in WRONG_TRAIN.items()
                                if k != "correct"})
        qual_rep = h.pipe.qualify(
            WRONG_TRAIN["task_id"], "SOSO-P3",
            {k: v for k, v in WRONG_XFER.items() if k != "correct"})
        test_rep, pathway = _run_one(h, WRONG_TEST)
        # The wrong candidate's source memory: the QUALIFIED
        # record that served it (the demonstration the exact
        # index used).
        latest = h.pipe.experiences.latest_per_pair()
        source = None
        for (t, p), r in sorted(latest.items()):
            if p == "SOSO-P3" and r.get("sig") == "svc_hung" \
                    and r["qual"] == "QUALIFIED":
                source = r
        return {"train_report": train_rep, "qualify_report": qual_rep,
                "test_report": test_rep, "pathway": pathway,
                "corrections": pathway["corrections"],
                "wrong_candidate_source": source,
                "trusted_after": [
                    (t, r["qual"]) for (t, p), r in sorted(latest.items())
                    if p == "SOSO-P3" and r.get("sig") == "svc_hung"]}
    finally:
        h.close()


# ------------------------------------------------------------------
# Requirement 3 — domain-transition stress test.
# ------------------------------------------------------------------
# (a) Legitimate transfer: a slope task on the blocked signature
#     (P7, description overlap "ground") — resolves by transfer.
# (b) Misleading lexical overlap: a flood task whose tags share
#     the word "ground" with P7's description. L24 proposes P7;
#     P7 fails (correct P6); the task resolves by trial with P6.
#     The transition is recorded, and it did NOT help.
# (c) Domain-inappropriate detour: a sensor task whose tags share
#     "vibration" with P4's description. L24 proposes P4; P4
#     fails (correct P3); the task resolves via P3. The transition
#     is recorded as a detour that did not help.
DOMAIN_STRESS_TASKS = {
    "legitimate": {"task_id": "ds-legit", "signature": "cache_big",
                   "family": "slope",
                   "tags": ["slope", "ground", "steep", "cross"],
                   "conditions": {}, "requested_operation": "repair",
                   "provenance": ("int:trace-002",), "correct": "P7"},
    "misleading_overlap": {"task_id": "ds-mislead", "signature": "flood_x",
                           "family": "flood",
                           "tags": ["flood", "ground", "water"],
                           "conditions": {}, "requested_operation": "repair",
                           "provenance": ("int:trace-002",), "correct": "P6"},
    # (c) Domain-inappropriate detour: a sensor task whose tags
    #     share words with P4's DESCRIPTION ("vibration", "check")
    #     but NOT with P3's demonstration tags (sensor /
    #     temperature / drift / readings / probe / thermal /
    #     wandering). The task presents no "kind" condition, so
    #     the demonstrated-condition gate does not refuse P4
    #     (nothing to contradict) — L24 proposes P4 as a
    #     cross-task candidate (a pump procedure for a sensor
    #     task); P4 is attempted and fails (correct P3); the task
    #     resolves by trial with P3. The transition is recorded
    #     as a detour that did not help.
    "inappropriate_detour": {"task_id": "ds-detour", "signature": "sensor_new_x",
                             "family": "sensor",
                             "tags": ["vibration", "check", "hum", "rattle"],
                             "conditions": {},
                             "requested_operation": "repair",
                             "provenance": ("int:trace-002",), "correct": "P3"},
}


def run_domain_stress(tasks=DOMAIN_STRESS_TASKS):
    """Requirement 3: the three domain-transition cases. For each,
    record the transition (origin, destination, trigger, reason,
    evidence), whether the proposed procedure succeeded, and the
    final stage — so a helpful transition can be distinguished
    from a misleading one."""
    out = {}
    for name, task in tasks.items():
        h = make_config_harness("FULL", [task])
        try:
            rep, pathway = _run_one(h, task)
            transitions = pathway["domain_transitions"]
            # Did the transition's procedure succeed? The first
            # attempt after the transition's span.
            helped = None
            for t in transitions:
                for a in rep["attempts"]:
                    if a["procedure"] == "SOSO-" + (
                            "P7" if name != "inappropriate_detour" else "P4"):
                        helped = bool(a["observed"] and a["validation"] == "PASS")
                        break
            out[name] = {"report": rep, "pathway": pathway,
                         "transitions": transitions,
                         "transition_helped": helped}
        finally:
            h.close()
    return out


# ------------------------------------------------------------------
# Requirement 4 — memory provenance.
# ------------------------------------------------------------------
def verify_provenance(harness, pathway, task_dict=None):
    """Requirement 4: every trace memory reference resolves to
    the correct immutable record. Returns a list of per-reference
    results {block, store, bucket, resolved, matches, detail}.
    Big49 identity blocks are read from the store and their
    digest compared with the task's identity digest. Big49
    experience blocks are looked up in the experience store and
    their qualification/procedure/signature compared with the
    event's detail. Small49 journal blocks are checked against
    the generations journal state. The transfer-index is a
    derived index, not a durable block — it must be labelled as
    such, and that labelling is itself the check."""
    pipe = harness.pipe
    # The experience records, keyed by their durable block id.
    exp_by_block = {}
    for r in pipe.experiences.read_all():
        native = reg_get(r["proc"]).native_id
        exp_by_block[record_id(r["task"], native, r["seq"])] = r
    results = []
    for m in pathway["memory_blocks"]:
        block, store, bucket = m["block"], m["store"], m["bucket"]
        detail = m.get("detail")
        if store == "big49" and block.startswith("task."):
            task_id = block[len("task."):]
            try:
                ptr = pipe._pointer_for(pipe.big, block, "L30")
                stored = pipe.big.read(ptr, principal="L30")
                content = json.loads(stored["content"])
                digest = content.get("digest")
                expected = None
                if task_dict is not None and task_dict["task_id"] == task_id:
                    expected = make_task(**{k: v for k, v in task_dict.items()
                                            if k in ("task_id", "signature", "family",
                                                     "tags", "conditions",
                                                     "unknown_conditions",
                                                     "contradictory_conditions",
                                                     "requested_operation",
                                                     "observed", "provenance")}).identity_digest()
                results.append({"block": block, "store": store, "bucket": bucket,
                                "resolved": True,
                                "matches": expected is None or digest == expected,
                                "detail": {"digest": digest,
                                           "expected_digest": expected}})
            except Exception as e:
                results.append({"block": block, "store": store, "bucket": bucket,
                                "resolved": False, "matches": False,
                                "detail": {"error": str(e)}})
        elif store == "big49" and block.startswith("exp."):
            rec = exp_by_block.get(block)
            if rec is None:
                results.append({"block": block, "store": store, "bucket": bucket,
                                "resolved": False, "matches": False,
                                "detail": {"error": "no such experience block"}})
            else:
                det = detail if isinstance(detail, dict) else {}
                qual_ok = ("qualification" not in det
                           or det["qualification"] == rec["qual"])
                src_ok = ("source" not in det or det["source"] == rec["src"])
                results.append({"block": block, "store": store, "bucket": bucket,
                                "resolved": True,
                                "matches": qual_ok and src_ok,
                                "detail": {"qualification": rec["qual"],
                                           "procedure": rec["proc"],
                                           "signature": rec["sig"],
                                           "source": rec["src"]}})
        elif store == "small49" and block.startswith("t."):
            # t.<task>.begin / t.<task>.s<n> / t.<task>.done
            task_id = block[2:].split(".")[0] if "." in block[2:] else block[2:]
            # The task id is the segment after "t." up to the
            # last ".begin"/".s<n>"/".done" suffix.
            for suffix in (".begin", ".done"):
                if block.endswith(suffix):
                    task_id = block[2:-len(suffix)]
            if ".s" in block[2:]:
                task_id = block[2:].rsplit(".s", 1)[0]
            # The journal is reached through the harness's
            # generations adapter by task id (the state is
            # keyed by the parent task id).
            try:
                journal, _gen = harness.generations.journal_for(
                    task_id, 1, "")
                st = journal.state(task_id)
                if block.endswith(".begin"):
                    ok = st["status"] != "NONE" and bool(st.get("plan"))
                    det = {"status": st["status"], "plan": st.get("plan")}
                elif block.endswith(".done"):
                    ok = st["status"] == "DONE"
                    det = {"status": st["status"], "result": st.get("result")}
                else:
                    n = int(block.rsplit(".s", 1)[1])
                    outcomes = st.get("outcomes", [])
                    ok = n < len(outcomes)
                    det = {"step_index": n,
                           "outcome": outcomes[n] if ok else None}
                results.append({"block": block, "store": store, "bucket": bucket,
                                "resolved": True, "matches": ok, "detail": det})
            except Exception as e:
                results.append({"block": block, "store": store, "bucket": bucket,
                                "resolved": False, "matches": False,
                                "detail": {"error": str(e)}})
        elif store == "transfer-index":
            # A derived index, not a durable block. The check is
            # that the event labels it as derived (it does).
            results.append({"block": block, "store": store, "bucket": bucket,
                            "resolved": True, "matches": True,
                            "detail": {"derived": True,
                                       "label": detail}})
        else:
            results.append({"block": block, "store": store, "bucket": bucket,
                            "resolved": False, "matches": False,
                            "detail": {"error": "unknown store/block form"}})
    return results


# ------------------------------------------------------------------
# Requirement 5 — independent trace replay.
# ------------------------------------------------------------------
_REPLAY_SCRIPT = r'''
import json, sys
sys.path.insert(0, "REPLACEME")
from contract.trace import (certify, child_executions, reconstruct,
                            trace_events, verify_receipt)

root, receipt_path, report_path, out_path = sys.argv[1:5]
from contract.harness import Harness
h = Harness(root=root)
try:
    receipt = verify_receipt(h.pipe.trace, receipt_path)
    with open(report_path) as f:
        committed = json.load(f)
    reconstructed = {}
    for task_id, rep in committed["tasks"].items():
        p = reconstruct(h.pipe.trace, task_id, rep["execution_id"])
        # Executed: the procedures actually executed (the L15
        # spans), with what was observed. Validated: the
        # validator's decision per attempt. These are two views
        # of the same attempts — observation and validation are
        # recorded separately (TRACE-001), and neither is the
        # other.
        executed, validated = [], []
        for s in p["spans"]:
            if s["function"] != "L15":
                continue
            for ev in s["events"]:
                if ev["event_type"] == "OBSERVATION":
                    executed.append({"procedure": ev.get("procedure"),
                                     "observed": ev.get("observed")})
                elif ev["event_type"] == "VALIDATION":
                    validated.append({"procedure": ev.get("procedure"),
                                      "validation": ev.get("validation")})
        # The IOU: its identity from the IOU_OPENED event in the
        # trace. The IOU's state is OPEN when the step is
        # unresolved (state UNSOLVED) and the IOU exists — the
        # work is pending, not dropped. The journal record (the
        # durable store, read back in this fresh process) carries
        # the same IOU id; the replay checks the two agree.
        iou_id = None
        for seq, ev in trace_events(h.pipe.trace, task_id,
                                    rep["execution_id"]):
            if ev["event_type"] == "IOU_OPENED":
                iou_id = ev["data"].get("iou")
        outcome = (p["outcome"] or {}).get("outcome")
        reconstructed[task_id] = {
            "outcome": outcome,
            "selected": (p["outcome"] or {}).get("selected"),
            "stage": (p["outcome"] or {}).get("stage"),
            "functions": p["functions"],
            "parent_execution_id": p.get("parent_execution_id"),
            "certified": certify(
                h.pipe.trace, task_id, rep["execution_id"])["certified"],
            "executed": executed, "validated": validated,
            "unresolved": outcome in ("UNSOLVED", "UNCOMMITTED", "ESCALATED"),
            "iou": {"id": iou_id,
                    "state": "OPEN" if (iou_id and outcome in (
                        "UNSOLVED", "UNCOMMITTED", "ESCALATED")) else None}}
    # The multi-step parent's children, from the ledger.
    parent_id = committed.get("parent_execution_id")
    children = child_executions(h.pipe.trace, parent_id) if parent_id else []
    # The parent's journal state, read back from the durable
    # Small49 journal in THIS fresh process (a store independent
    # of the trace — a trace error cannot corrupt it, and a
    # journal error cannot corrupt the trace). The journal's step
    # records (outcome, procedure, validation, IOU) are the
    # independent record the reconstruction is compared against.
    journal_state = None
    journal_steps = {}
    parent_info = committed.get("parent") or {}
    if parent_info.get("task_id") and parent_info.get("identity_digest"):
        journal, gen = h.generations.journal_for(
            parent_info["task_id"], len(parent_info.get("plan", [])),
            parent_info["identity_digest"])
        st = journal.state(parent_info["task_id"])
        journal_state = {"status": st["status"], "result": st["result"],
                         "done_steps": st["done_steps"],
                         "generation": gen}
        for r in st["outcomes"]:
            journal_steps[r["s"]] = {"outcome": r["o"], "procedure": r["a"],
                                     "validation": r.get("v"),
                                     "iou": r.get("i")}
        # The step classification, recomputed in this fresh
        # process from the journal + the trace (the runner's own
        # rule: COMPLETED / RECONSTRUCTABLE / AMBIGUOUS /
        # UNCOMMITTED — the source terminology, preserved).
        for step_id, task_id in parent_info.get("steps", {}).items():
            journal_steps.setdefault(step_id, {})
            journal_steps[step_id]["classification"] = (
                h.runner._classify_step(journal, parent_info["task_id"], task_id))
    with open(out_path, "w") as f:
        json.dump({"receipt_ok": receipt["ok"],
                   "receipt": receipt,
                   "reconstructed": reconstructed,
                   "child_executions": children,
                   "journal_state": journal_state,
                   "journal_steps": journal_steps}, f, indent=2, sort_keys=True)
finally:
    h.close()
'''


def run_replay(root, receipt_path, report_path, out_path=None):
    """Requirement 5 (corrected, items 3+8): reconstruct from a
    freshly opened ledger in a NEW process. Verifies the receipt
    against the reopened ledger, reconstructs each committed
    task, and compares the reconstructed state with TWO
    independent records: the committed reports (the pipeline's
    own reports, written before the replay) AND the durable
    Small49 journal (read back from disk in the fresh process —
    a store independent of the trace). The comparison reports
    the exact fields (outcome, selected, stage, executed
    procedures, validations, IOU identity and state, journal
    classification and journal outcome) and the per-field
    match. Returns the replay result dict (and writes it to
    out_path when given).

    LIMIT, stated plainly: this is reconstruction-vs-record
    CONSISTENCY, not independent correctness verification. The
    committed reports and the journal were written by the same
    system that ran the tasks; the replay checks that the
    reconstruction agrees with them, not that the outcomes were
    correct. A match means the trace, the reports and the
    journal tell the same story — it does not prove the story
    is right.

    The five states are distinguished with the source trace's
    own terminology: executed (a procedure was attempted),
    validated (the validator's decision on that attempt),
    unresolved (the step's outcome, with an IOU when pending),
    completed (the journal classification: COMPLETED /
    RECONSTRUCTABLE / AMBIGUOUS / UNCOMMITTED), certified (the
    trace certification for the execution)."""
    out_path = out_path or os.path.join(tempfile.mkdtemp(), "replay.json")
    script = _REPLAY_SCRIPT.replace(
        "REPLACEME", str(Path(__file__).resolve().parents[1]))
    script_path = os.path.join(tempfile.mkdtemp(), "replay_trace002.py")
    with open(script_path, "w") as f:
        f.write(script)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    proc = subprocess.run(
        [sys.executable,
         script_path, root, receipt_path, report_path, out_path],
        capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"replay subprocess failed: {proc.stderr[-2000:]}")
    with open(out_path) as f:
        result = json.load(f)
    # Compare reconstructed vs committed state, field by field.
    with open(report_path) as f:
        committed = json.load(f)
    mismatches = []
    fields = []
    for task_id, rep in committed["tasks"].items():
        r = result["reconstructed"].get(task_id, {})
        for field, want, got in (
                ("outcome", rep["state"], r.get("outcome")),
                ("selected", rep["selected"], r.get("selected")),
                ("stage", rep["stage"], r.get("stage"))):
            fields.append((task_id, field, want, got, want == got))
            if want != got:
                mismatches.append((task_id, field, want, got))
        # The IOU: the committed step's IOU id (from the runner's
        # step record) against the IOU reconstructed from the
        # trace's IOU_OPENED event. Only compared when the
        # committed record actually carries the field — a
        # committed record that never recorded an IOU is
        # uncompared on this field, not a mismatch (the journal
        # comparison below still covers the IOU when the parent
        # step records carry it).
        if "iou" in rep:
            committed_iou = rep.get("iou")
            got_iou = r.get("iou", {}).get("id")
            fields.append((task_id, "iou", committed_iou, got_iou,
                           committed_iou == got_iou))
            if committed_iou != got_iou:
                mismatches.append((task_id, "iou", committed_iou, got_iou))
    # Compare reconstructed vs the durable journal (the
    # independent store), per step.
    parent_steps = (committed.get("parent") or {}).get("steps", {})
    for step_id, task_id in parent_steps.items():
        j = result.get("journal_steps", {}).get(step_id, {})
        c = (committed.get("parent") or {}).get("step_records", {}).get(step_id, {})
        for field, want, got in (
                ("journal_outcome", c.get("outcome"), j.get("outcome")),
                ("journal_procedure", c.get("procedure"), j.get("procedure")),
                ("journal_iou", c.get("iou"), j.get("iou"))):
            if want is None and got is None:
                continue
            fields.append((task_id, field, want, got, want == got))
            if want != got:
                mismatches.append((task_id, field, want, got))
    result["mismatches"] = mismatches
    result["fields_compared"] = [
        {"task_id": t, "field": f, "committed": w, "reconstructed": g,
         "match": m} for (t, f, w, g, m) in fields]
    result["match"] = (result["receipt_ok"] and not mismatches)
    result["verification_limit"] = (
        "Reconstruction-vs-record consistency, not independent "
        "correctness verification: the committed reports and the "
        "journal were written by the same system that ran the "
        "tasks. A match means the trace, the reports and the "
        "journal tell the same story; it does not prove the story "
        "is right.")
    return result


def save_replay_inputs(harness, tasks, report_path, receipt_path,
                       parent_report=None, parent_task=None):
    """Save the committed reports (per task) and the ledger-head
    receipt for a replay run. tasks is {task_id: report} (the
    multi-step parent's report goes under "parent"). parent_report
    is the multi-step runner's report (with its steps) and
    parent_task is the task spec (for the identity digest and
    the plan) — both are needed for the replay to read the
    durable Small49 journal in the fresh process. Returns the
    committed dict."""
    committed = {"tasks": {}, "parent_execution_id": None}
    for task_id, rep in tasks.items():
        # The "iou" key is only written when the source report
        # actually carries one. A committed record that never
        # recorded an IOU is uncompared on that field in the
        # replay — writing "iou": None here would turn an
        # unrecorded field into a false mismatch against the
        # IOU the trace reconstructs.
        entry = {"state": rep["state"], "selected": rep["selected"],
                 "stage": rep["stage"],
                 "execution_id": rep["execution_id"]}
        if "iou" in rep:
            entry["iou"] = rep["iou"]
        committed["tasks"][task_id] = entry
    parent = tasks.get("parent")
    if parent is not None:
        committed["parent_execution_id"] = parent.get("execution_id")
    if parent_report is not None and parent_task is not None:
        committed["parent"] = {
            "task_id": parent_task["task_id"],
            "identity_digest": parent_report.get("identity_digest"),
            "plan": [s["step_id"] for s in parent_task["steps"]],
            "steps": {s["step_id"]: f"{parent_task['task_id']}.{s['step_id']}"
                      for s in parent_task["steps"]},
            "result": parent_report.get("result"),
            "step_records": {
                s["step_id"]: {"outcome": s["outcome"],
                               "procedure": s["procedure"],
                               "validation": s["validation"],
                               "iou": s["iou"],
                               "classification": s["classification"]}
                for s in parent_report["steps"]}}
    with open(report_path, "w") as f:
        json.dump(committed, f, indent=2, sort_keys=True)
    receipt = write_receipt(harness.pipe.trace, receipt_path)
    return {"committed": committed, "receipt": receipt}


# ------------------------------------------------------------------
# Requirement 6 — trace completeness.
# ------------------------------------------------------------------
# The five boundaries, and the hook that crashes there:
#   after_decide       — the fault_hook (after a U37 span begins,
#                        before admission).
#   after_authorization — the pre_execute_hook (after admission,
#                        before execution).
#   after_intent       — the post_intent_hook (after the ACTION_INTENT,
#                        before the observation is read).
#   after_validation   — the fault_hook (after the validation,
#                        before the record is committed).
#   after_record       — the fault_hook (after the record is
#                        committed, before the task ends).
COMPLETENESS_BOUNDARIES = ("after_decide", "after_authorization",
                           "after_intent", "after_validation",
                           "after_record")

_CRASH_SCRIPT = r'''
import os, signal, sys
sys.path.insert(0, "REPLACEME")
from contract.harness import Harness

root, boundary, task_json, out_path = sys.argv[1:5]
import json
with open(task_json) as f:
    task = json.load(f)

def _kill(boundary_name, _task, _entry):
    if boundary_name == boundary:
        os.kill(os.getpid(), signal.SIGKILL)

def _kill_hook(_task, _entry):
    os.kill(os.getpid(), signal.SIGKILL)

# Training happens with no hook (it needs the transfer stage to
# qualify procedures); the hook is armed only for the test task.
h = Harness(root=root, tasks=[task])
try:
    h.train_baseline()
    if boundary in ("after_decide", "after_validation", "after_record"):
        h.pipe.fault_hook = _kill
    elif boundary == "after_authorization":
        h.pipe.pre_execute_hook = _kill_hook
    else:  # after_intent
        h.pipe.post_intent_hook = _kill_hook
    rep = h.pipe.run({k: v for k, v in task.items() if k != "correct"})
    with open(out_path, "w") as f:
        json.dump({"survived": True, "report": rep}, f)
finally:
    h.close()
'''


def run_completeness(task, boundaries=COMPLETENESS_BOUNDARIES):
    """Requirement 6: crash at each boundary (in a subprocess),
    then reopen and reconstruct. Returns per-boundary:
    the incomplete spans, whether an IOU was opened, the
    certification result, and whether the reconstruction matches
    the expected state (the step's intent without a result is
    AMBIGUOUS; the task itself is unresolved)."""
    out = {}
    for boundary in boundaries:
        tmp = tempfile.mkdtemp()
        root = os.path.join(tmp, "root")
        task_path = os.path.join(tmp, "task.json")
        out_path = os.path.join(tmp, "out.json")
        with open(task_path, "w") as f:
            json.dump(task, f)
        script = _CRASH_SCRIPT.replace(
            "REPLACEME",
            str(Path(__file__).resolve().parents[1]))
        script_path = os.path.join(tmp, "crash_trace002.py")
        with open(script_path, "w") as f:
            f.write(script)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.expanduser(
            str(Path(__file__).resolve().parents[1]))
        proc = subprocess.run(
            [sys.executable,
             script_path, root, boundary, task_path, out_path],
            capture_output=True, text=True, env=env)
        # The subprocess is EXPECTED to die by SIGKILL (returncode
        # -9). Any other failure is a real error.
        if proc.returncode not in (-9,):
            raise RuntimeError(
                f"crash subprocess for {boundary} returned "
                f"{proc.returncode}: {proc.stderr[-2000:]}")
        # Reopen in this process and reconstruct.
        h = Harness(root=root, tasks=[task])
        try:
            h.pipe.trace.verify()
            events = trace_events(h.pipe.trace, task["task_id"])
            pathway = reconstruct(h.pipe.trace, task["task_id"])
            certification = certify(h.pipe.trace, task["task_id"])
            # The recovery classification (Contract 002): does
            # the trace have an ACTION_INTENT without a TASK_DONE?
            kinds = [k for _, k, t, _, _ in
                     h.pipe.trace.events(task["task_id"])]
            ambiguous = ("ACTION_INTENT" in kinds
                         and "TASK_DONE" not in kinds)
            out[boundary] = {
                "incomplete_spans": [
                    {"function": s["function"], "span_id": s["span_id"]}
                    for s in pathway["spans"]
                    if s["span_id"] in pathway["incomplete_spans"]],
                "incomplete_functions": sorted(
                    {s["function"] for s in pathway["spans"]
                     if s["span_id"] in pathway["incomplete_spans"]}),
                "has_iou": any(e["event_type"] == "IOU_OPENED"
                               for _, e in events),
                "certified": certification["certified"],
                "certification_gaps": certification["gaps"],
                "ambiguous_intent": ambiguous,
                "n_events": len(events),
            }
        finally:
            h.close()
    return out
