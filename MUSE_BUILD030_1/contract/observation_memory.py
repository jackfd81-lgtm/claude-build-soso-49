"""PH6 experimental observation pathway: record what sensors presented, without judging quoted claims.

This is a structured-observation ingestion interface, not a native video decoder.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .authority import require_task

KINDS = frozenset({'caption', 'speech', 'scene', 'sound', 'measurement', 'action'})


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _validate(record: dict) -> dict:
    if not isinstance(record, dict):
        raise ValueError('observation must be an object')
    if not isinstance(record.get('kind'), str) or record['kind'] not in KINDS:
        raise ValueError('invalid observation kind')
    if not isinstance(record.get('text'), str) or not record['text'].strip():
        raise ValueError('observation text must be nonempty')
    at = record.get('time_ms')
    if type(at) is not int or not 0 <= at < 2**63:
        raise ValueError('time_ms must be a nonnegative integer')
    return {'kind': record['kind'], 'text': record['text'], 'time_ms': at}


def _connect(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'observations.sqlite3', timeout=10)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA busy_timeout=10000')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('''CREATE TABLE IF NOT EXISTS observation (
            digest TEXT PRIMARY KEY, source_id TEXT NOT NULL, time_ms INTEGER NOT NULL,
            kind TEXT NOT NULL, text TEXT NOT NULL, raw_json TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS obs_source_time ON observation(source_id,time_ms)')
    except BaseException:
        db.close()
        raise
    return db


def observation_rows(payload):
    require_task(payload)
    if payload.get("task_type") != "observe":
        raise ValueError("task_type must be observe")
    source_id = payload.get("source_id")
    observations = payload.get("observations")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id must be a nonempty string")
    if not isinstance(observations, list) or not observations:
        raise ValueError("observations must be a nonempty list")
    rows = []
    for observation in observations:
        item = _validate(observation)
        raw = _canonical({"source_id": source_id, **item})
        rows.append((hashlib.sha256(raw.encode("utf-8")).hexdigest(), source_id,
                     item["time_ms"], item["kind"], item["text"], raw))
    return rows


def insert_rows(db, rows):
    return db.executemany("INSERT OR IGNORE INTO observation VALUES (?,?,?,?,?,?)", rows).rowcount


def record_observations(root: Path, payload: dict) -> dict:
    """Validate the entire batch before touching the filesystem or opening SQLite."""
    rows = observation_rows(payload)
    source_id = payload["source_id"]
    db = _connect(Path(root))
    try:
        with db:
            insert_rows(db, rows)
        stored = db.execute('SELECT count(*) FROM observation WHERE source_id=?', (source_id,)).fetchone()[0]
    finally:
        db.close()
    return {'task_type': 'observe', 'status': 'RECORDED', 'source_id': source_id,
            'submitted': len(rows), 'source_records': stored,
            'observation_status': 'PRESENTED_AS_INPUT',
            'claim_truth_evaluated': False,
            'note': 'Quoted words are preserved as observations, not held for external fact checking.'}


def recall_observations(root: Path, source_id: str) -> dict:
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError('source_id must be a nonempty string')
    path = Path(root) / 'observations.sqlite3'
    if not path.is_file():
        return {'source_id': source_id, 'observations': []}
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = db.execute('SELECT kind,text,time_ms,digest FROM observation WHERE source_id=? ORDER BY time_ms,digest', (source_id,)).fetchall()
    finally:
        db.close()
    return {'source_id': source_id, 'observations': [dict(kind=k, text=t, time_ms=ms, digest=d) for k,t,ms,d in rows]}
