"""105-Position Historical Precision Construction Interface for Suit and Brains Build 040.

Removes hardcoded answer banks: open positions MUST be constructed dynamically from
OnlineAIGateway responses. If the gateway is offline or falls back to
OfflineAdvisoryProvider, raises BlockedGenerationError rather than silently
inserting predetermined text.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .brains_49_runtime import Brains49Runtime
from .online_ai import AIResponse, OnlineAIGateway
from .soso_continuity import SoSoContinuity
from .suit_runtime import SoSoSuitRuntime


class BlockedGenerationError(RuntimeError):
    """Raised when no live or replay generative provider can supply open positions."""


def _anchor_year_iso(anchor_text: str) -> str:
    part = anchor_text.split(",")[-1].strip()
    num_str, era = part.split()
    year = int(num_str)
    signed = -year if era.upper() == "BCE" else year
    shifted = signed + 2000
    return f"{shifted:04d}-01-01T00:00:00+00:00"


class Precision105Constructor:
    """Constructs 105-position responses using Build 040 Brains, SoSo, Suit, CRAM-PU, and OnlineAIGateway."""

    def __init__(
        self,
        continuity: SoSoContinuity,
        suit: SoSoSuitRuntime,
        brains: Brains49Runtime,
        gateway: OnlineAIGateway | None = None,
    ) -> None:
        self.continuity = continuity
        self.suit = suit
        self.brains = brains
        self.gateway = gateway

    def construct(
        self,
        challenge_path: Path,
        *,
        session_id: str = "precision-105-run",
        agent_id: str = "L01",
    ) -> dict[str, Any]:
        challenge = json.loads(Path(challenge_path).read_text(encoding="utf-8"))
        cid = challenge.get("id") or challenge.get("challenge_id")
        word_min = int(challenge.get("word_min") or challenge.get("open_word_min") or 4)
        word_max = int(challenge.get("word_max") or challenge.get("open_word_max") or 9)
        self.continuity.start_session(session_id, f"Construct {cid}")

        anchors = [p for p in challenge["positions"] if p["kind"] == "ANCHOR"]
        open_specs = [p for p in challenge["positions"] if p["kind"] != "ANCHOR"]
        functions_executed: list[str] = []
        memory_reads = 0
        memory_writes = 0

        # 1. Store all locked anchors in Big49 via SoSo Suit -> CRAM-PU -> Big49
        for idx, anc in enumerate(anchors):
            self.continuity.record_event(session_id, "OBSERVATION", "challenge_public", anc)
            self.suit.execute_memory_write(
                agent_id,
                "BIG49",
                idx,
                {"position": anc["position"], "anchor": anc["text"], "relation": "ASSOCIATED_WITH"},
            )
            self.brains.mark_exercised("BIG49", idx)
            memory_writes += 1

        # 2. Dispatch Lower49 L22 temporal chronology check across consecutive anchor pairs
        for i in range(len(anchors) - 1):
            a1, a2 = anchors[i], anchors[i + 1]
            res = self.brains.dispatch_lower49(
                session_id,
                "L22",
                {
                    "pathway": "B",
                    "query": {"subject": "era_order", "predicate": "latest_anchor"},
                    "observations": [
                        {"source": "anchor", "subject": "era_order", "predicate": "latest_anchor", "value": f"pos_{a1['position']}", "event": _anchor_year_iso(a1["text"])},
                        {"source": "anchor", "subject": "era_order", "predicate": "latest_anchor", "value": f"pos_{a2['position']}", "event": _anchor_year_iso(a2["text"])},
                    ],
                },
            )
            functions_executed.append("L22")
            if res["decision"].get("answer") != f"pos_{a2['position']}":
                raise ValueError(f"Chronology failure between {a1['text']} and {a2['text']}")

        # 3. Dispatch Higher49 U09 structural readiness check
        u09_res = self.brains.dispatch_higher49(
            session_id, "U09", {"obs_id": session_id, "facts": {"current": "high", "rotation": 0}}
        )
        functions_executed.append("U09")

        # 4. Read back anchors from Big49
        recalled_anchors: dict[int, str] = {}
        for idx, anc in enumerate(anchors):
            rec = self.suit.execute_memory_read(agent_id, "BIG49", idx)
            memory_reads += 1
            recalled_anchors[anc["position"]] = rec["payload"]["anchor"]

        # 5. Request open positions dynamically from OnlineAIGateway (no hardcoded answer bank)
        if self.gateway is None:
            raise BlockedGenerationError("OnlineAIGateway not configured; cannot generate open positions")

        prompt = json.dumps({
            "challenge_id": cid,
            "word_min": word_min,
            "word_max": word_max,
            "anchors": [{"position": a["position"], "text": a["text"]} for a in anchors],
            "open_positions": [o["position"] for o in open_specs],
        }, sort_keys=True)
        adv: AIResponse = self.gateway.request_advisory(prompt)
        self.continuity.record_event(
            session_id,
            "MODEL_OUTPUT",
            adv.provider,
            {"request_digest": adv.request_digest, "fallback_used": adv.fallback_used, "from_replay": adv.from_replay},
        )
        if adv.fallback_used:
            raise BlockedGenerationError("OnlineAIGateway fell back to OfflineAdvisoryProvider; live/replay model required")

        generated_map_raw = json.loads(adv.text)
        generated_map = {int(k): str(v).strip() for k, v in generated_map_raw.items()}

        positions_out: list[dict[str, Any]] = []
        for spec in challenge["positions"]:
            pos = spec["position"]
            if spec["kind"] == "ANCHOR":
                positions_out.append({"position": pos, "text": recalled_anchors[pos]})
            else:
                if pos not in generated_map:
                    raise ValueError(f"Gateway output missing open position {pos}")
                txt = generated_map[pos]
                wc = len(txt.split())
                if not (word_min <= wc <= word_max):
                    raise ValueError(f"Gateway output at position {pos} has {wc} words (expected {word_min}..{word_max})")
                positions_out.append({"position": pos, "text": txt})

        # 6. Persist completion summary into Small49 (index 0 -> block 1600)
        self.suit.execute_memory_write(
            agent_id,
            "SMALL49",
            0,
            {"challenge_id": cid, "positions": len(positions_out), "provider": adv.provider, "relation": "ASSOCIATED_WITH"},
        )
        self.brains.mark_exercised("SMALL49", 0)
        memory_writes += 1
        self.continuity.complete_session(session_id)

        return {
            "response": {"id": cid, "positions": positions_out},
            "execution_trace": {
                "session_id": session_id,
                "functions_executed": sorted(set(functions_executed)),
                "l22_chronology_checks": len(anchors) - 1,
                "u09_events": len(u09_res["events"]),
                "memory_writes": memory_writes,
                "memory_reads": memory_reads,
                "generation_source": adv.provider,
                "external_ai_used": not adv.fallback_used,
            },
        }
