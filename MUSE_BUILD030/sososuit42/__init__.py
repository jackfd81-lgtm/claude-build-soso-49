"""Deterministic advisory governance for SoSoSuit 4.2."""
import hashlib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

VERSION = "soso-suit/4.2.0"
AUTHORITY = {"authority": "AUTH:0", "execution_authorization": "NOT_GRANTED"}
ACTIONS = frozenset({"REQUEST_READING", "REQUEST_DISCRIMINATOR", "LOG_ADVISORY", "NO_OP"})
ADVISORY = ACTIONS - {"REQUEST_DISCRIMINATOR"}
IOU_TYPES = frozenset({"READING", "QUESTION", "REQUEST", "PERMISSION", "CONFIRMATION"})
IOU_CODES = frozenset({"IOU_UNMAPPED_OBSERVATION", "IOU_MISSING_DISCRIMINATOR",
                       "IOU_SOURCE_UNAVAILABLE", "IOU_AMBIGUOUS_READING"})
BUILD_ID = "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MAX_BYTES = 256 * 1024
MAX_ITEMS = 512
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class Invalid(ValueError):
    """Input cannot enter the governance pipeline."""


def canonical(value):
    """Private 4.2 profile: NFC, UTF-16 key order, signed int64, no floats."""
    def normalize(item, depth):
        if depth > 24:
            raise Invalid("DEPTH_LIMIT")
        if type(item) in (type(None), bool):
            return item
        if type(item) is int and -(2**63) <= item < 2**63:
            return item
        if type(item) is str:
            if len(item) > MAX_BYTES or any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise Invalid("INVALID_STRING")
            return unicodedata.normalize("NFC", item)
        if type(item) is list and len(item) <= MAX_ITEMS:
            return [normalize(child, depth + 1) for child in item]
        if type(item) is dict and len(item) <= MAX_ITEMS:
            normalized = {}
            for key, child in item.items():
                if type(key) is not str:
                    raise Invalid("INVALID_KEY")
                key = normalize(key, depth + 1)
                if key in normalized:
                    raise Invalid("DUPLICATE_KEY")
                normalized[key] = normalize(child, depth + 1)
            return dict(sorted(normalized.items(), key=lambda pair: pair[0].encode("utf-16-be")))
        raise Invalid("UNSUPPORTED_VALUE")
    data = json.dumps(normalize(value, 0), separators=(",", ":"), ensure_ascii=False).encode()
    if len(data) > MAX_BYTES:
        raise Invalid("BYTE_LIMIT")
    return data


def parse(data):
    if len(data) > MAX_BYTES:
        raise Invalid("BYTE_LIMIT")

    def pairs(items):
        result = dict(items)
        if len(result) != len(items):
            raise Invalid("DUPLICATE_KEY")
        return result

    def invalid_number(_):
        raise Invalid("INVALID_NUMBER")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                           parse_float=invalid_number, parse_constant=invalid_number)
        return json.loads(canonical(value))
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, Invalid):
            raise
        raise Invalid("INVALID_JSON") from exc


def digest(value):
    data = canonical(value)
    return {"blake2b256": "blake2b256:" + hashlib.blake2b(data, digest_size=32).hexdigest(),
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest()}


def _identifier(value, prefix=""):
    if not isinstance(value, str) or not value.startswith(prefix) or not IDENTIFIER.fullmatch(value[len(prefix):]):
        raise Invalid("INVALID_IDENTIFIER")


def _fields(value, fields):
    if type(value) is not dict or set(value) != set(fields.split()):
        raise Invalid("INVALID_FIELDS")


def _strings(value):
    if type(value) is not list or any(type(item) is not str for item in value):
        raise Invalid("INVALID_LIST")
    if len(set(value)) != len(value):
        raise Invalid("DUPLICATE_LIST_ENTRY")


def _state(context):
    _fields(context, "task_id principal allowed_actions evidence")
    _identifier(context["task_id"])
    _identifier(context["principal"])
    actions = context["allowed_actions"]
    _strings(actions)
    if "NO_OP" not in actions or not set(actions) <= ACTIONS:
        raise Invalid("UNSATISFIABLE_ACTION_POLICY")
    evidence = context["evidence"]
    if type(evidence) is not dict:
        raise Invalid("INVALID_EVIDENCE")
    for ref in evidence:
        _identifier(ref, "ext:")
    return {"protocol": VERSION, **AUTHORITY, "producer": "GOVERNOR",
            "build_id": BUILD_ID, "canonical_profile": "sososuit42-canonical/1.0",
            "task_id": context["task_id"], "principal": context["principal"],
            "allowed_actions": sorted(actions),
            "evidence": {key: digest(value) for key, value in evidence.items()}}


def govern_context(context):
    """Commit trusted host context before soliciting a candidate."""
    return _state(parse(canonical(context)))


def _evaluate(state, candidate):
    _fields(candidate, "producer candidate_id governed_state_digest actions claims ious")
    if candidate["producer"] != "AI":
        raise Invalid("PRODUCER_MISMATCH")
    _identifier(candidate["candidate_id"])
    if candidate["governed_state_digest"] != digest(state):
        raise Invalid("STATE_BINDING_MISMATCH")
    actions = candidate["actions"]
    _strings(actions)
    if not actions or not set(actions) <= ACTIONS:
        raise Invalid("UNREGISTERED_CODE")
    if not set(actions) <= ADVISORY:
        raise Invalid("NON_ADVISORY_ACTION")
    if not set(actions) <= set(state["allowed_actions"]):
        raise Invalid("ACTION_NOT_ALLOWED")
    if "NO_OP" in actions and len(actions) != 1:
        raise Invalid("NO_OP_CONFLICT")

    # Forward references are prohibited, so each accepted dependency path is
    # acyclic and ends at known external evidence. Input order is replayable.
    available = set(state["evidence"])
    if type(candidate["claims"]) is not list:
        raise Invalid("INVALID_CLAIMS")
    for claim in candidate["claims"]:
        _fields(claim, "id text refs")
        _identifier(claim["id"], "int:")
        if claim["id"] in available:
            raise Invalid("DUPLICATE_ID")
        if type(claim["text"]) is not str or not claim["text"].strip():
            raise Invalid("INVALID_CLAIM")
        _strings(claim["refs"])
        if not claim["refs"] or not set(claim["refs"]) <= available:
            raise Invalid("UNRESOLVED_PROVENANCE")
        available.add(claim["id"])

    if type(candidate["ious"]) is not list:
        raise Invalid("INVALID_IOUS")
    seen = set()
    for iou in candidate["ious"]:
        _fields(iou, "id type code description")
        _identifier(iou["id"])
        if iou["id"] in seen:
            raise Invalid("DUPLICATE_IOU")
        seen.add(iou["id"])
        if type(iou["type"]) is not str or iou["type"] not in IOU_TYPES:
            raise Invalid("INVALID_IOU_TYPE")
        if type(iou["code"]) is not str or iou["code"] not in IOU_CODES:
            raise Invalid("INVALID_IOU_CODE")
        if type(iou["description"]) is not str or not iou["description"].strip():
            raise Invalid("INVALID_IOU_DESCRIPTION")
    if candidate["ious"] and "REQUEST_READING" not in actions:
        raise Invalid("IOU_REQUEST_ACTION_REQUIRED")


def run_pipeline(context, candidate):
    """Produce a sealed advisory verdict. Persistence is a separate host step."""
    state = govern_context(context)
    candidate = parse(canonical(candidate))
    reason = None
    try:
        _evaluate(state, candidate)
    except Invalid as exc:
        reason = str(exc)
    disposition = {"protocol": VERSION, **AUTHORITY, "producer": "GOVERNOR",
                   "state_digest": digest(state), "candidate_digest": digest(candidate),
                   "verdict": "REJECT" if reason else "ADVISORY_ACCEPT",
                   "reasons": [reason] if reason else []}
    seal = {"disposition_digest": digest(disposition), "executability": "NON_EXECUTABLE"}
    seal["digest"] = digest(seal)
    return {"state": state, "disposition": disposition, "seal": seal}


def verify_replay(context, candidate, receipt):
    """Re-evaluate against supplied trusted context and compare canonical bytes."""
    return canonical(run_pipeline(context, candidate)) == canonical(receipt)


class Journal:
    """Local SQLite advisory decisions and durable pending IOUs."""

    def __init__(self, path):
        self.path = Path(path)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY, receipt BLOB NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS iou_progress (
                decision_id TEXT NOT NULL, iou_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('EVIDENCE_RECEIVED','ABANDONED')),
                body BLOB NOT NULL, PRIMARY KEY (decision_id, iou_id),
                FOREIGN KEY (decision_id, iou_id) REFERENCES pending(decision_id, iou_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS pending (
                decision_id TEXT NOT NULL REFERENCES decisions(id),
                iou_id TEXT NOT NULL, principal TEXT NOT NULL, body BLOB NOT NULL,
                PRIMARY KEY (decision_id, iou_id))""")

    def _connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return _Connection(db)

    def submit(self, context, candidate):
        # Snapshot input once before evaluation and transaction construction.
        context, candidate = parse(canonical(context)), parse(canonical(candidate))
        receipt = run_pipeline(context, candidate)
        decision_id = receipt["seal"]["digest"]["sha256"]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            inserted = db.execute("INSERT OR IGNORE INTO decisions VALUES (?, ?)",
                                  (decision_id, canonical(receipt))).rowcount
            if inserted and receipt["disposition"]["verdict"] == "ADVISORY_ACCEPT":
                db.executemany("INSERT INTO pending VALUES (?, ?, ?, ?)", [
                    (decision_id, iou["id"], context["principal"], canonical(iou))
                    for iou in candidate["ious"]
                ])
        return receipt

    def pending(self, principal):
        """Trusted host must supply the authenticated principal."""
        _identifier(principal)
        with self._connect() as db:
            rows = db.execute("""SELECT p.decision_id, p.body FROM pending AS p
                                 LEFT JOIN iou_progress AS e USING (decision_id, iou_id)
                                 WHERE p.principal=? AND e.status IS NULL
                                 ORDER BY p.decision_id, p.iou_id""", (principal,)).fetchall()
        return [{"decision_id": decision_id, "iou": parse(body)} for decision_id, body in rows]


    def complete_iou(self, principal, decision_id, iou_id, status, note):
        """Record an advisory terminal event from the authenticated host."""
        _identifier(principal)
        _identifier(iou_id)
        if status not in ("EVIDENCE_RECEIVED", "ABANDONED"):
            raise Invalid("INVALID_IOU_STATUS")
        if type(note) is not str or not note.strip():
            raise Invalid("IOU_NOTE_REQUIRED")
        event = {"status": status, "note": note, **AUTHORITY}
        body = canonical(event)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute("""SELECT 1 FROM pending
                WHERE decision_id=? AND iou_id=? AND principal=?""",
                (decision_id, iou_id, principal)).fetchone()
            if not exists:
                raise Invalid("UNKNOWN_IOU")
            prior = db.execute("""SELECT body FROM iou_progress
                WHERE decision_id=? AND iou_id=?""", (decision_id, iou_id)).fetchone()
            if prior:
                if prior[0] != body:
                    raise Invalid("IOU_ALREADY_CLOSED")
                return parse(prior[0])
            db.execute("INSERT INTO iou_progress VALUES (?, ?, ?, ?)",
                       (decision_id, iou_id, status, body))
        return parse(body)


class _Connection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db.__enter__()

    def __exit__(self, *error):
        try:
            return self.db.__exit__(*error)
        finally:
            self.db.close()
