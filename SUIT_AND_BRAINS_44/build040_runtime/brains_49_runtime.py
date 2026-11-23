"""49 Brains Runtime & Experience Engine for Suit and Brains Build 040.

Connects Lower49 / Higher49 functions with SoSo Continuity, SoSo Suit, and
CRAM-PU Big49/Small49 memory, and implements a measurable experience-reuse
and ablation cycle.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from contract.disposition_adapter import evaluate_u09
from contract.functions025 import OPERATIONS
from contract.cross_specialty import CROSS_OPERATIONS
from crampu.controller import CRAMError
from muse024 import call as muse_call, initialize as muse_initialize

from .soso_continuity import SoSoContinuity
from .suit_runtime import SoSoSuitRuntime


class Brains49Runtime:
    """Dispatches Lower49/Higher49 operations and tracks 2,208-block cognitive utilization."""

    def __init__(self, continuity: SoSoContinuity, suit: SoSoSuitRuntime, muse_state_dir: Path, *, muse_token: str | None = None) -> None:
        self.continuity = continuity
        self.suit = suit
        self.muse_state_dir = Path(muse_state_dir)
        self.muse_state_dir.mkdir(parents=True, exist_ok=True)
        self.muse_state_dir.chmod(0o700)
        self._muse_token = muse_token if muse_token is not None else muse_initialize(self.muse_state_dir)
        self._exercised_blocks: set[tuple[str, int]] = set()
        self._call_seq = 0

    def dispatch_lower49(self, session_id: str, function_id: str, problem: dict[str, Any]) -> dict[str, Any]:
        if not function_id.startswith("L"):
            raise ValueError(f"Expected Lower49 function_id, got {function_id!r}")
        self.continuity.record_event(session_id, "ROUTING_DECISION", "brains49", {"tier": "LOWER_49", "function_id": function_id})
        if function_id == "start025" or function_id in OPERATIONS:
            self._call_seq += 1
            seq = self._call_seq
            res = muse_call(self.muse_state_dir, self._muse_token, {
                "authority": 0, "operation": "start025", "run": f"{session_id}-{function_id}-{seq}", "arm": "A", "problem": problem,
            })
            self.continuity.record_event(session_id, "VERIFIED_RESULT", function_id, res["decision"])
            return res
        if function_id in CROSS_OPERATIONS:
            out = CROSS_OPERATIONS[function_id](problem)
            self.continuity.record_event(session_id, "INTERPRETATION", function_id, out)
            return out
        raise KeyError(f"Lower49 function not bound: {function_id}")

    def dispatch_higher49(self, session_id: str, function_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not function_id.startswith("U"):
            raise ValueError(f"Expected Higher49 function_id, got {function_id!r}")
        self.continuity.record_event(session_id, "ROUTING_DECISION", "brains49", {"tier": "HIGHER_49", "function_id": function_id})
        if function_id == "U09":
            # 42.1: the dedicated Higher49 U09 route goes through the named
            # disposition adapter (same evaluator, same events, same behaviour).
            events = evaluate_u09(payload, execution_id=f"{session_id}-U09")
            out = {"function_id": "U09", "events": events}
            self.continuity.record_event(session_id, "INTERPRETATION", "U09", {"event_count": len(events)})
            return out
        if function_id in CROSS_OPERATIONS:
            out = CROSS_OPERATIONS[function_id](payload)
            self.continuity.record_event(session_id, "INTERPRETATION", function_id, out)
            return out
        if function_id in OPERATIONS:
            self._call_seq += 1
            seq = self._call_seq
            res = muse_call(self.muse_state_dir, self._muse_token, {
                "authority": 0, "operation": "start025", "run": f"{session_id}-{function_id}-{seq}", "arm": "A", "problem": payload,
            })
            self.continuity.record_event(session_id, "VERIFIED_RESULT", function_id, res["decision"])
            return res
        raise KeyError(f"Higher49 function not bound: {function_id}")

    def mark_exercised(self, family: str, index: int) -> None:
        self._exercised_blocks.add((family, index))

    def utilization_metrics(self, admin_token: str) -> dict[str, Any]:
        ctrl_stats = self.suit.adapter.controller.inspect(admin_token)
        return {
            "logical_capacity": 2208,
            "allocated_owners": ctrl_stats["owners"],
            "populated_cool_blocks": ctrl_stats["committed"],
            "big49_mirrored": self.suit.adapter.big.count(),
            "small49_mirrored": self.suit.adapter.small.count(),
            "exercised_by_cognition": len(self._exercised_blocks),
        }


class ExperienceEngine:
    """Deterministic experience capture, retrieval, reapplication, and ablation engine."""

    PROBE_ORDER = ("probe_thermal", "probe_pressure", "probe_voltage")

    def __init__(self, continuity: SoSoContinuity, suit: SoSoSuitRuntime, brains: Brains49Runtime) -> None:
        self.continuity = continuity
        self.suit = suit
        self.brains = brains

    def record_experience(
        self,
        session_id: str,
        agent_id: str,
        task_type: str,
        signature: str,
        best_probe: str,
        outcome: str,
        *,
        family: str = "BIG49",
        index: int = 0,
    ) -> dict[str, Any]:
        payload = {
            "task_type": task_type,
            "signature": signature,
            "best_probe": best_probe,
            "outcome": outcome,
            "relation": "ASSOCIATED_WITH",
        }
        commit_rec, ptr = self.suit.execute_memory_write(agent_id, family, index, payload)
        self.brains.mark_exercised(family, index)
        exp_id = f"exp-{commit_rec['txid'][:12]}"
        self.continuity.index_experience(
            exp_id, session_id, task_type, outcome, family, index, commit_rec["revision"]
        )
        self.continuity.record_event(
            session_id,
            "EXPERIENCE_REF",
            f"experience:{agent_id}",
            {"exp_id": exp_id, "family": family, "index": index, "outcome": outcome},
        )
        return {"exp_id": exp_id, "commit": commit_rec, "pointer": ptr.record_id}

    def solve_diagnostic_case(
        self,
        session_id: str,
        agent_id: str,
        task_type: str,
        signature: str,
        true_probe: str,
        *,
        mode: str = "with_memory",
    ) -> dict[str, Any]:
        """Solve a 3-probe diagnostic task using CRAM-PU memory or ablation mode."""
        self.continuity.start_session(session_id, f"diagnose:{signature}")
        selected_probe: str | None = None
        memory_status = "NOT_USED"

        if mode == "with_memory":
            entries = self.continuity.lookup_experience(task_type)
            for ent in entries:
                try:
                    rec = self.suit.execute_memory_read(agent_id, ent["block_family"], ent["block_index"])
                    self.brains.mark_exercised(ent["block_family"], ent["block_index"])
                    p = rec["payload"]
                    if p.get("signature") == signature and p.get("outcome") == "SUCCESS":
                        selected_probe = p.get("best_probe")
                        memory_status = "APPLIED"
                        break
                except CRAMError:
                    memory_status = "CORRUPTED_FALLBACK"
        elif mode == "irrelevant_memory":
            entries = self.continuity.lookup_experience(task_type)
            if entries:
                ent = entries[0]
                _ = self.suit.execute_memory_read(agent_id, ent["block_family"], ent["block_index"])
                self.brains.mark_exercised(ent["block_family"], ent["block_index"])
            # Irrelevant memory gives wrong probe -> forces fallback search
            selected_probe = self.PROBE_ORDER[0]
            memory_status = "IRRELEVANT"
        elif mode == "corrupted_memory":
            entries = self.continuity.lookup_experience(task_type)
            for ent in entries:
                try:
                    self.suit.execute_memory_read(agent_id, ent["block_family"], ent["block_index"])
                except CRAMError as exc:
                    memory_status = "CORRUPTED_FALLBACK"
                    self.continuity.record_event(
                        session_id, "OBSERVATION", "experience_ablation", {"error": str(exc)}
                    )
                    break

        if selected_probe is None:
            selected_probe = self.PROBE_ORDER[0]

        first_attempt_hit = selected_probe == true_probe
        probes_needed = 1 if first_attempt_hit else (self.PROBE_ORDER.index(true_probe) + 1)

        # Invoke Lower49 L22 temporal/pathway check as part of cognitive processing
        self.brains.dispatch_lower49(session_id, "L22", {
            "pathway": "B",
            "query": {"subject": signature, "predicate": "winning_probe"},
            "observations": [
                {"source": "diagnostic", "subject": signature, "predicate": "winning_probe", "value": true_probe, "event": "2026-11-22T01:00:00Z"},
            ],
        })
        return {
            "session_id": session_id,
            "mode": mode,
            "memory_status": memory_status,
            "selected_probe": selected_probe,
            "true_probe": true_probe,
            "first_attempt_hit": first_attempt_hit,
            "probes_needed": probes_needed,
        }
