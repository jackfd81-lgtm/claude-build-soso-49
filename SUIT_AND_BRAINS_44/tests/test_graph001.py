"""GRAPH-001 — FlyWire / COIN / TRACE-003 integration tests.

The typed graph adapter (contract/graph.py) and the four-arm
comparison (contract/graph_experiment.py). The baseline is the
123-test TRACE-003 suite; these tests add the graph layer on top
of it and assert the measured comparison, not just that the code
runs.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from contract.graph import (
    build_graph, compute_features, find_reciprocal_cores,
    score_tables, apply_supersession)
from contract.graph_experiment import (
    run_graph_experiment, TEST_SETS, DIFFERENT_ANSWER_TASKS,
    MISLEADING_TASKS)
from contract.harness import Harness, TRAINING, TRANSFER_TASKS
from contract.trace import reconstruct
from contract.trace002 import run_wrong_decision
from contract.trace003 import (
    MATCHED_TASKS, P5_BENCHMARK_TASKS, _executions_for_task)


def _training_graph():
    """Train once (baseline ranker) and build the graph from the
    24 training pathways. Returns (graph, features, harness)
    — the caller closes the harness."""
    h = Harness(tasks=list(TRAINING) + list(TRANSFER_TASKS.values()))
    h.train_baseline()
    task_ids = ([t["task_id"] for t in TRAINING]
                + [x["task_id"] for x in TRANSFER_TASKS.values()])
    pathways = []
    for tid in task_ids:
        for eid in _executions_for_task(h.pipe.trace, tid):
            pathways.append(reconstruct(h.pipe.trace, tid, eid))
    graph = build_graph(pathways, source_root="graph001-test")
    return graph, compute_features(graph), h


def test_graph_has_distinct_edge_types_and_provenance():
    """The graph keeps chronology, nesting, memory access,
    proposals, attempts and validation as distinct edge types,
    and every edge carries BCV2-style source provenance."""
    graph, _, h = _training_graph()
    try:
        assert graph["n_pathways"] == 24
        assert graph["n_executions"] == 24
        # EXPANSION (2026-11-18): was 105 nodes / 470 edges. The
        # +4 nodes are the newly-invoked functions (U01, U02, L02,
        # U04 — U03/U05 need an evidence source/selector, which
        # the training pathways lack); the +228 edges are their
        # span, nesting and proposal edges.
        assert len(graph["nodes"]) == 109
        assert len(graph["edges"]) == 698
        types = {e["type"] for e in graph["edges"]}
        assert {"FOLLOWED_BY", "NESTED_IN", "ACCESSED", "PROPOSED",
                "ATTEMPTED", "VALIDATED"} <= types
        for e in graph["edges"]:
            prov = e["provenance"]
            assert prov["derived_by"] == "graph-adapter/0.1"
            assert prov["source_root"] == "graph001-test"
            # Every edge names the trace event it came from
            # (or None for a pathway-derived relationship).
            assert "source_event_id" in prov
            assert "source_seq" in prov
        # The memory edges are derived from the span linkage:
        # an ACCESSED edge's function is the enclosing span's
        # function, and every ACCESSED edge points at a block
        # that exists as a node.
        for e in graph["edges"]:
            if e["type"] == "ACCESSED":
                assert e["from"].startswith("fn:")
                assert e["to"].startswith("blk:")
                assert e["to"] in graph["nodes"]
    finally:
        h.close()


def test_graph_no_authority():
    """The graph never writes experience, qualifies, or
    executes: it has no reference to any store, and building
    it changes nothing in the ledger (the head sequence is
    the same before and after)."""
    h = Harness(tasks=list(TRAINING) + list(TRANSFER_TASKS.values()))
    try:
        h.train_baseline()
        n_before = len(h.pipe.trace.events())
        task_ids = [t["task_id"] for t in TRAINING]
        pathways = []
        for tid in task_ids:
            for eid in _executions_for_task(h.pipe.trace, tid):
                pathways.append(reconstruct(h.pipe.trace, tid, eid))
        build_graph(pathways, source_root="graph001-test")
        n_after = len(h.pipe.trace.events())
        assert n_after == n_before, (n_before, n_after)
        # The graph module imports no pipeline write path.
        import contract.graph as gmod
        src = open(gmod.__file__).read()
        assert "Pipeline" not in src
        assert "experiences" not in src
        assert "qualify" not in src
    finally:
        h.close()


def test_validated_history_counts_success_not_truthful_failure():
    """P5 fails its observations (a truthful failure, validation
    PASS) and must NOT earn validated pathway history. P2/P3/P4/
    P7 each earn 6 (their observed successes)."""
    _, features, h = _training_graph()
    try:
        f = features["procedures"]
        assert f["SOSO-P5"]["validated_pathway_history"] == 0, f["SOSO-P5"]
        assert f["SOSO-P5"]["attempted_executions"] == 3
        for p in ("SOSO-P2", "SOSO-P3", "SOSO-P4", "SOSO-P7"):
            assert f[p]["validated_pathway_history"] == 6, (p, f[p])
            assert f[p]["repeated_sequence_patterns"] == 3, (p, f[p])
        assert f["SOSO-P5"]["repeated_sequence_patterns"] == 0
        # The trap's only graph signal is its transition: P5 -> P7.
        assert f["SOSO-P5"]["fan_out"] == 1
        assert f["SOSO-P7"]["fan_in"] == 1
        assert f["SOSO-P7"]["sink_score"] == 1
    finally:
        h.close()


def test_reciprocal_core_present_after_expansion():
    """EXPANSION 2026-11-18 — this REVERSES GRAPH-001's headline
    finding. The literal 4-node 9-edge reciprocal core NOW occurs
    in the training graph: 5 cores, densest 4-node subset 9
    directed pairs (was 8, no cores). All 5 cores are
    function-only and every core contains U02 + U09; no procedure
    is in a core. This is an instrumentation-density artifact —
    admission (U02) is called at every execution point and trial
    (U09) follows transfer — NOT evidence for the biological
    motif: the four-arm comparison still shows no ranking
    improvement (see test_four_arm_comparison_measured), and JD
    had already decided to drop the motif."""
    graph, features, h = _training_graph()
    try:
        cores = find_reciprocal_cores(graph)
        assert len(cores) == 5, cores
        for c in cores:
            assert len(c["members"]) == 4 and c["n_edges"] == 9
            assert all(m.startswith("fn:") for m in c["members"])
            assert "fn:U02" in c["members"] and "fn:U09" in c["members"]
        pairs = {(e["from"], e["to"]) for e in graph["edges"]
                 if e["from"].startswith(("fn:", "proc:"))
                 and e["to"].startswith(("fn:", "proc:"))}
        import itertools
        core_nodes = sorted({a for a, b in pairs}
                            | {b for a, b in pairs})
        best = max(len([(a, b) for a, b in pairs
                        if a in set(s) and b in set(s)])
                   for s in itertools.combinations(core_nodes, 4))
        assert best == 9, best
        assert all(not f["reciprocal_core"]
                   for f in features["procedures"].values())
    finally:
        h.close()


def test_score_tables_are_keyed_by_registered_id():
    """The score tables are keyed by the registered procedure id
    the ranker sees (SOSO-P7), not by the graph's node id
    (proc:SOSO-P7) — the earlier key bug made every score 0."""
    _, features, h = _training_graph()
    try:
        tables = score_tables(features)
        assert set(tables["generic"]) == set(features["procedures"])
        assert "proc:SOSO-P7" not in tables["generic"]
        assert tables["generic"]["SOSO-P7"] == 1.25
        assert tables["generic"]["SOSO-P5"] == 0.0
        assert tables["motif"]["SOSO-P7"] == 2.25
        # The randomized table is the same total motif weight,
        # reassigned: exactly one procedure carries the 1.0
        # random component (its total is its generic score + 1.0).
        assert sum(1 for v in tables["components"]["random_component"].values()
                   if v == 1.0) == 1
        assert sum(tables["components"]["random_component"].values()) == \
            sum(tables["components"]["motif_component"].values())
        # Deterministic: the same seed gives the same table.
        assert score_tables(features)["random"] == tables["random"]
    finally:
        h.close()


def test_supersession_preserves_history():
    """A correction supersedes the earlier proposal it demotes:
    the old edge stays, marked superseded_by, and the
    correction edge names what it supersedes. Nothing is
    deleted."""
    graph, _, h = _training_graph()
    try:
        # A synthetic demotion on the training graph: P5's
        # proposals in the first blocked execution are
        # superseded by a correction edge.
        proposed = [e for e in graph["edges"]
                    if e["type"] == "PROPOSED"
                    and e["to"] == "proc:SOSO-P5"]
        assert proposed
        exec_id = proposed[0]["execution_id"]
        corr = {"id": "e-corr", "type": "CORRECTED_BY",
                "from": "fn:L15", "to": "proc:SOSO-P5",
                "execution_id": exec_id, "task_id": "x",
                "provenance": {}, "supersedes": [],
                "superseded_by": None}
        graph["edges"].append(corr)
        records = apply_supersession(graph, [{
            "execution_id": exec_id, "procedure": "SOSO-P5",
            "correction_edge_id": "e-corr"}])
        assert len(records) == 1
        n_superseded = len(records[0]["superseded"])
        assert n_superseded >= 1
        assert len(corr["supersedes"]) == n_superseded
        for e in graph["edges"]:
            if e["id"] in records[0]["superseded"]:
                assert e["superseded_by"] == "e-corr"
                assert e in graph["edges"]  # still present
    finally:
        h.close()


def test_correction_edges_from_wrong_decision_pathway():
    """The graph built from the wrong-decision pathway carries
    CORRECTED_BY and CAUSED_BY edges (the correction is
    observable in the graph, with the causal edge only from
    the CORRECTION event)."""
    res = run_wrong_decision()
    assert res["test_report"]["state"] == "RESOLVED"
    graph = build_graph([res["pathway"]], source_root="graph001-test")
    types = {e["type"] for e in graph["edges"]}
    assert "CORRECTED_BY" in types
    assert "CAUSED_BY" in types
    # The CAUSED_BY edge goes from the correction to the
    # L15 span that ran the demoted procedure.
    caused = [e for e in graph["edges"]
              if e["type"] == "CAUSED_BY"]
    assert caused and all(e["to"].startswith("fn:L15")
                          for e in caused)
    # The correction supersedes P3's earlier proposal in the
    # same execution: the old PROPOSED edge stays, marked
    # superseded_by, and the correction names it.
    assert graph["supersession"], graph["supersession"]
    rec = graph["supersession"][0]
    assert rec["procedure"] == "SOSO-P3"
    assert rec["superseded"]
    corr = [e for e in graph["edges"]
            if e["id"] == rec["correction_edge_id"]][0]
    assert corr["supersedes"] == rec["superseded"]
    for e in graph["edges"]:
        if e["id"] in rec["superseded"]:
            assert e["type"] == "PROPOSED"
            assert e["superseded_by"] == corr["id"]


def test_four_arm_comparison_measured():
    """The full comparison, asserted on the measured numbers:
    B improves on A only on the P5 set (12 -> 9 attempts),
    costs one attempt on a matched flood task, and C and D are
    identical to B (the motif and the random control change no
    ranking on this workload). Zero false resolutions in every
    arm; no improvement after restart in any arm."""
    r = run_graph_experiment()
    # EXPANSION (2026-11-18): was 105 nodes / 470 edges (see
    # test_graph_has_distinct_edge_types_and_provenance). The
    # arm numbers below are UNCHANGED by the expansion.
    assert r["graph"]["n_nodes"] == 109
    assert r["graph"]["n_edges"] == 698
    assert r["graph"]["n_pathways"] == 24
    # Overhead: the graph build + features is under a second.
    assert r["graph"]["overhead_seconds"] < 5.0
    a, b, c, d = (r["arms"][k] for k in ("A", "B", "C", "D"))
    # Every arm resolves every task correctly; no false
    # resolutions, no abstentions anywhere.
    for arm in (a, b, c, d):
        assert arm["summary"]["all"]["correct"] == 27, arm["summary"]
        assert arm["summary"]["all"]["false_resolved"] == 0
        assert arm["summary"]["all"]["abstained"] == 0
    # A: 61 attempts (P5 12, different 18, misleading 12,
    # matched 19). B: 59 (P5 9, matched 20).
    assert a["summary"]["all"]["attempts"] == 61, a["summary"]
    assert b["summary"]["all"]["attempts"] == 59, b["summary"]
    assert a["summary"]["p5"]["attempts"] == 12
    assert b["summary"]["p5"]["attempts"] == 9
    assert b["summary"]["p5"]["wrong_attempts"] == 2
    assert a["summary"]["matched"]["attempts"] == 19
    assert b["summary"]["matched"]["attempts"] == 20
    # The different-answer and misleading sets are unchanged
    # in count by the graph (the correct procedure there has
    # no validated history to rank on).
    assert b["summary"]["different"]["attempts"] == 18
    assert b["summary"]["misleading"]["attempts"] == 12
    # C is identical to B, attempt for attempt: the motif
    # component changes no ranking on this workload (it only
    # adds weight to P7, which already wins its ties on the
    # generic score). D's counts are identical to B's, but
    # its attempt ORDER differs on the four P6-correct tasks:
    # the random bonus moves ROTATE-LOGS up (second, after
    # P7) without changing any attempt count — a random signal
    # of the same magnitude reorders failures, it does not
    # change outcomes.
    assert c["summary"] == b["summary"]
    assert d["summary"] == b["summary"]
    assert c["results"] == b["results"]
    for s in ("different", "misleading", "matched"):
        for tid in b["results"][s]:
            assert (b["results"][s][tid]["n_attempts"]
                    == d["results"][s][tid]["n_attempts"])
    diffs = [(s, tid) for s in b["results"]
             for tid in b["results"][s]
             if b["results"][s][tid] != d["results"][s][tid]]
    assert {(s, tid) for s, tid in diffs} == {
        ("different", "da-flood"), ("different", "da-slope-p5"),
        ("misleading", "ds-mislead"), ("matched", "ab-trial-flood")}, diffs
    for s, tid in diffs:
        assert (d["results"][s][tid]["attempts"].index(
            "RC-FIX-ROTATE-LOGS") == 1)
        assert (b["results"][s][tid]["attempts"].index(
            "RC-FIX-ROTATE-LOGS") > 1)
    # The graph's P5 improvement is the revised ranker's
    # improvement: P7 first on every P7-correct task, P5
    # first (then P7) on the P5-correct tasks.
    for tid in ("pf-tie", "pf-h1", "pf-h2", "pf-h3", "pf-h4"):
        assert b["results"]["p5"][tid]["attempts"] == ["SOSO-P7"]
    for tid in ("pf-h5", "pf-h6"):
        assert b["results"]["p5"][tid]["attempts"] == [
            "SOSO-P7", "SOSO-P5"]
    # No improvement after restart in any arm (the test
    # tasks' own records are CANDIDATE, not QUALIFIED, so the
    # second pass repeats the work).
    for arm in (a, b, c, d):
        assert arm["restart_delta_attempts"] == 0, arm
        assert arm["restart_delta_correct"] == 0, arm
    # Memory cost: the graph arms access fewer distinct blocks
    # (95 vs 97) because they attempt fewer procedures.
    assert a["memory_blocks_accessed"] == 97
    assert b["memory_blocks_accessed"] == 95


def test_test_sets_are_held_out_and_frozen():
    """The test sets are held out from training by task id.
    The p5 / different / misleading sets also have new
    signatures. The matched set deliberately SHARES signatures
    with training (the slope tasks share cache_big with the
    blocked transfer task) — that shared signature is the
    transfer mechanism, not a leak: the tasks are new, the
    executions are new, and the correct procedures are held
    out from the training tasks' own records."""
    train_ids = {t["task_id"] for t in TRAINING} | {
        x["task_id"] for x in TRANSFER_TASKS.values()}
    train_sigs = {t["signature"] for t in TRAINING} | {
        x["signature"] for x in TRANSFER_TASKS.values()}
    for name, tasks in TEST_SETS.items():
        for t in tasks:
            assert t["task_id"] not in train_ids, (name, t)
            if name in ("p5", "different"):
                assert t["signature"] not in train_sigs, (name, t)
    shared = [t for name in ("matched", "misleading")
              for t in TEST_SETS[name]
              if t["signature"] in train_sigs]
    assert shared  # the transfer mechanism, by design
    # The shared signatures are exactly the training
    # signatures: the matched tasks are re-presentations and
    # transfers on the signatures the system trained on.
    assert {t["signature"] for t in shared} == train_sigs
    # Frozen: two runs of the experiment give the same arm A
    # and arm B summaries (the experiment is deterministic).
    r1 = run_graph_experiment()
    r2 = run_graph_experiment()
    assert (r1["arms"]["A"]["summary"] == r2["arms"]["A"]["summary"])
    assert (r1["arms"]["B"]["summary"] == r2["arms"]["B"]["summary"])
