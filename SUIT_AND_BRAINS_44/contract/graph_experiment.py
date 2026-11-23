"""The four-arm graph comparison (GRAPH-001 — FlyWire / COIN /
TRACE-003 integration, JD 2026-11-18).

Question: does source-bound graph analysis improve the existing
SoSo cognitive pipeline? The graph is an advisor to the trial
ranker, never an executor: it supplies a per-procedure score
table; the pipeline's ranker breaks ties by that score. Four
matched arms, frozen observations, equal candidate access,
held-out task families, separately preserved training and
evaluation evidence:
  A — current TRACE-003 baseline (existing ranker, no graph).
  B — generic graph features (validated pathway history + fan-in).
  C — graph features plus the selected motif (convergent-sink
      score + reciprocal-core membership).
  D — graph features plus a RANDOMIZED motif control (the same
      motif weight, randomly reassigned with a fixed seed).

The graph is built ONCE, from a training run under the baseline
ranker, and the trained root is copied to each arm — so all four
arms start from identical training evidence and differ only in
how they use the graph. The test tasks run in isolated roots;
training and evaluation evidence are separately preserved.

Test sets (all frozen, all held out from training):
  p5          — the standing P5-first regression fixture + 6
                held-out cases (trace003.P5_BENCHMARK_TASKS).
  different   — different-answer held-out tasks: the pathway-
                transfer B task (correct rotate_logs) plus three
                new ones (flood correct P6, slope correct P5,
                valve correct P2) whose vocabulary invites a
                transfer to the wrong procedure.
  misleading  — misleading-transfer tasks: a sensor task carrying
                P7's description words (correct P3) plus the
                three domain-stress cases.
  matched     — the 12 TRACE-003 matched tasks (regression
                control: the graph must not regress them).

Metrics per arm: correct decisions, wrong attempts, false
resolutions, abstentions, trial counts, memory costs (blocks
accessed during the test runs, plus the graph's node/edge
counts), computation overhead (graph build + feature time; the
ranker itself is a dict lookup), and improvement after restart
(the test sets re-run after closing and reopening the arm's
root).

If C does not outperform B reproducibly, the biological motif is
NOT integrated into the cognitive engine (JD's directive).
"""

import os
import shutil
import tempfile
import time

from .graph import build_graph, compute_features, score_tables
from .harness import Harness, TRAINING, TRANSFER_TASKS
from .trace import reconstruct
from .trace002 import DOMAIN_STRESS_TASKS
from .trace003 import (
    MATCHED_TASKS, P5_BENCHMARK_TASKS, PATHWAY_TRANSFER_B,
    _executions_for_task, _run_one)

# Different-answer held-out tasks (new, frozen). Each shares
# vocabulary with a trained procedure's description but has a
# DIFFERENT correct procedure — the transfer stage is invited
# to propose the wrong one, and the trial ranker must recover.
DIFFERENT_ANSWER_TASKS = [
    PATHWAY_TRANSFER_B,
    {"task_id": "da-flood", "signature": "da_flood_new",
     "family": "flood", "tags": ["flood", "detour", "water", "rise"],
     "conditions": {}, "requested_operation": "repair",
     "provenance": ("int:graph-001",), "correct": "P6"},
    {"task_id": "da-slope-p5", "signature": "da_slope_p5",
     "family": "slope", "tags": ["slope", "ground", "steep", "cross"],
     "conditions": {}, "requested_operation": "repair",
     "provenance": ("int:graph-001",), "correct": "P5"},
    {"task_id": "da-valve", "signature": "da_valve_new",
     "family": "valve", "tags": ["valve", "firmware_v2", "ground", "probe"],
     "conditions": {"firmware": "v2"}, "requested_operation": "repair",
     "provenance": ("int:graph-001",), "correct": "P2"},
]

# Misleading-transfer tasks: the sensor case from the Phase 2
# challenge (correct P3, carrying P7's description words) plus
# the three domain-stress cases (one helpful, two misleading).
MISLEADING_TASKS = [
    {"task_id": "ms-sensor", "signature": "ms_sensor_new",
     "family": "sensor",
     "tags": ["sensor", "temperature", "ground", "probe"],
     "conditions": {"kind": "sensor"}, "requested_operation": "repair",
     "provenance": ("int:graph-001",), "correct": "P3"},
] + list(DOMAIN_STRESS_TASKS.values())

TEST_SETS = {
    "p5": P5_BENCHMARK_TASKS,
    "different": DIFFERENT_ANSWER_TASKS,
    "misleading": MISLEADING_TASKS,
    "matched": MATCHED_TASKS,
}

ARMS = ("A", "B", "C", "D")


def _train_and_build_graph():
    """Train once under the baseline ranker, build the typed
    graph from the 24 training executions, compute the features
    and the three score tables. Returns (training_root,
    graph, features, tables, overhead_seconds). The caller owns
    the training root (a tempdir) and must delete it."""
    tmp = tempfile.mkdtemp(prefix="graph001-train-")
    root = os.path.join(tmp, "root")
    t0 = time.perf_counter()
    h = Harness(root=root, tasks=list(TRAINING)
                + list(TRANSFER_TASKS.values()))
    try:
        h.train_baseline()
        task_ids = ([t["task_id"] for t in TRAINING]
                    + [x["task_id"] for x in TRANSFER_TASKS.values()])
        pathways = []
        for tid in task_ids:
            for eid in _executions_for_task(h.pipe.trace, tid):
                pathways.append(reconstruct(h.pipe.trace, tid, eid))
        assert len(pathways) == 24, len(pathways)
        graph = build_graph(pathways, source_root="graph001-training")
        features = compute_features(graph)
        tables = score_tables(features)
    finally:
        h.close()
    overhead = time.perf_counter() - t0
    return tmp, root, graph, features, tables, overhead


def _reopened_arm(root, tasks, trial_ranker, graph_scores):
    """Open a Harness on an existing (trained) root, re-register
    the training + transfer tasks WITHOUT re-training, and
    register the test tasks. The arm's ranker and score table
    are the only things that differ between arms."""
    h = Harness(root=root, tasks=list(tasks),
                trial_ranker=trial_ranker, graph_scores=graph_scores)
    for t in TRAINING:
        h.add_task(t)
    for x in TRANSFER_TASKS.values():
        h.add_task(x)
    return h


def _run_set(harness, tasks):
    """Run one test set under a harness. Returns per-task results
    and the memory cost (distinct blocks accessed)."""
    results = {}
    blocks = set()
    from .registry import procedure_identity
    for t in tasks:
        rep, pathway = _run_one(harness, t)
        attempts = [a["procedure"] for a in rep["attempts"]]
        selected = rep.get("selected")
        correct = t["correct"]
        # A false resolution is a RESOLVED run whose selected
        # procedure is not the correct one. The report's
        # selected is the registered id; the task's correct is
        # the native id — compare via the registry.
        try:
            reg = procedure_identity(correct)["registered"]
        except Exception:
            reg = correct
        false = rep["state"] == "RESOLVED" and selected != reg
        results[t["task_id"]] = {
            "correct": correct,
            "state": rep["state"], "stage": rep["stage"],
            "selected": selected,
            "attempts": attempts, "n_attempts": len(attempts),
            "resolved": rep["state"] == "RESOLVED",
            "false_resolved": bool(false),
            "abstained": rep["state"] != "RESOLVED"}
        for m in pathway["memory_blocks"]:
            blocks.add(m["block"])
    return results, len(blocks)


def _summarize(results):
    n = len(results)
    return {"of": n,
            "correct": sum(1 for r in results.values() if r["resolved"]
                           and not r["false_resolved"]),
            "resolved": sum(1 for r in results.values() if r["resolved"]),
            "false_resolved": sum(1 for r in results.values()
                                  if r["false_resolved"]),
            "abstained": sum(1 for r in results.values()
                             if r["abstained"]),
            "wrong_attempts": sum(
                r["n_attempts"] - (1 if r["resolved"] else 0)
                for r in results.values()),
            "attempts": sum(r["n_attempts"] for r in results.values())}


def run_graph_experiment():
    """Run the four-arm comparison. Returns the full result
    dict (also JSON-serialisable)."""
    tmp, train_root, graph, features, tables, overhead = (
        _train_and_build_graph())
    try:
        all_test_tasks = [t for tasks in TEST_SETS.values()
                          for t in tasks]
        arm_specs = {
            "A": ("existing", None),
            "B": ("graph", tables["generic"]),
            "C": ("graph", tables["motif"]),
            "D": ("graph", tables["random"]),
        }
        arms = {}
        for arm, (ranker, scores) in arm_specs.items():
            arm_root = os.path.join(tmp, f"arm-{arm}")
            shutil.copytree(train_root, arm_root)
            h = _reopened_arm(arm_root, all_test_tasks, ranker, scores)
            try:
                results = {}
                memory_blocks = 0
                for set_name, tasks in TEST_SETS.items():
                    r, blocks = _run_set(h, tasks)
                    results[set_name] = r
                    memory_blocks += blocks
                summary = {name: _summarize(r)
                           for name, r in results.items()}
                summary["all"] = _summarize(
                    {tid: r for r in results.values() for tid, r in r.items()})
                # Restart: close, reopen the same root, re-run
                # the p5 + different + misleading sets, and
                # measure the change against the SAME sets
                # before the restart (the graph scores are frozen
                # from training; the restart tests the pipeline's
                # own durable state).
                restart_sets = {k: TEST_SETS[k] for k in
                                ("p5", "different", "misleading")}
                pre_restart = _summarize(
                    {tid: r for name, r in results.items()
                     if name in restart_sets
                     for tid, r in r.items()})
            finally:
                h.close()
            h2 = _reopened_arm(arm_root, all_test_tasks, ranker, scores)
            try:
                restart_results = {}
                for set_name, tasks in restart_sets.items():
                    r, _ = _run_set(h2, tasks)
                    restart_results[set_name] = r
                restart_summary = {name: _summarize(r)
                                   for name, r in restart_results.items()}
                restart_summary["all"] = _summarize(
                    {tid: r for r in restart_results.values()
                     for tid, r in r.items()})
            finally:
                h2.close()
            arms[arm] = {
                "ranker": ranker,
                "results": results, "summary": summary,
                "memory_blocks_accessed": memory_blocks,
                "restart_results": restart_results,
                "restart_summary": restart_summary,
                "restart_delta_attempts": (
                    restart_summary["all"]["attempts"]
                    - pre_restart["attempts"]),
                "restart_delta_correct": (
                    restart_summary["all"]["correct"]
                    - pre_restart["correct"])}
        return {"arms": arms,
                "graph": {"n_nodes": len(graph["nodes"]),
                          "n_edges": len(graph["edges"]),
                          "edge_types": sorted({e["type"]
                                                for e in graph["edges"]}),
                          "n_pathways": graph["n_pathways"],
                          "n_executions": graph["n_executions"],
                          "overhead_seconds": overhead},
                "features": features,
                "tables": tables,
                "test_sets": {k: [t["task_id"] for t in v]
                              for k, v in TEST_SETS.items()}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
