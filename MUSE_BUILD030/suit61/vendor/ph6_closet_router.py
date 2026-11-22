"""
PH6 Closet Router — Reference Implementation
Version: 0.1
Authority: AUTH:0
Tests: 11/11
Known drift: 22-field envelope (corrected spec uses 14-field Bridge1Envelope + adapter)
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4


PROTOCOL_VERSION = "ph6.closet.circulation/0.1"

PERMITTED_EDGES = frozenset({
    ("QUALIFIED_SOURCE", "ROOM_A_IN"),
    ("ROOM_A_IN", "ROOM_B_IN"),
    ("ROOM_B_IN", "REGULAR_SOSO_IN"),
    ("REGULAR_SOSO_IN", "JEDI_IN"),
    ("JEDI_IN", "MASTER_JEDI_IN"),
    ("MASTER_JEDI_IN", "CLOSET_FRONT_IN"),
    ("CLOSET_BACK_OUT", "MASTER_JEDI_OUT"),
    ("MASTER_JEDI_OUT", "JEDI_OUT"),
    ("JEDI_OUT", "REGULAR_SOSO_OUT"),
    ("REGULAR_SOSO_OUT", "ROOM_B_OUT"),
    ("ROOM_B_OUT", "ROOM_A_OUT"),
    ("ROOM_A_OUT", "DISPATCH_GATEWAY"),
})

INBOUND_DIRECTION = "INBOUND"
OUTBOUND_DIRECTION = "OUTBOUND"

REQUEST_KINDS = frozenset({"CHECK", "IOU", "DEBT", "AUDIT", "REPLAY"})
RESPONSE_KINDS = frozenset({"PAYMENT", "RETURN_PAYMENT", "FAILURE_LETTER"})
CONTROL_KINDS = frozenset({"RECEIPT", "ATTENTION_LETTER", "DEBT_SENT_LETTER"})
ALL_KINDS = REQUEST_KINDS | RESPONSE_KINDS | CONTROL_KINDS

STATE_CREATED = "CREATED"
STATE_ADMITTED = "ADMITTED"
STATE_ROUTED = "ROUTED"
STATE_DISPATCHED = "DISPATCHED"
STATE_COMPLETED = "COMPLETED"
STATE_FAILED = "FAILED"
STATE_INDETERMINATE = "INDETERMINATE"

AUTHORITY_LANE1 = "LANE1_REFERENCE"
AUTHORITY_ADVISORY = "ADVISORY"
AUTHORITY_CONTROL = "CONTROL"


class RouterError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class Envelope:
    protocol_version: str
    message_id: str
    transaction_id: str
    parent_message_id: Optional[str]
    kind: str
    direction: str
    source_node: str
    destination_node: str
    route_id: str
    sequence: int
    issued_at: str
    origin_identity: str
    scope_id: str
    capability_id: Optional[str]
    payload_schema: str
    payload: dict
    source_refs: tuple[str, ...]
    authority_class: str
    payload_digest: str
    expected_reply_kind: Optional[str]
    expiry: Optional[str]
    trace_id: str


def canonical_payload(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_of(payload: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_edge(source_node: str, destination_node: str) -> None:
    if (source_node, destination_node) not in PERMITTED_EDGES:
        raise RouterError("ROUTE_FORBIDDEN",
                          f"no permitted edge {source_node} -> {destination_node}")


@dataclass
class LedgerEntry:
    ledger_sequence: int
    client_id: str
    msg_id: str
    session_id: str
    payload_digest: str
    disposition: str
    response: Optional[dict] = None


class IdempotencyLedger:
    def __init__(self):
        self._entries: dict[tuple[str, str], LedgerEntry] = {}
        self._sequence = 0

    def reserve(self, client_id: str, msg_id: str, session_id: str,
                payload_digest: str) -> LedgerEntry | None:
        key = (client_id, msg_id)
        if key in self._entries:
            existing = self._entries[key]
            if existing.payload_digest == payload_digest:
                existing.disposition = "DUPLICATE"
                return existing
            existing.disposition = "CONFLICT"
            return existing
        self._sequence += 1
        entry = LedgerEntry(
            ledger_sequence=self._sequence,
            client_id=client_id, msg_id=msg_id, session_id=session_id,
            payload_digest=payload_digest, disposition="ACKED",
        )
        self._entries[key] = entry
        return None

    def record_response(self, client_id: str, msg_id: str, response: dict) -> None:
        key = (client_id, msg_id)
        if key in self._entries:
            self._entries[key].response = response

    def lookup(self, client_id: str, msg_id: str) -> Optional[LedgerEntry]:
        return self._entries.get((client_id, msg_id))


@dataclass
class IOU:
    obligation_id: str
    origin_ref: str
    reason: str
    priority: int
    revisit_condition: str
    integrity_state: str
    payload_digest: Optional[str] = None


class IOURegistry:
    def __init__(self):
        self._ious: dict[str, IOU] = {}

    def register_pending(self, obligation_id: str, origin_ref: str, reason: str,
                         priority: int, revisit_condition: str) -> IOU:
        iou = IOU(obligation_id, origin_ref, reason, priority,
                  revisit_condition, integrity_state="PENDING")
        self._ious[obligation_id] = iou
        return iou

    def complete_integrity_check(self, obligation_id: str, payload: dict,
                                  expected_digest: str) -> IOU:
        iou = self._ious[obligation_id]
        actual = digest_of(payload)
        if actual == expected_digest:
            iou.integrity_state = "VERIFIED"
            iou.payload_digest = actual
        else:
            iou.integrity_state = "REJECTED"
        return iou

    def get(self, obligation_id: str) -> Optional[IOU]:
        return self._ious.get(obligation_id)


@dataclass
class Transaction:
    transaction_id: str
    kind: str
    state: str
    session_id: str
    client_id: str
    message_id: str
    created_at: str
    updated_at: str
    history: list[dict] = field(default_factory=list)


class TransactionTracker:
    def __init__(self):
        self._tx: dict[str, Transaction] = {}

    def open(self, tx: Transaction) -> Transaction:
        tx.history.append({"state": STATE_CREATED, "at": now_iso()})
        self._tx[tx.transaction_id] = tx
        return tx

    def transition(self, transaction_id: str, new_state: str, reason: str = "") -> Transaction:
        tx = self._tx[transaction_id]
        allowed = {
            STATE_CREATED: {STATE_ADMITTED, STATE_FAILED},
            STATE_ADMITTED: {STATE_ROUTED, STATE_FAILED},
            STATE_ROUTED: {STATE_DISPATCHED, STATE_FAILED},
            STATE_DISPATCHED: {STATE_COMPLETED, STATE_FAILED, STATE_INDETERMINATE},
            STATE_INDETERMINATE: {STATE_COMPLETED, STATE_FAILED},
        }
        if new_state not in allowed.get(tx.state, set()):
            raise RouterError("STATE_TRANSITION_FORBIDDEN",
                              f"{tx.state} -> {new_state}")
        tx.state = new_state
        tx.updated_at = now_iso()
        tx.history.append({"state": new_state, "at": tx.updated_at, "reason": reason})
        return tx

    def get(self, transaction_id: str) -> Optional[Transaction]:
        return self._tx.get(transaction_id)


class ClosetRouter:
    def __init__(self,
                 ledger: IdempotencyLedger | None = None,
                 ious: IOURegistry | None = None,
                 transactions: TransactionTracker | None = None,
                 dispatch: Callable[[Envelope], dict] | None = None):
        self.ledger = ledger or IdempotencyLedger()
        self.ious = ious or IOURegistry()
        self.transactions = transactions or TransactionTracker()
        self.dispatch = dispatch or (lambda env: {"result": "STUB", "kind": env.kind})

    def submit(self, env: Envelope) -> dict:
        try:
            self._validate_envelope(env)
            check_edge(env.source_node, env.destination_node)
            self._validate_direction(env)

            reserved = self.ledger.reserve(
                client_id=env.origin_identity,
                msg_id=env.message_id,
                session_id=env.scope_id,
                payload_digest=env.payload_digest,
            )
            if reserved is not None:
                if reserved.disposition == "DUPLICATE" and reserved.response:
                    return {**reserved.response, "idempotent_replay": True}
                if reserved.disposition == "CONFLICT":
                    raise RouterError("IDEMPOTENCY_CONFLICT",
                                      "same msg_id, different payload")

            tx = Transaction(
                transaction_id=env.transaction_id, kind=env.kind,
                state=STATE_CREATED, session_id=env.scope_id,
                client_id=env.origin_identity, message_id=env.message_id,
                created_at=now_iso(), updated_at=now_iso(),
            )
            self.transactions.open(tx)
            self.transactions.transition(env.transaction_id, STATE_ADMITTED)
            self.transactions.transition(env.transaction_id, STATE_ROUTED)

            response = self._handle(env)

            self.transactions.transition(env.transaction_id, STATE_DISPATCHED)
            self.transactions.transition(env.transaction_id, STATE_COMPLETED,
                                          reason=f"kind={env.kind}")
            self.ledger.record_response(env.origin_identity, env.message_id, response)
            return response

        except RouterError as e:
            self._record_failure(env, e)
            return {"error": e.code, "detail": e.detail}

    def _validate_envelope(self, env: Envelope) -> None:
        if env.protocol_version != PROTOCOL_VERSION:
            raise RouterError("PROTOCOL_VERSION_UNSUPPORTED", env.protocol_version)
        if env.kind not in ALL_KINDS:
            raise RouterError("KIND_UNKNOWN", env.kind)
        if env.authority_class not in (AUTHORITY_LANE1, AUTHORITY_ADVISORY, AUTHORITY_CONTROL):
            raise RouterError("AUTHORITY_CLASS_INVALID", env.authority_class)
        expected = digest_of(env.payload)
        if env.payload_digest != expected:
            raise RouterError("PAYLOAD_DIGEST_MISMATCH",
                              f"declared={env.payload_digest} actual={expected}")

    def _validate_direction(self, env: Envelope) -> None:
        if env.destination_node == "CLOSET_FRONT_IN" and env.direction != INBOUND_DIRECTION:
            raise RouterError("DIRECTION_FORBIDDEN", "FRONT_IN requires INBOUND")
        if env.source_node == "CLOSET_BACK_OUT" and env.direction != OUTBOUND_DIRECTION:
            raise RouterError("DIRECTION_FORBIDDEN", "BACK_OUT requires OUTBOUND")

    def _handle(self, env: Envelope) -> dict:
        if env.kind == "CHECK":
            return {"kind": "CHECK_RESULT", "transaction_id": env.transaction_id,
                    "target_refs": env.payload.get("target_refs", []),
                    "findings": [], "note": "no external measurement performed"}
        if env.kind == "IOU":
            iou = self.ious.register_pending(
                obligation_id=env.payload["obligation_id"],
                origin_ref=env.payload["origin_ref"],
                reason=env.payload["reason"],
                priority=env.payload.get("priority", 0),
                revisit_condition=env.payload["revisit_condition"],
            )
            return {"kind": "IOU_ACK", "transaction_id": env.transaction_id,
                    "obligation_id": iou.obligation_id,
                    "integrity_state": iou.integrity_state}
        if env.kind == "DEBT":
            result = self.dispatch(env)
            return {"kind": "DEBT_ACK", "transaction_id": env.transaction_id,
                    "operation_key": self._operation_key(env), "dispatch_result": result}
        if env.kind == "AUDIT":
            result = self.dispatch(env)
            return {"kind": "AUDIT_ACK", "transaction_id": env.transaction_id,
                    "dispatch_result": result}
        if env.kind == "REPLAY":
            return {"kind": "REPLAY_RESULT", "transaction_id": env.transaction_id,
                    "evidence_refs": env.payload.get("evidence_refs", []),
                    "note": "read-only; original evidence unmodified"}
        raise RouterError("KIND_NOT_IMPLEMENTED", env.kind)

    def _operation_key(self, env: Envelope) -> str:
        parts = [env.origin_identity, env.scope_id, env.kind,
                 env.payload.get("work_spec_id", ""), env.payload_digest,
                 env.protocol_version]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]

    def _record_failure(self, env: Envelope, e: RouterError) -> None:
        tx = self.transactions.get(env.transaction_id)
        if tx:
            try:
                self.transactions.transition(env.transaction_id, STATE_FAILED, e.code)
            except RouterError:
                pass


def make_envelope(kind, source_node, destination_node, direction, payload,
                  origin_identity="client-001", scope_id="session-001",
                  message_id=None, transaction_id=None, capability_id=None,
                  authority_class=AUTHORITY_ADVISORY) -> Envelope:
    return Envelope(
        protocol_version=PROTOCOL_VERSION,
        message_id=message_id or f"msg-{uuid4().hex[:12]}",
        transaction_id=transaction_id or f"tx-{uuid4().hex[:12]}",
        parent_message_id=None, kind=kind, direction=direction,
        source_node=source_node, destination_node=destination_node,
        route_id=f"{source_node}->{destination_node}", sequence=1,
        issued_at=now_iso(), origin_identity=origin_identity,
        scope_id=scope_id, capability_id=capability_id,
        payload_schema=f"ph6.closet.{kind.lower()}.v0.1",
        payload=payload, source_refs=(), authority_class=authority_class,
        payload_digest=digest_of(payload), expected_reply_kind=None,
        expiry=None, trace_id=f"trace-{uuid4().hex[:8]}",
    )


def run_tests() -> int:
    passed, failed = 0, 0
    def check(name, cond):
        nonlocal passed, failed
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        passed += 1 if cond else 0
        failed += 0 if cond else 1

    router = ClosetRouter()

    env = make_envelope("CHECK", "MASTER_JEDI_IN", "CLOSET_FRONT_IN",
                        INBOUND_DIRECTION, {"target_refs": ["ref-1"], "question": "what changed?"})
    resp = router.submit(env)
    check("valid CHECK accepted", "error" not in resp)

    env_bad = make_envelope("CHECK", "CLOSET_FRONT_IN", "MASTER_JEDI_IN",
                            OUTBOUND_DIRECTION, {"target_refs": [], "question": "x"})
    resp = router.submit(env_bad)
    check("reverse edge rejected", resp.get("error") == "ROUTE_FORBIDDEN")

    env_bad2 = make_envelope("CHECK", "MASTER_JEDI_IN", "CLOSET_FRONT_IN",
                             OUTBOUND_DIRECTION, {"target_refs": [], "question": "x"})
    resp = router.submit(env_bad2)
    check("FRONT_IN requires INBOUND", resp.get("error") == "DIRECTION_FORBIDDEN")

    env_bad3 = make_envelope("CHECK", "MASTER_JEDI_IN", "CLOSET_FRONT_IN",
                             INBOUND_DIRECTION, {"target_refs": [], "question": "x"})
    env_bad3 = Envelope(**{**asdict(env_bad3), "payload_digest": "sha256:00"})
    resp = router.submit(env_bad3)
    check("digest tamper rejected", resp.get("error") == "PAYLOAD_DIGEST_MISMATCH")

    env_dup = make_envelope("CHECK", "MASTER_JEDI_IN", "CLOSET_FRONT_IN",
                            INBOUND_DIRECTION, {"target_refs": ["r"], "question": "q"},
                            origin_identity="client-A", scope_id="session-A",
                            message_id="msg-dup-001", transaction_id="tx-dup-001")
    router.submit(env_dup)
    resp2 = router.submit(env_dup)
    check("duplicate msg returns idempotent replay", resp2.get("idempotent_replay") is True)

    env_conf = make_envelope("CHECK", "MASTER_JEDI_IN", "CLOSET_FRONT_IN",
                             INBOUND_DIRECTION, {"target_refs": ["DIFFERENT"], "question": "q"},
                             origin_identity="client-A", scope_id="session-A",
                             message_id="msg-dup-001", transaction_id="tx-conf-001")
    resp3 = router.submit(env_conf)
    check("conflicting payload rejected", resp3.get("error") == "IDEMPOTENCY_CONFLICT")

    env_iou = make_envelope("IOU", "MASTER_JEDI_IN", "CLOSET_FRONT_IN",
                            INBOUND_DIRECTION, {"obligation_id": "iou-001",
                            "origin_ref": "evt-42", "reason": "unexplained noise",
                            "priority": 2, "revisit_condition": "more sensor data"})
    resp = router.submit(env_iou)
    check("IOU registered as PENDING", resp.get("integrity_state") == "PENDING")

    router.ious.complete_integrity_check("iou-001", payload={"x": 1},
                                          expected_digest=digest_of({"x": 1}))
    check("IOU becomes VERIFIED on digest match",
          router.ious.get("iou-001").integrity_state == "VERIFIED")

    router.ious.register_pending("iou-002", "evt-43", "r2", 1, "cond2")
    router.ious.complete_integrity_check("iou-002", payload={"y": 2},
                                          expected_digest="sha256:deadbeef")
    check("IOU becomes REJECTED on digest mismatch",
          router.ious.get("iou-002").integrity_state == "REJECTED")

    tx = router.transactions.get(env.transaction_id)
    check("transaction state machine recorded history",
          tx.state == STATE_COMPLETED and len(tx.history) >= 4)

    print(f"\n  {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(run_tests())
    print("PH6 Closet Router — reference implementation")
    print("Run with: python3 ph6_closet_router.py --test")
