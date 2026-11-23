"""Opt-in advisory bridge from committed Suit HOLD events to SoSoSuit 4.2."""
import argparse
import hashlib
import json
from pathlib import Path
import re

import sososuit42 as suit
from realcog.procedural_memory import identity

TRACE_HASH = re.compile(r"[0-9a-f]{64}\Z")
MAPPING = "investigation-reading/v1"


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS shadow029_config (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS shadow029_principal (
        subject TEXT PRIMARY KEY, principal TEXT NOT NULL UNIQUE)""")
    db.execute("""CREATE TABLE IF NOT EXISTS shadow029_work (
        source_id TEXT PRIMARY KEY, seq INTEGER NOT NULL, principal TEXT NOT NULL,
        context BLOB, governed_digest BLOB, status TEXT NOT NULL,
        candidate BLOB, receipt BLOB, diagnostic TEXT)""")
    db.execute("""CREATE INDEX IF NOT EXISTS shadow029_pending
                 ON shadow029_work(status,seq)""")


def enabled(db):
    return db.execute("SELECT 1 FROM shadow029_config WHERE enabled=1").fetchone() is not None


def principal_id(principal):
    # Bind the exact authenticated host identity before 4.2 normalizes strings.
    return "p-" + hashlib.sha256(principal.encode()).hexdigest()[:62]


def source_id(principal, payload, seq):
    request = payload.get("request")
    task_id = payload.get("task")
    request_id = request.get("id") if isinstance(request, dict) else None
    if isinstance(task_id, str) and isinstance(request_id, str):
        return identity([MAPPING, principal, task_id, request_id])
    return identity([MAPPING, principal, seq])


def derive_context(principal, seq, payload, trace_digest):
    request = payload.get("request")
    if (not isinstance(request, dict) or not isinstance(payload.get("task"), str)
            or not isinstance(request.get("id"), str) or not TRACE_HASH.fullmatch(request["id"])
            or payload.get("context") != request["id"]
            or not isinstance(request.get("feature"), str) or not request["feature"]
            or request.get("release") != "matching sourced measurement"):
        raise suit.Invalid("UNSUPPORTED_EVIDENCE_GAP")
    evidence = {"kind": "INVESTIGATION_HOLD", "seq": seq, "trace_sha256": trace_digest,
                "task": payload["task"], "request": request["id"], "feature": request["feature"],
                "mapping": MAPPING}
    return {"task_id": "t-" + source_id(principal, payload, seq)[:62],
            "principal": principal_id(principal), "allowed_actions": ["NO_OP", "REQUEST_READING"],
            "evidence": {"ext:source": evidence}}


def capture(db, principal, seq, kind, payload, trace_digest):
    """Run in the same SQLite transaction as the source event."""
    if kind != "INVESTIGATION_HOLD" or not enabled(db):
        return
    key = source_id(principal, payload, seq)
    context = governed_digest = diagnostic = None
    try:
        value = derive_context(principal, seq, payload, trace_digest)
        db.execute("INSERT OR IGNORE INTO shadow029_principal VALUES (?,?)",
                   (value["principal"], principal))
        if db.execute("SELECT principal FROM shadow029_principal WHERE subject=?",
                      (value["principal"],)).fetchone() != (principal,):
            raise suit.Invalid("PRINCIPAL_MAPPING_CONFLICT")
        context = suit.canonical(value)
        governed_digest = suit.canonical(suit.digest(suit.govern_context(value)))
    except suit.Invalid as exc:
        diagnostic = str(exc)
    db.execute("""INSERT OR IGNORE INTO shadow029_work
                  (source_id,seq,principal,context,governed_digest,status,diagnostic)
                  VALUES (?,?,?,?,?,?,?)""",
               (key, seq, principal, context, governed_digest,
                "DIAGNOSTIC" if diagnostic else "PENDING", diagnostic))


def candidate_for(context, governed_digest):
    return {"producer": "AI", "candidate_id": context["task_id"],
            "governed_state_digest": governed_digest, "actions": ["REQUEST_READING"],
            "claims": [{"id": "int:gap", "text": "An investigation requested a reading.",
                        "refs": ["ext:source"]}],
            "ious": [{"id": "reading", "type": "READING", "code": "IOU_MISSING_DISCRIMINATOR",
                      "description": "Obtain the measurement identified by the source event."}]}


def _verify_work(db, key, seq, principal, context, governed_digest):
    row = db.execute("SELECT previous,principal,kind,payload,hash FROM suit61_trace WHERE seq=?",
                     (seq,)).fetchone()
    if row is None or row[1:3] != (principal, "INVESTIGATION_HOLD"):
        raise suit.Invalid("SOURCE_EVENT_UNAVAILABLE")
    previous, _, kind, payload_bytes, trace_digest = row
    payload = json.loads(payload_bytes)
    if identity(dict(seq=seq, previous=previous, principal=principal, kind=kind, payload=payload)) != trace_digest:
        raise suit.Invalid("SOURCE_TRACE_DIGEST_MISMATCH")
    expected = derive_context(principal, seq, payload, trace_digest)
    if db.execute("SELECT principal FROM shadow029_principal WHERE subject=?",
                  (expected["principal"],)).fetchone() != (principal,):
        raise suit.Invalid("PRINCIPAL_MAPPING_CONFLICT")
    if (source_id(principal, payload, seq) != key or suit.canonical(expected) != context
            or suit.canonical(suit.digest(suit.govern_context(expected))) != governed_digest):
        raise suit.Invalid("SOURCE_BINDING_MISMATCH")
    return expected


def drain(root, limit=100, fault=None):
    """Host worker. Retry uses persisted inputs and the journal's decision identity."""
    from muse024 import private_root, transaction

    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be in 1..1000")
    root = private_root(root)
    with transaction(root) as db:
        setup(db)
        if not enabled(db):
            return []
        keys = db.execute("SELECT source_id FROM shadow029_work WHERE status='PENDING' ORDER BY seq LIMIT ?",
                          (limit,)).fetchall()
    results = []
    for (key,) in keys:
        with transaction(root) as db:
            if not enabled(db):
                break
            row = db.execute("""SELECT seq,principal,context,governed_digest,candidate
                                FROM shadow029_work WHERE source_id=? AND status='PENDING'""",
                             (key,)).fetchone()
            if row is None:
                continue
            seq, principal, context, governed_digest, candidate = row
            try:
                value = _verify_work(db, key, seq, principal, context, governed_digest)
                expected = suit.canonical(candidate_for(value, suit.parse(governed_digest)))
                if candidate is not None and candidate != expected:
                    raise suit.Invalid("CANDIDATE_BINDING_MISMATCH")
            except (suit.Invalid, json.JSONDecodeError) as exc:
                db.execute("UPDATE shadow029_work SET status='DIAGNOSTIC',diagnostic=? WHERE source_id=?",
                           (str(exc), key))
                results.append({"source_id": key, "status": "DIAGNOSTIC", "diagnostic": str(exc)})
                continue
            # Keep the host write lock through submission. A crash after the
            # journal commit rolls back only this acknowledgement transaction.
            if fault:
                fault("before_journal_submit")
            receipt = suit.Journal(root / "sososuit42.sqlite").submit(value, suit.parse(expected))
            if fault:
                fault("after_journal_commit")
            status = "ACKED" if receipt["disposition"]["verdict"] == "ADVISORY_ACCEPT" else "REFUSED"
            db.execute("""UPDATE shadow029_work SET status=?, candidate=?, receipt=?
                          WHERE source_id=?""", (status, expected, suit.canonical(receipt), key))
            results.append({"source_id": key, "status": status})
    return results


def configure(root, active):
    from muse024 import private_root, transaction

    with transaction(private_root(root)) as db:
        setup(db)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='suit61_trace'").fetchone():
            raise ValueError("initialize the MUSE root before configuring shadow integration")
        db.execute("INSERT OR REPLACE INTO shadow029_config VALUES (1,?)", (int(active),))


def receipts(db, principal, task):
    after = task.get("after", 0)
    if type(after) is not int or after < 0:
        raise ValueError("after must be a nonnegative trace sequence")
    fields = ("source_id", "seq", "status", "diagnostic", "receipt")
    records = []
    for row in db.execute("""SELECT source_id,seq,status,diagnostic,receipt FROM shadow029_work
                             WHERE principal=? AND seq>? ORDER BY seq LIMIT 100""", (principal, after)):
        entry = dict(zip(fields, row))
        if entry["receipt"] is not None:
            entry["receipt"] = suit.parse(entry["receipt"])
        records.append(entry)
    return {"records": records, **suit.AUTHORITY}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("action", choices=("enable", "disable", "drain"))
    args = parser.parse_args()
    if args.action == "drain":
        print(json.dumps(drain(args.root)))
    else:
        active = args.action == "enable"
        configure(args.root, active)
        print(json.dumps({"enabled": active}))


if __name__ == "__main__":
    main()
