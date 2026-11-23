"""All 20 required failure-injection and end-to-end cognitive pathway tests for Build 030.1 CRAM-PU."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crampu.build030_bridge import Merged49BrainsCramAdapter
from crampu.controller import (
    AuthorizationError,
    CRAMPUMemoryController,
    ConflictError,
    IntegrityError,
)
from muse024 import call as muse_call, initialize as muse_initialize

ADMIN = "qual-candidate-admin-secret-0301"


class SimulatedCrash(RuntimeError):
    pass


class TestFailureInjection20(unittest.TestCase):
    def _setup(self, td: str, *, max_hot_entries: int = 256):
        root = Path(td)
        ctrl = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN, max_hot_entries=max_hot_entries)
        ctrl.register_block_owner(0, "L01", ADMIN)
        ctrl.register_block_owner(1, "L02", ADMIN)
        ctrl.register_block_owner(1600, "L01", ADMIN)
        merged = Merged49BrainsCramAdapter(ctrl, root / "brains")
        return root, ctrl, merged

    # 1. HOT memory loss
    def test_01_hot_memory_loss(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            merged.write_and_sync("L01", "BIG49", 0, {"content": "durable"})
            ctrl.write_hot("L01", 0, {"content": "volatile-uncommitted"})
            self.assertTrue(ctrl.purge_hot_state(0, "L01"))
            loaded = merged.read("L01", "BIG49", 0)
            self.assertEqual(loaded["payload"]["content"], "durable")
            merged.close()
            ctrl.close()

    # 2. WARM interruption
    def test_02_warm_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, ctrl, merged = self._setup(td)
            merged.write_and_sync("L01", "BIG49", 0, {"content": "v1"})
            ctrl.write_hot("L01", 0, {"content": "v2-interrupted"})
            tx = ctrl.stage_to_warm("L01", 0)
            self.assertTrue((root / "warm" / f"{tx}.json").exists())
            ctrl.close()
            ctrl2 = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
            self.assertEqual(ctrl2.load_from_cool(0, "L01")["payload"]["content"], "v1")
            ctrl2.close()
            merged.close()

    # 3. COOL transaction interruption
    def test_03_cool_transaction_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            merged.write_and_sync("L01", "BIG49", 0, {"content": "before-crash"})
            ctrl.write_hot("L01", 0, {"content": "aborted-cool"})
            tx = ctrl.stage_to_warm("L01", 0)
            with self.assertRaises(SimulatedCrash):
                ctrl.commit_to_cool("L01", tx, enqueue_mirror=True, fault_hook=lambda s: (_ for _ in ()).throw(SimulatedCrash(s)))
            self.assertEqual(ctrl.load_from_cool(0, "L01")["payload"]["content"], "before-crash")
            self.assertEqual(ctrl.list_outbox(), [])
            merged.close()
            ctrl.close()

    # 4. Controller restart
    def test_04_controller_restart(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, ctrl, merged = self._setup(td)
            merged.write_and_sync("L01", "SMALL49", 0, {"content": "survives-restart"})
            merged.close()
            ctrl.close()
            ctrl2 = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
            merged2 = Merged49BrainsCramAdapter(ctrl2, root / "brains")
            self.assertEqual(merged2.read("L01", "SMALL49", 0)["payload"]["content"], "survives-restart")
            merged2.verify_all()
            merged2.close()
            ctrl2.close()

    # 5. Unauthorized reads
    def test_05_unauthorized_reads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            merged.write_and_sync("L01", "BIG49", 0, {"content": "private"})
            with self.assertRaises(AuthorizationError):
                merged.read("L02", "BIG49", 0)
            merged.close()
            ctrl.close()

    # 6. Unauthorized writes
    def test_06_unauthorized_writes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            with self.assertRaises(AuthorizationError):
                merged.write_and_sync("L02", "BIG49", 0, {"content": "tamper"})
            merged.close()
            ctrl.close()

    # 7. Corrupted memory payloads
    def test_07_corrupted_memory_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            merged.write_and_sync("L01", "BIG49", 0, {"content": "clean"})
            ctrl._db.execute("UPDATE blocks SET record = ? WHERE block_id = 0", (b'{"corrupt":true}',))
            with self.assertRaises(IntegrityError):
                ctrl.load_from_cool(0, "L01")
            merged.close()
            ctrl.close()

    # 8. Modified ownership metadata
    def test_08_modified_ownership_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            merged.write_and_sync("L01", "BIG49", 0, {"content": "owned-by-l01"})
            # Tamper owners table directly in SQLite to L02 -> cross-check with transitions.agent_id catches it
            ctrl._db.execute("UPDATE owners SET agent_id = 'L02' WHERE block_id = 0")
            with self.assertRaises(IntegrityError):
                ctrl.load_from_cool(0, "L02")
            merged.close()
            ctrl.close()

    # 9. Concurrent updates
    def test_09_concurrent_updates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            ctrl.write_hot("L01", 0, {"content": "c1"})
            tx1 = ctrl.stage_to_warm("L01", 0)
            ctrl.write_hot("L01", 0, {"content": "c2"})
            tx2 = ctrl.stage_to_warm("L01", 0)
            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(ctrl.commit_to_cool, "L01", tx1)
                f2 = pool.submit(ctrl.commit_to_cool, "L01", tx2)
                outcomes = []
                for f in (f1, f2):
                    try:
                        outcomes.append(("OK", f.result()))
                    except ConflictError:
                        outcomes.append(("CONFLICT", None))
            self.assertEqual(sorted(x[0] for x in outcomes), ["CONFLICT", "OK"])
            merged.close()
            ctrl.close()

    # 10. Stale revision writes
    def test_10_stale_revision_writes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            ctrl.write_hot("L01", 0, {"content": "base"})
            tx_stale = ctrl.stage_to_warm("L01", 0)
            merged.write_and_sync("L01", "BIG49", 0, {"content": "newer"})
            with self.assertRaises(ConflictError):
                ctrl.commit_to_cool("L01", tx_stale)
            merged.close()
            ctrl.close()

    # 11. Duplicate transaction replay
    def test_11_duplicate_transaction_replay(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            ctrl.write_hot("L01", 0, {"content": "once"})
            tx = ctrl.stage_to_warm("L01", 0)
            r1 = ctrl.commit_to_cool("L01", tx, enqueue_mirror=True)
            r2 = ctrl.commit_to_cool("L01", tx, enqueue_mirror=True)
            self.assertFalse(r1["replayed"])
            self.assertTrue(r2["replayed"])
            self.assertEqual(r1["revision"], r2["revision"])
            self.assertEqual(len(ctrl.transitions(ADMIN)), 1)
            merged.close()
            ctrl.close()

    # 12. Missing brain-pool mirrors
    def test_12_missing_brain_pool_mirrors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            _, ptr = merged.write_and_sync("L01", "BIG49", 0, {"content": "mirrored"})
            # Wipe big49 blocks table simulating lost mirror
            merged.big.db.execute("DELETE FROM blocks")
            merged.big.db.execute("DELETE FROM events")
            self.assertEqual(merged.big.count(), 0)
            recovered = merged.recover_missing_mirrors()
            self.assertEqual(len(recovered), 1)
            self.assertEqual(merged.big.read(ptr, principal="L01")["content"], "mirrored")
            merged.close()
            ctrl.close()

    # 13. Crash after COOL commit but before mirror synchronization
    def test_13_crash_after_cool_before_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, ctrl, merged = self._setup(td)

            def crash_before_mirror(stage: str) -> None:
                if stage == "after_cool_before_mirror":
                    raise SimulatedCrash(stage)

            with self.assertRaises(SimulatedCrash):
                merged.write_and_sync("L01", "BIG49", 0, {"content": "pending-outbox"}, fault_hook=crash_before_mirror)

            # COOL committed, Big49 still 0, outbox has 1 PENDING
            self.assertEqual(merged.big.count(), 0)
            self.assertEqual(len(ctrl.list_outbox(("PENDING",))), 1)
            merged.close()
            ctrl.close()

            # Restart and recover pending outbox
            ctrl2 = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
            merged2 = Merged49BrainsCramAdapter(ctrl2, root / "brains")
            synced = merged2.sync_pending_outbox()
            self.assertEqual(len(synced), 1)
            self.assertEqual(merged2.big.count(), 1)
            self.assertEqual(ctrl2.list_outbox(("PENDING", "APPLIED")), [])
            merged2.close()
            ctrl2.close()

    # 14. Crash after mirror synchronization but before acknowledgment
    def test_14_crash_after_mirror_before_ack(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, ctrl, merged = self._setup(td)

            def crash_before_ack(stage: str) -> None:
                if stage == "after_mirror_before_ack":
                    raise SimulatedCrash(stage)

            with self.assertRaises(SimulatedCrash):
                merged.write_and_sync("L01", "BIG49", 0, {"content": "applied-not-acked"}, fault_hook=crash_before_ack)

            self.assertEqual(merged.big.count(), 1)
            self.assertEqual(len(ctrl.list_outbox(("APPLIED",))), 1)
            merged.close()
            ctrl.close()

            # Restart recovery acknowledges idempotently without duplicate write
            ctrl2 = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
            merged2 = Merged49BrainsCramAdapter(ctrl2, root / "brains")
            synced = merged2.sync_pending_outbox()
            self.assertEqual(len(synced), 1)
            self.assertEqual(merged2.big.count(), 1)
            self.assertEqual(ctrl2.list_outbox(("PENDING", "APPLIED")), [])
            merged2.close()
            ctrl2.close()

    # 15. Disk-full conditions
    def test_15_disk_full_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            ctrl.write_hot("L01", 0, {"content": "enospc-test"})
            with mock.patch("crampu.controller.durable_write", side_effect=OSError(errno.ENOSPC, "No space left on device")):
                with self.assertRaises(OSError) as ctx:
                    ctrl.stage_to_warm("L01", 0)
                self.assertEqual(ctx.exception.errno, errno.ENOSPC)
            self.assertEqual(ctrl.inspect(ADMIN)["committed"], 0)
            merged.close()
            ctrl.close()

    # 16. Invalid block IDs
    def test_16_invalid_block_ids(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            for bad in (-1, 2208, "0", 1.5, True):
                with self.assertRaises((ValueError, AuthorizationError)):
                    ctrl.write_hot("L01", bad, {"content": "bad"})
            with self.assertRaises(ValueError):
                merged.write_and_sync("L01", "BIG49", 1600, {"content": "oob"})
            with self.assertRaises(ValueError):
                merged.write_and_sync("L01", "SMALL49", 608, {"content": "oob"})
            merged.close()
            ctrl.close()

    # 17. Invalid agent identities
    def test_17_invalid_agent_identities(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            for bad_agent in ("", "L00", "L50", "ADMIN", "root"):
                with self.assertRaises(AuthorizationError):
                    merged.write_and_sync(bad_agent, "BIG49", 0, {"content": "bad-agent"})
            merged.close()
            ctrl.close()

    # 18. OD-05 rejection
    def test_18_od05_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            for bad_payload in (
                {"content": "no basis", "relation": "CAUSES", "causal_basis": None},
                {"content": "undetermined", "relation": "CAUSES", "causal_basis": "UNDETERMINED"},
                {"content": "promo", "relation": "ASSOCIATED_WITH", "promotes_to": "CAUSES", "causal_basis": "x"},
                {"content": "promo2", "relation": "PROMOTES_TO_CAUSES", "causal_basis": "x"},
                {"content": "bad rel", "relation": "INVENTED_REL"},
            ):
                with self.assertRaises(AuthorizationError):
                    merged.write_and_sync("L01", "BIG49", 0, bad_payload)
            merged.close()
            ctrl.close()

    # 19. Authority escalation attempts
    def test_19_authority_escalation_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, merged = self._setup(td)
            with self.assertRaises(AuthorizationError):
                merged.write_and_sync("L01", "BIG49", 0, {"content": "esc", "authority": 1})
            with self.assertRaises(AuthorizationError):
                ctrl.register_block_owner(2, "L01", "wrong-admin-token")
            merged.close()
            ctrl.close()

    # 20. Full end-to-end cognitive memory utilization
    def test_20_end_to_end_cognitive_pathway(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, ctrl, merged = self._setup(td, max_hot_entries=4)
            # Step 1: Seed prior context in Big49 via CRAM-PU + Suit
            merged.write_and_sync("L01", "BIG49", 0, {"content": "prior_pressure_normal", "relation": "ASSOCIATED_WITH"})
            retrieved = merged.read("L01", "BIG49", 0)
            self.assertEqual(retrieved["payload"]["content"], "prior_pressure_normal")

            # Step 2: Execute real Build 030.1 49-Brains pathway (muse024 start025)
            muse_dir = root / "muse_state"
            tok = muse_initialize(muse_dir)
            cog_res = muse_call(
                muse_dir,
                tok,
                {
                    "authority": 0,
                    "operation": "start025",
                    "run": "e2e-cram-pathway",
                    "arm": "A",
                    "problem": {
                        "pathway": "B",
                        "query": {"subject": "valve", "predicate": "state"},
                        "observations": [
                            {"source": "sensor_a", "subject": "valve", "predicate": "state", "value": "open", "event": "2026-11-22T01:00:00Z"},
                        ],
                    },
                },
            )
            self.assertEqual(cog_res["decision"]["status"], "SUPPORTED")

            # Step 3: Stage cognitive output to WARM -> COOL + outbox -> Small49 mirror -> subsequent retrieval
            commit_rec, ptr_small = merged.write_and_sync(
                "L01",
                "SMALL49",
                0,
                {
                    "content": f"decision={cog_res['decision']['answer']}",
                    "relation": "ASSOCIATED_WITH",
                    "prior_ref": f"big49-0-r{retrieved['revision']}",
                },
            )
            self.assertEqual(commit_rec["revision"], 1)
            recalled = merged.read("L01", "SMALL49", 0)
            self.assertEqual(recalled["payload"]["content"], "decision=open")
            self.assertEqual(merged.small.read(ptr_small, principal="L01")["authority"], 0)
            merged.verify_all()
            merged.close()
            ctrl.close()


if __name__ == "__main__":
    unittest.main()
