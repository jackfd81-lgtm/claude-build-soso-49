"""Shared structural provenance checks for stored relationships (MUSE-FUNCTIONAL-009).

Lives below both contract.relationship_engine and contract.mac002_adapter so that
the check can be enforced at the shared execution boundary
(RelationshipEngine.retrieve_and_apply) without an import cycle. A stored
`qualification == "VALID"` label is a CLAIM, never evidence."""
from __future__ import annotations

from typing import Any, Mapping

_UNTRUSTED_SOURCES = frozenset({"unknown", "none", "null", "fake", "fabricated", "unverified", "n/a", "tbd"})


def provenance_defects(record: Mapping[str, Any]) -> list[str]:
    """Structural provenance check (no observation store needed): returns the
    defect reasons for a stored relationship's `provenance` field. Single source
    of truth, shared by qualify_record_against_observations() and the stored-
    record path of eval022/mac002.py (MUSE-REGRESSION-001)."""
    prov = record.get("provenance")
    if prov is None or prov == [] or prov == "":
        return ["missing_provenance"]
    if not isinstance(prov, list) or not all(isinstance(x, str) and x.strip() for x in prov):
        return ["malformed_provenance"]
    for src in prov:
        sl = src.strip().lower()
        if sl in _UNTRUSTED_SOURCES or sl.startswith(("fake:", "fabricated:", "unverified:")):
            return ["fabricated_or_untrusted_provenance"]
    return []


def evidence_id_defects(record: Mapping[str, Any]) -> list[str]:
    """A VALID label is a claim, not evidence: a stored relationship must name
    the observations that support it."""
    ev_ids = record.get("evidence_ids")
    if not isinstance(ev_ids, list) or not ev_ids or not all(isinstance(x, str) and x.strip() for x in ev_ids):
        return ["missing_evidence_ids"]
    return []


def stored_record_trust_defects(record: Mapping[str, Any]) -> list[str]:
    """All structural reasons a stored relationship may NOT be promoted to an
    authoritative answer, regardless of its historical `qualification` label."""
    return sorted(set(provenance_defects(record) + evidence_id_defects(record)))
