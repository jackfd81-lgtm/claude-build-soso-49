"""Immutable RealCog investigation methods linked through the existing pools."""
import json

from realcog.memory024 import OWNER
from realcog.ph6_brains_2208 import Big49, Block, Pointer, Rejected, Small49
from realcog.procedural_memory import canonical, identity

CONTRACT = "investigation-partition-v1"


def configure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS method_025 (
        id TEXT PRIMARY KEY, trainer TEXT NOT NULL, body TEXT NOT NULL,
        qualification TEXT, big TEXT, small TEXT, revoked INTEGER NOT NULL DEFAULT 0)""")


def record(db, trainer, profile, experience):
    body = {"contract": CONTRACT, "profile": profile,
            "training": identity(experience), "trainer": trainer}
    method = identity(body)
    db.execute("INSERT OR IGNORE INTO method_025 (id,trainer,body) VALUES (?,?,?)",
               (method, trainer, canonical(body)))
    return method


def qualify(db, method, qualifier, check, fault=None):
    row = db.execute("SELECT trainer,body,qualification,revoked FROM method_025 WHERE id=?",
                     (method,)).fetchone()
    if row is None or row[3]:
        raise ValueError("no eligible method")
    if row[0] == qualifier:
        raise PermissionError("qualification requires a separate principal")
    body = json.loads(row[1])
    if identity(body) != method:
        raise ValueError("method digest mismatch")
    if row[2]:
        return {"method": method, "qualification": json.loads(row[2]), "memory": [], "replayed": True}
    evidence = {"qualifier": qualifier, "checks": check(body["profile"])}
    big = {"method": method, "contract": CONTRACT, "proof": identity(evidence), "profile": body["profile"]}
    bp = Big49(connection=db).put(Block("p025-" + method, OWNER, "cognitive", canonical(big),
        source_refs=("int:method:" + method, "int:training:" + body["training"])), principal=OWNER)
    if fault:
        fault("after_big_025")
    small = {"method": method, "contract": CONTRACT, "big": bp.digest}
    sp = Small49(connection=db).put(Block("c025-" + method, OWNER, "active", canonical(small),
        source_refs=("int:" + bp.handle,)), principal=OWNER)
    db.execute("UPDATE method_025 SET qualification=?,big=?,small=? WHERE id=?",
               (canonical(evidence), canonical(vars(bp)), canonical(vars(sp)), method))
    return {"method": method, "qualification": evidence, "memory": [receipt(bp, "write"), receipt(sp, "write")]}


def receipt(pointer, operation):
    return dict(vars(pointer), operation=operation, authority=0, function_id="U15" if operation == "read" else None)


def recall(db, requested=None):
    candidates, receipts = [], []
    for method, body, qualification, bp, sp in db.execute(
            """SELECT id,body,qualification,big,small FROM method_025
               WHERE revoked=0 AND qualification IS NOT NULL AND (? IS NULL OR id=?) ORDER BY id""",
            (requested, requested)):
        try:
            small_pointer = Pointer(**json.loads(sp))
            big_pointer = Pointer(**json.loads(bp))
            sr = Small49(connection=db).read(small_pointer, principal=OWNER)
            receipts.append(receipt(small_pointer, "read"))
            if json.loads(sr["content"]) != {"contract": CONTRACT, "method": method, "big": big_pointer.digest}:
                raise ValueError("context pointer mismatch")
            br = Big49(connection=db).read(big_pointer, principal=OWNER)
            receipts.append(receipt(big_pointer, "read"))
            strategy = json.loads(br["content"])
            body = json.loads(body)
            if identity(body) != method or strategy != {"contract": CONTRACT, "method": method,
                    "proof": identity(json.loads(qualification)), "profile": body["profile"]}:
                raise ValueError("method or proof mismatch")
            candidates.append({**strategy, "blocks": [small_pointer.record_id, big_pointer.record_id]})
        except (ValueError, TypeError, KeyError, Rejected) as exc:
            receipts.append({"method": method, "operation": "rejected", "reason": str(exc), "authority": 0})
    return candidates, receipts
