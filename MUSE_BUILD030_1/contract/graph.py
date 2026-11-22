"""The typed graph adapter (GRAPH-001 — FlyWire / COIN / TRACE-003
integration, JD 2026-11-18).

A directed multigraph built from TRACE-003 reconstructed pathways.
It represents what the recorder actually observed — function
invocations, memory references, procedure proposals, attempted
actions, validation results and corrections — as typed edges, so
the cognitive pathways can be analysed as ordered relationships
instead of only as event lists.

EDGE TYPES (kept distinct, per the directive):
  FOLLOWED_BY  — observed chronology (one span after another).
                 Chronological only, never causal.
  NESTED_IN    — call nesting (a span inside its parent span).
  ACCESSED     — a function accessed a memory block (derived from
                 the span/event linkage: the record is the same
                 object the enclosing span lists in its "memory").
  PROPOSED     — a stage proposed a procedure as a candidate.
  ATTEMPTED    — the execution span attempted a procedure.
  VALIDATED    — a procedure's attempt received a validation
                 outcome (PASS / FAIL / INCONCLUSIVE).
  CORRECTED_BY — a correction demoted a procedure/qualification.
  CAUSED_BY    — inferred causal dependence, and ONLY from a
                 CORRECTION event to the span that ran the demoted
                 procedure and observed False (the TRACE-002 rule).
  TRANSITIONED_TO — a domain transition recorded by the pathway.

PROVENANCE (BCV2-style, as described in JD's review):
  every edge carries its source — the trace event id and sequence
  it was derived from, the ledger root, and the adapter version.
  An edge that a later correction replaces is SUPERSEDED, never
  deleted or overwritten: the new edge names what it supersedes,
  the old edge names what superseded it, and both stay in the
  graph. History is preserved; the graph never rewrites the past.

NO AUTHORITY: the graph never writes an experience record, never
qualifies or withdraws a procedure, never executes anything, and
never changes any store. It reads pathways and returns a graph
and per-procedure feature scores. A ranker that consults those
scores is an experiment (the four arms in graph_experiment.py);
the graph itself decides nothing.

VERIFICATION BOUNDARY (JD's review, 2026-11-18): the FlyWire
packages' detailed contents (the motif 8,021 edge list, the BCV2
record fields, the reference-code API, the P2-G1 record's own
text) were NOT verified from the packages in this session — the
package-inventory helper was refused by a safety policy and was
not re-run or routed around. The motif used here is defined from
JD's review: a four-node motif with 9 directed edges, all pairs
confirmed in the preserved derived graph, a different motif from
the rejected 10,002 — operationalised below as a 4-node subset
with >= 9 directed pairs, at least one reciprocal pair, and a
convergent sink. If that definition is wrong, the motif score is
wrong with it; the generic features do not depend on it.
"""

import itertools
import random

ADAPTER_VERSION = "graph-adapter/0.1"

EDGE_TYPES = (
    "FOLLOWED_BY", "NESTED_IN", "ACCESSED", "PROPOSED", "ATTEMPTED",
    "VALIDATED", "CORRECTED_BY", "CAUSED_BY", "TRANSITIONED_TO",
)

# Node id prefixes by kind.
FN = "fn:"      # a 49-Brain function, e.g. fn:L30
BLK = "blk:"    # a memory block, e.g. blk:exp.ct-train-blocked-c.P7.2
PROC = "proc:"  # a procedure (registered id), e.g. proc:SOSO-P7
TASK = "task:"  # a task id
EXEC = "exec:"  # an execution id
VAL = "val:"    # a validation outcome, e.g. val:PASS


def _node(kind, label):
    return {"kind": kind, "label": label}


def _edge(eid, frm, to, etype, execution_id, task_id,
          source_event_id, source_seq, source_root):
    return {"id": eid, "from": frm, "to": to, "type": etype,
            "execution_id": execution_id, "task_id": task_id,
            "provenance": {"source_event_id": source_event_id,
                           "source_seq": source_seq,
                           "source_root": source_root,
                           "derived_by": ADAPTER_VERSION},
            "supersedes": [], "superseded_by": None}


def build_graph(pathways, source_root="unknown"):
    """Build the typed multigraph from a list of reconstructed
    pathways (each from contract.trace.reconstruct). Returns
    {"nodes", "edges", "supersession", "n_pathways",
    "n_executions"}. Deterministic: edges are emitted in
    pathway order, spans in order, and no timestamps or
    randomness are used."""
    nodes = {}
    edges = []
    supersession = []
    demotions = []
    eid = [0]

    def node(nid, kind, label):
        if nid not in nodes:
            nodes[nid] = _node(kind, label)
        return nid

    def edge(frm, to, etype, execution_id, task_id,
             source_event_id, source_seq):
        eid[0] += 1
        e = _edge(f"e{eid[0]}", frm, to, etype, execution_id, task_id,
                  source_event_id, source_seq, source_root)
        edges.append(e)
        return e

    for p in pathways:
        exec_id = p.get("execution_id")
        task_id = p.get("task_id")
        node(TASK + task_id, "task", task_id)
        node(EXEC + str(exec_id), "execution", str(exec_id))
        # The pathway's own typed relationships (NESTED_IN,
        # FOLLOWED_BY, TRANSITIONED_TO, CORRECTED_BY, CAUSED_BY)
        # are already span-level; the graph lifts them to
        # function nodes, keeping the type and the provenance
        # (the relationship's span, which names the event).
        spans = p.get("spans", [])
        span_fn = {}
        for s in spans:
            sid = s.get("span_id", s["function"])
            span_fn[sid] = FN + s["function"]
            node(FN + s["function"], "function", s["function"])
        for rel in p.get("relationships", []):
            frm = span_fn.get(rel["from"], rel["from"])
            to = span_fn.get(rel["to"], rel["to"])
            if rel["relation"] not in EDGE_TYPES:
                continue
            # TRANSITIONED_TO's destination is a domain, not a
            # span — keep it as a task-level edge.
            if rel["relation"] == "TRANSITIONED_TO":
                frm = TASK + task_id
                to = TASK + str(rel["to"])
                node(to, "task", str(rel["to"]))
            edge(frm, to, rel["relation"], exec_id, task_id,
                 rel.get("span"), None)
        # Corrections: the pathway's corrections list names the
        # procedure, the span that recorded it, and the
        # qualification change. A correction is a first-class
        # graph edge (CORRECTED_BY, from the recording span's
        # function to the procedure). The pathway's relationships
        # alone do not always carry it — the failures list is
        # empty when the correction comes from a CORRECTION
        # event rather than a recorded failure — so the graph
        # reads the corrections list directly. A correction
        # already covered by a CORRECTED_BY relationship is not
        # added twice.
        corrected = {(e["execution_id"], e["to"])
                     for e in edges if e["type"] == "CORRECTED_BY"}
        for c in p.get("corrections", []):
            proc = c.get("procedure")
            span_id = c.get("span")
            if not proc or span_id not in span_fn:
                continue
            if (exec_id, PROC + proc) in corrected:
                continue
            pnode = node(PROC + proc, "procedure", proc)
            # The CORRECTION event's id/seq, from the span's
            # own events; the correction's seq as the fallback.
            src_eid, src_seq = None, c.get("seq")
            for s in p.get("spans", []):
                if s.get("span_id") == span_id:
                    for ev in s.get("events", []):
                        if ev.get("event_type") == "CORRECTION":
                            src_eid = ev.get("event_id")
                            src_seq = ev.get("seq")
                            break
            e = edge(span_fn[span_id], pnode, "CORRECTED_BY",
                     exec_id, task_id, src_eid, src_seq)
            demotions.append({"execution_id": exec_id,
                              "procedure": proc,
                              "correction_edge_id": e["id"]})
        for s in p.get("spans", []):
            fn = FN + s["function"]
            for m in s.get("memory", []):
                blk = BLK + str(m["block"])
                node(blk, "block", str(m["block"]))
                edge(fn, blk, "ACCESSED", exec_id, task_id,
                     m.get("event_id"), m.get("seq"))
        # Proposals, attempts and validations, from the spans'
        # events (the L15 span carries OBSERVATION/VALIDATION;
        # CANDIDATE_PROPOSED events carry the proposing stage).
        for s in p.get("spans", []):
            fn = FN + s["function"]
            for ev in s.get("events", []):
                etype = ev.get("event_type")
                proc = ev.get("procedure")
                if etype == "CANDIDATE_PROPOSED" and proc:
                    pnode = node(PROC + proc, "procedure", proc)
                    edge(fn, pnode, "PROPOSED", exec_id, task_id,
                         ev.get("event_id"), ev.get("seq"))
                elif etype == "OBSERVATION" and proc:
                    pnode = node(PROC + proc, "procedure", proc)
                    e = edge(FN + "L15", pnode, "ATTEMPTED", exec_id,
                             task_id, ev.get("event_id"), ev.get("seq"))
                    # The observation outcome rides on the edge,
                    # so the feature computation can join an
                    # attempt with its validation without
                    # re-reading the trace.
                    e["observed"] = ev.get("observed")
                elif etype == "VALIDATION" and proc:
                    pnode = node(PROC + proc, "procedure", proc)
                    vnode = node(VAL + str(ev.get("validation")),
                                 "validation", str(ev.get("validation")))
                    e = edge(pnode, vnode, "VALIDATED", exec_id, task_id,
                             ev.get("event_id"), ev.get("seq"))
                    e["validation"] = ev.get("validation")
    # Supersession is applied here, from the corrections the
    # graph itself recorded: a correction supersedes the earlier
    # proposal of the procedure it demotes, in the same
    # execution. The old edge stays, marked superseded_by; the
    # correction edge names what it supersedes. Nothing is
    # deleted or overwritten. (apply_supersession is also
    # available for demotions supplied from outside the graph.)
    supersession = apply_supersession(
        {"edges": edges, "supersession": supersession}, demotions)
    return {"nodes": nodes, "edges": edges,
            "supersession": supersession,
            "n_pathways": len(pathways),
            "n_executions": len({p.get("execution_id")
                                 for p in pathways})}


def apply_supersession(graph, demotions):
    """Apply BCV2-style supersession to a built graph.
    demotions: a list of {"execution_id", "procedure",
    "correction_edge_id"} — each names a procedure demoted by
    a correction in an execution. Every PROPOSED edge for that
    procedure in that execution is marked superseded_by the
    correction edge, and the correction edge names what it
    supersedes. Returns the supersession records. The old
    edges stay in the graph; nothing is deleted."""
    by_id = {e["id"]: e for e in graph["edges"]}
    records = []
    for d in demotions:
        corr = by_id.get(d["correction_edge_id"])
        if corr is None:
            continue
        superseded = []
        for e in graph["edges"]:
            if (e["type"] == "PROPOSED"
                    and e["execution_id"] == d["execution_id"]
                    and e["to"] == PROC + d["procedure"]
                    and e["superseded_by"] is None):
                e["superseded_by"] = corr["id"]
                corr["supersedes"].append(e["id"])
                superseded.append(e["id"])
        records.append({"correction_edge_id": corr["id"],
                        "execution_id": d["execution_id"],
                        "procedure": d["procedure"],
                        "superseded": superseded})
    graph["supersession"] = records
    return records


# ------------------------------------------------------------------
# Features.
# ------------------------------------------------------------------

def _procedure_transitions(graph):
    """Consecutive ATTEMPTED procedures within an execution, in
    attempt order. Returns a list of (from_proc, to_proc,
    execution_id)."""
    by_exec = {}
    for e in graph["edges"]:
        if e["type"] == "ATTEMPTED":
            by_exec.setdefault(e["execution_id"], []).append(e)
    transitions = []
    for exec_id, evs in by_exec.items():
        procs = [e["to"] for e in evs]
        for a, b in zip(procs, procs[1:]):
            transitions.append((a, b, exec_id))
    return transitions


def compute_features(graph):
    """Per-procedure graph features, from the built graph.
    Returns {"procedures": {proc: {...}}, "repeated_patterns",
    "reciprocal_cores"}. A procedure is a registered id
    (e.g. SOSO-P7). All counts are over the graph as built —
    no hidden state, no randomness."""
    procs = sorted({e["to"] for e in graph["edges"]
                    if e["type"] in ("PROPOSED", "ATTEMPTED")})
    # The feature table is keyed by the REGISTERED procedure
    # id (e.g. "SOSO-P7") — the same id the pipeline's ranker
    # sees as entry.procedure_id. The graph's node ids carry
    # the "proc:" prefix; the prefix is stripped here, at the
    # boundary between the graph and the ranker.
    proc_ids = [p[len(PROC):] if p.startswith(PROC) else p
                for p in procs]
    proc_of = dict(zip(procs, proc_ids))
    transitions = _procedure_transitions(graph)
    # Validated pathway history: executions in which the
    # procedure was attempted AND the attempt was an OBSERVED
    # SUCCESS validated PASS. A truthful failure (observed
    # False, validation PASS — the validator passes a correct
    # failure) is NOT a validated success: the trap procedure
    # P5 fails its observations and must not earn validated
    # history for failing correctly. The join is on
    # (execution, procedure) between the ATTEMPTED edge's
    # observed flag and the VALIDATED edge's outcome.
    observed_success = {}
    for e in graph["edges"]:
        if e["type"] == "ATTEMPTED" and e.get("observed") is True:
            observed_success.setdefault(e["to"], set()).add(
                e["execution_id"])
    validated = {}
    for e in graph["edges"]:
        if e["type"] == "VALIDATED" and e.get("validation") == "PASS":
            validated.setdefault(e["from"], set()).add(
                e["execution_id"])
    validated_history = {
        p: (observed_success.get(p, set()) & validated.get(p, set()))
        for p in procs}
    attempted_exec = {}
    for e in graph["edges"]:
        if e["type"] == "ATTEMPTED":
            attempted_exec.setdefault(e["to"], set()).add(
                e["execution_id"])
    # Repeated sequence patterns: executions grouped by their
    # function sequence; a sequence that occurs in >= 2
    # executions is a repeated pattern. A procedure earns the
    # count of the repeated-pattern executions it appears in.
    seq_by_exec = {}
    proc_by_exec = {}
    for e in graph["edges"]:
        if e["type"] == "FOLLOWED_BY":
            seq_by_exec.setdefault(e["execution_id"], set()).add(
                (e["from"], e["to"]))
        if e["type"] == "ATTEMPTED":
            proc_by_exec.setdefault(e["execution_id"], set()).add(
                e["to"])
    # Function sequences are represented by the set of
    # FOLLOWED_BY pairs per execution (a chain's edge set
    # determines the chain). Group executions by that key; a
    # key occurring in >= 2 executions is a repeated pattern.
    exec_seq_key = {eid: tuple(sorted(pairs))
                    for eid, pairs in seq_by_exec.items()}
    seq_counts = {}
    for key in exec_seq_key.values():
        seq_counts[key] = seq_counts.get(key, 0) + 1
    repeated = {k: v for k, v in seq_counts.items() if v >= 2}
    features = {}
    for p in procs:
        succ = {b for a, b, _ in transitions if a == p}
        pred = {a for a, b, _ in transitions if b == p}
        # Repeated sequence patterns: the DISTINCT repeated
        # function-sequence patterns in which the procedure
        # appears AND the execution validated PASS. A recurring
        # pattern that ends in failure (the trap procedure's
        # first-attempt pattern) is a real graph property but
        # not a positive signal — it earns nothing here.
        rep_patterns = {
            exec_seq_key[eid]
            for eid in proc_by_exec
            if p in proc_by_exec.get(eid, set())
            and exec_seq_key.get(eid) in repeated
            and eid in validated_history.get(p, set())}
        features[proc_of[p]] = {
            "degree": len(succ) + len(pred),
            "fan_in": len(pred),
            "fan_out": len(succ),
            "branch_count": len(succ),
            "validated_pathway_history": len(
                validated_history.get(p, set())),
            "attempted_executions": len(attempted_exec.get(p, set())),
            "repeated_sequence_patterns": len(rep_patterns),
            "sink_score": len(pred),
            "reciprocal_core": False,
        }
    cores = find_reciprocal_cores(graph)
    for core in cores:
        for member in core["members"]:
            pid = proc_of.get(member, member)
            if pid in features:
                features[pid]["reciprocal_core"] = True
    return {"procedures": features,
            "repeated_patterns": len(repeated),
            "reciprocal_cores": cores}


def find_reciprocal_cores(graph, size=4, min_edges=9):
    """The reciprocal-core motif, operationalised from JD's
    review: a subset of `size` nodes with >= `min_edges`
    directed pairs among them, at least one reciprocal pair
    (A->B and B->A), and a convergent sink (a node with
    in-degree >= size-1 within the subset — at least three of
    the other nodes lead into it). The search runs over the
    function + procedure nodes (the cognitive core), counting
    distinct directed pairs regardless of edge type. Returns a
    list of {"members", "n_edges", "reciprocal_pairs", "sink"}.
    An empty list is a finding, not a failure: it means the
    software's execution graph does not contain the dense
    reciprocal structure the biological motif describes."""
    core_nodes = sorted({e["from"] for e in graph["edges"]
                         if e["from"].startswith((FN, PROC))}
                        | {e["to"] for e in graph["edges"]
                           if e["to"].startswith((FN, PROC))})
    pairs = set()
    for e in graph["edges"]:
        if e["from"].startswith((FN, PROC)) and e["to"].startswith((FN, PROC)):
            pairs.add((e["from"], e["to"]))
    cores = []
    for subset in itertools.combinations(core_nodes, size):
        s = set(subset)
        inside = [(a, b) for a, b in pairs if a in s and b in s]
        if len(inside) < min_edges:
            continue
        reciprocal = [(a, b) for a, b in inside
                      if a != b and (b, a) in pairs]
        if not reciprocal:
            continue
        in_deg = {n: sum(1 for a, b in inside if b == n) for n in s}
        sinks = [n for n, d in in_deg.items() if d >= size - 1]
        if not sinks:
            continue
        cores.append({"members": sorted(subset),
                      "n_edges": len(inside),
                      "reciprocal_pairs": sorted({tuple(sorted(p))
                                                  for p in reciprocal}),
                      "sink": sorted(sinks)})
    return cores


def _norm(values):
    """Normalise a dict of numbers to 0..1 by the max (0 when
    the max is 0). Deterministic."""
    mx = max(values.values()) if values else 0
    if not mx:
        return {k: 0.0 for k in values}
    return {k: v / mx for k, v in values.items()}


def score_tables(features, seed=20261118):
    """The three score tables for the experiment arms, from one
    feature set. Scores are keyed by registered procedure id
    and are the tie-breaker after the base (family, shared,
    soft) rank — higher is better.
      generic: validated pathway history + fan-in (the ordinary
               graph features).
      motif:   generic + convergent-sink score + reciprocal-core
               membership (the FlyWire-derived feature).
      random:  generic + the motif component randomly reassigned
               across procedures with a fixed seed (the control:
               the same total motif weight, no motif information).
    """
    procs = features["procedures"]
    validated = _norm({p: f["validated_pathway_history"]
                       for p, f in procs.items()})
    fan_in = _norm({p: f["fan_in"] for p, f in procs.items()})
    sink = _norm({p: f["sink_score"] for p, f in procs.items()})
    generic = {p: validated[p] + 0.25 * fan_in[p] for p in procs}
    motif_component = {
        p: sink[p] + (1.0 if f["reciprocal_core"] else 0.0)
        for p, f in procs.items()}
    motif = {p: generic[p] + motif_component[p] for p in procs}
    rng = random.Random(seed)
    shuffled = list(motif_component.values())
    rng.shuffle(shuffled)
    random_component = {p: v for p, v in zip(sorted(procs), shuffled)}
    random_table = {p: generic[p] + random_component[p] for p in procs}
    return {"generic": generic, "motif": motif, "random": random_table,
            "components": {"validated": validated, "fan_in": fan_in,
                           "sink": sink,
                           "motif_component": motif_component,
                           "random_component": random_component}}
