"""TRACE-003 — Cognitive Pathway Topology and Comparative Learning
(JD's TRACE-002 acceptance, 2026-11-18).

TRACE-001 recorded which functions and memory blocks were used.
TRACE-002 measured what changes when functions are removed, a
decision is wrong, or execution is interrupted. TRACE-003 uses
the recorder that now exists to discover which recurring
function sequences actually provide useful domain-specific
performance — and whether a learned pathway improves a held-out
task.

The work is deliberately NOT another recorder and NOT another
governance system. Every result here is built from reconstructed
pathways (contract/trace.py reconstruct), never from the final
answer alone. A plausible-looking trace is never mistaken for
proof of correct reasoning.

Honest scope, stated up front: the four domains (mechanical,
legal, clinical-reasoning, general technical) are SYNTHETIC
labels over one shared procedure fixture. The legal and
clinical tasks are not real legal or medical work — the
procedures they resolve to are the same mechanical / RealCog
procedures the rest of the fixture uses. What the domain
comparison measures is whether the PROFILE changes the executed
functions, the evidence requests and the investigation
behaviour — not domain competence. The profiles are
experimental strategies, not professional credentials.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

from .eval001 import make_config_harness
from .harness import TRAINING, TRANSFER_TASKS, Harness
from .profiles import PROFILES
from .trace import (TRACE_KIND, certify, child_executions, reconstruct,
                    trace_events, verify_receipt, write_receipt,
                    write_trace_export)
from .trace002 import (DOMAIN_STRESS_TASKS, MATCHED_TASKS, run_ablation,
                       run_domain_stress, run_wrong_decision)

# ------------------------------------------------------------------
# 1. Pathway signatures.
# ------------------------------------------------------------------
# A pathway signature is the ordered, comparable fingerprint of
# one execution's pathway: the functions in first-invocation
# order, the full span sequence, the stage and outcome, the
# domain transitions, the corrections, the memory accesses and
# the attempt count. The exact-cluster key is (functions, stage,
# outcome) — the coarsest identity that still distinguishes a
# transfer pathway from a trial pathway that happened to use the
# same functions.


def pathway_signature(pathway, report=None):
    """Build the signature of one reconstructed pathway (plus
    its run report, when given, for the attempt list). Returns
    a dict with the ordered function sequence, the stage and
    outcome, the transitions, the corrections, the memory
    accesses, and the exact-cluster key."""
    outcome = pathway.get("outcome") or {}
    attempts = (report or {}).get("attempts", [])
    info_requests = (report or {}).get("information_requests", [])
    return {
        "task_id": pathway.get("task_id"),
        "execution_id": pathway.get("execution_id"),
        "domain": pathway.get("domain"),
        "profile": pathway.get("profile"),
        "functions": list(pathway["functions"]),
        "function_sequence": [s["function"] for s in pathway["spans"]],
        "n_spans": len(pathway["spans"]),
        "stage": outcome.get("stage"),
        "outcome": outcome.get("outcome"),
        "selected": outcome.get("selected"),
        "n_attempts": len(attempts),
        "attempt_procedures": [a["procedure"] for a in attempts],
        "n_information_requests": len(info_requests),
        "information_requests": [
            {"missing": r.get("missing"), "question": r.get("question")}
            for r in info_requests],
        "transitions": [
            {"origin": t.get("origin"), "destination": t.get("destination"),
             "trigger": t.get("trigger"), "reason": t.get("reason")}
            for t in pathway["domain_transitions"]],
        "n_transitions": len(pathway["domain_transitions"]),
        "n_corrections": len(pathway["corrections"]),
        "corrections": [
            (c.get("procedure"), c.get("previous_qualification"),
             c.get("new_qualification"))
            for c in pathway["corrections"]],
        "memory_blocks": [(m["store"], m["block"])
                          for m in pathway["memory_blocks"]],
        "memory_stores": sorted({m["store"]
                                 for m in pathway["memory_blocks"]}),
        "n_memory_accesses": len(pathway["memory_blocks"]),
        "incomplete": bool(pathway["incomplete_spans"]),
        "certified": None,  # filled by the caller (certify needs the log)
        "key": (tuple(pathway["functions"]), outcome.get("stage"),
                outcome.get("outcome")),
        "key_str": ("|".join(pathway["functions"]) + " :: "
                    + str(outcome.get("stage")) + " :: "
                    + str(outcome.get("outcome"))),
    }


def extract_signatures(trace_log, task_ids, reports=None):
    """Extract the pathway signature for each task id in the
    given ledger. reports is an optional {task_id: report}
    mapping (for the attempt lists). Returns {task_id: sig}."""
    out = {}
    reports = reports or {}
    for tid in task_ids:
        pathway = reconstruct(trace_log, tid)
        sig = pathway_signature(pathway, reports.get(tid))
        sig["certified"] = certify(trace_log, tid)["certified"]
        out[tid] = sig
    return out


# ------------------------------------------------------------------
# 2. Domain comparison.
# ------------------------------------------------------------------
# Unfamiliar synthetic tasks, three per domain. Each has a novel
# signature and a correct procedure the trial can find, so the
# investigation path is exercised. The domains are labels over
# one shared procedure fixture (see the module docstring) — the
# measurement is the PROFILE's effect on the pathway.
DOMAIN_TASKS = {
    "mechanical": [
        {"task_id": "dm-mech-a", "signature": "mech_valve_new",
         "family": "mechanical",
         "tags": ["valve", "stuck", "pressure", "seal"],
         "conditions": {"firmware": "v2"},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "P2"},
        {"task_id": "dm-mech-b", "signature": "mech_cache_new",
         "family": "mechanical",
         "tags": ["cache", "full", "memory", "evict"],
         "conditions": {},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "clear_cache"},
        {"task_id": "dm-mech-c", "signature": "mech_pump_new",
         "family": "mechanical",
         "tags": ["pump", "rattle", "bearing", "hum"],
         "conditions": {"kind": "pump"},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "P4"},
    ],
    "legal": [
        {"task_id": "dm-legal-a", "signature": "legal_evidence_x",
         "family": "legal",
         "tags": ["evidence", "authority", "precedent", "brief"],
         "conditions": {},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "P6"},
        {"task_id": "dm-legal-b", "signature": "legal_cache_x",
         "family": "legal",
         "tags": ["cache", "record", "retention", "archive"],
         "conditions": {},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "clear_cache"},
        {"task_id": "dm-legal-c", "signature": "legal_sensor_x",
         "family": "legal",
         "tags": ["sensor", "reading", "calibration", "drift"],
         "conditions": {"kind": "sensor"},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "P3"},
    ],
    "clinical": [
        {"task_id": "dm-clin-a", "signature": "clin_symptom_x",
         "family": "clinical",
         "tags": ["symptom", "finding", "diagnosis", "referral"],
         "conditions": {},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "P6"},
        {"task_id": "dm-clin-b", "signature": "clin_cache_x",
         "family": "clinical",
         "tags": ["cache", "chart", "record", "retrieval"],
         "conditions": {},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "clear_cache"},
        {"task_id": "dm-clin-c", "signature": "clin_valve_x",
         "family": "clinical",
         "tags": ["valve", "flow", "pressure", "reading"],
         "conditions": {"firmware": "v2"},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "P2"},
    ],
    "general": [
        {"task_id": "dm-gen-a", "signature": "gen_system_x",
         "family": "general",
         "tags": ["system", "fault", "diagnostic", "log"],
         "conditions": {},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "P6"},
        {"task_id": "dm-gen-b", "signature": "gen_cache_x",
         "family": "general",
         "tags": ["cache", "stale", "refresh", "memory"],
         "conditions": {},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "clear_cache"},
        {"task_id": "dm-gen-c", "signature": "gen_slope_x",
         "family": "general",
         "tags": ["slope", "ground", "route", "detour"],
         "conditions": {},
         "requested_operation": "repair",
         "provenance": ("int:trace-003",), "correct": "P7"},
    ],
}

PROFILE_IDS = ("mechanical", "legal", "medical", "general")


def _run_one(harness, task):
    rep = harness.pipe.run({k: v for k, v in task.items() if k != "correct"})
    pathway = reconstruct(harness.pipe.trace, task["task_id"])
    return rep, pathway


def run_domain_comparison(tasks_by_domain=None, profiles=PROFILE_IDS):
    """Requirement 2 + 3: run each domain's unfamiliar tasks
    under each profile (fresh harness per profile, identical
    training — the profile is applied after training, like
    TRACE-001's train-under-general-then-set_profile).
    Returns {domain: {profile: {task_id: {report, pathway,
    signature}}}} plus a per-(domain, profile) summary:
    the functions actually invoked, the stages, the attempt
    counts, the information requests, and whether the profile
    changed the pathway relative to the general profile."""
    tasks_by_domain = tasks_by_domain or DOMAIN_TASKS
    out = {}
    for domain, tasks in tasks_by_domain.items():
        out[domain] = {}
        for profile_id in profiles:
            # Train under the general profile (the training needs
            # the trial stage to qualify procedures — the legal
            # and medical profiles forbid trial, so training
            # under them cannot resolve the training tasks), then
            # apply the profile for the test tasks only.
            h = Harness(tasks=[t for ts in tasks_by_domain.values()
                               for t in ts], profile="general")
            try:
                h.train_baseline()
                if profile_id != "general":
                    h.pipe.set_profile(profile_id)
                results = {}
                for t in tasks:
                    rep, pathway = _run_one(h, t)
                    sig = pathway_signature(pathway, rep)
                    sig["certified"] = certify(
                        h.pipe.trace, t["task_id"])["certified"]
                    results[t["task_id"]] = {
                        "report": rep, "pathway": pathway, "signature": sig}
                functions = sorted({f for r in results.values()
                                    for f in r["signature"]["functions"]})
                stages = Counter(r["signature"]["stage"]
                                 for r in results.values())
                attempts = sum(r["signature"]["n_attempts"]
                               for r in results.values())
                info = sum(r["signature"]["n_information_requests"]
                           for r in results.values())
                resolved = sum(1 for r in results.values()
                               if r["report"]["state"] == "RESOLVED")
                out[domain][profile_id] = {
                    "results": results,
                    "summary": {"functions": functions,
                                "stages": dict(stages),
                                "attempts": attempts,
                                "information_requests": info,
                                "resolved": resolved, "of": len(tasks)}}
            finally:
                h.close()
    # Did the profile change the pathway relative to general?
    for domain in out:
        general_keys = {tid: r["signature"]["key_str"]
                        for tid, r in out[domain]["general"]["results"].items()}
        for profile_id in profiles:
            changed = {tid: (r["signature"]["key_str"] != general_keys[tid])
                       for tid, r in out[domain][profile_id]["results"].items()}
            out[domain][profile_id]["summary"]["pathway_changed_vs_general"] = changed
            out[domain][profile_id]["summary"]["n_pathways_changed"] = sum(changed.values())
    return out


def _failure_rates(signatures):
    """Resolved / failed counts per stratum and overall, for
    one signature set. A stratum is a domain, a profile, an
    ablation arm, or the wrong-decision / domain-stress
    singletons. The caller computes this twice — once on the
    deduplicated set (unique-case) and once on the raw set
    (execution-weighted) — and reports both (JD item 2)."""

    def rate(sigs):
        resolved = sum(1 for s in sigs if s["outcome"] == "RESOLVED")
        return {"resolved": resolved, "of": len(sigs),
                "failed": len(sigs) - resolved,
                "failure_rate": (len(sigs) - resolved) / len(sigs)
                if sigs else None}

    by_domain, by_profile, by_arm = {}, {}, {}
    for s in signatures:
        if s.get("domain_label"):
            by_domain.setdefault(s["domain_label"], []).append(s)
        if s.get("profile_label"):
            by_profile.setdefault(s["profile_label"], []).append(s)
        if s.get("ablation_arm"):
            by_arm.setdefault(s["ablation_arm"], []).append(s)
    return {"overall": rate(signatures),
            "by_domain": {k: rate(v) for k, v in sorted(by_domain.items())},
            "by_profile": {k: rate(v) for k, v in sorted(by_profile.items())},
            "by_arm": {k: rate(v) for k, v in sorted(by_arm.items())}}


# ------------------------------------------------------------------
# 3. Clustering.
# ------------------------------------------------------------------
def deduplicate_signatures(signatures):
    """One signature per unique execution. The same execution
    legitimately appears under more than one label — the three
    L24 levels alias arms ("l24_function" IS the L24 arm,
    "l24_strategy" IS the U08 arm), so the same execution_id
    occurs twice in a mixed set. Any aggregate (clusters,
    pattern counts, medians) computed over the raw mixed set
    double-counts those executions. Deduplicate by
    execution_id, keep the first occurrence, and report how
    many were removed. Signatures with no execution_id are
    kept (they cannot be shown to be duplicates)."""
    seen = set()
    unique = []
    removed = 0
    for sig in signatures:
        exec_id = sig.get("execution_id")
        if exec_id is None:
            unique.append(sig)
            continue
        if exec_id in seen:
            removed += 1
            continue
        seen.add(exec_id)
        unique.append(sig)
    return {"signatures": unique, "n_raw": len(signatures),
            "n_unique": len(unique), "n_duplicates_removed": removed}


def cluster_signatures(signatures):
    """Group pathway signatures into exact clusters (same
    (functions, stage, outcome) key) and near clusters (same
    stage, function-set Jaccard >= 0.8). Each cluster retains
    the member executions (task_id, execution_id, outcome,
    stage, selected), the success rate, and the source
    references — the cluster never replaces the events it
    summarises. signatures is a list of signature dicts."""
    exact = defaultdict(list)
    for sig in signatures:
        exact[sig["key_str"]].append(sig)
    exact_clusters = []
    for key, members in sorted(exact.items()):
        resolved = sum(1 for m in members if m["outcome"] == "RESOLVED")
        exact_clusters.append({
            "key": key,
            "functions": members[0]["functions"],
            "stage": members[0]["stage"],
            "outcome": members[0]["outcome"],
            "n_members": len(members),
            "n_resolved": resolved,
            "success_rate": resolved / len(members) if members else 0,
            "members": [{"task_id": m["task_id"],
                         "execution_id": m["execution_id"],
                         "outcome": m["outcome"], "stage": m["stage"],
                         "selected": m["selected"],
                         "n_attempts": m["n_attempts"]}
                        for m in members]})
    # Near clusters: same stage, function-set Jaccard >= 0.8.
    # Greedy single-linkage over the exact clusters.
    near = []
    used = set()
    for i, c in enumerate(exact_clusters):
        if i in used:
            continue
        group = [c]
        used.add(i)
        fset = set(c["functions"])
        for j, d in enumerate(exact_clusters):
            if j in used or d["stage"] != c["stage"]:
                continue
            other = set(d["functions"])
            jac = (len(fset & other) / len(fset | other)
                   if fset | other else 1.0)
            if jac >= 0.8:
                group.append(d)
                used.add(j)
        n = sum(g["n_members"] for g in group)
        r = sum(g["n_resolved"] for g in group)
        near.append({"stage": c["stage"],
                     "functions": sorted(fset),
                     "n_exact_clusters": len(group),
                     "n_members": n, "n_resolved": r,
                     "success_rate": r / n if n else 0,
                     "exact_keys": [g["key"] for g in group]})
    return {"exact": exact_clusters, "near": near}


# ------------------------------------------------------------------
# 4. Ablation at the pathway level (three intervention types).
# ------------------------------------------------------------------
def run_pathway_ablation():
    """Requirement 5: the TRACE-002 multi-function ablation,
    measured at the pathway-signature level. For each arm, the
    signature distribution (which signatures disappear, which
    appear, how the stages shift) — not just the resolution
    count. The three L24 intervention types (function,
    information source, strategy) stay distinct."""
    ablation = run_ablation()
    out = {}
    for arm, data in ablation.items():
        sigs = []
        pathways = []
        for r in data["results"]:
            sig = pathway_signature(r["pathway"], r["report"])
            sig["certified"] = True  # certified in the arm's run
            sigs.append(sig)
            pathways.append(r["pathway"])
        keys = Counter(s["key_str"] for s in sigs)
        out[arm] = {"signatures": sigs, "pathways": pathways,
                    "keys": dict(keys),
                    "n_distinct_signatures": len(keys),
                    "summary": data["summary"]}
    control_keys = set(out["control"]["keys"])
    for arm, data in out.items():
        keys = set(data["keys"])
        data["signatures_lost_vs_control"] = sorted(control_keys - keys)
        data["signatures_gained_vs_control"] = sorted(keys - control_keys)
    return out


# ------------------------------------------------------------------
# 5. Recurring patterns: inefficient, misleading, correction.
# ------------------------------------------------------------------
def find_inefficient_pathways(signatures):
    """A resolved pathway is inefficient when it took at least
    twice the median attempt count of the resolved pathways in
    the same set, or its function sequence is at least twice
    the median length. Returns the inefficient signatures with
    the medians they are judged against. The definition is
    explicit and relative to the set — it is not a claim that
    a long pathway is wrong, only that it cost more than its
    peers to reach the same outcome."""
    resolved = [s for s in signatures if s["outcome"] == "RESOLVED"]
    if not resolved:
        return {"median_attempts": None, "median_sequence_len": None,
                "inefficient": []}
    attempts = sorted(s["n_attempts"] for s in resolved)
    seq_lens = sorted(len(s["function_sequence"]) for s in resolved)
    med_a = attempts[len(attempts) // 2]
    med_s = seq_lens[len(seq_lens) // 2]
    inefficient = []
    for s in resolved:
        reasons = []
        if med_a and s["n_attempts"] >= 2 * med_a and s["n_attempts"] > med_a:
            reasons.append(f"attempts {s['n_attempts']} >= 2x median {med_a}")
        if med_s and len(s["function_sequence"]) >= 2 * med_s \
                and len(s["function_sequence"]) > med_s:
            reasons.append(
                f"sequence {len(s['function_sequence'])} >= 2x median {med_s}")
        if reasons:
            inefficient.append({"task_id": s["task_id"], "stage": s["stage"],
                                "selected": s["selected"],
                                "n_attempts": s["n_attempts"],
                                "function_sequence": s["function_sequence"],
                                "reasons": reasons})
    return {"median_attempts": med_a, "median_sequence_len": med_s,
            "inefficient": inefficient}


def find_misleading_transfers(signatures):
    """A domain transition that did not solve the task: the
    pathway has a domain transition AND resolves (or fails) via
    higher-trial — the transfer stage was entered and did not
    produce the resolution. The transition is recorded with its
    reason and evidence; the pathway's stage is the verdict.
    (A transition that resolves via soso-transfer is, by the
    same rule, a helpful one.)"""
    out = []
    for s in signatures:
        if not s["transitions"]:
            continue
        helped = s["stage"] == "soso-transfer"
        out.append({"task_id": s["task_id"], "stage": s["stage"],
                    "outcome": s["outcome"], "selected": s["selected"],
                    "transitions": s["transitions"],
                    "helped": helped})
    return {"n_transitions": sum(len(s["transitions"]) for s in signatures),
            "n_helped": sum(1 for o in out if o["helped"]),
            "n_misleading": sum(1 for o in out if not o["helped"]),
            "cases": out}


def find_correction_sequences(signatures):
    """Pathways containing a CORRECTION event: the wrong
    candidate, its demotion, the revised candidate and the
    subsequent outcome. A correction sequence is useful when
    the pathway ends RESOLVED after the correction — the
    correction is what the pathway learned, not what it lost."""
    out = []
    for s in signatures:
        if not s["corrections"]:
            continue
        out.append({"task_id": s["task_id"], "stage": s["stage"],
                    "outcome": s["outcome"], "selected": s["selected"],
                    "corrections": [{"procedure": c[0], "from": c[1], "to": c[2]}
                                    for c in s["corrections"]],
                    "attempt_procedures": s["attempt_procedures"],
                    "useful": s["outcome"] == "RESOLVED"})
    return {"n_correction_pathways": len(out),
            "n_useful": sum(1 for o in out if o["useful"]),
            "cases": out}


# ------------------------------------------------------------------
# 6. Memory-block access topology.
# ------------------------------------------------------------------
def _span_function_for_memory(pathway):
    """The function-to-memory linkage, derived from the
    span/event structure — NOT from a field on the memory
    record (JD, TRACE-003 GO item 1). reconstruct() attaches
    each MEMORY_* record to its enclosing span's "memory"
    list; the record in the pathway's flat memory_blocks list
    is the same object. Joining on seq/event_id gives, for
    each memory access, the function whose span made it. A
    record no span claims (a parent span, or a span from
    another execution) maps to None: unattributed, not
    guessed."""
    func_by_seq = {}
    for span in pathway.get("spans", []):
        for rec in span.get("memory", []):
            func_by_seq[rec.get("seq")] = span.get("function")
            func_by_seq[rec.get("event_id")] = span.get("function")
    return func_by_seq


def memory_topology(pathways):
    """The memory-block access topology over a list of
    (execution_id, pathway) pairs: for each block, the
    functions that accessed it (derived from the span/event
    linkage), the operations, the executions it appears in,
    and whether it was written in one execution and
    read/retrieved in a later one (reuse). Returns nodes
    (blocks) and edges (function -> block, and block reuse
    across executions). pathways is a list of pathway dicts
    (each carries execution_id and memory_blocks).

    REUSE IS PER ROOT (TRACE-003 fix): a pathway may carry a
    "root" key naming the ledger it was reconstructed from.
    A block counts as reused only when the write and the
    read/retrieve happened in the SAME root. Pathways from
    different ledgers share block ids (every root writes an
    exp block for P7) but they are different stores — counting
    a write in one ledger and a read in another as reuse
    would be a false claim. Pathways with no "root" key are
    treated as one root ("default"). The return carries the
    per-root block counts and the root on each reuse entry.

    REPEATED REFERENCES vs GENUINE REUSE (JD, TRACE-003 GO
    item 1): a block read many times is a repeated reference,
    not reuse by itself. Each reuse entry therefore names
    the writing tasks and the reading tasks, the number of
    reads and the number of distinct reading tasks, and
    whether any reading task differs from the writing tasks
    (cross_task — genuine reuse across tasks) or every read
    is by the same task that wrote it (same_task_only — a
    re-presentation re-reading its own record). The summary
    reports n_reused, n_cross_task_reuse, n_same_task_reread
    and n_repeated_references (read accesses beyond the first
    per block, summed over blocks)."""
    nodes = {}
    for p in pathways:
        exec_id = p.get("execution_id")
        root = p.get("root", "default")
        task_id = p.get("task_id")
        func_by_seq = _span_function_for_memory(p)
        for m in p["memory_blocks"]:
            block = m["block"]
            node = nodes.setdefault(block, {
                "block": block, "store": m["store"], "bucket": m.get("bucket"),
                "accesses": [], "functions": set(), "operations": set(),
                "executions": [], "roots": set()})
            # The function is derived from the span linkage,
            # never read off the record itself.
            func = (func_by_seq.get(m.get("seq"))
                    or func_by_seq.get(m.get("event_id")))
            node["accesses"].append({
                "execution_id": exec_id, "operation": m["operation"],
                "task_id": task_id, "root": root, "function": func})
            if func:
                node["functions"].add(func)
            node["operations"].add(m["operation"])
            node["roots"].add(root)
            if exec_id not in node["executions"]:
                node["executions"].append(exec_id)
    # Written in one execution, read/retrieved in a later one
    # — in the SAME root. A block written during training and
    # retrieved by a test task in the same ledger is reuse;
    # the same block id in two different ledgers is not.
    reuse = []
    n_repeated_references = 0
    for block, node in nodes.items():
        reads_all = [a for a in node["accesses"]
                     if a["operation"] in ("read", "retrieve")]
        if reads_all:
            n_repeated_references += max(0, len(reads_all) - 1)
        for root in sorted(node["roots"]):
            writes = [a for a in node["accesses"]
                      if a["operation"] == "write" and a["root"] == root]
            reads = [a for a in node["accesses"]
                     if a["operation"] in ("read", "retrieve")
                     and a["root"] == root]
            if writes and reads:
                written_by = sorted({a["task_id"] for a in writes})
                read_by = sorted({a["task_id"] for a in reads})
                cross_task = any(t not in written_by for t in read_by)
                reuse.append({"block": block, "store": node["store"],
                              "root": root,
                              "written_in": sorted({a["execution_id"]
                                                    for a in writes}),
                              "read_in": sorted({a["execution_id"]
                                                 for a in reads}),
                              "written_by": written_by,
                              "read_by": read_by,
                              "n_reads": len(reads),
                              "n_reading_tasks": len(read_by),
                              "n_reading_executions": len(
                                  {a["execution_id"] for a in reads}),
                              "cross_task": cross_task,
                              "same_task_only": not cross_task,
                              "n_executions": len(
                                  {a["execution_id"]
                                   for a in writes + reads})})
    for node in nodes.values():
        node["functions"] = sorted(f for f in node["functions"] if f)
        node["operations"] = sorted(node["operations"])
        node["roots"] = sorted(node["roots"])
        node["n_accesses"] = len(node["accesses"])
        node["n_executions"] = len(node["executions"])
    edges = [{"from": f, "to": block, "relation": "ACCESSED"}
             for block, node in nodes.items() for f in node["functions"]]
    roots = sorted({p.get("root", "default") for p in pathways})
    return {"nodes": nodes, "edges": edges, "reuse": reuse,
            "n_blocks": len(nodes),
            "n_reused": len(reuse),
            "n_cross_task_reuse": sum(1 for r in reuse if r["cross_task"]),
            "n_same_task_reread": sum(
                1 for r in reuse if r["same_task_only"]),
            "n_repeated_references": n_repeated_references,
            "roots": roots,
            "n_roots": len(roots),
            "blocks_by_store": dict(Counter(
                n["store"] for n in nodes.values())),
            "blocks_by_root": {r: sum(
                1 for n in nodes.values() if r in n["roots"])
                for r in roots}}


def _executions_for_task(trace_log, task_id):
    """All execution_ids for a task in a ledger, in order, from
    the TASK_BEGIN events. A task id run more than once (a
    training task run by trial and re-presented) has more than
    one execution; each is a separate pathway. NOTE: this reads
    the raw ledger rows, not trace_events() — trace_events()
    with no execution_id returns only the LATEST execution,
    which would silently drop every earlier run of the task."""
    exec_ids = []
    for seq, _kind, _task, _step, data in trace_log.events(
            task_id, kinds=[TRACE_KIND]):
        ev = json.loads(data) if isinstance(data, str) else dict(data)
        if ev.get("event_type") == "TASK_BEGIN":
            eid = ev.get("execution_id")
            if eid and eid not in exec_ids:
                exec_ids.append(eid)
    return exec_ids


def _reconstruct_executions(trace_log, task_ids, root, phases):
    """Reconstruct every execution of each task id in the
    ledger. phases maps task_id -> phase label ("training" /
    "test" / "after_restart"). Each pathway is tagged with its
    root and phase."""
    out = []
    for tid in task_ids:
        for eid in _executions_for_task(trace_log, tid):
            p = reconstruct(trace_log, tid, eid)
            p["root"] = root
            p["phase"] = phases.get(tid, "test")
            out.append(p)
    return out


# Shared-memory experiment tasks: two genuinely new slope
# tasks (not in MATCHED_TASKS) that share the word "ground"
# with P7's description, so they should retrieve the same
# experience block the fixture's slope tasks retrieve.
SHARED_MEMORY_NEW = [
    {"task_id": "sm-slope-new", "signature": "sm_slope_new",
     "family": "slope", "tags": ["slope", "ground", "ascent", "path"],
     "conditions": {}, "requested_operation": "repair",
     "provenance": ("int:trace-003",), "correct": "P7"},
    {"task_id": "sm-slope-new2", "signature": "sm_slope_new2",
     "family": "slope", "tags": ["slope", "ground", "ramp", "cross"],
     "conditions": {}, "requested_operation": "repair",
     "provenance": ("int:trace-003",), "correct": "P7"},
]


def run_shared_memory_experiment():
    """A DELIBERATE shared-memory experiment (JD, TRACE-003 GO):
    one record, many readers — so genuine cross-task memory
    reuse can be measured, rather than reporting zero reuse
    from experiments that cannot share records. One ledger:
    train P7 (blocked), then run shared tasks from different
    families (slope x4, narrow, blocked — all known to
    retrieve the blocked-c P7 experience block) plus two
    genuinely new slope tasks, and control tasks with no
    shared vocabulary (flood, valve, sensor — they must NOT
    retrieve the P7 block). Reopen the root and run the second
    new slope task after the restart. Reports, per block: the
    distinct reading tasks, the read count, whether the reuse
    is cross-task, and whether a read happened after the
    restart. The shared block is named explicitly:
    exp.ct-train-blocked-c.P7.2."""
    shared_tasks = [t for t in MATCHED_TASKS
                    if t["task_id"].startswith("ab-xfer-slope")
                    or t["task_id"] in ("ab-trial-narrow",
                                        "ab-exact-blocked")]
    control_tasks = [t for t in MATCHED_TASKS
                     if t["task_id"] in ("ab-trial-flood",
                                         "ab-trial-valve",
                                         "ab-trial-sensor")]
    all_tasks = (shared_tasks + control_tasks
                 + [SHARED_MEMORY_NEW[0]])
    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, "shared-memory-root")
    root_label = "shared-memory-root"
    try:
        h = Harness(root=root, tasks=all_tasks)
        try:
            h.train_baseline()
            reports = {}
            for t in shared_tasks + control_tasks:
                rep, _ = _run_one(h, t)
                reports[t["task_id"]] = rep
            new_rep, _ = _run_one(h, SHARED_MEMORY_NEW[0])
            reports[SHARED_MEMORY_NEW[0]["task_id"]] = new_rep
        finally:
            h.close()
        # Reopen the same root and run the second new task
        # after the restart.
        h2 = Harness(root=root, tasks=all_tasks + [SHARED_MEMORY_NEW[1]])
        try:
            for t in TRAINING:
                h2.add_task(t)
            for x in TRANSFER_TASKS.values():
                h2.add_task(x)
            new2_rep = h2.pipe.run(
                {k: v for k, v in SHARED_MEMORY_NEW[1].items()
                 if k != "correct"})
            reports[SHARED_MEMORY_NEW[1]["task_id"]] = new2_rep
            phases = {}
            for t in TRAINING:
                phases[t["task_id"]] = "training"
            for x in TRANSFER_TASKS.values():
                phases[x["task_id"]] = "training"
            for t in all_tasks:
                phases[t["task_id"]] = "test"
            pathways = _reconstruct_executions(
                h2.pipe.trace,
                [t["task_id"] for t in TRAINING]
                + [x["task_id"] for x in TRANSFER_TASKS.values()]
                + [t["task_id"] for t in all_tasks]
                + [SHARED_MEMORY_NEW[1]["task_id"]],
                root_label, phases)
            for p in pathways:
                if p["execution_id"] == new2_rep["execution_id"]:
                    p["phase"] = "after_restart"
            topology = memory_topology(pathways)
        finally:
            h2.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    shared_block = "exp.ct-train-blocked-c.P7.2"
    shared_entry = next(
        (r for r in topology["reuse"] if r["block"] == shared_block), None)
    # Which tasks retrieved the shared block (from the
    # pathways' memory records, not from the topology alone).
    retrieved_by = {}
    for p in pathways:
        blocks = {m["block"] for m in p["memory_blocks"]}
        if shared_block in blocks:
            retrieved_by.setdefault(p["task_id"], []).append(
                p["execution_id"])
    control_retrieved = {t["task_id"]: (t["task_id"] in retrieved_by)
                         for t in control_tasks}
    # The second shared record: the transfer-index. It is a
    # derived index (rebuilt from Big49), read by every
    # transfer task and never written as a block event — so
    # it is a SHARED READ-ONLY record, not a write+read reuse
    # entry. The two new slope tasks resolve via soso-transfer
    # and retrieve the transfer-index, not the exp block: the
    # exp-block read is an exact-signature lookup, and a new
    # signature has no exact record. Both sharing mechanisms
    # are reported, with the mechanism named — they are not
    # the same claim.
    index_readers = {}
    for p in pathways:
        blocks = {m["block"] for m in p["memory_blocks"]}
        if "transfer-index" in blocks:
            index_readers.setdefault(p["task_id"], []).append(
                p["execution_id"])
    new_tasks = {}
    for t in SHARED_MEMORY_NEW:
        rep = reports[t["task_id"]]
        p = next((q for q in pathways
                  if q["execution_id"] == rep["execution_id"]), None)
        blocks = [m["block"] for m in p["memory_blocks"]] if p else []
        new_tasks[t["task_id"]] = {
            "stage": rep["stage"],
            "resolved": rep["state"] == "RESOLVED",
            "retrieved_exp_block": shared_block in blocks,
            "retrieved_transfer_index": "transfer-index" in blocks}
    return {"topology": topology,
            "n_executions": len(pathways),
            "shared_block": shared_block,
            "shared_entry": shared_entry,
            "retrieved_by": retrieved_by,
            "n_reading_tasks": len(retrieved_by),
            "shared_records": [
                {"block": shared_block,
                 "mechanism": "exact-signature lookup (write+read reuse)",
                 "reading_tasks": sorted(retrieved_by),
                 "n_reading_tasks": len(retrieved_by),
                 "n_reads": shared_entry["n_reads"] if shared_entry else 0,
                 "cross_task": bool(shared_entry and
                                  shared_entry["cross_task"])},
                {"block": "transfer-index",
                 "mechanism": "description-transfer lookup "
                              "(shared read-only record; never written "
                              "as a block event)",
                 "reading_tasks": sorted(index_readers),
                 "n_reading_tasks": len(index_readers),
                 "n_reads": sum(len(v) for v in index_readers.values()),
                 "cross_task": len(index_readers) > 1}],
            "new_tasks": new_tasks,
            "control_retrieved": control_retrieved,
            "reports": reports,
            "resolved": {tid: (rep["state"] == "RESOLVED")
                         for tid, rep in reports.items()}}


def run_topology_root():
    """Requirement 7, corrected (TRACE-003 fix for defect a):
    the memory-block access topology over a SINGLE root that
    contains the writes AND the reads — one ledger, training
    executions (which write the experience blocks) + test
    executions (which retrieve them) + one execution after a
    fresh-process restart (which retrieves a block written
    before the restart). The earlier topology reported 0
    reused blocks because it was built over pathways from
    separate ledgers whose training writes were never in the
    set — the counting was right, the set was wrong.

    Returns the topology plus the pathway list it was built
    from, the per-phase execution counts, and the reuse
    entries split by whether the read happened before or
    after the restart."""
    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, "topology-root")
    root_label = "topology-root"
    try:
        h = Harness(root=root, tasks=list(MATCHED_TASKS))
        try:
            h.train_baseline()
            for t in MATCHED_TASKS:
                rep, _ = _run_one(h, t)
                assert rep["state"] == "RESOLVED", (t["task_id"], rep)
        finally:
            h.close()
        # Reopen the same root — a fresh-process boundary: the
        # ledger is re-read and the experience store rebuilt
        # from the durable records — and run one reuse task.
        h2 = Harness(root=root, tasks=list(MATCHED_TASKS))
        try:
            for t in TRAINING:
                h2.add_task(t)
            for x in TRANSFER_TASKS.values():
                h2.add_task(x)
            reuse_task = [t for t in MATCHED_TASKS
                          if t["task_id"] == "ab-exact-valve"][0]
            reuse_rep = h2.pipe.run(
                {k: v for k, v in reuse_task.items() if k != "correct"})
            assert reuse_rep["state"] == "RESOLVED", reuse_rep
            assert reuse_rep["stage"] == "lower-exact", reuse_rep
            # Reconstruct every execution in the ledger while
            # it is still open.
            phases = {}
            for t in TRAINING:
                phases[t["task_id"]] = "training"
            for x in TRANSFER_TASKS.values():
                phases[x["task_id"]] = "training"
            for t in MATCHED_TASKS:
                phases[t["task_id"]] = "test"
            pathways = _reconstruct_executions(
                h2.pipe.trace,
                [t["task_id"] for t in TRAINING]
                + [x["task_id"] for x in TRANSFER_TASKS.values()]
                + [t["task_id"] for t in MATCHED_TASKS],
                root_label, phases)
            # The reuse execution is the ab-exact-valve run
            # after the reopen: tag it after_restart.
            for p in pathways:
                if p["execution_id"] == reuse_rep["execution_id"]:
                    p["phase"] = "after_restart"
            topology = memory_topology(pathways)
        finally:
            h2.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    by_phase = Counter(p["phase"] for p in pathways)
    reuse_after_restart = []
    for r in topology["reuse"]:
        after = [e for e in r["read_in"]
                 if any(p["execution_id"] == e
                        and p["phase"] == "after_restart"
                        for p in pathways)]
        if after:
            reuse_after_restart.append({**r, "read_after_restart": after})
    return {"topology": topology, "pathways": pathways,
            "n_executions": len(pathways),
            "executions_by_phase": dict(by_phase),
            "reuse_after_restart": reuse_after_restart,
            "n_reuse_after_restart": len(reuse_after_restart),
            "reuse_report": reuse_rep}


# ------------------------------------------------------------------
# 7. Pathway transfer: does a learned pathway improve a
#    held-out related task without copying its answer?
# ------------------------------------------------------------------
# Task A: a novel cache task, correct procedure clear_cache.
# Task B: a held-out related cache task (same family, overlapping
# vocabulary, different signature), correct procedure
# rotate_logs — a DIFFERENT answer from A's. If B merely copied
# A's answer (clear_cache) it would fail. The pathway being
# tested is the investigation route (the stage and function
# sequence), not the procedure.
PATHWAY_TRANSFER_A = {"task_id": "pt-a", "signature": "pt_cache_a",
                      "family": "cache",
                      "tags": ["cache", "full", "clear", "memory"],
                      "conditions": {},
                      "requested_operation": "repair",
                      "provenance": ("int:trace-003",),
                      "correct": "clear_cache"}
PATHWAY_TRANSFER_A_XFER = {"task_id": "pt-a-xfer",
                           "signature": "pt_cache_a", "family": "cache",
                           "tags": ["cache", "full", "evict", "memory"],
                           "conditions": {},
                           "requested_operation": "repair",
                           "provenance": ("int:trace-003",),
                           "correct": "clear_cache"}
PATHWAY_TRANSFER_B = {"task_id": "pt-b", "signature": "pt_cache_b",
                      "family": "cache",
                      "tags": ["cache", "stale", "rotate", "logs"],
                      "conditions": {},
                      "requested_operation": "repair",
                      "provenance": ("int:trace-003",),
                      "correct": "rotate_logs"}


def run_pathway_transfer():
    """Requirement 8: learn a pathway on task A (a novel cache
    task, resolved by trial, qualified through transfer), then
    run the held-out related task B (same family, overlapping
    vocabulary, different signature, DIFFERENT correct
    procedure). Compare B against a control B' run in a fresh
    harness without A's experience. Report: B's pathway
    signature vs A's (same stage? same function sequence?),
    B's attempts vs the control's, whether B selected A's
    procedure (copying — which would fail), and whether the
    pathway improved B. The result is empirical: if the
    pathway does not transfer, that is the finding (transfer
    in this fixture is procedure-specific and lexical, per
    MEM-004), not a failure of the experiment."""
    # Learn A.
    h = Harness(tasks=[PATHWAY_TRANSFER_A, PATHWAY_TRANSFER_A_XFER,
                       PATHWAY_TRANSFER_B])
    try:
        h.train_baseline()
        a_rep, a_path = _run_one(h, PATHWAY_TRANSFER_A)
        assert a_rep["state"] == "RESOLVED", a_rep
        q_rep = h.pipe.qualify(
            PATHWAY_TRANSFER_A["task_id"], "RC-FIX-CLEAR-CACHE",
            {k: v for k, v in PATHWAY_TRANSFER_A_XFER.items()
             if k != "correct"})
        assert q_rep["state"] == "RESOLVED", q_rep
        a_sig = pathway_signature(a_path, a_rep)
        a_sig["certified"] = certify(
            h.pipe.trace, PATHWAY_TRANSFER_A["task_id"])["certified"]
        # Held-out B, with A's experience in the store.
        b_rep, b_path = _run_one(h, PATHWAY_TRANSFER_B)
        b_sig = pathway_signature(b_path, b_rep)
        b_sig["certified"] = certify(
            h.pipe.trace, PATHWAY_TRANSFER_B["task_id"])["certified"]
    finally:
        h.close()
    # Control B': the same task, no A experience (baseline only).
    h2 = Harness(tasks=[PATHWAY_TRANSFER_B])
    try:
        h2.train_baseline()
        c_rep, c_path = _run_one(h2, PATHWAY_TRANSFER_B)
        c_sig = pathway_signature(c_path, c_rep)
        c_sig["certified"] = certify(
            h2.pipe.trace, PATHWAY_TRANSFER_B["task_id"])["certified"]
    finally:
        h2.close()
    copied = "RC-FIX-CLEAR-CACHE" in b_sig["attempt_procedures"]
    same_stage = b_sig["stage"] == a_sig["stage"]
    same_functions = b_sig["functions"] == a_sig["functions"]
    improved = (b_rep["state"] == "RESOLVED"
                and b_sig["n_attempts"] < c_sig["n_attempts"])
    return {
        "a": {"report": a_rep, "signature": a_sig},
        "qualify": {"report": q_rep},
        "b": {"report": b_rep, "signature": b_sig},
        "control_b": {"report": c_rep, "signature": c_sig},
        "a_procedure_attempted_on_b": copied,
        "b_selected_a_procedure": b_rep.get("selected") == "RC-FIX-CLEAR-CACHE",
        "same_stage_as_a": same_stage,
        "same_functions_as_a": same_functions,
        "b_attempts": b_sig["n_attempts"],
        "control_attempts": c_sig["n_attempts"],
        "improved": improved,
        "verdict": ("pathway improved the held-out task" if improved
                    else "pathway did not improve the held-out task "
                         "(attempts equal or worse, or B unresolved)")}


# ------------------------------------------------------------------
# 8. The P5-first standing benchmark (JD, TRACE-003 GO).
# ------------------------------------------------------------------
# The recurring blocked-family mistake (TRACE-002 correction
# item 10): on a blocked task whose correct procedure is P7,
# P5 and P7 tie on the trial ranker's (family, shared, soft)
# key and the stable sort falls back to registry order — P5
# first. The revised ranker prefers demonstrated success in
# the task's family. This benchmark makes it standing: the
# original fixture plus HELD-OUT cases (new signatures, new
# tags), compared across three conditions — the existing
# ranker trained, the revised ranker trained, and the revised
# ranker untrained (the control: with no demonstrated
# records the revised ranker must behave like the existing
# one). The held-out set also includes P5-correct tasks: the
# regression check. The revised ranker prefers P7's
# demonstrated blocked-family success, so on a task where P5
# IS correct it may try P7 first — the benchmark measures
# that cost against the saving on P7-correct tasks, instead
# of assuming the revision is free.
#
# Tag discipline (from the corrections): the tags share no
# word with any procedure description or demonstration tag,
# so neither transfer nor the pointed-at rule fires and the
# task reaches trial with P5 and P7 tied. Avoided words
# include: route, detour, ground, probe, narrow, passage,
# valve, sensor, pump, cache, blocked, debris, rubble,
# obstacle, fallen, stuck, jammed, and every other
# description/demonstration word.
P5_BENCHMARK_FIXTURE = {"task_id": "pf-tie",
                        "signature": "blocked_shut_new",
                        "family": "blocked",
                        "tags": ["shut", "jammed", "wedged"],
                        "conditions": {},
                        "requested_operation": "repair",
                        "provenance": ("int:trace-002",),
                        "correct": "P7"}
P5_BENCHMARK_HELDOUT = [
    {"task_id": "pf-h1", "signature": "blocked_seize_new",
     "family": "blocked", "tags": ["seized", "shut", "locked"],
     "conditions": {}, "requested_operation": "repair",
     "provenance": ("int:trace-003",), "correct": "P7"},
    {"task_id": "pf-h2", "signature": "blocked_halt_new",
     "family": "blocked", "tags": ["halted", "barred", "choked"],
     "conditions": {}, "requested_operation": "repair",
     "provenance": ("int:trace-003",), "correct": "P7"},
    {"task_id": "pf-h3", "signature": "blocked_grid_new",
     "family": "blocked", "tags": ["gridlocked", "immobilized", "shut"],
     "conditions": {}, "requested_operation": "repair",
     "provenance": ("int:trace-003",), "correct": "P7"},
    {"task_id": "pf-h4", "signature": "blocked_wedge_new",
     "family": "blocked", "tags": ["wedged", "seized", "halted"],
     "conditions": {}, "requested_operation": "repair",
     "provenance": ("int:trace-003",), "correct": "P7"},
    # P5-correct held-out tasks: the regression check. If the
    # revised ranker wrongly prefers P7 here, it costs an
    # attempt (P7 fails, P5 succeeds) — measured, not assumed.
    {"task_id": "pf-h5", "signature": "blocked_seize_p5",
     "family": "blocked", "tags": ["seized", "locked", "barred"],
     "conditions": {}, "requested_operation": "repair",
     "provenance": ("int:trace-003",), "correct": "P5"},
    {"task_id": "pf-h6", "signature": "blocked_halt_p5",
     "family": "blocked", "tags": ["halted", "choked", "wedged"],
     "conditions": {}, "requested_operation": "repair",
     "provenance": ("int:trace-003",), "correct": "P5"},
]
P5_BENCHMARK_TASKS = [P5_BENCHMARK_FIXTURE] + P5_BENCHMARK_HELDOUT


def _p5_run_one(harness, task):
    rep = harness.pipe.run({k: v for k, v in task.items() if k != "correct"})
    attempts = [a["procedure"] for a in rep["attempts"]]
    false = any(a["observed"] and a["validation"] == "FAIL"
                for a in rep["attempts"]) and rep["state"] == "RESOLVED"
    return {"correct": task["correct"],
            "first_attempt": attempts[0] if attempts else None,
            "attempts": attempts, "n_attempts": len(attempts),
            "resolved": rep["state"] == "RESOLVED",
            "false_resolved": bool(false),
            "stage": rep["stage"]}


def _p5_condition(tasks, trial_ranker, train, isolated=False):
    """One benchmark condition: the named ranker, training iff
    asked. With isolated=False the tasks share one root (the
    trained conditions). With isolated=True each task runs in
    its own fresh root — the clean untrained control: a task's
    own success must not teach the ranker for the next task.
    Returns the per-task results (first attempt, attempts,
    resolved, false)."""
    out = {}
    if isolated:
        for t in tasks:
            h = Harness(tasks=[t], trial_ranker=trial_ranker)
            try:
                out[t["task_id"]] = _p5_run_one(h, t)
            finally:
                h.close()
        return out
    h = Harness(tasks=list(tasks), trial_ranker=trial_ranker)
    try:
        if train:
            h.train_baseline()
        for t in tasks:
            out[t["task_id"]] = _p5_run_one(h, t)
        return out
    finally:
        h.close()


def _p5_summarize(results):
    """Aggregate one condition's per-task results, split by
    whether P7 or P5 is correct."""
    def agg(tasks):
        n = len(tasks)
        return {"of": n,
                "p5_first": sum(1 for t in tasks
                                if t["first_attempt"] == "SOSO-P5"),
                "resolved": sum(1 for t in tasks if t["resolved"]),
                "false_resolved": sum(1 for t in tasks
                                      if t["false_resolved"]),
                "attempts": sum(t["n_attempts"] for t in tasks),
                "mean_attempts": (sum(t["n_attempts"] for t in tasks) / n
                                  if n else 0)}
    all_tasks = list(results.values())
    p7_tasks = [t for t in all_tasks if t["correct"] == "P7"]
    p5_tasks = [t for t in all_tasks if t["correct"] == "P5"]
    return {"all": agg(all_tasks), "p7_correct": agg(p7_tasks),
            "p5_correct": agg(p5_tasks)}


def run_p5_benchmark(tasks=None):
    """The P5-first standing benchmark: the original fixture
    plus held-out cases, under the existing ranker (trained),
    the revised ranker (trained) and the revised ranker
    untrained (control). Each condition runs in its own
    isolated root. The headline comparison is on the held-out
    tasks alone; the fixture is reported alongside it, not
    folded into it."""
    tasks = tasks if tasks is not None else P5_BENCHMARK_TASKS
    heldout = [t for t in tasks if t["task_id"] != "pf-tie"]
    conditions = {}
    for name, ranker, train, isolated in (
            ("existing_trained", "existing", True, False),
            ("revised_trained", "revised", True, False),
            # Clean control: no training, one fresh root per
            # task, so no within-run learning leaks between
            # tasks.
            ("revised_untrained", "revised", False, True),
            # The same untrained run in ONE shared root,
            # kept as a separate condition: it documents the
            # bootstrapping — the first task's own success
            # teaches the revised ranker for the later tasks.
            ("revised_untrained_sequential", "revised", False, False)):
        results = _p5_condition(tasks, ranker, train, isolated)
        heldout_results = {tid: r for tid, r in results.items()
                           if tid != "pf-tie"}
        conditions[name] = {
            "results": results,
            "summary": _p5_summarize(results),
            "heldout_summary": _p5_summarize(heldout_results)}
    return {"tasks": [t["task_id"] for t in tasks],
            "heldout_tasks": [t["task_id"] for t in heldout],
            "conditions": conditions}


# ------------------------------------------------------------------
# 9. The full run: everything in one reproducible pass.
# ------------------------------------------------------------------
def run_all(out_dir=None):
    """Run the TRACE-003 experiments and return the combined
    result dict (also written as pathway JSON when out_dir is
    given). The domain comparison, the pathway ablation, the
    wrong-decision and domain-stress pathways (reused from
    TRACE-002), the pattern finders, the memory topology, and
    the pathway-transfer experiment."""
    out_dir = out_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..")
    domain = run_domain_comparison()
    ablation = run_pathway_ablation()
    wrong = run_wrong_decision()
    stress = run_domain_stress()
    # All signatures, for pattern finding. TRACE-003 fix
    # (defects b/c): deduplicate by execution_id BEFORE any
    # aggregate — the three L24 levels alias arms, so the same
    # execution occurs twice in the raw mixed set and would be
    # double-counted in every count. JD item 2: the pattern
    # statistics are reported BOTH ways — unique-case (the
    # deduplicated set) and execution-weighted (the raw set) —
    # so a reader can see which measures the duplicates move
    # and which they do not.
    all_sigs = []
    for d, per_profile in domain.items():
        for prof, data in per_profile.items():
            for tid, r in data["results"].items():
                s = dict(r["signature"])
                s["domain_label"] = d
                s["profile_label"] = prof
                all_sigs.append(s)
    for arm, data in ablation.items():
        for s in data["signatures"]:
            s2 = dict(s)
            s2["ablation_arm"] = arm
            all_sigs.append(s2)
    wrong_sig = pathway_signature(wrong["pathway"], wrong["test_report"])
    wrong_sig["domain_label"] = "wrong-decision"
    all_sigs.append(wrong_sig)
    for name, data in stress.items():
        s = pathway_signature(data["pathway"], data["report"])
        s["domain_label"] = f"domain-stress-{name}"
        all_sigs.append(s)
    dedup = deduplicate_signatures(all_sigs)
    unique_sigs = dedup["signatures"]
    # Completeness (JD item 4): expected vs observed
    # execution counts, asserted — a missing or duplicated
    # component fails loudly instead of silently changing
    # every downstream count.
    expected_counts = {
        "domain_executions": 4 * 4 * 3,  # domains x profiles x tasks
        "ablation_raw": 10 * 12,  # arms (control + 6 + 3 L24 levels) x tasks
        "ablation_unique": 8 * 12,  # distinct arms x tasks
        "wrong_decision": 1,
        "domain_stress": len(stress),
        "total_raw": 172,
        "total_unique": 148,
    }
    observed_counts = {
        "domain_executions": sum(
            1 for s in all_sigs if s.get("profile_label")),
        "ablation_raw": sum(
            1 for s in all_sigs if s.get("ablation_arm")),
        "ablation_unique": sum(
            1 for s in unique_sigs if s.get("ablation_arm")),
        "wrong_decision": sum(
            1 for s in all_sigs
            if s.get("domain_label") == "wrong-decision"),
        "domain_stress": sum(
            1 for s in all_sigs
            if (s.get("domain_label") or "").startswith("domain-stress")),
        "total_raw": len(all_sigs),
        "total_unique": len(unique_sigs),
    }
    for key, want in expected_counts.items():
        got = observed_counts[key]
        assert got == want, (
            f"run_all completeness: {key} expected {want}, got {got}")
    # Memory topology, two views. (1) The single-root set:
    # training + test + after-restart executions in one ledger
    # — the only set where reuse (a block written in one
    # execution and read in a later one) is measurable. (2)
    # The cross-root set: the domain, ablation, wrong-decision
    # and stress pathways, each tagged with its own ledger —
    # reuse is computed per root, so a block id shared across
    # ledgers is never counted as reuse. TRACE-003 fix
    # (defect d): the ablation pathways are really included
    # this time — the old dead loop over them did nothing.
    topo_root = run_topology_root()
    topology = topo_root["topology"]
    assert topo_root["n_executions"] == 37, (
        f"topology root expected 37 executions, "
        f"got {topo_root['n_executions']}")
    assert topo_root["executions_by_phase"] == {
        "training": 24, "test": 12, "after_restart": 1}, (
        f"topology root phases {topo_root['executions_by_phase']}")
    topo_pathways = []
    for d, per_profile in domain.items():
        for prof, data in per_profile.items():
            for tid, r in data["results"].items():
                p = dict(r["pathway"])
                p["root"] = f"domain:{d}:{prof}"
                topo_pathways.append(p)
    for arm, data in ablation.items():
        if arm in ("l24_function", "l24_strategy"):
            continue  # aliases of L24 / U08 — same executions
        for p in data["pathways"]:
            p2 = dict(p)
            p2["root"] = f"ablation:{arm}"
            topo_pathways.append(p2)
    wrong_pathway = dict(wrong["pathway"])
    wrong_pathway["root"] = "wrong-decision"
    topo_pathways.append(wrong_pathway)
    for name, data in stress.items():
        p = dict(data["pathway"])
        p["root"] = f"stress:{name}"
        topo_pathways.append(p)
    assert len(topo_pathways) == 148, (
        f"cross-root topology expected 148 pathways, "
        f"got {len(topo_pathways)}")
    cross_topology = memory_topology(topo_pathways)
    shared_memory = run_shared_memory_experiment()
    p5_benchmark = run_p5_benchmark()
    # Stratified clusters (JD item 3): one clustering run per
    # experimental population — training, evaluation, each
    # domain, each profile, each ablation arm, the
    # wrong-decision and the domain-stress cases. Mixed
    # populations are NEVER clustered together: a cluster
    # over the mixed set cannot be read as domain-specific
    # cognition, so no mixed-set cluster is reported at all.
    strata = {}
    strata["training"] = [
        pathway_signature(p)
        for p in topo_root["pathways"] if p["phase"] == "training"]
    strata["evaluation"] = list(ablation["control"]["signatures"])
    for d in domain:
        strata[f"domain:{d}"] = [
            s for s in unique_sigs if s.get("domain_label") == d]
    for prof in PROFILE_IDS:
        strata[f"profile:{prof}"] = [
            s for s in unique_sigs if s.get("profile_label") == prof]
    for arm, data in ablation.items():
        if arm in ("l24_function", "l24_strategy"):
            continue  # aliases — same executions as L24 / U08
        strata[f"ablation:{arm}"] = list(data["signatures"])
    strata["wrong-decision"] = [wrong_sig]
    strata["domain-stress"] = [
        s for s in unique_sigs
        if (s.get("domain_label") or "").startswith("domain-stress")]
    clusters = {name: cluster_signatures(sigs)
                for name, sigs in strata.items()}
    # Patterns, both countings (JD item 2).
    inefficient = {
        "unique_case": find_inefficient_pathways(unique_sigs),
        "execution_weighted": find_inefficient_pathways(all_sigs)}
    misleading = {
        "unique_case": find_misleading_transfers(unique_sigs),
        "execution_weighted": find_misleading_transfers(all_sigs)}
    corrections = {
        "unique_case": find_correction_sequences(unique_sigs),
        "execution_weighted": find_correction_sequences(all_sigs)}
    failure_rates = {
        "unique_case": _failure_rates(unique_sigs),
        "execution_weighted": _failure_rates(all_sigs)}
    transfer = run_pathway_transfer()
    # The four measurement layers, kept apart (JD, TRACE-003
    # GO): actual function calls, memory accesses, pathway
    # structure, and inferred causal contribution are four
    # different kinds of evidence. Only the last one is
    # causal, and only because it comes from controlled
    # interventions (the ablations), not from correlation.
    # A function appearing in a pathway does not mean it
    # caused the outcome; a block being read does not mean
    # the reading caused the decision.
    func_invocations = Counter()
    func_executions = Counter()
    for sig in unique_sigs:
        for f in sig["function_sequence"]:
            func_invocations[f] += 1
        for f in set(sig["functions"]):
            func_executions[f] += 1
    control_summary = ablation["control"]["summary"]
    causal = {}
    for arm, data in ablation.items():
        if arm in ("control", "l24_function", "l24_strategy"):
            continue  # aliases — the same intervention as L24 / U08
        arm_summary = data["summary"]
        causal[arm] = {
            "intervention": ("function disabled" if arm in
                             ("L24", "U08", "U09", "L30", "U37", "L15")
                             else "information source removed"),
            "resolved": arm_summary["resolved"],
            "resolved_control": control_summary["resolved"],
            "delta_resolved": (arm_summary["resolved"]
                               - control_summary["resolved"]),
            "attempts": arm_summary["attempts"],
            "spans": arm_summary["spans"],
            "stages": arm_summary["stages"],
            "stages_control": control_summary["stages"],
            "basis": "controlled intervention (train-then-disable); "
                     "not a correlation over pathways"}
    measurement_layers = {
        "function_calls": {
            "what": "functions actually invoked, counted from the "
                    "reconstructed spans of the deduplicated set",
            "invocations_by_function": dict(func_invocations),
            "executions_by_function": dict(func_executions),
            "n_executions": len(unique_sigs)},
        "memory_accesses": {
            "what": "blocks read/written/retrieved, from the "
                    "single-root topology (the only set where "
                    "reuse is measurable)",
            "n_blocks": topology["n_blocks"],
            "n_accesses": sum(n["n_accesses"]
                              for n in topology["nodes"].values()),
            "operations": sorted({op for n in topology["nodes"].values()
                                  for op in n["operations"]}),
            "n_reused": topology["n_reused"],
            "n_cross_task_reuse": topology["n_cross_task_reuse"],
            "n_same_task_reread": topology["n_same_task_reread"],
            "n_repeated_references": topology["n_repeated_references"]},
        "pathway_structure": {
            "what": "ordered function sequences and their clusters, "
                    "per experimental stratum — structure, not cause",
            "n_signatures": len(unique_sigs),
            "strata": sorted(clusters),
            "exact_clusters_per_stratum": {
                name: len(c["exact"]) for name, c in clusters.items()},
            "near_clusters_per_stratum": {
                name: len(c["near"]) for name, c in clusters.items()}},
        "causal_contribution": {
            "what": "what changes when a function is removed or "
                    "its information source is removed — the ONLY "
                    "causal claims in this report, from controlled "
                    "interventions",
            "arms": causal,
            "non_causal_note": "Cluster membership, pathway "
                "frequency and memory-access counts are NOT causal "
                "evidence. L24 function-removal vs "
                "information-source removal measure identically on "
                "this workload and are distinguishable only in "
                "trace context."}}
    result = {
        "domain_comparison": {
            d: {p: {"summary": data["summary"],
                    "signatures": {tid: r["signature"]
                                   for tid, r in data["results"].items()}}
                for p, data in per_profile.items()}
            for d, per_profile in domain.items()},
        "pathway_ablation": {
            arm: {"summary": data["summary"],
                  "n_distinct_signatures": data["n_distinct_signatures"],
                  "keys": data["keys"],
                  "signatures_lost_vs_control":
                      data["signatures_lost_vs_control"],
                  "signatures_gained_vs_control":
                      data["signatures_gained_vs_control"]}
            for arm, data in ablation.items()},
        "clusters": clusters,
        "cluster_strata": sorted(clusters),
        "inefficient_pathways": inefficient,
        "misleading_transfers": misleading,
        "correction_sequences": corrections,
        "failure_rates": failure_rates,
        "execution_counts": {"expected": expected_counts,
                             "observed": observed_counts},
        "memory_topology": {
            "n_blocks": topology["n_blocks"],
            "n_reused": topology["n_reused"],
            "n_cross_task_reuse": topology["n_cross_task_reuse"],
            "n_same_task_reread": topology["n_same_task_reread"],
            "n_repeated_references": topology["n_repeated_references"],
            "blocks_by_store": topology["blocks_by_store"],
            "reuse": topology["reuse"],
            "n_executions": topo_root["n_executions"],
            "executions_by_phase": topo_root["executions_by_phase"],
            "reuse_after_restart": topo_root["reuse_after_restart"],
            "n_reuse_after_restart": topo_root["n_reuse_after_restart"],
            "nodes": topology["nodes"]},
        "cross_root_topology": {
            "n_blocks": cross_topology["n_blocks"],
            "n_reused": cross_topology["n_reused"],
            "n_cross_task_reuse": cross_topology["n_cross_task_reuse"],
            "n_same_task_reread": cross_topology["n_same_task_reread"],
            "n_repeated_references":
                cross_topology["n_repeated_references"],
            "n_roots": cross_topology["n_roots"],
            "blocks_by_root": cross_topology["blocks_by_root"],
            "reuse": cross_topology["reuse"],
            "n_edges": len(cross_topology["edges"])},
        "pathway_transfer": {
            "a_stage": transfer["a"]["signature"]["stage"],
            "a_functions": transfer["a"]["signature"]["functions"],
            "b_stage": transfer["b"]["signature"]["stage"],
            "b_functions": transfer["b"]["signature"]["functions"],
            "b_attempts": transfer["b_attempts"],
            "control_attempts": transfer["control_attempts"],
            "a_procedure_attempted_on_b":
                transfer["a_procedure_attempted_on_b"],
            "b_selected_a_procedure":
                transfer["b_selected_a_procedure"],
            "same_stage_as_a": transfer["same_stage_as_a"],
            "same_functions_as_a": transfer["same_functions_as_a"],
            "improved": transfer["improved"],
            "verdict": transfer["verdict"]},
        "shared_memory_experiment": {
            "shared_block": shared_memory["shared_block"],
            "n_executions": shared_memory["n_executions"],
            "n_reading_tasks": shared_memory["n_reading_tasks"],
            "retrieved_by": shared_memory["retrieved_by"],
            "shared_records": shared_memory["shared_records"],
            "new_tasks": shared_memory["new_tasks"],
            "control_retrieved": shared_memory["control_retrieved"],
            "resolved": shared_memory["resolved"],
            "shared_entry": shared_memory["shared_entry"],
            "topology": {
                "n_blocks": shared_memory["topology"]["n_blocks"],
                "n_reused": shared_memory["topology"]["n_reused"],
                "n_cross_task_reuse":
                    shared_memory["topology"]["n_cross_task_reuse"],
                "n_same_task_reread":
                    shared_memory["topology"]["n_same_task_reread"],
                "n_repeated_references":
                    shared_memory["topology"]["n_repeated_references"]}},
        "p5_benchmark": p5_benchmark,
        "measurement_layers": measurement_layers,
        "n_signatures": len(unique_sigs),
        "n_signatures_raw": dedup["n_raw"],
        "n_duplicates_removed": dedup["n_duplicates_removed"],
        "function_coverage": {"registered": 147, "implemented": 9},
    }
    if out_dir:
        path = os.path.join(out_dir, "trace003_pathways.json")
        with open(path, "w") as f:
            json.dump(result, f, indent=2, sort_keys=True, default=str)
    return result
