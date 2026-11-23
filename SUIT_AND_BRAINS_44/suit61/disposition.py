"""Suit 6.1 decision -> SoSo Suit v3.2.3 DispositionRecord (closed vocabularies, vendored frozen monitor).

Every Suit decision is expressed with the monitor's own closed codes; nothing is invented, and an unmappable
mode is an error, not a guess. The record carries state_change=False always: the Suit never changes evidence.
A handshake failure is advisory evidence of an unavailable or invalid link.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import ph6_soso_suit_monitor_v3_2_3 as M  # noqa: E402  (imports the frozen v3.2.2 beside it)

VENDORED = {
    "ph6_soso_suit_monitor_v3_2_2.py": "150f1d12c999d9425c6855fd0b5033e5e92f29504e542ac6b2bf66d35c81b775",
    "ph6_soso_suit_monitor_v3_2_3.py": "2a5d95eeed8e4a5baeeb575d9c21c2068dd625e8829fcdc14f13a98ccad7ee83",
}
_O, _V, _D, _R, _C = M.ObservationCode, M.VerificationCode, M.DispositionCode, M.ReasonCode, M.ConfidenceEnum

# mode -> (observation, verification, disposition, reason, confidence)
MAP = {
    "REUSE_QUALIFIED_SKILL": (_O.OBS_HASH_MATCH, _V.VER_HASH_RECOMPUTED, _D.DISP_ACCEPTED, _R.REASON_VERIFICATION_SATISFIED, _C.MEDIUM),
    "REUSE_QUALIFIED_TRANSFER": (_O.OBS_HASH_MATCH, _V.VER_HASH_RECOMPUTED, _D.DISP_ACCEPTED, _R.REASON_VERIFICATION_SATISFIED, _C.MEDIUM),
    "PROPOSE_TRANSFER": (_O.OBS_UNCERTAIN, _V.VER_EVIDENCE_RESOLVED, _D.DISP_IOU, _R.REASON_INSUFFICIENT_EVIDENCE, _C.LOW),
    "UNFAMILIAR_REDISCOVERY_REQUIRED": (_O.OBS_UNCERTAIN, _V.VER_EVIDENCE_RESOLVED, _D.DISP_IOU, _R.REASON_INSUFFICIENT_EVIDENCE, _C.LOW),
    "NEEDS_INVESTIGATION": (_O.OBS_UNCERTAIN, _V.VER_EVIDENCE_RESOLVED, _D.DISP_IOU, _R.REASON_INSUFFICIENT_EVIDENCE, _C.LOW),
    "DO_NOT_TRANSFER": (_O.OBS_UNCERTAIN, _V.VER_EVIDENCE_RESOLVED, _D.DISP_REFUSED, _R.REASON_EVIDENCE_CONTRADICTS_REQUEST, _C.HIGH),
    "HANDSHAKE_FAILED": (_O.OBS_UNCERTAIN, _V.VER_EVIDENCE_RESOLVED, _D.DISP_REFUSED, _R.REASON_REQUIRED_CONTROL, _C.UNQUANTIFIED),
    "PREREQUISITE_HOLD": (_O.OBS_UNCERTAIN, _V.VER_EVIDENCE_RESOLVED, _D.DISP_IOU, _R.REASON_INSUFFICIENT_EVIDENCE, _C.LOW),
    "GOVERNANCE_HOLD": (_O.OBS_UNCERTAIN, _V.VER_AUTHORITY_CHECKED, _D.DISP_REFUSED, _R.REASON_REQUIRED_CONTROL, _C.UNQUANTIFIED),
    "READY": (_O.OBS_UNCERTAIN, _V.VER_AUTHORITY_CHECKED, _D.DISP_ACCEPTED, _R.REASON_VERIFICATION_SATISFIED, _C.UNQUANTIFIED),
    "VALIDATED_STEP": (_O.OBS_UNCERTAIN, _V.VER_EVIDENCE_RESOLVED, _D.DISP_ACCEPTED, _R.REASON_VERIFICATION_SATISFIED, _C.UNQUANTIFIED),
    "EVIDENCE_HOLD": (_O.OBS_UNCERTAIN, _V.VER_EVIDENCE_RESOLVED, _D.DISP_IOU, _R.REASON_INSUFFICIENT_EVIDENCE, _C.LOW),
    "SUIT_DISABLED": (_O.OBS_UNCERTAIN, _V.VER_AUTHORITY_CHECKED, _D.DISP_NO_ACTION, _R.REASON_REQUIRED_CONTROL, _C.UNQUANTIFIED),
}


def record_for(mode, receipt, context_digest):
    if mode not in MAP:
        raise ValueError("no closed-vocabulary mapping for mode " + str(mode))
    obs, ver, disp, reason, conf = MAP[mode]
    ev = receipt.get("trace_digest") or hashlib.sha256(json.dumps({"seq": receipt["seq"], "context": context_digest, "mode": mode}, sort_keys=True).encode()).hexdigest()
    return M.DispositionRecord(observation=obs, verification=ver, disposition=disp, reason=reason, confidence=conf,
                               evidence_source="suit61:trace:%d" % receipt["seq"], evidence_sha256=ev,
                               state_change=False, note=mode)


def verify_vendored(directory=None):
    d = directory or os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    return {n: hashlib.sha256(open(os.path.join(d, n), "rb").read()).hexdigest() == h for n, h in VENDORED.items()}
