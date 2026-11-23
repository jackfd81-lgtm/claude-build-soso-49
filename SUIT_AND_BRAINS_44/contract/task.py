"""Integration Contract 001 — shared task record (schema v1).

A task is the unit the pipeline reasons about. It carries BOTH
worlds' identity: RealCog's signature and SoSo's tags, so neither
system has to translate the task into the other's native form
before the pipeline can reason about it.

Identity-bearing fields (changing any of these for the same
task_id is an identity violation — the task is REJECTED, not
updated):
  task_id, signature, family, requested_operation, tags.
NOT identity-bearing (they may change as the world changes):
  observed environmental conditions, state, provenance additions.

The identity digest is sha256 over the canonical JSON of the
identity-bearing fields. The harness stores one identity block
per task_id in Big49 (`task.<task_id>`); a second intake with a
different digest is TASK_IDENTITY_MISMATCH.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

SCHEMA_VERSION = "contract-task/1"

# Task states. NEW -> RUNNING -> RESOLVED | UNSOLVED | ESCALATED.
# REJECTED is terminal (identity violation or invalid record).
STATES = ("NEW", "RUNNING", "RESOLVED", "UNSOLVED", "ESCALATED", "REJECTED")


def canon(obj) -> str:
    """Canonical JSON: sorted keys, no whitespace. The same form
    Big49 uses for its own digests (local, not project canon)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def digest_of(obj) -> str:
    return "sha256:" + hashlib.sha256(canon(obj).encode()).hexdigest()


@dataclass(frozen=True)
class TaskRecord:
    """A versioned, validated task. Frozen: a task record is never
    mutated in place — a state change produces a new record (the
    harness keeps the latest per task_id in its trace, and the
    identity block in Big49 is write-once)."""
    task_id: str
    signature: str
    family: str
    tags: frozenset = field(default_factory=frozenset)
    conditions: dict = field(default_factory=dict)
    unknown_conditions: tuple = ()
    contradictory_conditions: tuple = ()
    requested_operation: str = ""
    observed: dict = field(default_factory=dict)
    provenance: tuple = ()
    state: str = "NEW"
    schema_version: str = SCHEMA_VERSION

    def identity(self) -> dict:
        """The identity-bearing fields, in canonical form."""
        return {"task_id": self.task_id,
                "signature": self.signature,
                "family": self.family,
                "requested_operation": self.requested_operation,
                "tags": sorted(self.tags)}

    def identity_digest(self) -> str:
        return digest_of(self.identity())

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version,
                "task_id": self.task_id,
                "signature": self.signature,
                "family": self.family,
                "tags": sorted(self.tags),
                "conditions": dict(self.conditions),
                "unknown_conditions": list(self.unknown_conditions),
                "contradictory_conditions": list(self.contradictory_conditions),
                "requested_operation": self.requested_operation,
                "observed": dict(self.observed),
                "provenance": list(self.provenance),
                "state": self.state}

    @classmethod
    def from_dict(cls, d: dict) -> "TaskRecord":
        return cls(
            task_id=d["task_id"], signature=d["signature"],
            family=d["family"], tags=frozenset(d.get("tags", [])),
            conditions=dict(d.get("conditions", {})),
            unknown_conditions=tuple(d.get("unknown_conditions", [])),
            contradictory_conditions=tuple(d.get("contradictory_conditions", [])),
            requested_operation=d.get("requested_operation", ""),
            observed=dict(d.get("observed", {})),
            provenance=tuple(d.get("provenance", [])),
            state=d.get("state", "NEW"),
            schema_version=d.get("schema_version", SCHEMA_VERSION))


def validate_task(d: dict) -> list:
    """Return a list of validation problems (empty = valid). The
    pipeline refuses an invalid record before any reasoning."""
    problems = []
    if d.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    if not isinstance(d.get("task_id"), str) or not d["task_id"]:
        problems.append("task_id must be a non-empty string")
    elif not all(c.isalnum() or c in "-_." for c in d["task_id"]):
        problems.append("task_id must be [A-Za-z0-9_.-]")
    if not isinstance(d.get("signature"), str) or not d["signature"]:
        problems.append("signature must be a non-empty string")
    if not isinstance(d.get("family"), str) or not d["family"]:
        problems.append("family must be a non-empty string")
    if not isinstance(d.get("tags"), (list, tuple)):
        problems.append("tags must be a list")
    elif not all(isinstance(t, str) and t for t in d["tags"]):
        problems.append("tags must be non-empty strings")
    if not isinstance(d.get("conditions", {}), dict):
        problems.append("conditions must be a dict")
    if d.get("state", "NEW") not in STATES:
        problems.append(f"state must be one of {STATES}")
    for ref in d.get("provenance", []):
        if not isinstance(ref, str) or not ref.startswith("int:"):
            problems.append("provenance refs must start with 'int:'")
    return problems


def make_task(task_id: str, signature: str, family: str, tags,
              conditions=None, unknown_conditions=(),
              contradictory_conditions=(), requested_operation="",
              observed=None, provenance=(),
              schema_version: str = SCHEMA_VERSION) -> TaskRecord:
    """Build and validate a task record. Raises ValueError on any
    validation problem — the pipeline never reasons about an
    invalid task."""
    d = {"schema_version": schema_version, "task_id": task_id,
         "signature": signature, "family": family,
         "tags": sorted(set(tags)),
         "conditions": dict(conditions or {}),
         "unknown_conditions": list(unknown_conditions),
         "contradictory_conditions": list(contradictory_conditions),
         "requested_operation": requested_operation,
         "observed": dict(observed or {}),
         "provenance": list(provenance), "state": "NEW"}
    problems = validate_task(d)
    if problems:
        raise ValueError("invalid task record: " + "; ".join(problems))
    return TaskRecord.from_dict(d)
