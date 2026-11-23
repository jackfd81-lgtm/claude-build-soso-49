"""Merged regression tests for CRAM-PU + Build 030.1 (Suit OD-05 gate & Big49/Small49)."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from crampu.build030_bridge import Merged49BrainsCramAdapter
from crampu.controller import AuthorizationError, CRAMPUMemoryController
from realcog.ph6_brains_2208 import Rejected


ADMIN = "merged-admin-secret-0301"


class TestMergedCrampuBuild0301(unittest.TestCase):
    def test_merged_big49_small49_write_sync_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ctrl = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
            ctrl.register_block_owner(0, "L01", ADMIN)
            ctrl.register_block_owner(1600, "L01", ADMIN)
            merged = Merged49BrainsCramAdapter(ctrl, root / "brains")

            rec_big, ptr_big = merged.write_and_sync(
                "L01", "BIG49", 0, {"content": "motor observation note", "relation": "ASSOCIATED_WITH"}
            )
            rec_small, ptr_small = merged.write_and_sync(
                "L01", "SMALL49", 0, {"content": "active working note", "relation": "ASSOCIATED_WITH"}
            )
            self.assertEqual(rec_big["revision"], 1)
            self.assertEqual(ptr_big.pool, "big")
            self.assertEqual(ptr_small.pool, "small")
            merged.verify_all()
            merged.close()
            ctrl.close()

            # Re-open after simulated restart
            ctrl2 = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
            merged2 = Merged49BrainsCramAdapter(ctrl2, root / "brains")
            merged2.verify_all()
            loaded = merged2.read("L01", "BIG49", 0)
            self.assertEqual(loaded["payload"]["content"], "motor observation note")
            self.assertEqual(loaded["authority_level"], "COGNITIVE_ADVISORY")
            self.assertEqual(merged2.big.read(ptr_big, principal="L01")["authority"], 0)
            merged2.close()
            ctrl2.close()

    def test_suit_od05_gate_blocks_unsupported_causes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ctrl = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
            ctrl.register_block_owner(5, "L02", ADMIN)
            merged = Merged49BrainsCramAdapter(ctrl, root / "brains")

            with self.assertRaises(AuthorizationError):
                merged.write_and_sync(
                    "L02", "BIG49", 5, {"content": "unsupported", "relation": "CAUSES", "causal_basis": "UNDETERMINED"}
                )
            self.assertEqual(merged.big.count(), 0)

            _, ptr = merged.write_and_sync(
                "L02", "BIG49", 5, {"content": "supported", "relation": "CAUSES", "causal_basis": "pressure_delta_log"}
            )
            self.assertEqual(merged.big.read(ptr, principal="L02")["causal_basis"], "pressure_delta_log")
            merged.close()
            ctrl.close()

    def test_non_principal_agent_and_cross_agent_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ctrl = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
            ctrl.register_block_owner(10, "L01", ADMIN)
            merged = Merged49BrainsCramAdapter(ctrl, root / "brains")

            # Invalid principal rejected by Suit capability check
            with self.assertRaises(AuthorizationError):
                merged.write_and_sync("not-a-principal", "BIG49", 10, {"content": "x"})

            # Cross-agent L02 cannot read L01 block
            merged.write_and_sync("L01", "BIG49", 10, {"content": "l01 secret"})
            with self.assertRaises(AuthorizationError):
                merged.read("L02", "BIG49", 10)
            merged.close()
            ctrl.close()


if __name__ == "__main__":
    unittest.main()
