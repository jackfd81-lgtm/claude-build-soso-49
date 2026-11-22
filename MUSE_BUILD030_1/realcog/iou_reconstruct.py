"""iou-reconstruct-0.1: implements the PROPOSED CR-01 read-only reconstruction (handoff section 4) over the REAL Small49.
Compatible replacement for the missing router rebuild; not the original router. Lane 2 / AUTH:0 / non-canon.
Owner decision NOT made here: are VERIFIED/REJECTED terminal? `policy` is REQUIRED (no default)."""
import json
from ph6_brains_2208 import Small49, _digest

PENDING, VERIFIED, REJECTED, INDETERMINATE = "PENDING", "VERIFIED", "REJECTED", "INDETERMINATE"
POLICIES = ("TERMINAL", "REPLACE")

def reconstruct(small: Small49, policy: str):
    if policy not in POLICIES: raise ValueError("POLICY_REQUIRED: choose one of %s (owner decision)" % (POLICIES,))
    small.verify()                                                   # Block digests + event chain, as the store does at open
    ious, malformed, hist = {}, [], {}
    q = ("SELECT b.record_id,b.payload,b.digest,e.digest FROM events e JOIN blocks b "
         "ON b.pool=e.pool AND b.record_id=e.record_id WHERE e.pool='small' AND e.operation='PUT' "
         "AND b.bucket='active' AND b.record_id LIKE 'closet-iou-%' ORDER BY e.seq")
    for block_id, raw, bdig, edig in small.db.execute(q):             # durable order = events.seq, never wall-clock
        try:
            blk = json.loads(raw); c = json.loads(blk["content"])
            if bdig != edig: raise ValueError("DIGEST_DISAGREES_WITH_EVENT")
            op, rid, st = c["operation"], c["record_id"], c["status"]
            if op not in ("IOU", "IOU_INTEGRITY") or not isinstance(rid, str) or not rid: raise ValueError("FIELDS")
            if op == "IOU" and st != PENDING: raise ValueError("BAD_CREATE_STATUS")
            if op == "IOU_INTEGRITY":
                if st not in (VERIFIED, REJECTED): raise ValueError("BAD_STATUS")
                if (st == VERIFIED) != (c["expected_digest"] == c["actual_digest"]): raise ValueError("STATUS_DIGEST_DISAGREE")
        except (ValueError, KeyError, TypeError) as exc:              # incl. truncated task-state ({"truncated":true,...})
            malformed.append((block_id, str(exc))); continue          # never infer the IOU id from the random block id
        hist.setdefault(rid, []).append((op, st))
    for rid, events in hist.items():
        creates = [e for e in events if e[0] == "IOU"]; checks = [e for e in events if e[0] == "IOU_INTEGRITY"]
        if len(creates) != 1 or events[0][0] != "IOU": ious[rid] = (INDETERMINATE, "NO_OR_AMBIGUOUS_CREATION"); continue
        if not checks: ious[rid] = (PENDING, None); continue
        if policy == "TERMINAL" and len(checks) > 1: ious[rid] = (INDETERMINATE, "REPEATED_CHECK_UNDER_TERMINAL_POLICY"); continue
        ious[rid] = (checks[-1][1], None)                             # REPLACE: last durable check wins
    return {"ious": ious, "malformed": malformed, "complete": not malformed, "policy": policy}

def may_use_as_accepted(rec, iou_id):
    """Fail closed: only an exact VERIFIED from a COMPLETE reconstruction is usable."""
    return rec["complete"] and rec["ious"].get(iou_id, (None,))[0] == VERIFIED
