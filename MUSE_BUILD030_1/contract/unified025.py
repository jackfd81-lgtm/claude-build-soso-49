"""Connect a committed Pathway C outcome to governed Suit admission."""
import json

from contract import pathway025
from realcog.procedural_memory import canonical, identity
from suit61 import compare, store

PREFIX = "unified61-"
SCOPES = {
    "unified_start025": {"procedure:execute", "observation:write", "big:read", "small:read", "suit:execute", "suit:admit"},
    "unified_resume025": {"procedure:execute", "observation:write", "big:read", "small:read", "suit:execute", "suit:admit"},
    "unified_retry025": {"procedure:execute", "big:read", "small:read", "suit:execute", "suit:admit"},
    "unified_show025": {"procedure:execute", "suit:read"},
    "unified_learn025": {"procedure:execute", "procedure:write"},
    "unified_train026": {"procedure:execute", "procedure:write"},
}


def handle(db, principal, task, audit, fault=None):
    store.setup(db)
    db.execute("""CREATE TABLE IF NOT EXISTS unified_runs_025 (
        principal TEXT, run TEXT, targets TEXT, cognitive TEXT, handoff TEXT,
        PRIMARY KEY(principal,run))""")
    db.execute("""CREATE TABLE IF NOT EXISTS unified_receipts_025 (
        principal TEXT, request TEXT, response TEXT, PRIMARY KEY(principal,request))""")
    run = task["run"]
    if not isinstance(run, str) or not run.strip() or len(run) > 80 or len(canonical(task).encode()) > 65536:
        raise ValueError("invalid unified request")
    tid = PREFIX + identity([principal, run])[:48]
    action = task["operation"]
    fields = {"unified_start025": {"problem", "targets", "method", "memory_enabled"}, "unified_resume025": {"measurement", "evidence_ref"},
              "unified_retry025": set(), "unified_show025": set(), "unified_learn025": set(), "unified_train026": set()}
    if set(task) - (fields[action] | {"operation", "authority", "run"}):
        raise ValueError("unexpected unified request fields")
    request = identity(task)
    prior = db.execute("SELECT response FROM unified_receipts_025 WHERE principal=? AND request=?",
                       (principal, request)).fetchone()
    if prior:
        return json.loads(prior[0])
    row = db.execute("SELECT targets,cognitive,handoff FROM unified_runs_025 WHERE principal=? AND run=?",
                     (principal, run)).fetchone()
    if action == "unified_start025":
        if row is not None:
            raise ValueError("unified run exists")
        problem = task["problem"]
        if not isinstance(problem, dict) or problem.get("pathway") != "C":
            raise ValueError("unified investigation requires Pathway C")
        hypotheses, _, _ = pathway025.validate_model(problem)
        raw = task["targets"]
        if not isinstance(raw, dict) or not raw or raw.keys() - hypotheses.keys():
            raise ValueError("Suit targets must map to offered hypotheses")
        targets = {key: compare.normalize_target(value) for key, value in raw.items()}
        memory_enabled = task.get("memory_enabled", True)
        if type(memory_enabled) is not bool:
            raise ValueError("memory_enabled must be boolean")
        cognitive = pathway025.handle(db, principal, {
            "operation": "start025", "run": tid, "problem": problem,
            "method": task.get("method"), **({} if memory_enabled else {"arm": "B"}),
        },
                                      lambda value: value)
        handoff = None
    else:
        if row is None:
            raise KeyError("unified run not found")
        targets, cognitive, handoff = map(json.loads, row)
        if action == "unified_show025":
            return {"run": run, "cognitive": cognitive, "handoff": handoff,
                    "task": store.resume(db, principal, {"task_id": tid}) if handoff and handoff.get("task_id") else None,
                    "authority": 0}
        if action in ("unified_learn025", "unified_train026"):
            if action == "unified_train026" and cognitive["decision"]["status"] != "SUPPORTED":
                raise ValueError("training requires a supported investigation")
            if action == "unified_learn025" and (not handoff or not handoff.get("task_id") or store.resume(
                    db, principal, {"task_id": tid})["status"] != "COMPLETE"):
                raise ValueError("complete the evidence-backed Suit task before learning")
            learned = pathway025.handle(db, principal, {"operation": "learn025", "run": tid}, lambda value: value)
            result = {"run": run, "learning": learned, "handoff": handoff, "authority": 0}
            db.execute("INSERT INTO unified_receipts_025 VALUES (?,?,?)", (principal, request, canonical(result)))
            return audit(result)
        if action == "unified_resume025":
            measurement = task["measurement"]
            if not isinstance(measurement, dict) or set(measurement) != {"request_id", "feature", "source", "value"}:
                raise ValueError("a scoped observation is required")
            store.verify_evidence(db, task["evidence_ref"], {"kind": "investigation", "run": run,
                                  "principal": principal, "measurement": measurement}, independent_of=principal)
            cognitive = pathway025.handle(db, principal, {"operation": "resume025", "run": tid,
                                          "measurement": measurement}, lambda value: value)
    decision = cognitive["decision"]
    if decision["status"] == "HOLD" and "request" in decision:
        store.trace(db, principal, "INVESTIGATION_HOLD", {"task": tid, "context": decision["request"]["id"],
                                                        "request": decision["request"]})
    if handoff and handoff.get("task_id"):
        pass
    elif decision["status"] != "SUPPORTED":
        handoff = {"status": decision["status"], "reason": "investigation has no supported conclusion"}
    elif decision["answer"] not in targets:
        handoff = {"status": "HOLD", "reason": "no Suit target configured for supported hypothesis"}
    else:
        target = targets[decision["answer"]]
        got = store.retrieve(db, principal, {"task_id": tid, "target": target})
        if got["mode"] not in ("REUSE_QUALIFIED_SKILL", "REUSE_QUALIFIED_TRANSFER"):
            handoff = {"status": "HOLD", "retrieval": got}
        else:
            handoff = store.begin(db, principal, {"operation": "suit_begin", "task_id": tid,
                                  "target": target, "skill_id": got["skill_id"], "transfer_id": got.get("transfer_id"),
                                  "retrieval_receipt": got["receipt"]})
        store.trace(db, principal, "UNIFIED_HANDOFF", {"run": run, "cognitive_event": cognitive["event"],
                    "hypothesis": decision["answer"], "suit_task": handoff.get("task_id"), "status": handoff["status"]})
    db.execute("""INSERT INTO unified_runs_025 VALUES (?,?,?,?,?)
                  ON CONFLICT(principal,run) DO UPDATE SET cognitive=excluded.cognitive,handoff=excluded.handoff""",
               (principal, run, canonical(targets), canonical(cognitive), canonical(handoff)))
    result = {"run": run, "cognitive": cognitive, "handoff": handoff, "authority": 0}
    # A retry with no admission must re-evaluate memory after an independent qualification.
    if action != "unified_retry025" or handoff.get("task_id"):
        db.execute("INSERT INTO unified_receipts_025 VALUES (?,?,?)", (principal, request, canonical(result)))
    if fault:
        fault("before_unified_025_commit")
    return audit(result)
