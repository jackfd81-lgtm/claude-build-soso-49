"""Versioned structures and the Self-Aware Cognitive Map (42.1).

Seven versioned structures, each with a schema_version tag, producer /
consumer ownership, ``to_dict`` / ``from_dict`` round trip and ``validate()``
raising ``ValidationError`` (a ``ValueError`` carrying a ``code``):

* SlotSpec — re-exported from ``contract.slot_specs`` (slot-spec/1.0).
* AssociationEdge (association-edge/1.0) — a cross-specialty route with a
  measured uses counter; persisted through the CRAM-PU adapter.
* ConsultationRequest (consultation-request/1.0).
* ConsultationResult (consultation-result/1.0).
* InvocationEvent (invocation-event/1.0).
* ResolutionRecord (resolution-record/1.0).
* CoherenceReview (coherence-review/1.0) — reviews specialist output
  against the original evidence snapshot, never against the specialist's
  own asserted status.

Phase A (passive replay projection) and Phase B (advisory suggestions,
matching, reconciliation) of the Self-Aware Cognitive Map live here too.
Phase C (the Arm A / Arm B / Arm C comparison) lives in
``build040_runtime.consultation`` because it drives the public ``consult()``
operation. No new slot IDs are introduced anywhere in this module.
Claims and provenance remain AUTH:0; an input must not set its own trusted
identity. Raw evidence stays distinct from derived claims.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from contract.slot_specs import SlotSpec  # noqa: F401  (re-exported structure 1)

__all__ = [
    "SlotSpec",
    "ValidationError",
    "AssociationEdge",
    "ConsultationRequest",
    "ConsultationResult",
    "InvocationEvent",
    "ResolutionRecord",
    "CoherenceReview",
    "store_edge",
    "load_edge",
    "record_use",
    "select_edge",
    "CognitiveMap",
    "InvestigationRecorder",
    "RelationshipMapper",
    "OpeningSequence",
    "ClosingReview",
    "replay",
    "RouteAdvisor",
    "ReadingMatcher",
    "InvestigationReconciliation",
    "OutcomeReview",
]


class ValidationError(ValueError):
    """A validation failure carrying a structured code."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(f"{message} [{code}]")
        self.code = code


def _require(condition: bool, message: str, code: str) -> None:
    if not condition:
        raise ValidationError(message, code)


def _canonical(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(data: Any) -> str:
    return hashlib.sha256(_canonical(data)).hexdigest()


_KNOWN_SPECIALTIES = ("Legal", "Medical")


# ---------------------------------------------------------------------------
# 2. AssociationEdge
# ---------------------------------------------------------------------------

@dataclass
class AssociationEdge:
    """A cross-specialty route. Producer: route author / pilot. Consumer:
    the consultation runtime (select_edge / consult)."""

    edge_id: str
    origin_specialty: str
    target_specialty: str
    source_op: str
    source_output_port: str
    target_op: str
    target_input_port: str
    purpose: str
    permitted_scope: list[str] = field(default_factory=list)
    excluded_scope: list[str] = field(default_factory=list)
    trigger_refs: list[str] = field(default_factory=list)
    supporting_evidence_refs: list[str] = field(default_factory=list)
    priority_rule: str = "narrowest_scope"
    max_uses: int = 1000
    enabled: bool = True
    uses: int = 0
    version: int = 1
    schema_version: str = "association-edge/1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "edge_id": self.edge_id,
            "version": self.version,
            "trigger_refs": list(self.trigger_refs),
            "supporting_evidence_refs": list(self.supporting_evidence_refs),
            "source_op": self.source_op,
            "source_output_port": self.source_output_port,
            "target_op": self.target_op,
            "target_input_port": self.target_input_port,
            "origin_specialty": self.origin_specialty,
            "target_specialty": self.target_specialty,
            "purpose": self.purpose,
            "permitted_scope": list(self.permitted_scope),
            "excluded_scope": list(self.excluded_scope),
            "priority_rule": self.priority_rule,
            "max_uses": self.max_uses,
            "enabled": self.enabled,
            "uses": self.uses,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AssociationEdge":
        if data.get("schema_version") != "association-edge/1.0":
            raise ValidationError(
                f"unsupported association-edge version: {data.get('schema_version')!r}", "VERSION"
            )
        return cls(**{k: v for k, v in data.items() if k != "schema_version"})

    def validate(self) -> None:
        _require(bool(self.edge_id and isinstance(self.edge_id, str)), "edge_id must be a non-empty string", "EDGE_ID")
        _require(self.origin_specialty in _KNOWN_SPECIALTIES, f"unknown origin specialty: {self.origin_specialty!r}", "ORIGIN")
        _require(self.target_specialty in _KNOWN_SPECIALTIES, f"unknown target specialty: {self.target_specialty!r}", "TARGET")
        _require(self.origin_specialty != self.target_specialty, "association edge must cross specialties", "SAME_SPECIALTY")
        _require(bool(self.source_op and self.target_op), "source_op and target_op are required", "ENDPOINT")
        _require(not (set(self.permitted_scope) & set(self.excluded_scope)),
                 "permitted and excluded scope must be disjoint", "SCOPE_OVERLAP")
        _require(self.priority_rule in ("narrowest_scope", "frequency", "manual"),
                 f"unknown priority_rule: {self.priority_rule!r}", "PRIORITY_RULE")
        _require(type(self.max_uses) is int and self.max_uses >= 0, "max_uses must be a non-negative integer", "MAX_USES")
        _require(type(self.uses) is int and 0 <= self.uses <= self.max_uses,
                 "uses must be within [0, max_uses]", "USES")


def store_edge(edge: AssociationEdge, adapter: Any, agent_id: str,
               family: str = "BIG49", index: int = 1280) -> dict[str, Any]:
    """Persist an edge via the CRAM-PU adapter into the association bucket
    (BIG49 index 1280 by default). Returns the COOL commit record. The
    caller must have registered the block owner with the controller
    beforehand. The edge is read back through COOL (see ``load_edge``).

    Integration note (merged candidate): the full edge JSON is ~700 chars,
    over the association bucket's 512-char cap. Under RC1 the bridge
    silently sliced the mirror to 512 while COOL kept the full JSON; Fix
    Rev 2's preflight now correctly rejects such a write. So the structured
    edge lives under the ``edge`` payload key (authoritative COOL record)
    and the mirror ``content`` is only a short reference,
    ``association-edge:<edge_id>``. The mirror reference therefore reports
    ``reconstructs_structured_payload: False`` — the mirror is a reference
    projection, not the edge itself (brief 2.3)."""
    edge.validate()
    if family != "BIG49":
        raise ValidationError("association edges persist in the BIG49 association bucket", "FAMILY")
    commit, _pointer = adapter.write_and_sync(
        agent_id, family, index,
        {"content": f"association-edge:{edge.edge_id}",
         "edge": edge.to_dict(),
         "relation": "ASSOCIATED_WITH"},
        bucket="association",
    )
    return commit


def load_edge(adapter: Any, agent_id: str, family: str = "BIG49", index: int = 1280) -> AssociationEdge:
    """Read an edge back via the adapter (COOL read) after reopen."""
    data = adapter.read(agent_id, family, index)
    payload = data["payload"]
    if isinstance(payload.get("edge"), dict):
        edge = AssociationEdge.from_dict(payload["edge"])
    else:
        # Legacy rows (RC1 / pre-merge): the full edge JSON was the content.
        raw = payload.get("content")
        if not isinstance(raw, str):
            raise ValidationError("stored edge has no content payload", "CONTENT")
        edge = AssociationEdge.from_dict(json.loads(raw))
    edge.validate()
    return edge


def record_use(edge: AssociationEdge, adapter: Any, agent_id: str,
               family: str = "BIG49", index: int = 1280) -> AssociationEdge:
    """Increment the measured uses counter and re-store (new revision)."""
    if edge.uses >= edge.max_uses:
        raise ValidationError(f"edge {edge.edge_id} reached max_uses={edge.max_uses}", "MAX_USES")
    updated = AssociationEdge(**{**edge.to_dict(), "schema_version": edge.schema_version, "uses": edge.uses + 1})
    # Rebuild explicitly to keep the dataclass constructor honest:
    updated = AssociationEdge.from_dict(updated.to_dict())
    store_edge(updated, adapter, agent_id, family=family, index=index)
    return updated


def select_edge(edges: list[AssociationEdge], origin: str, target: str) -> AssociationEdge | None:
    """Pick one eligible edge. Eligibility: enabled, matching origin/target,
    uses below max_uses. Priority: the narrowest eligible route (smallest
    permitted scope) outranks a common high-frequency route; frequency only
    breaks ties between equally narrow routes. Returns None if none eligible."""
    eligible = [
        e for e in edges
        if e.enabled and e.origin_specialty == origin and e.target_specialty == target
        and e.uses < e.max_uses
    ]
    if not eligible:
        return None
    return sorted(eligible, key=lambda e: (len(e.permitted_scope), -e.uses, e.edge_id))[0]


# ---------------------------------------------------------------------------
# 3. ConsultationRequest
# ---------------------------------------------------------------------------

@dataclass
class ConsultationRequest:
    """Producer: the Closet caller (identity bound by the runtime, never by
    the request itself). Consumer: the consultation runtime."""

    case_id: str
    invocation_id: str
    expected_case_revision: int
    origin_specialty: str
    target_specialty: str
    purpose: str
    original_question: str
    evidence_snapshot: dict[str, dict[str, Any]] = field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    trigger_edge_ref: str | None = None
    scope: list[str] = field(default_factory=list)
    budget: dict[str, int] = field(default_factory=lambda: {"max_stage_calls": 5, "max_steps": 64})
    schema_version: str = "consultation-request/1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "invocation_id": self.invocation_id,
            "expected_case_revision": self.expected_case_revision,
            "origin_specialty": self.origin_specialty,
            "target_specialty": self.target_specialty,
            "purpose": self.purpose,
            "original_question": self.original_question,
            "evidence_snapshot": json.loads(json.dumps(self.evidence_snapshot, sort_keys=True)),
            "evidence_refs": json.loads(json.dumps(self.evidence_refs, sort_keys=True)),
            "trigger_edge_ref": self.trigger_edge_ref,
            "scope": list(self.scope),
            "budget": dict(self.budget),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConsultationRequest":
        if data.get("schema_version") != "consultation-request/1.0":
            raise ValidationError(
                f"unsupported consultation-request version: {data.get('schema_version')!r}", "VERSION"
            )
        return cls(**{k: v for k, v in data.items() if k != "schema_version"})

    def validate(self) -> None:
        _require(bool(self.case_id and isinstance(self.case_id, str)), "case_id must be a non-empty string", "CASE_ID")
        _require(bool(self.invocation_id and isinstance(self.invocation_id, str)),
                 "invocation_id must be a non-empty string", "INVOCATION_ID")
        _require(type(self.expected_case_revision) is int and self.expected_case_revision >= 0,
                 "expected_case_revision must be a non-negative integer", "CASE_REVISION")
        _require(self.origin_specialty in _KNOWN_SPECIALTIES,
                 f"unknown origin specialty: {self.origin_specialty!r}", "ORIGIN_MISMATCH")
        _require(self.target_specialty in _KNOWN_SPECIALTIES,
                 f"unknown target specialty: {self.target_specialty!r}", "COUNTERPART_UNAVAILABLE")
        _require(self.origin_specialty != self.target_specialty,
                 "consultation must cross specialties (circular route)", "CIRCULAR_ROUTE")
        _require(bool(self.original_question and isinstance(self.original_question, str)),
                 "original_question must be a non-empty string", "QUESTION")
        _require(isinstance(self.budget, dict) and type(self.budget.get("max_stage_calls", 5)) is int
                 and self.budget.get("max_stage_calls", 5) > 0,
                 "budget.max_stage_calls must be a positive integer", "BUDGET")
        for ref in self.evidence_refs:
            sid = ref.get("source_id")
            _require(isinstance(sid, str) and sid in self.evidence_snapshot,
                     f"evidence ref {sid!r} is not in the evidence snapshot", "UNQUALIFIED_REF")
            stored = self.evidence_snapshot[sid]
            _require(ref.get("verification_status") == "VERIFIED",
                     f"evidence ref {sid!r} is not VERIFIED", "UNQUALIFIED_REF")
            _require(ref.get("key") == stored.get("key") and ref.get("value") == stored.get("value"),
                     f"evidence ref {sid!r} does not match the snapshot record", "UNQUALIFIED_REF")


# ---------------------------------------------------------------------------
# 4. ConsultationResult
# ---------------------------------------------------------------------------

_RESULT_DISPOSITIONS = (
    "COHERENT", "CONFLICT_PRESERVED", "HOLD", "REJECTED",
    "UNSUPPORTED_OR_UNGROUNDED", "CONFLICT_PROVENANCE_LOST", "PERSPECTIVE_DRIFT",
    "CIRCULAR_ROUTE_REJECTED", "COUNTERPART_UNAVAILABLE", "ASSOCIATION_REJECTED", "UNCHECKED",
)
_CHECK_DIMENSIONS = ("relevance", "scope", "provenance", "uncertainty", "perspective")


@dataclass
class ConsultationResult:
    """Producer: the consultation runtime. Consumer: the originating
    specialty (the result always returns there) and the ledger."""

    case_id: str
    invocation_id: str
    edge_id: str
    edge_version: int
    evidence_refs: list[str] = field(default_factory=list)
    trace_refs: list[str] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    conflicting_pairs: list[dict[str, Any]] = field(default_factory=list)
    uncertainty: list[str] = field(default_factory=list)
    disposition: str = "HOLD"
    return_specialty: str = ""
    checks: dict[str, bool] = field(default_factory=dict)
    rejected_inputs: list[str] = field(default_factory=list)
    schema_version: str = "consultation-result/1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "invocation_id": self.invocation_id,
            "edge_id": self.edge_id,
            "edge_version": self.edge_version,
            "evidence_refs": list(self.evidence_refs),
            "trace_refs": list(self.trace_refs),
            "claims": json.loads(json.dumps(self.claims, sort_keys=True)),
            "conflicting_pairs": json.loads(json.dumps(self.conflicting_pairs, sort_keys=True)),
            "uncertainty": list(self.uncertainty),
            "disposition": self.disposition,
            "return_specialty": self.return_specialty,
            "checks": dict(self.checks),
            "rejected_inputs": list(self.rejected_inputs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConsultationResult":
        if data.get("schema_version") != "consultation-result/1.0":
            raise ValidationError(
                f"unsupported consultation-result version: {data.get('schema_version')!r}", "VERSION"
            )
        return cls(**{k: v for k, v in data.items() if k != "schema_version"})

    def validate(self) -> None:
        _require(bool(self.case_id and self.invocation_id), "case_id and invocation_id are required", "IDS")
        _require(self.disposition in _RESULT_DISPOSITIONS,
                 f"unknown disposition: {self.disposition!r}", "DISPOSITION")
        _require(self.return_specialty in _KNOWN_SPECIALTIES,
                 f"unknown return specialty: {self.return_specialty!r}", "RETURN_SPECIALTY")
        _require(set(self.checks) == set(_CHECK_DIMENSIONS),
                 "checks must cover relevance, scope, provenance, uncertainty, perspective", "CHECKS")
        _require(all(type(v) is bool for v in self.checks.values()),
                 "checks must be boolean", "CHECKS")
        if self.disposition == "CONFLICT_PRESERVED":
            _require(len(self.conflicting_pairs) > 0,
                     "CONFLICT_PRESERVED requires at least one conflicting pair", "CONFLICTS")


# ---------------------------------------------------------------------------
# 5. InvocationEvent
# ---------------------------------------------------------------------------

@dataclass
class InvocationEvent:
    """One append-only attempt record. Producer: the stage that ran.
    Consumer: the ledger, SoSoContinuity and the replay projection."""

    seq: int
    case_revision: int
    invocation_id: str
    attempt_id: str
    slot_id: str
    selected: str
    input_ref: str
    output_ref: str
    disposition: str
    parent_id: str | None = None
    predecessor_id: str | None = None
    registry_version: str = "42.1"
    rejected_alternatives: list[dict[str, Any]] = field(default_factory=list)
    timing: dict[str, int] = field(default_factory=dict)
    schema_version: str = "invocation-event/1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "seq": self.seq,
            "case_revision": self.case_revision,
            "invocation_id": self.invocation_id,
            "attempt_id": self.attempt_id,
            "parent_id": self.parent_id,
            "predecessor_id": self.predecessor_id,
            "slot_id": self.slot_id,
            "registry_version": self.registry_version,
            "selected": self.selected,
            "rejected_alternatives": json.loads(json.dumps(self.rejected_alternatives, sort_keys=True)),
            "input_ref": self.input_ref,
            "output_ref": self.output_ref,
            "disposition": self.disposition,
            "timing": dict(self.timing),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InvocationEvent":
        if data.get("schema_version") != "invocation-event/1.0":
            raise ValidationError(
                f"unsupported invocation-event version: {data.get('schema_version')!r}", "VERSION"
            )
        return cls(**{k: v for k, v in data.items() if k != "schema_version"})

    def validate(self) -> None:
        _require(type(self.seq) is int and self.seq >= 1, "seq must be a positive integer", "SEQ")
        _require(type(self.case_revision) is int and self.case_revision >= 0,
                 "case_revision must be a non-negative integer", "CASE_REVISION")
        _require(bool(self.invocation_id and self.attempt_id), "invocation_id and attempt_id are required", "IDS")
        _require(bool(self.slot_id), "slot_id is required", "SLOT")
        _require(bool(self.input_ref and self.output_ref), "input_ref and output_ref are required", "REFS")
        _require(bool(self.disposition), "disposition is required", "DISPOSITION")


# ---------------------------------------------------------------------------
# 6. ResolutionRecord
# ---------------------------------------------------------------------------

@dataclass
class ResolutionRecord:
    """A pending requirement and its release condition. Producer: the
    investigation (OpeningSequence / a stage that needs evidence).
    Consumer: ReadingMatcher and the reconciliation step."""

    request_id: str
    case_revision: int
    required_feature: str
    scope_predicate: str = ""
    time_predicate: str = ""
    units_predicate: str = ""
    release_condition: str = ""
    supporting_evidence: list[str] = field(default_factory=list)
    disposition: str = "PENDING"
    applied_observation_ids: list[str] = field(default_factory=list)
    schema_version: str = "resolution-record/1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "case_revision": self.case_revision,
            "required_feature": self.required_feature,
            "scope_predicate": self.scope_predicate,
            "time_predicate": self.time_predicate,
            "units_predicate": self.units_predicate,
            "release_condition": self.release_condition,
            "supporting_evidence": list(self.supporting_evidence),
            "disposition": self.disposition,
            "applied_observation_ids": list(self.applied_observation_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResolutionRecord":
        if data.get("schema_version") != "resolution-record/1.0":
            raise ValidationError(
                f"unsupported resolution-record version: {data.get('schema_version')!r}", "VERSION"
            )
        return cls(**{k: v for k, v in data.items() if k != "schema_version"})

    def validate(self) -> None:
        _require(bool(self.request_id and isinstance(self.request_id, str)), "request_id is required", "REQUEST_ID")
        _require(type(self.case_revision) is int and self.case_revision >= 0,
                 "case_revision must be a non-negative integer", "CASE_REVISION")
        _require(bool(self.required_feature), "required_feature is required", "FEATURE")
        _require(self.disposition in ("PENDING", "RELEASED", "STALE", "UNRESOLVED"),
                 f"unknown disposition: {self.disposition!r}", "DISPOSITION")

    def apply_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        """Apply one observation to this pending requirement.

        Outcomes: RELEASED (valid, conclusive, matching feature/revision/
        evidence), DUPLICATE (already applied), WRONG_FEATURE, STALE (wrong
        case revision), MISSING_EVIDENCE, INCONCLUSIVE (valid but not
        conclusive — the requirement stays pending). Only RELEASED mutates
        the record's disposition; every other outcome preserves the pending
        requirement on the record.
        """
        obs_id = observation.get("observation_id")
        if obs_id is not None and obs_id in self.applied_observation_ids:
            return {"request_id": self.request_id, "outcome": "DUPLICATE",
                    "disposition": self.disposition, "observation_id": obs_id}
        if observation.get("feature") != self.required_feature:
            return {"request_id": self.request_id, "outcome": "WRONG_FEATURE",
                    "disposition": self.disposition,
                    "expected_feature": self.required_feature,
                    "observed_feature": observation.get("feature")}
        if observation.get("case_revision") != self.case_revision:
            return {"request_id": self.request_id, "outcome": "STALE",
                    "disposition": self.disposition,
                    "expected_revision": self.case_revision,
                    "observed_revision": observation.get("case_revision")}
        source_id = observation.get("source_id")
        if not source_id or (self.supporting_evidence and source_id not in self.supporting_evidence):
            return {"request_id": self.request_id, "outcome": "MISSING_EVIDENCE",
                    "disposition": self.disposition, "source_id": source_id}
        if obs_id is not None:
            self.applied_observation_ids.append(obs_id)
        if not observation.get("conclusive", False):
            return {"request_id": self.request_id, "outcome": "INCONCLUSIVE",
                    "disposition": self.disposition, "observation_id": obs_id}
        self.disposition = "RELEASED"
        if source_id not in self.supporting_evidence:
            self.supporting_evidence.append(source_id)
        return {"request_id": self.request_id, "outcome": "RELEASED",
                "disposition": self.disposition, "observation_id": obs_id,
                "value": observation.get("value")}


# ---------------------------------------------------------------------------
# 7. CoherenceReview
# ---------------------------------------------------------------------------

def _expected_conflicts(snapshot: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, list[dict[str, Any]]] = {}
    for sid, rec in snapshot.items():
        by_key.setdefault(rec.get("key"), []).append({"source_id": sid, **rec})
    conflicts = []
    for key, recs in by_key.items():
        values = {r.get("value") for r in recs}
        if len(values) > 1:
            conflicts.append({
                "key": key,
                "source_ids": sorted(r["source_id"] for r in recs),
                "propositions": sorted(f"{key}={r.get('value')}" for r in recs),
            })
    return conflicts


def _normalise_conflict_pairs(conflict_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = []
    for rec in conflict_records or []:
        if "records" in rec:
            inner = rec["records"]
            key = rec.get("key")
            pairs.append({
                "key": key,
                "source_ids": sorted(r["source_id"] for r in inner),
                "propositions": sorted(f"{key}={r.get('value')}" for r in inner),
            })
        else:
            pairs.append({
                "key": rec.get("key"),
                "source_ids": sorted(rec.get("source_ids", [])),
                "propositions": sorted(rec.get("propositions", [])),
            })
    return pairs


@dataclass
class CoherenceReview:
    """Producer: the independent reviewer (U33 slot). Consumer: the
    consultation runtime, which turns a failed review into a HOLD."""

    origin_specialty: str
    target_specialty: str
    reviewer: str
    verified_refs: list[str] = field(default_factory=list)
    perspective_decision: str = ""
    scope_decision: str = ""
    retained_conflicts: list[dict[str, Any]] = field(default_factory=list)
    unresolved_items: list[str] = field(default_factory=list)
    result: str = "HOLD"
    reason: str = ""
    schema_version: str = "coherence-review/1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "origin_specialty": self.origin_specialty,
            "target_specialty": self.target_specialty,
            "reviewer": self.reviewer,
            "verified_refs": list(self.verified_refs),
            "perspective_decision": self.perspective_decision,
            "scope_decision": self.scope_decision,
            "retained_conflicts": json.loads(json.dumps(self.retained_conflicts, sort_keys=True)),
            "unresolved_items": list(self.unresolved_items),
            "result": self.result,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoherenceReview":
        if data.get("schema_version") != "coherence-review/1.0":
            raise ValidationError(
                f"unsupported coherence-review version: {data.get('schema_version')!r}", "VERSION"
            )
        return cls(**{k: v for k, v in data.items() if k != "schema_version"})

    def validate(self) -> None:
        _require(self.origin_specialty in _KNOWN_SPECIALTIES, f"unknown origin: {self.origin_specialty!r}", "ORIGIN")
        _require(self.target_specialty in _KNOWN_SPECIALTIES, f"unknown target: {self.target_specialty!r}", "TARGET")
        _require(bool(self.reviewer), "reviewer identity/slot is required", "REVIEWER")
        _require(self.result in ("COHERENT", "CONFLICT_PRESERVED", "UNGROUNDED",
                                 "PROVENANCE_LOST", "PERSPECTIVE_DRIFT", "HOLD"),
                 f"unknown result: {self.result!r}", "RESULT")

    @classmethod
    def review_specialist_output(
        cls, origin_specialty: str, target_specialty: str, reviewer: str,
        specialist_output: dict[str, Any], evidence_snapshot: dict[str, dict[str, Any]],
    ) -> "CoherenceReview":
        """Re-check specialist output against the ORIGINAL evidence snapshot
        — never against the specialist's own asserted status. Citations must
        resolve in the snapshot; retained conflicts must match the snapshot's
        exact source pairs and propositions; the perspective tag must be the
        bounded tag for this origin/target pair."""
        citations = list(specialist_output.get("citations", []))
        verified = [c for c in citations if c in evidence_snapshot]
        grounded = bool(citations) and len(verified) == len(citations)
        expected = _expected_conflicts(evidence_snapshot)
        retained = _normalise_conflict_pairs(specialist_output.get("conflict_records", []))
        provenance_intact = (
            sorted(expected, key=lambda p: p["key"]) == sorted(retained, key=lambda p: p["key"])
            if expected else not retained
        )
        expected_tag = {
            ("Legal", "Medical"): "medical_bounded_for_legal",
            ("Medical", "Legal"): "legal_bounded_for_medical",
        }.get((origin_specialty, target_specialty), "")
        tag = specialist_output.get("perspective_tag", "")
        perspective_ok = tag in (expected_tag, "medical_native_clinical", "legal_native_litigation") \
            if specialist_output.get("status") in ("NATIVE_CLINICAL", "NATIVE_LEGAL") else tag == expected_tag
        if specialist_output.get("status") == "UNSUPPORTED":
            perspective_ok = tag in ("medical_only", "legal_only")
        unresolved = []
        if not grounded:
            unresolved.append("ungrounded_citations")
        if expected and not provenance_intact:
            unresolved.append("conflict_provenance")
        if not perspective_ok:
            unresolved.append("perspective")
        if not verified and not expected:
            unresolved.append("no_verified_evidence")
        if not grounded or not verified:
            result, reason = "UNGROUNDED", "citations do not resolve against the original evidence snapshot"
        elif expected and not provenance_intact:
            result, reason = "PROVENANCE_LOST", "retained conflict pairs do not match the snapshot exactly"
        elif not perspective_ok:
            result, reason = "PERSPECTIVE_DRIFT", f"perspective tag {tag!r} is not the bounded tag for {origin_specialty}->{target_specialty}"
        elif expected:
            result, reason = "CONFLICT_PRESERVED", "conflicts retained exactly; uncertainty preserved"
        else:
            result, reason = "COHERENT", "citations grounded, no conflicts, perspective bounded"
        return cls(
            origin_specialty=origin_specialty,
            target_specialty=target_specialty,
            reviewer=reviewer,
            verified_refs=verified,
            perspective_decision="preserved" if perspective_ok else "drift",
            scope_decision="bounded" if perspective_ok else "unbounded",
            retained_conflicts=retained,
            unresolved_items=unresolved,
            result=result,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# Phase A — Self-Aware Cognitive Map (passive replay projection)
# ---------------------------------------------------------------------------

@dataclass
class CognitiveMap:
    """Passive replay projection over committed events. No new slot IDs.
    Producer: InvestigationRecorder / replay(). Consumer: OpeningSequence,
    ClosingReview, reconciliation."""

    case_id: str
    goal: str
    evidence: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    unknowns: list[dict[str, Any]] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    selected_branch: str = ""
    disposition: str = ""
    resume_point: str = ""
    schema_version: str = "cognitive-map/1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "goal": self.goal,
            "evidence": list(self.evidence),
            "conflicts": json.loads(json.dumps(self.conflicts, sort_keys=True)),
            "unknowns": json.loads(json.dumps(self.unknowns, sort_keys=True)),
            "alternatives": list(self.alternatives),
            "selected_branch": self.selected_branch,
            "disposition": self.disposition,
            "resume_point": self.resume_point,
        }

    @classmethod
    def from_events(cls, case_id: str, goal: str, events: list[InvocationEvent]) -> "CognitiveMap":
        evidence: list[str] = []
        for ev in events:
            for ref in (ev.input_ref, ev.output_ref):
                if ref and ref not in evidence:
                    evidence.append(ref)
        conflicts = [
            {"slot_id": ev.slot_id, "output_ref": ev.output_ref}
            for ev in events if ev.disposition == "CONFLICT_PRESERVED"
        ]
        unknowns = [
            {"alternative": alt.get("alternative"), "reason": alt.get("reason")}
            for ev in events for alt in ev.rejected_alternatives
        ]
        alternatives = sorted({ev.selected for ev in events} |
                              {a.get("alternative") for ev in events for a in ev.rejected_alternatives if a.get("alternative")})
        last = events[-1] if events else None
        return cls(
            case_id=case_id,
            goal=goal,
            evidence=sorted(evidence),
            conflicts=conflicts,
            unknowns=unknowns,
            alternatives=alternatives,
            selected_branch=last.selected if last else "",
            disposition=last.disposition if last else "",
            resume_point=unknowns[0]["alternative"] if unknowns else ("complete" if last else "start"),
        )


class InvestigationRecorder:
    """Named responsibility: append-only recording of committed events."""

    def __init__(self, case_id: str, goal: str) -> None:
        self.case_id = case_id
        self.goal = goal
        self._events: list[InvocationEvent] = []

    def record(self, event: InvocationEvent) -> InvocationEvent:
        event.validate()
        self._events.append(event)
        return event

    @property
    def events(self) -> list[InvocationEvent]:
        return list(self._events)

    def build_map(self) -> CognitiveMap:
        return CognitiveMap.from_events(self.case_id, self.goal, self._events)


class RelationshipMapper:
    """Named responsibility: map evidence references to derived claims."""

    @staticmethod
    def map_relationships(evidence: list[str], claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        relations = []
        for claim in claims:
            for ref in claim.get("source_refs", []):
                relations.append({
                    "evidence_ref": ref,
                    "claim": claim.get("claim"),
                    "relation": "SUPPORTS" if ref in evidence else "UNGROUNDED",
                })
        return relations


class OpeningSequence:
    """Named responsibility: opening checks — task, scope, evidence,
    provenance, conflicts, unknowns and the starting route."""

    @staticmethod
    def check(case_id: str, goal: str, scope: list[str], evidence_snapshot: dict[str, Any],
              conflicts: list[dict[str, Any]], unknowns: list[str],
              starting_route: str) -> dict[str, Any]:
        return {
            "case_id": case_id,
            "task": {"status": "READY" if goal else "MISSING_GOAL", "goal": goal},
            "scope": {"status": "BOUNDED" if scope else "UNBOUNDED", "scope": list(scope)},
            "evidence": {"status": "PRESENT" if evidence_snapshot else "MISSING",
                         "refs": sorted(evidence_snapshot)},
            "provenance": {"status": "VERIFIED" if evidence_snapshot else "UNVERIFIED"},
            "conflicts": {"status": "PRESENT" if conflicts else "NONE", "count": len(conflicts)},
            "unknowns": {"status": "OPEN" if unknowns else "NONE", "items": list(unknowns)},
            "starting_route": {"status": "SELECTED" if starting_route else "UNDECIDED",
                               "route": starting_route},
        }


class ClosingReview:
    """Named responsibility: closing review of a replayed map."""

    @staticmethod
    def review(cognitive_map: CognitiveMap) -> dict[str, Any]:
        return {
            "case_id": cognitive_map.case_id,
            "resolved": [a for a in cognitive_map.alternatives if a == cognitive_map.selected_branch],
            "unresolved": [u["alternative"] for u in cognitive_map.unknowns],
            "disposition": cognitive_map.disposition,
            "resume_point": cognitive_map.resume_point,
            "evidence_count": len(cognitive_map.evidence),
            "conflict_count": len(cognitive_map.conflicts),
        }


def replay(events: list[InvocationEvent], case_id: str, goal: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reconstruct the map and its causal graph from committed events.
    Pure: it does not mutate the events or the case. Returns
    (map_dict, causal_graph) where the graph links each event's predecessor
    output to the event's own output."""
    case_hash_before = _digest([ev.to_dict() for ev in events])
    cognitive_map = CognitiveMap.from_events(case_id, goal, events)
    graph = [
        {"from": ev.predecessor_id or ev.input_ref, "to": ev.output_ref, "slot_id": ev.slot_id,
         "invocation_id": ev.invocation_id, "seq": ev.seq}
        for ev in events
    ]
    case_hash_after = _digest([ev.to_dict() for ev in events])
    assert case_hash_before == case_hash_after, "replay mutated the case"
    return cognitive_map.to_dict(), graph


# ---------------------------------------------------------------------------
# Phase B — advisory suggestions, matching, reconciliation
# ---------------------------------------------------------------------------

@dataclass
class RouteAdvisor:
    """Advisory route suggestions. Suggestions never select a route;
    selection stays under a governed task."""

    registry_version: str = "42.1"
    suggestions: list[dict[str, Any]] = field(default_factory=list)

    def suggest_routes(self, case_id: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Record one suggestion per considered candidate. Each candidate:
        {slot_id, eligible, reason, expected_effect}. Returns the recorded
        suggestions (advisory only)."""
        made = []
        for cand in candidates:
            suggestion = {
                "suggestion_id": f"{case_id}:suggest:{len(self.suggestions) + 1}",
                "case_id": case_id,
                "slot_id": cand["slot_id"],
                "eligible": bool(cand.get("eligible", False)),
                "reason": cand.get("reason", ""),
                "expected_effect": cand.get("expected_effect", ""),
                "observed_effect": None,
                "disposition": "CONSIDERED",
                "advisory": True,
                "registry_version": self.registry_version,
            }
            self.suggestions.append(suggestion)
            made.append(suggestion)
        return made

    def review_outcome(self, suggestion_id: str, observed_effect: str, disposition: str) -> dict[str, Any]:
        """Record the observed effect of a suggestion that was followed."""
        for suggestion in self.suggestions:
            if suggestion["suggestion_id"] == suggestion_id:
                suggestion["observed_effect"] = observed_effect
                suggestion["disposition"] = disposition
                return suggestion
        raise KeyError(f"Unknown suggestion: {suggestion_id!r}")


class ReadingMatcher:
    """Correlate one pending ResolutionRecord to a qualifying observation."""

    @staticmethod
    def match_pending_request(record: ResolutionRecord,
                              observations: list[dict[str, Any]]) -> dict[str, Any] | None:
        for obs in observations:
            if obs.get("feature") != record.required_feature:
                continue
            if obs.get("case_revision") != record.case_revision:
                continue
            source_id = obs.get("source_id")
            if not source_id:
                continue
            if record.supporting_evidence and source_id not in record.supporting_evidence:
                continue
            return {"request_id": record.request_id, "observation": obs,
                    "outcome": "RELEASED" if obs.get("conclusive") else "INCONCLUSIVE"}
        return None


@dataclass
class InvestigationReconciliation:
    """Measure what changed between two replayed maps."""

    @staticmethod
    def reconcile(before: CognitiveMap, after: CognitiveMap,
                  observations: list[dict[str, Any]]) -> dict[str, Any]:
        before_unknowns = {u.get("alternative") for u in before.unknowns}
        after_unknowns = {u.get("alternative") for u in after.unknowns}
        return {
            "resolved_unknowns": sorted(before_unknowns - after_unknowns),
            "new_unknowns": sorted(after_unknowns - before_unknowns),
            "new_evidence": sorted(set(after.evidence) - set(before.evidence)),
            "disposition_change": (
                {"from": before.disposition, "to": after.disposition}
                if before.disposition != after.disposition else None
            ),
            "unresolved_remaining": sorted(after_unknowns),
            "observations_applied": len(observations),
        }


@dataclass
class OutcomeReview:
    """Review the outcome of an investigation against its opening state."""

    @staticmethod
    def review(opening: dict[str, Any], closing: dict[str, Any],
               reconciliation: dict[str, Any]) -> dict[str, Any]:
        return {
            "goal": opening["task"]["goal"],
            "disposition": closing["disposition"],
            "resolved_unknowns": reconciliation["resolved_unknowns"],
            "unresolved_remaining": reconciliation["unresolved_remaining"],
            "new_evidence_count": len(reconciliation["new_evidence"]),
            "disposition_changed": reconciliation["disposition_change"] is not None,
            "resume_point": closing["resume_point"],
        }
