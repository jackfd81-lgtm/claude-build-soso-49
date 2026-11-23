"""BrainBridge <-> Suit Gate wiring (experimental integration, AUTH:0).

Wires the Suit Gate Engine -- the OD-05 enforcement in the applied 0.3.0
base tree (exp-impl-0.3.0-defect-corrections, suite 262/262) -- to the
BrainBridge in ph6_brains_2208.py, as the injected suit_gate callback.

This module adds NO new rules. It translates a Big 49 payload dict into
the Suit's typed IR and calls the Suit's own enforcement:

  Suit side (applied tree, src/gtml_impl/):
    - frontend.validate_causal(record): the OD-05 gate. Rule 1: a CAUSES
      statement without a (non-empty) causal_basis raises FrontendError
      (presence judged by truthiness; an empty string counts as no basis).
      Rule 2 (widened): promotes_to == CAUSES raises REGARDLESS of basis.
    - ir.py: Statement.causal_basis is Optional[str]; promotes_to is
      Optional[str]. Record.authority is pinned "ZERO" by construction.

  Translation (faithful, no invented fields):
    - predicate <- payload['relation']. (Big relation values used here --
      CAUSES, ASSOCIATED_WITH -- are both members of the Suit RELATIONS
      set. A payload relation outside RELATIONS raises the IR's own
      validation error at construction: loud failure, never a silent
      pass or deny, and still fail-closed -- BrainBridge.submit calls
      the gate before any small-memory mutation, so no write happens.)
    - causal_basis <- payload['causal_basis'], EXCEPT the bridge's own
      absence marker 'UNDETERMINED' maps to None. The bridge itself
      equates 'UNDETERMINED' with no basis (put(): G_P with basis in
      (None, '', 'UNDETERMINED') -> PROVENANCE_BASIS_REQUIRED; its
      dual-bridge Record default causal_basis is 'UNDETERMINED', "no
      basis recorded"). The Suit gate judges a real basis by truthiness,
      so passing 'UNDETERMINED' through as a basis would misrepresent it.
    - promotes_to <- None. The Big payload carries no promotion claim;
      none is invented.
    - sid <- payload['record_id'] (the statement IS the record).
    - subject/object <- neutral OID placeholders. The Big payload carries
      no subject/object decomposition and validate_causal does not
      inspect them; they exist only because the IR requires them.

  Bridge side (ph6_brains_2208.py):
    - put() enforces its own MISSING_CAUSAL_BASIS (CAUSES with falsy
      basis), PROVENANCE_BASIS_REQUIRED, EXT_PROVENANCE_MINT_DENIED,
      BLOCK_CHAR_LIMIT, AUTHORITY_ESCALATION, CAPACITY_EXHAUSTED, ...
    - BrainBridge.submit() calls suit_gate BEFORE any small-memory
      mutation; a non-True return raises SUIT_GATE_DENIED.

Scope: the gate wired here is the experimental GTML causal gate
(OD-05, applied tree), NOT the frozen Suit 4.0 complete run_pipeline()
call. The packet's own README states this boundary. A CAUSES claim that
passes is still a CLAIM at Authority ZERO, never evidence and never a
Lane 1 verdict.

2026-11-14 fix note: Big's put() now rejects CAUSES with the literal
'UNDETERMINED' directly (MISSING_CAUSAL_BASIS), closing the put-path
divergence this adapter originally proved the gate through. The
'UNDETERMINED' -> None translation above remains necessary for payloads
that carry the marker from other paths (older writers, migrations),
which the integration suite proves the gate still denies.
"""
from __future__ import annotations

import sys
from pathlib import Path

# --- locate the applied Suit tree (never edit it; read-only import) -------
_BRIDGE_DIR = Path(__file__).resolve().parent
_SUIT_SRC = _BRIDGE_DIR
if not (_SUIT_SRC / "gtml_impl" / "ir.py").exists():
    raise RuntimeError(f"applied Suit tree not found at {_SUIT_SRC}")
sys.path.insert(0, str(_SUIT_SRC))

from gtml_impl import ir as suit_ir  # noqa: E402
from gtml_impl.frontend import FrontendError, validate_causal  # noqa: E402

from ph6_brains_2208 import Big49, Small49, BrainBridge  # noqa: E402

SUIT_VERSION = suit_ir.VERSION  # "exp-impl-0.2.0-hardening"

# The bridge's own marker for "no basis recorded" (its default, and the
# value its G_P rule equates with None/''). Never a real basis.
_NO_BASIS = (None, "", "UNDETERMINED")


def suit_gate_for_big_payload(payload: dict) -> bool:
    """The injected suit_gate: True = gate passed, False = gate denied.

    Never raises for a gate denial: BrainBridge requires a boolean and
    raises SUIT_GATE_DENIED itself on any non-True return. A malformed
    payload (relation outside the Suit RELATIONS set) raises the IR's
    own validation error instead of being silently passed or denied.
    """
    basis = payload.get("causal_basis")
    if basis in _NO_BASIS:
        basis = None
    stmt = suit_ir.Statement(
        ident=suit_ir.Ident(sid=suit_ir.Handle("SID", str(payload["record_id"]))),
        subject=suit_ir.Handle("OID", "big-subject"),
        object=suit_ir.Handle("OID", "big-object"),
        predicate=payload["relation"],
        causal_basis=basis,
        promotes_to=None,
    )
    record = suit_ir.Record(version=SUIT_VERSION, statements=(stmt,))
    try:
        validate_causal(record)
    except FrontendError:
        return False
    return True


def make_bridge(big: Big49, small: Small49) -> BrainBridge:
    """Build a BrainBridge whose suit_gate is the real Suit OD-05 gate."""
    return BrainBridge(big, small, suit_gate_for_big_payload)
