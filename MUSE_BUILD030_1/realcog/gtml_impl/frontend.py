"""Frontend: held 0.2.3-d002a parser AST -> typed IR.

EXPERIMENTAL — NOT HISTORICALLY AUTHENTICATED. Authority ZERO.
Gate-1 HOLD. Canon UNCHANGED.

The parser is imported READ-ONLY from the held d002a tree; it is never
copied, vendored, or modified. D-002 Alternative A gated promotion is
already applied by the parser — this module only builds Statements where
the parser emitted a relation/typed_relation node, and never invents
relations from bare word sequences.

Fail-closed: any AST node type outside the mapping below raises
FrontendError. Unknown-state vocabulary is never collapsed:
UNKNOWN_ORIGIN (epistemic) != UNCERTAIN (epistemic: the source text
explicitly marks the span uncertain) != UNKNOWN (availability)
!= MISSING (availability) != UNRESOLVED (knowledge).

OD-04 (experimental variant; owner-confirmed Option A): the lowerer
reads the held parser's span-level AST `uncertainty` flag and applies
epistemic=UNCERTAIN, per lowered Node/Statement, to every AST node
whose flag is True. When the flag is False the default
UNKNOWN_ORIGIN is kept. Carrying is per-node: each lowered object
reports its own source span's flag; a statement-only or operand-only
reading would carry strictly less, and is NOT implemented here.

Focus areas served: identity preservation (handles assigned, never
derived), unknown-state handling.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import identity as _identity
from .ir import (
    EpiState,
    Handle,
    Ident,
    Literal,
    Node,
    ProvenanceManifest,
    Record,
    Statement,
    TemporalMark,
    RELATIONS,
)
from .provenance import build_manifest

VERSION = "exp-impl-0.2.0-hardening"

_PARSER_DIR = str(
    Path.home()
    / "workspace/goals/tml-reference-implementation/tml-master-language-study-v01"
    / "gtml-d002-alternative-a"
)


class FrontendError(Exception):
    """Fail-closed frontend error (parse or lowering failure)."""


def _load_parser():
    """Import the held parser read-only. Raises FrontendError on failure."""
    if _PARSER_DIR not in sys.path:
        sys.path.insert(0, _PARSER_DIR)
    try:
        import parser as _parser  # held tree; read-only import
    except Exception as exc:
        raise FrontendError(
            f"could not import held 0.2.3-d002a parser: {exc}"
        ) from exc
    return _parser


def parse_text(text: str) -> Dict[str, Any]:
    """Parse text with the held 0.2.3-d002a parser.

    Returns the parser's AST dict. Wraps the parser's ParseError as
    FrontendError. Any other exception from the parser propagates
    unwrapped (it is not a parse error the frontend can classify).
    """
    if not isinstance(text, str):
        raise FrontendError("parse_text requires a string")
    _parser = _load_parser()
    try:
        return _parser.parse(text)
    except _parser.ParseError as exc:
        raise FrontendError(f"parser rejected input: {exc}") from exc


# ---------------------------------------------------------------------------
# AST -> IR lowering
# ---------------------------------------------------------------------------

# Node types with an explicit mapping. Anything else -> FrontendError.
_MAPPED_TYPES = frozenset({
    "word", "word_seq", "unknown", "missing", "tag", "uncertain",
    "compound", "phrase", "brace", "relation", "typed_relation",
    "alternatives", "candidate_join", "construction", "definition",
    "ladder", "sequence",
})

# Subset with executable mapping rules. OD-04 adds lowering rules for
# 'uncertain' and 'sequence' (see _lower_uncertain / _lower_sequence);
# both previously failed closed per spec.
_STRUCTURAL_TYPES = frozenset({"tag", "brace", "compound", "phrase", "construction"})


def _causal_key(subject: Handle, predicate: str, object_: Handle) -> str:
    """Stable lookup key for a caller-supplied causal basis.

    Keyed by the rendered triple "SID:A|CAUSES|SID:B" (handles as
    "KIND:name" strings). Word operands carry text SIDs, so a caller
    that knows the input text can compute the key; operands lowered
    to generated node-NNNN SIDs need the lowering order (see the
    nested-typed_relation test, which pins the derivation). This is an
    EXPERIMENTAL supply channel only — the surface syntax carries no
    basis annotation, so without a caller-supplied mapping a CAUSES
    statement's basis is always absent.
    """
    return f"{subject}|{predicate}|{object_}"


class _Lowerer:
    """Deterministic AST->IR lowerer. One instance per lower() call."""

    def __init__(self, causal_bases: Optional[Dict[str, str]] = None) -> None:
        self._counter = 0
        self._causal_bases: Dict[str, str] = dict(causal_bases or {})
        self.nodes: List[Node] = []
        self.statements: List[Statement] = []
        self.literals: List[Literal] = []

    # -- identity ------------------------------------------------------
    def _next_sid(self) -> Handle:
        self._counter += 1
        return _identity.assign_handle("SID", f"node-{self._counter:04d}")

    # -- entry ---------------------------------------------------------
    # Returns the lowered Node or Statement. Statements (from relation /
    # typed_relation nodes) are appended to self.statements; Nodes to
    # self.nodes. Parents simply ensure children are lowered — nesting a
    # relation inside another construct is allowed: the nested relation
    # becomes a Statement, and its statement SID may serve as a referent
    # handle (experimental statement reification by handle reference;
    # no new relation is ever invented).
    def lower_node(self, ast: Dict[str, Any]) -> "Node | Statement":
        ntype = ast.get("type")
        if ntype not in _MAPPED_TYPES:
            raise FrontendError(f"unmapped AST node type: {ntype!r}")
        if ntype == "word":
            return self._lower_word(ast)
        if ntype == "word_seq":
            return self._lower_word_seq(ast)
        if ntype == "unknown":
            return self._lower_unknown(ast)
        if ntype == "missing":
            return self._lower_missing(ast)
        if ntype in _STRUCTURAL_TYPES:
            return self._lower_structural(ast)
        if ntype == "relation":
            return self.lower_relation(ast)
        if ntype == "typed_relation":
            return self.lower_typed_relation(ast)
        if ntype == "ladder":
            return self._lower_ladder(ast)
        if ntype == "definition":
            return self._lower_definition(ast)
        if ntype in ("candidate_join", "alternatives"):
            return self._lower_no_selection(ast)
        if ntype == "uncertain":
            return self._lower_uncertain(ast)
        if ntype == "sequence":
            return self._lower_sequence(ast)
        # Unreachable for any ntype in _MAPPED_TYPES (all 17 dispatch
        # above); kept as a defensive guard, never a guess.
        raise FrontendError(f"no lowering rule for AST node type: {ntype!r}")

    # -- epistemic mapping (OD-04) ------------------------------------
    def _epi(self, ast: Dict[str, Any], availability: str = "UNKNOWN") -> EpiState:
        """EpiState for one lowered object from its source AST node.

        OD-04 Option A, per-node carrier: the parser's span-level
        `uncertainty` flag ("a ? appears in this node's span") maps to
        epistemic=UNCERTAIN; a False flag keeps the default
        UNKNOWN_ORIGIN. Availability is untouched by the flag (MISSING
        stays MISSING; the flag never changes availability).
        """
        epistemic = "UNCERTAIN" if ast.get("uncertainty") else "UNKNOWN_ORIGIN"
        return EpiState(epistemic, availability)

    # -- leaves --------------------------------------------------------
    def _lower_word(self, ast: Dict[str, Any]) -> Node:
        text = ast.get("text")
        if not isinstance(text, str) or not text:
            raise FrontendError("word node without text")
        sid = _identity.assign_handle("SID", text)
        self.literals.append(Literal(value=text, datatype="string"))
        node = Node(ident=Ident(sid=sid), kind="LEXICAL", label=text,
                    epi=self._epi(ast))
        self.nodes.append(node)
        return node

    def _lower_word_seq(self, ast: Dict[str, Any]) -> Node:
        children = ast.get("children", [])
        texts: List[str] = []
        for child in children:
            if child.get("type") != "word" or not child.get("text"):
                raise FrontendError("word_seq with non-word child")
            texts.append(child["text"])
        label = " ".join(texts)
        if not label:
            raise FrontendError("word_seq with no words")
        sid = _identity.assign_handle("SID", label)
        self.literals.append(Literal(value=label, datatype="string"))
        node = Node(ident=Ident(sid=sid), kind="LEXICAL", label=label,
                    epi=self._epi(ast))
        self.nodes.append(node)
        return node

    def _lower_unknown(self, ast: Dict[str, Any]) -> Node:
        node = Node(
            ident=Ident(sid=self._next_sid()),
            kind="UNKNOWN",
            label=ast.get("marker", "?"),
            epi=self._epi(ast),
        )
        self.nodes.append(node)
        return node

    def _lower_missing(self, ast: Dict[str, Any]) -> Node:
        # MISSING is availability, distinct from UNKNOWN — never collapsed.
        # The uncertainty flag never applies to a missing node (the parser
        # never sets it there), so this keeps UNKNOWN_ORIGIN / MISSING.
        node = Node(
            ident=Ident(sid=self._next_sid()),
            kind="MISSING",
            label=ast.get("marker", "Ø"),
            epi=self._epi(ast, "MISSING"),
        )
        self.nodes.append(node)
        return node

    # -- structural ----------------------------------------------------
    def _child_nodes(self, parts: List[Dict[str, Any]]) -> None:
        # Lower children for their side effects (appended to nodes /
        # statements in lowering order). Parents carry no operand payload
        # beyond their label: no invented semantics.
        for part in parts:
            self.lower_node(part)

    def _lower_structural(self, ast: Dict[str, Any]) -> Node:
        ntype = ast["type"]
        parts: List[Dict[str, Any]] = []
        if ntype in ("tag", "phrase", "brace"):
            child = ast.get("child")
            if child is None:
                raise FrontendError(f"{ntype} node without child")
            parts = [child]
        elif ntype in ("compound",):
            parts = list(ast.get("children", []))
        elif ntype == "construction":
            left, right = ast.get("left"), ast.get("right")
            if left is None or right is None:
                raise FrontendError("construction node without left/right")
            parts = [left, right]
        # Children are lowered (and recorded) before the parent; the
        # parent carries no operand payload beyond its label: no invented
        # semantics.
        self._child_nodes(parts)
        node = Node(
            ident=Ident(sid=self._next_sid()),
            kind="STRUCTURAL",
            label=ntype,
            epi=self._epi(ast),
        )
        self.nodes.append(node)
        return node

    def _lower_ladder(self, ast: Dict[str, Any]) -> Node:
        self._child_nodes(list(ast.get("rungs", [])))
        node = Node(ident=Ident(sid=self._next_sid()), kind="LADDER", label="ladder",
                    epi=self._epi(ast))
        self.nodes.append(node)
        return node

    def _lower_definition(self, ast: Dict[str, Any]) -> Node:
        name, value = ast.get("name"), ast.get("value")
        if name is None or value is None:
            raise FrontendError("definition node without name/value")
        self._child_nodes([name, value])
        node = Node(
            ident=Ident(sid=self._next_sid()), kind="DEFINITION", label="definition",
            epi=self._epi(ast),
        )
        self.nodes.append(node)
        return node

    def _lower_no_selection(self, ast: Dict[str, Any]) -> Node:
        # candidate_join / alternatives: no selection made by the parser.
        # Recorded as UNRESOLVED knowledge, never silently bound.
        ntype = ast["type"]
        self._child_nodes(list(ast.get("children", [])))
        manifest = build_manifest(
            [(ntype, "UNRESOLVED", "NO_SELECTION_MADE")]
        )
        node = Node(
            ident=Ident(sid=self._next_sid()),
            kind="STRUCTURAL",
            label=ntype,
            epi=self._epi(ast),
            provenance=manifest,
        )
        self.nodes.append(node)
        return node

    def _lower_uncertain(self, ast: Dict[str, Any]) -> Node:
        # OD-04 experimental lowering rule for 'uncertain' ("?A": a "?"
        # prefix marking its child uncertain). Direct reading:
        #   - the child is lowered for its side effects and recorded as
        #     its own node (never silently unwrapped: F-C09's concern);
        #   - the wrapper is a distinct UNCERTAIN node carrying the
        #     marker, with epistemic=UNCERTAIN (the flag is always True
        #     for this type by parser construction).
        # Ambiguity, reported: the wrapper's kind/label are a choice.
        # "UNCERTAIN" / marker "?" mirrors the 'unknown' leaf
        # (kind UNKNOWN, label "?"). The rejected alternative was
        # unwrapping the child and marking only the child UNCERTAIN —
        # that would delete the wrapper from the record.
        child = ast.get("child")
        if child is None:
            raise FrontendError("uncertain node without child")
        self._child_nodes([child])
        node = Node(
            ident=Ident(sid=self._next_sid()),
            kind="UNCERTAIN",
            label=ast.get("marker", "?"),
            epi=self._epi(ast),
        )
        self.nodes.append(node)
        return node

    def _lower_sequence(self, ast: Dict[str, Any]) -> Node:
        # OD-04 experimental lowering rule for 'sequence' (multiple
        # top-level items). Direct reading: a sequence is a container,
        # not a semantic composition claim (C10: adjacency alone
        # composes nothing). Children are lowered for their side
        # effects and recorded as independent nodes/statements; the
        # container node records the grouping only, with the epi of
        # its own span (UNCERTAIN when a "?" appears among its items).
        # Ambiguity, reported: kind "SEQUENCE" (parallel to LADDER /
        # DEFINITION having their own kinds) rather than "STRUCTURAL".
        # A sequence with no children is malformed input: fail closed.
        children = list(ast.get("children", []))
        if not children:
            raise FrontendError("sequence node without children")
        self._child_nodes(children)
        node = Node(
            ident=Ident(sid=self._next_sid()),
            kind="SEQUENCE",
            label="sequence",
            epi=self._epi(ast),
        )
        self.nodes.append(node)
        return node

    # -- relations -----------------------------------------------------
    def _operand_handle(self, operand: Dict[str, Any]) -> Handle:
        lowered = self.lower_node(operand)
        sid = lowered.ident.sid
        if sid is None:
            raise FrontendError("relation operand lowered without a SID")
        return sid

    def _make_statement(
        self,
        predicate: str,
        subject: Handle,
        object_: Handle,
        provenance: ProvenanceManifest,
        epi: EpiState | None = None,
    ) -> Statement:
        if predicate not in RELATIONS:
            raise FrontendError(
                f"relation operator {predicate!r} is not in the governed "
                "RELATIONS vocabulary"
            )
        # OD-05 EXPERIMENTAL (Option B): a CAUSES statement's basis comes
        # only from the caller-supplied mapping; the surface syntax
        # carries none. promotes_to is never populated by lowering — no
        # surface syntax claims promotion.
        basis = self._causal_bases.get(
            _causal_key(subject, predicate, object_))
        self._counter += 1
        stmt = Statement(
            ident=Ident(
                sid=_identity.assign_handle("SID", f"stmt-{self._counter:04d}")
            ),
            subject=subject,
            predicate=predicate,
            object=object_,
            causal_basis=basis,
            promotes_to=None,
            negated=False,
            epi=epi if epi is not None else EpiState("UNKNOWN_ORIGIN", "UNKNOWN"),
            provenance=provenance,
        )
        self.statements.append(stmt)
        return stmt

    def lower_relation(self, ast: Dict[str, Any]) -> Statement:
        op = ast.get("op")
        if not isinstance(op, str) or not op:
            raise FrontendError("relation node without an operator")
        subject = self._operand_handle(ast["left"])
        object_ = self._operand_handle(ast["right"])
        return self._make_statement(op, subject, object_, ProvenanceManifest(),
                                    epi=self._epi(ast))

    def lower_typed_relation(self, ast: Dict[str, Any]) -> Statement:
        alternatives = list(ast.get("alternatives", []))
        if not alternatives:
            raise FrontendError("typed_relation with no alternatives")
        selected = alternatives[0]
        if not isinstance(selected, str) or not selected:
            raise FrontendError("typed_relation with non-string first alternative")
        # Non-selected alternatives are recorded as UNRESOLVED knowledge with
        # the DP-01 reason; the selection itself is the parser's, not ours.
        manifest = build_manifest(
            [
                (f"alternative:{alt}", "UNRESOLVED", "ALTERNATIVE_NOT_SELECTED")
                for alt in alternatives[1:]
                if isinstance(alt, str) and alt
            ]
        )
        subject = self._operand_handle(ast["left"])
        object_ = self._operand_handle(ast["right"])
        return self._make_statement(selected, subject, object_, manifest,
                                    epi=self._epi(ast))
def validate_causal(record: Record) -> None:
    """OD-05 EXPERIMENTAL guard (Option B), mirroring the held
    check_no_causal_promotion (gtml_validator.py:68-80) with the hole
    fixed: promotion to CAUSES is rejected from ANY source predicate,
    not just ASSOCIATED_WITH/BEFORE.

    Rule 1: a CAUSES statement without a (non-empty) causal_basis
      raises FrontendError. Presence is judged by truthiness, exactly
      like the held validator's `not basis` — an empty string counts
      as no basis.
    Rule 2 (widened): a statement whose promotes_to is CAUSES raises
      REGARDLESS of basis. A basis legitimizes a direct CAUSES claim,
      never a promotion — this is the held Rule 2's semantics (its
      case 6: ASSOCIATED_WITH promoted WITH basis still raises); only
      the predicate set is widened, from (ASSOCIATED_WITH, BEFORE) to
      any predicate other than CAUSES itself (promoting CAUSES to
      CAUSES is vacuous and already governed by Rule 1).

    First failure aborts (held validator shape). This is a frontend
    gate only: transform.serialize/reconstruct round-trip the fields
    without validating them, so a Record built by hand (or restored
    from bytes) is unchecked until it passes through this function.
    """
    for stmt in record.statements:
        sid = str(stmt.ident.sid or "?")
        if stmt.predicate == "CAUSES" and not stmt.causal_basis:
            raise FrontendError(
                "[association_promoted_to_causation] "
                f"CAUSES without causal_basis in {sid}")
        if stmt.promotes_to == "CAUSES" and stmt.predicate != "CAUSES":
            raise FrontendError(
                "[association_promoted_to_causation] "
                f"{stmt.predicate} promoted to CAUSES in {sid}")


def lower(ast: Dict[str, Any], causal_bases: Optional[Dict[str, str]] = None) -> Record:
    """Lower a held-parser AST dict to a typed IR Record.

    Only builds Statements where the parser emitted a relation or
    typed_relation node; never invents relations from bare word sequences.

    OD-05 EXPERIMENTAL: after lowering, validate_causal() runs over
    the finished Record — any CAUSES statement without a basis, and
    any promotion-to-CAUSES claim, fails closed (FrontendError).

    causal_bases: optional mapping from "SUBJECT|PREDICATE|OBJECT"
    triple keys (handles rendered "KIND:name", e.g.
    "SID:A|CAUSES|SID:B") to basis strings. The surface syntax has no
    basis annotation, so this mapping is the only way a lowered CAUSES
    statement can carry a basis. Keys and values must be str.
    """
    if not isinstance(ast, dict):
        raise FrontendError("lower requires the parser's AST dict")
    if causal_bases is not None:
        if not isinstance(causal_bases, dict):
            raise FrontendError("causal_bases must be a dict or None")
        for key, value in causal_bases.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise FrontendError("causal_bases keys and values must be str")
    lowerer = _Lowerer(causal_bases=causal_bases)
    lowerer.lower_node(ast)  # nodes/statements accumulate in lowering order
    record = Record(
        version=VERSION,
        nodes=tuple(lowerer.nodes),
        statements=tuple(lowerer.statements),
        literals=tuple(lowerer.literals),
        authority="ZERO",
    )

    validate_causal(record)
    return record
