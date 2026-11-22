"""Provenance subsystem for exp-impl-0.2.0-hardening.

EXPERIMENTAL — NOT HISTORICALLY AUTHENTICATED. Authority ZERO.
Gate-1 HOLD. Canon UNCHANGED.

DP-01 ratified discipline, adapted to the IR:
- One entry per distinct ref, sorted byte-lexicographically by ref.
- identity_status is RESOLVED or UNRESOLVED; conflicting statuses for the
  same ref are fail-closed (ProvenanceError).
- RESOLVED entries carry an EMPTY unresolved_reason: per DP-01, RESOLVED
  implies identity_basis BUNDLE_ITEM_SELF_DECLARED by convention — the
  basis is pinned, not recorded per entry. RESOLVED != authenticated
  (DP-01 P-4): a RESOLVED status means the ref is representable as
  supplied, never that it has been validated.
- endorsement is ALWAYS NONE (enforced by ProvenanceManifest).
- manifest_digest digests the canonical bytes of the manifest EXCLUDING
  any content_ref of the manifest itself (self-exclusion, DP-01 G-4
  disposition). The caller supplies serialize_fn; it must be a pure
  bytes-producing function that embeds no self content_ref.

This module is dependency-free within the package: it imports only the
stdlib and .ir (the core types). It never imports transform, engine, or
frontend — transform.serialize may be PASSED IN as serialize_fn, never
imported.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Dict, Iterable, List, Tuple

from .ir import ContentDigest, ProvenanceManifest

VERSION = "exp-impl-0.2.0-hardening"

Entry = Tuple[str, str, str]  # (ref, identity_status, unresolved_reason)


class ProvenanceError(Exception):
    """Fail-closed provenance error."""


_RESOLVED = "RESOLVED"
_UNRESOLVED = "UNRESOLVED"


def _check_entry(entry: object) -> Entry:
    try:
        ref, status, reason = entry  # type: ignore[misc]
    except (TypeError, ValueError) as exc:
        raise ProvenanceError(f"entry must be a (ref, status, reason) triple: {entry!r}") from exc
    if not isinstance(ref, str) or not ref:
        raise ProvenanceError("ref must be a non-empty string")
    if status not in (_RESOLVED, _UNRESOLVED):
        raise ProvenanceError(f"bad identity_status: {status!r}")
    if not isinstance(reason, str):
        raise ProvenanceError("unresolved_reason must be a string")
    if status == _RESOLVED and reason != "":
        raise ProvenanceError(
            "RESOLVED entries must carry an empty unresolved_reason "
            "(identity_basis BUNDLE_ITEM_SELF_DECLARED is implied)"
        )
    if status == _UNRESOLVED and reason == "":
        raise ProvenanceError("UNRESOLVED entries must carry a non-empty unresolved_reason")
    return (ref, status, reason)


def build_manifest(entries: Iterable[Entry]) -> ProvenanceManifest:
    """Build a ProvenanceManifest from (ref, identity_status, unresolved_reason)
    entries, sorted byte-lexicographically by ref with one entry per ref.

    Fail-closed: conflicting (status, reason) pairs for the same ref raise
    ProvenanceError; identical duplicate triples are deduped silently.
    """
    by_ref: Dict[str, Tuple[str, str]] = {}
    for raw in entries:
        ref, status, reason = _check_entry(raw)
        if ref in by_ref:
            if by_ref[ref] != (status, reason):
                raise ProvenanceError(
                    f"conflicting entries for ref {ref!r}: "
                    f"{by_ref[ref]!r} vs {(status, reason)!r}"
                )
            continue  # identical duplicate: dedupe
        by_ref[ref] = (status, reason)
    ordered = sorted(by_ref.items(), key=lambda kv: kv[0].encode("utf-8"))
    refs = tuple((ref, status, reason) for ref, (status, reason) in ordered)
    return ProvenanceManifest(refs=refs)


def manifest_digest(
    manifest: ProvenanceManifest,
    serialize_fn: Callable[[ProvenanceManifest], bytes],
) -> ContentDigest:
    """Digest the canonical bytes of a manifest, excluding any self content_ref.

    The manifest carries no content_ref of itself, so the digest covers
    exactly (refs, endorsement); callers must ensure serialize_fn does not
    embed a self content_ref (DP-01 G-4). serialize_fn must return bytes.
    """
    if not isinstance(manifest, ProvenanceManifest):
        raise ProvenanceError(
            f"manifest_digest requires a ProvenanceManifest, got {type(manifest).__name__}"
        )
    if not callable(serialize_fn):
        raise ProvenanceError("serialize_fn must be callable")
    try:
        data = serialize_fn(manifest)
    except Exception as exc:
        raise ProvenanceError(f"serialize_fn raised: {exc}") from exc
    if not isinstance(data, (bytes, bytearray)):
        raise ProvenanceError("serialize_fn must return bytes")
    return ContentDigest(algorithm="sha256", hex=hashlib.sha256(bytes(data)).hexdigest())


def quote_bundle(bundle: Dict[str, object]) -> Dict[str, List[Entry]]:
    """Faithfully quote a caller-supplied bundle into manifest entries.

    DP-01 P-4: RESOLVED != authenticated. This function performs NO honesty
    validation — a (possibly dishonest) bundle is quoted exactly as
    supplied, so downstream consumers can see the claims without them being
    laundered into validated facts. Only structural unpacking is performed;
    pass the returned entries through build_manifest for fail-closed checks.

    Bundle shape: {ref: (identity_status, unresolved_reason)} or
    {ref: {"identity_status": ..., "unresolved_reason": ...}}.
    Returns {"entries": [(ref, identity_status, unresolved_reason), ...]}
    in the bundle's own order.
    """
    if not isinstance(bundle, dict):
        raise ProvenanceError(f"quote_bundle requires a dict, got {type(bundle).__name__}")
    entries: List[Entry] = []
    for ref, value in bundle.items():
        if isinstance(value, dict):
            status = value.get("identity_status")
            reason = value.get("unresolved_reason")
        else:
            try:
                status, reason = value  # type: ignore[misc]
            except (TypeError, ValueError) as exc:
                raise ProvenanceError(
                    f"bundle value for ref {ref!r} is not a (status, reason) pair"
                ) from exc
        entries.append((ref, status, reason))
    return {"entries": entries}
