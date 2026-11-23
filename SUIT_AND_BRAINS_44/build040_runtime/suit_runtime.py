"""SoSo Suit 4.x Agent Execution Boundary for Build 040.

Enforces agent identity, scoped capabilities, block-ID scope, resource limits,
child-agent capability attenuation, revocation, termination, OD-05 causal checks,
and audit logging. Never grants PH6 Lane-1 evidence authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import wraps
import json
from threading import RLock
import time
from typing import Any

from crampu.build030_bridge import Merged49BrainsCramAdapter, build030_suit_capability_check
from crampu.controller import AuthorizationError


def _serialized(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return run


@dataclass
class AgentGrant:
    agent_id: str
    capabilities: frozenset[str]
    allowed_block_ids: frozenset[int] | None
    max_steps: int = 64
    max_payload_bytes: int = 16384
    steps_used: int = 0
    revoked: bool = False
    terminated: bool = False
    parent_agent_id: str | None = None
    created_ns: int = field(default_factory=time.time_ns)


class SoSoSuitRuntime:
    """Operational and capability boundary for agents accessing CRAM-PU + 49 Brains."""

    AUTHORITY_CLASS = "ADVISORY_AUTH_0"

    def __init__(self, adapter: Merged49BrainsCramAdapter) -> None:
        self.adapter = adapter
        self._lock = RLock()
        self._grants: dict[str, AgentGrant] = {}
        self._audit: list[dict[str, Any]] = []

    def _log(self, agent_id: str, action: str, outcome: str, detail: str = "") -> None:
        self._audit.append({
            "seq": len(self._audit) + 1,
            "agent_id": agent_id,
            "action": action,
            "outcome": outcome,
            "detail": detail,
            "authority_class": self.AUTHORITY_CLASS,
            "ts_ns": time.time_ns(),
        })

    @staticmethod
    def _validate_limits(max_steps: int, max_payload_bytes: int) -> None:
        if type(max_steps) is not int or max_steps <= 0:
            raise ValueError("max_steps must be a positive integer")
        if type(max_payload_bytes) is not int or max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes must be a positive integer")

    @_serialized
    def run_advisory(self, agent_id: str, payload: dict[str, Any], execute) -> Any:
        grant = self._active_grant(agent_id, "task.run")
        size = len(json.dumps(payload, allow_nan=False).encode("utf-8"))
        if size > grant.max_payload_bytes:
            raise AuthorizationError("Task exceeds agent payload limit")
        self._debit(grant)
        result = execute()
        self._log(agent_id, "task.run", "ALLOWED")
        return result

    @_serialized
    def authorize_agent(
        self,
        agent_id: str,
        capabilities: set[str] | frozenset[str],
        *,
        allowed_block_ids: set[int] | frozenset[int] | None = None,
        max_steps: int = 64,
        max_payload_bytes: int = 16384,
    ) -> AgentGrant:
        if not build030_suit_capability_check(agent_id, "memory.read", 0):
            self._log(agent_id, "authorize", "DENIED", "invalid L01-L49 principal")
            raise AuthorizationError(f"Invalid agent principal: {agent_id!r}")
        self._validate_limits(max_steps, max_payload_bytes)
        for existing in self._grants.values():
            if existing.parent_agent_id == agent_id:
                existing.revoked = True
        grant = AgentGrant(
            agent_id=agent_id,
            capabilities=frozenset(capabilities),
            allowed_block_ids=frozenset(allowed_block_ids) if allowed_block_ids is not None else None,
            max_steps=max_steps,
            max_payload_bytes=max_payload_bytes,
        )
        self._grants[agent_id] = grant
        self._log(agent_id, "authorize", "GRANTED", f"caps={sorted(grant.capabilities)}")
        return grant

    @_serialized
    def request_child_agent(
        self,
        parent_id: str,
        child_id: str,
        capabilities: set[str] | frozenset[str],
        *,
        allowed_block_ids: set[int] | frozenset[int] | None = None,
        max_steps: int = 16,
    ) -> AgentGrant:
        parent = self._active_grant(parent_id, "spawn_child")
        self._validate_limits(max_steps, parent.max_payload_bytes)
        if parent.parent_agent_id is not None or parent_id == child_id or child_id in self._grants:
            raise AuthorizationError("Child must have a fresh identity and cannot delegate")
        req_caps = frozenset(capabilities)
        if "spawn_child" in req_caps:
            raise AuthorizationError("Child cannot delegate")
        if not req_caps.issubset(parent.capabilities):
            self._log(child_id, "spawn_child", "DENIED", "capability escalation over parent")
            raise AuthorizationError("Child capabilities must be subset of parent capabilities")
        if parent.allowed_block_ids is not None:
            if allowed_block_ids is None or not frozenset(allowed_block_ids).issubset(parent.allowed_block_ids):
                self._log(child_id, "spawn_child", "DENIED", "block scope escalation over parent")
                raise AuthorizationError("Child block scope must be subset of parent scope")
        if not build030_suit_capability_check(child_id, "memory.read", 0):
            raise AuthorizationError(f"Invalid child principal: {child_id!r}")
        grant = AgentGrant(
            agent_id=child_id,
            capabilities=req_caps,
            allowed_block_ids=frozenset(allowed_block_ids) if allowed_block_ids is not None else parent.allowed_block_ids,
            max_steps=min(max_steps, parent.max_steps - parent.steps_used),
            max_payload_bytes=parent.max_payload_bytes,
            parent_agent_id=parent_id,
        )
        self._grants[child_id] = grant
        self._log(child_id, "spawn_child", "GRANTED", f"parent={parent_id}")
        return grant

    @_serialized
    def revoke_agent(self, agent_id: str) -> None:
        if agent_id in self._grants:
            self._grants[agent_id].revoked = True
            self._log(agent_id, "revoke", "REVOKED")

    @_serialized
    def terminate_agent(self, agent_id: str) -> None:
        if agent_id in self._grants:
            self._grants[agent_id].terminated = True
            self._grants[agent_id].revoked = True
            self._log(agent_id, "terminate", "TERMINATED")

    def _active_grant(self, agent_id: str, capability: str) -> AgentGrant:
        grant = self._grants.get(agent_id)
        if grant is None or grant.revoked or grant.terminated:
            self._log(agent_id, capability, "DENIED", "no active grant or revoked/terminated")
            raise AuthorizationError("Agent not authorized or revoked/terminated")
        if grant.parent_agent_id is not None:
            self._active_grant(grant.parent_agent_id, "spawn_child")
        if capability not in grant.capabilities:
            self._log(agent_id, capability, "DENIED", f"missing capability {capability}")
            raise AuthorizationError(f"Missing capability: {capability}")
        if grant.steps_used >= grant.max_steps:
            self._log(agent_id, capability, "DENIED", "step budget exhausted")
            raise AuthorizationError("Agent step budget exhausted")
        return grant

    def _debit(self, grant: AgentGrant) -> None:
        if grant.parent_agent_id is not None:
            self._active_grant(grant.parent_agent_id, "spawn_child").steps_used += 1
        grant.steps_used += 1

    def _resolve_block_id(self, family: str, index: int) -> int:
        if family == "BIG49":
            return self.adapter.brain_adapter.big49_id(index)
        if family == "SMALL49":
            return self.adapter.brain_adapter.small49_id(index)
        raise ValueError(f"Unknown family: {family!r}")

    @_serialized
    def execute_memory_write(
        self, agent_id: str, family: str, index: int, payload: dict[str, Any],
        *, bucket: str | None = None,
    ) -> tuple[dict[str, Any], Any]:
        # Build 043: the mirror bucket is selectable (default None keeps the
        # family's default bucket, as before). Case state persists with
        # bucket="workspace_large" so its mirror is a short reference while
        # COOL holds the full state.
        grant = self._active_grant(agent_id, "memory.write")
        block_id = self._resolve_block_id(family, index)
        if grant.allowed_block_ids is not None and block_id not in grant.allowed_block_ids:
            self._log(agent_id, "memory.write", "DENIED", f"block {block_id} out of scope")
            raise AuthorizationError(f"Block {block_id} outside agent scope")
        raw_len = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
        if raw_len > grant.max_payload_bytes:
            self._log(agent_id, "memory.write", "DENIED", f"payload {raw_len} > {grant.max_payload_bytes}")
            raise AuthorizationError("Payload exceeds agent max_payload_bytes")
        self._debit(grant)
        res = self.adapter.write_and_sync(agent_id, family, index, payload, bucket=bucket)
        self._log(agent_id, "memory.write", "ALLOWED", f"block={block_id} rev={res[0]['revision']}")
        return res

    @_serialized
    def execute_memory_read(self, agent_id: str, family: str, index: int) -> dict[str, Any]:
        grant = self._active_grant(agent_id, "memory.read")
        block_id = self._resolve_block_id(family, index)
        if grant.allowed_block_ids is not None and block_id not in grant.allowed_block_ids:
            self._log(agent_id, "memory.read", "DENIED", f"block {block_id} out of scope")
            raise AuthorizationError(f"Block {block_id} outside agent scope")
        self._debit(grant)
        # FIX-42.1.2: payload and COOL reference come from one revision-bound
        # read (load_from_cool_with_reference), so they always describe the
        # same revision. Previously the payload was read first and the
        # reference fetched in a second read, and a write between the two
        # could leave the reference pointing at a newer revision.
        data, reference = self.adapter.controller.load_from_cool_with_reference(block_id, agent_id)
        data["cool_reference"] = reference
        self._log(agent_id, "memory.read", "ALLOWED", f"block={block_id}")
        return data

    @_serialized
    def audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit)
