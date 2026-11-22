"""Pure fold of IOU events under an explicit policy (TERMINAL|REPLACE). No default: owner decision. Lane 2 / AUTH:0."""
PENDING, VERIFIED, REJECTED, INDETERMINATE = "PENDING", "VERIFIED", "REJECTED", "INDETERMINATE"
POLICIES = ("TERMINAL", "REPLACE")
def fold(events, policy):
    if policy not in POLICIES: raise ValueError("POLICY_REQUIRED: choose one of %s (owner decision)" % (POLICIES,))
    ious, malformed, hist = {}, [], {}
    for i, c in enumerate(events):
        try:
            op, rid, st = c["operation"], c["record_id"], c["status"]
            if op not in ("IOU", "IOU_INTEGRITY") or not isinstance(rid, str) or not rid: raise ValueError("FIELDS")
            if op == "IOU" and st != PENDING: raise ValueError("BAD_CREATE_STATUS")
            if op == "IOU_INTEGRITY":
                if st not in (VERIFIED, REJECTED): raise ValueError("BAD_STATUS")
                if (st == VERIFIED) != (c["expected_digest"] == c["actual_digest"]): raise ValueError("STATUS_DIGEST_DISAGREE")
        except (ValueError, KeyError, TypeError) as exc:
            malformed.append((i, str(exc))); continue
        hist.setdefault(rid, []).append((op, st))
    for rid, ev in hist.items():
        cr = [e for e in ev if e[0] == "IOU"]; ck = [e for e in ev if e[0] == "IOU_INTEGRITY"]
        if len(cr) != 1 or ev[0][0] != "IOU": ious[rid] = (INDETERMINATE, "NO_OR_AMBIGUOUS_CREATION")
        elif not ck: ious[rid] = (PENDING, None)
        elif policy == "TERMINAL" and len(ck) > 1: ious[rid] = (INDETERMINATE, "REPEATED_CHECK_UNDER_TERMINAL_POLICY")
        else: ious[rid] = (ck[-1][1], None)
    return {"ious": ious, "malformed": malformed, "complete": not malformed, "policy": policy}
def may_use_as_accepted(rec, iou_id):
    return rec["complete"] and rec["ious"].get(iou_id, (None,))[0] == VERIFIED
