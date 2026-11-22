"""Local authenticated MUSE runtime command line."""
import argparse
from contextlib import closing, contextmanager
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
import tempfile

from contract.authority import require_task
from contract import agent025, derivation025, pathway025
from contract import unified025
import suit61.ops
import suit61.store
from contract.cognitive_pathway_023 import run_cognitive_023
from contract.observation_memory import _connect, observation_rows, insert_rows, recall_observations
from realcog import memory024, procedural_memory
from realcog.ph6_brains_2208 import Big49, Small49, Pointer, _region

DOMAINS = ("big", "small")
POLICY = {
    **agent025.SCOPES,
    **suit61.ops.SCOPES,
    **unified025.SCOPES,
    "show025": {"procedure:execute"},
    "start025": {"procedure:execute", "observation:write", "big:read", "small:read"},
    "resume025": {"procedure:execute", "observation:write", "big:read", "small:read"},
    "expire025": {"procedure:execute"},
    "learn025": {"procedure:write"},
    "qualify025": {"procedure:qualify", "procedure:read", "big:write", "small:write"},
    "revoke025": {"procedure:qualify"},
    "observe": {"observation:write"}, "recall_observations": {"observation:read"},
    "train": {"procedure:write", "procedure:execute"}, "evaluate": {"procedure:execute", "procedure:read", "big:read", "small:read"},
    "qualify": {"procedure:qualify", "procedure:read", "big:write", "small:write"},
    "revoke": {"procedure:qualify"}, "occupancy": {"big:read", "small:read"}, "quarantine": {"big:read", "small:read"},
    "audit": {"audit:read"}, "issue": {"admin"}, "backup": {"admin"},
    "migrate": {"admin"}, "restore": {"admin"}, "recover_audit": {"admin"}, "memory_read": set(), "memory_write": set(),
}
SCOPES = set().union(*POLICY.values(), {"observation:read", "observation:write", "audit:raw", "experiment"})
POOLS = {"big": Big49, "small": Small49}


def private_root(root, *, create=False):
    root = Path(root)
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PermissionError("state directory must be a private OS-owned directory")
    return root


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def make_token():
    return secrets.token_urlsafe(32)


@contextmanager
def transaction(root):
    with closing(_connect(root)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        yield db


def initialize(root):
    root = private_root(root, create=True)
    secret = make_token()
    with transaction(root) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS credentials_024
                      (hash TEXT PRIMARY KEY, principal TEXT UNIQUE, scopes TEXT, owners TEXT)""")
        if db.execute("SELECT 1 FROM credentials_024 LIMIT 1").fetchone():
            raise ValueError("already initialized")
        db.execute("INSERT INTO credentials_024 VALUES (?,?,?,?)",
                   (token_hash(secret), "bootstrap", json.dumps(sorted(SCOPES)), json.dumps(["*"])))
        migrate_db(db)
    return secret


def migrate_db(db, fault=None):
    suit61.store.setup(db)
    procedural_memory.configure(db)
    memory024.configure(db)
    if fault:
        fault("before_migration_commit")
    db.execute("""CREATE TABLE IF NOT EXISTS operations_024 (
        seq INTEGER PRIMARY KEY, principal TEXT NOT NULL, operation TEXT NOT NULL, output TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS audit_attempts_024 (
        seq INTEGER PRIMARY KEY, principal TEXT NOT NULL, operation TEXT NOT NULL,
        process TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        state TEXT NOT NULL, error_type TEXT, request_digest TEXT)""")
    return memory024.utilization(db)


def process_identity(pid):
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        return f"{pid}:{text.rsplit(')', 1)[1].split()[19]}"
    except (OSError, IndexError):
        return None


def reconcile_attempts(db):
    recovered = []
    for seq, process in db.execute("SELECT seq,process FROM audit_attempts_024 WHERE state='STARTED'"):
        pid = int(process.partition(":")[0])
        if process_identity(pid) != process:
            db.execute("UPDATE audit_attempts_024 SET state='INTERRUPTED' WHERE seq=?", (seq,))
            recovered.append(seq)
    return {"recovered_attempts": recovered, "state": "INTERRUPTED", "requires_receipt_check": bool(recovered)}


def log_operation(db, principal, operation, output):
    db.execute("INSERT INTO operations_024(principal,operation,output) VALUES (?,?,?)",
               (principal, operation, procedural_memory.canonical(output)))
    return output


def login(root, token):
    if not isinstance(token, str) or not re.fullmatch(r"[\w-]{43}", token, re.ASCII):
        raise PermissionError("invalid credential")
    path = root / "observations.sqlite3"
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT principal,scopes,owners FROM credentials_024 WHERE hash=?",
                         (token_hash(token),)).fetchone()
    if row is None:
        raise PermissionError("invalid credential")
    return row[0], set(json.loads(row[1])), set(json.loads(row[2]))


def issue(root, task):
    principal = task.get("principal")
    scopes, owners = task.get("scopes"), task.get("owners", [])
    if not isinstance(principal, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", principal):
        raise ValueError("invalid principal")
    if not isinstance(scopes, list) or not all(isinstance(s, str) and s in SCOPES and s != "admin" for s in scopes):
        raise ValueError("invalid scopes")
    if not isinstance(owners, list) or not all(isinstance(x, str) for x in owners):
        raise ValueError("invalid owners")
    for owner in owners:
        _region(owner)
    secret = make_token()
    with transaction(root) as db:
        db.execute("INSERT INTO credentials_024 VALUES (?,?,?,?)",
                   (token_hash(secret), principal, json.dumps(scopes), json.dumps(owners)))
    return {"token": secret, "principal": principal}


def copy_snapshot(source, destination):
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError("backup destination exists")
    fd, name = tempfile.mkstemp(dir=dest.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as a:
            with closing(sqlite3.connect(name)) as b:
                a.backup(b)
                if b.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("backup integrity failure")
        with open(name, "rb") as stream:
            os.fsync(stream.fileno())
        os.link(name, dest)
        dir_fd = os.open(dest.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        os.unlink(name)
    return {"backup": str(dest)}


def audit_page(db, task, scopes):
    limit = task.get("limit", 25)
    after = task.get("after", 0)
    raw = task.get("raw", False)
    if type(limit) is not int or not 1 <= limit <= 100 or type(after) is not int or after < 0:
        raise ValueError("invalid pagination")
    if raw and "audit:raw" not in scopes:
        raise PermissionError("raw audit scope required")
    stream = task.get("stream", "execution")
    if stream == "execution":
        query = "SELECT rowid,output FROM executions_023 WHERE rowid>? ORDER BY rowid LIMIT ?"
    elif stream == "operation":
        query = "SELECT seq,output FROM operations_024 WHERE seq>? ORDER BY seq LIMIT ?"
    elif stream == "attempt":
        query = """SELECT seq,principal,operation,process,created,state,error_type,request_digest
                   FROM audit_attempts_024 WHERE seq>? ORDER BY seq LIMIT ?"""
    else:
        raise ValueError("unknown audit stream")
    entries = db.execute(query, (after, limit + 1)).fetchall()
    records = []
    for cursor, *fields in entries[:limit]:
        if stream == "attempt":
            names = ("principal", "operation", "process", "created", "state", "error_type", "request_digest")
            result = dict(zip(names, fields))
            if not raw:
                result.pop("process")
                result.pop("principal")
        else:
            result = json.loads(fields[0])
        if not raw and stream != "attempt":
            result = {k: result[k] for k in ("status", "family", "operation_cost", "chosen_strategy",
                                             "memory_accesses", "contributing_blocks", "operation", "outcome") if k in result}
        records.append({"cursor": cursor, "digest": procedural_memory.identity(result), "record": result})
    return {"records": records, "next": records[-1]["cursor"] if len(entries) > limit else None,
            "redacted": not raw, "truncated_records": False, "stream": stream}


def call(root, token, task, *, fault=None):
    require_task(task)
    root = private_root(root)
    principal, scopes, owners = login(root, token)
    action = task.get("operation")
    if action in ("audit", "show025"):
        return dispatch(root, task, principal, scopes, owners, fault=fault)
    label = action if isinstance(action, str) and action in POLICY else "unknown"
    digest = procedural_memory.identity(task)
    with transaction(root) as db:
        seq = db.execute("""INSERT INTO audit_attempts_024(principal,operation,process,state,request_digest)
                            VALUES (?,?,?,'STARTED',?)""",
                         (principal, label, process_identity(os.getpid()), digest)).lastrowid
    try:
        output = dispatch(root, task, principal, scopes, owners, fault=fault, attempt=seq)
    except Exception as exc:
        try:
            with transaction(root) as db:
                db.execute("UPDATE audit_attempts_024 SET state='FAILED',error_type=? WHERE seq=?",
                           (type(exc).__name__, seq))
        except sqlite3.Error:
            # An unavailable audit store leaves the durable STARTED intent for reconciliation.
            pass
        raise
    if fault and isinstance(action, str) and (action.endswith("025") or action.startswith("agent_")):
        fault("after_pathway_025_commit")
    if not (isinstance(action, str) and (action.endswith("025") or action.startswith("agent_"))):
        with transaction(root) as db:
            db.execute("UPDATE audit_attempts_024 SET state='COMPLETED' WHERE seq=?", (seq,))
    return output


def dispatch(root, task, principal, scopes, owners, *, fault=None, attempt=None):
    action = task.get("operation")
    if not isinstance(action, str) or action not in POLICY:
        raise ValueError("unknown operation")
    required = set(POLICY[action])
    pool = task.get("pool")
    if action in ("memory_read", "memory_write"):
        if pool not in DOMAINS:
            raise ValueError("invalid pool")
        required.add(pool + (":read" if action == "memory_read" else ":write"))
    if task.get("observations") and action in ("evaluate", "train"):
        required.add("observation:write")
    if (task.get("ablate") or task.get("disabled_functions") or task.get("mode") not in (None, action)
            or task.get("arm", "A") != "A"):
        required.add("experiment")
    measurement = task.get("measurement") if action == "resume025" else None
    if isinstance(measurement, dict) and "derive" in measurement:
        required |= derivation025.required_scopes(measurement["derive"])
    if action == "unified_start025" and "memory_enabled" in task:
        required.add("experiment")
    if not required <= scopes:
        raise PermissionError("operation scope required")
    if action == "qualify025" and not ({"L15", "*"} & owners):
        raise PermissionError("qualification owner denied")
    if action in unified025.SCOPES:
        with transaction(root) as db:
            if agent025.managed(db, principal):
                raise PermissionError("unified workflow currently requires an unmanaged scoped principal")
            result = unified025.handle(db, principal, task,
                                       lambda value: log_operation(db, principal, action, value), fault)
            if attempt is not None:
                db.execute("UPDATE audit_attempts_024 SET state='COMPLETED' WHERE seq=?", (attempt,))
            return result
    if action in ("start025", "resume025", "expire025", "learn025", "show025"):
        run = task.get("run")
        if isinstance(run, str) and run.startswith(unified025.PREFIX):
            raise PermissionError("reserved unified workflow identity")
    if action in suit61.ops.SCOPES:
        with transaction(root) as db:
            result = suit61.ops.handle(db, principal, task, fault)
            log_operation(db, principal, action, {"suit": action, "state": result.get("state") or result.get("mode") or result.get("status")})
            if attempt is not None:
                db.execute("UPDATE audit_attempts_024 SET state='COMPLETED' WHERE seq=?", (attempt,))
            return result
    derived = None
    if isinstance(measurement, dict) and "derive" in measurement:
        # Runs the cognitive pathway in its own transaction BEFORE the investigation transaction opens.
        # Deterministic and replay-cached under a task id derived from the request, so a retried or
        # duplicated delivery recomputes nothing and gets the same typed result.
        derived = derivation025.derive(root, principal, procedural_memory.identity([principal, task]),
                                       measurement["derive"])
    if action.startswith("agent_") or action in agent025.LEGACY_RESTRICTED:
        with transaction(root) as db:
            if action.startswith("agent_"):
                result = agent025.handle(db, principal, scopes, owners, task,
                                          lambda value: log_operation(db, principal, action, value), fault)
                if attempt is not None:
                    db.execute("UPDATE audit_attempts_024 SET state='COMPLETED' WHERE seq=?", (attempt,))
                return result
            if agent025.managed(db, principal):
                raise PermissionError("managed agents must use the versioned agent interface")
            if action.endswith("025"):
                result = pathway025.handle(db, principal, task,
                                           lambda value: log_operation(db, principal, action, value), fault, derived)
                if attempt is not None:
                    db.execute("UPDATE audit_attempts_024 SET state='COMPLETED' WHERE seq=?", (attempt,))
                return result
    if action == "issue":
        return issue(root, task)
    if action == "backup":
        return copy_snapshot(root / "observations.sqlite3", task["destination"])
    if action == "restore":
        target = Path(task["destination"])
        target.mkdir(mode=0o700)
        try:
            result = copy_snapshot(Path(task["snapshot"]), target / "observations.sqlite3")
            with transaction(target) as db:
                memory024.utilization(db)
            return result
        except BaseException:
            # Remove only the newly created generation on verification failure.
            for item in target.iterdir():
                item.unlink()
            target.rmdir()
            raise
    if action == "recall_observations":
        return recall_observations(root, task["source_id"])
    if action in ("evaluate", "train"):
        request = {k: v for k, v in task.items() if k not in ("operation", "ablate")}
        if not isinstance(request.get("task_id"), str) or not request["task_id"].strip():
            raise ValueError("task_id required")
        request["task_id"] = principal + ":" + request["task_id"]
        request["task_type"] = "cognitive_023"
        mode = task.get("mode", action)
        if action == "evaluate" and mode == "train":
            raise PermissionError("training requires train operation")
        if action == "train" and mode != "train":
            raise ValueError("invalid training mode")
        request["mode"] = mode
        disabled_pools = task.get("ablate", [])
        if not isinstance(disabled_pools, list) or any(item not in DOMAINS for item in disabled_pools):
            raise ValueError("invalid store ablation")
        request["pilot_ablation"] = sorted(disabled_pools)
        return run_cognitive_023(root, request, pilot_memory=memory024.CognitiveMemory(disabled_pools),
                                 fault=fault)
    with transaction(root) as db:
        if action.endswith("025"):
            def audit(result):
                return log_operation(db, principal, action, result)
            result = pathway025.handle(db, principal, task, audit, fault, derived)
            if attempt is not None:
                db.execute("UPDATE audit_attempts_024 SET state='COMPLETED' WHERE seq=?", (attempt,))
            return result
        if action == "migrate":
            return migrate_db(db, fault)
        if action == "recover_audit":
            outcome = reconcile_attempts(db)
            return log_operation(db, principal, action, {"operation": action, "outcome": "recovered", **outcome})
        if action == "observe":
            rows = observation_rows({**task, "task_type": "observe"})
            result = {"written": insert_rows(db, rows), "digests": [row[0] for row in rows],
                      "epistemic_class": "PRESENTED"}
            log_operation(db, principal, action, {"operation": action, "outcome": "presented", **result})
            return result
        if action == "qualify":
            record = memory024.load_method(db, task["method"])
            if record["training_evidence"]["task_id"].partition(":")[0] == principal:
                raise PermissionError("qualification requires a separate principal")
            cases = json.loads(files("realcog").joinpath("qualification_024.json").read_text())
            scale = json.loads(files("realcog").joinpath("qualification_010_scale.json").read_text())
            result = memory024.qualify(db, task["method"], cases, qualifier=principal, fault=fault, scale_cases=scale)
            log_operation(db, principal, action, {"operation": action, "outcome": "qualified", **result})
            return result
        if action == "revoke":
            return {"revoked": db.execute("INSERT OR IGNORE INTO revoked_024 VALUES (?,?)",
                                         (task["method"], task["reason"])).rowcount}
        if action == "occupancy":
            return memory024.utilization(db)
        if action == "quarantine":
            memory024.configure(db)
            return {"quarantined": [{"method": m, "reason": r, "stored_digest": d} for m, r, d in
                                    db.execute("SELECT method,reason,stored_digest FROM quarantine_024 ORDER BY method")]}
        if action == "audit":
            return audit_page(db, task, scopes)
        owner = task["owner"]
        if owner not in owners and "*" not in owners:
            raise PermissionError("memory owner denied")
        if action == "memory_read":
            pointer = Pointer(**task["pointer"])
            result = POOLS[pool](connection=db).read(pointer, principal=owner)
            log_operation(db, principal, action, {"operation": action, "outcome": "read", "pointer": vars(pointer)})
            return result
        result = memory024.write_block(db, pool, owner, task["record_id"], task["bucket"], task["content"])
        log_operation(db, principal, action, {"operation": action, "outcome": "written", "pointer": result})
        return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("command", choices=("init", "call", "serve"))
    parser.add_argument("--json")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = {"token": initialize(args.state)}
        elif args.command == "serve":
            from contract.http025 import serve
            if args.recover:
                call(args.state, os.environ.get("MUSE024_TOKEN"), {"operation": "recover_audit", "authority": 0})
            serve(private_root(args.state), args.port)
            return 0
        else:
            if args.json == "-":
                data = sys.stdin.buffer.read(131073)
                if len(data) > 131072:
                    raise ValueError("bounded JSON request required")
                args.json = data.decode("utf-8")
            if args.json is None or len(args.json.encode()) > 131072:
                raise ValueError("bounded JSON request required")
            result = call(args.state, os.environ.get("MUSE024_TOKEN"), json.loads(args.json))
        print(procedural_memory.canonical(result))
        return 0
    except (PermissionError, ValueError, OSError, TypeError, KeyError, sqlite3.Error) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
