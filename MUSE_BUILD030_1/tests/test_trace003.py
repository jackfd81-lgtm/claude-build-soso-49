"""TRACE-003 — Cognitive Pathway Topology and Comparative Learning
(JD's TRACE-002 acceptance, 2026-11-18).

Tests pin the TRACE-003 measurements AND the four defects found
in the first TRACE-003 pass and fixed before delivery:

  (a) memory_topology reported 0 reused blocks and empty edges:
      the reconstruction's memory records carried no function
      field, and the topology was built over pathways from
      separate ledgers whose training writes were never in the
      set. Fixed: reconstruct() attributes each memory record
      to its span's function, and the topology is built over a
      single root holding the training writes, the test reads
      and one execution after a restart.
  (b) find_inefficient_pathways / find_misleading_transfers ran
      over a signature list containing duplicates (the same
      execution under two L24-level labels). Fixed: the mixed
      set is deduplicated by execution_id before any aggregate.
  (c) clusters were computed over the same duplicated mixed
      set. Fixed: clusters are computed on the deduplicated set.
  (d) run_all had a dead no-op loop over the ablation
      signatures and left the ablation pathways out of the
      topology. Fixed: run_pathway_ablation retains the
      pathways and run_all includes them, root-tagged.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.harness import Harness
from contract.trace import reconstruct
from contract.trace002 import MATCHED_TASKS
from contract.trace003 import (
    deduplicate_signatures, memory_topology, run_all,
    run_domain_comparison, run_pathway_ablation, run_pathway_transfer,
    run_topology_root)


@pytest.fixture(scope="module")
def full_run():
    """One full TRACE-003 pass, shared by the tests that assert
    on its aggregates (it takes ~25 s)."""
    return run_all()


# ---- Defect (a): function attribution + single-root reuse ----

def test_memory_function_linkage_comes_from_spans_not_records():
    """The function-to-memory linkage is derived from the
    span/event structure (JD item 1), never from a field on
    the memory record: reconstruct() attaches each MEMORY_*
    record to its enclosing span's "memory" list, and the
    topology joins on seq/event_id. The record itself carries
    no function field."""
    h = Harness(tasks=list(MATCHED_TASKS))
    try:
        h.train_baseline()
        task = MATCHED_TASKS[4]  # a slope transfer task
        h.pipe.run({k: v for k, v in task.items() if k != "correct"})
        p = reconstruct(h.pipe.trace, task["task_id"])
        assert p["memory_blocks"], "the task accessed memory"
        for m in p["memory_blocks"]:
            assert "function" not in m, (
                "memory record must not carry a function field")
            assert "span" not in m
        # The span linkage: every record a span claims belongs
        # to that span's function, and every function named is
        # a function that ran.
        from contract.trace003 import _span_function_for_memory
        func_by_seq = _span_function_for_memory(p)
        claimed = [m for m in p["memory_blocks"]
                   if func_by_seq.get(m.get("seq"))]
        assert claimed, "no memory record is claimed by any span"
        for s in p["spans"]:
            for m in s["memory"]:
                assert func_by_seq[m["seq"]] == s["function"]
        assert {func_by_seq[m["seq"]] for m in claimed} <= set(
            p["functions"])
    finally:
        h.close()


def test_topology_root_has_real_reuse():
    """The single-root topology (defect a, part 2): 37
    executions — 24 training, 12 test, 1 after restart —
    80 blocks, 17 reused, 88 function->block edges, and 2
    reuse entries whose read happened after the restart.
    The reused blocks are the re-presented training tasks'
    task blocks and exp blocks (written by the trial run,
    read by the re-presentation) plus the reuse task's block
    read after the restart."""
    r = run_topology_root()
    t = r["topology"]
    assert r["n_executions"] == 37
    assert r["executions_by_phase"] == {
        "training": 24, "test": 12, "after_restart": 1}
    assert t["n_blocks"] == 80
    assert t["n_reused"] == 17
    assert len(t["edges"]) == 88
    assert r["n_reuse_after_restart"] == 2
    # Repeated references are not reuse: 17 reused blocks
    # split into 8 genuine cross-task reuses (an exp block
    # written by one task, read by different tasks — the
    # blocked-c P7 block is read by 6 distinct tasks) and 9
    # same-task re-reads (a task identity block re-read by
    # its own re-presentation). 28 read accesses are repeats
    # beyond the first read of their block.
    assert t["n_cross_task_reuse"] == 8
    assert t["n_same_task_reread"] == 9
    assert t["n_repeated_references"] == 28
    blocked = [e for e in t["reuse"]
               if e["block"] == "exp.ct-train-blocked-c.P7.2"][0]
    assert blocked["cross_task"] is True
    assert blocked["n_reading_tasks"] == 6
    assert blocked["n_reads"] == 6
    # Every reuse entry names its root and its executions.
    for entry in t["reuse"]:
        assert entry["root"] == "topology-root"
        assert entry["written_in"] and entry["read_in"]
        assert not (set(entry["written_in"]) & set(entry["read_in"]))
        assert entry["written_by"] and entry["read_by"]
        assert entry["cross_task"] == (not entry["same_task_only"])
    # The restart read is a block written before the restart.
    assert any("ab-exact-valve" in e["block"]
               for e in r["reuse_after_restart"])


def test_topology_reuse_is_per_root():
    """Reuse is only counted within one ledger (defect a):
    the same block id written in one root and read in
    another is NOT reuse — the stores are different files."""
    def pathway(root, exec_id, accesses):
        return {"execution_id": exec_id, "task_id": "t",
                "root": root,
                "memory_blocks": [
                    {"block": b, "store": "big49", "operation": op,
                     "function": "L21"}
                    for b, op in accesses]}
    # Same block, write in root A, read in root B: no reuse.
    topo = memory_topology([
        pathway("A", "e1", [("exp.x.P2.1", "write")]),
        pathway("B", "e2", [("exp.x.P2.1", "retrieve")])])
    assert topo["n_reused"] == 0
    # Same block, write and read in root A: reuse.
    topo = memory_topology([
        pathway("A", "e1", [("exp.x.P2.1", "write")]),
        pathway("A", "e2", [("exp.x.P2.1", "retrieve")])])
    assert topo["n_reused"] == 1
    assert topo["reuse"][0]["root"] == "A"


# ---- Defects (b)/(c): deduplication before aggregates ----

def test_deduplicate_signatures_removes_aliased_arms():
    """The three L24 levels alias arms, so the same execution
    occurs twice in a mixed set. Deduplication by execution_id
    keeps the first occurrence and reports the removal; a
    signature with no execution_id is never dropped as a
    duplicate of another."""
    sigs = [{"execution_id": "e1", "task_id": "a"},
            {"execution_id": "e2", "task_id": "b"},
            {"execution_id": "e1", "task_id": "a"},
            {"execution_id": None, "task_id": "c"},
            {"execution_id": None, "task_id": "c"}]
    d = deduplicate_signatures(sigs)
    assert (d["n_raw"], d["n_unique"], d["n_duplicates_removed"]) == (5, 4, 1)


def test_full_run_deduplicates_before_aggregates(full_run):
    """The mixed set is 172 raw signatures, 148 unique —
    exactly the 24 aliased-arm duplicates removed. The
    clusters, the inefficient count and the misleading
    counts are computed on the deduplicated set: the
    inefficient count drops from the pre-fix 23 to 19 (the
    duplicates were resolved pathways above the median)."""
    assert full_run["n_signatures_raw"] == 172
    assert full_run["n_signatures"] == 148
    assert full_run["n_duplicates_removed"] == 24
    # Both countings are reported (JD item 2): the
    # inefficient count drops from 23 execution-weighted to
    # 19 unique-case — the duplicates were resolved pathways
    # above the median. The misleading counts agree under
    # both countings (the duplicated signatures had no
    # transitions), which is itself informative.
    ineff = full_run["inefficient_pathways"]
    assert len(ineff["unique_case"]["inefficient"]) == 19
    assert len(ineff["execution_weighted"]["inefficient"]) == 23
    mis = full_run["misleading_transfers"]
    assert mis["unique_case"]["n_transitions"] == 47
    assert mis["execution_weighted"]["n_transitions"] == 47
    # Failure rates, both countings: overall and per stratum.
    # The failed count is the same under both (49) — the
    # duplicated signatures were all resolved executions, so
    # dedup removes 24 resolved and no failed.
    fr = full_run["failure_rates"]
    assert fr["unique_case"]["overall"] == {
        "resolved": 99, "of": 148, "failed": 49,
        "failure_rate": 49 / 148}
    assert fr["execution_weighted"]["overall"] == {
        "resolved": 123, "of": 172, "failed": 49,
        "failure_rate": 49 / 172}
    # The execution counts were asserted in run_all; the
    # result carries expected vs observed.
    assert full_run["execution_counts"]["expected"] == (
        full_run["execution_counts"]["observed"])


# ---- Defect (d): ablation pathways really in the topology ----

def test_ablation_pathways_retained_and_in_topology(full_run):
    """run_pathway_ablation retains the pathways (defect d),
    and run_all's cross-root topology includes them,
    root-tagged — the ablation roots are present and the
    edges are non-empty (function attribution works)."""
    ablation = run_pathway_ablation()
    for arm, data in ablation.items():
        assert len(data["pathways"]) == len(data["signatures"])
        assert all(p["memory_blocks"] is not None
                   for p in data["pathways"])
    cross = full_run["cross_root_topology"]
    assert cross["n_roots"] == 28
    assert cross["n_edges"] > 0
    assert any(r.startswith("ablation:") for r in cross["blocks_by_root"])
    # Per-root reuse in the cross-root set is 0 — honestly:
    # each small root's training writes are not in the set,
    # so no block is both written and read within one root.
    # The reuse evidence lives in the single-root topology.
    assert cross["n_reused"] == 0
    assert full_run["memory_topology"]["n_reused"] == 17


# ---- Main TRACE-003 measurements ----

def test_domain_profiles_change_executed_pathways(full_run):
    """Profiles change the executed functions, not just the
    wording. Medical changes all 3 pathways in every domain
    (its pathways lose L24 — descriptions off); legal changes
    1-2 per domain; the mechanical profile is identical to
    general on this fixture. Information requests are 0
    everywhere on these tasks — recorded, not assumed."""
    dc = full_run["domain_comparison"]
    for domain in dc:
        assert dc[domain]["medical"]["summary"]["n_pathways_changed"] == 3
        assert dc[domain]["legal"]["summary"]["n_pathways_changed"] >= 1
        assert dc[domain]["mechanical"]["summary"]["n_pathways_changed"] == 0
        for profile in dc[domain]:
            assert dc[domain][profile]["summary"]["information_requests"] == 0
    # Medical resolves 1/3 mechanical, 0/3 elsewhere; its
    # pathways never contain L24.
    assert dc["mechanical"]["medical"]["summary"]["resolved"] == 1
    assert dc["legal"]["medical"]["summary"]["resolved"] == 0
    for tid, sig in dc["clinical"]["medical"]["signatures"].items():
        assert "L24" not in sig["functions"]


def test_pathway_ablation_signatures(full_run):
    """The ablation measured at the signature level: the
    control has 5 distinct signatures (was 4 — the expanded
    function set splits one previously-merged pathway); the L24
    arm loses three L24 signatures and gains two; the U09 arm
    gains unresolved signatures; the L30 arm loses all 5 and
    gains 5 (same resolutions, different routes). The three L24
    intervention types stay distinct arms."""
    ab = full_run["pathway_ablation"]
    assert ab["control"]["n_distinct_signatures"] == 5
    assert len(ab["L24"]["signatures_lost_vs_control"]) == 3
    assert len(ab["L24"]["signatures_gained_vs_control"]) == 2
    assert len(ab["L30"]["signatures_lost_vs_control"]) == 5
    assert len(ab["L30"]["signatures_gained_vs_control"]) == 5
    assert ab["U09"]["summary"]["resolved"] == 5
    assert "l24_info_source" in ab and "l24_strategy" in ab
    assert ab["l24_info_source"]["summary"]["resolved"] == 12
    assert ab["l24_strategy"]["summary"]["resolved"] == 12


def test_clusters_are_stratified_not_mixed(full_run):
    """Clusters are computed per experimental population (JD
    item 3) — training, evaluation, each domain, each profile,
    each ablation arm — never over the mixed set. A cluster
    over mixed populations cannot be read as domain-specific
    cognition, so no mixed-set cluster is reported at all.
    Each cluster retains its member executions with outcome,
    stage and selected procedure, and its success rate is the
    members' rate."""
    clusters = full_run["clusters"]
    expected_strata = {"training", "evaluation", "wrong-decision",
                       "domain-stress"}
    expected_strata |= {f"domain:{d}" for d in
                        ("mechanical", "legal", "clinical", "general")}
    expected_strata |= {f"profile:{p}" for p in
                        ("mechanical", "legal", "medical", "general")}
    expected_strata |= {f"ablation:{a}" for a in
                        ("control", "L24", "U08", "U09", "L30", "U37",
                         "L15", "l24_info_source")}
    assert set(clusters) == expected_strata
    assert "mixed" not in clusters
    for name, stratum in clusters.items():
        for c in stratum["exact"]:
            assert c["n_members"] == len(c["members"])
            resolved = sum(1 for m in c["members"]
                           if m["outcome"] == "RESOLVED")
            assert c["n_resolved"] == resolved
            assert c["success_rate"] == resolved / c["n_members"]
            for m in c["members"]:
                assert m["execution_id"] and m["task_id"]
        # Every member of a stratum's clusters is a unique
        # execution within that stratum.
        exec_ids = [m["execution_id"]
                    for c in stratum["exact"] for m in c["members"]]
        assert len(exec_ids) == len(set(exec_ids))
    # The training stratum clusters the 24 training executions;
    # the evaluation stratum clusters the 12 control executions.
    assert sum(c["n_members"] for c in
               clusters["training"]["exact"]) == 24
    assert sum(c["n_members"] for c in
               clusters["evaluation"]["exact"]) == 12


def test_correction_sequence_is_useful(full_run):
    """One correction pathway (the wrong-decision task): the
    misleading qualified P3 proposed first, fails, demoted,
    P4 resolves via transfer. It is useful — the pathway
    ends RESOLVED after the correction."""
    corr = full_run["correction_sequences"]["unique_case"]
    assert corr["n_correction_pathways"] == 1
    assert corr["n_useful"] == 1
    assert full_run["correction_sequences"]["execution_weighted"][
        "n_correction_pathways"] == 1
    case = corr["cases"][0]
    assert case["outcome"] == "RESOLVED"
    assert case["corrections"][0]["procedure"] == "SOSO-P3"
    assert case["corrections"][0]["from"] == "QUALIFIED"
    assert case["corrections"][0]["to"] == "INCONCLUSIVE"


def test_misleading_transfers_counted_per_unique_execution(full_run):
    """47 transitions over the deduplicated set: 26 helped
    (resolved via soso-transfer), 21 misleading (the transfer
    stage was entered and did not produce the resolution).
    Each case carries the transition's reason and evidence
    and the pathway's stage as the verdict."""
    m = full_run["misleading_transfers"]["unique_case"]
    assert m["n_transitions"] == 47
    assert m["n_helped"] == 26
    assert m["n_misleading"] == 21
    w = full_run["misleading_transfers"]["execution_weighted"]
    assert (w["n_transitions"], w["n_helped"],
            w["n_misleading"]) == (47, 26, 21)
    for case in m["cases"]:
        assert case["transitions"]
        assert case["helped"] == (case["stage"] == "soso-transfer")
        for t in case["transitions"]:
            assert t.get("reason") or t.get("trigger")


def test_pathway_transfer_does_not_copy(full_run):
    """A learned pathway does not improve the held-out task
    by attempts (B 2, control 2) and does not copy A's answer:
    B selects rotate_logs, not A's clear_cache. The trace
    shows a real proposal-level difference the attempt count
    hides — with A's experience the transfer stage proposed
    clear_cache for B (correctly not executed, by the
    demonstrated-condition gate); without A, no transfer
    proposal. Verdict as measured: no improvement."""
    tr = full_run["pathway_transfer"]
    assert tr["b_attempts"] == 2
    assert tr["control_attempts"] == 2
    assert tr["improved"] is False
    assert tr["b_selected_a_procedure"] is False
    assert "did not improve" in tr["verdict"]


def test_pathway_transfer_proposal_level_difference():
    """The attempt count hides a real difference: with A's
    experience, the transfer stage proposes A's procedure
    for B; without it, no transfer proposal. The proposal is
    correctly not executed (demonstrated-condition gate)."""
    from contract.trace import trace_events
    h = Harness(tasks=[])
    try:
        from contract.trace003 import (PATHWAY_TRANSFER_A,
                                       PATHWAY_TRANSFER_A_XFER,
                                       PATHWAY_TRANSFER_B)
        for t in (PATHWAY_TRANSFER_A, PATHWAY_TRANSFER_A_XFER,
                  PATHWAY_TRANSFER_B):
            h.add_task(t)
        h.train_baseline()
        h.pipe.run({k: v for k, v in PATHWAY_TRANSFER_A.items()
                    if k != "correct"})
        h.pipe.qualify(PATHWAY_TRANSFER_A["task_id"], "RC-FIX-CLEAR-CACHE",
                       {k: v for k, v in PATHWAY_TRANSFER_A_XFER.items()
                        if k != "correct"})
        b_rep = h.pipe.run({k: v for k, v in PATHWAY_TRANSFER_B.items()
                            if k != "correct"})
        assert b_rep["state"] == "RESOLVED"
        # B selects rotate_logs, not A's clear_cache — it did
        # not copy A's answer. (Trial does attempt clear_cache
        # and it fails; the transfer stage proposed it but the
        # demonstrated-condition gate kept it from executing
        # via transfer — that is the proposal-level difference
        # below.)
        assert b_rep.get("selected") == "RC-FIX-ROTATE-LOGS"
        evs = trace_events(h.pipe.trace, PATHWAY_TRANSFER_B["task_id"],
                           b_rep["execution_id"])
        proposals = [e for _, e in evs
                     if e["event_type"] == "CANDIDATE_PROPOSED"]
        transfer_proposals = [
            e for e in proposals
            if e["data"].get("source") == "transfer"
            and "RC-FIX-CLEAR-CACHE" in str(e["data"].get("procedure", ""))]
        assert transfer_proposals, (
            "no transfer-stage proposal of A's procedure for B")
    finally:
        h.close()
    # Control: the same task without A's experience — no
    # transfer proposal of clear_cache at all.
    h2 = Harness(tasks=[PATHWAY_TRANSFER_B])
    try:
        h2.train_baseline()
        c_rep = h2.pipe.run({k: v for k, v in PATHWAY_TRANSFER_B.items()
                             if k != "correct"})
        evs = trace_events(h2.pipe.trace, PATHWAY_TRANSFER_B["task_id"],
                           c_rep["execution_id"])
        control_proposals = [
            e for _, e in evs
            if e["event_type"] == "CANDIDATE_PROPOSED"
            and e["data"].get("source") == "transfer"
            and "RC-FIX-CLEAR-CACHE" in str(e["data"].get("procedure", ""))]
        assert not control_proposals
    finally:
        h2.close()


def test_function_coverage_stays_explicit(full_run):
    """Coverage is explicit: 147 registered, 9 implemented.
    Registered-only functions are never counted as exercised
    cognition."""
    assert full_run["function_coverage"] == {
        "registered": 147, "implemented": 9}


# ---- JD items: shared-memory experiment, P5 benchmark,
#      measurement layers, completeness ----

def test_shared_memory_experiment_genuine_cross_task_reuse():
    """The deliberate shared-memory experiment: one record,
    many readers. The P7 experience block is read by 7
    distinct tasks (6 non-training tasks + the training task
    itself) — genuine cross-task reuse, not repeated
    references. The transfer-index is a second shared record:
    read by 25 executions/tasks, never written as a block
    event — a shared read-only record of the transfer
    pathway. The two genuinely new slope tasks resolve via
    soso-transfer and retrieve the transfer-index (not the
    exp block: that read is an exact-signature lookup, and a
    new signature has no exact record). The control tasks
    retrieve neither."""
    from contract.trace003 import run_shared_memory_experiment
    sm = run_shared_memory_experiment()
    exp_rec, index_rec = sm["shared_records"]
    assert exp_rec["block"] == "exp.ct-train-blocked-c.P7.2"
    assert exp_rec["n_reading_tasks"] == 7
    assert exp_rec["cross_task"] is True
    assert exp_rec["n_reads"] == 6
    assert index_rec["block"] == "transfer-index"
    assert index_rec["n_reading_tasks"] == 25
    assert "never written" in index_rec["mechanism"]
    for tid, nt in sm["new_tasks"].items():
        assert nt["resolved"] and nt["stage"] == "soso-transfer"
        assert nt["retrieved_transfer_index"] is True
        assert nt["retrieved_exp_block"] is False
    assert sm["control_retrieved"] == {
        "ab-trial-flood": False, "ab-trial-valve": False,
        "ab-trial-sensor": False}
    assert all(sm["resolved"].values())


def test_p5_benchmark_heldout_cases():
    """The P5-first standing benchmark on held-out cases
    (JD): the trap reproduces on 4 new P7-correct tasks
    (existing ranker: P5 first 4/4, 2 attempts each) and the
    revised ranker fixes it (P7 first 4/4, 1 attempt each).
    The P5-correct held-out tasks measure the cost: the
    revised ranker tries P7 first there too (2 attempts
    each). The clean untrained control (one fresh root per
    task) is identical to the existing ranker — the
    improvement comes from the learning, not the ranker
    alone. The sequential untrained condition documents the
    bootstrapping: the first task's own success teaches the
    ranker for the later tasks."""
    from contract.trace003 import run_p5_benchmark
    b = run_p5_benchmark()
    conds = b["conditions"]
    assert b["heldout_tasks"] == ["pf-h1", "pf-h2", "pf-h3", "pf-h4",
                                  "pf-h5", "pf-h6"]
    existing = conds["existing_trained"]["heldout_summary"]
    assert existing["p7_correct"]["p5_first"] == 4
    assert existing["p7_correct"]["attempts"] == 8
    assert existing["p5_correct"]["p5_first"] == 2
    assert existing["p5_correct"]["attempts"] == 2
    revised = conds["revised_trained"]["heldout_summary"]
    assert revised["p7_correct"]["p5_first"] == 0
    assert revised["p7_correct"]["attempts"] == 4
    assert revised["p5_correct"]["p5_first"] == 0
    assert revised["p5_correct"]["attempts"] == 4
    assert revised["all"]["false_resolved"] == 0
    untrained = conds["revised_untrained"]["heldout_summary"]
    assert untrained == existing
    sequential = conds["revised_untrained_sequential"]["heldout_summary"]
    assert sequential["all"]["p5_first"] == 1
    assert sequential["all"]["attempts"] == 7


def test_measurement_layers_kept_apart(full_run):
    """The four measurement layers are separate blocks in the
    result (JD): function calls (counted from spans),
    memory accesses (from the topology), pathway structure
    (signatures and stratified clusters), and causal
    contribution (ablation interventions only, labelled as
    such — with the explicit note that cluster membership,
    pathway frequency and memory-access counts are NOT
    causal evidence)."""
    layers = full_run["measurement_layers"]
    assert set(layers) == {"function_calls", "memory_accesses",
                           "pathway_structure", "causal_contribution"}
    fc = layers["function_calls"]
    assert fc["n_executions"] == 148
    assert fc["invocations_by_function"]["L01"] > 0
    assert "L25" not in fc["invocations_by_function"]
    ma = layers["memory_accesses"]
    assert ma["n_reused"] == 17
    assert ma["n_cross_task_reuse"] == 8
    ps = layers["pathway_structure"]
    assert ps["n_signatures"] == 148
    assert "training" in ps["strata"] and "evaluation" in ps["strata"]
    cc = layers["causal_contribution"]
    assert cc["arms"]["L24"]["delta_resolved"] == 0
    assert cc["arms"]["U37"]["delta_resolved"] == -12
    assert "controlled intervention" in cc["arms"]["L24"]["basis"]
    assert "NOT causal" in cc["non_causal_note"]


def test_shared_memory_and_p5_in_full_run(full_run):
    """run_all carries the shared-memory experiment and the
    P5 benchmark in its result, with the corrected topology
    JSON's reuse split (cross-task 8, same-task 9)."""
    sm = full_run["shared_memory_experiment"]
    assert sm["shared_records"][0]["n_reading_tasks"] == 7
    assert sm["shared_records"][1]["block"] == "transfer-index"
    assert sm["new_tasks"]["sm-slope-new2"]["stage"] == "soso-transfer"
    p5 = full_run["p5_benchmark"]
    assert p5["conditions"]["revised_trained"]["heldout_summary"][
        "p7_correct"]["attempts"] == 4
    topo = full_run["memory_topology"]
    assert topo["n_cross_task_reuse"] == 8
    assert topo["n_same_task_reread"] == 9
    assert topo["n_repeated_references"] == 28
