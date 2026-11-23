"""Truthful, versioned slot map for Suit and Brains 42.0 (SLOT_MAP_VERSION 42.1).

Two dispatch contracts are preserved, unchanged:

* Registry lookup (``BrainsWiringRegistry.invoke``) describes a slot and emits
  generic metadata. It never executes a bound operation.
* Operation execution (``BrainsWiringRegistry.execute_operation``) invokes the
  exact bound callable, or raises ``KeyError`` for an unbound slot.

This module adds a third, typed boundary on top of them — ``execute_typed`` —
which never raises ``KeyError`` for an unbound slot: unbound slots return an
explicit UNSUPPORTED disposition, malformed inputs return a REJECTED
disposition with a structured code, and every result names the handler that
ran (or ``None``). It does not change ``invoke`` or ``execute_operation``.

Lineage (42.1):
* Historical U09 "Compare Candidates" and RC1 U09 "Motor Disposition" are
  distinct lineage entries. They share an ID only; they are not the same
  operation. RC1 U09 is a dedicated Higher49 route (see
  ``contract.disposition_adapter``).
* Medical L06/L07 <-> U01/U02 and Legal L19/L20 <-> U17/U18 are proposals
  only. No new numeric assignments are made in this map; the affected slots
  remain generic-only and carry the proposal in their lineage note.

States: "bound" (an exact callable is bound in OPERATIONS / CROSS_OPERATIONS),
"dedicated" (U09 only: bound through the named disposition adapter and the
Higher49 runtime branch), "generic-only" (registry route, no callable),
"experimental" (a valid state with no current members in 42.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contract.authority import require_task
from contract.brains_wiring import BrainsWiringRegistry, REGIONS
from contract.cross_specialty import CROSS_OPERATIONS
from contract.disposition_adapter import ADAPTER_ID, evaluate_u09
from contract.functions025 import OPERATIONS

SLOT_MAP_VERSION = "42.1"
SLOT_SPEC_VERSION = "slot-spec/1.0"

# Lineage notes that must not be silently reused as behaviour.
LINEAGE_U09 = (
    "RC1 U09 Motor Disposition; distinct lineage from historical "
    "U09 Compare Candidates; dedicated Higher49 route"
)
LINEAGE_MEDICAL_PROPOSAL = (
    "Proposal: Medical L06/L07 <-> U01/U02; no new numeric assignment in 42.1"
)
LINEAGE_LEGAL_PROPOSAL = (
    "Proposal: Legal L19/L20 <-> U17/U18; no new numeric assignment in 42.1"
)
_LINEAGE_BY_SLOT = {
    "U09": LINEAGE_U09,
    "L06": LINEAGE_MEDICAL_PROPOSAL,
    "L07": LINEAGE_MEDICAL_PROPOSAL,
    "U01": LINEAGE_MEDICAL_PROPOSAL,
    "U02": LINEAGE_MEDICAL_PROPOSAL,
    "L19": LINEAGE_LEGAL_PROPOSAL,
    "L20": LINEAGE_LEGAL_PROPOSAL,
    "U17": LINEAGE_LEGAL_PROPOSAL,
    "U18": LINEAGE_LEGAL_PROPOSAL,
    "L33": "Registry binding is a lambda wrapping contract.cross_specialty.associate_L33",
    "L34": "Registry binding is a lambda wrapping contract.cross_specialty.coherence_L34",
    "U32": "Registry binding is a lambda wrapping contract.cross_specialty.associate_audit_U32",
    "U33": "Registry binding is a lambda wrapping contract.cross_specialty.coherence_audit_U33",
}

# Human-readable operation contracts for the bound operations. These are
# schema *references* (the callable's contract), not re-implementations.
_IO_BY_SLOT: dict[str, tuple[str, str]] = {
    "L03": ("observations: list[observation envelope] -> {accepted: [parsed row], rejected: [{digest, reason}]}",
            "parse"),
    "L04": ("rows: [parsed row] -> {index: {entity: {spellings, sources, facts}}, rejected: [...]}",
            "entities"),
    "L05": ("rejections: [{digest, reason}] -> {request: clarify_observation, required_fields, observation_ids}",
            "request_clarification"),
    "L16": ("index: {entity: {facts}} -> [row with normalised predicate]",
            "relations"),
    "L17": ("rows: [parsed row] -> [pair comparison {left, right, same_scope, same_value, same_polarity, ...}]",
            "compare"),
    "L18": ("pairs: [pair comparison] -> [conflicting pair comparison]",
            "conflicts"),
    "L22": ("rows: [parsed row with event] -> {groups: [{instant, rows}], undated: [digest]}",
            "temporal"),
    "L31": ("rows, contradictions, blocked -> {status: SUPPORTED | CONFLICT | ABSTAIN, ...}",
            "sufficiency"),
    "L33": ("envelope: ContextEnvelope, target_specialty -> association {relevant, bounded_question, permitted_scope, excluded_scope}",
            "associate_L33"),
    "L34": ("envelope, association, specialist_out -> {function_id: L34, checks: {perspective_preserved, relevance_established, uncertainty_preserved}}",
            "coherence_L34"),
    "U06": ("premises: [literal], rules: [{if: [literal], then: literal}] -> {facts: [literal], contradiction: bool}",
            "infer"),
    "U07": ("templates: {key: {predicts, requires}}, facts -> {key: predicts} for satisfied templates",
            "hypotheses"),
    "U09": ("observation: {obs_id, facts} -> [disposition event {disposition: PASS | DROP | HOLD, ...}]",
            "evaluate_motor_branches"),
    "U11": ("candidates: {key: {feature: value}}, observed -> partitions: {feature: [counts]}",
            "gaps"),
    "U12": ("partitions: {feature: [counts]}, preference -> {feature, used_method: bool}",
            "select"),
    "U13": ("candidates, observed -> {remaining: {key: predictions}, basis, causation: UNDETERMINED}",
            "evaluate_cause"),
    "U14": ("history: [{before, after, profile}] -> profile | None",
            "learn"),
    "U15": ("records: [{profile, source}], partitions -> record | None",
            "transfer"),
    "U16": ("decision: {status, support}, relevant rows -> decision + {presented_sources: {source: [digest]}}",
            "synthesize"),
    "U32": ("envelope, l33_out, target_specialty, ev_check -> {approved, circular_detected, counterpart_available, scope_disjoint, conflicting_keys}",
            "associate_audit_U32"),
    "U33": ("envelope, ev_check, l34_out, specialist_out, target_specialty -> {coherence_status, dimensions, citations_grounded, conflict_provenance_intact}",
            "coherence_audit_U33"),
}

_KNOWN_CALLERS: dict[str, list[str]] = {
    "U11": ["build040_runtime.learning_loop.AssistedLearningLoop"],
    "U12": ["build040_runtime.learning_loop.AssistedLearningLoop"],
    "U13": ["build040_runtime.learning_loop.AssistedLearningLoop"],
    "U14": ["build040_runtime.learning_loop.AssistedLearningLoop"],
    "U15": ["build040_runtime.learning_loop.AssistedLearningLoop"],
    "L33": ["contract.cross_specialty.run_consultation"],
    "U32": ["contract.cross_specialty.run_consultation"],
    "L34": ["contract.cross_specialty.run_consultation"],
    "U33": ["contract.cross_specialty.run_consultation"],
    "U09": [
        "build040_runtime.brains_49_runtime.Brains49Runtime.dispatch_higher49",
        "contract.disposition_adapter.evaluate_u09",
    ],
}

_GENERIC_IO = (
    "payload: {authority: 0, task_id: str} -> "
    "{function_id, layer, region, implementation_tier, authority: 0, status: OK}"
)


@dataclass(frozen=True)
class SlotSpec:
    """One versioned slot record. Schema version: slot-spec/1.0."""

    function_id: str
    name: str
    region: str
    state: str  # bound | dedicated | generic-only | experimental
    callable_identity: str | None
    input_schema: str
    output_schema: str
    required_grant: str
    side_effects: list[str] = field(default_factory=lambda: ["none"])
    known_callers: list[str] = field(default_factory=list)
    trace_name: str = ""
    schema_version: str = SLOT_SPEC_VERSION
    lineage: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "function_id": self.function_id,
            "name": self.name,
            "region": self.region,
            "state": self.state,
            "callable_identity": self.callable_identity,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "required_grant": self.required_grant,
            "side_effects": list(self.side_effects),
            "known_callers": list(self.known_callers),
            "trace_name": self.trace_name,
            "lineage": self.lineage,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SlotSpec":
        if data.get("schema_version") != SLOT_SPEC_VERSION:
            raise ValueError(f"unsupported slot-spec version: {data.get('schema_version')!r} [VERSION]")
        return cls(
            function_id=data["function_id"],
            name=data["name"],
            region=data["region"],
            state=data["state"],
            callable_identity=data.get("callable_identity"),
            input_schema=data["input_schema"],
            output_schema=data["output_schema"],
            required_grant=data["required_grant"],
            side_effects=list(data.get("side_effects", ["none"])),
            known_callers=list(data.get("known_callers", [])),
            trace_name=data.get("trace_name", ""),
            lineage=data.get("lineage", ""),
        )

    def validate(self) -> None:
        if self.state not in ("bound", "dedicated", "generic-only", "experimental"):
            raise ValueError(f"unknown slot state: {self.state!r} [STATE]")
        if self.state in ("bound", "dedicated") and not self.callable_identity:
            raise ValueError(f"{self.function_id}: bound/dedicated slot needs a callable [CALLABLE]")
        if self.state == "generic-only" and self.callable_identity is not None:
            raise ValueError(f"{self.function_id}: generic-only slot must not name a callable [CALLABLE]")
        if not self.required_grant:
            raise ValueError(f"{self.function_id}: required_grant is empty [GRANT]")


def _region_name(index: int) -> str:
    reg_id = (index - 1) // 7 + 1
    return REGIONS[reg_id][2]


def _callable_identity(fid: str) -> str | None:
    if fid == "U09":
        return ADAPTER_ID
    fn = CROSS_OPERATIONS.get(fid) or OPERATIONS.get(fid)
    if fn is None:
        return None
    return f"{fn.__module__}.{fn.__qualname__}"


def _build_spec(fid: str) -> SlotSpec:
    index = int(fid[1:])
    region = _region_name(index)
    if fid == "U09":
        state = "dedicated"
        name = "evaluate_motor_branches"
    elif fid in OPERATIONS or fid in CROSS_OPERATIONS:
        state = "bound"
        name = _IO_BY_SLOT.get(fid, ("", ""))[1] or fid
    else:
        state = "generic-only"
        name = f"{region.lower()} region route"
    io_in, _ = _IO_BY_SLOT.get(fid, (_GENERIC_IO, ""))
    io_out = io_in
    if fid in _IO_BY_SLOT:
        # input_schema / output_schema are the two halves of the contract string.
        io_in, io_out = _IO_BY_SLOT[fid][0].split(" -> ", 1)
    return SlotSpec(
        function_id=fid,
        name=name,
        region=region,
        state=state,
        callable_identity=_callable_identity(fid),
        input_schema=io_in,
        output_schema=io_out,
        required_grant="task.run",
        side_effects=["none"],
        known_callers=list(_KNOWN_CALLERS.get(fid, [])),
        trace_name=f"{fid}.{name}",
        lineage=_LINEAGE_BY_SLOT.get(fid, ""),
    )


_SPECS: dict[str, SlotSpec] = {}
for _prefix in ("L", "U"):
    for _i in range(1, 50):
        _fid = f"{_prefix}{_i:02d}"
        _SPECS[_fid] = _build_spec(_fid)


def describe(fid: str) -> SlotSpec:
    """Return the SlotSpec for one slot id, e.g. ``describe("U09")``."""
    try:
        return _SPECS[fid]
    except KeyError:
        raise KeyError(f"Unknown slot: {fid!r}") from None


def registry() -> list[SlotSpec]:
    """Return SlotSpec records for all 98 slots, L01-L49 then U01-U49."""
    return [_SPECS[f"L{i:02d}"] for i in range(1, 50)] + [
        _SPECS[f"U{i:02d}"] for i in range(1, 50)
    ]


# Structured rejection codes at the typed boundary.
_CODE_FOR_EXCEPTION = {
    KeyError: "MISSING_FIELD",
    TypeError: "TYPE_MISMATCH",
    ValueError: "INVALID_VALUE",
    AttributeError: "INTERNAL",
}


def _error_evidence(exc: BaseException) -> dict[str, Any]:
    return {"exception_type": type(exc).__name__, "message": str(exc)}


def _u16_status_check(args: tuple[Any, ...]) -> None:
    """Boundary validation for U16: the callable passes unknown statuses
    through silently, so the typed boundary rejects them explicitly."""
    if args and isinstance(args[0], dict):
        status = args[0].get("status")
        if status not in ("SUPPORTED", "CONFLICT", "ABSTAIN"):
            raise ValueError(f"unknown decision status: {status!r}")


def execute_typed(fid: str, auth_payload: dict[str, Any], *args: Any) -> dict[str, Any]:
    """Execute one slot through the typed boundary.

    Returns ``{function_id, handler, status, code, result | error}`` where
    status is OK | UNSUPPORTED | REJECTED. Bound operations run via their
    exact callable; unbound slots return UNSUPPORTED (never ``KeyError``);
    U09 runs via the named disposition adapter. Malformed inputs are caught
    and returned as REJECTED with a structured code and the exception as
    evidence. ``invoke`` and ``execute_operation`` are untouched.
    """
    if fid not in _SPECS:
        return {
            "function_id": fid,
            "handler": None,
            "status": "REJECTED",
            "code": "UNKNOWN_SLOT",
            "error": {"exception_type": "KeyError", "message": f"Unknown slot: {fid!r}"},
        }
    spec = _SPECS[fid]
    try:
        require_task(auth_payload)
    except Exception as exc:  # require_task raises on missing/invalid authority
        return {
            "function_id": fid,
            "handler": spec.callable_identity,
            "status": "REJECTED",
            "code": "AUTH_REQUIRED",
            "error": _error_evidence(exc),
        }
    if spec.state == "generic-only":
        return {
            "function_id": fid,
            "handler": None,
            "status": "UNSUPPORTED",
            "code": "UNSUPPORTED_SLOT",
            "error": {
                "exception_type": None,
                "message": f"{fid} is generic-only in slot map {SLOT_MAP_VERSION}: "
                "no bound operation; registry invoke returns generic metadata only",
            },
        }
    try:
        if fid == "U09":
            execution_id = args[1] if len(args) > 1 and isinstance(args[1], str) else "exec-motor-001"
            result = evaluate_u09(args[0], execution_id)
            handler = ADAPTER_ID
        else:
            if fid == "U16":
                _u16_status_check(args)
            bound = BrainsWiringRegistry(operations=CROSS_OPERATIONS)
            result = bound.execute_operation(fid, auth_payload, *args)
            handler = spec.callable_identity
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return {
            "function_id": fid,
            "handler": spec.callable_identity,
            "status": "REJECTED",
            "code": _CODE_FOR_EXCEPTION[type(exc)],
            "error": _error_evidence(exc),
        }
    return {
        "function_id": fid,
        "handler": handler,
        "status": "OK",
        "code": "OK",
        "result": result,
    }
