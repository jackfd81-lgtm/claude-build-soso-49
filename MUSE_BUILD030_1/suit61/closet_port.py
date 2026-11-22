"""Suit 6.1 -> PH6 Closet router port (reference router v0.1, vendored unmodified).

What flows: Suit evidence gaps become IOU envelopes (PENDING, never accepted as evidence); Suit decisions
become AUDIT envelopes (receipt only). Nothing flows back into Suit state: a Closet response cannot qualify,
authorize or change a procedure.

Identity: the envelope's origin_identity is the AUTHENTICATED principal that Build 025 reported, never a
caller-supplied field (the reference router keys its ledger on origin_identity, a known drift from the corrected
spec; this port closes that for its own envelopes only).

Known limits of the vendored reference (not hidden): 22-field envelope (spec says 14-field Bridge1Envelope +
adapter), in-memory ledger/IOU registry/transactions (lost on restart), no SO_PEERCRED/session binding, the
dispatch callable is a stub supplied here. Closet unavailable or refusing = explicit status, never silent.
"""
import hashlib
import json

from suit61 import disposition as D
from suit61.vendor import ph6_closet_router as R

SOURCE, DEST = "MASTER_JEDI_IN", "CLOSET_FRONT_IN"
VENDORED_SHA256 = "efa216da5c72f5ba258ecebf189ba8a7c831b5b0e2246f117f0c071166f025fe"


class ClosetPort:
    def __init__(self, principal, router=None, scope_id="suit61"):
        if not isinstance(principal, str) or not principal:
            raise ValueError("authenticated principal required")
        self.principal, self.scope = principal, scope_id
        self.dispatched = []
        self.router = router or R.ClosetRouter(dispatch=self._dispatch)
        self.log = []

    def _dispatch(self, env):
        self.dispatched.append(env.payload.get("receipt_seq"))
        return {"result": "RECEIPT_RECORDED", "receipt_seq": env.payload.get("receipt_seq")}

    def _send(self, kind, payload, key):
        mid = "suit61-" + hashlib.sha256(json.dumps([self.principal, kind, key], sort_keys=True).encode()).hexdigest()[:20]
        env = R.make_envelope(kind, SOURCE, DEST, R.INBOUND_DIRECTION, payload, origin_identity=self.principal,
                              scope_id=self.scope, message_id=mid, transaction_id="tx-" + mid[7:],
                              authority_class=R.AUTHORITY_ADVISORY)
        try:
            resp = self.router.submit(env)
        except Exception as exc:                       # an unavailable/broken Closet is reported, never swallowed
            resp = {"error": "CLOSET_EXCEPTION", "detail": type(exc).__name__}
        ok = ("error" not in resp and resp.get("transaction_id") == env.transaction_id
              and resp.get("kind") == kind + "_ACK")
        out = {"status": "ACKED" if ok else "FAILED", "kind": kind, "transaction_id": env.transaction_id,
               "response_kind": resp.get("kind"), "error": resp.get("error") or (None if ok else "UNEXPECTED_RECEIPT"), "replay": bool(resp.get("idempotent_replay")),
               "payload_digest": env.payload_digest}
        self.log.append(out)
        return out

    def evidence_gap(self, receipt, context_digest, count, mode):
        """An unresolved element is an IOU: PENDING until independent evidence arrives. Not evidence."""
        key = [context_digest, receipt["seq"], receipt.get("trace_digest")]
        iou = "iou-" + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]
        return self._send("IOU", {"obligation_id": iou, "origin_ref": "suit61:trace:%d" % receipt["seq"],
                                  "reason": "%d element(s) need evidence (%s)" % (count, mode), "priority": 1,
                                  "revisit_condition": "independent measurement or attempt evidence for context " + context_digest},
                          key) | {"obligation_id": iou}

    def decision(self, receipt, context_digest, mode):
        """Receipt-only audit of a Suit decision; carries no verdict and grants nothing."""
        key = [context_digest, receipt["seq"], mode, receipt.get("trace_digest")]
        rec = D.record_for(mode, receipt, context_digest)          # closed v3.2.3 codes; raises on an unmappable mode
        return self._send("AUDIT", {"receipt_seq": receipt["seq"], "context_digest": context_digest, "mode": mode,
                                    "disposition_record": rec.as_dict(), "task_id": receipt.get("task_id"), "execution_authorization": "NOT_GRANTED"}, key) | {
            "record_digest": rec.canonical_digest()}

    def pending_ious(self):
        return {k: v.integrity_state for k, v in self.router.ious._ious.items()}
