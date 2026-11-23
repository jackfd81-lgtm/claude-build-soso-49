"""RealCog procedural records in the observation store's SQLite transaction."""
import hashlib
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def configure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS procedures_023 (
        id TEXT PRIMARY KEY, family TEXT NOT NULL, version INTEGER NOT NULL,
        record TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS procedure_family_023 ON procedures_023(family)")
    db.execute("""CREATE TABLE IF NOT EXISTS executions_023 (
        id TEXT PRIMARY KEY, task_digest TEXT NOT NULL, output TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS feedback_023 (
        execution_id TEXT NOT NULL, method_id TEXT NOT NULL, outcome TEXT NOT NULL,
        PRIMARY KEY (execution_id, method_id))""")


def remember(db, record):
    return db.execute("INSERT OR IGNORE INTO procedures_023 VALUES (?, ?, ?, ?)",
                      (record["id"], record["family"], record["version"], canonical(record))).rowcount


def recall(db, family):
    rows = db.execute("""SELECT id, version, record FROM procedures_023
                         WHERE family=? ORDER BY id LIMIT 128""", (family,)).fetchall()
    records = []
    for method_id, version, raw in rows:
        try:
            record = json.loads(raw)
            material = {k: v for k, v in record.items() if k != "id"}
            if record["id"] == method_id == identity(material) and record["version"] == version:
                records.append(record)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return records, len(rows)
