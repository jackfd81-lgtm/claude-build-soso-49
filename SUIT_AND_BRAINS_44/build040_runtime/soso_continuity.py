"""SoSo Continuity & Organization Subsystem for Suit and Brains Build 040.

Manages sessions, deterministic event ordering, provenance separation
(OBSERVATION / CLAIM / INTERPRETATION / MODEL_OUTPUT / VERIFIED_RESULT),
task/goal tracking, interrupted-session recovery, and cognitive function routing.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
from typing import Any

VALID_KINDS = frozenset({
    "OBSERVATION",
    "CLAIM",
    "INTERPRETATION",
    "MODEL_OUTPUT",
    "VERIFIED_RESULT",
    "ROUTING_DECISION",
    "EXPERIENCE_REF",
})

COGNITIVE_ROUTES = {
    "temporal_chronology": ("LOWER_49", "L22"),
    "text_relation": ("LOWER_49", "L03"),
    "evidence_qualification": ("LOWER_49", "L33"),
    "cross_specialty_return": ("LOWER_49", "L34"),
    "motor_disposition": ("HIGHER_49", "U09"),
    "memory_recall": ("HIGHER_49", "U15"),
    "advisory_synthesis": ("HIGHER_49", "U32"),
    "cross_specialty_review": ("HIGHER_49", "U33"),
    "pathway_investigation": ("BOTH", "start025"),
}


class SoSoContinuity:
    """Durable SQLite continuity & provenance store for SoSo."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            goal TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('ACTIVE','INTERRUPTED','COMPLETED')),
            created_ns INTEGER NOT NULL,
            updated_ns INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            source TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_ns INTEGER NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        );
        CREATE TABLE IF NOT EXISTS experience_index (
            exp_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            outcome TEXT NOT NULL,
            block_family TEXT NOT NULL,
            block_index INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            created_ns INTEGER NOT NULL
        );
        """)

    def close(self) -> None:
        self._db.close()

    def start_session(self, session_id: str, goal: str) -> dict[str, Any]:
        now = time.time_ns()
        self._db.execute(
            "INSERT OR IGNORE INTO sessions(session_id, goal, status, created_ns, updated_ns) VALUES (?,?,?,?,?)",
            (session_id, goal, "ACTIVE", now, now),
        )
        return self.get_session(session_id)

    def mark_interrupted(self, session_id: str) -> None:
        self._db.execute(
            "UPDATE sessions SET status='INTERRUPTED', updated_ns=? WHERE session_id=?",
            (time.time_ns(), session_id),
        )

    def resume_session(self, session_id: str) -> dict[str, Any]:
        row = self._db.execute("SELECT session_id FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        self._db.execute(
            "UPDATE sessions SET status='ACTIVE', updated_ns=? WHERE session_id=?",
            (time.time_ns(), session_id),
        )
        return self.reconstruct_session(session_id)

    def complete_session(self, session_id: str) -> dict[str, Any]:
        self._db.execute(
            "UPDATE sessions SET status='COMPLETED', updated_ns=? WHERE session_id=?",
            (time.time_ns(), session_id),
        )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        row = self._db.execute(
            "SELECT session_id, goal, status, created_ns, updated_ns FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        return {
            "session_id": row[0],
            "goal": row[1],
            "status": row[2],
            "created_ns": row[3],
            "updated_ns": row[4],
        }

    def record_event(self, session_id: str, kind: str, source: str, payload: dict[str, Any]) -> int:
        if kind not in VALID_KINDS:
            raise ValueError(f"Invalid continuity event kind: {kind!r}")
        now = time.time_ns()
        cur = self._db.execute(
            "INSERT INTO events(session_id, kind, source, payload_json, created_ns) VALUES (?,?,?,?,?)",
            (session_id, kind, source, json.dumps(payload, sort_keys=True), now),
        )
        self._db.execute("UPDATE sessions SET updated_ns=? WHERE session_id=?", (now, session_id))
        return cur.lastrowid

    def index_experience(
        self, exp_id: str, session_id: str, task_type: str, outcome: str, block_family: str, block_index: int, revision: int
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO experience_index VALUES (?,?,?,?,?,?,?,?)",
            (exp_id, session_id, task_type, outcome, block_family, block_index, revision, time.time_ns()),
        )

    def lookup_experience(self, task_type: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT exp_id, session_id, outcome, block_family, block_index, revision FROM experience_index "
            "WHERE task_type=? ORDER BY created_ns DESC",
            (task_type,),
        ).fetchall()
        return [
            {
                "exp_id": r[0],
                "session_id": r[1],
                "outcome": r[2],
                "block_family": r[3],
                "block_index": r[4],
                "revision": r[5],
            }
            for r in rows
        ]

    def route_cognitive_function(self, task_type: str) -> tuple[str, str]:
        if task_type not in COGNITIVE_ROUTES:
            raise KeyError(f"Unknown cognitive route: {task_type!r}")
        return COGNITIVE_ROUTES[task_type]

    def reconstruct_session(self, session_id: str) -> dict[str, Any]:
        sess = self.get_session(session_id)
        rows = self._db.execute(
            "SELECT seq, kind, source, payload_json, created_ns FROM events WHERE session_id=? ORDER BY seq",
            (session_id,),
        ).fetchall()
        events = [
            {"seq": r[0], "kind": r[1], "source": r[2], "payload": json.loads(r[3]), "created_ns": r[4]}
            for r in rows
        ]
        counts: dict[str, int] = {}
        for ev in events:
            counts[ev["kind"]] = counts.get(ev["kind"], 0) + 1
        return {**sess, "event_count": len(events), "counts_by_kind": counts, "events": events}
