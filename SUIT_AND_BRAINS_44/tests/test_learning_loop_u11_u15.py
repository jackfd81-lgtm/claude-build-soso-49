"""Closed U11–U15 Assisted Learning, Unassisted Retry, & Unseen Transfer tests."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from build040_runtime import (
    OfflineAdvisoryProvider,
    OnlineAIGateway,
    ReplayProvider,
    SoSoContinuity,
    SoSoSuitRuntime,
)
from build040_runtime.learning_loop import AssistedLearningLoop
from crampu.build030_bridge import Merged49BrainsCramAdapter
from crampu.controller import CRAMPUMemoryController

ADMIN = "learning-loop-admin-secret-040"


class TestLearningLoopU11U15(unittest.TestCase):
    def test_baseline_assisted_unassisted_retry_and_unseen_transfer(self) -> None:
        # Candidates where 'f_bad' has partition [3] (no split) or [1,1,1] vs optimal [1,2]
        # Note: U12 falls back to min(partitions) alphabetically when preference is None,
        # so put 'a_suboptimal' first alphabetically with [1, 1, 1] and 'z_optimal' with [1, 2].
        candidates = {
            "H1": {"a_suboptimal": 1, "z_optimal": "yes"},
            "H2": {"a_suboptimal": 2, "z_optimal": "no"},
            "H3": {"a_suboptimal": 3, "z_optimal": "no"},
        }
        history = [
            {"before": 3, "after": 3, "profile": [1, 1, 1]},
            {"before": 3, "after": 1, "profile": [1, 2]},
        ]

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ctrl = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
            ctrl.register_block_owner(0, "L01", ADMIN)
            adapter = Merged49BrainsCramAdapter(ctrl, root / "brains")
            cont = SoSoContinuity(root / "soso.sqlite3")
            suit = SoSoSuitRuntime(adapter)
            suit.authorize_agent("L01", {"memory.read", "memory.write"}, allowed_block_ids={0})

            rp = ReplayProvider()
            rp.record(
                "Provide a diagnostic hint for discriminating partitions: {'a_suboptimal': [1, 1, 1], 'z_optimal': [1, 2]}",
                "Prefer a binary [1, 2] split to isolate H1 cleanly.",
            )
            gw = OnlineAIGateway(primary=rp, fallback=OfflineAdvisoryProvider())
            loop = AssistedLearningLoop(cont, suit, gateway=gw)

            # 1. Baseline (unassisted, no memory) -> picks 'a_suboptimal', used_learned_method=False
            base = loop.run_unassisted("ll-base", "L01", candidates, {})
            self.assertEqual(base["selected_feature"], "a_suboptimal")
            self.assertFalse(base["used_learned_method"])

            # 2. Assisted learning (limited AI hint + U14 + Big49 persistence)
            learned = loop.assist_and_learn("ll-assist", "L01", candidates, {}, history, target_block=("BIG49", 0))
            self.assertEqual(learned["learned_profile"], [1, 2])

            # 3. Unassisted retry (NO gateway!) -> U15 transfers [1, 2] from Big49 -> selects 'z_optimal'
            offline_loop = AssistedLearningLoop(cont, suit, gateway=None)
            retry = offline_loop.run_unassisted("ll-retry", "L01", candidates, {}, memory_block=("BIG49", 0))
            self.assertTrue(retry["transferred_from_memory"])
            self.assertTrue(retry["used_learned_method"])
            self.assertEqual(retry["selected_feature"], "z_optimal")

            # 4. Unseen domain transfer (completely new feature names & hypotheses, NO gateway!)
            unseen_candidates = {
                "Fault_X": {"aa_noisy": "r1", "zz_discrim": "high"},
                "Fault_Y": {"aa_noisy": "r2", "zz_discrim": "low"},
                "Fault_Z": {"aa_noisy": "r3", "zz_discrim": "low"},
            }
            unseen = offline_loop.run_unassisted("ll-unseen", "L01", unseen_candidates, {}, memory_block=("BIG49", 0))
            self.assertTrue(unseen["transferred_from_memory"])
            self.assertTrue(unseen["used_learned_method"])
            self.assertEqual(unseen["selected_feature"], "zz_discrim")

            adapter.close(); ctrl.close(); cont.close()


if __name__ == "__main__":
    unittest.main()
