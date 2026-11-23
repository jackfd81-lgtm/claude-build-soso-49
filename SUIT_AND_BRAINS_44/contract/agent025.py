"""Versioned agent policy and Mask execution over Build 025's state transaction."""
import json
import re

from contract.authority import require_task
from contract import pathway025
from contract.provenance import provenance_defects
from realcog.procedural_memory import canonical, identity

RUN = {"start", "resume", "show", "expire", "learn"}
MEDIA = {"stream", "frame", "close"}
OPERATIONS = RUN | MEDIA
READ = {"big:read", "small:read"}
SCOPES = {
    "agent_configure": {"admin"},
    "agent_configuration": {"procedure:execute"},
    "agent_start": {"procedure:execute", "observation:write"} | READ,
    "agent_resume": {"procedure:execute", "observation:write"} | READ,
    "agent_show": {"procedure:execute"},
    "agent_expire": {"procedure:execute"},
    "agent_learn": {"procedure:write"},
    "agent_stream": {"observation:write"},
    "agent_frame": {"observation:write"},
    "agent_close": {"observation:write"},
    "agent_inventory": READ | {"audit:raw"},
}
LEGACY_RESTRICTED = {"start025", "resume025", "show025", "expire025", "learn025", "train", "evaluate",
                     "memory_write", "memory_read", "observe", "recall_observations", "qualify025", "revoke025", "qualify", "revoke"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")


def setup(db):
    statements = (
        """CREATE TABLE IF NOT EXISTS agent_modes_025 (
            principal TEXT, version INTEGER, settings TEXT NOT NULL,
            PRIMARY KEY(principal,version))""",
        """CREATE TABLE IF NOT EXISTS agent_bindings_025 (
            principal TEXT, kind TEXT, item TEXT, version INTEGER NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(principal,kind,item))""",
        """CREATE TABLE IF NOT EXISTS agent_receipts_025 (
            principal TEXT, request_id TEXT, receipt TEXT NOT NULL,
            PRIMARY KEY(principal,request_id))""",
    )
    for statement in statements:
        db.execute(statement)


def current(db, principal):
    row = db.execute("SELECT settings FROM agent_modes_025 WHERE principal=? ORDER BY version DESC LIMIT 1",
                     (principal,)).fetchone()
    return json.loads(row[0]) if row else None


def managed(db, principal):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_modes_025'").fetchone()
    return bool(exists and current(db, principal))


def name(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("invalid agent, run or stream identity")
    return value


def source_ok(value):
    return not provenance_defects({"provenance": [value]}) and len(value) <= 200


def configure(db, task):
    target = name(task["agent"])
    if not db.execute("SELECT 1 FROM credentials_024 WHERE principal=?", (target,)).fetchone():
        raise KeyError("unknown authenticated agent")
    settings = task["configuration"]
    if not isinstance(settings, dict) or settings.keys() - {"suit_enabled", "mask_enabled", "policy"}:
        raise ValueError("invalid agent configuration")
    if type(settings.get("suit_enabled")) is not bool or type(settings.get("mask_enabled")) is not bool:
        raise ValueError("both subsystem switches must be booleans")
    previous = current(db, target)
    version = previous["version"] if previous else 0
    if type(task.get("expected_version")) is not int or version != task["expected_version"]:
        raise ValueError("configuration version conflict")
    if previous is None and db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pathway_025'").fetchone():
        for (raw,) in db.execute("SELECT state FROM pathway_025 WHERE principal=?", (target,)):
            if json.loads(raw)["decision"]["status"] == "HOLD":
                raise ValueError("complete or expire legacy HOLD before enrolling the agent")
    policy = settings.get("policy", previous["policy"] if previous else {})
    if not isinstance(policy, dict) or policy.keys() - {"operations", "source_prefixes", "max_frames", "max_hypotheses"}:
        raise ValueError("invalid agent policy")
    allowed = policy.get("operations", sorted(OPERATIONS))
    prefixes = policy.get("source_prefixes", [])
    frames = policy.get("max_frames", 2048)
    hypotheses = policy.get("max_hypotheses", 12)
    if (not isinstance(allowed, list) or any(not isinstance(x, str) or x not in OPERATIONS for x in allowed)
            or not isinstance(prefixes, list) or len(prefixes) > 16
            or any(not isinstance(x, str) or not x.strip() or len(x) > 100 for x in prefixes)
            or type(frames) is not int or not 1 <= frames <= 2048
            or type(hypotheses) is not int or not 2 <= hypotheses <= 12):
        raise ValueError("invalid Suit policy values")
    config = {"agent": target, "version": version + 1, "suit_enabled": settings["suit_enabled"],
              "mask_enabled": settings["mask_enabled"], "policy": {
                  "operations": sorted(set(allowed)), "source_prefixes": sorted(set(prefixes)),
                  "max_frames": frames, "max_hypotheses": hypotheses}}
    db.execute("INSERT INTO agent_modes_025 VALUES (?,?,?)", (target, version + 1, canonical(config)))
    return {"configuration": config}


def suit(config, operation, sources, weight, frame):
    if not config["suit_enabled"]:
        return {"decision": "BYPASSED", "reason": "optional_mediation_disabled"}
    policy = config["policy"]
    reason = "advisory_capability_admitted"
    prefixes = tuple(policy["source_prefixes"])
    if operation not in policy["operations"] and operation not in ("expire", "close"):
        reason = "operation_outside_capabilities"
    elif prefixes and any(not value.startswith(prefixes) for value in sources):
        reason = "evidence_source_outside_capabilities"
    elif frame is not None and frame >= policy["max_frames"]:
        reason = "suit_frame_budget_exhausted"
    elif weight > policy["max_hypotheses"]:
        reason = "suit_model_budget_exhausted"
    return {"decision": "ADMIT" if reason == "advisory_capability_admitted" else "REFUSE", "reason": reason}


def save_binding(db, principal, kind, item, config, payload):
    db.execute("""INSERT INTO agent_bindings_025 VALUES (?,?,?,?,?)
                  ON CONFLICT(principal,kind,item) DO UPDATE SET payload=excluded.payload""",
               (principal, kind, item, config["version"], canonical(payload)))


def validate_start(task):
    problem = task.get("problem")
    require_task(problem)
    pathway = problem.get("pathway")
    if pathway == "C":
        model, _, _ = pathway025.validate_model(problem)
        weight = len(model)
        intake = problem.get("intake", {})
        require_task(intake)
        observations = intake.get("observations", [])
    elif pathway in ("A", "B"):
        weight = 0
        observations = problem.get("observations")
    else:
        raise ValueError("unknown pathway")
    observations = pathway025.require_list(observations, 32)
    if any(not isinstance(row, dict) or not source_ok(row.get("source")) for row in observations):
        raise ValueError("sourced observations required")
    for row in observations:
        require_task(row)
    return [row["source"] for row in observations], weight


def run_task(db, principal, task, op, key, conf, state, fault):
    if not conf["mask_enabled"]:
        if op in ("resume", "learn"):
            return {"decision": {"status": "ABSTAIN", "reason": "mask_disabled"}, "trace": []}
        if op == "start":
            result = {"decision": {"status": "ABSTAIN", "reason": "base_agent_records_only"},
                      "trace": [], "observation_digest": identity(task["problem"])}
            save_binding(db, principal, "run", key, conf, {"result": result, "problem": task["problem"]})
            return result
        return state["result"]
    underlying = {k: v for k, v in task.items() if k not in ("operation", "run")}
    underlying.update(operation=op + "025", run="agent-" + identity([principal, key])[:48])
    if op == "start":
        save_binding(db, principal, "run", key, conf, {})
    return pathway025.handle(db, principal, underlying, lambda value: value, fault)


def handle(db, principal, scopes, owners, task, audit, fault=None):
    setup(db)
    encoded = canonical(task).encode("utf-8")
    if len(encoded) > 65536:
        raise ValueError("agent request exceeds 64 KiB")
    action = task["operation"].removeprefix("agent_")
    head = current(db, principal)
    if action == "configure":
        return audit({"authority": 0, **configure(db, task)})
    if head is None:
        raise PermissionError("agent has no configured runtime")
    if action == "configuration":
        return {"authority": 0, "configuration": head}
    if action == "inventory":
        from contract.visual025 import inventory
        return {"authority": 0, "agent": principal, "blocks": inventory(db, principal)}
    kind = "run" if action in RUN else "stream"
    key = name(task[kind])
    bound = db.execute("SELECT version,payload FROM agent_bindings_025 WHERE principal=? AND kind=? AND item=?",
                       (principal, kind, key)).fetchone()
    conf = head if not bound else json.loads(db.execute(
        "SELECT settings FROM agent_modes_025 WHERE principal=? AND version=?", (principal, bound[0])).fetchone()[0])
    request_id = identity([principal, conf["version"], task])
    row = db.execute("SELECT receipt FROM agent_receipts_025 WHERE principal=? AND request_id=?",
                     (principal, request_id)).fetchone()
    if row is not None:
        return json.loads(row[0])
    opening = action in ("start", "stream")
    if opening == bool(bound):
        if opening:
            raise ValueError("identity already open")
        raise KeyError("unknown investigation or stream")
    state = json.loads(bound[1]) if bound else {}
    sources, weight, frame = [], 0, None
    if action == "start":
        sources, weight = validate_start(task)
        if "mask_enabled" in task or "suit_enabled" in task or "configuration" in task:
            raise ValueError("mode changes require the configuration operation")
    if action in ("resume", "frame", "stream"):
        if action == "resume":
            require_task(task["measurement"])
        source = task["measurement"].get("source") if action == "resume" else task.get("source")
        if not source_ok(source):
            raise ValueError("valid presented source required")
        sources = [source]
    if action == "frame":
        frame = task["seq"]
        if type(frame) is not int or not 0 <= frame < 2048:
            raise ValueError("invalid frame sequence")
        if conf["mask_enabled"] and (not {"big:read", "small:read", "big:write", "small:write"} <= scopes
                                     or not ({"L30", "*"} & owners)):
            raise PermissionError("visual Mask requires scoped memory capabilities")
    verdict = suit(conf, action, sources, weight, frame)
    result = {"authority": 0, "agent": principal, "configuration_version": conf["version"],
              "suit_enabled": conf["suit_enabled"], "mask_enabled": conf["mask_enabled"], "suit": verdict,
              "request_id": request_id, kind: key}
    if verdict["decision"] == "REFUSE":
        result.update(decision={"status": "REFUSED", "reason": verdict["reason"]}, trace=[])
    elif kind == "run":
        result.update(run_task(db, principal, task, action, key, conf, state, fault))
    else:
        from contract.visual025 import handle_media
        result.update(handle_media(db, principal, task, action, key, conf, state, request_id))
    result[kind] = key
    result["request_id"] = request_id
    if action == "show":
        return result
    db.execute("INSERT INTO agent_receipts_025 VALUES (?,?,?)", (principal, request_id, canonical(result)))
    if fault:
        fault("before_pathway_025_commit")
    return audit(result)
