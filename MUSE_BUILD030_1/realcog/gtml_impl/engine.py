"""Engine: IR -> TML-ML bytecode -> VM execution.

EXPERIMENTAL — NOT HISTORICALLY AUTHENTICATED. Authority ZERO.
Gate-1 HOLD. Canon UNCHANGED.

tml.py is imported READ-ONLY from the held v0.5.0 language-machine tree;
it is never copied, vendored, or modified. Authority stays ZERO
everywhere: the VM rejects any bytecode carrying authority above ZERO,
and this module never emits an authority field (the VM defaults NODE
authority to {'record': 'ZERO'}).

OP-0017 RESOLVE is NOT in the held tml.py instruction surface, so it is
implemented here as a bounded EXPERIMENTAL helper (resolve_op17), not as
a VM opcode. It performs no VM execution and claims no historical
authenticity.

Fail-closed: every emitted instruction is validated with the VM's own
_validate_instruction before the bytecode is returned; any validation
failure becomes EngineError. Deterministic: first-wins ID dedupe,
insertion-order member lists, no timestamps.

Focus areas served: deterministic replay (bytecode is a pure function of
the Record), semantic conservation (explicit negations preserved in
qualification), identity preservation (VM instruction IDs are the IR
handles, never digests).
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .ir import Node, Record, Statement

_TML_DIR = str(
    Path.home() / "workspace/language_machine_v060/inc/PH6_LANGUAGE_MACHINE_v0.5.0"
)

if _TML_DIR not in sys.path:
    sys.path.insert(0, _TML_DIR)

import tml as vm  # held tree; read-only import (never modified)

_BYTECODE_MAGIC = vm.MAGIC
_BYTECODE_VERSION = vm.VERSION

_GROUP_ID = "g:all"
_RESOLVE_STATEMENT_CAP = 10_000


class EngineError(Exception):
    """Fail-closed engine error (compile or VM execution failure)."""


class ResolveError(Exception):
    """Fail-closed OP-0017 experimental resolution error."""


def _validate(ins: Dict[str, Any], index: int) -> None:
    """Run the VM's own instruction validator; wrap failures as EngineError."""
    try:
        vm._validate_instruction(ins, index)
    except Exception as exc:
        raise EngineError(
            f"bytecode instruction {index} failed VM validation: {exc}"
        ) from exc


def _node_id(node: Node) -> str:
    if node.ident.sid is not None:
        return str(node.ident.sid)
    if node.label:
        return node.label
    raise EngineError("node has neither SID nor label; cannot emit NODE")


def _statement_id(stmt: Statement, fallback: str) -> str:
    if stmt.ident.sid is not None:
        return str(stmt.ident.sid)
    return fallback


def compile_record(record: Record) -> Dict[str, Any]:
    """Compile an IR Record to validated TML-ML bytecode.

    Mapping:
      Node      -> NODE  {id: str(sid), type: kind, content: {label},
                         states: {epistemic, availability}}
      Statement -> RELATE {id, relation: predicate, args: [subject, object],
                         qualification: {negated}}
                   (ASSERT instead of RELATE when the statement's epistemic
                   state is ASSERTED)
      then GROUP g:all over every emitted object id, EMIT g:all, HALT.

    Duplicate instruction IDs are deduped deterministically (first wins);
    every instruction is validated with vm._validate_instruction.
    """
    if not isinstance(record, Record):
        raise EngineError(f"compile_record requires a Record, got {type(record).__name__}")
    code: List[Dict[str, Any]] = []
    emitted: List[str] = []  # instruction IDs in emission order
    seen = set()
    # Ops that do NOT register an identity in the VM (mirrors tml.run):
    # EMIT and HALT only reference. Only identity-registering ops dedupe.
    _NO_IDENTITY = {"EMIT", "RECONCILE", "HALT"}

    def emit(ins: Dict[str, Any]) -> None:
        op = ins.get("op")
        iid = ins.get("id")
        if op not in _NO_IDENTITY and iid in seen:
            return  # deterministic first-wins dedupe on registered identities
        _validate(ins, len(code))
        code.append(ins)
        if op not in _NO_IDENTITY:
            seen.add(iid)
            emitted.append(iid)

    for node in record.nodes:
        if not isinstance(node, Node):
            raise EngineError("record.nodes must contain only Nodes")
        emit({
            "op": "NODE",
            "id": _node_id(node),
            "type": node.kind,
            "content": {"label": node.label},
            "states": {
                "epistemic": node.epi.epistemic,
                "availability": node.epi.availability,
            },
        })

    for i, stmt in enumerate(record.statements):
        if not isinstance(stmt, Statement):
            raise EngineError("record.statements must contain only Statements")
        asserted = stmt.epi.epistemic == "ASSERTED"
        emit({
            "op": "ASSERT" if asserted else "RELATE",
            "id": _statement_id(stmt, f"stmt:anon-{i:04d}"),
            ("predicate" if asserted else "relation"): stmt.predicate,
            "args": [str(stmt.subject), str(stmt.object)],
            "qualification": {"negated": stmt.negated},
        })

    if _GROUP_ID in seen:
        raise EngineError(f"reserved group id {_GROUP_ID!r} collides with a node id")
    emit({"op": "GROUP", "id": _GROUP_ID, "members": list(emitted)})
    emit({"op": "EMIT", "id": _GROUP_ID})
    emit({"op": "HALT"})

    return {"magic": _BYTECODE_MAGIC, "version": _BYTECODE_VERSION, "code": code}


def run_record(record: Record) -> List[Any]:
    """Compile a Record and execute it on the held VM.

    The bytecode is deep-copied before execution (pipeline practice).
    Returns the VM's output list. VM runtime failures become EngineError.
    """
    bytecode = compile_record(record)
    try:
        return vm.run(copy.deepcopy(bytecode))
    except Exception as exc:
        raise EngineError(f"VM execution failed: {exc}") from exc


def resolve_op17(records: Sequence[Record]) -> Dict[str, List[Statement]]:
    """OP-0017 RESOLVE — bounded EXPERIMENTAL helper (not a VM opcode).

    A Statement is resolved iff its subject and object handles both appear
    as Node SIDs or Statement SIDs in the record set. Statement SIDs count
    as endpoints because the frontend reifies statements by handle
    reference for chained relations (the second statement's subject IS a
    statement SID by design) — a reified statement is a first-class
    referent, not a dangling handle. (Rationale recorded 2026-11-10:
    the earlier Node-SIDs-only rule left every chained relation
    unresolved, contradicting the frontend's reification design.)

    Bounded: at most 10_000 statements are examined; beyond that raises
    ResolveError (fail-closed, never silent truncation). Returns
    {"resolved": [...], "unresolved": [...]}.
    """
    records = list(records)
    for record in records:
        if not isinstance(record, Record):
            raise ResolveError(
                f"resolve_op17 requires Records, got {type(record).__name__}"
            )
    statements: List[Statement] = [
        stmt for record in records for stmt in record.statements
    ]
    if len(statements) > _RESOLVE_STATEMENT_CAP:
        raise ResolveError(
            f"resolve_op17 input exceeds bounded cap of {_RESOLVE_STATEMENT_CAP} "
            f"statements ({len(statements)} supplied)"
        )
    node_sids = {
        str(node.ident.sid)
        for record in records
        for node in record.nodes
        if node.ident.sid is not None
    }
    statement_sids = {
        str(stmt.ident.sid)
        for record in records
        for stmt in record.statements
        if stmt.ident.sid is not None
    }
    endpoints = node_sids | statement_sids
    resolved: List[Statement] = []
    unresolved: List[Statement] = []
    for stmt in statements:
        if str(stmt.subject) in endpoints and str(stmt.object) in endpoints:
            resolved.append(stmt)
        else:
            unresolved.append(stmt)
    return {"resolved": resolved, "unresolved": unresolved}
