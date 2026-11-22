"""Integration Contract 001 — the cognitive pipeline.

Lower-first, over the shared task record and the single durable
experience record. Stages:
  1. Intake + validation + task-identity check (complete
     identity-bearing fields, not only the signature).
  2. Lower — exact qualified reuse from the exact-signature
     projection (QUALIFIED records only).
  3. SoSo — transfer and recognition (applicability -> analogy ->
     description overlap -> mismatch checks), from the
     transferable projection. The layer proposes; the pipeline
     admits. Policy is enforced here, at admission — not inside
     the layer's suit filter (the layer is called with suit=None,
     as its own docstring allows).
  4. Higher — bounded trial, ONLY for procedures that are
     trial-SAFE, policy-allowed, and whose hard preconditions are
     known and satisfied. Unknown hard preconditions produce an
     information request, never a trial. A trial success is stored
     as CANDIDATE — never auto-qualified, never in the trusted
     index — until it passes transfer qualification.
  5. Unresolved -> durable IOU (RealCog ledger, opened BEFORE
     the task is marked done) + escalation.

Candidate lists are deduplicated before execution. Every rejected
candidate is preserved with its reason. No actual external side
effect is performed merely because a procedure is present in a
trial list: the harness executor is synthetic and frozen, and in
a real deployment the trial stage's SAFE classification is the
authorization boundary (see registry.py).

Crash safety: the WriterLock is held for the whole run; each
experience is one atomic Big49 put; projections are rebuilt from
Big49 on every run, so a crash between a write and a rebuild
cannot create conflicting projections.
"""
from __future__ import annotations

import dataclasses
import os

from contextlib import contextmanager

from . import profiles as profiles_mod
from . import task as task_mod
from .admission import Policy, Validator, admit
from .experience import ExperienceStore, record_id
from .registry import (REGISTRY, TRIAL_SAFE, by_native,
                       check_hard_preconditions, get as reg_get)
from .cognitive_recorder import CognitiveRecorder
from .seven_domain_cognition import evaluate_seven_domain_task
from .visual_evidence_adapter import ingest_dual_stream
from .disposition_engine import (
    HoldStore,
    deliver_evidence_and_reactivate,
    evaluate_motor_branches,
    explain_disposition_pathway,
    propagate_authorized_results,
)
from .trace import Tracer

DEFAULT_DISPOSITION_EDGES = {
    ("U09", "U37_MECH"),
    ("U09", "L24_THERMAL"),
    ("U09", "U10_ELECTRICAL"),
    ("U10", "U37_ELECTRICAL"),
}

MAX_TRIAL_ATTEMPTS = 10
PRINCIPAL = "L30"


def _desc_words(description):
    """Mirror of cognitive_layer._desc_words: words longer than 2
    characters, lowercased, punctuation stripped."""
    return {w.strip(",.;") for w in (description or "").lower().split()
            if len(w.strip(",.;")) > 2}


def _trial_rank(entry, task):
    """Relevance rank for the trial list (Contract 002): registered
    task-family relevance first, then shared description words,
    then soft-precondition matches, then registry order (the
    caller's iteration order is the registry order, so a stable
    sort preserves it for ties). The family is a RANK, never a
    filter — cross-family candidates stay eligible; they simply
    rank after the procedures registered for this task's family."""
    family = 1 if task.family in entry.families else 0
    shared = len(_desc_words(entry.description) & set(task.tags))
    soft = sum(1 for name, (value, hard) in entry.preconditions.items()
               if not hard and (task.conditions.get(name) == value
                                or value in task.tags))
    return (-family, -shared, -soft)


def _rank_components(entry, task):
    """The score components behind _trial_rank, for the trace
    (TRACE-002 correction, item 6): a candidate's rank is not a
    bare number — it is the family relevance, the shared
    description words and the soft-precondition matches that
    produced it. Returns {family, shared, soft} (each a count;
    higher is better; _trial_rank negates them for the sort)."""
    return {"family": 1 if task.family in entry.families else 0,
            "shared": len(_desc_words(entry.description) & set(task.tags)),
            "soft": sum(1 for name, (value, hard) in entry.preconditions.items()
                        if not hard and (task.conditions.get(name) == value
                                         or value in task.tags))}


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _condition_status(entry, task, demonstrated):
    """Tri-state demonstrated-condition check (HARDEN-001 fix 1).
    Returns (status, reason) with status in:
      "satisfied"    — every task condition the procedure is
                       concerned with matches its demonstrated value
                       (or the task presents no such condition).
      "unknown"      — the task presents a condition the procedure
                       is concerned with (a registry precondition)
                       but the procedure was never demonstrated
                       with it. Missing evidence is NOT silently
                       treated as satisfied.
      "contradicted" — a task condition explicitly differs from the
                       demonstrated value.
    A task condition is "concerned with" when its name is a
    registry precondition of the procedure or appears in the
    demonstrated conditions. Any other task condition is
    irrelevant to this procedure and is ignored (so a slope task
    carrying an unrelated condition does not block a valid
    cross-family transfer)."""
    demonstrated = demonstrated or {}
    relevant = {name for name in entry.preconditions} | set(demonstrated)
    for name, value in task.conditions.items():
        if name not in relevant:
            continue
        if name in demonstrated and demonstrated[name] != value:
            return ("contradicted",
                    f"demonstrated {name}={demonstrated[name]!r} "
                    f"contradicts task {name}={value!r}")
        if name not in demonstrated:
            return ("unknown",
                    f"no demonstrated value for {name} "
                    f"(task has {name}={value!r})")
    return ("satisfied", "")


def _conditions_compatible(demonstrated: dict, task) -> bool:
    """A demonstrated condition contradicts the task only when the
    task KNOWS a different value. Unknown does not contradict
    (SoSo's applicability rule). Kept for callers that only need
    the contradiction test; the tri-state _condition_status is
    the enforcement point."""
    for name, value in (demonstrated or {}).items():
        if name in task.conditions and task.conditions[name] != value:
            return False
    return True


def _tags_overlap(record_tags, task_tags) -> bool:
    """HARDEN-001 fix 1: the exact-index tag gate. The task must
    share at least one tag with the demonstration record's tags.
    A procedure qualified for sensor work must not be served from
    the exact index to a pump-tagged task on signature alone."""
    return bool(set(record_tags or []) & set(task_tags or []))


def _native_of(procedure_id: str) -> str:
    return reg_get(procedure_id).native_id


def _registered_of(native_id: str) -> str:
    return by_native(native_id).procedure_id


class Pipeline:
    def __init__(self, root, policy: Policy, criteria=None,
                 observations=None, soso_modules=None,
                 iou_policy="TERMINAL", pre_execute_hook=None,
                 post_intent_hook=None, descriptions="default",
                 profile="general", disabled_functions=frozenset(),
                 fault_hook=None, recording_mode="best_effort",
                 trial_ranker="existing", graph_scores=None,
                 evidence_source=None, pathway_provider=None,
                 measurement_selector=None, measurement_costs=None,
                 replay_contradiction_abstention=False,
                 mac002_adapter=None):
        # TRACE-002 — ablation (JD's directive, Priority 2):
        # disabled_functions is the set of 49-Brain functions
        # deliberately disabled for a controlled ablation
        # experiment (e.g. {"L24"}). A disabled function never
        # emits a span; the stage that needs it records a
        # FUNCTION_DISABLED event and falls through to the next
        # stage. This is NOT a silent omission: the trace says
        # the function was disabled, so a missing span is
        # attributable to the experiment. The three ablation
        # levels (Correction B) are distinct: disabling a
        # FUNCTION (the pipeline ignores its output), disabling
        # an INFORMATION SOURCE (descriptions=None — the layer
        # cannot compute cross-task candidates), and disabling a
        # STRATEGY (U08 — the whole transfer stage). They are
        # measured separately (contract/trace_eval.py).
        self.disabled_functions = frozenset(disabled_functions or ())
        # TRACE-002 — fault-injection seam for the trace
        # completeness test: fault_hook(boundary, task, entry)
        # is called at named boundaries ("after_decide" — after
        # a decision span begins, before admission; "after_
        # validation" — after the validation, before the record
        # is committed; "after_record" — after the record is
        # committed, before the task ends). A test hooks it to
        # crash (SIGKILL) at one boundary and then reconstructs
        # the interrupted execution.
        self.fault_hook = fault_hook
        # TRACE-002 correction, item 10: the trial ranker.
        # "existing" is the Contract 002 ranker (registered
        # family relevance, shared description words,
        # soft-precondition matches, registry order for ties).
        # "revised" adds the learned experience to the rank: a
        # procedure with a demonstrated SUCCESS in this task's
        # family (a QUALIFIED/UNDER_TEST record for a task in
        # the family) ranks before one without, and a procedure
        # with a demonstrated FAILURE in the family (an
        # INCONCLUSIVE/REJECTED record) ranks after. The revised
        # ranker is opt-in — the baseline keeps "existing", so
        # the 93-test suite is unaffected.
        self.trial_ranker = trial_ranker
        # GRAPH-001 (FlyWire/COIN/TRACE-003 integration): the
        # graph scores table, keyed by registered procedure id
        # (e.g. "SOSO-P7"). It is consulted ONLY by the "graph"
        # ranker, as a tie-breaker after the base (family,
        # shared, soft) rank. The table is computed by the graph
        # adapter from the training graph (contract/graph.py)
        # and is frozen for the run — the graph advises the
        # ranker; it never writes experience, qualifies, or
        # executes. None (the default) means no graph advice.
        self.graph_scores = dict(graph_scores or {})
        # PATHWAY-001: the answered-evidence-request machinery.
        # evidence_source answers one named discriminator per
        # task (contract/evidence.py); pathway_provider is
        # duck-typed and offers discriminators_for(task) ->
        # list[str] (built in contract/pathway.py by a separate
        # agent — pipeline.py never imports it). Both default to
        # None, which is current behavior: runs that do not opt
        # in produce byte-for-byte identical reports/traces.
        self.evidence_source = evidence_source
        self.pathway_provider = pathway_provider
        # PATHWAY-002 (opt-in only; defaults preserve PATHWAY-001
        # behavior verbatim):
        # - measurement_selector: a MeasurementSelector instance.
        #   When set, stage 4 uses it for BOTH the direct U10
        #   path and the replay path (unified selection): the
        #   replay provider's discriminators and the retrieved
        #   method record become selector inputs; the ranked
        #   selection is frozen (EVIDENCE_SELECTION) and then
        #   answered in order via _answer_condition. When None:
        #   current behavior verbatim.
        # - measurement_costs: the cost mapping the selector was
        #   built with, kept for the record.
        # - replay_contradiction_abstention: when True and the
        #   escalate_because branch is reached after answered
        #   replay evidence that contradicts every pointed-at
        #   procedure, the run records the justified-abstention
        #   disposition exactly as the direct U10 path does.
        #   When False (default): current behavior verbatim.
        self.measurement_selector = measurement_selector
        self.measurement_costs = measurement_costs
        self.replay_contradiction_abstention = \
            replay_contradiction_abstention
        self.mac002_adapter = mac002_adapter
        self._selection_counter = 0
        # TRACE-002, Correction C: the default recording mode
        # for runs (best_effort or evidence_grade). A single
        # run() call can override it.
        self.recording_mode = recording_mode
        # TRACE-001: the cognitive profile is an advisory strategy
        # (contract/profiles.py). It gates the trial stage, the
        # description transfer and the under-test proposals — and
        # the trace records which profile was active, so the
        # difference is proven from the pathway, not assumed
        # from the name. The pipeline's explicit descriptions
        # parameter (the EVAL-001 trained control) takes
        # precedence over the profile's when it is not "default".
        self.profile = profiles_mod.get(profile)
        self._profile_id = profile
        import sys
        from pathlib import Path
        _rc_dir = str(Path(__file__).resolve().parent.parent / "realcog")
        if _rc_dir not in sys.path:
            sys.path.insert(0, _rc_dir)
        from ph6_brains_2208 import Big49, Block, Rejected
        from ph6_iouledger import IOULedger
        from ph6_realbridge import pointer_for, rid
        from ph6_trace import TraceLog
        from ph6_writerlock import WriterLock
        self._Big49, self._Block, self._Rejected = Big49, Block, Rejected
        self._pointer_for, self._rid = pointer_for, rid
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.lock = WriterLock(root)
        self.big = self.ious = self.trace = None
        try:
            self.big = Big49(os.path.join(root, "big.db"))
            self.ious = IOULedger(os.path.join(root, "iou.db"), iou_policy)
            self.trace = TraceLog(os.path.join(root, "trace.db"))
        except BaseException:
            for _conn in (self.trace, self.ious, self.big):
                if _conn is not None:
                    try:
                        _conn.close()
                    except Exception:
                        pass
            self.lock.release()
            raise
        self.experiences = ExperienceStore(self.big)
        self.policy = policy
        self.validator = Validator(criteria)
        self.observations = dict(observations or {})
        self.soso = soso_modules or {}
        self._cl = self.soso.get("cognitive_layer")
        # Contract 002 — called between admission and execution.
        # It may change external state (a test uses it to flip the
        # policy mid-run); execution revalidates after it.
        self.pre_execute_hook = pre_execute_hook
        # Contract 002 — fault-injection seam, called after the
        # ACTION_INTENT trace and before the observation is read.
        # RealCog has the same pattern (System04's fault_hook with
        # "before_action"/"after_action"). A crash here leaves an
        # intent without a result: the ambiguous case.
        self.post_intent_hook = post_intent_hook
        # EVAL-001 — the trained control sets descriptions=None to
        # disable description-based transfer (applicability, analogy
        # and the under-test check still run). "default" means the
        # full PROCEDURE_DESCRIPTIONS (Contract 001/002 behavior).
        # TRACE-001: the profile's descriptions override the
        # pipeline's when the pipeline's is the default (the
        # medical profile turns description transfer off). An
        # explicit non-default descriptions value (the EVAL-001
        # trained control) keeps precedence, including across a
        # later set_profile.
        self._explicit_descriptions = (descriptions
                                       if descriptions != "default" else None)
        self.descriptions = (descriptions if descriptions != "default"
                             else self.profile["descriptions"])
        self._tracer = None
        self.disposition_store_path = os.path.join(root, "disposition_holds.json")
        self.trace.append("SYSTEM_OPEN", None, None,
                          {"policy": policy.version, "iou_policy": iou_policy})

    def _disabled(self, function_id):
        """Is this function disabled for the ablation experiment?"""
        return function_id in self.disabled_functions

    def _fault(self, boundary, task, entry):
        """Call the fault-injection hook at a named boundary
        (TRACE-002 completeness test). A no-op when no hook is
        set — the production path is unchanged."""
        if self.fault_hook is not None:
            self.fault_hook(boundary, task, entry)

    def _disable_event(self, function_id, span_id=None, reason=None):
        """Record that a function was disabled (ablation) on the
        given span (the root span when None). A no-op when no
        tracer is active."""
        if self._tracer is not None:
            self._tracer.disabled(function_id, span_id=span_id or "root",
                                  reason=reason)

    def _span_result(self, span_id, function_id, data):
        """Record a function's input/output summary on its own
        span (a FUNCTION_RESULT event carrying the function id
        and the summary: stable references, digests and counts,
        never unrestricted memory contents). A no-op when no
        tracer is active or the span is None (no tracer). The
        span's FUNCTION_ENTER purpose carries the input summary;
        this event carries the computed output. FUNCTION_RESULT
        — never OBSERVATION: OBSERVATION is the per-attempt
        execution observation, paired 1:1 with VALIDATION by
        the PathwayRecorder and read as ATTEMPTED edges by the
        graph adapter; a span-result record under OBSERVATION
        would read as a phantom attempt."""
        if self._tracer is not None and span_id is not None:
            self._tracer.event("FUNCTION_RESULT", span_id,
                               {"function": function_id, **data})

    def _admit(self, entry, task, span):
        """U02 — Authorize Execution (GOVERNANCE, not a brain
        function): the Suit admission computation — admit()
        (Policy.decision + check_hard_preconditions) — under its
        own span, parented on the caller's span. The ADMISSION
        event the caller records on its U37 decision span is
        unchanged; this span is the authorization computation
        itself. Disabled (test-only ablation): skip admission
        and treat as admitted (True, "admitted", "disabled:
        U02"), recording a FUNCTION_DISABLED event. That
        demonstrates the function's contribution; it does not
        weaken the live pipeline (ablation is test-only, via
        set_disabled_functions)."""
        if self._disabled("U02"):
            self._disable_event("U02", span,
                                reason="ablation: U02 disabled")
            return True, "admitted", "disabled: U02"
        with self._trace_span(
                "U02", span or "root",
                f"authorize {entry.procedure_id} "
                f"(policy {self.policy.version})") as u02:
            ok, status, detail = admit(entry, task, self.policy)
            self._span_result(u02, "U02", {
                "procedure": entry.procedure_id, "admitted": ok,
                "status": status, "detail": detail})
            return ok, status, detail

    def _condition_check(self, entry, task, demonstrated, span):
        """U01 — Evaluate Demonstrated Conditions: the tri-state
        _condition_status check (satisfied / unknown /
        contradicted) under its own span, parented on the
        caller's span. `demonstrated` is the procedure's
        demonstrated conditions for this task (from the record
        or from _demonstrated_conditions — the lookup is the
        check's input, computed by the caller). Disabled
        (ablation): skip the check and treat the status as
        "satisfied", recording a FUNCTION_DISABLED event."""
        if self._disabled("U01"):
            self._disable_event("U01", span,
                                reason="ablation: U01 disabled")
            return "satisfied", ""
        with self._trace_span(
                "U01", span or "root",
                f"evaluate demonstrated conditions for "
                f"{entry.procedure_id}") as u01:
            status, reason = _condition_status(entry, task, demonstrated)
            self._span_result(u01, "U01", {
                "procedure": entry.procedure_id, "status": status,
                "reason": reason,
                "demonstrated": dict(demonstrated or {})})
            return status, reason

    def _trial_candidates_eligible(self, task):
        """The U04-disabled trial list: the eligible procedures
        only, ranked exactly as the normal path ranks them —
        no pointed-at gating, no ask branch, no escalate
        branch. Same dict shape as _trial_candidates
        (candidates ranked, blocked_by_unknown None,
        escalate_because None, pointed_blocks [])."""
        candidates = []
        for entry in REGISTRY.values():
            if not self.policy.decision(entry.procedure_id):
                continue
            if entry.trial_safety != TRIAL_SAFE:
                continue
            if check_hard_preconditions(
                    entry, task.conditions, task.unknown_conditions,
                    task.contradictory_conditions) != "satisfied":
                continue
            candidates.append(entry)
        candidates = sorted(
            candidates, key=lambda e: self._trial_rank_key(e, task))
        return {"candidates": candidates, "blocked_by_unknown": None,
                "escalate_because": None, "pointed_blocks": []}

    def _trial_candidates_spanned(self, task, parent_span):
        """U04 — Gate Trial Candidates: _trial_candidates
        (pointed-at detection, the unknown-hard ask branch, the
        every-pointed-blocked escalate branch, else the ranked
        eligible list) under its own span, parented on the
        enclosing span (the U09 trial span, or the U10 span on
        the recompute after an answered request). Disabled
        (ablation): skip the pointed-at gating and return the
        eligible-only list (see _trial_candidates_eligible),
        recording a FUNCTION_DISABLED event."""
        if self._disabled("U04"):
            self._disable_event("U04", parent_span,
                                reason="ablation: U04 disabled")
            return self._trial_candidates_eligible(task)
        with self._trace_span(
                "U04", parent_span or "root",
                f"gate trial candidates for {task.task_id} "
                f"(signature {task.signature})") as u04:
            result = self._trial_candidates(task)
            self._span_result(u04, "U04", {
                "candidates": [e.procedure_id
                               for e in result["candidates"]],
                "blocked_by_unknown":
                    (result["blocked_by_unknown"] or {}).get("procedures")
                    if result["blocked_by_unknown"] else None,
                "escalate_because": result["escalate_because"],
                "pointed_blocks": result["pointed_blocks"]})
            return result

    @contextmanager
    def _l02_span(self, parent_span, input_digest):
        """L02 — Learn Applicability: the span factory passed to
        the cognitive layer (via select_with_policy ->
        recognise / cross_task_candidates ->
        learned_applicability). The layer calls the factory as
        span_emitter(input_digest); the yielded emit(output)
        records the learned applicability on the span. The span
        brackets the layer's own computation — it is emitted
        from inside learned_applicability, never from the
        pipeline around the U08 call (that would misattribute
        U08's work to L02). A no-op span when no tracer is
        active."""
        purpose = (f"learn applicability: tags="
                   f"{input_digest.get('tags')}")
        with self._trace_span("L02", parent_span or "root",
                              purpose) as l02:
            def _emit(output):
                self._span_result(l02, "L02", {
                    "input": input_digest, "output": output})
            yield _emit

    @contextmanager
    def _trace_span(self, function_id, parent_span_id="root", purpose=None):
        """A cognitive-function span (TRACE-001). Yields the span
        id, or None when no tracer is active (e.g. outside run()).
        The span is ended on exit with result "ok" (or "error" when
        an exception propagates); the detailed outcome is recorded
        by the OBSERVATION / VALIDATION events on the span."""
        tracer = self._tracer
        if tracer is None:
            yield None
            return
        span = tracer.begin_span(function_id, parent_span_id, purpose)
        try:
            yield span
            tracer.end_span(span, result="ok")
        except BaseException:
            tracer.end_span(span, result="error")
            raise

    def set_descriptions(self, descriptions):
        """Apply a new description set (TRACE-002, Correction B
        level b — the information source). The change is recorded
        in the hash-chained trace. Setting None removes the
        description transfer (the layer cannot compute cross-task
        candidates); "default" restores the full set. This is the
        information-source ablation, distinct from disabling the
        L24 function (which ignores the candidates the layer
        computed) and from disabling U08 (the whole strategy)."""
        old = self.descriptions
        self.descriptions = descriptions
        self._explicit_descriptions = (descriptions
                                       if descriptions != "default" else None)
        self.trace.append("DESCRIPTIONS_CHANGE", None, None,
                          {"from": str(old)[:40],
                           "to": str(descriptions)[:40]})
        return old

    def set_disabled_functions(self, functions):
        """Apply a new ablation set (TRACE-002). The change is
        recorded in the hash-chained trace with the old and new
        sets, so every later stage decision is attributable to a
        specific ablation. Returns the previous set. Training
        (which needs the transfer stage to qualify procedures)
        happens with no functions disabled; the ablation is
        applied only for the test tasks."""
        old = self.disabled_functions
        self.disabled_functions = frozenset(functions or ())
        self.trace.append("DISABLED_FUNCTIONS_CHANGE", None, None,
                          {"from": sorted(old),
                           "to": sorted(self.disabled_functions)})
        return old

    def set_profile(self, profile_id: str):
        """Apply a new cognitive profile. The change is recorded
        in the hash-chained trace with the old and new profile
        ids, so every later stage decision is attributable to a
        specific profile. Returns the previous profile id. The
        effective descriptions follow the new profile (unless the
        pipeline was constructed with an explicit non-default
        descriptions value, which keeps precedence)."""
        old = self._profile_id
        self.profile = profiles_mod.get(profile_id)
        self._profile_id = profile_id
        if self._explicit_descriptions is None:
            self.descriptions = self.profile["descriptions"]
        self.trace.append("PROFILE_CHANGE", None, None,
                          {"from": old, "to": profile_id})
        return old

    def set_policy(self, new_policy: Policy):
        """Apply a new policy version. The change is recorded in
        the hash-chained trace with the old and new versions, so
        every later admission is attributable to a specific
        policy version. Returns the previous policy."""
        old = self.policy
        self.policy = new_policy
        self.trace.append("POLICY_CHANGE", None, None,
                          {"from": old.version, "to": new_policy.version,
                           "forbidden": sorted(new_policy.forbidden)})
        return old

    def close(self):
        for c in (self.ious, self.trace, self.big):
            try:
                c.close()
            except Exception:
                pass
        self.lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---- recovery (mirrors RealCog System04.recover) ----
    def recover(self):
        tasks = set()
        for _, kind, task, _, _ in self.trace.events():
            if task:
                tasks.add(task)
        running = []
        for task in sorted(tasks):
            kinds = [k for _, k, t, _, _ in self.trace.events(task)]
            if "PROBLEM_ARRIVED" in kinds and "TASK_DONE" not in kinds:
                running.append(task)
        amb = []
        for task in running:
            last = {}
            for _, kind, _, step, _ in self.trace.events(
                    task, ("ACTION_INTENT", "RECORDED")):
                last[step] = kind
            amb += [[task, s] for s, k in last.items() if k == "ACTION_INTENT"]
        st = self.ious.state()
        counts = {}
        for v in st["ious"].values():
            counts[v[0]] = counts.get(v[0], 0) + 1
        rep = {"tasks_indexed": len(tasks), "running": running,
               "ambiguous": amb, "iou_counts": counts,
               "iou_complete": st["complete"]}
        self.trace.append("RECOVERY", None, None, rep)
        return rep

    # ---- task identity ----
    def _check_identity(self, task, span_id=None):
        """Write-once identity block in Big49. Returns None when
        the identity is new or matches; returns the mismatch
        description when the same task_id arrives with different
        identity-bearing fields. TRACE-001: the identity block
        access (write when new, read when it exists) is recorded
        on the given span."""
        rid = f"task.{task.task_id}"
        try:
            ptr = self._pointer_for(self.big, rid, PRINCIPAL)
            stored = self.big.read(ptr, principal=PRINCIPAL)
            stored_digest = __import__("json").loads(stored["content"]).get("digest")
        except self._Rejected:
            stored_digest = None
        digest = task.identity_digest()
        if stored_digest is not None and stored_digest != digest:
            return (f"task {task.task_id} identity mismatch: stored "
                    f"{stored_digest} != {digest}")
        if stored_digest is None:
            content = task_mod.canon({**task.identity(), "digest": digest})
            self.big.put(self._Block(rid, PRINCIPAL, "cognitive", content,
                                     graph="G_S", relation="ASSOCIATED_WITH",
                                     source_refs=(f"int:{task.task_id}",)),
                         principal=PRINCIPAL)
            if self._tracer is not None and span_id is not None:
                self._tracer.memory(span_id, "write", "big49", rid,
                                    bucket="cognitive",
                                    detail="identity block (first run)")
        elif self._tracer is not None and span_id is not None:
            self._tracer.memory(span_id, "read", "big49", rid,
                                bucket="cognitive",
                                detail="identity block (existing)")
        return None

    # ---- execution (frozen, synthetic) ----
    def _execute(self, task, entry, _span_id=None):
        """One attempt against the frozen observations. Admission
        is REVALIDATED immediately before execution, with the
        CURRENT policy (Contract 002): a procedure admitted under
        an earlier policy cannot execute after a new policy
        prohibits it. The pre_execute_hook, when set, runs between
        admission and execution and may change external state; the
        admission is checked again after it. A refusal returns
        (None, "refused") and nothing is executed or recorded —
        the caller treats it as a rejected candidate.
        The observation is consulted here and nowhere else in
        selection."""
        ok, status, detail = self._admit(entry, task, _span_id)
        if not ok:
            return self._refuse(task, entry, detail)
        if self.pre_execute_hook is not None:
            self.pre_execute_hook(task, entry)
            ok, status, detail = self._admit(entry, task, _span_id)
            if not ok:
                return self._refuse(task, entry, detail)
        self.trace.append("ACTION_INTENT", task.task_id, None,
                          {"action": entry.native_id})
        if self.post_intent_hook is not None:
            self.post_intent_hook(task, entry)
        observed = bool(self.observations.get(
            (task.task_id, entry.native_id), False))
        validation = self.validator.validate(task, entry.native_id, observed)
        self.trace.append("RECORDED", task.task_id, None,
                          {"action": entry.native_id,
                           "observed_success": observed,
                           "validation": validation})
        # TRACE-001: the observation and the independent validation
        # are recorded on the execution span (the L15 span the
        # caller passed in), so the pathway shows what was observed
        # and what the validator decided, separately.
        if self._tracer is not None and _span_id is not None:
            self._tracer.event("OBSERVATION", _span_id, {
                "procedure": entry.procedure_id, "observed": observed})
            self._tracer.event("VALIDATION", _span_id, {
                "procedure": entry.procedure_id, "validation": validation})
        # TRACE-002 completeness boundary: after the validation,
        # before the record is committed. A crash here leaves a
        # VALIDATION event with no L21 span and no experience
        # block — the execution happened, the memory did not.
        self._fault("after_validation", task, entry)
        return observed, validation, ""

    def _refuse(self, task, entry, detail):
        self.trace.append("REVALIDATION_DENIED", task.task_id, None,
                          {"action": entry.procedure_id,
                           "reason": detail,
                           "policy": self.policy.version})
        return None, "refused", detail

    def _demonstrated_conditions(self, entry, task):
        """The demonstrated conditions for a procedure, preferring
        a trusted record for this task's signature (the procedure
        as demonstrated for this problem), else any trusted record
        for the procedure (the most recent), else {} (never
        demonstrated — every relevant task condition is unknown).
        Used by the Fix 1 gates at exact retrieval, transfer
        selection and final admission."""
        latest = self.experiences.latest_per_pair()
        best = None
        for (t, p), r in latest.items():
            if p != entry.procedure_id or r["qual"] not in ("QUALIFIED", "UNDER_TEST"):
                continue
            if r.get("sig") == task.signature:
                return dict(r.get("cond") or {})
            if best is None or r.get("seq", 0) > best.get("seq", 0):
                best = r
        return dict(best.get("cond") or {}) if best else {}

    def _record(self, task, entry, observed, validation, qualification, source,
              _span_id=None):
        # TRACE-002: a CORRECTION is recorded when this write
        # demotes a TRUSTED (QUALIFIED) record for the same
        # procedure and signature — the correction of a wrong
        # decision. The earlier record is preserved (append-only);
        # only the qualification changes. A merely-failed
        # procedure (INCONCLUSIVE from a CANDIDATE/UNDER_TEST
        # standing) is not a correction — it was never trusted.
        trusted = None
        for (t, p), r in self.experiences.latest_per_pair().items():
            if p == entry.procedure_id and r.get("sig") == task.signature \
                    and r["qual"] == "QUALIFIED":
                trusted = r
                break
        rec = self.experiences.write(
            task_id=task.task_id, signature=task.signature,
            family=task.family, procedure_id=entry.procedure_id,
            observed_outcome="SUCCESS" if observed else "FAILURE",
            validator_result=validation,
            conditions=dict(task.conditions),
            qualification=qualification, source=source,
            tags=sorted(task.tags))
        # TRACE-002, Correction D: the memory reference must be
        # the ACTUAL durable block id (the store's record_id,
        # which uses the NATIVE procedure id). TRACE-001 recorded
        # the registered id ("exp.<task>.SOSO-P2.2"), which does
        # not resolve to any block in Big49 (the store's id is
        # "exp.<task>.P2.2"). A trace reference that does not
        # resolve is not provenance — it is a label.
        if self._tracer is not None and _span_id is not None:
            block = record_id(rec['task'], entry.native_id, rec['seq'])
            self._tracer.memory(_span_id, "write", "big49", block,
                                bucket="cognitive",
                                detail={"qualification": qualification,
                                        "source": source})
            if trusted is not None and qualification in (
                    "INCONCLUSIVE", "REJECTED"):
                self._tracer.correction(
                    _span_id, entry.procedure_id,
                    previous="QUALIFIED", new=qualification,
                    reason=("trusted procedure failed on a task it "
                            "was qualified for"),
                    evidence={"demonstration_task": trusted.get("task"),
                              "demonstration_signature": trusted.get("sig"),
                              "observed": observed,
                              "validation": validation})
        # TRACE-002 completeness boundary: after the record is
        # committed, before the task ends. A crash here leaves a
        # complete L21 span and a committed experience block but
        # no TASK_END — the memory happened, the task did not
        # finish.
        self._fault("after_record", task, entry)
        return rec

    # ---- the pipeline ----
    def run(self, task_dict, parent_execution_id=None, mode=None):
        # TRACE-001: one tracer per execution. The tracer is stored
        # on self for the duration of run() so _execute / _record /
        # _check_identity can record on the active spans; it is
        # cleared in the finally, even on an early return.
        # TRACE-002, Correction E: parent_execution_id links a
        # child execution (a multi-step step) to its parent
        # execution — recorded in TASK_BEGIN, not inferred from
        # task names. Correction C: mode is the recording mode
        # ("best_effort" or "evidence_grade"); the report carries
        # the certification result, so an incomplete trajectory
        # is never silently presented as complete.
        from .authority import require_task
        require_task(task_dict)
        mode = mode or self.recording_mode
        report = {"task_id": task_dict.get("task_id"),
                  "stage": None, "state": "REJECTED", "selected": None,
                  "attempts": [], "rejected": [], "information_requests": [],
                  # TRACE-002 correction, item 6: the full proposed
                  # list (every candidate the ranking produced, with
                  # its rank and score components) and the unattempted
                  # tail (proposed but never executed, with the
                  # reason). A proposal is not an executed branch.
                  "proposed": [], "unattempted": [], "pathway_cost": {},
                  "iou": None, "identity_digest": None,
                  "parent_execution_id": parent_execution_id,
                  "recording_mode": mode,
                  "trace_certified": None, "trace_certification_gaps": []}
        tracer = Tracer(self.trace, task_dict.get("task_id") or "unknown",
                        domain=self.profile["domain"],
                        profile=self._profile_id,
                        parent_execution_id=parent_execution_id,
                        mode=mode)
        report["execution_id"] = tracer.execution_id
        self._tracer = tracer
        try:
            result = self._run_inner(task_dict, report, tracer)
            certification = tracer.certify()
            result["trace_certified"] = certification["certified"]
            result["trace_certification_gaps"] = certification["gaps"]
            # TRACE-002 correction, item 6: the unattempted tail
            # and the pathway cost. A proposed candidate that was
            # never attempted is not a rejected candidate — it was
            # never reached. The reason is explicit: the task
            # resolved before it (an earlier candidate succeeded),
            # the task was left unresolved after the attempted
            # prefix, or the run was rejected before the stage.
            attempted = {a["procedure"] for a in result["attempts"]}
            rejected = {r["procedure"] for r in result["rejected"]}
            resolved = result["state"] == "RESOLVED"
            # Unattempted is per distinct procedure (a procedure
            # can be proposed by several sources — cross-task,
            # transfer and trial — and one attempt consumes all
            # of them). The entry lists every source that
            # proposed it, with the first proposal's rank.
            seen = {}
            for p in result["proposed"]:
                if p["procedure"] in seen:
                    seen[p["procedure"]]["sources"].append(p.get("source"))
                    continue
                seen[p["procedure"]] = {"procedure": p["procedure"],
                                        "native": p.get("native"),
                                        "sources": [p.get("source")],
                                        "rank": p.get("rank")}
            unattempted = []
            for proc, entry in seen.items():
                if proc in attempted or proc in rejected:
                    continue
                reason = ("not reached: an earlier candidate resolved "
                          "the task" if resolved else
                          "not reached: the run ended before this candidate")
                unattempted.append({**entry, "reason": reason})
            result["unattempted"] = unattempted
            result["pathway_cost"] = {
                "n_proposed": len(result["proposed"]),
                "n_proposed_procedures": len(seen),
                "n_attempted": len(result["attempts"]),
                "n_rejected": len(result["rejected"]),
                "n_unattempted": len(unattempted)}
            if self.mac002_adapter is not None and isinstance(task_dict, dict) and "mac002_records" in task_dict:
                result["mac002_report"] = self.mac002_adapter.evaluate(
                    task_dict,
                    task_dict["mac002_records"],
                    task_dict.get("mac002_observation_store", {}),
                    evidence_provider=task_dict.get("mac002_evidence_provider"),
                )
            if isinstance(task_dict, dict) and (
                "disposition_observation" in task_dict
                or "deliver_evidence" in task_dict
                or task_dict.get("disposition_gate")
            ):
                result["disposition_report"] = self._run_disposition(task_dict, result, tracer)
            if isinstance(task_dict, dict) and ("l24_features" in task_dict or "l24_method_records" in task_dict):
                result["l24_report"] = self._run_l24_causal(task_dict, result, tracer)
            if isinstance(task_dict, dict) and "visual_stream_a" in task_dict and "visual_stream_b" in task_dict:
                ve = ingest_dual_stream(
                    task_dict["visual_stream_a"],
                    task_dict["visual_stream_b"],
                    execution_id=result["execution_id"],
                    authority=task_dict.get("authority", 0),
                )
                self.trace.append("VISUAL_EVIDENCE_RECONCILED", task_dict.get("task_id"), None, {
                    "execution_id": result["execution_id"],
                    "disposition": ve["disposition_event"]["disposition"],
                    "conflicts": ve["conflicts"],
                    "native_vision": ve["native_soso_vision_status"],
                })
                result["visual_evidence_report"] = ve
            if isinstance(task_dict, dict) and "cognitive_domain" in task_dict:
                cog_res = evaluate_seven_domain_task(task_dict, execution_id=result["execution_id"])
                self.trace.append("SEVEN_DOMAIN_EVALUATED", task_dict.get("task_id"), None, {
                    "execution_id": result["execution_id"],
                    "domain": cog_res["domain"],
                    "status": cog_res["status"],
                    "disposition": cog_res["disposition_event"]["disposition"],
                    "functions_invoked": cog_res["functions_invoked"],
                })
                result["cognitive_result"] = cog_res
            return result
        finally:
            self._tracer = None

    def _run_inner(self, task_dict, report, tracer):
        tracer.begin_task(context={
            "family": task_dict.get("family"),
            "signature": task_dict.get("signature"),
            "tags": sorted(task_dict.get("tags") or []),
            "conditions": dict(task_dict.get("conditions") or {}),
            # TRACE-002: the ablation set active for this
            # execution, so a missing span is attributable to
            # the experiment (not to a recording failure). The
            # description set is recorded too, so the
            # information-source ablation (descriptions=None) is
            # visible in the trace, distinct from the function
            # ablation (L24 disabled, descriptions intact).
            "disabled_functions": sorted(self.disabled_functions),
            "descriptions": ("default" if self.descriptions == "default"
                             else "none")})
        # L01 — Interpret (INITIATE): the task is interpreted
        # (domain, tags, conditions) and its identity checked.
        with self._trace_span("L01", "root", "interpret task") as l01:
            try:
                task = task_mod.make_task(**{k: v for k, v in task_dict.items()
                                             if k in ("task_id", "signature", "family",
                                                      "tags", "conditions",
                                                      "unknown_conditions",
                                                      "contradictory_conditions",
                                                      "requested_operation",
                                                      "observed", "provenance")})
            except ValueError as e:
                report["rejected"].append({"procedure": None, "reason": str(e)})
                self.trace.append("TASK_REJECTED", task_dict.get("task_id"),
                                  None, {"reason": str(e)})
                tracer.end_task("REJECTED", detail={"reason": str(e)})
                return report
            report["identity_digest"] = task.identity_digest()
            mismatch = self._check_identity(task, l01)
            if mismatch:
                report["rejected"].append({"procedure": None, "reason": mismatch})
                self.trace.append("IDENTITY_MISMATCH", task.task_id, None,
                                  {"reason": mismatch})
                tracer.end_task("REJECTED", detail={"reason": mismatch})
                return report
        self.trace.append("PROBLEM_ARRIVED", task.task_id, None,
                          {"sig": task.signature, "family": task.family})
        projections = self.experiences.rebuild_projections(self.soso)
        exact = projections["exact"]
        transfer_conn = projections["transfer_conn"]

        # -- Stage 2: Lower, exact qualified reuse --
        # HARDEN-001 fix 1: exact retrieval enforces the
        # demonstrated conditions AND the demonstration's tags.
        # An explicit contradiction or an unknown relevant
        # condition refuses the serve (missing evidence is not
        # silently treated as satisfied); a task sharing no tag
        # with the demonstration is not the same problem, even
        # when the signature matches. Either refusal falls
        # through to transfer/trial — it is not a hard error.
        # TRACE-001: L30 (Retrieve Experience) wraps the stage;
        # each executed candidate gets a U37 decision span, an
        # L15 execution span and an L21 recording span.
        # TRACE-002 ablation: L30 disabled -> the whole exact
        # stage is skipped (a FUNCTION_DISABLED event records
        # it); U37 disabled -> candidates are retrieved but
        # never decided; L15 disabled -> candidates are decided
        # but never executed. Each falls through to transfer.
        if self._disabled("L30"):
            self._disable_event("L30", reason="ablation: L30 disabled")
        else:
            with self._trace_span("L30", "root",
                                 "retrieve exact experience") as l30:
                for rank, proc_id in enumerate(
                        sorted(exact.get(task.signature, {}))):
                    rec = exact[task.signature][proc_id]
                    # TRACE-002, Correction D: the actual durable
                    # block id (native procedure id), not the
                    # registered form.
                    tracer.memory(l30, "retrieve", "big49",
                                  record_id(rec['task'],
                                            _native_of(rec['proc']),
                                            rec['seq']),
                                  bucket="cognitive",
                                  detail={"qualification": rec.get("qual")})
                    # Item 4+6: the exact candidates are proposed
                    # too (they are the ranked list for this stage
                    # — rank = position in the sorted exact index,
                    # components = the exact-signature match).
                    # A proposal is not an executed branch.
                    tracer.event("CANDIDATE_PROPOSED", l30, {
                        "procedure": proc_id,
                        "native": _native_of(proc_id),
                        "source": "lower-exact", "rank": rank,
                        "rank_components": {"exact_signature": 1}})
                    report["proposed"].append({
                        "procedure": proc_id,
                        "native": _native_of(proc_id),
                        "source": "lower-exact", "rank": rank,
                        "rank_components": {"exact_signature": 1}})
                if self._disabled("U37"):
                    self._disable_event(
                        "U37", l30,
                        reason="ablation: U37 disabled (exact stage)")
                elif self._disabled("L15"):
                    self._disable_event(
                        "L15", l30,
                        reason="ablation: L15 disabled (exact stage)")
                    for proc_id in sorted(exact.get(task.signature, {})):
                        report["rejected"].append(
                            {"procedure": proc_id,
                             "reason": "execution function L15 disabled"})
                else:
                    for proc_id in sorted(exact.get(task.signature, {})):
                        rec = exact[task.signature][proc_id]
                        entry = reg_get(proc_id)
                        # U01: the demonstrated-condition check
                        # under its own span (parent: L30).
                        cstatus, cdetail = self._condition_check(
                            entry, task, rec.get("cond"), l30)
                        if cstatus != "satisfied":
                            report["rejected"].append(
                                {"procedure": proc_id,
                                 "reason": f"demonstrated conditions {cstatus}: {cdetail}"})
                            tracer.event("CANDIDATE_REJECTED", l30, {
                                "procedure": proc_id,
                                "reason": f"demonstrated conditions {cstatus}: {cdetail}"})
                            continue
                        if not _tags_overlap(rec.get("tags"), task.tags):
                            report["rejected"].append(
                                {"procedure": proc_id,
                                 "reason": "demonstrated tags do not overlap the task"})
                            tracer.event("CANDIDATE_REJECTED", l30, {
                                "procedure": proc_id,
                                "reason": "demonstrated tags do not overlap the task"})
                            continue
                        # U02: the Suit admission computation
                        # under its own span (parent: L30).
                        ok, status, detail = self._admit(
                            entry, task, l30)
                        if not ok:
                            report["rejected"].append({"procedure": proc_id, "reason": detail})
                            tracer.event("CANDIDATE_REJECTED", l30, {
                                "procedure": proc_id, "reason": detail})
                            continue
                        with self._trace_span(
                                "U37", l30,
                                f"decide {entry.procedure_id} (lower-exact)") as dspan:
                            # TRACE-002 completeness boundary: after
                            # the decision span begins, before
                            # admission. A crash here leaves a U37
                            # span with no ADMISSION event and no
                            # execution — the decision was entered,
                            # not made.
                            self._fault("after_decide", task, entry)
                            tracer.event("ADMISSION", dspan, {
                                "procedure": entry.procedure_id,
                                "policy": self.policy.version, "status": status})
                            with self._trace_span(
                                    "L15", dspan,
                                    f"execute {entry.procedure_id}") as espan:
                                observed, validation, _detail = self._execute(
                                    task, entry, espan)
                            if validation == "refused":
                                report["rejected"].append(
                                    {"procedure": proc_id, "reason": _detail})
                                continue
                            # HARDEN-001 fix 2: a proven false success is
                            # REJECTED at the exact stage too (not merely
                            # INCONCLUSIVE). A procedure that merely fails stays
                            # INCONCLUSIVE — contested, not a proven lie.
                            # TRACE-002: the positive qualification
                            # requires the procedure to have SUCCEEDED
                            # (observed) as well as validated. A truthful
                            # failure (observed False, validation PASS —
                            # the validator confirms the procedure
                            # correctly did not succeed) is a failed
                            # procedure, not a re-qualified one: the
                            # previous code re-qualified it, contradicting
                            # the comment below ("a qualified procedure
                            # that fails ... drops from the trusted
                            # index"). This matches the trial stage,
                            # which already required observed AND
                            # validation PASS for a positive outcome.
                            if observed and validation == "FAIL":
                                qual = "REJECTED"
                            elif observed and validation == "PASS":
                                qual = "QUALIFIED"
                            else:
                                qual = "INCONCLUSIVE"
                            with self._trace_span(
                                    "L21", espan,
                                    f"record {entry.procedure_id}") as rspan:
                                self._record(task, entry, observed, validation, qual,
                                             "lower-exact", rspan)
                            report["attempts"].append(
                                {"procedure": proc_id, "native": entry.native_id,
                                 "observed": observed,
                                 "validation": validation, "qualification_after": qual})
                            if observed and validation == "PASS":
                                return self._done(report, task, "lower-exact", proc_id, "RESOLVED")
            # A qualified procedure that fails within its conditions
            # is contested: it drops from the trusted index (the
            # INCONCLUSIVE record supersedes the QUALIFIED one).
            # Fall through to the transfer stage.

        # -- Stage 3: SoSo transfer and recognition --
        # TRACE-001: U08 (Retrieve Memory) wraps the layer call
        # (the transfer index is a derived index, not a durable
        # block — recorded as store "transfer-index"); U09
        # (Compare Candidates) wraps the ranking; L24 (Cross-Task
        # Recognise) runs only when a cross-task candidate is
        # produced. A cross-family proposal is a domain transition
        # with its trigger and evidence recorded.
        # TRACE-002 ablation: U08 disabled -> the whole transfer
        # stage is skipped (the strategy is removed, Correction B
        # level c). L24 disabled -> the FUNCTION is removed (the
        # layer still computes the cross-task candidates, but the
        # pipeline drops them — no L24 span, no domain transition,
        # Correction B level a). U09 disabled -> the comparison
        # is removed (retrieval runs, nothing is ranked or
        # proposed). U37 disabled -> candidates are proposed but
        # never decided; L15 disabled -> decided but never
        # executed. Each falls through to the trial stage.
        if self._cl is not None and self._disabled("U08"):
            self._disable_event("U08", reason="ablation: U08 disabled")
        elif self._cl is not None:
            with self._trace_span("U08", "root",
                                 "retrieve from transfer index") as u08:
                tracer.memory(u08, "retrieve", "transfer-index",
                              "transfer-index", bucket=None,
                              detail="derived index (rebuilt from Big49)")
                # L02: the layer's learned-applicability computation
                # runs under its own L02 span(s), parented on this
                # U08 span — threaded through select_with_policy
                # -> recognise / cross_task_candidates ->
                # learned_applicability via the span-emitter
                # factory. L02 disabled: the layer returns {}
                # (recognise [], cross-task gated off); the
                # FUNCTION_DISABLED event records it here.
                if self._disabled("L02"):
                    self._disable_event(
                        "L02", u08, reason="ablation: L02 disabled")
                sel = self._cl.select_with_policy(
                    transfer_conn, frozenset(task.tags), suit=None, ops=None,
                    policy="LOWER_FIRST",
                    descriptions=(self._cl.PROCEDURE_DESCRIPTIONS
                                  if self.descriptions == "default"
                                  else self.descriptions),
                    applicability_span=(
                        lambda digest: self._l02_span(u08, digest)),
                    span_tags=frozenset(task.tags),
                    disabled_functions=self.disabled_functions)
            proposed = list(_dedup(sel.get("procedures", [])))
            cross = sel.get("cross_task") or []
            if self._disabled("L24"):
                # Function-level ablation (Correction B, level a):
                # the layer computed the cross-task candidates
                # (the information source is intact), but L24 is
                # disabled, so they are dropped — no L24 span, no
                # domain transition, and the cross-task procedures
                # are removed from the proposed list. This is
                # distinct from descriptions=None (level b, the
                # information source removed) and from disabling
                # U08 (level c, the whole strategy removed).
                self._disable_event(
                    "L24", u08, reason="ablation: L24 disabled")
                cross_ids = {c["procedure"] for c in cross}
                proposed = [p for p in proposed if p not in cross_ids]
                cross = []
            if cross:
                with self._trace_span("L24", u08,
                                     "cross-task recognition") as l24:
                    for rank, c in enumerate(cross):
                        # Item 4: the event carries BOTH ids (the
                        # layer names the native id; the contract
                        # identity is the registered id). Item 6:
                        # the rank and the score components (the
                        # overlapping words that produced the
                        # analogy) are recorded — a proposal is
                        # not an executed branch.
                        tracer.event("CANDIDATE_PROPOSED", l24, {
                            "procedure": _registered_of(c["procedure"]),
                            "native": c["procedure"],
                            "overlapping_words": sorted(c["overlapping_words"]),
                            "source": "cross-task",
                            "rank": rank,
                            "rank_components": {
                                "overlapping_words":
                                    len(c["overlapping_words"])}})
                        report["proposed"].append({
                            "procedure": _registered_of(c["procedure"]),
                            "native": c["procedure"], "source": "cross-task",
                            "rank": rank,
                            "rank_components": {
                                "overlapping_words":
                                    len(c["overlapping_words"])}})
                        # A cross-domain proposal is a domain
                        # transition: the procedure was demonstrated
                        # in a different family than the task's.
                        # The trigger is the description overlap;
                        # the reason and the evidence are the
                        # shared words and the demonstration
                        # record that produced them.
                        demo = None
                        for (t, p), r in sorted(
                                self.experiences.latest_per_pair().items()):
                            if p == _registered_of(c["procedure"]) and \
                                    r["qual"] in ("QUALIFIED", "UNDER_TEST"):
                                if demo is None or r.get("seq", 0) > demo.get("seq", 0):
                                    demo = r
                        origin = demo.get("fam") if demo else None
                        if origin and origin != task.family:
                            tracer.domain_transition(
                                l24, origin=origin,
                                destination=task.family,
                                trigger="description overlap",
                                reason=(f"description overlap: "
                                        f"{sorted(c['overlapping_words'])}"),
                                evidence={
                                    "overlapping_words":
                                        sorted(c["overlapping_words"]),
                                    "demonstration_task":
                                        demo.get("task"),
                                    "demonstration_signature":
                                        demo.get("sig")},
                                outcome=None)
            # Under-test procedures are proposed directly (SoSo's
            # reason() gives an under-test experience a real but
            # weaker standing than a validated match): a procedure
            # with exactly one demonstration cannot have a learned
            # applicability set yet (min_support 2), so the layer
            # alone cannot propose it. The check is tag overlap
            # with the under-test record's tags, not a second
            # experience store — the record is the same Big49
            # block the projections derive from.
            # TRACE-001: the profile's evidence requirement
            # gates the under-test proposals. A profile that
            # requires qualified evidence (legal, medical) does
            # not propose UNDER_TEST procedures from transfer.
            for (t, p), r in sorted(
                    self.experiences.latest_per_pair().items()):
                if r["qual"] != "UNDER_TEST":
                    continue
                if self.profile["evidence_required"] != "under_test":
                    continue
                if not (set(r["tags"]) & set(task.tags)):
                    continue
                # HARDEN-001 fix 1: an under-test proposal must
                # also satisfy the demonstrated conditions — an
                # unknown relevant condition is not silently
                # treated as satisfied.
                _entry = reg_get(p)
                # U01: the demonstrated-condition check under its
                # own span (parent: U08).
                cstatus, _ = self._condition_check(
                    _entry, task, r.get("cond"), u08)
                if cstatus != "satisfied":
                    continue
                proposed.append(_native_of(p))
            proposed = _dedup(proposed)
            # U09 — Compare Candidates: the ranking and the
            # proposed list. Each proposed candidate is recorded;
            # each executed candidate gets a U37 decision span,
            # an L15 execution span and an L21 recording span.
            # U09 disabled -> the comparison is removed: the
            # retrieval ran (U08) and the candidates were
            # computed, but nothing is ranked or proposed.
            if self._disabled("U09"):
                self._disable_event(
                    "U09", u08, reason="ablation: U09 disabled")
            else:
                with self._trace_span("U09", "root",
                                     "compare transfer candidates") as u09:
                    for rank, native in enumerate(proposed):
                        # Item 4+6: both ids, the rank in the
                        # layer's proposed order, and the source.
                        # The transfer list is the layer's order,
                        # not a scored rank — the rank is the
                        # position, and rank_components is empty
                        # (the layer does not score it).
                        tracer.event("CANDIDATE_PROPOSED", u09, {
                            "procedure": _registered_of(native),
                            "native": native, "source": "transfer",
                            "rank": rank, "rank_components": {}})
                        report["proposed"].append({
                            "procedure": _registered_of(native),
                            "native": native, "source": "transfer",
                            "rank": rank, "rank_components": {}})
                    if self._disabled("U37"):
                        self._disable_event(
                            "U37", u09,
                            reason="ablation: U37 disabled (transfer stage)")
                    elif self._disabled("L15"):
                        self._disable_event(
                            "L15", u09,
                            reason="ablation: L15 disabled (transfer stage)")
                        for native in proposed:
                            report["rejected"].append(
                                {"procedure": native,
                                 "reason": "execution function L15 disabled"})
                    else:
                        for native in proposed:
                            try:
                                entry = by_native(native)
                            except KeyError:
                                report["rejected"].append(
                                    {"procedure": native,
                                     "reason": "not in the procedure registry"})
                                continue
                            if entry.procedure_id in [a["procedure"] for a in report["attempts"]]:
                                continue
                            # U02: the Suit admission computation
                            # under its own span (parent: U09).
                            ok, status, detail = self._admit(
                                entry, task, u09)
                            if not ok:
                                report["rejected"].append(
                                    {"procedure": entry.procedure_id, "reason": detail})
                                tracer.event("CANDIDATE_REJECTED", u09, {
                                    "procedure": entry.procedure_id, "reason": detail})
                                continue
                            # HARDEN-001 fix 1: transfer selection enforces
                            # the demonstrated conditions too. A contradiction
                            # or an unknown relevant condition rejects the
                            # candidate (cross-family transfer is preserved:
                            # a task presenting no relevant condition is
                            # "satisfied", and the check uses the procedure's
                            # demonstrated conditions for this signature).
                            # U01: the check under its own span (parent: U09).
                            cstatus, cdetail = self._condition_check(
                                entry, task,
                                self._demonstrated_conditions(entry, task),
                                u09)
                            if cstatus != "satisfied":
                                report["rejected"].append(
                                    {"procedure": entry.procedure_id,
                                     "reason": f"demonstrated conditions {cstatus}: {cdetail}"})
                                tracer.event("CANDIDATE_REJECTED", u09, {
                                    "procedure": entry.procedure_id,
                                    "reason": f"demonstrated conditions {cstatus}: {cdetail}"})
                                continue
                            with self._trace_span(
                                    "U37", u09,
                                    f"decide {entry.procedure_id} (soso-transfer)") as dspan:
                                self._fault("after_decide", task, entry)
                                tracer.event("ADMISSION", dspan, {
                                    "procedure": entry.procedure_id,
                                    "policy": self.policy.version, "status": status})
                                with self._trace_span(
                                        "L15", dspan,
                                        f"execute {entry.procedure_id}") as espan:
                                    observed, validation, _detail = self._execute(
                                        task, entry, espan)
                                if validation == "refused":
                                    report["rejected"].append(
                                        {"procedure": entry.procedure_id, "reason": _detail})
                                    continue
                                # A transfer success reaffirms the procedure's
                                # current standing; it does not promote it.
                                # Promotion happens only through qualify().
                                prior = self.experiences.latest_per_pair()
                                standing = "UNDER_TEST"
                                for (t, p), r in prior.items():
                                    if p == entry.procedure_id and r["qual"] == "QUALIFIED":
                                        standing = "QUALIFIED"
                                        break
                                # HARDEN-001 fix 2: a proven false success is
                                # REJECTED here, matching the trial stage. The
                                # observation, validator decision and evidence are
                                # preserved in the record; only the qualification
                                # changes.
                                # TRACE-002: as in the exact stage, a
                                # positive standing requires the procedure to
                                # have SUCCEEDED (observed) as well as
                                # validated. A truthful failure is
                                # INCONCLUSIVE (contested), not a
                                # re-affirmation of its standing.
                                if observed and validation == "FAIL":
                                    qual = "REJECTED"
                                elif observed and validation == "PASS":
                                    qual = standing
                                else:
                                    qual = "INCONCLUSIVE"
                                with self._trace_span(
                                        "L21", espan,
                                        f"record {entry.procedure_id}") as rspan:
                                    self._record(task, entry, observed, validation, qual,
                                                 "soso-transfer", rspan)
                                report["attempts"].append(
                                    {"procedure": entry.procedure_id,
                                     "native": entry.native_id, "observed": observed,
                                     "validation": validation, "qualification_after": qual})
                                if observed and validation == "PASS":
                                    return self._done(report, task, "soso-transfer",
                                                      entry.procedure_id, "RESOLVED")
            if sel.get("mismatch"):
                self.trace.append("MISMATCH", task.task_id, None,
                                  {"mismatch": sel["mismatch"]})

        # -- Stage 4: Higher, bounded trial --
        # TRACE-001: U09 (Compare Candidates) wraps the ranking;
        # U10 (Disambiguate Conditions) wraps the information
        # request (the top candidates differ in an unknown
        # required condition, so the system asks instead of
        # guessing). The profile gate (trial_allowed) is applied
        # AFTER the information request — a profile that forbids
        # trial still asks for the missing condition.
        # TRACE-002 ablation: U09 disabled -> the whole trial
        # block is skipped (no ranking, no information request,
        # no trial). U37 disabled -> candidates are ranked but
        # never decided; L15 disabled -> decided but never
        # executed. Each falls through to unresolved.
        if self._disabled("U09"):
            self._disable_event("U09", reason="ablation: U09 disabled")
        else:
            with self._trace_span("U09", "root",
                                 "rank trial candidates") as u09:
                # PATHWAY-001: replay stored pathway
                # discriminators (arm C) before the trial ranking.
                # The provider names the discriminators; the
                # source answers them for THIS task. Runs without
                # a provider and a source skip this entirely.
                if self.pathway_provider is not None \
                        and self.evidence_source is not None:
                    task = self._replay_discriminators(task, report, u09)
                # U04: the trial gating (pointed-at detection, the
                # unknown-hard ask branch, the every-pointed-blocked
                # escalate branch) under its own span (parent: U09).
                trial = self._trial_candidates_spanned(task, u09)
                for rank, entry in enumerate(trial["candidates"]):
                    # Item 4+6: both ids, the rank in the ranked
                    # trial list, and the score components that
                    # produced it (family relevance, shared
                    # description words, soft-precondition
                    # matches). A proposal is not an executed
                    # branch — the attempts are the prefix that
                    # actually ran.
                    tracer.event("CANDIDATE_PROPOSED", u09, {
                        "procedure": entry.procedure_id,
                        "native": entry.native_id, "source": "trial",
                        "rank": rank,
                        "rank_components": _rank_components(entry, task)})
                    report["proposed"].append({
                        "procedure": entry.procedure_id,
                        "native": entry.native_id, "source": "trial",
                        "rank": rank,
                        "rank_components": _rank_components(entry, task)})
                if trial["blocked_by_unknown"]:
                    req = {"missing": trial["blocked_by_unknown"]["missing"],
                           "question": trial["blocked_by_unknown"]["question"],
                           "procedures": trial["blocked_by_unknown"]["procedures"],
                           "source": "targeted_investigation"}
                    with self._trace_span(
                            "U10", u09,
                            "disambiguate missing condition") as u10:
                        if self.evidence_source is None:
                            # Current behavior verbatim: ask and
                            # escalate; the request is never
                            # answered and the run never resumes.
                            tracer.event("ESCALATION", u10, {
                                "reason": "unknown required condition",
                                "missing": req["missing"],
                                "question": req["question"]})
                            report["information_requests"].append(req)
                            for proc_id, reason in trial["pointed_blocks"]:
                                report["rejected"].append(
                                    {"procedure": proc_id, "reason": reason})
                            self.trace.append("INFORMATION_REQUEST", task.task_id,
                                              None, req)
                            return self._unresolved(report, task, "ESCALATED")
                        # PATHWAY-001: answered evidence requests.
                        # The pointed-at procedures are the ones
                        # this request names (the unknown-pointed
                        # procedures from _trial_candidates); the
                        # pointed_blocks are the rejections the
                        # run was carrying when it asked.
                        pointed = list(
                            trial["blocked_by_unknown"]["procedures"])
                        pointed_blocks = list(trial["pointed_blocks"])
                        expectation_ids, expectations = {}, {}
                        observations = {}
                        answered = True
                        # PATHWAY-002: when a measurement selector
                        # is configured, the pointed-at
                        # procedures' hard AND soft preconditions
                        # become the selector's hypotheses; the
                        # ranked selection (frozen as
                        # EVIDENCE_SELECTION before any answer)
                        # decides which conditions are measured,
                        # in which order. Without a selector:
                        # every missing condition is answered,
                        # sorted (verbatim).
                        sel_hyps = None
                        hyps_by_condition = {}
                        # U05: the selector path runs only when a
                        # selector is configured AND U05 is not
                        # disabled. Disabled: exactly the
                        # no-selector fallback (every missing
                        # condition, sorted), with a
                        # FUNCTION_DISABLED event recorded.
                        if self.measurement_selector is not None \
                                and not self._disabled("U05"):
                            sel_hyps = self._selector_hypotheses(
                                procedures=set(pointed))
                            selection = self._select_measurements(
                                task, sel_hyps, dict(task.conditions), u10)
                            to_answer = [s["condition"]
                                         for s in selection["selected"]]
                            for proc, conds in sel_hyps:
                                for name, (value, _hard) in conds.items():
                                    hyps_by_condition.setdefault(
                                        name, []).append((proc, value))
                        else:
                            if self.measurement_selector is not None:
                                self._disable_event(
                                    "U05", u10,
                                    reason="ablation: U05 disabled")
                            to_answer = sorted(req["missing"])
                        for condition_name in to_answer:
                            if condition_name in hyps_by_condition:
                                hypotheses = hyps_by_condition[condition_name]
                                hard = self._condition_is_hard(
                                    condition_name, sel_hyps)
                            else:
                                hypotheses = self._evidence_hypotheses(
                                    condition_name, procedures=set(pointed))
                                hard = True
                            # The request's question names the hard
                            # unknowns that blocked the trial; a soft
                            # measurement the selector added gets its
                            # own ranking-worded question (a soft
                            # precondition never refuses execution).
                            if hard:
                                question = req["question"]
                            else:
                                question = (
                                    f"What is {condition_name} for this task? "
                                    f"{', '.join(sorted(p for p, _ in hypotheses))} "
                                    f"rank on it — the measurement "
                                    f"distinguishes them.")
                            task, observation, expectation = \
                                self._answer_condition(
                                    task, condition_name, u10,
                                    question, hypotheses, hard=hard)
                            expectation_ids[condition_name] = \
                                expectation["expectation_id"]
                            expectations[condition_name] = expectation
                            if observation is None:
                                answered = False
                            else:
                                observations[condition_name] = observation
                        req["answered"] = answered
                        req["expectation_id"] = expectation_ids
                        req["expectation"] = expectations
                        req["observation"] = observations or None
                        outcome = None
                        contradicted = []
                        if not answered:
                            outcome = "unanswered"
                        else:
                            # Recompute the trial list from the
                            # UPDATED task; only procedures the
                            # recompute lists may be attempted,
                            # and admission is revalidated before
                            # execution as always. U04: the
                            # gating under its own span (parent:
                            # U10 — the recompute happens inside
                            # the answered-request span).
                            trial = self._trial_candidates_spanned(task, u10)
                            if trial["blocked_by_unknown"]:
                                # The answer exposed a further
                                # unknown condition.
                                outcome = "unanswered"
                            else:
                                contradicted = [
                                    p for p in pointed
                                    if self._procedure_contradicted_by(
                                        p, observations)]
                                if pointed and len(contradicted) == len(pointed):
                                    outcome = "justified_abstention"
                                else:
                                    outcome = "resumed"
                        req["outcome"] = outcome
                        if outcome == "unanswered":
                            tracer.event("ESCALATION", u10, {
                                "reason": "unknown required condition",
                                "missing": req["missing"],
                                "question": req["question"]})
                            report["information_requests"].append(req)
                            for proc_id, reason in pointed_blocks:
                                report["rejected"].append(
                                    {"procedure": proc_id, "reason": reason})
                            self.trace.append("INFORMATION_REQUEST", task.task_id,
                                              None, req)
                            return self._unresolved(report, task, "ESCALATED")
                        if outcome == "justified_abstention":
                            tracer.event("ESCALATION", u10, {
                                "reason": ("evidence contradicts every "
                                           "pointed-at procedure"),
                                "observation": observations,
                                "contradicted": contradicted})
                            report["information_requests"].append(req)
                            for proc_id, reason in pointed_blocks:
                                report["rejected"].append(
                                    {"procedure": proc_id, "reason": reason})
                            self.trace.append("INFORMATION_REQUEST", task.task_id,
                                              None, req)
                            return self._unresolved(report, task, "ESCALATED")
                        # Resumed: propose the recomputed
                        # candidates (same shape as the existing
                        # proposal loop) and fall through to the
                        # existing flow — profile gate, U37/L15
                        # spans, attempts — using the UPDATED
                        # task. A procedure admitted after the
                        # answer was never rejected, so
                        # pointed_blocks are NOT appended here.
                        for rank, entry in enumerate(trial["candidates"]):
                            tracer.event("CANDIDATE_PROPOSED", u09, {
                                "procedure": entry.procedure_id,
                                "native": entry.native_id, "source": "trial",
                                "rank": rank,
                                "rank_components": _rank_components(entry, task)})
                            report["proposed"].append({
                                "procedure": entry.procedure_id,
                                "native": entry.native_id, "source": "trial",
                                "rank": rank,
                                "rank_components": _rank_components(entry, task)})
                        report["information_requests"].append(req)
                        self.trace.append("INFORMATION_REQUEST", task.task_id,
                                          None, req)
                if trial["escalate_because"]:
                    # PATHWAY-002 §4 (gated): when answered replay
                    # evidence contradicts every procedure in
                    # this branch's list, the run records the
                    # justified-abstention disposition EXACTLY as
                    # the direct U10 path does — the replay
                    # request entry is updated (outcome +
                    # contradicted), the ESCALATION event names
                    # the contradiction, the INFORMATION_REQUEST
                    # entry is appended, the pointed-blocks
                    # rejections are appended, and the run ends
                    # ESCALATED. Default off: PATHWAY-001
                    # behavior verbatim (UNSOLVED, no record).
                    if self.replay_contradiction_abstention:
                        abstention = self._replay_contradiction_abstention(
                            report, trial["escalate_because"])
                        if abstention is not None:
                            req, contradicted, observations = abstention
                            req["outcome"] = "justified_abstention"
                            req["contradicted"] = sorted(contradicted)
                            for proc_id, reason in trial["escalate_because"]:
                                report["rejected"].append(
                                    {"procedure": proc_id, "reason": reason})
                            tracer.event("ESCALATION", u09, {
                                "reason": ("evidence contradicts every "
                                           "pointed-at procedure"),
                                "observation": observations,
                                "contradicted": sorted(contradicted)})
                            self.trace.append("INFORMATION_REQUEST",
                                              task.task_id, None, req)
                            tracer.event("INFORMATION_REQUEST", u09, req)
                            return self._unresolved(report, task, "ESCALATED")
                    for proc_id, reason in trial["escalate_because"]:
                        report["rejected"].append(
                            {"procedure": proc_id, "reason": reason})
                    tracer.event("ESCALATION", u09, {
                        "reason": "every pointed-at procedure blocked"})
                    return self._unresolved(report, task, "UNSOLVED")
                # TRACE-001: the profile gate. A profile that forbids
                # trial (legal, medical) escalates instead of testing
                # an unproven procedure. The reason is recorded.
                if not self.profile["trial_allowed"]:
                    tracer.event("ESCALATION", u09, {
                        "reason": f"trial not allowed by profile {self._profile_id}",
                        "profile": self._profile_id})
                    return self._unresolved(report, task, "UNSOLVED")
                if self._disabled("U37"):
                    self._disable_event(
                        "U37", u09,
                        reason="ablation: U37 disabled (trial stage)")
                elif self._disabled("L15"):
                    self._disable_event(
                        "L15", u09,
                        reason="ablation: L15 disabled (trial stage)")
                    for entry in trial["candidates"][:MAX_TRIAL_ATTEMPTS]:
                        report["rejected"].append(
                            {"procedure": entry.procedure_id,
                             "reason": "execution function L15 disabled"})
                else:
                    for entry in trial["candidates"][:MAX_TRIAL_ATTEMPTS]:
                        if entry.procedure_id in [a["procedure"] for a in report["attempts"]]:
                            continue
                        # U02: the Suit admission computation under
                        # its own span (parent: U09).
                        ok, status, detail = self._admit(
                            entry, task, u09)
                        if not ok:
                            report["rejected"].append(
                                {"procedure": entry.procedure_id, "reason": detail})
                            tracer.event("CANDIDATE_REJECTED", u09, {
                                "procedure": entry.procedure_id, "reason": detail})
                            continue
                        with self._trace_span(
                                "U37", u09,
                                f"decide {entry.procedure_id} (higher-trial)") as dspan:
                            self._fault("after_decide", task, entry)
                            tracer.event("ADMISSION", dspan, {
                                "procedure": entry.procedure_id,
                                "policy": self.policy.version, "status": status})
                            with self._trace_span(
                                    "L15", dspan,
                                    f"execute {entry.procedure_id}") as espan:
                                observed, validation, _detail = self._execute(
                                    task, entry, espan)
                            if validation == "refused":
                                report["rejected"].append(
                                    {"procedure": entry.procedure_id, "reason": _detail})
                                continue
                            if observed and validation == "FAIL":
                                # A false success is caught, not learned.
                                qual = "REJECTED"
                            elif observed and validation == "PASS":
                                # A trial success is CANDIDATE: stored, never
                                # auto-qualified, never in the trusted index.
                                qual = "CANDIDATE"
                            else:
                                qual = "INCONCLUSIVE"
                            with self._trace_span(
                                    "L21", espan,
                                    f"record {entry.procedure_id}") as rspan:
                                self._record(task, entry, observed, validation, qual,
                                             "higher-trial", rspan)
                            report["attempts"].append(
                                {"procedure": entry.procedure_id,
                                 "native": entry.native_id, "observed": observed,
                                 "validation": validation, "qualification_after": qual})
                            if observed and validation == "PASS":
                                return self._done(report, task, "higher-trial",
                                                  entry.procedure_id, "RESOLVED")
        return self._unresolved(report, task, "UNSOLVED")

    # ---- PATHWAY-001: answered evidence requests ----
    def _next_evidence_expectation_id(self):
        """The event_id the next trace event will carry
        ("ev-<seq:04d>"). The TraceLog's seq is AUTOINCREMENT,
        so the next seq is MAX(seq)+1, and the run holds the
        WriterLock, so nothing else appends in between. The
        expectation payload is frozen with this id before the
        event is emitted; the caller verifies the returned
        event's real id agrees and uses the real id downstream."""
        row = self.trace.db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
        return f"ev-{row[0] + 1:04d}"

    def _evidence_hypotheses(self, condition_name, procedures=None):
        """[(registered_id, required_value)] for every registry
        entry with a HARD precondition on condition_name,
        restricted to `procedures` (registered ids) when given.
        Sorted by procedure id, so the expectation is
        deterministic."""
        out = []
        for entry in sorted(REGISTRY.values(),
                            key=lambda e: e.procedure_id):
            req = entry.preconditions.get(condition_name)
            if req is None or not req[1]:
                continue
            if procedures is not None \
                    and entry.procedure_id not in procedures:
                continue
            out.append((entry.procedure_id, req[0]))
        return out

    def _procedure_contradicted_by(self, procedure_id, observations):
        """Is this procedure contradicted by the observations?
        A procedure is contradicted when an observed condition
        is one of its HARD preconditions and the observed value
        differs from the value it requires. A procedure with no
        hard precondition on any observed condition is never
        contradicted by the observations."""
        entry = reg_get(procedure_id)
        for name, obs in observations.items():
            req = entry.preconditions.get(name)
            if req is not None and req[1] and obs["value"] != req[0]:
                return True
        return False

    def _build_expectation(self, condition_name, question,
                           hypotheses, hard=True):
        """Build the frozen expectation for one unknown condition
        (competing hypotheses, predicted classes, the implication
        of each class, the permitted source, the requested
        measurement) WITHOUT emitting it or consulting the
        source. The expectation_id is the id the next trace
        event will carry; the caller corrects it to the real
        event id after emitting (see _answer_condition).
        PATHWAY-002: the expectation carries the additive
        "hard" flag (True for a hard/admission measurement,
        False for a soft/ranking one). Hard implications use
        admits / contradicts; soft implications use ranks_up /
        ranks_down — a soft mismatch is never worded as a
        contradiction. No existing expectation key changes
        meaning."""
        values = sorted({v for _, v in hypotheses})
        predicted = values + ["other"]
        implications = {}
        for cls in predicted:
            if hard:
                if cls == "other":
                    implications[cls] = {
                        "admits": [],
                        "contradicts": [p for p, _ in hypotheses],
                        "next": ("justified abstention: escalate with "
                                 "the observation recorded")}
                else:
                    admits = [p for p, v in hypotheses if v == cls]
                    contradicts = [p for p, v in hypotheses if v != cls]
                    implications[cls] = {
                        "admits": admits,
                        "contradicts": contradicts,
                        "next": (f"apply {condition_name}={cls} and "
                                 f"recompute the trial list: "
                                 f"{', '.join(admits) or 'no procedure'} "
                                 f"becomes admissible; "
                                 f"{', '.join(contradicts) or 'no procedure'} "
                                 f"is contradicted")}
            else:
                if cls == "other":
                    implications[cls] = {
                        "ranks_up": [],
                        "ranks_down": [p for p, _ in hypotheses],
                        "next": ("no procedure ranks up: the observed "
                                 "value matches no required value")}
                else:
                    up = [p for p, v in hypotheses if v == cls]
                    down = [p for p, v in hypotheses if v != cls]
                    implications[cls] = {
                        "ranks_up": up,
                        "ranks_down": down,
                        "next": (f"apply {condition_name}={cls}: "
                                 f"{', '.join(up) or 'no procedure'} "
                                 f"ranks up; "
                                 f"{', '.join(down) or 'no procedure'} "
                                 f"ranks down")}
        expectation_id = self._next_evidence_expectation_id()
        return {
            "expectation_id": expectation_id,
            "condition": condition_name,
            "hard": hard,
            "hypotheses": [{"procedure": p, "requires": v}
                           for p, v in hypotheses],
            "predicted_classes": predicted,
            "implications": implications,
            "source": self.evidence_source.source_id,
            "measurement": question}

    def _answer_condition(self, task, condition_name, span, question,
                          hypotheses, hard=True):
        """U03 — Answer With Frozen Expectation. Answer one
        unknown condition through the evidence source. (a) Build
        the expectation (competing hypotheses, predicted
        classes, the implication of each class, the permitted
        source, the requested measurement) and emit it as an
        EVIDENCE_EXPECTATION trace event — frozen BEFORE the
        source is consulted. (b) Only after that event is
        committed, call the source. (c) Emit an
        EVIDENCE_OBSERVED event with the returned value and its
        provenance. (d) Return (updated_task, observation,
        expectation): updated_task carries conditions
        [condition_name]=value with the condition removed from
        unknown_conditions (a NEW task — TaskRecord is frozen;
        the identity-bearing fields are unchanged). If the
        source raises KeyError, return (task, None, expectation):
        an unanswered request is honest — the expectation is
        frozen, no observation event is emitted, and the run
        escalates rather than guessing. The source is never
        consulted before the expectation, and the validator /
        criteria are never consulted here at all. The whole
        operation runs under its own U03 span, parented on the
        caller's span; the EVIDENCE_EXPECTATION / EVIDENCE_
        OBSERVED events stay on the caller's span (frozen
        placement). Disabled (ablation): the expectation is
        still built and frozen (the audit property is
        preserved), but the source is NOT consulted — return
        (task, None, expectation), honest unanswered, so the
        run escalates; a FUNCTION_DISABLED event records it
        and no U03 span is emitted."""
        tracer = self._tracer
        if self._disabled("U03"):
            self._disable_event("U03", span,
                                reason="ablation: U03 disabled")
            expectation = self._build_expectation(
                condition_name, question, hypotheses, hard)
            ev = tracer.event("EVIDENCE_EXPECTATION", span, expectation)
            expectation["expectation_id"] = ev["event_id"]
            return task, None, expectation
        with self._trace_span(
                "U03", span or "root",
                f"answer {condition_name} with frozen expectation") as u03:
            expectation = self._build_expectation(
                condition_name, question, hypotheses, hard)
            ev = tracer.event("EVIDENCE_EXPECTATION", span, expectation)
            # The returned event carries the real id; the prediction
            # must agree. If it ever disagreed, the observation and
            # the report link to the real id (the certify check
            # matches on the event id derived from the sequence).
            expectation_id = ev["event_id"]
            expectation["expectation_id"] = expectation_id
            try:
                value, provenance = self.evidence_source.answer(
                    task.task_id, condition_name)
            except KeyError:
                self._span_result(u03, "U03", {
                    "condition": condition_name,
                    "expectation_id": expectation_id,
                    "answered": False})
                return task, None, expectation
            observation = {"expectation_id": expectation_id,
                           "condition": condition_name,
                           "value": value,
                           "provenance": provenance}
            tracer.event("EVIDENCE_OBSERVED", span, observation)
            updated = dataclasses.replace(
                task,
                conditions={**task.conditions, condition_name: value},
                unknown_conditions=tuple(
                    c for c in task.unknown_conditions if c != condition_name))
            self._span_result(u03, "U03", {
                "condition": condition_name,
                "expectation_id": expectation_id,
                "answered": True, "value": value})
            return updated, observation, expectation

    # ---- PATHWAY-002: unified measurement selection ----
    def _selector_hypotheses(self, procedures=None, conditions=None):
        """[(registered_id, {condition: (required_value, is_hard)})]
        from registry preconditions, HARD and SOFT, restricted to
        `procedures` (registered ids) and/or `conditions` when
        given. Sorted by procedure id, so the selection is
        deterministic. Unlike _evidence_hypotheses (hard-only,
        untouched), this includes soft preconditions — the
        selector ranks on them; it never treats a soft mismatch
        as a contradiction."""
        out = []
        for entry in sorted(REGISTRY.values(),
                            key=lambda e: e.procedure_id):
            if procedures is not None \
                    and entry.procedure_id not in procedures:
                continue
            conds = {n: (v, h) for n, (v, h) in entry.preconditions.items()
                     if conditions is None or n in conditions}
            if conds:
                out.append((entry.procedure_id, conds))
        return out

    def _selector_method(self, task):
        """The retrieved method record for this task, when the
        pathway provider offers one; None when the provider has
        no such method or returns no record. Read defensively:
        a provider without method_for is a provider without a
        method record, not an error — selection works with or
        without it."""
        provider = self.pathway_provider
        method_for = getattr(provider, "method_for", None)
        if provider is None or not callable(method_for):
            return None
        return method_for(task)

    def _select_measurements(self, task, hypotheses, known, span):
        """U05 — Select Measurements: run the configured
        MeasurementSelector and freeze the ranked selection as an
        EVIDENCE_SELECTION trace event, BEFORE any selected
        measurement is answered. Returns the selection dict. The
        caller answers the selected conditions in ranked order
        via _answer_condition. The selection runs under its own
        U05 span, parented on the caller's span; the
        EVIDENCE_SELECTION event itself stays on the caller's
        span (frozen placement). The caller gates on U05: when
        U05 is disabled this method is not called at all — the
        caller falls back to the no-selector order."""
        with self._trace_span(
                "U05", span or "root",
                f"select measurements for {task.task_id}") as u05:
            self._selection_counter += 1
            method = self._selector_method(task)
            selection = self.measurement_selector.select(
                hypotheses, task, known=known, method=method)
            data = {"selection_id": f"sel-{self._selection_counter:04d}",
                    "task_id": task.task_id,
                    "selected": selection["selected"],
                    "evaluations": selection["evaluations"]}
            self._tracer.event("EVIDENCE_SELECTION", span, data)
            self._span_result(u05, "U05", {
                "selection_id": data["selection_id"],
                "selected": [s["condition"]
                             for s in selection["selected"]],
                "n_evaluations": len(selection["evaluations"])})
            return selection

    def _condition_is_hard(self, condition_name, hypotheses=None):
        """Is this condition a hard (admission) measurement?
        True when every hypothesis requiring it marks it hard;
        False when every such hypothesis marks it soft. Falls
        back to the registry when no hypothesis requires it, and
        to True when nothing requires it at all (legacy hard
        expectations). In this registry a condition is uniformly
        hard or soft (firmware hard, kind soft), so the
        per-condition flag is exact."""
        flags = [h for _p, conds in (hypotheses or [])
                 for n, (_v, h) in conds.items() if n == condition_name]
        if not flags:
            for entry in REGISTRY.values():
                req = entry.preconditions.get(condition_name)
                if req is not None:
                    flags.append(req[1])
        if not flags:
            return True
        return all(flags)

    def _replay_contradiction_abstention(self, report, escalate_because):
        """The first answered pathway-replay request whose
        observation contradicts every procedure in
        escalate_because, or None. Contradiction is hard-only
        (_procedure_contradicted_by): an observed condition that
        is a hard precondition of the procedure with a different
        required value. Returns (request, contradicted,
        observations)."""
        escalate_procs = [p for p, _ in escalate_because]
        if not escalate_procs:
            return None
        for req in report.get("information_requests") or []:
            if req.get("source") != "pathway-replay":
                continue
            if not req.get("answered"):
                continue
            observations = req.get("observation") or {}
            if not observations:
                continue
            contradicted = [p for p in escalate_procs
                            if self._procedure_contradicted_by(p, observations)]
            if len(contradicted) == len(escalate_procs):
                return req, contradicted, observations
        return None

    def _replay_discriminators(self, task, report, u09):
        """Replay the pathway provider's discriminators for this
        task (PATHWAY-001, arm C). The pathway supplies WHICH
        discriminator to ask about, never the answer: each
        replayed request is a fresh request (new expectation
        record, new observation from the source for THIS task).
        Returns the task with every answered observation
        applied. A replayed observation that contradicts every
        procedure requiring that condition stops the replay —
        the normal flow then decides (escalate_because /
        justified abstention through the U10 blocked branch, if
        applicable). No second abstention path is invented here,
        and no new operational-log kinds are added."""
        discriminators = self.pathway_provider.discriminators_for(task)
        if not discriminators:
            return task
        tracer = self._tracer
        with self._trace_span(
                "U10", u09,
                "replay stored pathway discriminators") as u10:
            # PATHWAY-002: when a measurement selector is
            # configured, the replayed discriminators and the
            # retrieved method record become selector inputs;
            # the ranked selection (frozen as EVIDENCE_SELECTION
            # before any answer) decides which discriminators
            # are measured, in which order. Without a selector:
            # every discriminator is answered in provider order
            # (verbatim).
            sel_hyps = None
            hyps_by_condition = {}
            # U05: as in the direct path — the selector runs only
            # when configured AND U05 is not disabled. Disabled:
            # exactly the no-selector fallback (every
            # discriminator, in provider order), with a
            # FUNCTION_DISABLED event recorded.
            if self.measurement_selector is not None \
                    and not self._disabled("U05"):
                sel_hyps = self._selector_hypotheses(
                    conditions=set(discriminators))
                selection = self._select_measurements(
                    task, sel_hyps, dict(task.conditions), u10)
                to_answer = [s["condition"]
                             for s in selection["selected"]]
                for proc, conds in sel_hyps:
                    for name, (value, _hard) in conds.items():
                        hyps_by_condition.setdefault(name, []).append(
                            (proc, value))
            else:
                if self.measurement_selector is not None:
                    self._disable_event(
                        "U05", u10,
                        reason="ablation: U05 disabled")
                to_answer = list(discriminators)
            for condition_name in to_answer:
                if condition_name not in task.unknown_conditions \
                        and condition_name in task.conditions:
                    continue
                if condition_name in hyps_by_condition:
                    hypotheses = hyps_by_condition[condition_name]
                    hard = self._condition_is_hard(condition_name, sel_hyps)
                else:
                    hypotheses = self._evidence_hypotheses(condition_name)
                    hard = True
                procs = sorted(p for p, _ in hypotheses)
                if hard:
                    question = (f"What is {condition_name} for this task? "
                                f"{', '.join(procs)} require it — execution is "
                                f"refused until the mandatory precondition "
                                f"is known.")
                else:
                    question = (f"What is {condition_name} for this task? "
                                f"{', '.join(procs)} rank on it — the "
                                f"measurement distinguishes them.")
                task, observation, expectation = self._answer_condition(
                    task, condition_name, u10, question, hypotheses,
                    hard=hard)
                req = {"missing": [condition_name],
                       "question": question,
                       "procedures": procs,
                       "source": "pathway-replay",
                       "answered": observation is not None,
                       "expectation_id": {condition_name:
                                          expectation["expectation_id"]},
                       "expectation": {condition_name: expectation},
                       "observation": ({condition_name: observation}
                                       if observation is not None else None),
                       "outcome": ("resumed" if observation is not None
                                   else "unanswered")}
                report["information_requests"].append(req)
                if observation is not None and hypotheses and all(
                        v != observation["value"] for _, v in hypotheses):
                    break
        return task

    def _trial_candidates(self, task):
        """The trial list, plus the two cases where the trial must
        NOT run. A procedure is 'pointed at' when it shares at
        least one description word with the task's tags — the
        task's own vocabulary names it.
          - A pointed-at procedure blocked solely by an UNKNOWN
            hard precondition -> information request (ask, don't
            guess). Fires before any trial, even when other
            procedures are eligible: the task is pointing at a
            procedure it cannot have without the missing
            condition, and guessing at unrelated procedures
            instead of asking is the wrong move.
          - EVERY pointed-at procedure blocked (unknown,
            contradicted, policy, or trial safety) -> escalate
            without trialling. Trialling unrelated procedures on
            a task whose answer family is known and barred is
            flailing, not investigation.
        Otherwise the trial list is the eligible procedures,
        ranked by relevance; the bound is the eligible list
        itself (finite, policy-filtered, safety-filtered,
        deduplicated), capped by MAX_TRIAL_ATTEMPTS as a safety
        valve. A trial list that stopped after an arbitrary
        number could never reach a correct procedure whose
        vocabulary does not overlap the task's — which is exactly
        the synonym case the trial exists to solve."""
        candidates, pointed = [], []
        for entry in REGISTRY.values():
            shares = bool(_desc_words(entry.description) & set(task.tags))
            allowed = self.policy.decision(entry.procedure_id)
            hard = check_hard_preconditions(
                entry, task.conditions, task.unknown_conditions,
                task.contradictory_conditions)
            safe = entry.trial_safety == TRIAL_SAFE
            if shares:
                if not allowed:
                    reason = "policy_denied"
                elif not safe:
                    reason = f"trial_safety {entry.trial_safety}"
                elif hard == "unknown":
                    reason = "unknown_preconditions"
                elif hard == "contradicted":
                    reason = "contradicted"
                else:
                    reason = None
                pointed.append((entry, reason))
            if allowed and safe and hard == "satisfied":
                candidates.append(entry)
        unknown_pointed = [(e, r) for e, r in pointed
                           if r == "unknown_preconditions"]
        if unknown_pointed:
            missing = sorted({n for e, _ in unknown_pointed
                              for n, (_, h) in e.preconditions.items()
                              if h and (n in task.unknown_conditions
                                        or n not in task.conditions)})
            procs = sorted(e.procedure_id for e, _ in unknown_pointed)
            return {"candidates": [],
                    "blocked_by_unknown": {
                        "missing": missing,
                        "question": (f"What is {' / '.join(missing)} for this task? "
                                     f"{', '.join(procs)} require it — execution is refused "
                                     f"until the mandatory precondition is known."),
                        "procedures": procs},
                    "escalate_because": None,
                    "pointed_blocks": [(e.procedure_id, r) for e, r in pointed
                                       if r is not None]}
        if pointed and all(r is not None for _, r in pointed):
            return {"candidates": [], "blocked_by_unknown": None,
                    "escalate_because": [(e.procedure_id, r)
                                         for e, r in pointed],
                    "pointed_blocks": [(e.procedure_id, r) for e, r in pointed]}
        candidates = sorted(candidates,
                            key=lambda e: self._trial_rank_key(e, task))
        return {"candidates": candidates, "blocked_by_unknown": None,
                "escalate_because": None, "pointed_blocks": []}

    def _trial_rank_key(self, entry, task):
        """The trial rank key for a candidate, under the
        configured ranker (item 10). "existing" is the Contract
        002 _trial_rank (family, shared, soft, registry order
        for ties). "revised" adds the learned experience:
        demonstrated success in this task's family first
        (a QUALIFIED/UNDER_TEST record for a task in the family),
        then the existing components, then a demonstrated failure
        in the family last (an INCONCLUSIVE/REJECTED record).
        The experience is read from the durable store (the
        latest record per (task, procedure) pair), so the rank
        reflects what the system has actually learned — not a
        claim about it. "graph" (GRAPH-001) keeps the existing
        base and breaks ties by the graph score table: the
        procedure's score from the typed graph built over the
        training executions (validated pathway history, fan-in,
        and — in the motif table — the convergent-sink /
        reciprocal-core component). Returns a tuple that sorts
        ascending."""
        base = _trial_rank(entry, task)
        if self.trial_ranker == "graph":
            return base + (-self.graph_scores.get(
                entry.procedure_id, 0.0),)
        if self.trial_ranker != "revised":
            return base
        success, failure = 0, 0
        for (t, p), r in self.experiences.latest_per_pair().items():
            if p != entry.procedure_id:
                continue
            if r.get("fam") != task.family:
                continue
            if r.get("qual") in ("QUALIFIED", "UNDER_TEST"):
                success = 1
            elif r.get("qual") in ("INCONCLUSIVE", "REJECTED"):
                failure = 1
        # (-success, ...existing..., failure): demonstrated
        # success first, demonstrated failure last.
        return (-success,) + base + (failure,)

    def _done(self, report, task, stage, procedure_id, state):
        report["stage"] = stage
        report["state"] = state
        report["selected"] = procedure_id
        self.trace.append("TASK_DONE", task.task_id, None,
                          {"result": state, "stage": stage,
                           "procedure": procedure_id})
        # TRACE-001: the task ends with its outcome, stage and
        # selected procedure.
        if self._tracer is not None:
            self._tracer.end_task(state, selected=procedure_id,
                                  stage=stage)
        return report

    def _unresolved(self, report, task, state):
        iou_id = f"iou.{task.task_id}"
        self.ious.open_iou(iou_id, f"unresolved:{task.signature}")
        self.trace.append("IOU_OPENED", task.task_id, None, {"iou": iou_id})
        report["iou"] = iou_id
        report["stage"] = "unresolved"
        report["state"] = state
        self.trace.append("TASK_DONE", task.task_id, None,
                          {"result": state, "iou": iou_id})
        # TRACE-001: the IOU is recorded on the root span, and the
        # failure is classified (the EVAL-001 taxonomy, mapped to
        # the directive's classes) so the pathway shows WHY the
        # task did not resolve — and whether the error was
        # recognition, transfer, investigation, authorization,
        # validation or missing information.
        if self._tracer is not None:
            self._tracer.event("IOU_OPENED", "root", {
                "iou": iou_id, "signature": task.signature})
            self._tracer.failure(
                "root", self._failure_class(report, task),
                divergence=self._first_divergence(report, task),
                evidence={"rejected": report["rejected"],
                          "information_requests":
                              report["information_requests"]})
            self._tracer.end_task(state, selected=None, stage="unresolved",
                                  detail={"iou": iou_id})
        return report

    def _failure_class(self, report, task):
        """Map the unresolved outcome to the directive's failure
        classes (TRACE-001 §5), using the EVAL-001 taxonomy
        (contract/eval001.py classify)."""
        correct = self.validator.criteria.get(task.task_id)
        if report["information_requests"]:
            return "missing_information"
        if any(a["observed"] and a["validation"] == "FAIL"
               for a in report["attempts"]):
            return "validation"
        if correct and any(a["procedure"] == correct
                           for a in report["attempts"]):
            return "investigation"
        if correct and any(r["procedure"] == correct and
                           "policy" in (r.get("reason") or "")
                           for r in report["rejected"]):
            return "authorization"
        if correct and any(r["procedure"] == correct
                           for r in report["rejected"]):
            return "recognition"
        return "recognition"

    def _first_divergence(self, report, task):
        """Where the first demonstrable divergence occurred: the
        first rejected candidate that named the correct procedure,
        or the first failed attempt, or the information request."""
        correct = self.validator.criteria.get(task.task_id)
        if report["information_requests"]:
            return {"at": "information_request",
                    "missing": report["information_requests"][0].get("missing")}
        for r in report["rejected"]:
            if correct and r.get("procedure") == correct:
                return {"at": "rejected", "procedure": r["procedure"],
                        "reason": r.get("reason")}
        for a in report["attempts"]:
            if not a["observed"]:
                return {"at": "attempt_failed", "procedure": a["procedure"]}
        return {"at": "unresolved", "procedure": None}

    # ---- qualification lifecycle ----
    def mark_under_test(self, task_id, procedure_id):
        """CANDIDATE -> UNDER_TEST: the procedure becomes visible
        to the transfer stage (at reduced standing) so a transfer
        task can test it. Writes a new block; the old CANDIDATE
        block remains as history."""
        latest = self.experiences.latest_per_pair().get((task_id, procedure_id))
        if latest is None or latest["qual"] != "CANDIDATE":
            raise ValueError(f"cannot mark under test: {task_id}/{procedure_id} "
                             f"is not CANDIDATE")
        return self.experiences.write(
            task_id=task_id, signature=latest["sig"], family=latest["fam"],
            procedure_id=procedure_id, observed_outcome=latest["obs"],
            validator_result=latest["val"], conditions=latest["cond"],
            qualification="UNDER_TEST", source="qualification",
            tags=latest["tags"])

    def qualify(self, task_id, procedure_id, transfer_task_dict):
        """Run a transfer task (a different task whose correct
        procedure is this one) through the pipeline. If the
        transfer stage selects this procedure and the outcome
        validates, write a QUALIFIED block. Returns the transfer
        report."""
        self.mark_under_test(task_id, procedure_id)
        rep = self.run(transfer_task_dict)
        if rep["state"] == "RESOLVED" and rep["selected"] == procedure_id \
                and rep["stage"] == "soso-transfer":
            latest = self.experiences.latest_per_pair().get((task_id, procedure_id))
            self.experiences.write(
                task_id=task_id, signature=latest["sig"], family=latest["fam"],
                procedure_id=procedure_id, observed_outcome=latest["obs"],
                validator_result=latest["val"], conditions=latest["cond"],
                qualification="QUALIFIED", source="qualification",
                tags=latest["tags"])
        return rep

    def _run_disposition(self, task_dict, result, tracer):
        authority = task_dict.get("authority", 0)
        if type(authority) is not int or authority != 0:
            raise PermissionError(f"AUTH:0 violation: nonzero or invalid authority {authority!r} rejected")
        hold_store = HoldStore(self.disposition_store_path)
        trace_dir = os.path.join(self.root, "disposition_traces")
        os.makedirs(trace_dir, exist_ok=True)
        exec_id = result["execution_id"]
        recorder = CognitiveRecorder(
            os.path.join(trace_dir, f"{exec_id}.jsonl"),
            run_id=exec_id,
            task_id=str(task_dict.get("task_id") or exec_id),
        )
        out = {"execution_id": exec_id, "hold_store_path": self.disposition_store_path}
        if "disposition_observation" in task_dict:
            obs = dict(task_dict["disposition_observation"])
            obs.setdefault("obs_id", str(task_dict.get("task_id") or exec_id))
            events = evaluate_motor_branches(obs, execution_id=exec_id)
            prop = propagate_authorized_results(
                events,
                DEFAULT_DISPOSITION_EDGES,
                observation=obs,
                hold_store=hold_store,
                evidence_available=task_dict.get("evidence"),
                recorder=recorder,
            )
            self.trace.append("DISPOSITION_EVALUATED", task_dict.get("task_id"), None, {
                "execution_id": exec_id,
                "dispositions": [e["disposition"] for e in events],
                "persisted_holds": prop["persisted_holds"],
            })
            out["evaluation"] = {
                "events": events,
                "propagation": prop,
                "explanation": explain_disposition_pathway(events, prop),
            }
        if "deliver_evidence" in task_dict:
            react = deliver_evidence_and_reactivate(
                hold_store,
                task_dict["deliver_evidence"],
                authorized_edges=DEFAULT_DISPOSITION_EDGES,
                recorder=recorder,
            )
            self.trace.append("DISPOSITION_REACTIVATION", task_dict.get("task_id"), None, {
                "execution_id": exec_id,
                "reactivated": [r["hold_id"] for r in react["reactivated"]],
                "still_pending": react["still_pending"],
                "duplicate_blocked": react["duplicate_blocked"],
            })
            out["reactivation"] = react
        if task_dict.get("disposition_gate") and self._cl is not None and hasattr(self._cl, "apply_disposition_gate"):
            gate = self._cl.apply_disposition_gate(
                {"stage": result.get("stage") or "escalated", "procedures": [result["selected"]] if result.get("selected") else []},
                execution_id=exec_id,
                obs_id=str(task_dict.get("task_id") or exec_id),
                authority=authority,
                hold_store=hold_store,
                recorder=recorder,
            )
            out["cognitive_gate"] = gate
        out["holds"] = hold_store.list_holds()
        out["causal_trace"] = recorder.replay()
        out["trace_digest"] = recorder.digest()
        return out

    def _run_l24_causal(self, task_dict, result, tracer):
        """L24-CAUSAL-001: L24 -> U10 -> U09 -> U37 -> L15 -> L21 runtime delivery."""
        if self._disabled("L24"):
            self._disable_event("L24", "root", reason="ablation: L24 disabled")
            return {
                "l24_activated": False,
                "disabled": True,
                "l24_outputs": [],
                "u10_consumed": False,
                "u09_consumed": False,
                "decision_before": result.get("selected"),
                "decision_after": result.get("selected"),
                "decision_changed_by_l24": False,
            }

        import sqlite3
        db_path = os.path.join(self.root, "l24_methods.db")
        conn = sqlite3.connect(db_path)
        try:
            if self._cl is not None and hasattr(self._cl, "recognize_cross_task_structure"):
                from adapters import durable_memory as _dm
                _dm.ensure_tables(conn)
                t_feats = list(task_dict.get("l24_features") or [])
                t_conds = dict(task_dict.get("conditions") or {})
                m_recs = task_dict.get("l24_method_records")
                with self._trace_span("L24", "root", "cross_task_structural_recognition") as l24_span:
                    l24_out = self._cl.recognize_cross_task_structure(
                        conn,
                        target_case_id=str(task_dict.get("task_id") or result["execution_id"]),
                        target_features=t_feats,
                        target_conditions=t_conds,
                        method_records=m_recs,
                        authority=task_dict.get("authority", 0),
                    )
                    tracer.event("L24_RECOGNITION", l24_span, {"match_count": len(l24_out)})

                u10_consumed = False
                u09_consumed = False
                ev_reqs = []
                ev_recv = {}
                decision_before = result.get("selected")
                decision_after = decision_before

                # U10 consumes HOLD + next_evidence_request
                hold_items = [o for o in l24_out if o["disposition"] == "HOLD" and o.get("next_evidence_request")]
                if hold_items and not self._disabled("U10"):
                    with self._trace_span("U10", "root", "consume L24 HOLD evidence request") as u10_span:
                        u10_consumed = True
                        prov = task_dict.get("l24_evidence_provider") or self.evidence_source
                        for hi in hold_items:
                            meas = hi["next_evidence_request"]["measurement"]
                            ev_reqs.append(hi["next_evidence_request"])
                            if callable(prov):
                                val = prov(meas, task_dict)
                                if val is not None:
                                    ev_recv[meas] = val
                            elif isinstance(prov, dict) and meas in prov:
                                ev_recv[meas] = prov[meas]
                        tracer.event("U10_EVIDENCE_CONSUMED", u10_span, {"requested": ev_reqs, "received": ev_recv})

                    if ev_recv:
                        updated_conds = {**t_conds, **ev_recv}
                        with self._trace_span("L24", "root", "reactivate L24 after U10 evidence") as l24_rspan:
                            l24_out = self._cl.recognize_cross_task_structure(
                                conn,
                                target_case_id=str(task_dict.get("task_id") or result["execution_id"]),
                                target_features=t_feats,
                                target_conditions=updated_conds,
                                method_records=m_recs,
                                authority=task_dict.get("authority", 0),
                            )
                            tracer.event("L24_REACTIVATED", l24_rspan, {"dispositions": [o["disposition"] for o in l24_out]})
                    else:
                        # Persist HOLD in HoldStore without mutating original observation
                        hs = HoldStore(self.disposition_store_path)
                        for idx, hi in enumerate(hold_items):
                            hs.save_hold({
                                "hold_id": f"HOLD-L24-{result['execution_id']}-{idx}",
                                "status": "PENDING",
                                "source_event_id": f"{result['execution_id']}-L24-{idx}",
                                "execution_id": result["execution_id"],
                                "function_id": "U10",
                                "source_function": "L24",
                                "missing_evidence": [hi["next_evidence_request"]["measurement"]],
                                "continuation_context": {"obs_id": str(task_dict.get("task_id")), "facts": dict(t_conds)},
                                "reactivation_count": 0,
                                "reactivated_events": [],
                            })

                # U09 consumes PASS / DROP outputs and updates candidate selection -> U37 -> L15 -> L21
                pass_items = [o for o in l24_out if o["disposition"] == "PASS" and o.get("recommended_procedure")]
                drop_items = [o for o in l24_out if o["disposition"] == "DROP"]
                if (pass_items or drop_items) and not self._disabled("U09"):
                    with self._trace_span("U09", "root", "consume L24 PASS/DROP for candidate ranking") as u09_span:
                        u09_consumed = True
                        tracer.event("U09_L24_CONSUMED", u09_span, {
                            "pass": [p["recommended_procedure"] for p in pass_items],
                            "drop_reasons": [d["reason"] for d in drop_items],
                        })
                    if pass_items:
                        chosen_proc = pass_items[0]["recommended_procedure"]
                        entry = reg_get(chosen_proc)
                        ok, _st, _det = self._admit(entry, task_mod.make_task(
                            task_id=str(task_dict.get("task_id") or result["execution_id"]),
                            signature=str(task_dict.get("signature") or "sig-l24"),
                            family=str(task_dict.get("family") or "l24"),
                            tags=list(task_dict.get("tags") or []),
                            conditions={**t_conds, **ev_recv},
                        ), u09_span)
                        if ok:
                            with self._trace_span("U37", u09_span, f"decide {chosen_proc} (L24)") as dspan:
                                with self._trace_span("L15", dspan, f"execute {chosen_proc} (L24)") as espan:
                                    t_obj = task_mod.make_task(
                                        task_id=str(task_dict.get("task_id") or result["execution_id"]),
                                        signature=str(task_dict.get("signature") or "sig-l24"),
                                        family=str(task_dict.get("family") or "l24"),
                                        tags=list(task_dict.get("tags") or []),
                                        conditions={**t_conds, **ev_recv},
                                    )
                                    observed, validation, _ = self._execute(t_obj, entry, espan)
                                with self._trace_span("L21", espan, f"record {chosen_proc} (L24)") as rspan:
                                    self._record(t_obj, entry, observed, validation, "CANDIDATE", "l24-causal", rspan)
                                if observed and validation == "PASS":
                                    decision_after = chosen_proc
                                    result["selected"] = chosen_proc
                                    result["state"] = "RESOLVED"
                                    result["stage"] = "l24-causal"

                return {
                    "l24_activated": True,
                    "disabled": False,
                    "l24_outputs": l24_out,
                    "u10_consumed": u10_consumed,
                    "evidence_requests": ev_reqs,
                    "evidence_received": ev_recv,
                    "u09_consumed": u09_consumed,
                    "decision_before": decision_before,
                    "decision_after": decision_after,
                    "decision_changed_by_l24": decision_after != decision_before,
                }
        finally:
            conn.close()
        return {"l24_activated": False}
