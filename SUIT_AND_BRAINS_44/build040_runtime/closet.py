"""SoSo Closet — Main AI Bounded Request Interface for Build 040.

Separates Main AI requests from the SoSo Suit agent execution boundary.
All agent invocations and memory requests route through SoSoSuitRuntime and
are logged in SoSoContinuity.
"""
from __future__ import annotations

from typing import Any

from .learning_loop import AssistedLearningLoop
from .soso_continuity import SoSoContinuity
from .suit_runtime import SoSoSuitRuntime


class SoSoCloset:
    """Bounded Main AI interface sitting above SoSo Continuity and SoSo Suit."""

    def __init__(self, continuity: SoSoContinuity, suit: SoSoSuitRuntime) -> None:
        self.continuity = continuity
        self.suit = suit
        # 42.1: consultation ledger, keyed by invocation ID. The only
        # Closet change for the consultation work; consult() in
        # build040_runtime.consultation reads and writes this dict.
        self.consultation_ledger: dict[str, Any] = {}

    def submit_task(self, session_id: str, goal: str) -> dict[str, Any]:
        sess = self.continuity.start_session(session_id, goal)
        self.continuity.record_event(session_id, "CLAIM", "main_ai_closet", {"goal": goal})
        return sess

    def retrieve_context(self, session_id: str) -> dict[str, Any]:
        return self.continuity.reconstruct_session(session_id)

    def invoke_agent(
        self, session_id: str, agent_id: str, family: str, index: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            commit_rec, ptr = self.suit.execute_memory_write(agent_id, family, index, payload)
            out = {"status": "COMMITTED", "commit": commit_rec, "pointer": ptr.record_id}
            self.continuity.record_event(session_id, "VERIFIED_RESULT", f"suit:{agent_id}", out)
            return out
        except Exception as exc:
            err = {"status": "DENIED_OR_FAILED", "error": str(exc), "agent_id": agent_id}
            self.continuity.record_event(session_id, "OBSERVATION", f"suit:{agent_id}", err)
            raise

    def run_task(self, session_id: str, agent_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """Execute a bounded, local advisory operation under a Suit grant."""
        if not isinstance(request, dict) or set(request) - {
            "version", "operation", "candidates", "observed", "memory_block"
        }:
            raise ValueError("Invalid task envelope")
        if request.get("version") != "42.0-task/1" or request.get("operation") != "select_discriminator":
            raise ValueError("Unsupported task version or operation")
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 96:
            raise ValueError("Invalid session ID")
        candidates, observed = request.get("candidates"), request.get("observed")
        if not isinstance(candidates, dict) or not 1 <= len(candidates) <= 64 or not isinstance(observed, dict):
            raise ValueError("Expected 1–64 candidates and an observation object")
        for fields in (*candidates.values(), observed):
            if not isinstance(fields, dict) or len(fields) > 32 or any(
                not isinstance(key, str) or type(value) not in (str, int, float, bool, type(None))
                for key, value in fields.items()
            ):
                raise ValueError("Expected up to 32 scalar fields per candidate or observation")
        memory = request.get("memory_block")
        if memory is not None:
            if (not isinstance(memory, list) or len(memory) != 2 or
                    memory[0] not in ("BIG49", "SMALL49") or type(memory[1]) is not int):
                raise ValueError("Invalid memory block reference")
            memory = (memory[0], memory[1])

        def execute():
            self.submit_task(session_id, request["operation"])
            try:
                result = AssistedLearningLoop(self.continuity, self.suit).run_unassisted(
                    session_id, agent_id, candidates, observed, memory_block=memory
                )
            except Exception:
                self.continuity.mark_interrupted(session_id)
                raise
            self.continuity.complete_session(session_id)
            return {"version": request["version"], "session_id": session_id, "authority": 0, **result}

        return self.suit.run_advisory(agent_id, request, execute)

    def request_memory(self, session_id: str, agent_id: str, family: str, index: int) -> dict[str, Any]:
        data = self.suit.execute_memory_read(agent_id, family, index)
        self.continuity.record_event(
            session_id,
            "OBSERVATION",
            f"memory:{family}:{index}",
            {"agent_id": agent_id, "revision": data.get("revision")},
        )
        return data

    def collect_results(self, session_id: str) -> dict[str, Any]:
        return self.continuity.reconstruct_session(session_id)

    def audit_log(self) -> list[dict[str, Any]]:
        return self.suit.audit_log()
