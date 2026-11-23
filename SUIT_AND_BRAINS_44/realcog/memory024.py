"""Qualified Big49 procedure references and Small49 context records."""
import json

from contract.cognitive_domains_023 import FAMILIES, applicable, execute, parse
from contract.cognitive_pathway_023 import _signature, _strategy
from realcog.ph6_brains_2208 import BIG, SMALL, Big49, Small49, Block, Pointer, Rejected
from realcog.procedural_memory import canonical, identity

OWNER = "L15"
SCHEMA = 1


def configure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pilot024_schema (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)""")
    db.execute("INSERT OR IGNORE INTO pilot024_schema VALUES (1,?)", (SCHEMA,))
    if db.execute("SELECT version FROM pilot024_schema WHERE singleton=1").fetchone() != (SCHEMA,):
        raise ValueError("unsupported pilot schema")
    db.execute("""CREATE TABLE IF NOT EXISTS qualifications_024 (
        method TEXT PRIMARY KEY, family TEXT NOT NULL, context TEXT NOT NULL,
        big_pointer TEXT NOT NULL, small_pointer TEXT NOT NULL, evidence TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS qualified_context_024 ON qualifications_024(family,context)")
    db.execute("""CREATE TABLE IF NOT EXISTS revoked_024 (
        method TEXT PRIMARY KEY, reason TEXT NOT NULL)""")
    # MUSE-025/CLAUDE-010: forensic record of procedures excluded for an integrity failure.
    # The damaged procedure row itself is never altered or deleted.
    db.execute("""CREATE TABLE IF NOT EXISTS quarantine_024 (
        method TEXT PRIMARY KEY, reason TEXT NOT NULL, stored_digest TEXT NOT NULL)""")


def load_method(db, method):
    row = db.execute("SELECT record FROM procedures_023 WHERE id=?", (method,)).fetchone()
    if row is None:
        raise ValueError("unknown method")
    record = json.loads(row[0])
    if record.get("id") != method or identity({k: v for k, v in record.items() if k != "id"}) != method:
        raise ValueError("method digest mismatch")
    return record


def qualify(db, method, fixtures, *, qualifier, fault=None, scale_cases=()):
    configure(db)
    record = load_method(db, method)
    family, signature = record["family"], record["precondition"]
    strategy = _strategy(record, family, signature)
    if strategy is None:
        raise ValueError("strategy is ineligible")
    existing = db.execute("SELECT big_pointer,small_pointer,evidence FROM qualifications_024 WHERE method=?",
                          (method,)).fetchone()
    if existing:
        return {"big": json.loads(existing[0]), "small": json.loads(existing[1]),
                "evidence": json.loads(existing[2]), "replayed": True, "memory": []}
    checked = []
    for case in fixtures:
        case_family, parsed = parse(case["task"])
        if (case_family, _signature(case_family, parsed)) != (family, signature):
            continue
        if not applicable(strategy, parsed):
            raise ValueError("qualification precondition mismatch")
        result, _ = execute(family, strategy, parsed)
        if any(result.get(key) != expected for key, expected in case["expected"].items()):
            raise ValueError("procedure failed independent qualification")
        checked.append(case["id"])
    if not checked:
        raise ValueError("no independent qualification case matches procedure")
    evidence = {"qualifier": qualifier, "suite": identity(fixtures), "cases": checked,
                "training": record["training_evidence"]}
    if scale_cases:
        scale = scale_profile(family, signature, strategy, scale_cases)
        if scale:
            evidence["scale"] = scale
            evidence["scale_suite"] = identity(scale_cases)
    big = Big49(connection=db)
    small = Small49(connection=db)
    big_record = {"schema": SCHEMA, "kind": "qualified_strategy", "method": method,
                  "operation": strategy, "evidence": identity(evidence)}
    bp = big.put(Block("p024-" + method, OWNER, "cognitive", canonical(big_record),
                       source_refs=("int:procedure:" + method, *("int:observation:" + x for x in
                                    record["training_evidence"]["observations"][:16]))), principal=OWNER)
    if fault:
        fault("after_big")
    small_record = {"schema": SCHEMA, "kind": "context_route", "family": family, "signature": signature,
                    "big_digest": bp.digest, "method": method}
    sp = small.put(Block("c024-" + method, OWNER, "active", canonical(small_record),
                         source_refs=("int:" + bp.handle,)), principal=OWNER)
    db.execute("INSERT INTO qualifications_024 VALUES (?,?,?,?,?,?)",
               (method, family, signature, canonical(vars(bp)), canonical(vars(sp)), canonical(evidence)))
    return {"big": vars(bp), "small": vars(sp), "evidence": evidence, "replayed": False,
            "memory": [{"store": p.pool, "record_id": p.record_id, "digest": p.digest,
                        "operation": "write"} for p in (bp, sp)]}


SCALE_TOLERANCE = (5, 4)     # a strategy may cost at most 5/4 of the cheapest alternative at scale


def scale_profile(family, signature, strategy, scale_cases):
    """Cost of the remembered strategy against every applicable alternative on larger
    qualification inputs. Training cost is measured on one (often tiny) task and is not
    transferable across input sizes, so a strategy that is dominated at scale is refused."""
    rows = []
    for case in scale_cases:
        case_family, parsed = parse(case["task"])
        if (case_family, _signature(case_family, parsed)) != (family, signature):
            continue
        costs = {name: execute(family, name, parsed)[1] for name in FAMILIES[family] if applicable(name, parsed)}
        if strategy not in costs:
            raise ValueError("qualification precondition mismatch")
        best = min(costs.values())
        if costs[strategy] * SCALE_TOLERANCE[1] > best * SCALE_TOLERANCE[0]:
            raise ValueError("strategy dominated at scale")
        rows.append({"case": case["id"], "size": case["size"], "cost": costs[strategy], "best": best})
    return rows


class CognitiveMemory:
    """U08 resolves independently qualified contextual references inside its transaction."""
    def __init__(self, unavailable=()):
        self.unavailable = set(unavailable)
        self.events = []
        self.contributors = {}
        self.integrity = []

    def recall(self, db, family, signature):
        configure(db)
        if "small" in self.unavailable:
            self.events.append({"store": "small", "operation": "unavailable"})
            return [], 0
        rows = db.execute("""SELECT method,big_pointer,small_pointer,evidence FROM qualifications_024 q
                             WHERE family=? AND context=? AND NOT EXISTS
                             (SELECT 1 FROM revoked_024 r WHERE r.method=q.method)
                             ORDER BY method""", (family, signature)).fetchall()
        small = Small49(connection=db)
        big = Big49(connection=db) if "big" not in self.unavailable else None
        records = []
        for method, bp, sp, evidence in rows:
            held = db.execute("SELECT reason FROM quarantine_024 WHERE method=?", (method,)).fetchone()
            if held:
                self.integrity.append({"method": method, "reason": held[0], "status": "QUARANTINED", "first_seen": False})
                continue
            bp, sp = Pointer(**json.loads(bp)), Pointer(**json.loads(sp))
            route = self.read(small, sp)
            if (route.get("schema"), route.get("kind"), route.get("family"), route.get("signature"),
                route.get("method"), route.get("big_digest")) != (SCHEMA, "context_route", family,
                                                                 signature, method, bp.digest):
                continue
            if big is None:
                self.events.append({"store": "big", "operation": "unavailable", "record_id": bp.record_id})
                continue
            strategy = self.read(big, bp)          # block-level hash failure (Rejected) stays fail-closed
            try:
                record = load_method(db, method)
            except ValueError as exc:
                # A damaged PROCEDURE row is isolated: this method is excluded and evidence kept; the
                # caller's trusted no-memory algorithms still run. Never silently used, always reported.
                self.quarantine(db, method, str(exc))
                continue
            if (strategy.get("schema"), strategy.get("kind"), strategy.get("method"),
                strategy.get("evidence")) != (SCHEMA, "qualified_strategy", method, identity(json.loads(evidence))):
                continue
            # The executed operation comes from the Big49 record. Its
            # eligibility is separately checked against the durable procedure.
            operation = strategy.get("operation")
            if operation != _strategy(record, family, signature):
                continue
            extra = {}
            scale = json.loads(evidence).get("scale")
            if scale:                                # comparable across methods: same qualification inputs
                extra["qualified_cost"] = sum(row["cost"] for row in scale)
            records.append({**record, "operation": operation, **extra})
            self.contributors[method] = [sp.record_id, bp.record_id]
        return records, len(rows)

    def quarantine(self, db, method, reason):
        row = db.execute("SELECT record FROM procedures_023 WHERE id=?", (method,)).fetchone()
        stored = identity(json.loads(row[0])) if row else "missing"
        db.execute("INSERT OR IGNORE INTO quarantine_024 VALUES (?,?,?)", (method, reason, stored))
        self.integrity.append({"method": method, "reason": reason, "status": "QUARANTINED", "first_seen": True,
                               "stored_digest": stored})

    def read(self, store, pointer):
        value = json.loads(store.read(pointer, principal=OWNER)["content"])
        self.events.append({"function_id": "U08", "store": pointer.pool, "operation": "read",
                            "record_id": pointer.record_id, "owner": pointer.owner, "digest": pointer.digest})
        return value

    def finish(self, result, chosen, task_id):
        result["memory_accesses"] = self.events
        result["contributing_blocks"] = self.contributors.get(chosen["id"], []) if chosen else []
        result["observation_epistemic_class"] = "PRESENTED"
        result["request_id"] = task_id
        if self.integrity:
            result["memory_integrity"] = self.integrity


def utilization(db):
    output = {}
    for store, table, limits in (("big", Big49, BIG), ("small", Small49, SMALL)):
        pool = table(connection=db)
        counts = {bucket: pool.count(bucket) for bucket in limits}
        capacity = sum(limit[0] for limit in limits.values())
        population = sum(counts.values())
        output[store] = {"configured": capacity, "addressable_by_pointer": population,
                         "allocated": population, "populated": population, "by_bucket": counts,
                         "unallocated": capacity - population, "invalid_observed": 0}
    return output


def write_block(db, pool, owner, record_id, bucket, content):
    if isinstance(record_id, str) and record_id.startswith(("p024-", "c024-", "p025-", "c025-", "vb025-", "vs025-")):
        raise PermissionError("qualified block namespace is reserved")
    store = {"big": Big49, "small": Small49}[pool](connection=db)
    return vars(store.put(Block(record_id, owner, bucket, content), principal=owner))
