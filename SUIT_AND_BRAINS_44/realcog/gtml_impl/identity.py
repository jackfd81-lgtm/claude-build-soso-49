"""Identity subsystem for exp-impl-0.2.0-hardening.

EXPERIMENTAL — NOT HISTORICALLY AUTHENTICATED. Authority ZERO.

Implements the four-domain baseline IDENTITY domain:
- Object identity, handles, versions, hashes, CHECK, and Seals remain distinct.
- A canonical identifier is NEVER equated with a content digest. A digest may
  verify or address content without establishing the governed object's identity.
- The gtml_project.py:420 `canon_id = canon_hash` equation is REJECTED here
  and is never replicated: there is no code path from ContentDigest to Handle.

Focus area 1: identity preservation.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .ir import ContentDigest, Handle, ID_KINDS, IRValidationError


class IdentityError(Exception):
    """Fail-closed identity operation error."""


def assign_handle(kind: str, name: str) -> Handle:
    """Assign a governed handle. Deterministic; never derived from a digest.

    RECOVERED principle (baseline: identity). The assignment is a pure
    function of (kind, name) — no digest input exists in the signature.
    """
    return Handle(kind=kind, name=name)


def content_digest(canonical_bytes: bytes) -> ContentDigest:
    """Compute the content digest of canonical bytes.

    The digest is for verification/addressability ONLY. It carries no
    identity semantics. There is intentionally no function converting a
    ContentDigest into a Handle.
    """
    if not isinstance(canonical_bytes, (bytes, bytearray)):
        raise IdentityError("canonical_bytes must be bytes")
    return ContentDigest(algorithm="sha256",
                         hex=hashlib.sha256(bytes(canonical_bytes)).hexdigest())


@dataclass(frozen=True)
class BindingAttestation:
    """A recorded claim that a handle refers to content with a given digest.

    Quotation, not identification: the attestation RECORDS the claim; it does
    not prove it, and it never merges the two namespaces.
    """
    handle: Handle
    digest: ContentDigest
    basis: str  # e.g. "BUNDLE_ITEM_SELF_DECLARED"
    endorsement: str = "NONE"

    def __post_init__(self) -> None:
        if self.endorsement != "NONE":
            raise IdentityError("endorsement must always be NONE")


def check_binding(attestation: BindingAttestation,
                  canonical_bytes: bytes) -> bool:
    """CHECK: verify that canonical_bytes digest-matches the attested digest.

    Returns True/False. A True result verifies CONTENT, not identity: the
    handle's governed identity is unchanged by the outcome. This is the
    baseline CHECK operation, kept distinct from both handle and digest.
    """
    expected = content_digest(canonical_bytes)
    return expected == attestation.digest


@dataclass(frozen=True)
class Seal:
    """A seal binds handle + digest + authority at a point in time.

    The seal is a RECORD of the binding; the handle and digest remain
    distinct values inside it. authority is pinned ZERO.
    """
    handle: Handle
    digest: ContentDigest
    authority: str = "ZERO"
    note: str = ""

    def __post_init__(self) -> None:
        if self.authority != "ZERO":
            raise IdentityError("Seal authority must be ZERO")


def seal(handle: Handle, digest: ContentDigest, note: str = "") -> Seal:
    """Create a seal. Does not equate its components."""
    return Seal(handle=handle, digest=digest, note=note)


# ---------------------------------------------------------------------------
# Anti-equation guardrails (also asserted by the test suite)
# ---------------------------------------------------------------------------
def handles_from_digest_forbidden() -> None:
    """Documents the absent operation: there is NO digest->handle path.

    Raises unconditionally; the test suite asserts this function exists and
    raises, as a tripwire against future introduction of the equation.
    """
    raise IdentityError(
        "digest-to-handle conversion is forbidden: "
        "a content digest never establishes identity")
