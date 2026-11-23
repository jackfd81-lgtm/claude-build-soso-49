"""EXPERIMENTAL operator definitions for D1-D4.

EXPERIMENTAL — NOT HISTORICALLY AUTHENTICATED. NOT BASELINE LAW.
Authority ZERO.

SEPARATION NOTICE (Jack 2026-11-02 ~19:39): these are provisional,
explicitly-labeled experimental definitions. They must NEVER be presented
as baseline laws, and successful execution NEVER promotes them. Only an
explicit owner act promotes an experimental definition. The test suite
asserts this separation.

Each public function carries EXPERIMENTAL=True as a function attribute.
The baseline D4 expression D4=(D1+D2+D3)xSPACExTIME is preserved verbatim
in baseline.py; the operator semantics below are one experimental
instantiation, labeled as such.

Operator semantics (EXPERIMENTAL DEFINITIONS, not recovered history):
- D1: direct lineage — a set of (child_handle, parent_handle) edges.
- D2: recursive ancestry — transitive closure of D1, computed deterministically.
- D3: typed cross-lineage bridges — (from_handle, bridge_type, to_handle) edges
  between distinct lineages.
- `+` (d4_add): disjoint union of relation multisets. Non-destructive: no
  input is modified; duplicates across operands are kept distinct by tagging
  each element with its operand origin (left/right).
- `x` (d4_mul): cartesian pairing of each relation element with a spatial
  coordinate and a temporal coordinate, producing (element, space, time)
  triples. SPACE and TIME coordinates are opaque strings here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Iterable, Tuple

from ..ir import Handle


def _experimental(fn):
    """Mark a function as an experimental definition (not a baseline law)."""
    fn.EXPERIMENTAL = True
    return fn


# A relation element: (subject, predicate, object) with string handles.
RelElement = Tuple[str, str, str]
# A D4 triple: (relation element, space coordinate, time coordinate).
D4Triple = Tuple[RelElement, str, str]


@_experimental
def d1_edges(pairs: Iterable[Tuple[Handle, Handle]]) -> FrozenSet[Tuple[str, str]]:
    """EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    D1 direct lineage: the set of (child, parent) handle-string edges.
    """
    return frozenset((str(c), str(p)) for c, p in pairs)


@_experimental
def d2_closure(d1: FrozenSet[Tuple[str, str]]) -> FrozenSet[Tuple[str, str]]:
    """EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    D2 recursive ancestry: transitive closure of D1, computed by
    deterministic fixpoint iteration (sorted order for determinism).
    D1 is preserved as a subset (cumulative, non-destructive).
    """
    closure = set(d1)
    changed = True
    while changed:
        changed = False
        for a, b in sorted(closure):
            for c, d in sorted(closure):
                if b == c and (a, d) not in closure:
                    closure.add((a, d))
                    changed = True
    return frozenset(closure)


@dataclass(frozen=True)
class Bridge:
    """EXPERIMENTAL: a typed cross-lineage bridge (D3)."""
    from_handle: str
    bridge_type: str
    to_handle: str


@_experimental
def d3_bridges(bridges: Iterable[Bridge]) -> FrozenSet[Bridge]:
    """EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    D3: the set of typed cross-lineage bridges, as supplied.
    """
    return frozenset(bridges)


@_experimental
def d4_add(left: FrozenSet, right: FrozenSet) -> FrozenSet[Tuple[str, object]]:
    """EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    Experimental `+`: disjoint union of relation multisets. Each element is
    tagged with its operand origin ("L"/"R") so the union is non-destructive
    and origin-preserving. No input is modified.
    """
    return (frozenset(("L", e) for e in left)
            | frozenset(("R", e) for e in right))


@_experimental
def d4_mul(elements: FrozenSet,
           spaces: FrozenSet[str],
           times: FrozenSet[str]) -> FrozenSet[D4Triple]:
    """EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    Experimental `x`: cartesian pairing of each element with each space
    coordinate and each time coordinate, yielding (element, space, time)
    triples. Deterministic; inputs unmodified.
    """
    return frozenset(
        (element, space, time)
        for element in sorted(elements, key=repr)
        for space in sorted(spaces)
        for time in sorted(times)
    )


@_experimental
def d4_expression(d1: FrozenSet[Tuple[str, str]],
                  spaces: FrozenSet[str],
                  times: FrozenSet[str],
                  bridges: FrozenSet[Bridge] = frozenset()) -> FrozenSet[D4Triple]:
    """EXPERIMENTAL DEFINITION — NOT A BASELINE LAW.

    One experimental instantiation of D4=(D1+D2+D3)xSPACExTIME:
      D4 = ((D1 + D2) + D3) x SPACE x TIME
    where D2 = closure(D1). Cumulative and non-destructive throughout.

    NOTE (defect fix, measured 2026-11-10): nested d4_add calls tag
    elements more than once (("L", ("L", e))). The final pairing strips
    ALL origin tags recursively, because relation elements are 3-tuples
    and tags are 2-tuples ("L"/"R", element) — the two shapes never
    collide. Origin information is NOT retained in the output triples;
    the union stage is origin-preserving internally, the final pairing
    is not. (Earlier code stripped only one tag level, leaving
    ("L", (...)) tuples inside D4 triples — wrong.)
    """
    d2 = d2_closure(d1)
    # Represent D3 bridges as relation elements for the union.
    d3_elements = frozenset(
        (b.from_handle, f"BRIDGE:{b.bridge_type}", b.to_handle) for b in bridges)
    d1_elements = frozenset((c, "LINEAGE", p) for c, p in d1)
    d2_elements = frozenset((c, "ANCESTOR", p) for c, p in d2)
    combined = d4_add(d4_add(d1_elements, d2_elements), d3_elements)
    untagged = frozenset(_untag(e) for _, e in combined)
    return d4_mul(untagged, spaces, times)


def _untag(element: object) -> object:
    """Recursively strip d4_add origin tags ("L"/"R", element).

    Tags are 2-tuples whose first item is "L" or "R"; relation elements
    are 3-tuples, so stripping is unambiguous.
    """
    while (isinstance(element, tuple) and len(element) == 2
           and element[0] in ("L", "R")):
        element = element[1]
    return element


# Registry of experimental operator IDs. The separation test asserts these
# never appear in baseline.BASELINE_LAWS.
EXPERIMENTAL_OPERATORS = frozenset({
    "d1_edges", "d2_closure", "d3_bridges", "d4_add", "d4_mul", "d4_expression",
})
