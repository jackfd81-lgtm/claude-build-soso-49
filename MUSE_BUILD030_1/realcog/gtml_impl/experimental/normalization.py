"""Governed normalization — the four layers, kept separate (Stage E).

EXPERIMENTAL — NOT HISTORICALLY AUTHENTICATED. NOT A BASELINE LAW.
Authority ZERO. Gate-1 HOLD. Canon UNCHANGED.

Directive GTML-SEMANTIC-CONSERVATION-HARDENING-001 §4 Stage E requires
the four normalization layers to be separated rather than conflated:

1. WRAPPER-FORM NORMALIZATION — unwrap_form(): validate the decode
   context and strip the GTMLEC01 wrapper header. This is about the
   BYTES' envelope, not their meaning. Source: transform.py
   reconstruct() context contract (LSC-002 Reconstruct(X, C) parameter).
2. CANONICAL SERIALIZATION — transform.serialize(): the deterministic
   canonical JSON form (sorted keys, compact separators, UTF-8,
   collections in canonical sort order). Source: transform.py serialize()
   and the Stage C profile C-TR-01. serialize() is UNCHANGED by this
   stage: it applies no Unicode NFC normalization and no Decimal
   normalization, matching the held GTML-CORE-001 serializer
   (gtml_serializer.py canonical()), which also applies neither. The
   kernel's _canon (gtml/core.py) DOES apply NFC + Decimal — but it
   governs the kernel's own canonical_bytes, not this IR's serialize().
   Which canonicalization governs the integrated IR is therefore
   CONTRACT-UNRESOLVED (DISC-C3, carried as DISC-E2); this module does
   NOT silently adopt NFC into serialize(). See nfc_fold_report().
3. SEMANTIC EQUIVALENCE — not computed here. Equivalence under the
   Stage C profile is the independent comparator's job
   (stage-f/tools/independent_comparator.py), which operates on
   canonical forms only and never calls this implementation's equality
   or reconstruction logic. No function here claims two Records mean
   the same thing.
4. LOSSY PROJECTION — transform.project() / expand(): declared
   suppression with the full Record retained in the dependency bytes.
   Source: transform.py project()/expand() (profile C-TR-03/C-TR-04).
   Suppression is lossy-by-design and recoverable-by-dependency, never
   silent. Nothing in this module adds a lossy step.

The governed normalization N itself remains transform.normalize():
canonical collection ordering plus (0.2.0-hardening) provenance-ref
ordering by provenance.build_manifest's byte-lexicographic rule.
This module does NOT re-implement N — a second implementation would be
a second source of truth. It only names the layers and provides the
wrapper-form step and the NFC-gap measurement below.

Every public function carries EXPERIMENTAL=True as a function
attribute, matching gtml_impl.experimental.operators.
"""

from __future__ import annotations

import unicodedata
from dataclasses import replace
from typing import Any, Dict, List

from ..ir import Handle, ProvenanceManifest, Record
from ..transform import COMPRESSED_HEADER, _CANONICAL_CONTEXT, _COMPRESSED_CONTEXT
from ..transform import TransformError
from .operators import _experimental

__all__ = ["unwrap_form", "nfc_fold_report", "LAYERS"]


# Layer registry: name -> (what it is, governing source, lossy?).
# Data only, mirroring the baseline.py contract-record pattern.
LAYERS = {
    "wrapper_form": (
        "Context validation + GTMLEC01 header strip (unwrap_form).",
        "transform.py reconstruct() context contract; LSC-002 Reconstruct(X, C).",
        False,
    ),
    "canonical_serialization": (
        "transform.serialize(): sorted keys, compact separators, UTF-8, "
        "canonical collection order. No NFC, no Decimal normalization.",
        "transform.py serialize(); gtml_serializer.py canonical(); "
        "profile C-TR-01. Kernel _canon differs (NFC + Decimal) — DISC-E2.",
        False,
    ),
    "semantic_equivalence": (
        "Dimension comparison over canonical forms by the independent "
        "comparator; never this implementation's own equality.",
        "Stage C profile comparator; stage-f/tools/independent_comparator.py.",
        False,
    ),
    "lossy_projection": (
        "transform.project(): declared suppression, full Record retained "
        "in dependency bytes; expand() restores exactly.",
        "transform.py project()/expand(); profile C-TR-03/C-TR-04.",
        True,
    ),
}


@_experimental
def unwrap_form(data: bytes, context: dict) -> bytes:
    """EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    Wrapper-form normalization: validate the decode context exactly as
    reconstruct() does, and return the canonical-json payload bytes
    (stripping the 8-byte GTMLEC01 header for compressed input).
    Returns bytes only — it never decodes, interprets, or validates the
    Record inside. Fail-closed with TransformError on any context or
    header problem, with the same UNSUPPORTED reasons reconstruct()
    reports for the wrapper layer.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise TransformError("UNSUPPORTED: data must be non-empty bytes")
    if not isinstance(context, dict):
        raise TransformError("UNSUPPORTED: context must be a dict")
    if context == _CANONICAL_CONTEXT:
        return bytes(data)
    if context == _COMPRESSED_CONTEXT:
        payload = bytes(data)
        if not payload.startswith(COMPRESSED_HEADER):
            raise TransformError(
                "UNSUPPORTED: compressed data missing GTMLEC01 header")
        return payload[len(COMPRESSED_HEADER):]
    raise TransformError(
        f"UNSUPPORTED: unsupported context {context!r}")


@_experimental
def nfc_fold_report(record: Any) -> Dict[str, Any]:
    """EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    MEASUREMENT ONLY — not a normalization step. Reports whether any
    string value in the Record (node labels, literal values, handle
    names, provenance refs, predicate strings) is not NFC-normalized,
    and whether NFC-folding those strings would change serialize()
    bytes. It never modifies the Record and never folds anything:
    serialize() preserves decomposed strings verbatim (DISC-C3/DISC-E2),
    and folding them silently would be an unauthorized semantic
    change — two distinct byte strings must not be collapsed without a
    governing contract. Returns {"non_nfc_fields": [...], "would_change_bytes": bool}.
    """
    from ..transform import serialize  # local import: no cycle at module load

    non_nfc: List[str] = []

    def _check(value: Any, where: str) -> None:
        if isinstance(value, str) and not unicodedata.is_normalized("NFC", value):
            non_nfc.append(where)

    for node in record.nodes:
        _check(node.label, f"node[{node.ident.sid}].label")
        if node.ident.sid is not None:
            _check(node.ident.sid.name, f"node[{node.ident.sid}].ident.sid")
        for ref, _status, _reason in node.provenance.refs:
            _check(ref, f"node[{node.ident.sid}].provenance.ref")
    for stmt in record.statements:
        _check(stmt.predicate, f"statement[{stmt.ident.sid}].predicate")
        _check(stmt.subject.name, f"statement[{stmt.ident.sid}].subject")
        _check(stmt.object.name, f"statement[{stmt.ident.sid}].object")
        for ref, _status, _reason in stmt.provenance.refs:
            _check(ref, f"statement[{stmt.ident.sid}].provenance.ref")
    for lit in record.literals:
        _check(lit.value, f"literal[{lit.value!r}]")

    would_change = False
    if non_nfc:
        # Measure honestly: build the NFC-folded Record and compare
        # serialize() bytes. (Folding the serialized JSON text would NOT
        # measure this, because serialize() escapes non-ASCII as \uXXXX
        # and NFC does not fold escape sequences.)
        folded_nodes = []
        for node in record.nodes:
            folded = node
            if node.label and not unicodedata.is_normalized("NFC", node.label):
                folded = replace(folded, label=unicodedata.normalize("NFC", node.label))
            if node.ident.sid is not None and not unicodedata.is_normalized(
                    "NFC", node.ident.sid.name):
                folded = replace(
                    folded,
                    ident=replace(
                        folded.ident,
                        sid=Handle(node.ident.sid.kind,
                                   unicodedata.normalize("NFC", node.ident.sid.name))))
            refs = tuple(
                (unicodedata.normalize("NFC", ref), status, reason)
                for ref, status, reason in folded.provenance.refs)
            if refs != folded.provenance.refs:
                folded = replace(
                    folded,
                    provenance=ProvenanceManifest(refs=refs,
                                                  endorsement=folded.provenance.endorsement))
            folded_nodes.append(folded)
        folded_stmts = []
        for stmt in record.statements:
            folded = stmt
            if not unicodedata.is_normalized("NFC", stmt.predicate):
                folded = replace(
                    folded, predicate=unicodedata.normalize("NFC", stmt.predicate))
            if not unicodedata.is_normalized("NFC", stmt.subject.name):
                folded = replace(
                    folded, subject=Handle(stmt.subject.kind,
                                           unicodedata.normalize("NFC", stmt.subject.name)))
            if not unicodedata.is_normalized("NFC", stmt.object.name):
                folded = replace(
                    folded, object=Handle(stmt.object.kind,
                                          unicodedata.normalize("NFC", stmt.object.name)))
            refs = tuple(
                (unicodedata.normalize("NFC", ref), status, reason)
                for ref, status, reason in folded.provenance.refs)
            if refs != folded.provenance.refs:
                folded = replace(
                    folded,
                    provenance=ProvenanceManifest(refs=refs,
                                                  endorsement=folded.provenance.endorsement))
            folded_stmts.append(folded)
        folded_lits = [
            replace(lit, value=unicodedata.normalize("NFC", lit.value))
            if not unicodedata.is_normalized("NFC", lit.value) else lit
            for lit in record.literals
        ]
        folded_record = Record(
            version=record.version,
            nodes=tuple(folded_nodes),
            statements=tuple(folded_stmts),
            literals=tuple(folded_lits),
            authority=record.authority,
            ledger_ref=record.ledger_ref,
        )
        would_change = serialize(folded_record) != serialize(record)
    return {"non_nfc_fields": non_nfc, "would_change_bytes": would_change}
