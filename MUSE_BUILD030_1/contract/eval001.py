"""EVAL-001 — cognitive capability evaluation harness.

Runs task sequences through three configurations on the frozen
Contract 002 baseline:
  UNTRAINED — no training records (trial only; nothing is trusted).
  TRAINED   — baseline training, description transfer OFF
              (applicability + analogy + under-test check only).
  FULL      — baseline training, description transfer ON
              (the Contract 002 configuration).

All three run the SAME frozen tasks with the SAME frozen
observations, so the comparison is fair. A configuration that
cannot beat the untrained control has not demonstrated learning;
a configuration that cannot beat the trained control has not
demonstrated that description-based transfer adds anything.

Failure taxonomy (JD's requirement): every non-resolution is
classified as selection, authorization, execution, validation,
memory_recovery, or correct_escalation (not a failure — the
system correctly refused or escalated). A false resolution
(resolved with the wrong procedure) is a validation failure:
the validator is the last line that should have caught it.
"""
from __future__ import annotations

import random

from .admission import Policy
from .harness import Harness, SOSO_MODULES
from .registry import (TRIAL_SAFE, by_native, check_hard_preconditions,
                       get as reg_get)

# ------------------------------------------------------------------
# Task families (the generator). "correct" is the native procedure
# id; "trap" is the procedure the system most often reaches for
# instead. Core tags are always present; pool tags are sampled.
# ------------------------------------------------------------------
FAMILIES = {
    "valve": {"correct": "P2", "trap": "P1",
              "core": ["valve", "firmware_v2"],
              "pool": ["stuck", "pressure", "high", "jammed", "seized",
                       "flow", "restricted", "rising", "line"],
              "conditions": {"firmware": "v2"}, "sig": "disk_full"},
    "sensor": {"correct": "P3", "trap": "P8",
               "core": ["sensor", "temperature"],
               "pool": ["drift", "readings", "unstable", "probe",
                        "wandering", "thermal", "erratic", "calibration"],
               "conditions": {}, "sig": "svc_hung"},
    "pump": {"correct": "P4", "trap": "P9",
             "core": ["pump", "intermittent"],
             "pool": ["vibration", "stalling", "cutting", "shaking",
                      "bearing", "flow", "housing"],
             "conditions": {}, "sig": "cfg_bad"},
    "blocked": {"correct": "P7", "trap": "P5",
                "core": ["route", "blocked"],
                "pool": ["debris", "obstacle", "rubble", "obstructed",
                         "fallen", "crates", "passage", "shut"],
                "conditions": {"kind": "detour"}, "sig": "cache_big"},
    "narrow": {"correct": "P6", "trap": "P10",
               "core": ["narrow", "clearance"],
               "pool": ["passage", "low", "traverse", "tight", "gap",
                        "move", "through"],
               "conditions": {"kind": "traverse"}, "sig": "narrow_1"},
    "slope": {"correct": "P7", "trap": "P11",
              "core": ["slope", "ground"],
              "pool": ["steep", "incline", "shifting", "cross", "loose",
                       "unstable"],
              "conditions": {}, "sig": "disk_almost_full"},
    "flood": {"correct": "P7", "trap": "P5",
              "core": ["flood", "water"],
              "pool": ["detour", "rising", "deep", "flowing", "bank",
                       "overflow"],
              "conditions": {}, "sig": "flood_1"},
}

# Synonym rewordings: same meaning, different words (no shared
# word with the procedure's description or applicability).
SYNONYMS = {
    "slope": (["hillside", "terrain"],
              ["incline", "shifting", "loose", "steep", "traverse",
               "ascend", "bank", "grade", "tilted", "uneven"]),
    "narrow": (["tight", "passage"],
               ["low", "gap", "move", "through", "squeeze", "confined"]),
    "blocked": (["shut", "passage"],
                ["debris", "obstacle", "rubble", "crates", "fallen"]),
}


def make_task(rng, family, task_id, synonym=False, n_pool=2):
    """One frozen task: core tags + sampled pool tags, the family's
    conditions and signature, and the correct procedure (kept out
    of the task dict the pipeline sees — it lives in the harness's
    criteria, never in the task record)."""
    fam = FAMILIES[family]
    if synonym and family in SYNONYMS:
        core, pool = SYNONYMS[family]
    else:
        core, pool = fam["core"], fam["pool"]
    tags = list(core) + rng.sample(pool, n_pool)
    return {"task_id": task_id, "signature": fam["sig"], "family": family,
            "tags": tags, "conditions": dict(fam["conditions"]),
            "requested_operation": "repair",
            "provenance": ("int:eval-001",), "correct": fam["correct"],
            "synonym": synonym}


def registered_id(native):
    """Native procedure id -> registered procedure id."""
    return by_native(native).procedure_id


# ------------------------------------------------------------------
# Configurations
# ------------------------------------------------------------------
CONFIGS = ("UNTRAINED", "TRAINED", "FULL")


def make_config_harness(name, tasks, root=None, profile="general"):
    """A harness in the named configuration, with the given tasks
    registered (criteria + frozen observations). TRAINED and FULL
    run the Contract 001 baseline training; UNTRAINED does not.
    TRACE-001: the cognitive profile (mechanical / legal /
    medical / general) is passed through to the pipeline."""
    if name == "TRAINED":
        h = Harness(root=root, tasks=tasks, descriptions=None,
                    profile=profile)
        h.train_baseline()
    elif name == "FULL":
        h = Harness(root=root, tasks=tasks, profile=profile)
        h.train_baseline()
    elif name == "UNTRAINED":
        h = Harness(root=root, tasks=tasks, profile=profile)
    else:
        raise ValueError(f"unknown config {name}")
    return h


# ------------------------------------------------------------------
# Failure taxonomy
# ------------------------------------------------------------------
def should_be_admitted(task, correct_native, policy):
    """Would the correct procedure be admitted for this task
    (hard preconditions satisfied, policy allows it)? Used to
    tell a genuine authorization failure from a correct refusal."""
    entry = by_native(correct_native)
    if not policy.decision(entry.procedure_id):
        return False
    hard = check_hard_preconditions(entry, task.get("conditions", {}),
                                    task.get("unknown_conditions", ()),
                                    task.get("contradictory_conditions", ()))
    return hard == "satisfied"


def classify(task, report, policy):
    """Classify a run's outcome. Returns None for a correct
    resolution; otherwise one of the failure classes, or
    "correct_escalation" when the system correctly refused or
    escalated (not a failure)."""
    correct = registered_id(task["correct"])
    if report["state"] == "RESOLVED":
        if report["selected"] == correct:
            return None
        # Resolved with the WRONG procedure: the validator is the
        # last line that should have caught it.
        return "validation"
    attempts = report["attempts"]
    att = [a for a in attempts if a["procedure"] == correct]
    if att and att[-1]["observed"] is False:
        return "execution"
    rej = [r for r in report["rejected"] if r["procedure"] == correct]
    if rej:
        if should_be_admitted(task, task["correct"], policy):
            return "authorization"
        return "correct_escalation"
    if not att and not rej:
        # The correct procedure was never proposed and never
        # refused. If it should have been admitted AND is
        # trial-eligible, the selection failed to find it (the
        # trial would have tried it). If it is not trial-eligible
        # (legacy / needs authorization) and has no trusted
        # record, the escalation is correct: the system may not
        # trial it blind and has never seen it work.
        if (should_be_admitted(task, task["correct"], policy)
                and by_native(task["correct"]).trial_safety == TRIAL_SAFE):
            return "selection"
        return "correct_escalation"
    return "correct_escalation"


def seed_legacy_experience(harness, procedure_native, task):
    """Seed a QUALIFIED record for a procedure that cannot be
    learned through the pipeline (its trial safety is
    REQUIRES_AUTHORIZATION, so the trial never tries it blind).
    This simulates a prior authorized execution + qualification:
    the setup fact the evaluation measures reuse of. The record
    is a normal Big49 experience block (source
    "prior-authorized-execution"); the projections rebuild from
    it like any other record. Documented in the log, not hidden."""
    proc_id = registered_id(procedure_native)
    harness.pipe.experiences.write(
        task_id=task["task_id"], signature=task["signature"],
        family=task["family"], procedure_id=proc_id,
        observed_outcome="SUCCESS", validator_result="PASS",
        conditions=dict(task.get("conditions", {})),
        qualification="QUALIFIED", source="prior-authorized-execution",
        tags=list(task["tags"]))


# ------------------------------------------------------------------
# Sequence runner with the qualification loop
# ------------------------------------------------------------------
def qualify_learned(harness, task, procedure_native, rng):
    """Qualify a trial-learned procedure: mark it under test, then
    run a held-out transfer task (same family, same correct
    procedure, overlapping core tags). Returns the qualify report
    or None if the procedure was not a CANDIDATE."""
    proc_id = registered_id(procedure_native)
    latest = harness.pipe.experiences.latest_per_pair().get(
        (task["task_id"], proc_id))
    if latest is None or latest["qual"] != "CANDIDATE":
        return None
    fam = task["family"]
    # The transfer task shares the TRIAL task's own conditions,
    # signature and core tags (a fresh pool draw): a valve-v1
    # trial needs a valve-v1 transfer task, or the exact index
    # serves the v2 procedure instead of the one being qualified.
    # A synonym trial needs a synonym transfer task, or the
    # under-test check (shared tags) cannot fire.
    if task.get("synonym") and fam in SYNONYMS:
        core, pool = SYNONYMS[fam]
    else:
        core, pool = FAMILIES[fam]["core"], FAMILIES[fam]["pool"]
    xfer = {"task_id": task["task_id"] + "-xfer",
            "signature": task["signature"], "family": fam,
            "tags": list(core) + rng.sample(pool, 2),
            "conditions": dict(task.get("conditions", {})),
            "requested_operation": "repair",
            "provenance": ("int:eval-001",), "correct": task["correct"],
            "synonym": task.get("synonym", False)}
    harness.add_task(xfer)
    return harness.pipe.qualify(
        task["task_id"], proc_id,
        {k: v for k, v in xfer.items() if k != "correct"})


def run_sequence(harness, tasks, rounds, rng, qualify=True):
    """qualify=False is the UNTRAINED control: it never learns, so
    every task is solved from scratch by trial."""
    """Run a task sequence organised into rounds (a list of lists
    of task indices). After each round except the last, qualify
    any CANDIDATE procedures learned in that round. Returns a list
    of per-task outcome records and per-round summaries."""
    outcomes = []
    for ri, round_idx in enumerate(rounds):
        for ti in round_idx:
            task = tasks[ti]
            rep = harness.pipe.run({k: v for k, v in task.items()
                                    if k != "correct"})
            outcomes.append({"task": task, "report": rep,
                             "round": ri,
                             "failure": classify(task, rep, harness.policy)})
        if qualify and ri < len(rounds) - 1:
            for o in outcomes:
                if o["round"] != ri:
                    continue
                rep = o["report"]
                if rep["state"] != "RESOLVED":
                    continue
                # Only trial-learned procedures need qualifying.
                if rep["stage"] == "higher-trial":
                    qualify_learned(harness, o["task"],
                                    o["task"]["correct"], rng)
    return outcomes


def summarize(outcomes):
    """Aggregate an outcome list into the evaluation metrics."""
    n = len(outcomes)
    resolved = [o for o in outcomes if o["report"]["state"] == "RESOLVED"]
    false = [o for o in outcomes if o["failure"] == "validation"]
    first = [o for o in resolved
             if o["report"]["stage"] in ("lower-exact", "soso-transfer")
             and len(o["report"]["attempts"]) == 1]
    attempts = sum(len(o["report"]["attempts"]) for o in outcomes)
    failures = {}
    for o in outcomes:
        if o["failure"]:
            failures[o["failure"]] = failures.get(o["failure"], 0) + 1
    return {"n": n, "resolved": len(resolved),
            "resolution_rate": len(resolved) / n if n else 0,
            "false_resolved": len(false),
            "first_attempt": len(first),
            "first_attempt_rate": len(first) / n if n else 0,
            "attempts": attempts,
            "attempts_per_task": attempts / n if n else 0,
            "escalated": sum(1 for o in outcomes
                             if o["report"]["state"] in ("UNSOLVED", "ESCALATED")),
            "failures": failures}


# ------------------------------------------------------------------
# Projection consistency + restart retention
# ------------------------------------------------------------------
def check_projections(harness):
    """Both projections must agree on the trusted pairs. The
    exact index is keyed by (signature, procedure): it holds a
    pair iff some record for it is QUALIFIED (several tasks share
    a signature, so the check is per (signature, procedure), not
    per task). The transfer layer's success sets must equal the
    (procedure, tags) pairs from TRUSTED records with a validated
    success. Returns a list of mismatches."""
    from adapters import cognitive_layer as cl
    proj = harness.pipe.experiences.rebuild_projections(SOSO_MODULES)
    # rebuild_projections is built from latest_per_pair, so the
    # check uses the same source: a QUALIFIED record later
    # withdrawn does not count as QUALIFIED.
    latest = harness.pipe.experiences.latest_per_pair()
    # success_sets returns lists; normalize to sets for comparison.
    succ = {k: set(v)
            for k, v in cl.success_sets(proj["transfer_conn"]).items()}
    mismatches = []
    qual_pairs = {(r["sig"], pid) for (tid, pid), r in latest.items()
                  if r["qual"] == "QUALIFIED"}
    exact_pairs = {(sig, pid) for sig, procs in proj["exact"].items()
                   for pid in procs}
    for pair in sorted(qual_pairs - exact_pairs):
        mismatches.append(f"{pair}: QUALIFIED but not in exact index")
    for pair in sorted(exact_pairs - qual_pairs):
        mismatches.append(f"{pair}: in exact index but not QUALIFIED")
    expected = {}
    for (tid, pid), r in latest.items():
        if (r["qual"] in ("QUALIFIED", "UNDER_TEST")
                and r["obs"] == "SUCCESS" and r["val"] == "PASS"):
            native = reg_get(pid).native_id
            expected.setdefault(native, set()).add(frozenset(r["tags"]))
    for proc in sorted(set(succ) | set(expected)):
        if succ.get(proc, set()) != expected.get(proc, set()):
            mismatches.append(
                f"{proc}: transfer layer {sorted(succ.get(proc, set()))} "
                f"!= trusted successes {sorted(expected.get(proc, set()))}")
    return mismatches


def retention_rate(harness, tasks):
    """Re-run the same tasks on the same harness and return the
    resolution rate (used before/after a restart to measure
    retained improvement)."""
    outs = []
    for task in tasks:
        rep = harness.pipe.run({k: v for k, v in task.items()
                                if k != "correct"})
        outs.append({"task": task, "report": rep,
                     "failure": classify(task, rep, harness.policy)})
    return summarize(outs)
