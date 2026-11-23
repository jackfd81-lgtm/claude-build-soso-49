"""Closed U11–U15 Assisted Learning & Transfer Loop for Suit and Brains Build 040.

Integrates existing Higher49 functions:
- U11 (gaps): Detect information gaps across candidate hypotheses.
- U12 (select): Select discriminating feature given learned preference profile.
- U13 (evaluate_cause): Filter candidate hypotheses against observed evidence.
- U14 (learn): Identify best partition profile from outcome history.
- U15 (transfer): Reuse learned profile from Big49/Small49 memory on unseen problems.
"""
from __future__ import annotations

from typing import Any

from contract.functions025 import OPERATIONS

from .online_ai import OnlineAIGateway
from .soso_continuity import SoSoContinuity
from .suit_runtime import SoSoSuitRuntime


class AssistedLearningLoop:
    """Coordinates U11–U15, Big49/Small49 memory, SoSo Continuity, and limited AI hints."""

    def __init__(
        self,
        continuity: SoSoContinuity,
        suit: SoSoSuitRuntime,
        gateway: OnlineAIGateway | None = None,
    ) -> None:
        self.continuity = continuity
        self.suit = suit
        self.gateway = gateway

    def run_unassisted(
        self,
        session_id: str,
        agent_id: str,
        candidates: dict[str, dict[str, Any]],
        observed: dict[str, Any],
        *,
        memory_block: tuple[str, int] | None = None,
    ) -> dict[str, Any]:
        """Attempt problem using U11/U13/U15/U12 and optional Big49/Small49 learned profile."""
        self.continuity.start_session(session_id, "U11-U15 unassisted attempt")
        eval_res = OPERATIONS["U13"](candidates, observed)
        remaining = eval_res["remaining"]
        self.continuity.record_event(
            session_id, "ROUTING_DECISION", "U13", {"remaining": list(remaining)}
        )
        partitions = OPERATIONS["U11"](remaining, observed)
        self.continuity.record_event(
            session_id, "ROUTING_DECISION", "U11", {"features": list(partitions)}
        )

        learned_records: list[dict[str, Any]] = []
        if memory_block is not None:
            family, idx = memory_block
            try:
                rec = self.suit.execute_memory_read(agent_id, family, idx)
                prof = rec["payload"].get("learned_profile")
                if isinstance(prof, list):
                    learned_records.append({"profile": prof, "source": f"{family}:{idx}"})
            except KeyError:
                self.continuity.record_event(
                    session_id, "OBSERVATION", "profile_read", {"status": "ABSENT", "block": [family, idx]}
                )
            except Exception as exc:
                self.continuity.record_event(
                    session_id, "OBSERVATION", "profile_read",
                    {"status": "FAILED", "block": [family, idx], "error_type": type(exc).__name__},
                )
                raise

        transferred = OPERATIONS["U15"](learned_records, partitions) if learned_records else None
        if learned_records:
            self.continuity.record_event(
                session_id, "ROUTING_DECISION", "U15",
                {"matched": transferred is not None, "from": learned_records[0]["source"]},
            )
        pref = transferred["profile"] if transferred else None
        sel = OPERATIONS["U12"](partitions, pref)
        self.continuity.record_event(session_id, "ROUTING_DECISION", "U12", sel)
        self.continuity.record_event(
            session_id,
            "VERIFIED_RESULT",
            "U11-U15",
            {"partitions": partitions, "selected": sel, "transferred": transferred is not None},
        )
        return {
            "remaining": list(remaining.keys()),
            "partitions": partitions,
            "selected_feature": sel["feature"],
            "used_learned_method": sel["used_method"],
            "transferred_from_memory": transferred is not None,
        }

    def assist_and_learn(
        self,
        session_id: str,
        agent_id: str,
        candidates: dict[str, dict[str, Any]],
        observed: dict[str, Any],
        history: list[dict[str, Any]],
        *,
        target_block: tuple[str, int] = ("BIG49", 0),
    ) -> dict[str, Any]:
        """Use limited AI hint + U11/U13/U14 to learn optimal profile and persist to Big49/Small49."""
        self.continuity.start_session(session_id, "U11-U14 assisted learning")
        eval_res = OPERATIONS["U13"](candidates, observed)
        partitions = OPERATIONS["U11"](eval_res["remaining"], observed)

        hint_text = "No gateway configured"
        if self.gateway is not None:
            adv = self.gateway.request_advisory(
                f"Provide a diagnostic hint for discriminating partitions: {partitions}"
            )
            hint_text = adv.text
            self.continuity.record_event(
                session_id, "MODEL_OUTPUT", adv.provider, {"hint": hint_text, "authority": 0}
            )

        best_profile = OPERATIONS["U14"](history)
        if best_profile is None:
            raise ValueError("U14 could not identify an improving profile from history")

        family, idx = target_block
        commit_rec, ptr = self.suit.execute_memory_write(
            agent_id,
            family,
            idx,
            {
                "learned_profile": best_profile,
                "hint_summary": hint_text[:120],
                "relation": "ASSOCIATED_WITH",
            },
        )
        self.continuity.record_event(
            session_id,
            "EXPERIENCE_REF",
            "U14",
            {"learned_profile": best_profile, "block": f"{family}:{idx}", "revision": commit_rec["revision"]},
        )
        self.continuity.complete_session(session_id)
        # FIX-REV2 (brief 2.3): return both references. The learner consumes the
        # structured COOL payload; the mirror reference is a txid-reference
        # projection that cannot reconstruct it (see mirror_reference).
        mirror_ref = commit_rec.get("mirror_reference") or {}
        return {
            "learned_profile": best_profile,
            "pointer": ptr.record_id,
            "revision": commit_rec["revision"],
            "cool_reference": commit_rec.get("cool_reference"),
            "mirror_reference": mirror_ref,
            "projection_kind": mirror_ref.get("projection_kind"),
        }
