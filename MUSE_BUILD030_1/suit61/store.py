"""Procedural skill memory, task continuity, transfer memory and an in-transaction hash-chained trace.

Everything runs inside Build 025's authenticated BEGIN IMMEDIATE transaction (muse024.transaction):
a trace event and the state it describes commit together or not at all, writers are serialized by
SQLite across processes, and the trace chain cannot be torn or forked by multiple recorders.
Qualification is performed only by this module on a principal holding `suit:qualify`, from stored
evidence; no caller can assert it.
"""
import hashlib
import json

from contract.provenance import provenance_defects
from realcog.procedural_memory import canonical, identity
from suit61 import compare as C
from suit61 import audit027, shadow029

TABLES = (
    """CREATE TABLE IF NOT EXISTS suit61_evidence (
        reference TEXT PRIMARY KEY, producer TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS suit61_skills (
        skill_id TEXT PRIMARY KEY, record TEXT NOT NULL, digest TEXT NOT NULL, author TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS suit61_practice (
        skill_id TEXT, seq INTEGER, outcome TEXT NOT NULL, evidence_ref TEXT NOT NULL, observer TEXT NOT NULL,
        PRIMARY KEY(skill_id, seq), UNIQUE(skill_id, evidence_ref))""",
    """CREATE TABLE IF NOT EXISTS suit61_skill_qual (
        skill_id TEXT PRIMARY KEY, qualifier TEXT NOT NULL, criteria TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS suit61_transfers (
        transfer_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, target TEXT NOT NULL, context TEXT NOT NULL,
        comparison TEXT NOT NULL, plan TEXT NOT NULL, proposer TEXT NOT NULL, review TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS suit61_attempts (
        transfer_id TEXT, seq INTEGER, outcome TEXT NOT NULL, effective TEXT NOT NULL, evidence_ref TEXT NOT NULL,
        observer TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY(transfer_id, seq), UNIQUE(transfer_id, evidence_ref))""",
    """CREATE TABLE IF NOT EXISTS suit61_transfer_qual (
        transfer_id TEXT PRIMARY KEY, qualifier TEXT NOT NULL, criteria TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS suit61_revoked (kind TEXT, item TEXT, reason TEXT NOT NULL, PRIMARY KEY(kind,item))""",
    """CREATE TABLE IF NOT EXISTS suit61_tasks (
        principal TEXT, task_id TEXT, revision INTEGER NOT NULL, state TEXT NOT NULL, PRIMARY KEY(principal, task_id))""",
    """CREATE TABLE IF NOT EXISTS suit61_links (
        kind TEXT, item TEXT, big_pointer TEXT NOT NULL, small_pointer TEXT NOT NULL, record_digest TEXT NOT NULL,
        PRIMARY KEY(kind,item))""",
    """CREATE TABLE IF NOT EXISTS suit61_trace (
        seq INTEGER PRIMARY KEY, previous TEXT NOT NULL, principal TEXT NOT NULL, kind TEXT NOT NULL,
        payload TEXT NOT NULL, hash TEXT NOT NULL)""",
)
GENESIS = "0" * 64
MIN_PRACTICE, MIN_ATTEMPTS = 2, 3
RANK = {"REUSE_CANDIDATE": 0, "TRANSFER_PROPOSED_ADAPTATION": 1, "TRANSFER_PROPOSED_NEEDS_EVIDENCE": 2, "DO_NOT_TRANSFER": 3}


def setup(db):
    audit027.setup(db)
    shadow029.setup(db)
    for t in TABLES:
        db.execute(t)


def _ident(v):
    return C._ident(v)


def _evidence(ref):
    if not isinstance(ref, str) or not ref.strip() or len(ref) > 200 or provenance_defects({"provenance": [ref]}):
        raise ValueError("evidence reference with acceptable provenance required")
    return ref


# ----------------------------------------------------------------------------- trace
def observe(db, principal, task):
    ref = _evidence(task["evidence_ref"])
    body = task["observation"]
    if not isinstance(body, dict) or body.get("kind") not in ("practice", "attempt", "step", "investigation"):
        raise ValueError("invalid observation kind")
    digest = identity([principal, ref, body])
    old = db.execute("SELECT digest FROM suit61_evidence WHERE reference=?", (ref,)).fetchone()
    if old and old[0] != digest:
        raise ValueError("conflicting observation identity")
    if not old:
        db.execute("INSERT INTO suit61_evidence VALUES (?,?,?,?)", (ref, principal, canonical(body), digest))
        trace(db, principal, "OBSERVATION_REGISTERED", {"reference": ref, "digest": digest})
    return {"reference": ref, "digest": digest}


def verify_evidence(db, ref, expected, *, producer=None, independent_of=None):
    row = db.execute("SELECT producer,body,digest FROM suit61_evidence WHERE reference=?", (ref,)).fetchone()
    if row is None:
        raise ValueError("registered observation required")
    actor, raw, digest = row
    body = json.loads(raw)
    if (digest != identity([actor, ref, body]) or body != expected or
            (producer is not None and actor != producer) or actor == independent_of):
        raise ValueError("observation identity, producer or outcome mismatch")
    if db.execute("SELECT 1 FROM suit61_revoked WHERE kind='evidence' AND item=?", (ref,)).fetchone():
        raise ValueError("observation revoked")


def transfer_digest(row):
    return identity([json.loads(row[0]), json.loads(row[1]), json.loads(row[2]), row[3]])


def trace(db, principal, kind, payload):
    row = db.execute("SELECT seq,hash FROM suit61_trace ORDER BY seq DESC LIMIT 1").fetchone()
    seq, prev = (row[0] + 1, row[1]) if row else (1, GENESIS)
    body = {"seq": seq, "previous": prev, "principal": principal, "kind": kind, "payload": payload}
    digest = hashlib.sha256(canonical(body).encode()).hexdigest()
    db.execute("INSERT INTO suit61_trace VALUES (?,?,?,?,?,?)", (seq, prev, principal, kind, canonical(payload), digest))
    audit027.record(db, principal, seq, kind, payload, digest)
    shadow029.capture(db, principal, seq, kind, payload, digest)
    return seq


def verify_trace(db):
    prev, n = GENESIS, 0
    for seq, previous, principal, kind, payload, digest in db.execute("SELECT * FROM suit61_trace ORDER BY seq"):
        body = {"seq": seq, "previous": previous, "principal": principal, "kind": kind, "payload": json.loads(payload)}
        if seq != n + 1 or previous != prev or digest != hashlib.sha256(canonical(body).encode()).hexdigest():
            return {"intact": False, "first_bad": seq}
        prev, n = digest, seq
    return {"intact": True, "events": n, "head": prev}


# ----------------------------------------------------------------------------- Big49 / Small49 handshake
LINK_OWNER = "L15"


def _stores(db):
    from realcog.ph6_brains_2208 import Big49, Small49
    return Big49(connection=db), Small49(connection=db)


def link_memory(db, kind, item, record_digest, context_digests):
    """Write the qualified item's Big49 reference and Small49 context route in the CALLER's transaction.
    Any failure (including Big/Small rejection) aborts the qualification itself."""
    from realcog.ph6_brains_2208 import Block
    big, small = _stores(db)
    tag = hashlib.sha256((kind + ":" + item).encode()).hexdigest()[:40]
    bp = big.put(Block("s061-" + tag, LINK_OWNER, "cognitive", canonical(
        {"schema": 1, "kind": "suit_procedure_ref", "item_kind": kind, "item": item, "record_digest": record_digest}),
        source_refs=("int:suit61:" + kind,)), principal=LINK_OWNER)
    sp = small.put(Block("t061-" + tag, LINK_OWNER, "active", canonical(
        {"schema": 1, "kind": "suit_context_route", "item_kind": kind, "item": item, "big_digest": bp.digest,
         "contexts": sorted(context_digests)}), source_refs=("int:" + bp.handle,)), principal=LINK_OWNER)
    db.execute("INSERT OR REPLACE INTO suit61_links VALUES (?,?,?,?,?)",
               (kind, item, canonical(vars(bp)), canonical(vars(sp)), record_digest))


def verify_link(db, kind, item, record_digest, context_digest=None):
    """Handshake used before any qualified plan is released: Suit record == Big49 reference == Small49 route.
    Returns None when intact, otherwise the reason. Missing link, mismatch or storage damage all fail closed."""
    from realcog.ph6_brains_2208 import Pointer
    row = db.execute("SELECT big_pointer,small_pointer,record_digest FROM suit61_links WHERE kind=? AND item=?", (kind, item)).fetchone()
    if row is None:
        return "no Big49/Small49 link for qualified item"
    if row[2] != record_digest:
        return "link does not match current Suit record"
    try:
        big, small = _stores(db)
        bp, sp = Pointer(**json.loads(row[0])), Pointer(**json.loads(row[1]))
        b = json.loads(big.read(bp, principal=LINK_OWNER)["content"])
        r = json.loads(small.read(sp, principal=LINK_OWNER)["content"])
    except Exception as exc:
        return "Big49/Small49 read failed: " + type(exc).__name__
    if (b.get("item"), b.get("item_kind"), b.get("record_digest")) != (item, kind, record_digest):
        return "Big49 reference does not match Suit record"
    if (r.get("item"), r.get("big_digest")) != (item, bp.digest):
        return "Small49 route does not match Big49 reference"
    if context_digest is not None and context_digest not in r.get("contexts", []):
        return "Small49 route does not cover this context"
    return None


# ----------------------------------------------------------------------------- skills
def skill_state(db, skill_id):
    if not db.execute("SELECT 1 FROM suit61_skills WHERE skill_id=?", (skill_id,)).fetchone():
        return None
    if db.execute("SELECT 1 FROM suit61_revoked WHERE kind='skill' AND item=?", (skill_id,)).fetchone():
        return "REVOKED"
    if db.execute("SELECT 1 FROM suit61_skill_qual WHERE skill_id=?", (skill_id,)).fetchone():
        return "QUALIFIED"
    if db.execute("SELECT 1 FROM suit61_practice WHERE skill_id=? AND outcome='SUCCESS'", (skill_id,)).fetchone():
        return "PRACTICED"
    return "KNOWN"


def load_skill(db, skill_id):
    row = db.execute("SELECT record,digest FROM suit61_skills WHERE skill_id=?", (skill_id,)).fetchone()
    if row is None:
        raise KeyError("unknown skill")
    if identity(json.loads(row[0])) != row[1]:
        raise ValueError("skill record digest mismatch")          # fail closed; never use a damaged skill
    return json.loads(row[0])


def learn(db, principal, task):
    skill = C.normalize_skill(task["skill"])
    if db.execute("SELECT 1 FROM suit61_skills WHERE skill_id=?", (skill["skill_id"],)).fetchone():
        raise ValueError("skill identity already recorded (records are immutable; use a new version id)")
    db.execute("INSERT INTO suit61_skills VALUES (?,?,?,?)", (skill["skill_id"], canonical(skill), identity(skill), principal))
    trace(db, principal, "SKILL_KNOWN", {"skill": skill["skill_id"], "digest": identity(skill)})
    return {"skill_id": skill["skill_id"], "state": "KNOWN", "digest": identity(skill)}


def practice(db, principal, task):
    sid = _ident(task["skill_id"])
    load_skill(db, sid)
    if task.get("outcome") not in ("SUCCESS", "FAILURE"):
        raise ValueError("outcome must be SUCCESS or FAILURE")
    ref = _evidence(task.get("evidence_ref"))
    verify_evidence(db, ref, {"kind": "practice", "item": sid, "outcome": task["outcome"]}, producer=principal)
    seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM suit61_practice WHERE skill_id=?", (sid,)).fetchone()[0]
    db.execute("INSERT INTO suit61_practice VALUES (?,?,?,?,?)", (sid, seq, task["outcome"], ref, principal))
    trace(db, principal, "SKILL_PRACTICED", {"skill": sid, "outcome": task["outcome"], "evidence": ref})
    return {"skill_id": sid, "state": skill_state(db, sid), "practice_records": seq}


def qualify_skill(db, principal, task):
    sid = _ident(task["skill_id"])
    skill = load_skill(db, sid)
    rows = db.execute("SELECT outcome,evidence_ref,observer FROM suit61_practice WHERE skill_id=?", (sid,)).fetchall()
    for outcome, ref, observer in rows:
        verify_evidence(db, ref, {"kind": "practice", "item": sid, "outcome": outcome}, producer=observer)
    ok = [r for r in rows if r[0] == "SUCCESS"]
    author = db.execute("SELECT author FROM suit61_skills WHERE skill_id=?", (sid,)).fetchone()[0]
    reasons = []
    if len(ok) < MIN_PRACTICE:
        reasons.append("insufficient successful practice")
    if any(r[0] == "FAILURE" for r in rows):
        reasons.append("unresolved failed practice")
    if principal == author or principal in {r[2] for r in rows}:
        reasons.append("qualifier must be independent of author and observers")
    if reasons:
        trace(db, principal, "SKILL_QUALIFICATION_REFUSED", {"skill": sid, "reasons": reasons})
        return {"skill_id": sid, "state": skill_state(db, sid), "status": "QUALIFICATION_REFUSED", "reasons": reasons}
    crit = {"min_success": MIN_PRACTICE, "successes": len(ok), "evidence": sorted(r[1] for r in ok), "skill": identity(skill)}
    db.execute("INSERT OR REPLACE INTO suit61_skill_qual VALUES (?,?,?)", (sid, principal, canonical(crit)))
    link_memory(db, "skill", sid, identity(skill), [])
    trace(db, principal, "SKILL_QUALIFIED", {"skill": sid, "criteria": identity(crit)})
    return {"skill_id": sid, "state": "QUALIFIED", "memory_link": True}


# ----------------------------------------------------------------------------- transfer
def transfer_state(db, tid):
    row = db.execute("SELECT 1 FROM suit61_transfers WHERE transfer_id=?", (tid,)).fetchone()
    if not row:
        return None
    if db.execute("SELECT 1 FROM suit61_revoked WHERE kind='transfer' AND item=?", (tid,)).fetchone():
        return "REVOKED"
    if db.execute("SELECT 1 FROM suit61_transfer_qual WHERE transfer_id=?", (tid,)).fetchone():
        return "TRANSFER_QUALIFIED"
    if db.execute("SELECT 1 FROM suit61_attempts WHERE transfer_id=?", (tid,)).fetchone():
        return "TRANSFER_TESTED"
    return "TRANSFER_PROPOSED"


def qualified_evidence(db, kind, item):
    table, key, event_kind = (("suit61_skill_qual", "skill_id", "SKILL_QUALIFIED") if kind == "skill"
                              else ("suit61_transfer_qual", "transfer_id", "TRANSFER_QUALIFIED"))
    row = db.execute(f"SELECT qualifier,criteria FROM {table} WHERE {key}=?", (item,)).fetchone()
    if row is None:
        raise ValueError("qualification missing")
    qualifier, raw = row
    criteria = json.loads(raw)
    expected = identity(criteria)
    records = db.execute("SELECT payload FROM suit61_trace WHERE kind=? AND principal=?", (event_kind, qualifier))
    if not any(json.loads(r[0]).get(kind) == item and json.loads(r[0]).get("criteria") == expected for r in records):
        raise ValueError("qualification trace missing or mismatched")
    if kind == "skill":
        practices = db.execute("SELECT outcome,evidence_ref,observer FROM suit61_practice WHERE skill_id=?", (item,)).fetchall()
        if (len(practices) < MIN_PRACTICE or any(r[0] != "SUCCESS" for r in practices)
                or sorted(r[1] for r in practices) != criteria["evidence"]
                or identity(load_skill(db, item)) != criteria["skill"]):
            raise ValueError("practice evidence differs from qualification")
        for outcome, ref, observer in practices:
            verify_evidence(db, ref, {"kind": "practice", "item": item, "outcome": outcome}, producer=observer)
    else:
        attempts = db.execute("SELECT evidence_ref,observer,record,effective FROM suit61_attempts WHERE transfer_id=? ORDER BY seq",
                              (item,)).fetchall()
        if (len(attempts) < MIN_ATTEMPTS or any(r[3] != "SUCCESS" for r in attempts)
                or [r[0] for r in attempts] != criteria["attempts"]):
            raise ValueError("attempt evidence differs from qualification")
        for ref, observer, body, _ in attempts:
            logged = json.loads(body)
            verify_evidence(db, ref, {"kind": "attempt", "item": item, "outcome": logged["reported"],
                            "executed_order": logged["executed_order"], "measured": logged["measured"]}, producer=observer)


def retrieve(db, principal, task):
    """Select a procedure for a target. Familiarity, qualification, permission, safety and evidence stay separate."""
    target = C.normalize_target(task["target"])
    permitted = task.get("permitted", True)
    if type(permitted) is not bool:
        raise ValueError("permitted must be boolean")
    out = {"task_id": task.get("task_id"), "familiarity": "UNFAMILIAR", "qualification": "NONE", "operational_permission":
           "PERMITTED_BY_CALLER" if permitted else "NOT_PERMITTED_BY_CALLER", "execution_authorization": "NOT_GRANTED",
           "authority": "AUTH:0", "safety": {"hazard_level": target["context"]["hazard"]},
           "evidence": "NONE", "context_digest": C.context_digest(target)}
    skills = [r[0] for r in db.execute("SELECT skill_id FROM suit61_skills ORDER BY skill_id")]
    live = [s for s in skills if skill_state(db, s) in ("QUALIFIED", "PRACTICED", "KNOWN")]
    comps = []
    for sid in live:
        skill = load_skill(db, sid)
        comp = C.compare(skill, target, None)
        comps.append((-comp["relevance"], RANK[comp["disposition"]], sid, comp))
    comps.sort(key=lambda x: x[:3])
    # 1. direct reuse of a qualified skill in its own envelope
    for _, rank, sid, comp in comps:
        if comp["disposition"] == "REUSE_CANDIDATE" and skill_state(db, sid) == "QUALIFIED" and comp["shared_primitives"]:
            qualified_evidence(db, "skill", sid)
            bad = verify_link(db, "skill", sid, identity(load_skill(db, sid)))
            if bad:
                out.update(mode="HANDSHAKE_FAILED", skill_id=sid, reason=bad)
                return _finish(db, principal, out, None)
            out.update(mode="REUSE_QUALIFIED_SKILL", familiarity="FAMILIAR", qualification="SKILL_QUALIFIED",
                       evidence="PRACTICE_RECORDS", skill_id=sid, plan={"steps": load_skill(db, sid)["steps"], "limits": {}})
            return _finish(db, principal, out, comp)
    # 2. qualified transfer covering this target
    if task.get("transfer_enabled", True):
        for row in db.execute("SELECT transfer_id,target,plan,skill_id FROM suit61_transfers ORDER BY transfer_id"):
            if skill_state(db, row[3]) == "QUALIFIED" and transfer_state(db, row[0]) == "TRANSFER_QUALIFIED" and C.covers(json.loads(row[1]), target):
                qualified_evidence(db, "skill", row[3])
                qualified_evidence(db, "transfer", row[0])
                crow = db.execute("SELECT comparison,target,plan,skill_id FROM suit61_transfers WHERE transfer_id=?", (row[0],)).fetchone()
                bad = verify_link(db, "transfer", row[0], transfer_digest(crow), C.context_digest(json.loads(row[1])))
                if bad:
                    out.update(mode="HANDSHAKE_FAILED", skill_id=row[3], transfer_id=row[0], reason=bad)
                    return _finish(db, principal, out, None)
                out.update(mode="REUSE_QUALIFIED_TRANSFER", familiarity="TRANSFERRED", qualification="TRANSFER_QUALIFIED",
                           evidence="TRANSFER_ATTEMPTS", transfer_id=row[0], skill_id=row[3], plan=json.loads(row[2]))
                return _finish(db, principal, out, None)
        # 2b. a recorded negative transfer in the same context blocks automatic re-proposal
        prior = [r for r in db.execute("SELECT transfer_id FROM suit61_transfers WHERE context=?", (out["context_digest"],))
                 if db.execute("SELECT 1 FROM suit61_attempts WHERE transfer_id=? AND effective='FAILURE'", (r[0],)).fetchone()]
        if prior:
            out.update(mode="DO_NOT_TRANSFER", reason="failed transfer retained for this context", transfer_id=prior[0][0])
            return _finish(db, principal, out, None)
    # 3. candidate comparison
    if not task.get("transfer_enabled", True):
        out.update(mode="UNFAMILIAR_REDISCOVERY_REQUIRED", reason="cross-domain transfer disabled")
        return _finish(db, principal, out, None)
    usable = [c for c in comps if c[3]["shared_primitives"]]
    if not usable:
        out.update(mode="UNFAMILIAR_REDISCOVERY_REQUIRED", reason="no practiced primitive in common; not a prohibition")
        return _finish(db, principal, out, None)
    _, rank, sid, comp = usable[0]
    if comp["disposition"] == "DO_NOT_TRANSFER":
        out.update(mode="DO_NOT_TRANSFER", familiarity="SIMILAR_BUT_INCOMPATIBLE", skill_id=sid,
                   reason="; ".join(e["name"] + ": " + e["why"] for e in comp["elements"] if e["state"] == C.I))
    else:
        out.update(mode="PROPOSE_TRANSFER", familiarity="PARTIALLY_FAMILIAR", skill_id=sid, evidence="STRUCTURAL_COMPARISON_ONLY",
                   needs_review=any(e["kind"] == "ordering" and e["state"] == C.U for e in comp["elements"]))
    return _finish(db, principal, out, comp)


def _finish(db, principal, out, comp):
    if comp is not None:
        out["comparison"] = comp
    event = {"mode": out["mode"], "skill": out.get("skill_id"), "context": out["context_digest"],
             "skill_digest": identity(load_skill(db, out["skill_id"])) if out.get("skill_id") else None,
             "transfer": out.get("transfer_id"), "plan_digest": identity(out["plan"]) if "plan" in out else None,
             "qualification": out["qualification"], "permission": out["operational_permission"],
             "task_id": out.get("task_id")}
    seq = trace(db, principal, "RETRIEVE", event)
    out["receipt"] = {"seq": seq, "context_digest": out["context_digest"],
                      "skill_id": event["skill"], "skill_digest": event["skill_digest"], "event_digest": identity(event)}
    return out


def _check_receipt(db, principal, receipt, context, skill_id):
    """A proposal requires a successful, same-principal, same-context, same-version retrieval."""
    if not isinstance(receipt, dict) or type(receipt.get("seq")) is not int or receipt["seq"] <= 0:
        raise ValueError("valid retrieval receipt required")
    row = db.execute("SELECT principal,kind,payload FROM suit61_trace WHERE seq=?", (receipt["seq"],)).fetchone()
    if row is None or row[1] != "RETRIEVE" or row[0] != principal:
        raise ValueError("retrieval receipt principal or event mismatch")
    event = json.loads(row[2])
    digest = identity(load_skill(db, skill_id))
    if (event.get("mode") not in ("PROPOSE_TRANSFER", "DO_NOT_TRANSFER") or receipt.get("event_digest") != identity(event) or event.get("context") != context
            or event.get("skill") != skill_id or event.get("skill_digest") != digest
            or receipt.get("context_digest") != context or receipt.get("skill_id") != skill_id
            or receipt.get("skill_digest") != digest):
        raise ValueError("retrieval receipt does not bind successful proposal candidate")


def propose(db, principal, task):
    sid = _ident(task["skill_id"])
    skill = load_skill(db, sid)
    target = C.normalize_target(task["target"])
    review = task.get("ordering_review") or {}
    if not isinstance(review, dict) or review.get("result") not in (None, "CONSISTENT", "VIOLATED", "NEED_INFORMATION"):
        raise ValueError("invalid ordering review")
    ordering = review.get("result")
    _check_receipt(db, principal, task.get("retrieval_receipt"), C.context_digest(target), sid)
    comp = C.compare(skill, target, ordering)
    tid = identity([skill["skill_id"], identity(skill), C.context_digest(target)])
    exists = transfer_state(db, tid)
    if exists in ("TRANSFER_TESTED", "TRANSFER_QUALIFIED", "REVOKED"):
        return {"transfer_id": tid, "state": exists, "comparison": comp, "note": "existing record preserved"}
    plan = C.adapted_plan(skill, target, comp)
    row = (tid, sid, canonical(target), C.context_digest(target), canonical(comp), canonical(plan), principal,
           canonical({"ordering": ordering, "invocation": review.get("invocation")}))
    db.execute("INSERT OR REPLACE INTO suit61_transfers VALUES (?,?,?,?,?,?,?,?)", row)
    trace(db, principal, "TRANSFER_PROPOSED", {"transfer": tid, "disposition": comp["disposition"],
                                               "counts": comp["counts"], "ordering_review": ordering})
    return {"transfer_id": tid, "state": "TRANSFER_PROPOSED", "disposition": comp["disposition"], "comparison": comp, "plan": plan}


def attempt(db, principal, task):
    tid = task["transfer_id"]
    state = transfer_state(db, tid)
    if state not in ("TRANSFER_PROPOSED", "TRANSFER_TESTED"):
        raise ValueError("attempts are recorded only against a proposed or tested transfer")
    trow = db.execute("SELECT target,comparison FROM suit61_transfers WHERE transfer_id=?", (tid,)).fetchone()
    target, comp = json.loads(trow[0]), json.loads(trow[1])
    if comp["disposition"] == "DO_NOT_TRANSFER":
        raise PermissionError("incompatible transfer cannot be tested")
    if task.get("outcome") not in ("SUCCESS", "FAILURE"):
        raise ValueError("outcome must be SUCCESS or FAILURE")
    ref = _evidence(task.get("evidence_ref"))
    order = task.get("executed_order", [])
    measured = C._ints(task.get("measured", {}), "measured")
    if not isinstance(order, list) or any(v not in C.VERBS for v in order):
        raise ValueError("invalid executed order")
    problems = []
    if target["precedence"] and not C.satisfies(order, target["precedence"]):
        problems.append("executed order violates precedence")
    for name, c in target["context"]["numeric"].items():
        v = measured.get(name)
        if v is None:
            problems.append("missing measurement " + name)
        elif (v > c["value"]) if c["sense"] == "max" else (v < c["value"]):
            problems.append("measurement outside limit " + name)
    if not set(target["required_verbs"]) <= set(order):
        problems.append("required primitive not executed")
    verify_evidence(db, ref, {"kind": "attempt", "item": tid, "outcome": task["outcome"],
                              "executed_order": order, "measured": measured}, producer=principal)
    effective = "SUCCESS" if task["outcome"] == "SUCCESS" and not problems else "FAILURE"
    seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM suit61_attempts WHERE transfer_id=?", (tid,)).fetchone()[0]
    rec = {"reported": task["outcome"], "executed_order": order, "measured": measured, "problems": problems}
    db.execute("INSERT INTO suit61_attempts VALUES (?,?,?,?,?,?,?)", (tid, seq, task["outcome"], effective, ref, principal, canonical(rec)))
    trace(db, principal, "TRANSFER_ATTEMPT", {"transfer": tid, "reported": task["outcome"], "effective": effective, "evidence": ref,
                                             "problems": problems})
    return {"transfer_id": tid, "state": "TRANSFER_TESTED", "effective_outcome": effective, "problems": problems, "attempts": seq}


def qualify_transfer(db, principal, task):
    tid = task["transfer_id"]
    state = transfer_state(db, tid)
    if state is None:
        raise KeyError("unknown transfer")
    trow = db.execute("SELECT comparison,proposer FROM suit61_transfers WHERE transfer_id=?", (tid,)).fetchone()
    comp, proposer = json.loads(trow[0]), trow[1]
    rows = db.execute("SELECT effective,evidence_ref,observer FROM suit61_attempts WHERE transfer_id=? ORDER BY seq", (tid,)).fetchall()
    for ref, reporter, raw in db.execute("SELECT evidence_ref,observer,record FROM suit61_attempts WHERE transfer_id=?", (tid,)):
        attempt_row = json.loads(raw)
        verify_evidence(db, ref, {"kind": "attempt", "item": tid, "outcome": attempt_row["reported"],
                        "executed_order": attempt_row["executed_order"], "measured": attempt_row["measured"]}, producer=reporter)
    why = []
    if state != "TRANSFER_TESTED":
        why.append("transfer is " + str(state) + ", not TRANSFER_TESTED")
    if comp["disposition"] in ("DO_NOT_TRANSFER", "TRANSFER_PROPOSED_NEEDS_EVIDENCE"):
        why.append("comparison has " + ("incompatible" if comp["disposition"].startswith("DO") else "unsupported") + " elements")
    if sum(1 for r in rows if r[0] == "SUCCESS") < MIN_ATTEMPTS:
        why.append("fewer than %d effective successful attempts" % MIN_ATTEMPTS)
    if any(r[0] == "FAILURE" for r in rows):
        why.append("a failed attempt is retained for this context")
    if principal == proposer or principal in {r[2] for r in rows}:
        why.append("qualifier must be independent of proposer and observers")
    if why:
        trace(db, principal, "TRANSFER_QUALIFICATION_REFUSED", {"transfer": tid, "reasons": why})
        return {"transfer_id": tid, "state": state, "status": "QUALIFICATION_REFUSED", "reasons": why}
    crit = {"min_attempts": MIN_ATTEMPTS, "attempts": [r[1] for r in rows], "comparison": identity(comp)}
    db.execute("INSERT INTO suit61_transfer_qual VALUES (?,?,?)", (tid, principal, canonical(crit)))
    ctx = db.execute("SELECT context FROM suit61_transfers WHERE transfer_id=?", (tid,)).fetchone()[0]
    record = db.execute("SELECT comparison,target,plan,skill_id FROM suit61_transfers WHERE transfer_id=?", (tid,)).fetchone()
    link_memory(db, "transfer", tid, transfer_digest(record), [ctx])
    trace(db, principal, "TRANSFER_QUALIFIED", {"transfer": tid, "criteria": identity(crit)})
    return {"transfer_id": tid, "state": "TRANSFER_QUALIFIED", "memory_link": True}


def revoke(db, principal, task):
    kind, item = task.get("kind"), task.get("item")
    if kind not in ("skill", "transfer", "evidence") or not isinstance(item, str):
        raise ValueError("kind must be skill or transfer")
    db.execute("INSERT OR IGNORE INTO suit61_revoked VALUES (?,?,?)", (kind, item, str(task.get("reason", ""))[:200]))
    trace(db, principal, "REVOKED", {"kind": kind, "item": item})
    return {"kind": kind, "item": item, "state": "REVOKED"}


# ----------------------------------------------------------------------------- task continuity
def _task(db, principal, tid):
    row = db.execute("SELECT revision,state FROM suit61_tasks WHERE principal=? AND task_id=?", (principal, tid)).fetchone()
    if row is None:
        return None, None
    state = json.loads(row[1])
    events = db.execute("SELECT payload FROM suit61_trace WHERE principal=? AND kind IN ('TASK_BEGIN','TASK_STEP_ACK') ORDER BY seq DESC",
                        (principal,))
    for (payload,) in events:
        event = json.loads(payload)
        if event.get("task") == tid:
            if event.get("state_digest") == identity([row[0], state]):
                return row[0], state
            break
    raise PermissionError("task ledger has no matching committed transition")


def begin(db, principal, task):
    tid = _ident(task["task_id"])
    if _task(db, principal, tid)[0] is not None:
        raise ValueError("task exists")
    if set(task) - {"authority", "operation", "task_id", "target", "skill_id", "transfer_id", "retrieval_receipt"}:
        raise ValueError("caller procedure overrides are forbidden")
    target = C.normalize_target(task["target"])
    receipt = task.get("retrieval_receipt", {})
    if not isinstance(receipt, dict) or type(receipt.get("seq")) is not int:
        raise ValueError("retrieval receipt required")
    row = db.execute("SELECT principal,kind,payload FROM suit61_trace WHERE seq=?", (receipt["seq"],)).fetchone()
    if not row or row[:2] != (principal, "RETRIEVE"):
        raise ValueError("retrieval receipt identity mismatch")
    event = json.loads(row[2])
    if (receipt != {"seq": receipt["seq"], "context_digest": event.get("context"),
                    "skill_id": event.get("skill"), "skill_digest": event.get("skill_digest"),
                    "event_digest": identity(event)} or event.get("task_id") != tid
            or event.get("context") != C.context_digest(target) or not verify_trace(db)["intact"]):
        raise ValueError("retrieval receipt context or integrity mismatch")
    sid, transfer = task.get("skill_id"), task.get("transfer_id")
    if event["skill"] != sid or event["transfer"] != transfer:
        raise ValueError("retrieval receipt procedure mismatch")
    if event["permission"] != "PERMITTED_BY_CALLER" or target["context"]["hazard"] > 1:
        trace(db, principal, "TASK_GOVERNANCE_HOLD", {"task": tid, "receipt": receipt["seq"]})
        return {"status": "GOVERNANCE_HOLD", "execution_authorization": "NOT_GRANTED"}
    selected = retrieve(db, principal, {"task_id": tid, "target": target})
    if (selected["mode"] not in ("REUSE_QUALIFIED_SKILL", "REUSE_QUALIFIED_TRANSFER")
            or selected["mode"] != event["mode"] or selected["qualification"] != event["qualification"]
            or selected.get("skill_id") != sid or selected.get("transfer_id") != transfer
            or identity(selected["plan"]) != event["plan_digest"]
            or identity(load_skill(db, sid)) != event["skill_digest"]):
        raise PermissionError("trusted procedure unavailable or changed")
    plan = selected["plan"]
    steps = [step["verb"] for step in plan["steps"]]
    if not steps or not C.satisfies(steps, target["precedence"]):
        raise PermissionError("plan fails required ordering")
    missing = sorted(set(target["context"]["preconditions"]))
    status = "PREREQUISITE_HOLD" if missing else "READY"
    state = {"task_id": tid, "steps": steps, "limits": plan.get("limits", {}), "step": 0,
             "completed": [], "status": status, "missing": missing, "source": sid,
             "transfer_id": transfer, "mode": selected["mode"], "admission_receipt": receipt["seq"],
             "authority": "AUTH:0", "execution_authorization": "NOT_GRANTED"}
    db.execute("INSERT INTO suit61_tasks VALUES (?,?,?,?)", (principal, tid, 1, canonical(state)))
    trace(db, principal, "TASK_BEGIN", {"task": tid, "status": status, "mode": state["mode"], "receipt": receipt["seq"], "state_digest": identity([1, state])})
    return dict(state, revision=1)


def next_step(db, principal, task):
    rev, st = _task(db, principal, _ident(task["task_id"]))
    if st is None:
        raise KeyError("unknown task")
    if skill_state(db, st["source"]) != "QUALIFIED" or (
            st["transfer_id"] and transfer_state(db, st["transfer_id"]) != "TRANSFER_QUALIFIED"):
        return {"status": "GOVERNANCE_HOLD", "step": None}
    if st["status"] not in ("READY", "ACTIVE"):
        return {"status": st["status"], "step": None, "missing": st["missing"]}
    if st["step"] >= len(st["steps"]):
        return {"status": "COMPLETE", "step": None}
    return {"status": "STEP_PROPOSED", "step": st["steps"][st["step"]], "index": st["step"], "revision": rev,
            "limits": st["limits"], "execution_authorization": "NOT_GRANTED"}


def ack(db, principal, task, fault=None):
    tid = _ident(task["task_id"])
    rev, st = _task(db, principal, tid)
    if st is None:
        raise KeyError("unknown task")
    ref = _evidence(task.get("evidence_ref"))
    idx = task.get("index")
    if st["status"] not in ("READY", "ACTIVE", "COMPLETE"):
        raise ValueError("task not active")
    if type(idx) is not int or idx < 0:
        raise ValueError("step index required")
    prior = [c for c in st["completed"] if c["index"] == idx]
    if prior:                                                # duplicate delivery: same receipt, no second advance
        if prior[0]["evidence_ref"] != ref:
            raise ValueError("step already completed with different evidence")
        return dict(st, revision=rev, duplicate=True)
    if idx != st["step"]:
        raise ValueError("step index conflict")
    if idx >= len(st["steps"]):
        raise ValueError("task has no remaining step")
    if not st.get("admission_receipt") or skill_state(db, st["source"]) != "QUALIFIED" or (
            st["transfer_id"] and transfer_state(db, st["transfer_id"]) != "TRANSFER_QUALIFIED"):
        raise PermissionError("active qualification and trusted admission required")
    verify_evidence(db, ref, {"kind": "step", "task": tid, "principal": principal,
                             "index": idx, "verb": st["steps"][idx], "outcome": "SUCCESS"},
                    independent_of=principal)
    st["completed"].append({"index": idx, "verb": st["steps"][idx], "evidence_ref": ref})
    st["step"] += 1
    st["status"] = "COMPLETE" if st["step"] == len(st["steps"]) else "ACTIVE"
    if fault:
        fault("before_suit61_ack_commit")
    db.execute("UPDATE suit61_tasks SET revision=?, state=? WHERE principal=? AND task_id=?", (rev + 1, canonical(st), principal, tid))
    trace(db, principal, "TASK_STEP_ACK", {"task": tid, "index": idx, "evidence": ref, "status": st["status"], "state_digest": identity([rev + 1, st])})
    return dict(st, revision=rev + 1)


def resume(db, principal, task):
    tid = _ident(task["task_id"])
    rev, st = _task(db, principal, tid)
    if st is None:
        raise KeyError("unknown task")
    trace(db, principal, "TASK_RESUME", {"task": tid, "step": st["step"]})
    return dict(st, revision=rev)


def show(db, principal, task):
    kind = task.get("kind", "skill")
    if kind == "skill":
        sid = _ident(task["item"])
        return {"skill_id": sid, "state": skill_state(db, sid), "record": load_skill(db, sid)}
    if kind == "transfer":
        state = transfer_state(db, task["item"])
        if state is None:
            raise KeyError("unknown transfer")
        row = db.execute("SELECT comparison,plan FROM suit61_transfers WHERE transfer_id=?", (task["item"],)).fetchone()
        atts = [json.loads(r[0]) | {"effective": r[1], "evidence_ref": r[2]} for r in db.execute(
            "SELECT record,effective,evidence_ref FROM suit61_attempts WHERE transfer_id=? ORDER BY seq", (task["item"],))]
        return {"transfer_id": task["item"], "state": state, "comparison": json.loads(row[0]), "plan": json.loads(row[1]), "attempts": atts}
    if kind == "trace":
        return verify_trace(db)
    raise ValueError("unknown kind")


HANDLERS = {"suit_observe": observe, "suit_learn": learn, "suit_practice": practice, "suit_qualify_skill": qualify_skill, "suit_retrieve": retrieve,
            "suit_propose": propose, "suit_attempt": attempt, "suit_qualify_transfer": qualify_transfer, "suit_revoke": revoke,
            "suit_begin": begin, "suit_next": next_step, "suit_resume": resume, "suit_show": show}
