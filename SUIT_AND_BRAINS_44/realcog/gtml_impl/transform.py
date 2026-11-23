"""The 8 LSC-002 transformation operations for exp-impl-0.2.0-hardening.

EXPERIMENTAL — NOT HISTORICALLY AUTHENTICATED. Authority ZERO.

Pure functions, all fail-closed with TransformError (never silent).

Every EXPERIMENTAL semantic below carries the docstring line:
    "EXPERIMENTAL DEFINITION — NOT A BASELINE LAW."

Unknown-state values (UNKNOWN_ORIGIN / UNKNOWN / UNRESOLVED / MISSING /
NOT_DETECTED / NOT_PERFORMED / UNREVIEWED / DROP / etc.) pass through every
op unchanged — they are never collapsed into each other.

serialize() never embeds a ContentDigest as identity: handles serialize as
"KIND:name" strings, and no digest value is ever used to identify a node.

0.2.0-hardening changes (GTML-SEMANTIC-CONSERVATION-HARDENING-001,
Stages D + E; EXPERIMENTAL, Authority ZERO):
- reconstruct() is now a STRICT canonical decoder: duplicate JSON keys,
  non-canonical bytes, unknown/missing fields, and payload-version
  mismatch are rejected with INVALID / UNSUPPORTED outcomes
  (see reconstruct()). The 0.1.0 decoder accepted all of
  these silently (measured 2026-11-10, DISC-D1–D5). OD-07 variant:
  the recognized prior version (0.1.0) decodes and is re-emitted
  stamped current; UNRESOLVED is no longer a reconstruct() outcome.
- normalize() additionally orders provenance refs byte-lexicographically
  by ref, using provenance.build_manifest's ordering rule (DISC-E1).
  Values, collection semantics, and idempotence are unchanged.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .ir import (
    EpiState,
    Handle,
    Ident,
    IRValidationError,
    Literal,
    Node,
    ProvenanceManifest,
    Record,
    Statement,
    TemporalMark,
    VERSION,
)

# ---------------------------------------------------------------------------
# Fail-closed error
# ---------------------------------------------------------------------------


class TransformError(Exception):
    """Fail-closed transformation error."""


# ---------------------------------------------------------------------------
# serialize: deterministic canonical JSON
# ---------------------------------------------------------------------------

_FIELD_ORDER = ("version", "authority", "ledger_ref", "nodes", "statements", "literals")


def _handle_str(handle: Optional[Handle]) -> Optional[str]:
    return str(handle) if handle is not None else None


def _ident_dict(ident: Ident) -> Dict[str, Optional[str]]:
    return {
        "sid": _handle_str(ident.sid),
        "repid": _handle_str(ident.repid),
        "oid": _handle_str(ident.oid),
        "rid": _handle_str(ident.rid),
        "srcid": _handle_str(ident.srcid),
        "vid": _handle_str(ident.vid),
    }


def _temporal_dict(temporal: TemporalMark) -> Dict[str, Optional[str]]:
    return {
        "event_time": temporal.event_time,
        "knowledge_time": temporal.knowledge_time,
    }


def _epi_dict(epi: EpiState) -> Dict[str, Optional[str]]:
    return {
        "epistemic": epi.epistemic,
        "availability": epi.availability,
        "authority": epi.authority,
        "confidence": epi.confidence,
        "attention": epi.attention,
    }


def _provenance_dict(prov: ProvenanceManifest) -> Dict[str, Any]:
    return {
        "refs": [list(r) for r in prov.refs],
        "endorsement": prov.endorsement,
    }


def _node_dict(node: Node) -> Dict[str, Any]:
    return {
        "ident": _ident_dict(node.ident),
        "kind": node.kind,
        "label": node.label,
        "temporal": _temporal_dict(node.temporal),
        "epi": _epi_dict(node.epi),
        "provenance": _provenance_dict(node.provenance),
    }


def _statement_dict(stmt: Statement) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "ident": _ident_dict(stmt.ident),
        "subject": str(stmt.subject),
        "predicate": stmt.predicate,
        "object": str(stmt.object),
        "negated": stmt.negated,
        "temporal": _temporal_dict(stmt.temporal),
        "epi": _epi_dict(stmt.epi),
        "provenance": _provenance_dict(stmt.provenance),
    }
    # OD-05 EXPERIMENTAL: causal_basis / promotes_to round-trip with the
    # statement, but only when present. MERGE RESOLUTION MR-1: the keys
    # are omitted when None (None = "no basis recorded"), so basis-less
    # statements serialize byte-identical to the pristine form. Always
    # emitting them (even as null) would retroactively change every
    # existing payload's bytes and break OD-07's legacy-path
    # canonicality check (serialize(record) must equal the payload with
    # only the version field swapped).
    if stmt.causal_basis is not None:
        d["causal_basis"] = stmt.causal_basis
    if stmt.promotes_to is not None:
        d["promotes_to"] = stmt.promotes_to
    return d


def _literal_dict(lit: Literal) -> Dict[str, Any]:
    return {
        "value": lit.value,
        "datatype": lit.datatype,
    }


def _node_sort_key(node: Node) -> Tuple[str, str]:
    return (str(node.ident.sid or ""), node.label)


def _statement_sort_key(stmt: Statement) -> Tuple[str, str, str]:
    return (str(stmt.subject), stmt.predicate, str(stmt.object))


def _literal_sort_key(lit: Literal) -> Tuple[str, str]:
    return (lit.datatype, lit.value)


def _require_record(record: Record) -> Record:
    if not isinstance(record, Record):
        raise TransformError(f"expected IR Record, got {type(record).__name__}")
    return record


def serialize(record: Record) -> bytes:
    """Deterministic canonical JSON of a Record.

    Fixed field order (version, authority, ledger_ref, nodes, statements,
    literals); sort_keys=True; separators=(",", ":"); utf-8 encoding.
    Collections are sorted by canonical keys. Handles serialize as
    "KIND:name" strings. Digests are NEVER embedded as identity — there is
    no digest input to this function at all.
    """
    _require_record(record)
    doc = {
        "version": record.version,
        "authority": record.authority,
        "ledger_ref": record.ledger_ref,
        "nodes": [_node_dict(n) for n in sorted(record.nodes, key=_node_sort_key)],
        "statements": [_statement_dict(s) for s in sorted(record.statements, key=_statement_sort_key)],
        "literals": [_literal_dict(l) for l in sorted(record.literals, key=_literal_sort_key)],
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# parse_text: delegate to the held 0.2.3-d002a parser (read-only)
# ---------------------------------------------------------------------------

_HELD_PARSER_DIR = Path.home() / "workspace/goals/tml-reference-implementation" \
    / "tml-master-language-study-v01/gtml-d002-alternative-a"
_held_parser_module: Any = None


def _load_held_parser() -> Any:
    """Import the held parser read-only. Never copies the parser file."""
    global _held_parser_module
    if _held_parser_module is None:
        if not _HELD_PARSER_DIR.is_dir():
            raise TransformError(
                f"held parser directory unavailable: {_HELD_PARSER_DIR}")
        sys.path.insert(0, str(_HELD_PARSER_DIR))
        # Strictly read-only: suppress bytecode writes into the held tree.
        old_dont_write = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            import parser as held_parser  # noqa: F401
        except Exception as exc:
            raise TransformError(
                f"could not import held parser: {exc}") from exc
        finally:
            sys.dont_write_bytecode = old_dont_write
        _held_parser_module = held_parser
    return _held_parser_module


def parse_text(text: str) -> dict:
    """Parse text with the held 0.2.3-d002a parser, read-only.

    Returns the parser's deterministic candidate AST as a dict.
    Fail-closed: wraps the held parser's ParseError as TransformError.
    The held tree is never modified or copied.
    """
    if not isinstance(text, str):
        raise TransformError("text must be a string")
    held = _load_held_parser()
    try:
        result = held.parse(text)
    except held.ParseError as exc:
        raise TransformError(f"held parser rejected input: {exc}") from exc
    except TransformError:
        raise
    except Exception as exc:
        raise TransformError(f"held parser raised unexpectedly: {exc}") from exc
    if not isinstance(result, dict):
        raise TransformError(
            f"held parser returned non-dict: {type(result).__name__}")
    return result


# ---------------------------------------------------------------------------
# normalize: canonical ordering only
# ---------------------------------------------------------------------------


def _sorted_provenance(prov: ProvenanceManifest) -> ProvenanceManifest:
    """Return prov with refs ordered byte-lexicographically by ref.

    Mirrors the ordering rule of provenance.build_manifest (sorted by
    ref.encode("utf-8")); it orders only, never dedupes, drops, or changes
    any entry. A manifest whose refs are already ordered is returned
    unchanged.
    """
    refs = prov.refs
    ordered = tuple(sorted(refs, key=lambda r: r[0].encode("utf-8")))
    if ordered == refs:
        return prov
    return ProvenanceManifest(refs=ordered, endorsement=prov.endorsement)


def normalize(record: Record) -> Record:
    """Canonical ordering of collections. Nothing else changes.

    EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    Nodes sorted by (str(ident.sid or ""), label); statements sorted by
    (str(subject), predicate, str(object)); literals sorted by
    (datatype, value). Provenance refs on every node and statement are
    ordered byte-lexicographically by ref (0.2.0-hardening; the ordering
    rule is provenance.build_manifest's, applied here to manifests built
    by any path, not only build_manifest). Drops NOTHING, merges NOTHING,
    changes no values — including unknown-state values, which pass through
    unchanged. Deterministic: equal-IR inputs (up to ordering) yield
    byte-identical serialize() output. Idempotent: N(N(T)) == N(T).
    """
    _require_record(record)
    return Record(
        version=record.version,
        nodes=tuple(
            replace(n, provenance=_sorted_provenance(n.provenance))
            for n in sorted(record.nodes, key=_node_sort_key)
        ),
        statements=tuple(
            replace(s, provenance=_sorted_provenance(s.provenance))
            for s in sorted(record.statements, key=_statement_sort_key)
        ),
        literals=tuple(sorted(record.literals, key=_literal_sort_key)),
        authority=record.authority,
        ledger_ref=record.ledger_ref,
    )


# ---------------------------------------------------------------------------
# project / expand
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Projection:
    """A suppressed view of a Record with its expansion dependency retained.

    view: the Record with suppressed fields set to None.
    suppressed: tuple of field paths actually suppressed.
    dependency: serialize() bytes of the FULL (unsuppressed) Record.
      The expansion dependency is recorded, never discarded: expand()
      restores the exact full Record from it.
    """
    view: Record
    suppressed: Tuple[str, ...]
    dependency: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.view, Record):
            raise TransformError("Projection.view must be an IR Record")
        if not isinstance(self.suppressed, tuple) or not all(
                isinstance(p, str) for p in self.suppressed):
            raise TransformError("Projection.suppressed must be a tuple of str")
        if not isinstance(self.dependency, bytes) or not self.dependency:
            raise TransformError("Projection.dependency must be non-empty bytes")


def _suppress_field(obj: Any, attrs: List[str]) -> Any:
    """Return a copy of the frozen dataclass obj with the attr path set to None."""
    head, rest = attrs[0], attrs[1:]
    current = getattr(obj, head, _MISSING)
    if current is _MISSING:
        raise TransformError(f"path attribute not found: {head!r}")
    if rest:
        new_child = _suppress_field(current, rest)
        return replace(obj, **{head: new_child})
    return replace(obj, **{head: None})


_MISSING = object()


def _resolve_path(path: str) -> Tuple[str, List[str]]:
    """Split 'nodes[].epi.confidence' -> ('nodes', ['epi', 'confidence'])."""
    if not isinstance(path, str) or "[]." not in path:
        raise TransformError(f"unsupported profile path: {path!r}")
    collection, rest = path.split("[]", 1)
    if collection not in ("nodes", "statements"):
        raise TransformError(
            f"profile path collection must be 'nodes' or 'statements': {path!r}")
    attrs = [a for a in rest.strip(".").split(".") if a]
    if not attrs or not all(a.isidentifier() for a in attrs):
        raise TransformError(f"bad attribute chain in path: {path!r}")
    return collection, attrs


def project(record: Record, profile: dict) -> Projection:
    """Suppressed view of a Record. EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    profile is a dict {"suppress": [field paths like "nodes[].epi.confidence"]}.
    Returns Projection(view=Record with suppressed fields set to None,
    suppressed=list of paths actually suppressed,
    dependency=serialize(record) of the FULL record).

    Fail-closed: unknown collections, unresolvable attribute paths, or
    suppressed values that would invalidate the IR raise TransformError.
    Unknown-state values pass through unchanged unless explicitly suppressed.
    """
    _require_record(record)
    if not isinstance(profile, dict):
        raise TransformError("profile must be a dict")
    suppress = profile.get("suppress", [])
    if not isinstance(suppress, list) or not all(isinstance(p, str) for p in suppress):
        raise TransformError("profile['suppress'] must be a list of str paths")

    nodes = list(record.nodes)
    statements = list(record.statements)
    suppressed: List[str] = []

    for path in suppress:
        collection, attrs = _resolve_path(path)
        elements = nodes if collection == "nodes" else statements
        try:
            new_elements = [_suppress_field(el, attrs) for el in elements]
        except (TransformError, IRValidationError, TypeError, AttributeError) as exc:
            raise TransformError(f"cannot suppress path {path!r}: {exc}") from exc
        if collection == "nodes":
            nodes = new_elements
        else:
            statements = new_elements
        suppressed.append(path)

    try:
        view = Record(
            version=record.version,
            nodes=tuple(nodes),
            statements=tuple(statements),
            literals=record.literals,
            authority=record.authority,
            ledger_ref=record.ledger_ref,
        )
    except IRValidationError as exc:
        raise TransformError(f"projection produced invalid Record: {exc}") from exc

    return Projection(
        view=view,
        suppressed=tuple(suppressed),
        dependency=serialize(record),
    )


def expand(projection: Projection) -> Record:
    """Restore the full Record from a Projection's dependency.

    Fail-closed: TransformError if projection is not a Projection, if
    projection.dependency is empty, or if it does not deserialize to a
    valid Record. Otherwise returns the exact full Record (exact restore).
    """
    if not isinstance(projection, Projection):
        raise TransformError(
            f"expected Projection, got {type(projection).__name__}")
    if not projection.dependency:
        raise TransformError("projection dependency is empty: cannot expand")
    return reconstruct(projection.dependency, {
        "version": VERSION,
        "encoding": "canonical-json",
    })


# ---------------------------------------------------------------------------
# translate: explicit predicate renaming
# ---------------------------------------------------------------------------


def translate(record: Record, mapping: dict) -> Record:
    """Rename predicates per explicit mapping {old: new}.

    EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    Fail-closed: any predicate present in the record but absent from the
    mapping raises TransformError (nothing is renamed by guess). Mapping
    targets that are not valid relations also raise TransformError.
    Handles/namespaces are renamed only where a mapping entry covers them;
    this experimental implementation supports predicate renames only.
    Unknown-state values pass through unchanged.
    """
    _require_record(record)
    if not isinstance(mapping, dict):
        raise TransformError("mapping must be a dict")
    for old, new in mapping.items():
        if not isinstance(old, str) or not isinstance(new, str):
            raise TransformError("mapping keys and values must be str")

    new_statements = []
    for stmt in record.statements:
        if stmt.predicate not in mapping:
            raise TransformError(
                f"predicate {stmt.predicate!r} not covered by mapping: "
                "refusing to translate")
        new_predicate = mapping[stmt.predicate]
        try:
            new_statements.append(replace(stmt, predicate=new_predicate))
        except IRValidationError as exc:
            raise TransformError(
                f"mapping target {new_predicate!r} is not a valid relation: "
                f"{exc}") from exc
    return replace(record, statements=tuple(new_statements))


# ---------------------------------------------------------------------------
# compress / reconstruct
# ---------------------------------------------------------------------------

COMPRESSED_HEADER = b"GTMLEC01"  # 8-byte header, no compression algorithm


def compress(record: Record) -> bytes:
    """Serialize with an 8-byte GTMLEC01 header.

    EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    Identity-preserving: NO structural dedup is performed across distinct
    handles — dedup would risk merging distinct identities, so it is
    explicitly not done here.
    """
    return COMPRESSED_HEADER + serialize(record)


def _parse_handle(text: Any) -> Optional[Handle]:
    if text is None:
        return None
    if not isinstance(text, str) or ":" not in text:
        raise ValueError(f"bad handle string: {text!r}")
    kind, name = text.split(":", 1)
    return Handle(kind=kind, name=name)


def _require_keys(doc: Any, keys: frozenset, where: str,
                  optional: frozenset = frozenset()) -> None:
    """Require a dict with exactly the given keys (no missing, no unknown),
    except keys listed in `optional`, which may be absent.

    0.2.0-hardening strict-decode rule. serialize() always emits exactly
    these key sets, so a well-formed canonical payload passes; anything
    else (missing field silently defaulted, or an unknown field silently
    ignored — e.g. a J-01-style "exposed_members" nesting) is INVALID.
    MERGE RESOLUTION MR-1: causal_basis / promotes_to are optional —
    they are defined as optional (None = "no basis recorded"), and
    payloads predating OD-05 (including every 0.1.0 payload OD-07 opens)
    legitimately lack them. Absence decodes to None; unknown keys and
    missing required keys still fail.
    """
    if not isinstance(doc, dict):
        raise ValueError(f"{where} must be a dict")
    have = frozenset(doc.keys())
    missing = sorted((keys - optional) - have)
    unknown = sorted(have - keys)
    if missing or unknown:
        raise ValueError(
            f"{where} key mismatch: missing={missing} unknown={unknown}")


_RECORD_KEYS = frozenset({"version", "authority", "ledger_ref", "nodes", "statements", "literals"})
_IDENT_KEYS = frozenset({"sid", "repid", "oid", "rid", "srcid", "vid"})
_TEMPORAL_KEYS = frozenset({"event_time", "knowledge_time"})
_EPI_KEYS = frozenset({"epistemic", "availability", "authority", "confidence", "attention"})
_PROVENANCE_KEYS = frozenset({"refs", "endorsement"})
_NODE_KEYS = frozenset({"ident", "kind", "label", "temporal", "epi", "provenance"})
_STATEMENT_KEYS = frozenset({"ident", "subject", "predicate", "object", "negated", "causal_basis", "promotes_to", "temporal", "epi", "provenance"})
_LITERAL_KEYS = frozenset({"value", "datatype"})


def _record_from_dict(doc: Any) -> Record:
    """Deserialize a serialize()-produced dict back to a Record.

    STRICT (0.2.0-hardening): every dict must carry exactly its
    serialize()-emitted key set — no missing field is defaulted, no
    unknown field is ignored. Fail-closed: any structural or validation
    problem raises ValueError, which reconstruct() converts to
    TransformError.
    """
    _require_keys(doc, _RECORD_KEYS, "record")

    def ident_from(d: Any) -> Ident:
        _require_keys(d, _IDENT_KEYS, "ident")
        return Ident(
            sid=_parse_handle(d.get("sid")),
            repid=_parse_handle(d.get("repid")),
            oid=_parse_handle(d.get("oid")),
            rid=_parse_handle(d.get("rid")),
            srcid=_parse_handle(d.get("srcid")),
            vid=_parse_handle(d.get("vid")),
        )

    def temporal_from(d: Any) -> TemporalMark:
        _require_keys(d, _TEMPORAL_KEYS, "temporal")
        return TemporalMark(
            event_time=d.get("event_time"),
            knowledge_time=d.get("knowledge_time"),
        )

    def epi_from(d: Any) -> EpiState:
        _require_keys(d, _EPI_KEYS, "epi")
        return EpiState(
            epistemic=d["epistemic"],
            availability=d["availability"],
            authority=d.get("authority", "ZERO"),
            confidence=d.get("confidence"),
            attention=d.get("attention"),
        )

    def provenance_from(d: Any) -> ProvenanceManifest:
        _require_keys(d, _PROVENANCE_KEYS, "provenance")
        refs = d.get("refs", ())
        if not isinstance(refs, (list, tuple)):
            raise ValueError("provenance refs must be a list")
        for r in refs:
            if (not isinstance(r, (list, tuple)) or len(r) != 3
                    or not all(isinstance(x, str) for x in r)):
                raise ValueError(
                    "provenance ref must be a [ref, status, reason] triple of str")
        return ProvenanceManifest(
            refs=tuple(tuple(r) for r in refs),
            endorsement=d.get("endorsement", "NONE"),
        )

    nodes = []
    for n in doc.get("nodes", ()):
        _require_keys(n, _NODE_KEYS, "node")
        nodes.append(Node(
            ident=ident_from(n.get("ident", {})),
            kind=n["kind"],
            label=n["label"],
            temporal=temporal_from(n.get("temporal", {})),
            epi=epi_from(n.get("epi", {})),
            provenance=provenance_from(n.get("provenance", {})),
        ))

    statements = []
    for s in doc.get("statements", ()):
        _require_keys(s, _STATEMENT_KEYS, "statement",
                      optional=frozenset({"causal_basis", "promotes_to"}))
        if not isinstance(s["negated"], bool):
            raise ValueError("statement negated must be a bool")
        statements.append(Statement(
            ident=ident_from(s.get("ident", {})),
            subject=_parse_handle(s["subject"]),
            predicate=s["predicate"],
            object=_parse_handle(s["object"]),
            negated=bool(s.get("negated", False)),
            causal_basis=s.get("causal_basis"),
            promotes_to=s.get("promotes_to"),
            temporal=temporal_from(s.get("temporal", {})),
            epi=epi_from(s.get("epi", {})),
            provenance=provenance_from(s.get("provenance", {})),
        ))

    literals = []
    for lit in doc.get("literals", ()):
        _require_keys(lit, _LITERAL_KEYS, "literal")
        literals.append(Literal(value=lit["value"], datatype=lit.get("datatype", "string")))

    return Record(
        version=doc["version"],
        nodes=tuple(nodes),
        statements=tuple(statements),
        literals=tuple(literals),
        authority=doc.get("authority", "ZERO"),
        ledger_ref=doc.get("ledger_ref"),
    )


_CANONICAL_CONTEXT = {"version": VERSION, "encoding": "canonical-json"}
_COMPRESSED_CONTEXT = {"version": VERSION, "encoding": "GTMLEC01"}

# Recognized prior experimental version. OD-07 experimental variant
# (owner-confirmed selection, 2026-11-13): 0.1.0 payloads decode under
# the current rules and are re-emitted stamped with the current version
# (see reconstruct()). Every OTHER version mismatch stays closed.
_LEGACY_VERSION = "exp-impl-0.1.0"


def _no_duplicate_object(pairs: Any) -> Dict[str, Any]:
    """object_pairs_hook: reject duplicate JSON keys (fail-closed).

    json.loads silently keeps the last value for a repeated key; a
    canonical payload never contains one, so a duplicate is INVALID
    input, not a value to guess at.
    """
    obj: Dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key!r}")
        obj[key] = value
    return obj


def _reject_constant(name: str) -> Any:
    """parse_constant: reject NaN / Infinity / -Infinity (not JSON)."""
    raise ValueError(f"non-JSON constant: {name}")


def reconstruct(data: bytes, context: dict) -> Record:
    """Parse serialized bytes back to a Record, gated on exact context.

    context must be exactly {"version": "exp-impl-0.2.0-hardening",
    "encoding": "canonical-json"} for serialize() output, or encoding
    "GTMLEC01" for compress() output. Anything else — wrong version, wrong
    encoding, extra keys, malformed bytes — raises TransformError
    (fail-closed). The context contract is the LSC-002 Reconstruct(X, C)
    parameter: no context, no reconstruction.

    STRICT CANONICAL DECODE (0.2.0-hardening, Stage D). The payload must
    be exactly serialize(record) bytes for the Record it decodes to:
    duplicate JSON keys, non-canonical key order / whitespace /
    escaping, unknown or missing fields, and a payload version that
    differs from the context version (other than the recognized legacy
    version, which decodes and is re-emitted stamped current — OD-07)
    are all rejected. The decoded Record is re-serialized and compared
    byte-for-byte against the payload before it is returned (for legacy
    payloads, against the payload with only the version field swapped).

    Failure outcomes (directive Stage D; one exception type,
    TransformError, with a reason prefix — no new normative exception
    class is invented):
      INVALID — the bytes are not well-formed canonical JSON for a
        valid Record of this version (malformed JSON, duplicate keys,
        non-canonical form, missing/unknown fields, payload version
        other than the context version or the recognized legacy
        version, authority != ZERO, unknown predicate, bad handle).
      UNSUPPORTED — the context itself is not a supported decode
        context (wrong version string in the CONTEXT, wrong encoding,
        extra context keys, compressed data missing the GTMLEC01
        header, non-dict context, non-bytes data).
      UNRESOLVED — no longer raised by reconstruct() in this variant:
        the recognized prior version (exp-impl-0.1.0) now decodes under
        the owner-confirmed OD-07 opening instead of failing closed as
        UNRESOLVED. UNRESOLVED remains a defined outcome elsewhere
        (provenance / knowledge state), but reconstruct() refuses
        unrecognized versions as INVALID and bad contexts as
        UNSUPPORTED.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise TransformError("UNSUPPORTED: data must be non-empty bytes")
    if not isinstance(context, dict):
        raise TransformError("UNSUPPORTED: context must be a dict")

    if context == _CANONICAL_CONTEXT:
        payload = bytes(data)
    elif context == _COMPRESSED_CONTEXT:
        payload = bytes(data)
        if not payload.startswith(COMPRESSED_HEADER):
            raise TransformError(
                "UNSUPPORTED: compressed data missing GTMLEC01 header")
        payload = payload[len(COMPRESSED_HEADER):]
    else:
        raise TransformError(
            f"UNSUPPORTED: unsupported context {context!r}: expected version "
            f"{VERSION!r} with encoding 'canonical-json' or 'GTMLEC01'")

    try:
        doc = json.loads(payload.decode("utf-8"),
                         object_pairs_hook=_no_duplicate_object,
                         parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise TransformError(f"INVALID: bytes are not canonical JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise TransformError("INVALID: document must be a JSON object")
    payload_version = doc.get("version")
    # OD-07 experimental variant (owner-confirmed selection, 2026-11-13):
    # the recognized prior version decodes. A 0.1.0 payload is decoded
    # under the current rules and RE-EMITTED stamped with the current
    # version — the bytes are otherwise identical (verified: swapping
    # only the version field reproduces current serialize() output).
    # Every other version mismatch stays strictly closed (INVALID).
    # VERSION itself is NOT bumped; this is a decode gate, not a release.
    is_legacy = payload_version == _LEGACY_VERSION
    if not is_legacy and payload_version != VERSION:
        raise TransformError(
            f"INVALID: payload version {payload_version!r} does not match "
            f"context version {VERSION!r}")
    try:
        record = _record_from_dict(doc)
    except (ValueError, KeyError, TypeError, IRValidationError) as exc:
        raise TransformError(
            f"INVALID: bytes do not deserialize to a valid Record: {exc}") from exc
    if is_legacy:
        # Re-emit as the current version (decision point, OD-07): the
        # decoded Record is stamped VERSION. The alternative — preserving
        # the 0.1.0 stamp — is NOT implemented; a preserved-stamp record
        # would re-serialize to 0.1.0 bytes, and this decoder's contract
        # is that everything it returns is current-version canonical.
        record = replace(record, version=VERSION)
        # (1) The payload must be exactly the canonical bytes of its own
        # document. json.dumps re-canonicalizes, so comparing against the
        # RAW payload bytes is what catches non-canonical formatting
        # (whitespace, key order) — without this, parsing would erase
        # the formatting difference and a non-canonical legacy payload
        # would slip through.
        if json.dumps(doc, sort_keys=True,
                      separators=(",", ":")).encode("utf-8") != payload:
            raise TransformError(
                "INVALID: bytes are not canonical: the payload is not "
                "the canonical re-emission of its own document (key "
                "order, whitespace, or escaping is non-canonical)")
        # (2) Canonicality check against the payload with ONLY the
        # version field swapped: any other byte difference (e.g.
        # unsorted collections, which dumps preserves but serialize
        # sorts) is still INVALID.
        swapped = dict(doc)
        swapped["version"] = VERSION
        expected = json.dumps(swapped, sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
        if serialize(record) != expected:
            raise TransformError(
                "INVALID: bytes are not canonical: re-serialization of the "
                "decoded Record differs from the payload with only the "
                "version field swapped (key order, whitespace, escaping, "
                "or collection order is non-canonical)")
    elif serialize(record) != payload:
        raise TransformError(
            "INVALID: bytes are not canonical: re-serialization of the "
            "decoded Record differs from the payload (key order, "
            "whitespace, escaping, or collection order is non-canonical)")
    return record


# ---------------------------------------------------------------------------
# round-trip property helper: d(X) == d(Reconstruct(T(X), C))
# ---------------------------------------------------------------------------


def roundtrip_ok(record: Record) -> bool:
    """Check the LSC-002 acceptance shape d(X)==d(Reconstruct(T(X),C)).

    For T in {normalize, translate-with-identity-mapping}: serialize T(X),
    reconstruct it with the canonical context C, and compare serialize()
    bytes of the reconstructed Record against serialize() bytes of T(X).

    Returns True/False. Genuine failures (invalid IR, broken codec) still
    raise TransformError — this helper reports the property, it does not
    swallow fail-closed errors.
    """
    _require_record(record)
    context = dict(_CANONICAL_CONTEXT)

    identity_mapping = {s.predicate: s.predicate for s in record.statements}
    transforms = [
        normalize(record),
        translate(record, identity_mapping),
    ]
    for transformed in transforms:
        blob = serialize(transformed)
        back = reconstruct(blob, context)
        if serialize(back) != serialize(transformed):
            return False
    return True
