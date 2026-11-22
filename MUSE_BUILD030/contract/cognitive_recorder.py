"""Observational cognitive execution recorder for MUSE Router 002.

The recorder has no routing, evidence, governance, or execution authority. It
only persists caller-supplied event facts. In particular, it never upgrades a
planned or simulated event into an executed event.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

SCHEMA = "muse-cognitive-recorder/1"
EVENT_KINDS = {"OBSERVE", "SELECT", "EXECUTE", "EVALUATE", "STORE", "RETRIEVE", "TRANSFER", "VALIDATE"}
PROVENANCE = {"planned", "simulated", "executed", "observed", "inferred"}


class RecorderError(ValueError):
    """Raised when the append-only event stream is invalid or corrupted."""


@dataclass(frozen=True)
class RecorderEvent:
    schema: str
    sequence: int
    event_id: str
    run_id: str
    task_id: str
    kind: str
    timestamp_utc: str
    provenance: str
    payload: dict[str, Any]
    predecessor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema": self.schema,
            "sequence": self.sequence,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "timestamp_utc": self.timestamp_utc,
            "provenance": self.provenance,
            "payload": self.payload,
        }
        if self.predecessor is not None:
            value["predecessor"] = self.predecessor
        return value


class CognitiveRecorder:
    """Append-only JSONL recorder; observational only and fail-closed."""

    def __init__(self, path: str | None = None, run_id: str | None = None,
                 task_id: str = "unknown"):
        self.path = path
        self.run_id = run_id or uuid.uuid4().hex
        self.task_id = str(task_id)
        self.events: list[dict[str, Any]] = []
        self._last_event_id: str | None = None
        if path and os.path.exists(path):
            self.events = self.load(path)
            if self.events:
                self._last_event_id = self.events[-1]["event_id"]

    @staticmethod
    def _utc_now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime()) + f"{time.time_ns() % 1_000_000_000:09d}Z"

    @staticmethod
    def _validate_event(event: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> None:
        required = {"schema", "sequence", "event_id", "run_id", "task_id", "kind",
                    "timestamp_utc", "provenance", "payload"}
        missing = sorted(required - set(event))
        if missing:
            raise RecorderError(f"missing event fields: {missing}")
        if event["schema"] != SCHEMA:
            raise RecorderError(f"unsupported recorder schema: {event['schema']!r}")
        if not isinstance(event["sequence"], int) or event["sequence"] < 1:
            raise RecorderError("sequence must be a positive integer")
        if event["kind"] not in EVENT_KINDS:
            raise RecorderError(f"unsupported event kind: {event['kind']!r}")
        if event["provenance"] not in PROVENANCE:
            raise RecorderError(f"unsupported provenance: {event['provenance']!r}")
        if not isinstance(event["payload"], dict):
            raise RecorderError("payload must be an object")
        if previous is not None:
            if event["sequence"] != previous["sequence"] + 1:
                raise RecorderError("non-monotonic event sequence")
            if event["run_id"] != previous["run_id"]:
                raise RecorderError("run_id changed within event stream")
            if event.get("predecessor") != previous["event_id"]:
                raise RecorderError("causal predecessor does not match prior event")

    def append(self, kind: str, payload: Mapping[str, Any] | None = None,
               *, provenance: str = "observed", task_id: str | None = None,
               predecessor: str | None = None, event_id: str | None = None,
               timestamp_utc: str | None = None) -> dict[str, Any]:
        sequence = len(self.events) + 1
        if predecessor is None:
            predecessor = self._last_event_id
        event = RecorderEvent(
            SCHEMA, sequence, event_id or f"{self.run_id}:{sequence:08d}",
            self.run_id, str(task_id if task_id is not None else self.task_id),
            kind, timestamp_utc or self._utc_now(), provenance, dict(payload or {}), predecessor,
        ).to_dict()
        self._validate_event(event, self.events[-1] if self.events else None)
        if any(e["event_id"] == event["event_id"] for e in self.events):
            raise RecorderError("duplicate event_id")
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
            with open(self.path, "a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
        self.events.append(event)
        self._last_event_id = event["event_id"]
        return dict(event)

    def record(self, kind: str, **kwargs: Any) -> dict[str, Any]:
        return self.append(kind, **kwargs)

    def close_run(self, *, outcome: Any = None, validation: Any = None,
                  provenance: str = "observed") -> dict[str, Any]:
        return self.append("VALIDATE", {"outcome": outcome, "validation": validation,
                                         "run_complete": True}, provenance=provenance)

    def replay(self) -> list[dict[str, Any]]:
        """Return a deterministic copy; replay never invokes cognitive functions."""
        return [json.loads(json.dumps(event, sort_keys=True)) for event in self.events]

    def incomplete(self) -> bool:
        return not self.events or not any(
            e["kind"] == "VALIDATE" and e["payload"].get("run_complete") is True
            for e in self.events)

    @classmethod
    def load(cls, path: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        seen: set[str] = set()
        with open(path, encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RecorderError(f"invalid JSON at line {line_number}") from exc
                if event.get("event_id") in seen:
                    raise RecorderError(f"duplicate event_id at line {line_number}")
                cls._validate_event(event, events[-1] if events else None)
                seen.add(event["event_id"])
                events.append(event)
        return events

    def digest(self) -> str:
        material = "\n".join(json.dumps(e, sort_keys=True, separators=(",", ":"))
                            for e in self.events).encode()
        return hashlib.sha256(material).hexdigest()
