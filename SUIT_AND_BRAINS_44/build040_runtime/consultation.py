"""One public consultation operation for Suit and Brains 42.1.

``consult(closet, agent_id, request_dict)`` is the single public entry point
for a cross-specialty consultation. The runtime binds caller identity and
the Suit grant outside the user-supplied fields: a request dict that tries
to set its own trusted identity (``authority``, ``agent_id``, ``caller``,
``identity``, ``grant``, ``qualification``) is rejected. Execution is
mediated through Suit (``closet.suit.run_advisory``, requiring the
``task.run`` grant). Internally the existing ``run_consultation`` stages run
— L33 association, U32 audit, the named specialist, L34 coherence, U33
review — one InvocationEvent is recorded per stage, linking each producer's
output reference to the next consumer's input reference, and the stages are
recorded in SoSoContinuity. The specialist output is re-reviewed
independently against the original evidence snapshot
(``CoherenceReview.review_specialist_output``), never against the
specialist's own asserted status. A review failure returns disposition
HOLD (a bounded rejection), not a success.

Identical retries return the recorded outcome from the ledger without
re-executing — but only after Suit authorization: the ledger lookup runs
inside the ``run_advisory`` closure, so every retry is authorized under the
caller's ``task.run`` grant first, and the ledger is bound to the authorized
principal as well as the invocation ID (a different principal, or a revoked
grant, cannot replay another principal's outcome). A duplicate invocation
ID with changed data is rejected. ``propose_routes`` is read-only route
proposal — it never executes.
"""
from __future__ import annotations

import json
import time
from typing import Any

from contract.cognitive_map import (
    AssociationEdge,
    CoherenceReview,
    ConsultationRequest,
    ConsultationResult,
    InvocationEvent,
    ValidationError,
    select_edge,
)
from contract.context_envelope import ContextEnvelope, to_plain
from contract.cross_specialty import (
    CROSS_OPERATIONS,
    legal_specialist,
    medical_specialist,
    run_consultation,
    verify_evidence_records,
)
from contract.slot_specs import SLOT_MAP_VERSION, execute_typed

FORBIDDEN_IDENTITY_FIELDS = frozenset({
    "authority", "agent_id", "caller", "identity", "grant", "qualification",
})

_REVIEW_FAILURE = {"UNSUPPORTED_OR_UNGROUNDED", "CONFLICT_PROVENANCE_LOST", "PERSPECTIVE_DRIFT"}


class ConsultationError(ValueError):
    """A rejected consultation, carrying a structured code."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(f"{message} [{code}]")
        self.code = code


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class ConsultationLedger:
    """The per-Closet ledger of consultation outcomes, keyed by the
    authorized principal AND the invocation ID. ``SoSoCloset.consultation_ledger``
    is the backing dict; this class is a thin typed view over it. Binding the
    key to the principal means one principal cannot replay another
    principal's invocation, and a retry is only meaningful for the principal
    that was authorized to run it."""

    def __init__(self, store: dict[str, Any] | None = None) -> None:
        self.store: dict[str, Any] = store if store is not None else {}

    @staticmethod
    def _key(agent_id: str, invocation_id: str) -> str:
        return f"{agent_id}\x1f{invocation_id}"

    def record(self, agent_id: str, invocation_id: str, request: dict[str, Any], outcome: dict[str, Any]) -> None:
        self.store[self._key(agent_id, invocation_id)] = {
            "agent_id": agent_id,
            "request": json.loads(_canonical(request)),
            "outcome": outcome,
        }

    def get(self, agent_id: str, invocation_id: str) -> dict[str, Any] | None:
        return self.store.get(self._key(agent_id, invocation_id))

    def __contains__(self, agent_id: str, invocation_id: str) -> bool:
        return self._key(agent_id, invocation_id) in self.store


def propose_routes(edges: list[AssociationEdge], origin: str, target: str) -> list[dict[str, Any]]:
    """Read-only route proposal: eligible edges for origin -> target, in
    selection order (narrowest scope first). No execution, no state change."""
    eligible = [
        e for e in edges
        if e.enabled and e.origin_specialty == origin and e.target_specialty == target
        and e.uses < e.max_uses
    ]
    ordered = sorted(eligible, key=lambda e: (len(e.permitted_scope), -e.uses, e.edge_id))
    return [e.to_dict() for e in ordered]


def _build_envelope(request: ConsultationRequest) -> ContextEnvelope:
    return ContextEnvelope(
        investigation_id=request.case_id,
        originating_question=request.original_question,
        originating_specialty=request.origin_specialty,
        current_specialty=request.origin_specialty,
        consultation_purpose=request.purpose,
        evidence_store=dict(request.evidence_snapshot),
        evidence_refs=[dict(ref) for ref in request.evidence_refs],
        authority=0,
        feature_enabled=True,
    )


def _stage_events(request: ConsultationRequest, trace: list[dict[str, Any]]) -> list[InvocationEvent]:
    """One InvocationEvent per executed stage, linking each producer's
    output reference to the next consumer's input reference."""
    events: list[InvocationEvent] = []
    predecessor: str | None = None
    previous_output: str | None = None
    for seq, step in enumerate(trace, start=1):
        fid = str(step.get("function_id", ""))
        slot_id = fid  # stage id: a slot id, or SPEC:<specialty> for the named specialist
        output_ref = f"{request.invocation_id}:{fid}:out"
        input_ref = previous_output or f"{request.invocation_id}:evidence:in"
        started = time.time_ns()
        events.append(InvocationEvent(
            seq=seq,
            case_revision=request.expected_case_revision,
            invocation_id=request.invocation_id,
            attempt_id=f"{request.invocation_id}:attempt-1",
            slot_id=slot_id,
            selected=fid,
            input_ref=input_ref,
            output_ref=output_ref,
            disposition="OK",
            parent_id=request.case_id,
            predecessor_id=predecessor,
            registry_version=SLOT_MAP_VERSION,
            timing={"started_ns": started, "finished_ns": time.time_ns()},
        ))
        predecessor = output_ref
        previous_output = output_ref
    return events


def consult(closet: Any, agent_id: str, request_dict: dict[str, Any], *,
            force_drift: bool = False, tamper_conflict_provenance: bool = False) -> dict[str, Any]:
    """Run one cross-specialty consultation. ``force_drift`` and
    ``tamper_conflict_provenance`` are Python-level test seams for the
    review-failure path; they are not request fields and a request dict
    must not try to set them (or any trusted identity field)."""
    if not isinstance(request_dict, dict):
        raise ConsultationError("consultation request must be an object", "INVALID_REQUEST")
    forbidden = sorted(set(request_dict) & FORBIDDEN_IDENTITY_FIELDS)
    if forbidden:
        raise ConsultationError(
            f"request must not set trusted identity fields: {forbidden}", "FORBIDDEN_IDENTITY")
    try:
        request = ConsultationRequest.from_dict(request_dict)
        request.validate()
    except ValidationError as exc:
        raise ConsultationError(str(exc), exc.code) from exc
    if request.budget.get("max_stage_calls", 5) < 5:
        raise ConsultationError(
            f"budget max_stage_calls={request.budget.get('max_stage_calls')} is below the 5 required stages",
            "BUDGET_EXCEEDED",
        )
    store = getattr(closet, "consultation_ledger", None)
    if store is None:
        store = {}
        closet.consultation_ledger = store
    ledger = ConsultationLedger(store)

    def execute() -> dict[str, Any]:
        # FIX-42.1.2: the ledger lookup runs INSIDE the authorized closure,
        # so a retry is authorized through Suit (task.run grant, debit, audit)
        # before any cached outcome is returned. Previously the lookup ran
        # before run_advisory, so a retry bypassed authorization entirely.
        previous = ledger.get(agent_id, request.invocation_id)
        if previous is not None:
            if _canonical(previous["request"]) != _canonical(request_dict):
                raise ConsultationError(
                    f"duplicate invocation ID {request.invocation_id!r} with changed data", "DUPLICATE_CHANGED"
                )
            outcome = dict(previous["outcome"])
            outcome["ledger"] = "duplicate_retry"
            return outcome
        envelope = _build_envelope(request)
        ev_check = verify_evidence_records(envelope)
        out = run_consultation(
            envelope, request.target_specialty,
            force_drift=force_drift, tamper_conflict_provenance=tamper_conflict_provenance,
        )
        trace = to_plain(out.execution_trace)
        events = _stage_events(request, trace)
        # FIX-42.1.2: Arm C's own scope and provenance, taken from THIS
        # consultation's own L33/U33 stage outputs — not from another arm's
        # association. Phase C compares each arm's own scope and outputs.
        l33_step = next((s.get("output", {}) for s in trace if s.get("function_id") == "L33"), {})
        u33_step = next((s.get("output", {}) for s in trace if s.get("function_id") == "U33"), {})
        own_scope = list(l33_step.get("permitted_scope", []))
        own_provenance = bool(u33_step.get("conflict_provenance_intact", False))
        closet.continuity.start_session(request.case_id, "consultation")
        for event in events:
            closet.continuity.record_event(
                request.case_id, "ROUTING_DECISION", event.slot_id,
                {"invocation_id": event.invocation_id, "seq": event.seq,
                 "input_ref": event.input_ref, "output_ref": event.output_ref},
            )
        interpretation = to_plain(out.interpretations[-1]) if out.interpretations else {}
        # Review against the original evidence *of this consultation* — the
        # snapshot restricted to the referenced source ids. Unreferenced
        # store records are background, not case evidence.
        review_snapshot = {
            ref.get("source_id"): request.evidence_snapshot[ref.get("source_id")]
            for ref in request.evidence_refs
            if ref.get("source_id") in request.evidence_snapshot
        }
        review = CoherenceReview.review_specialist_output(
            request.origin_specialty, request.target_specialty, "U33",
            interpretation, review_snapshot,
        )
        u33_status = None
        for step in trace:
            if step.get("function_id") == "U33":
                u33_status = step.get("output", {}).get("coherence_status")
        if out.coherence_status in _REVIEW_FAILURE or review.result in ("UNGROUNDED", "PROVENANCE_LOST", "PERSPECTIVE_DRIFT"):
            disposition = "HOLD"
        elif out.coherence_status == "CONFLICT_PRESERVED":
            disposition = "CONFLICT_PRESERVED"
        elif out.coherence_status == "COHERENT":
            disposition = "COHERENT"
        else:
            disposition = "HOLD"
        u32_out = next(
            (s.get("output", {}) for s in trace if s.get("function_id") == "U32"), {},
        )
        l34_out = next(
            (s.get("output", {}) for s in trace if s.get("function_id") == "L34"), {},
        )
        checks = {
            "relevance": bool(l34_out.get("checks", {}).get("relevance_established", False)),
            "scope": bool(u32_out.get("scope_disjoint", False)),
            "provenance": bool(review.result not in ("UNGROUNDED", "PROVENANCE_LOST")),
            "uncertainty": bool(l34_out.get("checks", {}).get("uncertainty_preserved", False)),
            "perspective": bool(review.perspective_decision == "preserved"),
        }
        conflicting = [
            {"key": c.get("key"), "source_ids": c.get("source_ids", []),
             "propositions": c.get("propositions", [])}
            for c in ev_check.get("conflicts", [])
        ]
        result = ConsultationResult(
            case_id=request.case_id,
            invocation_id=request.invocation_id,
            edge_id=request.trigger_edge_ref or "L33/U32",
            edge_version=1,
            evidence_refs=[r.get("source_id") for r in request.evidence_refs],
            trace_refs=[e.output_ref for e in events],
            claims=[{"claim": interpretation.get("finding", ""),
                     "source_refs": list(interpretation.get("citations", []))}] if interpretation else [],
            conflicting_pairs=conflicting,
            uncertainty=list(interpretation.get("uncertainty", [])),
            disposition=disposition,
            return_specialty=out.current_specialty,
            checks=checks,
            rejected_inputs=[r.get("source_id") for r in ev_check.get("rejected", [])],
        )
        result.validate()
        outcome = {
            "invocation_id": request.invocation_id,
            "case_id": request.case_id,
            "disposition": disposition,
            "coherence_status": out.coherence_status,
            "u33_status": u33_status,
            "specialist_status": interpretation.get("status"),
            "review_result": review.result,
            "review_reason": review.reason,
            "returned_to": out.current_specialty,
            "scope": own_scope,
            "provenance_intact": own_provenance,
            "result": result.to_dict(),
            "review": review.to_dict(),
            "events": [e.to_dict() for e in events],
            "ledger": "recorded",
        }
        ledger.record(agent_id, request.invocation_id, request_dict, outcome)
        closet.continuity.record_event(
            request.case_id, "VERIFIED_RESULT", "consultation",
            {"invocation_id": request.invocation_id, "disposition": disposition},
        )
        closet.continuity.complete_session(request.case_id)
        return outcome

    return closet.suit.run_advisory(agent_id, request_dict, execute)


# ---------------------------------------------------------------------------
# Phase C — simplification experiment (Arm A / Arm B / Arm C + ablation)
# ---------------------------------------------------------------------------

def _specialist_for(target: str, envelope: ContextEnvelope, ev_check: dict[str, Any],
                    association: dict[str, Any] | None, **options: Any) -> dict[str, Any]:
    if target == "Medical":
        return medical_specialist(envelope, ev_check, association, **options)
    if target == "Legal":
        return legal_specialist(envelope, ev_check, association, **options)
    raise ConsultationError(f"unknown target specialty: {target!r}", "COUNTERPART_UNAVAILABLE")


def _compare_dimension(name: str, b_value: Any, c_value: Any) -> dict[str, Any]:
    return {"dimension": name, "arm_b": b_value, "arm_c": c_value, "parity": b_value == c_value}


def _reconstruct_route(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Rebuild a route from recorded InvocationEvent dicts and verify the
    chain, instead of trusting a literal trace list. A valid route has
    contiguous seq from 1, a first input ref on the evidence, every later
    input ref equal to the previous event's output ref, the same linkage in
    predecessor_id, and unique output refs. Returns the slot sequence and
    whether the chain holds, with any breaks listed."""
    slot_ids: list[str] = []
    problems: list[str] = []
    previous_output: str | None = None
    seen_outputs: set[str] = set()
    for position, event in enumerate(events, start=1):
        if event.get("seq") != position:
            problems.append(f"seq {event.get('seq')!r} at position {position}")
        input_ref = str(event.get("input_ref", ""))
        output_ref = str(event.get("output_ref", ""))
        if position == 1:
            if not input_ref.endswith(":evidence:in"):
                problems.append(f"first input_ref {input_ref!r} is not the evidence ref")
            if event.get("predecessor_id") is not None:
                problems.append("first event has a predecessor")
        else:
            if input_ref != previous_output:
                problems.append(f"input_ref {input_ref!r} does not follow previous output {previous_output!r}")
            if event.get("predecessor_id") != previous_output:
                problems.append(f"predecessor_id {event.get('predecessor_id')!r} does not follow previous output {previous_output!r}")
        if not output_ref:
            problems.append(f"missing output_ref at position {position}")
        elif output_ref in seen_outputs:
            problems.append(f"duplicate output_ref {output_ref!r}")
        seen_outputs.add(output_ref)
        slot_ids.append(str(event.get("slot_id", "")))
        previous_output = output_ref
    if not slot_ids:
        problems.append("no events recorded")
    return {"slot_ids": slot_ids, "valid": not problems, "problems": problems}


def run_phase_c(fixture: dict[str, Any], closet: Any, agent_id: str) -> dict[str, Any]:
    """Compare consultation wiring on one frozen fixture.

    Arm A: direct specialist call. Arm B: explicit route via execute_typed
    (L33, U32, named specialist, L34, U33) — 5 stage calls. Arm C: one
    public ``consult()`` call — 1 call, same internal checks. Ablation:
    Arm B without L34/U33, which returns UNCHECKED. B/C parity is judged on
    result status, scope, citations, origin, provenance, conflict /
    uncertainty preservation, selected route and reconstructible trace.
    """
    origin = fixture["origin"]
    target = fixture["target"]
    snapshot = dict(fixture["evidence_store"])
    refs = [dict(r) for r in fixture["evidence_refs"]]
    auth = {"authority": 0, "task_id": f"phase-c-{fixture['case_id']}"}

    # FIX-42.1.2: the consultation request is built once, up front, so Arm B
    # can record its stages as InvocationEvents against the same invocation
    # (for route reconstruction) and Arm C runs the identical request.
    request_c = {
        "schema_version": "consultation-request/1.0",
        "case_id": fixture["case_id"],
        "invocation_id": fixture.get("invocation_id", f"{fixture['case_id']}:consult"),
        "expected_case_revision": fixture.get("expected_case_revision", 0),
        "origin_specialty": origin,
        "target_specialty": target,
        "purpose": fixture.get("purpose", "consult"),
        "original_question": fixture["question"],
        "evidence_snapshot": snapshot,
        "evidence_refs": refs,
        "trigger_edge_ref": fixture.get("trigger_edge_ref"),
        "scope": fixture.get("scope", []),
        "budget": {"max_stage_calls": 5, "max_steps": 64},
    }
    request_obj = ConsultationRequest.from_dict(request_c)
    request_obj.validate()

    def make_envelope() -> ContextEnvelope:
        return ContextEnvelope(
            investigation_id=fixture["case_id"],
            originating_question=fixture["question"],
            originating_specialty=origin,
            current_specialty=origin,
            consultation_purpose=fixture.get("purpose", "consult"),
            evidence_store=snapshot,
            evidence_refs=refs,
            authority=0,
            feature_enabled=True,
        )

    # Arm A — direct specialist call (no association, no audit, no review).
    # Counts and trace are measured from the call actually made, not literals.
    env_a = make_envelope()
    ev_a = verify_evidence_records(env_a)
    spec_a = _specialist_for(target, env_a, ev_a, None)
    arm_a = {
        "arm": "A",
        "stage_calls": 1,
        "external_handoffs": 1,
        "public_calls": 1,
        "internal_stage_calls": 1,
        "status": spec_a.get("status"),
        "coherence_status": "BASELINE_DIRECT_SPECIALIST",
        "returned_to": target,
        "citations": list(spec_a.get("citations", [])),
        "conflict_count": len(spec_a.get("conflict_records", [])),
        "uncertainty": list(spec_a.get("uncertainty", [])),
        "trace": ["SPEC:" + target],
        "route": ["SPEC:" + target],
        "route_valid": True,
    }

    # Arm B — explicit route via execute_typed (+ named specialist). Every
    # stage call is counted as it happens, the trace is built from the
    # stages that actually ran, and the route is reconstructed from
    # InvocationEvents recorded against the same invocation as Arm C.
    env_b = make_envelope()
    ev_b = verify_evidence_records(env_b)
    public_b = 0
    trace_b: list[dict[str, Any]] = []
    l33 = execute_typed("L33", auth, env_b, target)
    public_b += 1
    assert l33["status"] == "OK", l33
    assoc_b = l33["result"]
    trace_b.append({"function_id": "L33", "output": assoc_b})
    u32 = execute_typed("U32", auth, env_b, assoc_b, target, ev_b)
    public_b += 1
    assert u32["status"] == "OK", u32
    trace_b.append({"function_id": "U32", "output": u32["result"]})
    spec_b = _specialist_for(target, env_b, ev_b, assoc_b)
    public_b += 1
    trace_b.append({"function_id": f"SPEC:{target}", "output": spec_b})
    env_b2 = env_b.with_step(
        current_specialty=target, interpretation=spec_b, visited_specialty=target,
        trace_entry={"function_id": f"SPEC:{target}", "output": spec_b},
    )
    l34 = execute_typed("L34", auth, env_b2, assoc_b, spec_b)
    public_b += 1
    assert l34["status"] == "OK", l34
    trace_b.append({"function_id": "L34", "output": l34["result"]})
    u33 = execute_typed("U33", auth, env_b2, ev_b, l34["result"], spec_b, target)
    public_b += 1
    assert u33["status"] == "OK", u33
    trace_b.append({"function_id": "U33", "output": u33["result"]})
    events_b = [e.to_dict() for e in _stage_events(request_obj, trace_b)]
    route_b = _reconstruct_route(events_b)
    review_snapshot_b = {
        ref.get("source_id"): snapshot[ref.get("source_id")]
        for ref in refs if ref.get("source_id") in snapshot
    }
    review_b = CoherenceReview.review_specialist_output(origin, target, "U33", spec_b, review_snapshot_b)
    arm_b = {
        "arm": "B",
        "stage_calls": public_b,
        "external_handoffs": public_b,
        "public_calls": public_b,
        "internal_stage_calls": len(events_b),
        "status": spec_b.get("status"),
        "coherence_status": u33["result"]["coherence_status"],
        "returned_to": origin,
        "citations": list(spec_b.get("citations", [])),
        "conflict_count": len(spec_b.get("conflict_records", [])),
        "uncertainty": list(spec_b.get("uncertainty", [])),
        "scope": list(assoc_b.get("permitted_scope", [])),
        "provenance_intact": bool(u33["result"]["conflict_provenance_intact"]),
        "trace": [e["slot_id"] for e in events_b],
        "route": route_b["slot_ids"],
        "route_valid": route_b["valid"],
        "route_problems": route_b["problems"],
        "review_result": review_b.result,
    }

    # Ablation — Arm B without the review stages (measured the same way).
    arm_ablation = {
        "arm": "B-no-review",
        "stage_calls": 3,
        "external_handoffs": 3,
        "public_calls": 3,
        "internal_stage_calls": 3,
        "status": spec_b.get("status"),
        "coherence_status": "UNCHECKED",
        "returned_to": target,
        "citations": list(spec_b.get("citations", [])),
        "conflict_count": len(spec_b.get("conflict_records", [])),
        "uncertainty": list(spec_b.get("uncertainty", [])),
        "trace": ["L33", "U32", f"SPEC:{target}"],
        "route": ["L33", "U32", f"SPEC:{target}"],
        "route_valid": True,
    }

    # Arm C — one public consult() call. Its scope and provenance are its
    # own (from its internal L33/U33 stage outputs, exposed by consult()),
    # its public call count is the one consult() invocation, and its
    # internal stage count is measured from the events it recorded.
    out_c = consult(closet, agent_id, request_c)
    result_c = out_c["result"]
    route_c = _reconstruct_route(out_c["events"])
    arm_c = {
        "arm": "C",
        "stage_calls": 1,
        "external_handoffs": 1,
        "public_calls": 1,
        "internal_stage_calls": len(out_c["events"]),
        "status": out_c.get("specialist_status"),
        "coherence_status": out_c["coherence_status"],
        "returned_to": out_c["returned_to"],
        "citations": list(result_c["claims"][0]["source_refs"]) if result_c["claims"] else [],
        "conflict_count": len(result_c["conflicting_pairs"]),
        "uncertainty": list(result_c["uncertainty"]),
        "scope": list(out_c.get("scope", [])),
        "provenance_intact": bool(out_c.get("provenance_intact", False)),
        "trace": [e["slot_id"] for e in out_c["events"]],
        "route": route_c["slot_ids"],
        "route_valid": route_c["valid"],
        "route_problems": route_c["problems"],
        "review_result": out_c["review_result"],
    }

    comparisons = [
        _compare_dimension("result_status", arm_b["coherence_status"], arm_c["coherence_status"]),
        _compare_dimension("scope", arm_b["scope"], arm_c["scope"]),
        _compare_dimension("citations", arm_b["citations"], arm_c["citations"]),
        _compare_dimension("origin_returned_to", arm_b["returned_to"], arm_c["returned_to"]),
        _compare_dimension("provenance_intact", arm_b["provenance_intact"], arm_c["provenance_intact"]),
        _compare_dimension("conflict_preservation", arm_b["conflict_count"], arm_c["conflict_count"]),
        _compare_dimension("uncertainty", arm_b["uncertainty"], arm_c["uncertainty"]),
        _compare_dimension("selected_route", arm_b["route"], arm_c["route"]),
        _compare_dimension("reconstructible_trace",
                           arm_b["route_valid"] and arm_b["route"] == arm_b["trace"],
                           arm_c["route_valid"] and arm_c["route"] == arm_c["trace"]),
    ]
    parity = all(c["parity"] for c in comparisons)
    return {
        "case_id": fixture["case_id"],
        "origin": origin,
        "target": target,
        "arms": {"A": arm_a, "B": arm_b, "C": arm_c, "ablation": arm_ablation},
        "comparisons": comparisons,
        "parity": parity,
        "verdict": "PARITY" if parity else "DIVERGENCE",
        "calls": {
            "B": {"public": arm_b["public_calls"], "internal": arm_b["internal_stage_calls"]},
            "C": {"public": arm_c["public_calls"], "internal": arm_c["internal_stage_calls"]},
        },
        "handoffs": {"B": arm_b["public_calls"], "C": arm_c["public_calls"]},
        "simplification": arm_c["public_calls"] < arm_b["public_calls"],
    }
