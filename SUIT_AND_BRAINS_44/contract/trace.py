"""TRACE-001 — Cognitive Execution Trace (Phases A + B).

Records ACTUAL cognitive activity in the SoSo–RealCog pipeline:
which of the 49-Brain functions really executed, which memory
blocks they accessed, in what order, and how the investigation
changed direction. This is the original SoSo cognitive-mapping
ambition, now implementable because RealCog, the memory substrate
and the integrated pipeline exist.

Two distinctions the trace preserves (they are the point of the
whole exercise):
  * Observed behaviour vs reconstructed interpretation are
    different kinds of information. A function call is an
    observation of software execution. Saying the call represents
    curiosity, judgment, expertise or understanding is an
    interpretation that needs experimental support. The trace
    records the observation; the analysis proposes the
    interpretation and tests it.
  * Transferable cognitive organization must not transfer
    authority. A pathway learned from another agent still
    operates under its own Suit, capabilities and applicable
    conditions. The trace is a measurement layer, not an
    authority.

A trace can prove that a function ran or a memory block was
accessed. It cannot, by itself, prove that a particular internal
mental representation caused the answer. Causal claims require
controlled experiments (e.g. disabling a function and measuring
what changes). The trace is the measurement system that makes
those experiments possible.

Event schema (a PROPOSED trace format, not an existing PH6
canonical wire schema — "soso-cognitive-event/0.1"):
  schema              "soso-cognitive-event/0.1"
  execution_id        stable id for one task execution
                      ("run-<task_id>-<uuid8>")
  event_id            stable per-event id ("ev-<seq>")
  parent_event_id     the event that caused/contains this one
                      (the parent span's FUNCTION_ENTER, or null
                      for the TASK_BEGIN). Causal order is
                      explicit; timestamps alone are insufficient.
  sequence            the TraceLog's global sequence (ordering)
  task_id
  function_namespace  "lower49" | "higher49" | "soso" | "suit" |
                      "memory" | None
  function_id         e.g. "L01" | "L30" | "U09" | "L24" | "U10",
                      or None for non-function events
  function_version    "experimental-1" (every instrumented
                      function is an experimental implementation)
  event_type          TASK_BEGIN | FUNCTION_ENTER | FUNCTION_EXIT |
                      MEMORY_READ | MEMORY_WRITE | MEMORY_RETRIEVE |
                      MEMORY_COMPARE | MEMORY_REFERENCE |
                      CANDIDATE_PROPOSED | CANDIDATE_REJECTED |
                      ADMISSION | OBSERVATION | VALIDATION |
                      DOMAIN_TRANSITION | ESCALATION | IOU_OPENED |
                      FAILURE_CLASS | TASK_END | FUNCTION_RESULT
                      (FUNCTION_RESULT records one instrumented
                      function's input/output summary on its own
                      span. OBSERVATION is reserved for execution
                      observations: exactly one OBSERVATION and
                      one VALIDATION per attempt, paired in order
                      — the PathwayRecorder enforces that pairing,
                      and the graph adapter derives ATTEMPTED
                      edges from OBSERVATION events. A span-result
                      record must never use OBSERVATION, or it
                      would read as a phantom attempt.)
  domain              the cognitive domain of the execution
  domain_transition_id  the id of the domain transition this event
                      belongs to, or None
  input_refs          stable references to the inputs, e.g.
                      ["int:<task_id>"] (never unrestricted
                      private memory contents)
  memory_refs         the memory blocks this event references
                      (a list; a memory event references exactly
                      the block it accessed)
  status              "STARTED" | "COMPLETED" | "FAILED" |
                      "REJECTED" | "RECORDED" | None
  authority           0 (AUTH:0 throughout)
  span_id             (internal grouping) the span this event
                      belongs to; "root" for the task span
  data                a small JSON payload: procedure ids, stage,
                      reasons, validation results, evidence
                      excerpts. Stable references, hashes and
                      metadata — never unrestricted private memory
                      contents.

Storage: trace events are stored in the pipeline's existing
RealCog TraceLog (hash-chained, append-only, WAL + synchronous
FULL) under kind "TRACE_EVENT", with the full event dict in the
data field. No new store is introduced, so the existing recovery
mechanisms (recover(), the hash-chain verify) cover the trace
too. The trace writer receives events from throughout the
pipeline; it is not an extra reasoning stage that every action
must pass through, and it never crashes a run: a schema problem
is recorded IN the event (a "schema_problems" field) rather than
raising.

The instrumented functions are the ones the pipeline actually
invokes (see FUNCTIONS). The other 42 functions are REGISTERED
and never invoked; L25 (Alphabetical Reorder) is the deliberate
control — a plausible-sounding function that adds no capability
and must never appear in a trace. A function appears in a trace
only when its operation actually ran: L24 only when a cross-task
candidate is produced, U10 only when a disambiguation is
performed. That is genuine instrumentation coverage, not
inference from module imports or procedure selection. Missing
spans are recorded as instrumentation gaps, never invented
activity.

The 2,208 logical memory blocks are logical memory capacity,
not 2,208 thinking functions: Big49 cognitive (1280) +
association (320); Small49 history (256) + active (64) +
workspace (128) + bridge (160). The trace records the blocks
actually accessed, by bucket; it never claims all 2,208 were
exercised. Primitive/Lower49 and Higher49 remain separate
identities even though both expose 49-position registries.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import Counter

SCHEMA = "soso-cognitive-event/0.1"
FUNCTION_VERSION = "experimental-1"
TRACE_KIND = "TRACE_EVENT"

# The 49-Brain functions the pipeline actually invokes, with the
# SoSo app's own names (adapters/cognition.py run_cognitive_cycle
# + adapters/cognitive_layer.py + adapters/expansion.py). The
# namespace is the function's home: L* = Lower/Primitive 49,
# U* = Higher/Big 49. L25 is listed as REGISTERED (never
# activated, never invoked) so the analysis can distinguish it
# from executed functions.
FUNCTIONS = {
    "L01": ("Interpret Task", "lower49"),
    "U08": ("Retrieve Memory", "higher49"),
    "L30": ("Retrieve Experience", "lower49"),
    "U09": ("Compare Candidates", "higher49"),
    "U37": ("Decide Selection", "higher49"),
    "L15": ("Execute Procedure", "lower49"),
    "L21": ("Record Outcome", "lower49"),
    "L24": ("Cross-Task Recognise", "lower49"),
    "U10": ("Disambiguate Conditions", "higher49"),
    # Cognitive Function Expansion (2026-11-18): the next six
    # functions, implemented in the pipeline by definition and
    # dependency. The ids are the lowest available registry
    # slots in each group, assigned by this build — bookkeeping,
    # NOT recovered historical definitions. U02 is GOVERNANCE,
    # not a brain function: it is the Suit admission capability
    # (admit(), contract/admission.py), counted separately in
    # the brain/admission split below.
    "U01": ("Evaluate Demonstrated Conditions", "higher49"),
    "U02": ("Authorize Execution", "higher49"),
    "U03": ("Answer With Frozen Expectation", "higher49"),
    "U04": ("Gate Trial Candidates", "higher49"),
    "U05": ("Select Measurements", "higher49"),
    "L02": ("Learn Applicability", "lower49"),
}
REGISTERED_ONLY = {
    "L25": ("Alphabetical Reorder", "lower49"),
}

# TRACE-002, Correction A (JD's audit): three function states,
# distinguished explicitly — REGISTERED, IMPLEMENTED, INVOKED.
# The app's registry (adapters/modules.py ALL_FUNCTION_IDS)
# registers 147 ids: U01–U49 (Higher 49), L01–L49 (Lower/
# Primitive 49) and P01–P49 (Primitive 49, same ledger).
# IMPLEMENTED in the pipeline are 15: the app's 7 pathway brains
# (U08, U09, U37, L01, L15, L21, L30 — modules.py
# IMPLEMENTED_BRAINS) plus L24 and U10 (the MEM-004 expansion,
# adapters/expansion.py, activated by measurement) plus the six
# Cognitive Function Expansion functions (U01, U03, U04, U05,
# L02 — brain functions — and U02, the Suit admission capability,
# which is GOVERNANCE, not a brain function: 14 brain + 1
# admission). The other 132 ids are REGISTERED only — never
# implemented, never invoked. INVOKED is per execution: the
# functions that actually ran (8 in the TRACE-001 demonstration;
# U10 was implemented but not invoked there). A trace is never
# presented as "49-function cognition": the reconstruction
# reports all three states.
ALL_REGISTERED_IDS = ([f"U{i:02d}" for i in range(1, 50)]
                      + [f"L{i:02d}" for i in range(1, 50)]
                      + [f"P{i:02d}" for i in range(1, 50)])
IMPLEMENTED_IDS = tuple(sorted(FUNCTIONS))
LOGICAL_BLOCKS = {
    "big49": {"cognitive": 1280, "association": 320},
    "small49": {"history": 256, "active": 64, "workspace": 128,
                "bridge": 160},
}
TOTAL_LOGICAL_BLOCKS = 2208

# Failure classes for incorrect reasoning pathways (directive §5).
# Mapped from the EVAL-001 failure taxonomy (contract/eval001.py
# classify): selection -> recognition, authorization ->
# authorization, execution -> investigation, validation ->
# validation, correct_escalation -> missing information (when an
# information request was emitted) or recognition (when the
# correct procedure was never proposed).
FAILURE_CLASSES = (
    "recognition", "transfer", "investigation", "authorization",
    "validation", "missing_information",
)

# The five data classes every experiment records (directive §5).
EVENT_CLASSES = {
    "TASK_BEGIN": "execution", "FUNCTION_ENTER": "execution",
    "FUNCTION_EXIT": "execution", "MEMORY_READ": "memory",
    "MEMORY_WRITE": "memory", "MEMORY_RETRIEVE": "memory",
    "MEMORY_COMPARE": "memory", "MEMORY_REFERENCE": "memory",
    "CANDIDATE_PROPOSED": "investigation",
    "CANDIDATE_REJECTED": "investigation",
    "ADMISSION": "investigation", "OBSERVATION": "investigation",
    "VALIDATION": "investigation", "DOMAIN_TRANSITION": "domain",
    "ESCALATION": "domain", "IOU_OPENED": "investigation",
    "FAILURE_CLASS": "outcome", "TASK_END": "outcome",
    # TRACE-002: a trusted (QUALIFIED) record demoted by a failed
    # attempt (the correction of a wrong decision), and a function
    # deliberately disabled for an ablation experiment.
    "CORRECTION": "outcome", "FUNCTION_DISABLED": "execution",
    # Cognitive Function Expansion: one instrumented function's
    # input/output summary, recorded on its own span. (See the
    # schema note above: never OBSERVATION — that type is the
    # per-attempt execution observation, paired with VALIDATION.)
    "FUNCTION_RESULT": "execution",
}

# Recording modes (TRACE-002, Correction C). "best_effort" is the
# experimental mode: execution continues, missing instrumentation
# is recorded and coverage is marked incomplete. "evidence_grade"
# is the certification mode: a trajectory is certified complete
# only when every required event and commitment is present; an
# incomplete trajectory is marked NOT CERTIFIED and is never
# silently presented as complete. Neither mode invents events.
RECORDING_MODES = ("best_effort", "evidence_grade")


def function_states(invoked, disabled_functions=(), descriptions="default"):
    """The three function states for an execution (Correction A):
    REGISTERED (all 147 ids in the app's registry), IMPLEMENTED
    (the 15 the pipeline actually implements) and INVOKED (the
    functions that ran in this execution). Returns the counts
    plus the implemented-but-not-invoked list, so a reader can
    see exactly how much of the 49-Brain cognition a trace
    covers — and how much it does not.

    TRACE-002 correction, item 5: an implemented function that
    did not run is NOT one undifferentiated absence. The reason
    it did not run is recorded per function, and the four kinds
    of absence are distinct:
      disabled                  — capability suppression: the
                                  function was disabled by ablation
                                  (a FUNCTION_DISABLED event).
      information_source_removed — L24, when descriptions is
                                  "none": the layer could not
                                  compute cross-task candidates,
                                  so L24 had no input. L24 itself
                                  was not disabled; its input was
                                  removed.
      strategy_ablated          — L24/U09, when U08 is disabled:
                                  the whole transfer stage was
                                  skipped, so the function never
                                  had a chance to run.
      not_invoked_by_this_task  — the function was available and
                                  its stage ran, but this task did
                                  not need it (U10 when no
                                  information request was
                                  triggered; L24 when no cross-task
                                  candidate was produced).
    A function that never appears is never counted as exercised
    cognition, whatever the reason."""
    invoked = list(invoked or [])
    disabled = set(disabled_functions or [])
    not_invoked = sorted(set(IMPLEMENTED_IDS) - set(invoked))
    reasons = {}
    for fid in not_invoked:
        if fid in disabled:
            reasons[fid] = "disabled"
        elif fid == "L24" and descriptions == "none":
            reasons[fid] = "information_source_removed"
        elif fid in ("L24", "U09") and "U08" in disabled:
            reasons[fid] = "strategy_ablated"
        else:
            reasons[fid] = "not_invoked_by_this_task"
    return {"registered": len(ALL_REGISTERED_IDS),
            "implemented": len(IMPLEMENTED_IDS),
            "invoked": len(invoked),
            "implemented_ids": list(IMPLEMENTED_IDS),
            "invoked_ids": invoked,
            "implemented_not_invoked": not_invoked,
            "not_invoked_reasons": reasons,
            "registered_only_count": (len(ALL_REGISTERED_IDS)
                                      - len(IMPLEMENTED_IDS))}


def new_execution_id(task_id: str) -> str:
    return f"run-{task_id}-{uuid.uuid4().hex[:8]}"


def validate_event(ev: dict) -> list:
    """Return the list of schema problems (empty when valid)."""
    problems = []
    if ev.get("schema") != SCHEMA:
        problems.append(f"schema {ev.get('schema')!r} != {SCHEMA!r}")
    for key in ("execution_id", "event_id", "task_id", "event_type"):
        if not ev.get(key):
            problems.append(f"missing {key}")
    if ev.get("function_id") is not None and ev["function_id"] not in FUNCTIONS:
        problems.append(f"unknown function {ev['function_id']!r}")
    if ev.get("function_namespace") is not None and ev["function_namespace"] not in (
            "lower49", "higher49", "soso", "suit", "memory"):
        problems.append(f"unknown namespace {ev['function_namespace']!r}")
    if ev.get("authority") != 0:
        problems.append(f"authority {ev.get('authority')!r} != 0")
    return problems


class Tracer:
    """Records trace events for one task execution into the
    pipeline's TraceLog. Every method appends exactly one event
    (durable, hash-chained). Spans are explicit: begin_span
    records FUNCTION_ENTER, end_span records FUNCTION_EXIT; a
    span with an entry and no exit is INCOMPLETE (a crash), and
    the reconstruction utility flags it rather than silently
    dropping it. The tracer never raises during a run: a schema
    problem is recorded in the event itself."""

    def __init__(self, trace_log, task_id, execution_id=None,
                 domain=None, profile=None, parent_execution_id=None,
                 mode="best_effort"):
        self.log = trace_log
        self.task_id = task_id
        self.execution_id = execution_id or new_execution_id(task_id)
        self.domain = domain
        self.profile = profile
        # TRACE-002, Correction E: the parent execution this
        # execution belongs to (a multi-step parent, or None).
        # Recorded in TASK_BEGIN so the parent/child linkage is
        # verifiable from the ledger, not from parsing task names.
        self.parent_execution_id = parent_execution_id
        # TRACE-002, Correction C: the recording mode. "best_effort"
        # (experimental) continues on missing instrumentation;
        # "evidence_grade" requires the trajectory to be certified
        # complete before it is presented as evidence.
        if mode not in RECORDING_MODES:
            raise ValueError(f"unknown recording mode {mode!r}")
        self.mode = mode
        self._spans = {}          # span_id -> (function_id, enter event_id)
        self._root_event_id = None

    def certify(self):
        """Certify this execution's trajectory (Correction C).
        Returns the certification dict (see certify()). In
        evidence_grade mode the caller must check certified ==
        True before presenting the trajectory as complete."""
        return certify(self.log, self.task_id, self.execution_id)

    def _emit(self, event_type, span_id, parent_event_id=None,
              function_id=None, function_namespace=None,
              domain_transition_id=None, input_refs=None,
              memory_refs=None, status=None, data=None):
        ev = {"schema": SCHEMA, "execution_id": self.execution_id,
              "event_id": None, "parent_event_id": parent_event_id,
              "sequence": None, "task_id": self.task_id,
              "function_namespace": function_namespace,
              "function_id": function_id,
              "function_version": FUNCTION_VERSION if function_id else None,
              "event_type": event_type, "domain": self.domain,
              "domain_transition_id": domain_transition_id,
              "input_refs": input_refs or [],
              "memory_refs": memory_refs or [], "status": status,
              "authority": 0, "span_id": span_id, "data": data or {}}
        problems = validate_event({**ev, "event_id": "ev-pending"})
        if problems:
            ev["data"] = {**ev["data"], "schema_problems": problems}
        seq = self.log.append(TRACE_KIND, self.task_id, None, ev)
        ev["event_id"] = f"ev-{seq:04d}"
        ev["sequence"] = seq
        # The stored copy carries the pending id; the returned
        # event carries the real one. Reconstruction reads the
        # stored copy and derives the id from the sequence, so
        # the two agree.
        self._last_event_id = ev["event_id"]
        return ev

    def begin_task(self, context=None):
        data = {"task_id": self.task_id, "domain": self.domain,
                "profile": self.profile,
                "parent_execution_id": self.parent_execution_id,
                "recording_mode": self.mode,
                "context": context or {}}
        ev = self._emit("TASK_BEGIN", "root", parent_event_id=None,
                        status="STARTED",
                        input_refs=[f"int:{self.task_id}"],
                        data=data)
        self._root_event_id = ev["event_id"]
        return ev

    def disabled(self, function_id, span_id="root", reason=None):
        """Record that a function was deliberately disabled (an
        ablation). The event names the function and the reason so
        a missing span is attributable to the experiment, not to
        a recording failure."""
        return self.event("FUNCTION_DISABLED", span_id, status="RECORDED",
                          function_id=function_id,
                          function_namespace=FUNCTIONS[function_id][1]
                          if function_id in FUNCTIONS else None,
                          data={"function": function_id,
                                "reason": reason or
                                "ablation: function disabled"})

    def correction(self, span_id, procedure, previous, new,
                   reason=None, evidence=None):
        """Record that a trusted (QUALIFIED) record was demoted by
        a failed attempt — the correction of a wrong decision.
        The previous qualification, the new one and the reason are
        recorded; the earlier record is preserved (append-only),
        not overwritten."""
        return self.event("CORRECTION", span_id, status="RECORDED",
                          data={"procedure": procedure,
                                "previous_qualification": previous,
                                "new_qualification": new,
                                "reason": reason or "",
                                "evidence": evidence or {}})

    def begin_span(self, function_id, parent_span_id="root", purpose=None):
        _name, namespace = FUNCTIONS[function_id]
        span_id = f"span-{function_id}-{uuid.uuid4().hex[:6]}"
        parent_event = None
        if parent_span_id in self._spans:
            parent_event = self._spans[parent_span_id][1]
        elif parent_span_id == "root":
            parent_event = self._root_event_id
        self._spans[span_id] = (function_id, None)
        ev = self._emit("FUNCTION_ENTER", span_id,
                        parent_event_id=parent_event,
                        function_id=function_id,
                        function_namespace=namespace, status="STARTED",
                        input_refs=[f"int:{self.task_id}"],
                        data={"purpose": purpose} if purpose else {})
        self._spans[span_id] = (function_id, ev["event_id"])
        return span_id

    def end_span(self, span_id, result=None, detail=None):
        function_id = self._spans.get(span_id, (None, None))[0]
        namespace = FUNCTIONS[function_id][1] if function_id else None
        parent_event = None
        data = {}
        if result is not None:
            data["result"] = result
        if detail is not None:
            data["detail"] = detail
        self._emit("FUNCTION_EXIT", span_id, function_id=function_id,
                   function_namespace=namespace,
                   status="COMPLETED" if result != "error" else "FAILED",
                   data=data)

    def memory(self, span_id, operation, store, block_id, bucket=None,
               detail=None):
        event_type = {"read": "MEMORY_READ", "write": "MEMORY_WRITE",
                      "retrieve": "MEMORY_RETRIEVE",
                      "compare": "MEMORY_COMPARE",
                      "reference": "MEMORY_REFERENCE"}[operation]
        data = {"store": store, "bucket": bucket}
        if detail is not None:
            data["detail"] = detail
        parent_event = self._spans.get(span_id, (None, None))[1]
        self._emit(event_type, span_id, parent_event_id=parent_event,
                   function_namespace="memory", status="RECORDED",
                   memory_refs=[block_id], data=data)

    def event(self, event_type, span_id, data=None, function_id=None,
              function_namespace=None, status=None, input_refs=None,
              domain_transition_id=None):
        parent_event = self._spans.get(span_id, (None, None))[1]
        return self._emit(event_type, span_id,
                          parent_event_id=parent_event,
                          function_id=function_id,
                          function_namespace=function_namespace,
                          domain_transition_id=domain_transition_id,
                          input_refs=input_refs, status=status,
                          data=data or {})

    def domain_transition(self, span_id, origin, destination, trigger,
                          reason, evidence=None, outcome=None):
        """A cross-domain move, with its explanation recorded. An
        unexplained jump (no reason) is flagged by reconstruction
        — the caller must supply the reason; this method records
        exactly what it is given, including an empty reason, so a
        missing explanation is visible, not hidden."""
        tid = f"dt-{uuid.uuid4().hex[:8]}"
        self.event("DOMAIN_TRANSITION", span_id, status="RECORDED",
                   domain_transition_id=tid, data={
                       "origin": origin, "destination": destination,
                       "trigger": trigger, "reason": reason,
                       "evidence": evidence or {}, "outcome": outcome})
        return tid

    def failure(self, span_id, error_class, divergence, evidence=None,
                corrected_by=None):
        if error_class not in FAILURE_CLASSES:
            raise ValueError(f"unknown failure class {error_class!r}")
        self.event("FAILURE_CLASS", span_id, status="RECORDED", data={
            "error_class": error_class, "divergence": divergence,
            "evidence": evidence or {}, "corrected_by": corrected_by})

    def end_task(self, outcome, selected=None, stage=None, detail=None):
        data = {"outcome": outcome, "selected": selected, "stage": stage}
        if detail is not None:
            data["detail"] = detail
        self._emit("TASK_END", "root", status="COMPLETED",
                   data=data)


def trace_events(trace_log, task_id, execution_id=None):
    """All TRACE_EVENT events for a task execution, in TraceLog
    order. Returns (seq, event) pairs, with the event_id derived
    from the sequence (the stored copy carries the pending id;
    the sequence is authoritative). With no execution_id, the
    LATEST execution of the task is used — a task run several
    times has several executions, and aggregating them would mix
    pathways."""
    all_ev = []
    for seq, _kind, _task, _step, data in trace_log.events(
            task_id, kinds=[TRACE_KIND]):
        ev = json.loads(data) if isinstance(data, str) else dict(data)
        ev["event_id"] = f"ev-{seq:04d}"
        ev["sequence"] = seq
        all_ev.append((seq, ev))
    if execution_id is None:
        begins = [ev for _, ev in all_ev if ev["event_type"] == "TASK_BEGIN"]
        if begins:
            execution_id = begins[-1]["execution_id"]
    return [(seq, ev) for seq, ev in all_ev
            if ev.get("execution_id") == execution_id]


def reconstruct(trace_log, task_id, execution_id=None):
    """Rebuild the ordered cognitive pathway for a task execution
    from its trace events (Phase B). Returns a pathway dict:
      execution_id, task_id, domain, profile, spans (in order,
      each with function, namespace, parent, memory accesses,
      events, result, complete?), functions (the functions that
      actually executed, in first-invocation order),
      memory_blocks (the blocks accessed, in order),
      domain_transitions, failures, outcome, incomplete_spans,
      unexplained_jumps, classes (the five data classes, with
      their event counts), relationships (typed TOK/COIN-style
      edges between pathway elements), instrumentation_gaps.
    A span with FUNCTION_ENTER and no FUNCTION_EXIT is incomplete
    (a crash or an interrupted execution) and is flagged. A domain
    transition with no reason is an unexplained jump and is
    flagged. Reconstruction is an ANALYSIS of the ledger: it
    writes nothing, so multiple reconstructions of one ledger do
    not create multiple observations."""
    events = trace_events(trace_log, task_id, execution_id)
    spans, order, memory_blocks, transitions, failures = {}, [], [], [], []
    corrections = []
    outcome, domain, profile, execution = None, None, None, execution_id
    parent_execution_id, recording_mode, disabled_functions = None, None, []
    descriptions = "default"
    classes = {c: 0 for c in
               ("execution", "memory", "investigation", "domain", "outcome")}
    by_event_id = {}
    for seq, ev in events:
        by_event_id[ev["event_id"]] = ev
        classes[EVENT_CLASSES.get(ev["event_type"], "execution")] += 1
        event_type, span = ev["event_type"], ev["span_id"]
        execution = ev.get("execution_id")
        if event_type == "TASK_BEGIN":
            domain = ev["data"].get("domain")
            profile = ev["data"].get("profile")
            parent_execution_id = ev["data"].get("parent_execution_id")
            recording_mode = ev["data"].get("recording_mode")
            ctx = ev["data"].get("context", {})
            disabled_functions = ctx.get("disabled_functions", [])
            descriptions = ctx.get("descriptions", "default")
        elif event_type == "FUNCTION_ENTER":
            spans[span] = {"span_id": span, "function": ev["function_id"],
                           "namespace": ev["function_namespace"],
                           "parent": ev["parent_event_id"],
                           "purpose": ev["data"].get("purpose"),
                           "begin_seq": seq, "end_seq": None,
                           "result": None, "memory": [], "events": []}
            order.append(span)
        elif event_type == "FUNCTION_EXIT":
            if span in spans:
                spans[span]["end_seq"] = seq
                spans[span]["result"] = ev["data"].get("result")
        elif event_type.startswith("MEMORY_"):
            # TRACE-003 GO (JD): the record carries what the
            # recorder observed — no function field. The
            # function that made the access is derived by the
            # topology from the span/event linkage: the record
            # is the same object the enclosing span lists in
            # its "memory" (joined on seq/event_id). A record
            # no span claims is unattributed, not guessed.
            rec = {"seq": seq, "event_id": ev["event_id"],
                   "event_type": event_type,
                   "operation": ev["data"].get("store") and
                   event_type.replace("MEMORY_", "").lower(),
                   "store": ev["data"].get("store"),
                   "block": (ev.get("memory_refs") or [None])[0],
                   "bucket": ev["data"].get("bucket"),
                   "detail": ev["data"].get("detail")}
            memory_blocks.append(rec)
            if span in spans:
                spans[span]["memory"].append(rec)
        elif event_type == "DOMAIN_TRANSITION":
            transitions.append({"seq": seq, "span": span, **ev["data"]})
            if span in spans:
                spans[span]["events"].append({"seq": seq,
                                             "event_type": event_type,
                                             **ev["data"]})
        elif event_type == "CORRECTION":
            corrections.append({"seq": seq, "span": span, **ev["data"]})
            if span in spans:
                spans[span]["events"].append({"seq": seq,
                                             "event_type": event_type,
                                             **ev["data"]})
        elif event_type == "FAILURE_CLASS":
            failures.append({"seq": seq, "span": span, **ev["data"]})
        elif event_type == "TASK_END":
            outcome = ev["data"]
        elif span in spans:
            spans[span]["events"].append({"seq": seq,
                                         "event_type": event_type,
                                         **ev["data"]})
    incomplete = [s for s in order if spans[s]["end_seq"] is None]
    unexplained = [t for t in transitions if not t.get("reason")]
    functions = []
    for s in order:
        f = spans[s]["function"]
        if f is not None and f not in functions:
            functions.append(f)
    # Typed relationships (TOK/COIN-style): the edges of the
    # ordered pathway graph, derived from the event stream.
    # TRACE-002 correction, item 9: three kinds of edge, kept
    # strictly apart. Chronology is not causation.
    #   FOLLOWED_BY   — chronological adjacency only: span i+1
    #                   follows span i in sequence order. It
    #                   asserts NOTHING about cause.
    #   NESTED_IN     — structural containment: a span whose
    #                   parent_event_id is another span's
    #                   FUNCTION_ENTER is nested in that span.
    #                   (Formerly INVOKED_BY, which misnamed a
    #                   containment as an invocation.)
    #   TRANSITIONED_TO — an explicit domain transition, recorded
    #                   with its reason and evidence.
    #   CORRECTED_BY  — an explicit link: a failure corrected by
    #                   a correction event.
    #   CAUSED_BY     — the ONLY causal edge the trace asserts:
    #                   a CORRECTION is caused by the failed
    #                   attempt it demotes. The link is derived
    #                   from the correction's own recorded reason
    #                   and evidence (the procedure, the observed
    #                   failure, the validation), matched to the
    #                   execution span that ran that procedure and
    #                   observed the failure in this execution.
    #                   No other causal dependence is asserted:
    #                   a function appearing after another in the
    #                   sequence did not necessarily cause it.
    # Map each FUNCTION_ENTER event id to its span, so a span's
    # parent_event_id resolves to the enclosing span.
    enter_event_to_span = {}
    for seq, ev in events:
        if ev["event_type"] == "FUNCTION_ENTER":
            enter_event_to_span[ev["event_id"]] = ev["span_id"]
    relationships = []
    for i, s in enumerate(order):
        sp = spans[s]
        if sp["parent"] and sp["parent"] in enter_event_to_span:
            relationships.append({"from": s,
                                  "to": enter_event_to_span[sp["parent"]],
                                  "relation": "NESTED_IN"})
        if i:
            relationships.append({"from": order[i - 1], "to": s,
                                  "relation": "FOLLOWED_BY",
                                  "note": "chronological only, not causal"})
    for t in transitions:
        relationships.append({"from": t["span"],
                              "to": t.get("destination"),
                              "relation": "TRANSITIONED_TO"})
    for f in failures:
        if f.get("corrected_by"):
            relationships.append({"from": f["span"], "to": f["corrected_by"],
                                  "relation": "CORRECTED_BY"})
    for c in corrections:
        # The failed attempt that caused this correction: the
        # execution span in this execution that ran the same
        # procedure and observed a failure.
        proc = c.get("procedure")
        for s in order:
            sp = spans[s]
            if sp["function"] != "L15":
                continue
            for ev in sp["events"]:
                if (ev["event_type"] == "OBSERVATION"
                        and ev.get("procedure") == proc
                        and ev.get("observed") is False):
                    relationships.append(
                        {"from": c["span"], "to": s,
                         "relation": "CAUSED_BY",
                         "note": (f"correction of {proc} caused by "
                                  f"the failed attempt in span {s}")})
                    break
            else:
                continue
            break
    # Instrumentation gaps: what is NOT covered. Registered-only
    # functions (never invoked) and the internals the trace does
    # not reach (the Suit's internal admission checks, the
    # layer's internal analogy computation, the validator's
    # internal criteria comparison) are disclosed, not presumed.
    gaps = {"registered_never_invoked": sorted(
        set(FUNCTIONS) - set(functions) | set(REGISTERED_ONLY)),
            "uninstrumented_internals": [
                "Suit admission internals (admit() runs under its own U02 span; the per-check internals are not recorded separately)",
                "Layer analogy/applicability internals (select_with_policy is recorded as one U08/U09 pair, not per-comparison)",
                "Validator criteria comparison internals (recorded as one VALIDATION event per attempt)",
                "Big49/Small49 storage internals (block writes are recorded; the stores' internal hash-chain maintenance is not)"]}
    return {"execution_id": execution, "task_id": task_id,
            "domain": domain, "profile": profile,
            "parent_execution_id": parent_execution_id,
            "recording_mode": recording_mode,
            "disabled_functions": disabled_functions,
            "descriptions": descriptions,
            "spans": [spans[s] for s in order], "functions": functions,
            "function_states": function_states(
                functions, disabled_functions, descriptions),
            "memory_blocks": memory_blocks,
            "domain_transitions": transitions, "failures": failures,
            "corrections": corrections,
            "outcome": outcome, "incomplete_spans": incomplete,
            "unexplained_jumps": unexplained, "classes": classes,
            "relationships": relationships,
            "instrumentation_gaps": gaps}


def child_executions(trace_log, parent_execution_id):
    """The child executions of a parent execution (Correction E):
    every TASK_BEGIN whose parent_execution_id is the given id.
    Returns [{execution_id, task_id, sequence}] in ledger order.
    The linkage is read from the ledger, not from task names."""
    out = []
    for seq, _kind, task, _step, data in trace_log.events(
            kinds=[TRACE_KIND]):
        ev = data if isinstance(data, dict) else json.loads(data)
        if ev.get("event_type") != "TASK_BEGIN":
            continue
        if ev.get("data", {}).get("parent_execution_id") == parent_execution_id:
            out.append({"execution_id": ev["execution_id"],
                        "task_id": task, "sequence": seq})
    return out


def export_trace(trace_log, task_ids=None):
    """All TRACE_EVENT events, sorted globally by sequence (JD's
    review: the exported JSON was grouped by execution; a
    reconstructed view combining executions must sort by
    sequence when presenting the full timeline). Returns a list
    of event dicts with event_id/sequence derived from the
    ledger sequence.

    EXPORT BOUNDARY (TRACE-002 review, documented in TRACE-003):
    the export is a FILTER, not a copy of the ledger. It holds
    only rows whose kind is TRACE_EVENT (the cognitive trace
    schema events). The ledger also holds the pipeline/RealCog
    operational events — PROBLEM_ARRIVED, ACTION_INTENT,
    RECORDED, TASK_DONE, SYSTEM_OPEN — which are the underlying
    execution log, not cognitive trace events. In the TRACE-002
    demo the ledger reached sequence 991 while the export held
    857 events: the 134-event gap is exactly those operational
    rows (the export is contiguous in the TRACE_EVENT
    subsequence, not in the full ledger sequence). The
    operational rows are NOT cognitive events, but they ARE
    evidence: the completeness classification (ACTION_INTENT
    without TASK_DONE = AMBIGUOUS) reads them from the live
    ledger, never from the export. export_summary() and
    verify_export() make the filter explicit, so the excluded
    rows cannot silently disappear from completeness
    accounting."""
    out = []
    for seq, _kind, task, _step, data in trace_log.events(
            None, kinds=[TRACE_KIND]):
        if task_ids is not None and task not in task_ids:
            continue
        ev = data if isinstance(data, dict) else json.loads(data)
        ev = dict(ev)
        ev["event_id"] = f"ev-{seq:04d}"
        ev["sequence"] = seq
        out.append(ev)
    out.sort(key=lambda e: e["sequence"])
    return out


def export_summary(trace_log, task_ids=None):
    """The export filter, accounted for. Returns the total
    ledger events, the number exported (TRACE_EVENT rows,
    optionally restricted to task_ids), the per-kind counts of
    the excluded rows, and the exported sequence range. The
    excluded kinds are enumerated, never silently dropped."""
    total = trace_log.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    by_kind = dict(trace_log.db.execute(
        "SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall())
    exported = len(export_trace(trace_log, task_ids))
    excluded = {k: v for k, v in by_kind.items() if k != TRACE_KIND}
    seqs = [e["sequence"] for e in export_trace(trace_log, task_ids)]
    return {"total_ledger_events": total,
            "exported": exported,
            "exported_kind": TRACE_KIND,
            "excluded_by_kind": excluded,
            "excluded_total": sum(excluded.values()),
            "sequence_min": min(seqs) if seqs else None,
            "sequence_max": max(seqs) if seqs else None,
            "task_ids": task_ids}


def write_trace_export(trace_log, path, task_ids=None):
    """Write the sequence-sorted trace export to a JSON file,
    plus a manifest (path + ".manifest.json") recording the
    export filter (see export_summary). Returns the number of
    events written."""
    events = export_trace(trace_log, task_ids)
    with open(path, "w") as f:
        json.dump(events, f, indent=2, sort_keys=True)
    with open(path + ".manifest.json", "w") as f:
        json.dump(export_summary(trace_log, task_ids), f,
                  indent=2, sort_keys=True)
    return len(events)


def verify_export(trace_log, path, task_ids=None):
    """Re-read the ledger and check the export file against it:
    the file must be exactly the TRACE_EVENT subsequence (no
    missing, no extra, in sequence order), and the excluded
    operational kinds are reported. Returns {ok, exported,
    expected, missing, extra, excluded_by_kind, summary}.
    ok is False when the file disagrees with the ledger in any
    way — an export that silently lost events must not pass."""
    with open(path) as f:
        exported = json.load(f)
    expected = export_trace(trace_log, task_ids)
    exp_ids = [e["event_id"] for e in exported]
    want_ids = [e["event_id"] for e in expected]
    missing = [i for i in want_ids if i not in exp_ids]
    extra = [i for i in exp_ids if i not in want_ids]
    # The event bodies must match the ledger rows, not just
    # the ids (a swapped body with a right id is still wrong).
    by_id = {e["event_id"]: e for e in expected}
    mismatched = [i for i in exp_ids
                  if i in by_id and exported[exp_ids.index(i)] != by_id[i]]
    summary = export_summary(trace_log, task_ids)
    ok = (not missing and not extra and not mismatched
          and len(exported) == len(expected))
    return {"ok": ok, "exported": len(exported),
            "expected": len(expected), "missing": missing,
            "extra": extra, "mismatched": mismatched,
            "excluded_by_kind": summary["excluded_by_kind"],
            "summary": summary}


def export_full(trace_log, task_ids=None):
    """The FULL-FIDELITY export: every ledger row, every kind,
    in sequence order. Each entry carries the ledger sequence,
    the kind, the task, the step, the raw data, and the hash
    chain links (prev_hash, event_hash). This is the export
    that loses nothing — the filtered export (export_trace,
    TRACE_EVENT only) is a view over it, and the difference
    between the two is exactly the omitted rows (see
    omitted_events). Returns a list of row dicts."""
    out = []
    for seq, kind, task, step, data in trace_log.events(None):
        if task_ids is not None and task not in task_ids:
            continue
        row = trace_log.db.execute(
            "SELECT prev_hash, event_hash FROM events WHERE seq=?",
            (seq,)).fetchone()
        out.append({"sequence": seq, "kind": kind, "task": task,
                    "step": step,
                    "data": data if isinstance(data, dict) else json.loads(data),
                    "prev_hash": row[0] if row else None,
                    "event_hash": row[1] if row else None})
    out.sort(key=lambda e: e["sequence"])
    return out


def omitted_events(trace_log, task_ids=None):
    """The rows the filtered export (export_trace) OMITS,
    enumerated by actual sequence, kind and task — not inferred
    from position. These are the ledger rows whose kind is not
    TRACE_EVENT: the pipeline/RealCog operational events
    (PROBLEM_ARRIVED, ACTION_INTENT, RECORDED, TASK_DONE,
    IOU_OPENED) and the SYSTEM_OPEN metadata row. The
    operational rows carry no cognitive event_type (their data
    has no event_type field) — their kind IS their type.
    Returns a list of {sequence, kind, event_type, task, step,
    data} dicts, in sequence order."""
    full = export_full(trace_log, task_ids)
    exported_seqs = {e["sequence"] for e in export_trace(trace_log, task_ids)}
    omitted = []
    for row in full:
        if row["sequence"] in exported_seqs:
            continue
        data = row["data"]
        omitted.append({"sequence": row["sequence"], "kind": row["kind"],
                        "event_type": data.get("event_type")
                        if isinstance(data, dict) else None,
                        "task": row["task"], "step": row["step"],
                        "data": data})
    return omitted


def write_full_export(trace_log, path, task_ids=None):
    """Write the full-fidelity export (every ledger row) to a
    JSON file, plus a manifest (path + ".manifest.json")
    recording the counting convention and the per-kind counts.
    Returns the number of rows written."""
    rows = export_full(trace_log, task_ids)
    with open(path, "w") as f:
        json.dump(rows, f, indent=2, sort_keys=True)
    manifest = {"export": "full-fidelity",
                "filter": "none (every ledger row, every kind)",
                "counting": COUNTING_CONVENTION,
                "n_rows": len(rows),
                "by_kind": dict(Counter(r["kind"] for r in rows)),
                "sequence_min": rows[0]["sequence"] if rows else None,
                "sequence_max": rows[-1]["sequence"] if rows else None,
                "task_ids": task_ids}
    with open(path + ".manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    return len(rows)


def write_omitted_export(trace_log, path, task_ids=None):
    """Write the omitted-rows enumeration (omitted_events) to a
    JSON file, plus a manifest. Returns the number of omitted
    rows written."""
    rows = omitted_events(trace_log, task_ids)
    with open(path, "w") as f:
        json.dump(rows, f, indent=2, sort_keys=True)
    manifest = {"export": "omitted-rows enumeration",
                "filter": ("ledger rows whose kind is not "
                           f"{TRACE_KIND}"),
                "counting": COUNTING_CONVENTION,
                "n_rows": len(rows),
                "by_kind": dict(Counter(r["kind"] for r in rows)),
                "task_ids": task_ids}
    with open(path + ".manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    return len(rows)


# The counting convention (TRACE-002 correction, item 2).
# Stated once, used by every export, receipt and log claim:
#   - A ledger "event" is a stored row, sequence 1..N (the
#     seq column is a dense AUTOINCREMENT from 1). n_events is
#     the row count, and for a dense ledger n_events == head_seq.
#   - GENESIS is the chain origin: the prev_hash of sequence 1.
#     It is NOT a stored row and is NOT counted in n_events or
#     head_seq. Counting it as an event gives N+1, which is how
#     the "992" figure arose (991 rows + GENESIS).
#   - SYSTEM_OPEN is a stored row (sequence 1 in a fresh ledger)
#     and IS counted in n_events and in head_seq. It is metadata
#     (the pipeline's open marker), not an execution event, so
#     the omitted-events enumeration distinguishes it from the
#     operational rows.
#   - The filtered export holds only TRACE_EVENT rows. The
#     omitted rows are the operational kinds (PROBLEM_ARRIVED,
#     ACTION_INTENT, RECORDED, TASK_DONE, IOU_OPENED) plus the
#     SYSTEM_OPEN metadata row. In the TRACE-002 demo:
#     991 rows = 857 TRACE_EVENT + 134 omitted, and 134 = 133
#     operational + 1 SYSTEM_OPEN. That is the reconciliation of
#     "857 exported vs 992 ledger events vs 133 omitted": 992
#     counts GENESIS, 133 excludes SYSTEM_OPEN.
COUNTING_CONVENTION = {
    "event": "a stored ledger row, sequence 1..N (dense AUTOINCREMENT)",
    "n_events": "the row count; for a dense ledger n_events == head_seq",
    "genesis": ("the chain origin (prev_hash of sequence 1). NOT a "
                "stored row, NOT counted in n_events or head_seq. "
                "Counting it gives N+1."),
    "system_open": ("a stored row and counted in n_events/head_seq. "
                    "Metadata (the pipeline open marker), not an "
                    "execution event."),
    "filtered_export": f"TRACE_EVENT rows only (kind == {TRACE_KIND})",
    "omitted": ("operational kinds (PROBLEM_ARRIVED, ACTION_INTENT, "
                "RECORDED, TASK_DONE, IOU_OPENED) plus the SYSTEM_OPEN "
                "metadata row"),
}


def ledger_head(trace_log):
    """The ledger-head commitment (JD's review: the JSON files
    alone do not establish the hash chain). Verifies the chain
    first, then returns {head_seq, head_hash, n_events,
    verified: True}. Raises ValueError on a chain mismatch —
    an unverifiable ledger has no head to commit to.

    COUNTING (item 2): head_seq is the maximum stored sequence
    (a row, seq >= 1); n_events is the stored row count. GENESIS
    is not counted. SYSTEM_OPEN is counted (it is a row). For a
    dense ledger n_events == head_seq."""
    trace_log.verify()
    rows = trace_log.db.execute(
        "SELECT seq, event_hash FROM events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    n = trace_log.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return {"head_seq": rows[0] if rows else 0,
            "head_hash": rows[1] if rows else "GENESIS",
            "n_events": n, "verified": True,
            "counting": COUNTING_CONVENTION}


def write_receipt(trace_log, path):
    """Write an independently stored ledger-head receipt: the
    chain verification result plus the head (seq + hash) and the
    event count. The receipt lives OUTSIDE the ledger, so a
    later reopen can compare the ledger's head against it.

    COUNTING (item 2): the receipt commits to the stored rows
    1..head_seq (a PREFIX of the ledger). head_seq and n_events
    count stored rows only; GENESIS is not counted, SYSTEM_OPEN
    is. The receipt's n_events is the row count at write time;
    for a dense ledger it equals head_seq."""
    head = ledger_head(trace_log)
    receipt = {"schema": "soso-ledger-receipt/0.1", **head}
    with open(path, "w") as f:
        json.dump(receipt, f, indent=2, sort_keys=True)
    return receipt


def verify_receipt(trace_log, receipt_path):
    """Reopen the ledger, verify its chain, and compare it against
    an independently stored receipt. The receipt commits to a
    PREFIX of the ledger (the first head_seq events): opening the
    ledger appends a SYSTEM_OPEN event, so the current head moves
    on every open — comparing the current head to the receipt
    would fail on every reopen. The check is therefore that the
    chain verifies, the event at the receipt's head_seq carries
    the receipt's hash, and the ledger holds at least the
    receipt's event count. Returns {ok, head, receipt, mismatch}.
    ok is True only when the chain verifies AND the committed
    prefix is intact.

    LIMIT (item 8): this proves the committed PREFIX is intact,
    not that nothing was deleted after it. A truncation or
    deletion AFTER the receipt's head_seq passes this check —
    the receipt says nothing about the tail. A truncation or
    tampering WITHIN the prefix fails it (the chain breaks or
    the hash at head_seq changes). See the tests for prefix
    verification, truncation, tampering and appended events."""
    with open(receipt_path) as f:
        receipt = json.load(f)
    try:
        trace_log.verify()
    except ValueError as e:
        return {"ok": False, "head": None, "receipt": receipt,
                "mismatch": f"chain verification failed: {e}"}
    head_seq = receipt.get("head_seq")
    row = trace_log.db.execute(
        "SELECT event_hash FROM events WHERE seq=?", (head_seq,)
    ).fetchone()
    n = trace_log.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    mismatch = None
    if row is None:
        mismatch = f"no event at head_seq {head_seq} (ledger truncated?)"
    elif row[0] != receipt.get("head_hash"):
        mismatch = (f"hash at seq {head_seq}: {row[0]} != "
                    f"receipt {receipt.get('head_hash')}")
    elif n < receipt.get("n_events", 0):
        mismatch = (f"ledger has {n} events, receipt commits to "
                    f"{receipt.get('n_events')}")
    head = {"head_seq": head_seq, "head_hash": row[0] if row else None,
            "n_events": n, "verified": True,
            "counting": COUNTING_CONVENTION}
    return {"ok": mismatch is None, "head": head, "receipt": receipt,
            "mismatch": mismatch}


def certify(trace_log, task_id, execution_id=None):
    """Certify a trajectory (Correction C): the checks that must
    all pass before a trajectory is presented as complete
    evidence. Returns {certified, checks, gaps}. certified is
    True only when the hash chain verifies, the execution has
    exactly one TASK_BEGIN and one TASK_END, every FUNCTION_ENTER
    has a matching FUNCTION_EXIT, every parent_event_id resolves
    to an event in the execution, and the event ids/sequences
    are unique. PATHWAY-001 adds one check: every
    EVIDENCE_OBSERVED event must have a matching
    EVIDENCE_EXPECTATION event (same expectation_id) at a
    strictly lower sequence in the same execution — the
    expectation is frozen before the observation, so a
    prediction cannot be fabricated after the fact. An
    expectation WITHOUT an observation is NOT a gap (an
    unanswered request is honest). PATHWAY-002 adds one check:
    every EVIDENCE_EXPECTATION covered by an EVIDENCE_SELECTION
    (the selection selects its condition) must be preceded by
    that selection, at a strictly lower sequence in the same
    execution — the selection is frozen before any selected
    measurement is answered. An expectation with NO covering
    selection is legacy (PATHWAY-001) and is NOT a gap. A failed
    check is listed in gaps — never silently omitted."""
    checks, gaps = {}, []
    try:
        trace_log.verify()
        checks["chain_verifies"] = True
    except ValueError as e:
        checks["chain_verifies"] = False
        gaps.append(f"hash chain mismatch: {e}")
    events = trace_events(trace_log, task_id, execution_id)
    begins = [ev for _, ev in events if ev["event_type"] == "TASK_BEGIN"]
    ends = [ev for _, ev in events if ev["event_type"] == "TASK_END"]
    checks["single_begin"] = len(begins) == 1
    checks["single_end"] = len(ends) == 1
    if len(begins) != 1:
        gaps.append(f"{len(begins)} TASK_BEGIN events (expected 1)")
    if len(ends) != 1:
        gaps.append(f"{len(ends)} TASK_END events (expected 1)")
    enters = {ev["span_id"] for _, ev in events
              if ev["event_type"] == "FUNCTION_ENTER"}
    exits = {ev["span_id"] for _, ev in events
             if ev["event_type"] == "FUNCTION_EXIT"}
    incomplete = sorted(enters - exits)
    checks["all_spans_closed"] = not incomplete
    if incomplete:
        gaps.append(f"incomplete spans: {incomplete}")
    ids = {ev["event_id"] for _, ev in events}
    checks["unique_event_ids"] = len(ids) == len(events)
    if len(ids) != len(events):
        gaps.append("duplicate event ids")
    seqs = [seq for seq, _ in events]
    checks["unique_sequences"] = len(set(seqs)) == len(seqs)
    if len(set(seqs)) != len(seqs):
        gaps.append("duplicate sequences")
    known = ids | {None}
    dangling = sorted({ev["parent_event_id"] for _, ev in events
                       if ev.get("parent_event_id") not in known})
    checks["parents_resolve"] = not dangling
    if dangling:
        gaps.append(f"dangling parent_event_ids: {dangling}")
    # PATHWAY-001: every EVIDENCE_OBSERVED must be preceded by
    # an EVIDENCE_EXPECTATION with the same expectation_id, at a
    # strictly lower sequence in this execution. The expectation
    # event's id is the id derived from its sequence (the stored
    # copy carries "ev-pending"; the sequence is authoritative),
    # which is the id the observation records. An expectation
    # without an observation is honest (unanswered) and is not
    # a gap; a missing or later expectation is.
    expectations = {ev["event_id"]: seq for seq, ev in events
                    if ev["event_type"] == "EVIDENCE_EXPECTATION"}
    orphaned = []
    for seq, ev in events:
        if ev["event_type"] != "EVIDENCE_OBSERVED":
            continue
        exp_id = (ev.get("data") or {}).get("expectation_id")
        if exp_id not in expectations or expectations[exp_id] >= seq:
            orphaned.append(ev["event_id"])
    orphaned = sorted(orphaned)
    checks["evidence_expectations_precede_observations"] = not orphaned
    if orphaned:
        gaps.append("evidence observations without a preceding "
                    f"expectation: {orphaned}")
    # PATHWAY-002: every EVIDENCE_EXPECTATION covered by an
    # EVIDENCE_SELECTION (the selection selects its condition)
    # must be preceded by that selection, at a strictly lower
    # sequence in this execution. An expectation with no
    # covering selection is legacy (PATHWAY-001) and is not a
    # gap; a selection that comes after the expectation it
    # covers is.
    selections = []
    for seq, ev in events:
        if ev["event_type"] != "EVIDENCE_SELECTION":
            continue
        selected = (ev.get("data") or {}).get("selected") or []
        selections.append((seq, {s.get("condition") for s in selected
                                 if isinstance(s, dict)}))
    uncovered = []
    for seq, ev in events:
        if ev["event_type"] != "EVIDENCE_EXPECTATION":
            continue
        condition = (ev.get("data") or {}).get("condition")
        covering = [s for s, conds in selections if condition in conds]
        if covering and not any(s < seq for s in covering):
            uncovered.append(ev["event_id"])
    uncovered = sorted(uncovered)
    checks["evidence_selections_precede_expectations"] = not uncovered
    if uncovered:
        gaps.append("evidence expectations covered by an "
                    "EVIDENCE_SELECTION but not preceded by it: "
                    f"{uncovered}")
    return {"certified": not gaps, "checks": checks, "gaps": gaps}


def coverage(pathways):
    """Memory-block coverage across reconstructed pathways: the
    distinct blocks actually accessed, by store and bucket,
    against the 2,208 logical blocks. Honest by construction —
    it counts what was accessed, never what was not."""
    seen = {}
    for p in pathways:
        for m in p["memory_blocks"]:
            key = (m["store"], m["bucket"], m["block"])
            seen.setdefault(key, {"store": m["store"], "bucket": m["bucket"],
                                  "block": m["block"], "operations": set()})
            seen[key]["operations"].add(m["operation"])
    by_store = {}
    for (store, bucket, _block), rec in seen.items():
        by_store.setdefault(store, {}).setdefault(bucket, 0)
        by_store[store][bucket] += 1
    total = sum(by_store[s][b] for s in by_store for b in by_store[s])
    return {"distinct_blocks": total,
            "of_logical": TOTAL_LOGICAL_BLOCKS,
            "fraction": total / TOTAL_LOGICAL_BLOCKS if TOTAL_LOGICAL_BLOCKS else 0,
            "by_store": by_store,
            "blocks": [{**r, "operations": sorted(r["operations"])}
                       for r in seen.values()]}


def analyze(pathways):
    """Pathway statistics across reconstructed pathways (directive
    §6). Returns frequencies, co-activation, common sequences,
    memory usage by operation, successful vs unsuccessful
    pathways, domain differences, bottlenecks, functions with no
    measurable benefit, and novice-vs-experienced differences.
    This is measurement, not activation: it recommends nothing
    automatically. A proposed configuration must be tested
    against controls before adoption."""
    from collections import Counter
    freq = Counter()
    coact = Counter()
    sequences = Counter()
    mem_by_op = Counter()
    success_paths, fail_paths = [], []
    by_domain = {}
    by_profile = {}
    for p in pathways:
        for f in p["functions"]:
            freq[f] += 1
        for i, a in enumerate(p["functions"]):
            for b in p["functions"][i + 1:]:
                coact[(a, b)] += 1
        sequences[tuple(p["functions"])] += 1
        for m in p["memory_blocks"]:
            mem_by_op[(m["store"], m["operation"])] += 1
        outcome = (p["outcome"] or {}).get("outcome")
        (success_paths if outcome == "RESOLVED" else fail_paths).append(p)
        by_domain.setdefault(p["domain"], []).append(p)
        by_profile.setdefault(p["profile"], []).append(p)
    fail_freq = Counter(f for p in fail_paths for f in p["functions"])
    ok_freq = Counter(f for p in success_paths for f in p["functions"])
    bottlenecks = []
    for f in sorted(set(fail_freq) | set(ok_freq)):
        if fail_freq[f] and ok_freq[f] == 0:
            bottlenecks.append((f, fail_freq[f], 0))
    no_benefit = sorted(
        set(FUNCTIONS) - set(freq) |
        {f for f in freq if ok_freq[f] == 0 and fail_freq[f] > 0})
    return {"n_pathways": len(pathways),
            "function_frequency": dict(freq),
            "co_activation": {f"{a}+{b}": n for (a, b), n in coact.items()},
            "common_sequences": {" > ".join(k): v
                                 for k, v in sequences.most_common(10)},
            "memory_by_operation": {f"{s}:{o}": n
                                    for (s, o), n in mem_by_op.items()},
            "successful": len(success_paths), "unsuccessful": len(fail_paths),
            "by_domain": {d: len(v) for d, v in by_domain.items()},
            "by_profile": {p: len(v) for p, v in by_profile.items()},
            "bottlenecks": bottlenecks,
            "no_measurable_benefit": no_benefit,
            "coverage": coverage(pathways)}
