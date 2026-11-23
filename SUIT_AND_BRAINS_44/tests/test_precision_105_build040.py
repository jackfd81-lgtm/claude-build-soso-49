"""Portable tests for Build 040 Precision105Constructor (no hardcoded paths or answer banks)."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from build040_runtime import (
    Brains49Runtime,
    OfflineAdvisoryProvider,
    OnlineAIGateway,
    ReplayProvider,
    SoSoContinuity,
    SoSoSuitRuntime,
)
from build040_runtime.precision_105 import BlockedGenerationError, Precision105Constructor
from crampu.build030_bridge import Merged49BrainsCramAdapter
from crampu.controller import CRAMPUMemoryController

ADMIN = "precision-105-admin-secret-0400"
CHALLENGE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "hist_geo_time_105_v1_public.json"


class TestPrecision105Build040(unittest.TestCase):
    def _setup(self, td: str, gw: OnlineAIGateway):
        root = Path(td)
        ctrl = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
        for idx in range(14):
            ctrl.register_block_owner(idx, "L01", ADMIN)
        ctrl.register_block_owner(1600, "L01", ADMIN)
        adapter = Merged49BrainsCramAdapter(ctrl, root / "brains")
        cont = SoSoContinuity(root / "soso.sqlite3")
        suit = SoSoSuitRuntime(adapter)
        suit.authorize_agent("L01", {"memory.read", "memory.write"}, allowed_block_ids=set(range(14)) | {1600})
        brains = Brains49Runtime(cont, suit, root / "muse_state")
        ctor = Precision105Constructor(cont, suit, brains, gateway=gw)
        return adapter, ctrl, cont, brains, ctor

    def test_offline_gateway_raises_blocked_rather_than_hardcoding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            gw = OnlineAIGateway(primary=OfflineAdvisoryProvider())
            adapter, ctrl, cont, _, ctor = self._setup(td, gw)
            with self.assertRaises(BlockedGenerationError):
                ctor.construct(CHALLENGE_PATH, session_id="p105-blocked", agent_id="L01")
            adapter.close(); ctrl.close(); cont.close()

    def test_gateway_output_is_actually_consumed_and_validated(self) -> None:
        challenge = json.loads(CHALLENGE_PATH.read_text(encoding="utf-8"))
        anchors = [p for p in challenge["positions"] if p["kind"] == "ANCHOR"]
        open_specs = [p for p in challenge["positions"] if p["kind"] != "ANCHOR"]
        prompt = json.dumps({
            "challenge_id": challenge["id"],
            "word_min": challenge["word_min"],
            "word_max": challenge["word_max"],
            "anchors": [{"position": a["position"], "text": a["text"]} for a in anchors],
            "open_positions": [o["position"] for o in open_specs],
        }, sort_keys=True)
        # Synthetic replay payload proving constructor consumes gateway text (not a hardcoded bank)
        fake_generated = {str(o["position"]): f"Gateway generated line {o['position']} words" for o in open_specs}
        rp = ReplayProvider()
        rp.record(prompt, json.dumps(fake_generated))

        with tempfile.TemporaryDirectory() as td:
            gw = OnlineAIGateway(primary=rp)
            adapter, ctrl, cont, brains, ctor = self._setup(td, gw)
            out = ctor.construct(CHALLENGE_PATH, session_id="p105-replay", agent_id="L01")
            self.assertEqual(len(out["response"]["positions"]), 105)
            self.assertEqual(out["response"]["positions"][1]["text"], "Gateway generated line 2 words")
            self.assertTrue(out["execution_trace"]["external_ai_used"])
            self.assertEqual(out["execution_trace"]["generation_source"], "replay_cache")
            self.assertEqual(brains.utilization_metrics(ADMIN)["big49_mirrored"], 14)
            adapter.close(); ctrl.close(); cont.close()


if __name__ == "__main__":
    unittest.main()
