"""TRACE-001 — cross-domain experimental harness (Phases C + D).

Phase C: run the SAME underlying investigation under different
cognitive profiles, then change the task domain while preserving
the relevant problem structure, and measure whether the system
changes function selection, investigation order, evidence
requirements, memory retrieval, hypothesis generation, error
handling and domain escalation. Include successful and
deliberately incorrect cases. The acceptance criterion is not
whether the response sounds like a mechanic, lawyer or
physician. It is whether the actual observed pathway follows the
declared domain strategy and whether deviations are identified
and explained using recorded events.

Phase D (one ablation): disable L24 (Cross-Task Recognise) and
measure what changes. Correlation is not proof that a function
caused success; a controlled ablation is the test.

Every comparison is built from reconstructed pathways
(contract/trace.py reconstruct), never from the final answer
alone. A plausible-looking trace is never mistaken for proof of
correct reasoning: the pathway is reported alongside the
independent validation result, and a wrong answer reached
through relevant domain procedures is a different failure class
from an unsupported domain jump (neither is accepted as
correct).
"""
from __future__ import annotations

from .eval001 import (check_projections, make_config_harness,
                      qualify_learned)
from .trace import analyze, reconstruct

PROFILES = ("mechanical", "legal", "medical", "general")


def _run_one(harness, task):
    """Run one task and return (report, pathway)."""
    rep = harness.pipe.run({k: v for k, v in task.items()
                            if k != "correct"})
    pathway = reconstruct(harness.pipe.trace, task["task_id"])
    return rep, pathway


def run_profile_comparison(test_task, train_tasks,
                           profiles=PROFILES):
    """Phase C (1): the same test task under each profile (fresh
    harness per profile, identical training). Returns
    {profile_id: {report, pathway}} plus a comparison summary:
    which functions ran, in what order, how many attempts, the
    outcome and stage, and whether the pathway followed the
    profile's declared strategy."""
    results = {}
    for pid in profiles:
        # Training (which learns by trial) happens under the
        # general profile; the test task then runs under the
        # profile being measured, so the training is held constant
        # and only the test-task strategy varies.
        h = make_config_harness("FULL", train_tasks + [test_task])
        try:
            h.pipe.set_profile(pid)
            rep, pathway = _run_one(h, test_task)
            results[pid] = {"report": rep, "pathway": pathway,
                            "projections_ok": check_projections(h) == []}
        finally:
            h.close()
    comparison = {}
    for pid, r in results.items():
        p = r["pathway"]
        comparison[pid] = {
            "functions": p["functions"],
            "n_attempts": len(r["report"]["attempts"]),
            "outcome": (p["outcome"] or {}).get("outcome"),
            "stage": (p["outcome"] or {}).get("stage"),
            "selected": (p["outcome"] or {}).get("selected"),
            "memory_blocks": len(p["memory_blocks"]),
            "domain_transitions": len(p["domain_transitions"]),
            "failures": p["failures"],
            "incomplete_spans": len(p["incomplete_spans"]),
            "unexplained_jumps": len(p["unexplained_jumps"]),
        }
    return {"results": results, "comparison": comparison}


def run_domain_change(structure_tasks, profiles=("general",)):
    """Phase C (2): the same problem structure in different
    domains (structure_tasks is a list of tasks that share a
    signature and a correct procedure but differ in family/tags
    — the domain). Each runs under the given profiles. Returns
    the pathways grouped by (domain, profile)."""
    out = {}
    for t in structure_tasks:
        for pid in profiles:
            h = make_config_harness("FULL", [t])
            try:
                h.pipe.set_profile(pid)
                rep, pathway = _run_one(h, t)
                out[(t["family"], pid)] = {"report": rep,
                                           "pathway": pathway}
            finally:
                h.close()
    return out


def run_incorrect_cases(cases, profiles=PROFILES):
    """Phase C (3): deliberately incorrect cases (a lying
    observation, a misleading prior) under each profile. Returns
    the pathways with their failure classes, so a wrong answer
    reached through relevant domain procedures can be
    distinguished from an unsupported domain jump."""
    out = {}
    for case in cases:
        for pid in profiles:
            h = make_config_harness("FULL", [case])
            try:
                h.pipe.set_profile(pid)
                rep, pathway = _run_one(h, case)
                out[(case["task_id"], pid)] = {"report": rep,
                                               "pathway": pathway}
            finally:
                h.close()
    return out


def run_ablation_l24(cross_tasks, train_tasks):
    """Phase D: disable L24 (Cross-Task Recognise) and measure
    what changes. L24 is driven by the description transfer, so
    disabling it means descriptions=None (the EVAL-001 trained
    control). The same cross-family tasks run with L24 enabled
    and disabled; the comparison shows the causal contribution
    of L24 to cross-family resolution — not a correlation, a
    controlled difference."""
    out = {}
    for label, descriptions in (("l24_enabled", "default"),
                                ("l24_disabled", None)):
        from .harness import Harness
        h = Harness(tasks=train_tasks + cross_tasks,
                    descriptions=descriptions)
        try:
            h.train_baseline()
            pathways = []
            resolved = 0
            for t in cross_tasks:
                rep, pathway = _run_one(h, t)
                pathways.append(pathway)
                if rep["state"] == "RESOLVED":
                    resolved += 1
            out[label] = {"resolved": resolved,
                          "of": len(cross_tasks),
                          "pathways": pathways,
                          "l24_present": any("L24" in p["functions"]
                                             for p in pathways),
                          "analysis": analyze(pathways)}
        finally:
            h.close()
    out["delta"] = {"resolved": (out["l24_enabled"]["resolved"]
                                 - out["l24_disabled"]["resolved"]),
                    "l24_present_enabled": out["l24_enabled"]["l24_present"],
                    "l24_present_disabled": out["l24_disabled"]["l24_present"]}
    return out


def compare_pathways(pathways):
    """A pathway-comparison report across reconstructed pathways:
    the analysis statistics plus, for each pathway, its function
    sequence, memory access pattern, domain transitions and
    outcome. Used by the log and by the first demonstration."""
    return {"analysis": analyze(pathways),
            "pathways": [{"task_id": p["task_id"],
                          "profile": p["profile"],
                          "domain": p["domain"],
                          "functions": p["functions"],
                          "n_memory_blocks": len(p["memory_blocks"]),
                          "memory_blocks": [m["block"]
                                           for m in p["memory_blocks"]],
                          "domain_transitions": p["domain_transitions"],
                          "failures": p["failures"],
                          "outcome": p["outcome"],
                          "incomplete_spans": p["incomplete_spans"],
                          "unexplained_jumps": p["unexplained_jumps"]}
                         for p in pathways]}
