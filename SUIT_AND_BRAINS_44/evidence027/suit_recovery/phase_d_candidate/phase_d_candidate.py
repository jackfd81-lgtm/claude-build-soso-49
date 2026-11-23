#!/usr/bin/env python3
"""SoSo Phase D repair candidate 0.2.0. Python 3.10+, standard library only.

Standalone successor candidate; does NOT replace or claim byte/hash equivalence
with 4.0-D-FROZEN-CANDIDATE. AUTH:0; execution NOT_GRANTED; roles ASSERTED_ONLY.

Run: python3 phase_d_candidate.py self-test
     python3 phase_d_candidate.py demo
     python3 phase_d_candidate.py verify /absolute/path/ious.sqlite3

Record wire shape: iou-record/1.0. Canonical profile for this independent build:
phase-d-candidate-canonical/1.0 (NFC, UTF-16-BE key order, int64, no floats).
The original frozen canonicalizer/source/vectors are not supplied; equivalence
is NOT_VERIFIED. Frozen source and enum memberships are not changed.

Explicit candidate policy choices where original source is unavailable:
- start: RESOLVER/ADMIN, IMPLEMENTED INTERPRETATION, NOT SOURCE-EXPLICIT.
- resolve: IN_PROGRESS or BLOCKED only; CREATED must first start or block.
- abandon/transfer: any non-terminal state, recorded OWNER or asserted ADMIN.
- pending: all non-terminal records; next_pending skips BLOCKED.
- conflict resolution removes active edges but preserves CONFLICT_RESOLVED events;
  termination clears each edge before appending the terminal event.
- registry digest commits sequence counter and every complete record.

PersistentRegistry uses an append-only, hash-chained operation journal. Each
operation is replay-verified and committed in one BEGIN IMMEDIATE transaction;
publication follows commit. Queries replay a consistent database snapshot.
Competing connections therefore serialize through SQLite, not a shared cache.
No implicit migration of the earlier snapshot database; old format is refused.

SQLite FULL/WAL durability depends on OS/storage fsync correctness. Digests are
integrity checks, NOT authentication. An attacker rewriting the entire database
and its head can evade unkeyed checks without a separately preserved checkpoint.
checkpoint()/verify --head permit external head pinning. This does not provide
IAM, hardware power-loss certification, or a freeze-cascade certification.
Each operation/read replays the entire journal and validates complete states;
cost grows more than linearly with history. This bounded reference is not a
high-throughput deployment engine. Concurrent arrival order is scheduler-
dependent; deterministic replay applies to the committed request sequence.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import unicodedata
import unittest
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

VERSION = "0.2.0"
AUTHORITY = "AUTH:0"
EXECUTION_AUTHORIZATION = "NOT_GRANTED"
CANONICAL_PROFILE = "phase-d-candidate-canonical/1.0"
JOURNAL_VERSION = "phase-d-candidate-journal/1.0"
ROLES = frozenset({"CREATOR", "RESOLVER", "BLOCKER", "OWNER", "OBSERVER", "ADMIN"})
STATES = frozenset({"CREATED", "IN_PROGRESS", "BLOCKED", "RESOLVED", "ABANDONED"})
TERMINAL = frozenset({"RESOLVED", "ABANDONED"})
IOU_TYPES = frozenset({"READING", "QUESTION", "REQUEST", "PERMISSION", "CONFIRMATION"})
IOU_CODES = frozenset({"IOU_UNMAPPED_OBSERVATION", "IOU_MISSING_DISCRIMINATOR",
                       "IOU_SOURCE_UNAVAILABLE", "IOU_AMBIGUOUS_READING"})
EVENTS = frozenset({"CREATED", "STARTED", "BLOCKED", "UNBLOCKED", "RESOLVED",
                    "ABANDONED", "TRANSFERRED", "CONFLICT_DETECTED", "CONFLICT_RESOLVED"})
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1
MAX_BYTES, MAX_DEPTH, MAX_RECORDS, MAX_JOURNAL = 2**20, 64, 10000, 100000


class BadRequest(ValueError):
    pass


class RoleDenied(PermissionError):
    pass


class DigestMismatch(BadRequest):
    pass


class PersistenceError(RuntimeError):
    pass


def _text(value: Any, name: str, nonempty: bool = False) -> str:
    if not isinstance(value, str):
        raise BadRequest(f"{name}: expected string")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise BadRequest(f"{name}: invalid Unicode surrogate")
    value = unicodedata.normalize("NFC", value)
    if len(value.encode("utf-8")) > MAX_BYTES:
        raise BadRequest(f"{name}: text bound exceeded")
    if nonempty and not value.strip():
        raise BadRequest(f"{name}: empty string")
    return value


def _integer(value: Any, name: str) -> int:
    if type(value) is not int or not INT64_MIN <= value <= INT64_MAX:
        raise BadRequest(f"{name}: expected signed int64, excluding bool")
    return value


def _id(value: Any) -> str:
    value = _text(value, "iou_id")
    if not ID_PATTERN.fullmatch(value):
        raise BadRequest("invalid iou_id")
    return value


def _member(value: Any, allowed: frozenset, name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise BadRequest(f"{name}: unknown member")
    return value


def _normalized(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        raise BadRequest("canonical depth exceeded")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return _integer(value, "JSON integer")
    if isinstance(value, str):
        return _text(value, "JSON string")
    if isinstance(value, DualDigest):
        return value.to_dict()
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            key = _text(key, "JSON key")
            if key in out:
                raise BadRequest("duplicate key after NFC")
            out[key] = _normalized(item, depth + 1)
        return {k: out[k] for k in sorted(out, key=lambda k: k.encode("utf-16-be"))}
    if isinstance(value, (list, tuple)):
        return [_normalized(v, depth + 1) for v in value]
    raise BadRequest(f"unsupported JSON type: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    data = json.dumps(_normalized(value), ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise BadRequest("canonical byte bound exceeded")
    return data


def strict_json(raw: str) -> Any:
    if not isinstance(raw, str):
        raise BadRequest("JSON input must be text")
    try:
        byte_count = len(raw.encode("utf-8"))
    except UnicodeError as exc:
        raise BadRequest("invalid Unicode in JSON input") from exc
    if byte_count > MAX_BYTES:
        raise BadRequest("JSON input bound exceeded")
    def pairs(items):
        result = {}
        for key, value in items:
            key = _text(key, "JSON key")
            if key in result:
                raise BadRequest("duplicate JSON key (including NFC)")
            result[key] = value
        return result
    def parse_int(token):
        if token == "-0":
            raise BadRequest("negative zero prohibited")
        if len(token.lstrip("-")) > 19:
            raise BadRequest("integer outside signed int64")
        return _integer(int(token), "JSON integer")
    def prohibited(token):
        raise BadRequest(f"noninteger/constant prohibited: {token}")
    try:
        return _normalized(json.loads(raw, object_pairs_hook=pairs, parse_int=parse_int,
                                      parse_float=prohibited, parse_constant=prohibited))
    except (json.JSONDecodeError, RecursionError, UnicodeError) as exc:
        raise BadRequest("invalid or overdeep JSON") from exc


@dataclass(frozen=True, slots=True)
class DualDigest(Mapping):
    blake2b256: str
    sha256: str

    def __post_init__(self):
        for name in ("blake2b256", "sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(name + r":[0-9a-f]{64}", value):
                raise BadRequest(f"invalid {name} digest")

    def __getitem__(self, key):
        if key not in ("blake2b256", "sha256"):
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self):
        return iter(("blake2b256", "sha256"))

    def __len__(self):
        return 2

    def to_dict(self):
        return {"blake2b256": self.blake2b256, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping) or set(value) != {"blake2b256", "sha256"}:
            raise BadRequest("DualDigest: exact two-key object required")
        return cls(value["blake2b256"], value["sha256"])


def dual_digest(value: Any) -> DualDigest:
    data = canonical_bytes(value)
    return DualDigest("blake2b256:" + hashlib.blake2b(data, digest_size=32).hexdigest(),
                      "sha256:" + hashlib.sha256(data).hexdigest())


@dataclass(frozen=True, slots=True)
class IOURecord:
    record_version: str
    iou_id: str
    iou_type: str
    code: str | None
    description: str
    priority: int
    blocking: bool
    state: str
    resource: str | None
    parameters_hash: DualDigest | None
    creator: str
    owner: str
    block_reason: str | None
    resolution_path: str | None
    resolution_summary: str | None
    conflicts: tuple[str, ...]
    history: tuple[Mapping, ...]
    record_digest: DualDigest

    def __post_init__(self):
        if self.record_version != "iou-record/1.0":
            raise BadRequest("record version mismatch")
        object.__setattr__(self, "iou_id", _id(self.iou_id))
        _member(self.iou_type, IOU_TYPES, "iou_type")
        _member(self.state, STATES, "state")
        if self.code is not None:
            _member(self.code, IOU_CODES, "code")
        _integer(self.priority, "priority")
        if type(self.blocking) is not bool:
            raise BadRequest("blocking: expected boolean")
        for name in ("description", "creator", "owner"):
            object.__setattr__(self, name, _text(getattr(self, name), name, name != "description"))
        for name in ("resource", "block_reason", "resolution_path", "resolution_summary"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name))
        if self.state == "BLOCKED" and not (self.resolution_path and self.resolution_path.strip()):
            raise BadRequest("MISSING_RESOLUTION_PATH")
        if self.parameters_hash is not None:
            object.__setattr__(self, "parameters_hash", DualDigest.from_dict(self.parameters_hash))
        if not isinstance(self.conflicts, (list, tuple)):
            raise BadRequest("conflicts: expected array")
        conflicts = tuple(_id(i) for i in self.conflicts)
        if len(conflicts) != len(set(conflicts)) or self.iou_id in conflicts:
            raise BadRequest("duplicate/self conflict")
        if self.state in TERMINAL and conflicts:
            raise BadRequest("terminal record has active conflicts")
        object.__setattr__(self, "conflicts", conflicts)
        if not isinstance(self.history, (list, tuple)) or not self.history:
            raise BadRequest("history: nonempty array required")
        events = []
        prev = 0
        for entry in self.history:
            if not isinstance(entry, Mapping) or set(entry) != {"seq", "event", "actor", "role", "detail"}:
                raise BadRequest("history entry schema violation")
            seq = _integer(entry["seq"], "event seq")
            if seq <= prev:
                raise BadRequest("history sequence must increase")
            prev = seq
            event = {"seq": seq, "event": _member(entry["event"], EVENTS, "event"),
                     "actor": _text(entry["actor"], "actor", True),
                     "role": _member(entry["role"], ROLES, "role"),
                     "detail": _text(entry["detail"], "detail")}
            events.append(MappingProxyType(event))
        if events[0]["event"] != "CREATED" or any(e["event"] == "CREATED" for e in events[1:]):
            raise BadRequest("history genesis violation")
        object.__setattr__(self, "history", tuple(events))
        object.__setattr__(self, "record_digest", DualDigest.from_dict(self.record_digest))
        if dual_digest(self.payload()) != self.record_digest:
            raise DigestMismatch("record_digest mismatch")

    def payload(self):
        return {field.name: _normalized(getattr(self, field.name))
                for field in dataclasses.fields(self) if field.name != "record_digest"}

    def to_dict(self):
        return dict(self.payload(), record_digest=self.record_digest.to_dict())

    @classmethod
    def build(cls, **fields):
        names = {field.name for field in dataclasses.fields(cls)} - {"record_digest"}
        if set(fields) - names:
            raise BadRequest("unknown IOURecord construction field")
        payload = {"record_version": "iou-record/1.0", "code": None, "description": "",
                   "priority": 0, "blocking": False, "resource": None, "parameters_hash": None,
                   "block_reason": None, "resolution_path": None, "resolution_summary": None,
                   "conflicts": []}
        payload.update(fields)
        if set(payload) != names:
            raise BadRequest("missing IOURecord field")
        payload = _normalized(payload)
        return cls(**payload, record_digest=dual_digest(payload))

    @classmethod
    def from_dict(cls, data):
        names = {field.name for field in dataclasses.fields(cls)}
        if not isinstance(data, Mapping) or set(data) != names:
            raise BadRequest("record: exact schema fields required")
        return cls(**dict(data))


class _Interface:
    def create(self, iou_id, iou_type, actor, role, owner, *, code=None, description="",
               priority=0, blocking=False, resource=None, parameters_hash=None):
        return self._execute("create", dict(iou_id=iou_id, iou_type=iou_type, actor=actor,
            role=role, owner=owner, code=code, description=description, priority=priority,
            blocking=blocking, resource=resource, parameters_hash=parameters_hash))

    def start(self, iou_id, actor, role):
        """IMPLEMENTED INTERPRETATION, NOT SOURCE-EXPLICIT: RESOLVER/ADMIN."""
        return self._execute("start", dict(iou_id=iou_id, actor=actor, role=role))

    def block(self, iou_id, actor, role, resolution_path, block_reason=""):
        return self._execute("block", dict(iou_id=iou_id, actor=actor, role=role,
                             resolution_path=resolution_path, block_reason=block_reason))

    def unblock(self, iou_id, actor, role):
        return self._execute("unblock", dict(iou_id=iou_id, actor=actor, role=role))

    def resolve(self, iou_id, actor, role, summary=None):
        return self._execute("resolve", dict(iou_id=iou_id, actor=actor, role=role, summary=summary))

    def abandon(self, iou_id, actor, role):
        return self._execute("abandon", dict(iou_id=iou_id, actor=actor, role=role))

    def transfer(self, iou_id, actor, role, new_owner):
        return self._execute("transfer", dict(iou_id=iou_id, actor=actor, role=role, new_owner=new_owner))

    def register_conflict(self, iou_id_a, iou_id_b, actor, role):
        """Compatibility method: refuses unjustified, self, terminal, or existing edges."""
        return self._execute("register_conflict", dict(iou_id_a=iou_id_a, iou_id_b=iou_id_b,
                                                       actor=actor, role=role))

    def resolve_conflict(self, iou_id_a, iou_id_b, actor, role):
        return self._execute("resolve_conflict", dict(iou_id_a=iou_id_a, iou_id_b=iou_id_b,
                                                      actor=actor, role=role))

    def register_candidate_ious(self, entries, type_mapping, actor, role, owner, priorities=None):
        """Atomic bridge for admitted IOUEntry dictionaries; caller must enforce Phase E."""
        return self._execute("candidate_ious", dict(entries=entries, type_mapping=type_mapping,
                           actor=actor, role=role, owner=owner,
                           priorities={} if priorities is None else priorities))

    def get(self, iou_id):
        return self._query("get", _id(iou_id))

    def all_records(self):
        return self._query("all")

    def pending(self):
        return self._query("pending")

    def next_pending(self):
        return self._query("next")

    def blocking_open(self):
        return self._query("blocking")

    def registry_digest(self):
        return self._query("digest")


class Registry(_Interface):
    """Atomic in-memory reference candidate; role assertions are NOT authenticated."""
    def __init__(self):
        self._records = {}
        self._seq = 0
        self._lock = threading.RLock()

    def _clone(self):
        other = Registry()
        other._records, other._seq = dict(self._records), self._seq
        return other

    def _execute(self, op, args):
        args = strict_json(canonical_bytes(args).decode("utf-8"))
        with self._lock:
            stage = self._clone()
            result = stage._apply(op, args)
            stage._validate_registry()
            self._records, self._seq = stage._records, stage._seq
            return result

    def _query(self, query, arg=None):
        with self._lock:
            if query == "get":
                return self._get(arg)
            if query == "all":
                return dict(self._records)
            records = sorted((r for r in self._records.values() if r.state not in TERMINAL),
                             key=lambda r: (-r.priority, r.history[0]["seq"]))
            if query == "pending":
                return records
            if query == "next":
                return next((r for r in records if r.state != "BLOCKED"), None)
            if query == "blocking":
                return [r for r in records if r.blocking]
            if query == "digest":
                return dual_digest({"registry_version": "phase-d-candidate-registry/1.0",
                    "seq": self._seq, "records": {k: v.to_dict() for k, v in sorted(self._records.items())}})
            raise BadRequest("unknown query")

    def _get(self, iid):
        try:
            return self._records[_id(iid)]
        except KeyError as exc:
            raise BadRequest("unknown iou_id") from exc

    @staticmethod
    def _role(role, allowed):
        _member(role, ROLES, "role")
        if role not in allowed:
            raise RoleDenied("asserted role not allowed")

    @staticmethod
    def _owner(rec, actor, role):
        if role != "ADMIN" and not (role == "OWNER" and actor == rec.owner):
            raise RoleDenied("actor is not recorded owner")

    def _event(self, actor, role, event, detail=""):
        self._seq = _integer(self._seq + 1, "registry seq")
        return {"seq": self._seq, "event": event, "actor": actor, "role": role, "detail": detail}

    def _emit(self, rec, actor, role, event, detail="", **changes):
        if rec.state in TERMINAL:
            raise BadRequest("terminal state: no further mutation")
        payload = rec.payload()
        payload.update(changes)
        payload["history"] = [dict(e) for e in rec.history] + [self._event(actor, role, event, detail)]
        new = IOURecord.build(**payload)
        self._records[new.iou_id] = new
        return new

    @staticmethod
    def _conflict(a, b):
        return (a.iou_id != b.iou_id and a.state not in TERMINAL and b.state not in TERMINAL
                and a.resource is not None and a.resource == b.resource
                and a.parameters_hash != b.parameters_hash)

    def _link(self, a, b, actor, role):
        self._emit(a, actor, role, "CONFLICT_DETECTED", b.iou_id,
                   conflicts=sorted((*a.conflicts, b.iou_id)))
        self._emit(b, actor, role, "CONFLICT_DETECTED", a.iou_id,
                   conflicts=sorted((*b.conflicts, a.iou_id)))

    def _unlink(self, a, b, actor, role, detail):
        self._emit(a, actor, role, "CONFLICT_RESOLVED", detail,
                   conflicts=[i for i in a.conflicts if i != b.iou_id])
        self._emit(b, actor, role, "CONFLICT_RESOLVED", detail,
                   conflicts=[i for i in b.conflicts if i != a.iou_id])

    def _apply(self, op, args):
        if op not in {"create", "start", "block", "unblock", "resolve", "abandon", "transfer",
                      "register_conflict", "resolve_conflict", "candidate_ious"}:
            raise BadRequest("unknown operation")
        common = {"iou_id", "actor", "role"}
        fields = {
            "create": common | {"iou_type", "owner", "code", "description", "priority",
                                 "blocking", "resource", "parameters_hash"},
            "start": common, "block": common | {"resolution_path", "block_reason"},
            "unblock": common, "resolve": common | {"summary"}, "abandon": common,
            "transfer": common | {"new_owner"},
            "register_conflict": {"iou_id_a", "iou_id_b", "actor", "role"},
            "resolve_conflict": {"iou_id_a", "iou_id_b", "actor", "role"},
            "candidate_ious": {"entries", "type_mapping", "priorities", "actor", "role", "owner"},
        }
        if not isinstance(args, dict) or set(args) != fields[op]:
            raise BadRequest("operation argument schema violation")
        actor = _text(args.get("actor"), "actor", True)
        role = _member(args.get("role"), ROLES, "role")
        if op == "candidate_ious":
            self._role(role, {"CREATOR", "ADMIN"})
            entries, types, priorities = args["entries"], args["type_mapping"], args["priorities"]
            if not isinstance(entries, list) or not isinstance(types, dict) or not isinstance(priorities, dict):
                raise BadRequest("candidate bridge types invalid")
            result = []
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"iou_id", "code", "blocking", "detail"}:
                    raise BadRequest("IOUEntry schema violation")
                iid = _id(entry["iou_id"])
                if iid not in types:
                    raise BadRequest("missing explicit IOUType mapping")
                _member(entry["code"], IOU_CODES, "candidate IOUCode")
                result.append(self._apply("create", dict(iou_id=iid, iou_type=types[iid], actor=actor,
                    role=role, owner=args["owner"], code=entry["code"], description=entry["detail"],
                    blocking=entry["blocking"], priority=priorities.get(iid, 0), resource=None,
                    parameters_hash=None)))
            # Return the latest immutable records after any automatic linking.
            return tuple(self._records[r.iou_id] for r in result)
        if op == "create":
            self._role(role, {"CREATOR", "ADMIN"})
            iid = _id(args["iou_id"])
            if iid in self._records:
                raise BadRequest("duplicate iou_id")
            if len(self._records) >= MAX_RECORDS:
                raise BadRequest("registry record bound exceeded")
            fields = {k: v for k, v in args.items() if k not in {"actor", "role"}}
            fields.update(state="CREATED", creator=actor, history=[self._event(actor, role, "CREATED")])
            rec = IOURecord.build(**fields)
            self._records[iid] = rec
            for other_id in sorted(self._records):
                a, b = self._records[iid], self._records[other_id]
                if self._conflict(a, b):
                    self._link(a, b, actor, role)
            return self._records[iid]
        if op in {"register_conflict", "resolve_conflict"}:
            a, b = self._get(args["iou_id_a"]), self._get(args["iou_id_b"])
            if a.iou_id == b.iou_id or a.state in TERMINAL or b.state in TERMINAL:
                raise BadRequest("self/terminal conflict operation")
            if role != "ADMIN" and not (role == "OWNER" and actor in {a.owner, b.owner}):
                raise RoleDenied("requires ADMIN or recorded owner of either IOU")
            if op == "resolve_conflict":
                if b.iou_id not in a.conflicts or a.iou_id not in b.conflicts:
                    raise BadRequest("no reciprocal active conflict")
                self._unlink(a, b, actor, role, "explicit resolution: " + a.iou_id + "/" + b.iou_id)
            else:
                if not self._conflict(a, b) or b.iou_id in a.conflicts:
                    raise BadRequest("unjustified or duplicate conflict")
                self._link(a, b, actor, role)
            return None
        rec = self._get(args["iou_id"])
        if rec.state in TERMINAL:
            raise BadRequest("terminal state: no further mutation")
        if op in {"abandon", "transfer"}:
            self._owner(rec, actor, role)
        else:
            self._role(role, {"RESOLVER", "ADMIN"} if op in {"start", "resolve"} else {"BLOCKER", "ADMIN"})
        if op == "start":
            if rec.state != "CREATED":
                raise BadRequest("start requires CREATED")
            return self._emit(rec, actor, role, "STARTED", state="IN_PROGRESS")
        if op == "block":
            if rec.state not in {"CREATED", "IN_PROGRESS"}:
                raise BadRequest("invalid block transition")
            path = _text(args["resolution_path"], "resolution_path", True)
            reason = _text(args["block_reason"], "block_reason")
            return self._emit(rec, actor, role, "BLOCKED", path, state="BLOCKED",
                              resolution_path=path, block_reason=reason or None)
        if op == "unblock":
            if rec.state != "BLOCKED":
                raise BadRequest("unblock requires BLOCKED")
            return self._emit(rec, actor, role, "UNBLOCKED", state="IN_PROGRESS",
                              resolution_path=None, block_reason=None)
        if op == "transfer":
            owner = _text(args["new_owner"], "new_owner", True)
            return self._emit(rec, actor, role, "TRANSFERRED", owner, owner=owner)
        if op == "resolve" and rec.state not in {"IN_PROGRESS", "BLOCKED"}:
            raise BadRequest("resolve requires IN_PROGRESS or BLOCKED (candidate policy)")
        summary = args.get("summary")
        if summary is not None:
            summary = _text(summary, "summary")
        for peer_id in tuple(rec.conflicts):
            self._unlink(self._get(rec.iou_id), self._get(peer_id), actor, role,
                         "mooted by termination: " + rec.iou_id)
        rec = self._get(rec.iou_id)
        if op == "resolve":
            return self._emit(rec, actor, role, "RESOLVED", summary or "", state="RESOLVED",
                              resolution_summary=summary, block_reason=None, resolution_path=None)
        return self._emit(rec, actor, role, "ABANDONED", state="ABANDONED",
                          block_reason=None, resolution_path=None)

    def _validate_registry(self):
        sequences = []
        for iid, rec in self._records.items():
            if iid != rec.iou_id or IOURecord.from_dict(rec.to_dict()) != rec:
                raise DigestMismatch("registry record mismatch")
            sequences.extend(e["seq"] for e in rec.history)
            for peer in rec.conflicts:
                if peer not in self._records or iid not in self._records[peer].conflicts:
                    raise BadRequest("asymmetric/dangling conflict")
                if not self._conflict(rec, self._records[peer]):
                    raise BadRequest("invalid active conflict")
        if sorted(sequences) != list(range(1, self._seq + 1)):
            raise BadRequest("registry event sequence continuity violation")
        self.registry_digest()  # Enforce the declared whole-state byte bound.


GENESIS_HEAD = dual_digest({"journal_version": JOURNAL_VERSION, "genesis": True})


class PersistentRegistry(_Interface):
    """Single-transaction, replay-verified SQLite journal; external roles remain asserted."""
    def __init__(self, db_path, *, expected_head=None):
        if str(db_path) == ":memory:":
            raise BadRequest("PersistentRegistry requires an on-disk path; use Registry for memory")
        self.db_path = str(Path(db_path).expanduser().resolve())
        self._lock = threading.RLock()
        self._closed = False
        self._failed = False
        self._conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            names = {r[0] for r in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if names and names != {"phase_d_journal", "phase_d_head"}:
                raise PersistenceError("unknown/legacy database; implicit migration forbidden")
            if not names:
                self._conn.execute("CREATE TABLE phase_d_journal (idx INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
                self._conn.execute("CREATE TABLE phase_d_head (singleton INTEGER PRIMARY KEY CHECK(singleton=1), version TEXT NOT NULL, head TEXT NOT NULL, entries INTEGER NOT NULL)")
                self._conn.execute("INSERT INTO phase_d_head VALUES (1,?,?,0)",
                                   (JOURNAL_VERSION, canonical_bytes(GENESIS_HEAD).decode()))
                self._conn.execute("CREATE TRIGGER phase_d_no_update BEFORE UPDATE ON phase_d_journal BEGIN SELECT RAISE(ABORT,'append-only journal'); END")
                self._conn.execute("CREATE TRIGGER phase_d_no_delete BEFORE DELETE ON phase_d_journal BEGIN SELECT RAISE(ABORT,'append-only journal'); END")
            stage, head, count = self._load()
            if expected_head is not None and head != DualDigest.from_dict(expected_head):
                raise DigestMismatch("external checkpoint mismatch")
            self._conn.commit()
            self._state, self._head, self._count = stage, head, count
        except BaseException:
            self._conn.rollback()
            self._conn.close()
            raise

    def _ready(self):
        if self._closed or self._failed:
            raise PersistenceError("registry closed or failed; reopen and verify before use")

    def _load(self):
        metadata = self._conn.execute("SELECT singleton,version,head,entries FROM phase_d_head").fetchall()
        if len(metadata) != 1 or metadata[0][0] != 1 or metadata[0][1] != JOURNAL_VERSION:
            raise DigestMismatch("journal metadata mismatch")
        count = _integer(metadata[0][3], "journal count")
        if count < 0 or count > MAX_JOURNAL:
            raise DigestMismatch("journal count bound exceeded")
        head = GENESIS_HEAD
        state = Registry()
        seen = 0
        for index, raw in self._conn.execute("SELECT idx,payload FROM phase_d_journal ORDER BY idx"):
            seen += 1
            if seen > MAX_JOURNAL or index != seen:
                raise DigestMismatch("journal ordering/gap violation")
            value = strict_json(raw)
            if not isinstance(value, dict) or set(value) != {"journal_version", "index", "prev_digest",
                    "request", "records", "seq", "registry_digest", "entry_digest"}:
                raise DigestMismatch("journal entry schema violation")
            supplied = DualDigest.from_dict(value["entry_digest"])
            body = {k: v for k, v in value.items() if k != "entry_digest"}
            if value["journal_version"] != JOURNAL_VERSION or type(value["index"]) is not int or value["index"] != index:
                raise DigestMismatch("journal version/index mismatch")
            if DualDigest.from_dict(value["prev_digest"]) != head or dual_digest(body) != supplied:
                raise DigestMismatch("journal chain/digest mismatch")
            request = value["request"]
            if not isinstance(request, dict) or set(request) != {"op", "args"} or not isinstance(request["args"], dict):
                raise DigestMismatch("invalid journal request")
            before = state.all_records()
            try:
                state._execute(request["op"], request["args"])
            except (BadRequest, RoleDenied, TypeError, KeyError) as exc:
                raise DigestMismatch("journal operation cannot be replayed") from exc
            changes = {k: r.to_dict() for k, r in state.all_records().items() if before.get(k) != r}
            if canonical_bytes(changes) != canonical_bytes(value["records"]):
                raise DigestMismatch("replayed record mismatch")
            if type(value["seq"]) is not int or value["seq"] != state._seq or DualDigest.from_dict(value["registry_digest"]) != state.registry_digest():
                raise DigestMismatch("replayed registry mismatch")
            head = supplied
        if seen != count or DualDigest.from_dict(strict_json(metadata[0][2])) != head:
            raise DigestMismatch("journal head/count mismatch (including tail deletion)")
        return state, head, count

    def _commit(self):
        self._conn.commit()

    def _execute(self, op, args):
        args = strict_json(canonical_bytes(args).decode())
        with self._lock:
            self._ready()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                stage, head, count = self._load()
                if count >= MAX_JOURNAL:
                    raise BadRequest("journal bound exceeded")
                before = stage.all_records()
                result = stage._execute(op, args)
                body = {"journal_version": JOURNAL_VERSION, "index": count + 1,
                        "prev_digest": head.to_dict(), "request": {"op": op, "args": args},
                        "records": {k: r.to_dict() for k, r in stage.all_records().items() if before.get(k) != r},
                        "seq": stage._seq, "registry_digest": stage.registry_digest().to_dict()}
                new_head = dual_digest(body)
                value = dict(body, entry_digest=new_head.to_dict())
                self._conn.execute("INSERT INTO phase_d_journal VALUES (?,?)",
                                   (count + 1, canonical_bytes(value).decode()))
                self._conn.execute("UPDATE phase_d_head SET head=?,entries=? WHERE singleton=1",
                                   (canonical_bytes(new_head).decode(), count + 1))
                self._commit()
                self._state, self._head, self._count = stage, new_head, count + 1
                return result
            except (BadRequest, RoleDenied):
                self._conn.rollback()
                raise
            except BaseException as exc:
                self._failed = True
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                raise PersistenceError("write/commit failed; outcome unacknowledged; reopen to verify") from exc

    def _query(self, query, arg=None):
        with self._lock:
            self._ready()
            try:
                self._conn.execute("BEGIN")
                state, head, count = self._load()
                result = state._query(query, arg)
                self._conn.commit()
                self._state, self._head, self._count = state, head, count
                return result
            except BaseException:
                self._conn.rollback()
                raise

    def checkpoint(self):
        with self._lock:
            self.registry_digest()
            return {"journal_version": JOURNAL_VERSION, "entries": self._count,
                    "head": self._head.to_dict(), "registry_digest": self._state.registry_digest().to_dict()}

    def close(self):
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class CandidateTests(unittest.TestCase):
    """Independent behavior checks; not the unavailable frozen 42-test suite."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "ious.sqlite3")
        self.reg = Registry()

    def tearDown(self):
        self.temp.cleanup()

    def create(self, reg=None, iid="a", **kw):
        return (reg or self.reg).create(iid, "READING", "alice", "CREATOR", "alice", **kw)

    def conflicts(self, reg=None):
        reg = reg or self.reg
        self.create(reg, "a", resource="sensor", parameters_hash=dual_digest({"x": 1}))
        self.create(reg, "b", resource="sensor", parameters_hash=dual_digest({"x": 2}))
        return reg

    def unchanged(self, callable_, error=BadRequest, reg=None):
        reg = reg or self.reg
        before = reg.registry_digest()
        with self.assertRaises(error):
            callable_()
        self.assertEqual(before, reg.registry_digest())

    def test_01_canonical_independent_bytes(self):
        self.assertEqual(canonical_bytes({"b": 2, "a": 1}), b'{"a":1,"b":2}')
        expected = b'{"a":1,"b":2}'
        self.assertEqual(dual_digest({"b": 2, "a": 1}).sha256,
                         "sha256:" + hashlib.sha256(expected).hexdigest())
        self.assertEqual(dual_digest({"b": 2, "a": 1}).blake2b256,
                         "blake2b256:" + hashlib.blake2b(expected, digest_size=32).hexdigest())

    def test_02_nfc(self):
        self.assertEqual(canonical_bytes({"s": "e\u0301"}), b'{"s":"\xc3\xa9"}')
        self.assertEqual(dual_digest({"s": "e\u0301"}), dual_digest({"s": "é"}))
        with self.assertRaises(BadRequest):
            canonical_bytes({"é": 1, "e\u0301": 2})

    def test_03_utf16_order(self):
        expected = '{"𐀀":1,"\ue000":2}'.encode()
        self.assertEqual(canonical_bytes({"\ue000": 2, "𐀀": 1}), expected)

    def test_04_parser_refusals(self):
        for raw in ['{"x":0.5}', '{"x":1e2}', '{"x":NaN}', '{"x":-0}',
                    '{"a":1,"a":2}', '{"é":1,"e\\u0301":2}', '"\\ud800"',
                    str(2**63), '[' * 1100 + '0' + ']' * 1100]:
            with self.subTest(raw=raw[:30]), self.assertRaises(BadRequest):
                strict_json(raw)
        self.assertEqual(strict_json(str(INT64_MIN)), INT64_MIN)
        self.assertEqual(strict_json(str(INT64_MAX)), INT64_MAX)

    def test_05_canonical_bounds(self):
        for value in [float("nan"), 1.0, 2**63, "\ud800", object()]:
            with self.subTest(value=type(value).__name__), self.assertRaises(BadRequest):
                canonical_bytes(value)
        with self.assertRaises(BadRequest):
            canonical_bytes("x" * (MAX_BYTES + 1))
        deep = None
        for _ in range(MAX_DEPTH + 2):
            deep = [deep]
        with self.assertRaises(BadRequest):
            canonical_bytes(deep)

    def test_06_lifecycle(self):
        original = self.create(blocking=True)
        started = self.reg.start("a", "r", "RESOLVER")
        self.assertEqual(original.state, "CREATED")
        self.assertEqual(started.state, "IN_PROGRESS")
        self.reg.block("a", "b", "BLOCKER", "wait")
        self.reg.unblock("a", "b", "BLOCKER")
        final = self.reg.resolve("a", "r", "RESOLVER", "done")
        self.assertEqual(final.state, "RESOLVED")
        self.assertEqual([e["event"] for e in final.history],
                         ["CREATED", "STARTED", "BLOCKED", "UNBLOCKED", "RESOLVED"])
        self.assertEqual(self.reg.blocking_open(), [])

    def test_07_deep_immutability(self):
        params = dual_digest({"x": 1}).to_dict()
        rec = self.create(parameters_hash=params)
        params["sha256"] = "sha256:" + "0" * 64
        self.assertNotEqual(rec.parameters_hash.sha256, params["sha256"])
        with self.assertRaises(TypeError):
            rec.history[0]["actor"] = "changed"
        with self.assertRaises((TypeError, dataclasses.FrozenInstanceError)):
            rec.record_digest.sha256 = "changed"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            rec.owner = "changed"
        wire = rec.to_dict()
        wire["history"][0]["actor"] = "changed"
        wire["record_digest"]["sha256"] = "changed"
        self.assertEqual(rec.history[0]["actor"], "alice")
        self.assertEqual(IOURecord.from_dict(rec.to_dict()), rec)

    def test_08_record_schema(self):
        base = self.create().to_dict()
        modifications = [{"iou_id": "bad id"}, {"iou_id": "a" * 129}, {"priority": True},
            {"blocking": "false"}, {"owner": 9}, {"state": "invented"}, {"code": "invented"},
            {"parameters_hash": {"bad": "hash"}}, {"record_version": "iou-record/9"},
            {"extra": 1}]
        for change in modifications:
            with self.subTest(change=change), self.assertRaises(BadRequest):
                IOURecord.from_dict(dict(base, **change))
        for kwargs in [{"priority": True}, {"blocking": "false"}, {"description": 9},
                       {"parameters_hash": {}}, {"code": "NEW"}]:
            self.unchanged(lambda kwargs=kwargs: self.create(iid="b", **kwargs))

    def test_09_digest_tamper(self):
        wire = self.create().to_dict()
        wire["description"] = "different"
        with self.assertRaises(DigestMismatch):
            IOURecord.from_dict(wire)

    def test_10_role_matrix(self):
        for role in sorted(ROLES - {"CREATOR", "ADMIN"}):
            self.unchanged(lambda role=role: self.reg.create("a", "READING", "a", role, "a"), RoleDenied)
        self.create()
        for role in sorted(ROLES - {"RESOLVER", "ADMIN"}):
            self.unchanged(lambda role=role: self.reg.start("a", "a", role), RoleDenied)
        self.reg.start("a", "r", "RESOLVER")
        for role in sorted(ROLES - {"RESOLVER", "ADMIN"}):
            self.unchanged(lambda role=role: self.reg.resolve("a", "a", role), RoleDenied)
        for role in sorted(ROLES - {"BLOCKER", "ADMIN"}):
            self.unchanged(lambda role=role: self.reg.block("a", "a", role, "path"), RoleDenied)
        self.reg.block("a", "b", "BLOCKER", "path")
        for role in sorted(ROLES - {"BLOCKER", "ADMIN"}):
            self.unchanged(lambda role=role: self.reg.unblock("a", "a", role), RoleDenied)

    def test_11_owner_transfer(self):
        self.create()
        self.unchanged(lambda: self.reg.transfer("a", "bob", "OWNER", "bob"), RoleDenied)
        self.reg.transfer("a", "alice", "OWNER", "bob")
        self.unchanged(lambda: self.reg.abandon("a", "alice", "OWNER"), RoleDenied)
        self.assertEqual(self.reg.abandon("a", "bob", "OWNER").state, "ABANDONED")

    def test_12_admin(self):
        self.reg.create("a", "QUESTION", "admin", "ADMIN", "alice")
        self.reg.start("a", "admin", "ADMIN")
        self.reg.block("a", "admin", "ADMIN", "path")
        self.reg.unblock("a", "admin", "ADMIN")
        self.reg.transfer("a", "admin", "ADMIN", "bob")
        self.assertEqual(self.reg.resolve("a", "admin", "ADMIN").state, "RESOLVED")

    def test_13_invalid_transition_atomic(self):
        self.create()
        self.unchanged(lambda: self.reg.resolve("a", "r", "RESOLVER"))
        self.unchanged(lambda: self.reg.unblock("a", "b", "BLOCKER"))
        for path in ["", "   ", 42, None]:
            self.unchanged(lambda path=path: self.reg.block("a", "b", "BLOCKER", path))
        self.reg.start("a", "r", "RESOLVER")
        self.unchanged(lambda: self.reg.start("a", "r", "RESOLVER"))
        self.unchanged(lambda: self.reg.transfer("a", "alice", "OWNER", ""))

    def test_14_terminal_immutability(self):
        self.conflicts()
        self.reg.start("a", "r", "RESOLVER")
        self.reg.resolve("a", "r", "RESOLVER")
        for call in [lambda: self.reg.start("a", "x", "ADMIN"),
                     lambda: self.reg.block("a", "x", "ADMIN", "p"),
                     lambda: self.reg.unblock("a", "x", "ADMIN"),
                     lambda: self.reg.resolve("a", "x", "ADMIN"),
                     lambda: self.reg.abandon("a", "x", "ADMIN"),
                     lambda: self.reg.transfer("a", "x", "ADMIN", "new"),
                     lambda: self.reg.register_conflict("a", "b", "x", "ADMIN"),
                     lambda: self.reg.resolve_conflict("a", "b", "x", "ADMIN")]:
            self.unchanged(call)

    def test_15_automatic_conflicts(self):
        self.conflicts()
        self.assertEqual(self.reg.get("a").conflicts, ("b",))
        self.assertEqual(self.reg.get("b").conflicts, ("a",))
        self.assertEqual(self.reg.get("a").history[-1]["event"], "CONFLICT_DETECTED")

    def test_16_conflict_negatives(self):
        for resource1, resource2, param1, param2 in [(None, None, None, dual_digest(1)),
            ("x", "y", None, dual_digest(1)), ("x", "x", None, None),
            ("x", "x", dual_digest(1), dual_digest(1))]:
            reg = Registry()
            self.create(reg, "a", resource=resource1, parameters_hash=param1)
            self.create(reg, "b", resource=resource2, parameters_hash=param2)
            self.assertEqual(reg.get("a").conflicts, ())
        reg = Registry()
        self.create(reg, "a", resource="x", parameters_hash=None)
        self.create(reg, "b", resource="x", parameters_hash=dual_digest(1))
        self.assertEqual(reg.get("a").conflicts, ("b",))

    def test_17_conflict_resolution(self):
        self.conflicts()
        self.unchanged(lambda: self.reg.resolve_conflict("a", "b", "bob", "OWNER"), RoleDenied)
        self.reg.resolve_conflict("a", "b", "alice", "OWNER")
        self.assertEqual(self.reg.get("a").conflicts, ())
        self.assertEqual(self.reg.get("b").conflicts, ())
        self.assertEqual(self.reg.get("b").history[-1]["event"], "CONFLICT_RESOLVED")
        self.unchanged(lambda: self.reg.resolve_conflict("a", "b", "x", "ADMIN"))

    def test_18_conflict_compatibility(self):
        self.conflicts()
        self.unchanged(lambda: self.reg.register_conflict("a", "b", "x", "ADMIN"))
        self.unchanged(lambda: self.reg.register_conflict("a", "a", "x", "ADMIN"))
        self.reg.resolve_conflict("a", "b", "x", "ADMIN")
        self.reg.register_conflict(iou_id_a="a", iou_id_b="b", actor="x", role="ADMIN")
        self.assertEqual(self.reg.get("a").conflicts, ("b",))

    def test_19_mooting(self):
        self.conflicts()
        self.create(iid="c", resource="sensor", parameters_hash=dual_digest(3))
        self.reg.abandon("a", "alice", "OWNER")
        self.assertEqual(self.reg.get("a").conflicts, ())
        self.assertEqual(self.reg.get("a").history[-1]["event"], "ABANDONED")
        self.assertEqual(self.reg.get("b").conflicts, ("c",))
        self.assertEqual(self.reg.get("c").conflicts, ("b",))
        self.assertIn("mooted", self.reg.get("b").history[-1]["detail"])

    def test_20_blocking_and_order(self):
        self.create(iid="a", priority=2, blocking=False)
        self.create(iid="b", priority=2, blocking=True)
        self.create(iid="c", priority=9, blocking=True)
        self.reg.block("c", "b", "BLOCKER", "path")
        self.assertEqual([r.iou_id for r in self.reg.pending()], ["c", "a", "b"])
        self.assertEqual(self.reg.next_pending().iou_id, "a")
        self.assertEqual([r.iou_id for r in self.reg.blocking_open()], ["c", "b"])

    def test_21_whole_registry_digest(self):
        variants = [{"description": "other"}, {"priority": 2}, {"blocking": True},
                    {"resource": "r"}, {"parameters_hash": dual_digest(1)}]
        baseline = Registry()
        self.create(baseline)
        for kw in variants:
            reg = Registry()
            self.create(reg, **kw)
            self.assertNotEqual(reg.registry_digest(), baseline.registry_digest())
        reg = Registry()
        reg.create("a", "READING", "alice", "CREATOR", "bob")
        self.assertNotEqual(reg.registry_digest(), baseline.registry_digest())

    def test_22_bridge_atomic(self):
        entries = [{"iou_id": "a", "code": "IOU_SOURCE_UNAVAILABLE", "blocking": True, "detail": "d"},
                   {"iou_id": "b", "code": "IOU_AMBIGUOUS_READING", "blocking": False, "detail": "d"}]
        self.unchanged(lambda: self.reg.register_candidate_ious(entries, {"a": "READING"},
                                                               "alice", "CREATOR", "alice"))
        result = self.reg.register_candidate_ious(entries, {"a": "READING", "b": "QUESTION"},
                                                  "alice", "CREATOR", "alice")
        self.assertEqual(len(result), 2)
        self.assertEqual([r.iou_id for r in self.reg.blocking_open()], ["a"])

    def test_23_restart_and_digest(self):
        with PersistentRegistry(self.path) as reg:
            self.conflicts(reg)
            reg.start("a", "r", "RESOLVER")
            reg.resolve("a", "r", "RESOLVER", "done")
            checkpoint = reg.checkpoint()
            records = {k: v.to_dict() for k, v in reg.all_records().items()}
        with PersistentRegistry(self.path, expected_head=checkpoint["head"]) as reg:
            self.assertEqual(reg.checkpoint(), checkpoint)
            self.assertEqual({k: v.to_dict() for k, v in reg.all_records().items()}, records)
            self.assertEqual(reg.get("a").state, "RESOLVED")
            self.assertEqual(reg.get("b").conflicts, ())

    def test_24_keyword_conflict_persistence(self):
        with PersistentRegistry(self.path) as reg:
            self.conflicts(reg)
            reg.resolve_conflict(iou_id_a="a", iou_id_b="b", actor="x", role="ADMIN")
            reg.register_conflict(iou_id_a="a", iou_id_b="b", actor="x", role="ADMIN")
        with PersistentRegistry(self.path) as reg:
            self.assertEqual(reg.get("a").conflicts, ("b",))
            self.assertEqual(reg.get("b").conflicts, ("a",))

    def test_25_rejected_write_unchanged(self):
        with PersistentRegistry(self.path) as reg:
            self.create(reg)
            before = reg.checkpoint()
            self.unchanged(lambda: reg.start("a", "alice", "OWNER"), RoleDenied, reg)
            self.assertEqual(reg.checkpoint(), before)

    def test_26_commit_failure_rollback(self):
        with PersistentRegistry(self.path) as reg:
            self.create(reg)
            before = reg.checkpoint()
            old_state = reg._state
            def fail():
                raise sqlite3.OperationalError("injected disk failure before commit")
            reg._commit = fail
            with self.assertRaises(PersistenceError):
                reg.start("a", "r", "RESOLVER")
            self.assertIs(reg._state, old_state)
            with self.assertRaises(PersistenceError):
                reg.get("a")
        with PersistentRegistry(self.path) as reg:
            self.assertEqual(reg.checkpoint(), before)
            self.assertEqual(reg.get("a").state, "CREATED")

    def test_27_ambiguous_commit_fail_closed(self):
        with PersistentRegistry(self.path) as reg:
            self.create(reg)
            actual = reg._commit
            def commit_then_error():
                actual()
                raise sqlite3.OperationalError("injected failure after durable commit")
            reg._commit = commit_then_error
            with self.assertRaises(PersistenceError):
                reg.start("a", "r", "RESOLVER")
            with self.assertRaises(PersistenceError):
                reg.get("a")
        with PersistentRegistry(self.path) as reg:
            self.assertEqual(reg.get("a").state, "IN_PROGRESS")
            self.assertEqual([e["event"] for e in reg.get("a").history], ["CREATED", "STARTED"])

    def test_28_append_only_sql(self):
        with PersistentRegistry(self.path) as reg:
            self.create(reg)
        with sqlite3.connect(self.path) as conn:
            for sql in ["DELETE FROM phase_d_journal", "UPDATE phase_d_journal SET payload='{}'"]:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql)

    def test_29_tail_deletion_detection(self):
        with PersistentRegistry(self.path) as reg:
            self.create(reg)
            reg.start("a", "r", "RESOLVER")
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TRIGGER phase_d_no_delete")
            conn.execute("DELETE FROM phase_d_journal WHERE idx=2")
        with self.assertRaises(DigestMismatch):
            PersistentRegistry(self.path)

    def test_30_recomputed_entry_cannot_hide_bad_record(self):
        with PersistentRegistry(self.path) as reg:
            self.create(reg)
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TRIGGER phase_d_no_update")
            value = strict_json(conn.execute("SELECT payload FROM phase_d_journal").fetchone()[0])
            value["records"]["a"]["owner"] = "attacker"
            body = {k: v for k, v in value.items() if k != "entry_digest"}
            value["entry_digest"] = dual_digest(body).to_dict()
            conn.execute("UPDATE phase_d_journal SET payload=?", (canonical_bytes(value).decode(),))
            conn.execute("UPDATE phase_d_head SET head=?", (canonical_bytes(value["entry_digest"]).decode(),))
        with self.assertRaises(DigestMismatch):
            PersistentRegistry(self.path)

    def test_31_external_head_pin(self):
        with PersistentRegistry(self.path) as reg:
            old = reg.checkpoint()["head"]
            self.create(reg)
        with self.assertRaises(DigestMismatch):
            PersistentRegistry(self.path, expected_head=old)

    def test_32_legacy_refusal(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE iou_records(iou_id TEXT, payload TEXT)")
        with self.assertRaises(PersistenceError):
            PersistentRegistry(self.path)

    def test_33_threaded_unique_operations(self):
        with PersistentRegistry(self.path) as reg:
            def worker(i):
                iid = f"iou-{i}"
                self.create(reg, iid)
                reg.start(iid, "r", "RESOLVER")
                return reg.resolve(iid, "r", "RESOLVER").state
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(worker, range(12))), ["RESOLVED"] * 12)
            records = reg.all_records()
            sequences = sorted(e["seq"] for r in records.values() for e in r.history)
            self.assertEqual(sequences, list(range(1, 37)))
            self.assertEqual(reg.checkpoint()["entries"], 36)
        with PersistentRegistry(self.path) as reg:
            self.assertEqual(len(reg.all_records()), 12)
            self.assertTrue(all(r.state == "RESOLVED" for r in reg.all_records().values()))

    def test_34_competing_connections(self):
        with PersistentRegistry(self.path) as seed:
            self.create(seed)
        barrier = threading.Barrier(2)
        def worker(i):
            with PersistentRegistry(self.path) as reg:
                barrier.wait(timeout=10)
                reg.transfer("a", "admin", "ADMIN", f"owner-{i}")
                return True
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(worker, range(2))), [True, True])
        with PersistentRegistry(self.path) as reg:
            self.assertEqual([e["event"] for e in reg.get("a").history],
                             ["CREATED", "TRANSFERRED", "TRANSFERRED"])
            self.assertEqual(reg.checkpoint()["entries"], 3)

    def test_35_concurrent_terminal_contenders(self):
        self.create()
        self.reg.start("a", "r", "RESOLVER")
        barrier = threading.Barrier(4)
        def worker(i):
            barrier.wait(timeout=10)
            try:
                self.reg.resolve("a", f"resolver-{i}", "RESOLVER")
                return True
            except BadRequest:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(worker, range(4)))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(len(self.reg.get("a").history), 3)

    def test_36_duplicate_and_unknown(self):
        self.create()
        self.unchanged(lambda: self.create())
        self.unchanged(lambda: self.reg.start("unknown", "r", "RESOLVER"))
        self.unchanged(lambda: self.reg.create("bad id", "READING", "a", "CREATOR", "a"))

    def test_37_stored_version_and_unknown_fields(self):
        base = self.create().to_dict()
        for version in ["iou-record/9", None]:
            with self.assertRaises(BadRequest):
                IOURecord.from_dict(dict(base, record_version=version))
        with self.assertRaises(BadRequest):
            IOURecord.build(**dict(self.reg.get("a").payload(), unknown=1))

    def test_38_atomic_multiple_conflict_rollback(self):
        with PersistentRegistry(self.path) as reg:
            self.conflicts(reg)
            prior = reg.checkpoint()
            def fail():
                raise sqlite3.OperationalError("injected multi-record commit failure")
            reg._commit = fail
            with self.assertRaises(PersistenceError):
                reg.resolve_conflict("a", "b", "x", "ADMIN")
        with PersistentRegistry(self.path) as reg:
            self.assertEqual(reg.checkpoint(), prior)
            self.assertEqual(reg.get("a").conflicts, ("b",))
            self.assertEqual(reg.get("b").conflicts, ("a",))

    def test_39_sqlite_rollback_on_process_exit(self):
        import subprocess
        with PersistentRegistry(self.path) as reg:
            self.create(reg)
            expected = reg.checkpoint()
        script = ('import sqlite3,os,sys; c=sqlite3.connect(sys.argv[1]); '
                  'c.execute("BEGIN IMMEDIATE"); '
                  'c.execute("UPDATE phase_d_head SET entries=999"); os._exit(23)')
        result = subprocess.run([sys.executable, "-c", script, self.path], check=False)
        self.assertEqual(result.returncode, 23)
        with PersistentRegistry(self.path) as reg:
            self.assertEqual(reg.checkpoint(), expected)

    def test_40_full_snapshot_defensive(self):
        self.create()
        snapshot = self.reg.all_records()
        snapshot.clear()
        self.assertEqual(self.reg.get("a").iou_id, "a")
        self.assertEqual(self.reg.registry_digest(), self.reg.registry_digest())

    def test_41_process_exit_during_real_operation(self):
        import subprocess
        with PersistentRegistry(self.path) as reg:
            self.create(reg)
            before = reg.checkpoint()
        script = ('import sys,os; sys.path.insert(0,sys.argv[2]); '
                  'from phase_d_candidate import PersistentRegistry; '
                  'r=PersistentRegistry(sys.argv[1]); r._commit=lambda:os._exit(37); '
                  'r.start("a","r","RESOLVER")')
        result = subprocess.run([sys.executable, "-c", script, self.path,
                                 str(Path(__file__).resolve().parent)], check=False)
        self.assertEqual(result.returncode, 37)
        with PersistentRegistry(self.path) as reg:
            self.assertEqual(reg.checkpoint(), before)
            self.assertEqual(reg.get("a").state, "CREATED")

    def test_42_process_exit_after_real_commit(self):
        import subprocess
        with PersistentRegistry(self.path) as reg:
            self.create(reg)
        script = ('import sys,os; sys.path.insert(0,sys.argv[2]); '
                  'from phase_d_candidate import PersistentRegistry; '
                  'r=PersistentRegistry(sys.argv[1]); original=r._commit; '
                  'r._commit=lambda:(original(),os._exit(38)); '
                  'r.start("a","r","RESOLVER")')
        result = subprocess.run([sys.executable, "-c", script, self.path,
                                 str(Path(__file__).resolve().parent)], check=False)
        self.assertEqual(result.returncode, 38)
        with PersistentRegistry(self.path) as reg:
            self.assertEqual(reg.get("a").state, "IN_PROGRESS")
            self.assertEqual(reg.checkpoint()["entries"], 2)

    def test_43_closed_operation_arguments(self):
        self.create()
        self.unchanged(lambda: self.reg._execute("start", dict(iou_id="a", actor="r",
                                                            role="RESOLVER", extra=True)))
        entry = [{"iou_id": "b", "code": "IOU_SOURCE_UNAVAILABLE", "blocking": False, "detail": ""}]
        self.unchanged(lambda: self.reg.register_candidate_ious(entry, {"b": "READING"},
                            "alice", "CREATOR", "alice", priorities=[]))

    def test_44_raw_unicode_and_large_integer(self):
        with self.assertRaises(BadRequest):
            strict_json('"\ud800"')
        with self.assertRaises(BadRequest):
            strict_json("9" * 5000)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("self-test", help="Run behavioral, recovery, tamper, and concurrency tests")
    sub.add_parser("demo", help="Temporary database demo; does not overwrite user data")
    verify = sub.add_parser("verify", help="Read/replay an existing candidate journal")
    verify.add_argument("database")
    verify.add_argument("--head", help="Expected checkpoint head, exact DualDigest JSON object")
    args = parser.parse_args(argv)
    if args.command == "self-test":
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(CandidateTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    if args.command == "demo":
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "demo.sqlite3")
            with PersistentRegistry(path) as reg:
                reg.create("source-1", "READING", "operator", "CREATOR", "operator",
                           code="IOU_SOURCE_UNAVAILABLE", blocking=True)
                reg.block("source-1", "operator", "BLOCKER", "restore committed source")
                reg.resolve("source-1", "operator", "RESOLVER", "source restored")
                checkpoint = reg.checkpoint()
            with PersistentRegistry(path, expected_head=checkpoint["head"]) as reg:
                print(json.dumps({"candidate_version": VERSION, "authority": AUTHORITY,
                    "execution_authorization": EXECUTION_AUTHORIZATION,
                    "canonical_profile": CANONICAL_PROFILE, "frozen_equivalence": "NOT_VERIFIED",
                    "record": reg.get("source-1").to_dict(), "checkpoint": reg.checkpoint()}, indent=2))
        return 0
    if not Path(args.database).is_file():
        parser.error("verify requires an existing database file")
    with PersistentRegistry(args.database, expected_head=strict_json(args.head) if args.head else None) as reg:
        print(json.dumps(reg.checkpoint(), indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BadRequest, RoleDenied, PersistenceError, sqlite3.Error) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
