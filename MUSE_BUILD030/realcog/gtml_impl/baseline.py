"""Four-domain architectural baseline — CONTRACT RECORD ONLY.

EXPERIMENTAL WORKING RECORD — NOT HISTORICALLY AUTHENTICATED for the
experimental portions. Authority ZERO. Gate-1 HOLD. Canon UNCHANGED.

This module is the baseline contract record supplied by Jack (2026-11-02
~18:51). It is IMPORT-ONLY: it defines data (law IDs, principle texts,
status labels) and NO executable operators. In particular it contains no
`def` that computes a D4 operator or any transformation.

SEPARATION RULE (Jack 2026-11-02 ~19:39): experimental operator definitions
live in gtml_impl.experimental.operators, NEVER here. The test suite asserts:
  (a) no public function in this module computes baseline-external semantics;
  (b) every experimental operator is absent from BASELINE_LAWS;
  (c) passing tests never promote an experimental definition.

Status labels: RECOVERED = attested in held sources; EXPERIMENTAL = supplied
as working framework without historical authentication; UNRESOLVED = open.
"""
from __future__ import annotations

BASELINE_VERSION = "2026-11-02-jack-supplied"

# ---------------------------------------------------------------------------
# Domain 1 — IDENTITY
# ---------------------------------------------------------------------------
IDENTITY_DOMAIN = {
    "principle": (
        "Object identity, handles, versions, hashes, CHECK, and Seals "
        "remain distinct. A canonical identifier must not automatically be "
        "equated with a content digest. A digest may verify or address "
        "content without establishing the governed object's identity."
    ),
    "status": "RECOVERED",  # principle attested (D02 separation law et al.)
    "laws": {
        # Law IDs are stable identifiers for the separation test registry.
        "ID-1": {
            "text": "canonical identity != projection identity (D02 separation law)",
            "status": "RECOVERED",
        },
        "ID-2": {
            "text": "a content digest never establishes identity",
            "status": "RECOVERED",
        },
        "ID-3": {
            "text": "CHECK verifies content against a binding attestation; "
                    "it does not confer identity",
            "status": "EXPERIMENTAL",
        },
    },
    "remaining_qualification": (
        "Resolve the canon_id = canon_hash implementation conflict "
        "(NOT AUTHORIZED — preserved unresolved)."
    ),
}

# ---------------------------------------------------------------------------
# Domain 2 — SIGL
# ---------------------------------------------------------------------------
SIGL_DOMAIN = {
    "principle": (
        "Machine representation has Authority ZERO and cannot change "
        "Lane-1 decisions."
    ),
    "status": "RECOVERED",
    "laws": {
        "SG-1": {
            "text": "machine representation authority is ZERO, always",
            "status": "RECOVERED",
        },
        "SG-2": {
            "text": "no machine-representation operation changes a Lane-1 decision",
            "status": "RECOVERED",
        },
    },
    "remaining_qualification": "Establish exact round-trip equivalence criteria (UNRESOLVED).",
}

# ---------------------------------------------------------------------------
# Domain 3 — D1-D4
# ---------------------------------------------------------------------------
D1D4_DOMAIN = {
    "principle": (
        "Cumulative, non-destructive relational progression. "
        "D4=(D1+D2+D3)xSPACExTIME is preserved as the supplied architectural "
        "expression; its multiplication and addition operators still need "
        "formally defined semantics before the expression can serve as "
        "executable algebra."
    ),
    "status": "EXPERIMENTAL",  # expression supplied; operator semantics unresolved
    "laws": {
        "D-EXPR": {
            "text": "D4=(D1+D2+D3)xSPACExTIME (supplied architectural expression, "
                    "preserved verbatim; operator semantics UNRESOLVED)",
            "status": "EXPERIMENTAL",
        },
        "D-CUM": {
            "text": "the D1-D4 progression is cumulative and non-destructive",
            "status": "EXPERIMENTAL",
        },
    },
    "remaining_qualification": (
        "Verify graph semantics, traversal rules, and D4 temporal bindings. "
        "Operator semantics for + and x are UNRESOLVED in the baseline; "
        "experimental definitions live in gtml_impl.experimental.operators "
        "and are NOT baseline laws."
    ),
}

# ---------------------------------------------------------------------------
# Domain 4 — TIME
# ---------------------------------------------------------------------------
TIME_DOMAIN = {
    "principle": (
        "Independent event and knowledge time; immutable historical record. "
        "Deterministic historical replay must preserve what was known at a "
        "given ledger time, even when later evidence changes the current "
        "interpretation."
    ),
    "status": "RECOVERED",  # principle attested; ES-T-002 bitemporal model PROPOSED
    "laws": {
        "T-1": {
            "text": "event time and knowledge time are independent axes; "
                    "distinguishing them requires more than storing two timestamps",
            "status": "RECOVERED",
        },
        "T-2": {
            "text": "the historical record is immutable; replay preserves "
                    "what was known at ledger time",
            "status": "RECOVERED",
        },
    },
    "remaining_qualification": (
        "Verify replay, clock uncertainty, and historical reconstruction."
    ),
}

DOMAINS = {
    "identity": IDENTITY_DOMAIN,
    "sigl": SIGL_DOMAIN,
    "d1d4": D1D4_DOMAIN,
    "time": TIME_DOMAIN,
}

# Registry of baseline law IDs. The separation test asserts that no
# experimental operator ID ever appears here, and that no law ID is
# computed by experimental code.
BASELINE_LAWS = frozenset(
    law_id
    for domain in DOMAINS.values()
    for law_id in domain["laws"]
)
