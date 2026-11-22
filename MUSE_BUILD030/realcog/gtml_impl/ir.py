"""Typed semantic IR for exp-impl-0.2.0-hardening.

EXPERIMENTAL — NOT HISTORICALLY AUTHENTICATED. Authority ZERO.

Vocabularies pinned from RECOVERED sources (byte-verified in held trees):
- EPISTEMIC / AVAILABILITY / AUTHORITY sets: gtml/core.py (v0.5.0, O-D2 pinned,
  sha 238986c3…). RECOVERED.
- RELATIONS: ph6_tok.py (v0.5.0, sha measured in readiness). RECOVERED.
- ID_CLASSES: top-level core.py ID_CLASSES. RECOVERED.

Load-bearing invariant: Handle and ContentDigest are DISTINCT types. A digest
may verify or address content; it NEVER establishes identity. No code path may
construct a Handle from a digest hex — the type system makes this a TypeError,
not a convention.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

VERSION = "exp-impl-0.2.0-hardening"

# ---------------------------------------------------------------------------
# Recovered vocabularies (frozen; fail-closed on unknown values)
# ---------------------------------------------------------------------------
# OD-04 (experimental variant; owner-confirmed Option A): EPISTEMIC gains
# UNCERTAIN. It is DISTINCT from UNKNOWN_ORIGIN, not a synonym:
#   UNKNOWN_ORIGIN = the origin of this claim is unknown (no source
#     recorded for it).
#   UNCERTAIN = the source text EXPLICITLY marks this span uncertain —
#     a "?" appears in the span (parser `uncertainty` flag, span-level).
# The two must never collapse into each other. UNCERTAIN is an
# experimental addition to the recovered vocabulary; it is NOT a
# historical claim and does not change Gate-1, canon, or authority.
EPISTEMIC = frozenset({
    "OBSERVED", "ASSERTED", "INTERPRETED", "INFERRED", "PREDICTED",
    "DERIVED", "GENERATED", "REPORTED", "UNKNOWN_ORIGIN", "UNCERTAIN",
})
AVAILABILITY = frozenset({
    "PRESENT", "MISSING", "NOT_DETECTED", "NOT_PERFORMED",
    "UNREVIEWED", "DROP", "UNKNOWN",
})
AUTHORITY = frozenset({"ZERO", "ADVISORY", "EVIDENTIARY", "NORMATIVE", "AUTHORITATIVE"})
# Relation vocabulary recovered from ph6_tok.py RELATIONS.
RELATIONS = frozenset({
    "HAS", "MADE_OF", "FROM", "COPY_OF", "CONNECTED_TO", "ASSOCIATED_WITH",
    "PART_OF", "MEMBER_OF", "LOCATED_IN", "BEFORE", "AFTER", "DURING",
    "MEASURES", "MEASURED_BY", "SUPPORTS", "CONTRADICTS", "CONFIRMED_BY",
    "REQUESTED_BY", "CAUSES", "SAME_TIME", "SAME_EVENT", "LEADS_TO",
    "DERIVED_FROM", "PARENT_OF",
})
ID_KINDS = frozenset({"SID", "OID", "RID", "LID", "SRCID", "VID", "REPID"})

# Unknown-state vocabulary: these must NEVER collapse into each other.
# UNKNOWN_ORIGIN (epistemic) != UNCERTAIN (epistemic: source text
# explicitly marks the span uncertain) != UNKNOWN (availability)
# != UNRESOLVED (knowledge) != MISSING (availability) != NOT_DETECTED.
# Focus area 3.


class IRValidationError(Exception):
    """Fail-closed IR construction error."""


# ---------------------------------------------------------------------------
# Identity primitives — distinct types by construction
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Handle:
    """An assigned governed identifier, e.g. SID:EVENT-17.

    Handles are ASSIGNED (by the identity subsystem), never derived from
    content digests. RECOVERED principle (four-domain baseline: identity).
    """
    kind: str
    name: str

    def __post_init__(self) -> None:
        if self.kind not in ID_KINDS:
            raise IRValidationError(f"unknown id kind: {self.kind!r}")
        if not self.name or not isinstance(self.name, str):
            raise IRValidationError("handle name must be a non-empty string")
        if len(self.name) > 256:
            raise IRValidationError("handle name too long")

    def __str__(self) -> str:
        return f"{self.kind}:{self.name}"


@dataclass(frozen=True)
class ContentDigest:
    """A content digest used for verification/addressability ONLY.

    Establishing a ContentDigest never establishes identity. This type is
    deliberately incompatible with Handle: there is no conversion.
    """
    algorithm: str
    hex: str

    def __post_init__(self) -> None:
        if self.algorithm != "sha256":
            raise IRValidationError("only sha256 digests supported (experimental)")
        if len(self.hex) != 64 or any(c not in "0123456789abcdef" for c in self.hex):
            raise IRValidationError("digest must be 64 lowercase hex chars")


@dataclass(frozen=True)
class Ident:
    """The five identity axes of a semantic object. All assigned handles."""
    sid: Optional[Handle] = None
    repid: Optional[Handle] = None
    oid: Optional[Handle] = None
    rid: Optional[Handle] = None
    srcid: Optional[Handle] = None
    vid: Optional[Handle] = None


# ---------------------------------------------------------------------------
# Epistemic / temporal marks
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EpiState:
    """Epistemic and information-availability state. Never collapsed."""
    epistemic: str
    availability: str
    authority: str = "ZERO"
    confidence: Optional[str] = None
    attention: Optional[str] = None

    def __post_init__(self) -> None:
        if self.epistemic not in EPISTEMIC:
            raise IRValidationError(f"unknown epistemic value: {self.epistemic!r}")
        if self.availability not in AVAILABILITY:
            raise IRValidationError(f"unknown availability value: {self.availability!r}")
        if self.authority not in AUTHORITY:
            raise IRValidationError(f"unknown authority value: {self.authority!r}")
        if self.authority != "ZERO":
            # Experimental engine pins ZERO; anything else is fail-closed here.
            raise IRValidationError(
                f"authority {self.authority!r} not permitted in experimental IR (ZERO only)")


@dataclass(frozen=True)
class TemporalMark:
    """Independent event time and knowledge time. Never merged.

    RECOVERED principle (four-domain baseline: time). ES-T-002 bitemporal
    model is PROPOSED-EXPERIMENTAL; this mechanism is EXPERIMENTAL.
    """
    event_time: Optional[str] = None
    knowledge_time: Optional[str] = None

    def __post_init__(self) -> None:
        for label, value in (("event_time", self.event_time),
                             ("knowledge_time", self.knowledge_time)):
            if value is not None and (not isinstance(value, str) or not value):
                raise IRValidationError(f"{label} must be a non-empty string or None")


# ---------------------------------------------------------------------------
# Provenance manifest (DP-01 ratified discipline, adapted)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProvenanceManifest:
    """Inline provenance refs. RESOLVED means representable, NOT validated.

    endorsement is ALWAYS NONE. identity_basis is pinned to
    BUNDLE_ITEM_SELF_DECLARED or empty. Unresolvable refs -> UNRESOLVED,
    never invented. (DP-01 ratified contract, adapted to IR.)
    """
    refs: Tuple[Tuple[str, str, str], ...] = ()  # (ref, identity_status, unresolved_reason)
    endorsement: str = "NONE"

    def __post_init__(self) -> None:
        if self.endorsement != "NONE":
            raise IRValidationError("endorsement must always be NONE")
        for ref, status, reason in self.refs:
            if status not in ("RESOLVED", "UNRESOLVED"):
                raise IRValidationError(f"bad identity_status: {status!r}")
            if not isinstance(ref, str) or not ref:
                raise IRValidationError("ref must be a non-empty string")


# ---------------------------------------------------------------------------
# Core IR nodes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Node:
    ident: Ident
    kind: str  # ENTITY | EVENT | SOURCE | ... (open vocabulary, experimental)
    label: str
    temporal: TemporalMark = field(default_factory=TemporalMark)
    epi: EpiState = field(default_factory=lambda: EpiState("UNKNOWN_ORIGIN", "UNKNOWN"))
    provenance: ProvenanceManifest = field(default_factory=ProvenanceManifest)

    def __post_init__(self) -> None:
        if not self.label:
            raise IRValidationError("node label must be non-empty")


@dataclass(frozen=True)
class Statement:
    ident: Ident
    subject: Handle
    predicate: str
    object: Handle
    negated: bool = False  # explicit negations preserved (LSC requirement)
    # OD-05 EXPERIMENTAL (Option B, free-form basis reference, presence
    # only): the recorded basis for a CAUSES claim, if any. None means
    # "no basis recorded". The surface syntax carries no basis annotation,
    # so lowering leaves this None unless the caller supplies a basis
    # (see frontend.lower causal_bases). Presence is judged by
    # truthiness at the frontend gate, exactly like the held validator's
    # `not basis` (an empty string counts as no basis).
    causal_basis: Optional[str] = None
    # OD-05 EXPERIMENTAL: a claimed promotion target, if any — the
    # predicate this statement is to be treated as. Lowering never
    # populates it (no surface syntax claims promotion); it exists so
    # the widened Rule 2 has a representation to check. None means
    # "no promotion claimed".
    promotes_to: Optional[str] = None
    temporal: TemporalMark = field(default_factory=TemporalMark)
    epi: EpiState = field(default_factory=lambda: EpiState("UNKNOWN_ORIGIN", "UNKNOWN"))
    provenance: ProvenanceManifest = field(default_factory=ProvenanceManifest)

    def __post_init__(self) -> None:
        if self.predicate not in RELATIONS:
            raise IRValidationError(f"unknown relation: {self.predicate!r}")
        if not isinstance(self.negated, bool):
            raise IRValidationError("negated must be bool")
        if self.causal_basis is not None and not isinstance(self.causal_basis, str):
            raise IRValidationError("causal_basis must be a string or None")
        if self.promotes_to is not None and self.promotes_to not in RELATIONS:
            raise IRValidationError(f"unknown promotes_to relation: {self.promotes_to!r}")


@dataclass(frozen=True)
class Literal:
    value: str
    datatype: str = "string"


@dataclass(frozen=True)
class Record:
    """Top-level envelope. authority is pinned ZERO."""
    version: str
    nodes: Tuple[Node, ...] = ()
    statements: Tuple[Statement, ...] = ()
    literals: Tuple[Literal, ...] = ()
    authority: str = "ZERO"
    ledger_ref: Optional[str] = None  # bitemporal ledger anchor (temporal.py)

    def __post_init__(self) -> None:
        if self.authority != "ZERO":
            raise IRValidationError("Record authority must be ZERO")
        if not self.version:
            raise IRValidationError("Record version must be non-empty")
