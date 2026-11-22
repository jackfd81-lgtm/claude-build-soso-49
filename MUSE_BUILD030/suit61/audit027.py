"""Persistent Suit-side advisory audit receipts for the local reference router."""
import json

from realcog.procedural_memory import canonical, identity
from suit61 import disposition
from suit61.closet_port import ClosetPort

M22 = disposition.M.M22


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS monitor_audit_027 (
        seq INTEGER PRIMARY KEY, principal TEXT NOT NULL, source TEXT NOT NULL,
        result TEXT NOT NULL)""")


def audit_control(source, candidate=None):
    """Evaluate the actual v3.2.2 protocol for a separate LOG_ADVISORY request."""
    ctx = M22.SuitContextPacket(
        task_id="audit-" + str(source["seq"]), run_nonce=source["trace_digest"][:24],
        substrate=M22.SubstrateProfile("muse", "027", 0, "cpu"),
        context_tokens=[], allowed_actions=["LOG_ADVISORY"], source_hashes=["sha256:" + source["trace_digest"]],
        policy_hash=M22.canonical_hash(["LOG_ADVISORY"]), ingress_adapter_hash=M22.ingress_adapter_hash(),
    )
    if candidate is None:
        candidate = canonical({
            "protocol_version": M22.PROTOCOL_VERSION, "task_id": ctx.task_id,
            "context_digest": ctx.context_digest, "action_requested": "LOG_ADVISORY",
            "observations": [], "interpretations": [], "unresolved_ious": [],
        })
    return M22.SoSoSuitReferenceMonitor(ctx, M22.EvidenceStore()).evaluate_candidate(candidate)


def deliver(principal, source):
    result = {"status": "PENDING", "authority": "AUTH:0",
              "execution_authorization": "NOT_GRANTED", "source": source}
    try:
        record = disposition.record_for(source["mode"], source, source["context"])
        result["disposition"] = record.as_dict()
        result["advisory_candidate"] = audit_control(source)
        if result["advisory_candidate"]["validation_status"] != "VALID":
            return result | {"error": "AUDIT_CONTROL_REFUSED"}
        port = ClosetPort(principal)
        messages = [port.decision(source, source["context"], source["mode"])]
        if record.disposition == disposition.M.DispositionCode.DISP_IOU:
            messages.append(port.evidence_gap(source, source["context"], 1, source["mode"]))
        result["messages"] = messages
        if all(message["status"] == "ACKED" for message in messages):
            result["status"] = "ACKED"
        else:
            result["error"] = "CLOSET_DELIVERY_FAILED"
    except Exception as exc:
        result["error"] = type(exc).__name__
    return result


def record(db, principal, seq, kind, payload, digest):
    if kind == "RETRIEVE":
        mode = payload["mode"]
    elif kind == "TASK_BEGIN":
        mode = payload["status"]
    elif kind == "TASK_GOVERNANCE_HOLD":
        mode = "GOVERNANCE_HOLD"
    elif kind == "TASK_STEP_ACK":
        mode = "VALIDATED_STEP"
    elif kind == "INVESTIGATION_HOLD":
        mode = "EVIDENCE_HOLD"
    else:
        return
    source = {"seq": seq, "kind": kind, "mode": mode, "trace_digest": digest,
              "task_id": payload.get("task_id", payload.get("task")),
              "context": payload.get("context", identity(payload))}
    result = deliver(principal, source)
    db.execute("INSERT INTO monitor_audit_027 VALUES (?,?,?,?)",
               (seq, principal, canonical(source), canonical(result)))


def receipts(db, principal, task):
    ids = task.get("after", 0)
    if type(ids) is not int or ids < 0 or type(task.get("retry", False)) is not bool:
        raise ValueError("invalid audit page or retry flag")
    rows = db.execute("""SELECT seq,source,result FROM monitor_audit_027
                         WHERE principal=? AND seq>? ORDER BY seq LIMIT 100""", (principal, ids)).fetchall()
    results = []
    for seq, raw_source, raw_result in rows:
        result = json.loads(raw_result)
        if task.get("retry") and result["status"] == "PENDING":
            result = deliver(principal, json.loads(raw_source))
            db.execute("UPDATE monitor_audit_027 SET result=? WHERE seq=? AND principal=?",
                       (canonical(result), seq, principal))
        results.append(result)
    return {"records": results, "authority": "AUTH:0", "execution_authorization": "NOT_GRANTED"}
