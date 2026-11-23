"""42.1.1 pre-commit capacity check: a write whose mirror bucket is already
full is rejected in the bridge preflight, before any HOT/WARM/COOL mutation.

Previously (42.1.0) the saturation sweep showed a bridge-level +1 write
committing COOL revision 2 and only failing at mirror time, contained as a
BLOCKED_PERMANENT row. The mirror-time CAPACITY_EXHAUSTED from
MemoryStore.put remains as the backstop for writes that bypass the bridge
(e.g. direct controller commits); the drain still contains those.

Disposable state only. Run: python3 -m unittest tests.test_build42_capacity_check -v
"""
import tempfile
import unittest
from pathlib import Path

from build040_runtime.standalone import local_runtime
from realcog.ph6_brains_2208 import Rejected

ADMIN = "capacity-check-admin-token"

# bridge_macro: SMALL49 indices 448..457 (global 2048..2057), 10 slots, cap 1000.
MACRO_INDICES = list(range(448, 458))


def _setup(ctrl, closet, blocks):
    for block_id in blocks:
        ctrl.register_block_owner(block_id, "L01", ADMIN)
    closet.suit.authorize_agent(
        "L01", {"task.run", "memory.read", "memory.write"},
        allowed_block_ids=set(blocks),
    )


def _fill_macro(adapter):
    for index in MACRO_INDICES:
        adapter.write_and_sync(
            "L01", "SMALL49", index, {"content": "x" * 1000},
            bucket="bridge_macro",
        )


class CapacityCheckTests(unittest.TestCase):
    def test_plus_one_new_block_rejected_before_cool(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                blocks = [1600 + i for i in MACRO_INDICES] + [2058]
                _setup(ctrl, closet, blocks)
                _fill_macro(adapter)
                self.assertEqual(adapter.small.count("bridge_macro"), 10)
                hot_before = ctrl.inspect(ADMIN)["hot"]
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "SMALL49", 458, {"content": "y" * 1000},
                        bucket="bridge_macro",
                    )
                self.assertEqual(str(ctx.exception), "CAPACITY_EXHAUSTED")
                # Nothing was committed for the new block: no COOL revision,
                # no outbox row, and no new HOT entry (HOT still holds only
                # the 10 fills — the rejected write added nothing).
                self.assertEqual(ctrl._revision(2058), 0)
                insp = ctrl.inspect(ADMIN)
                self.assertEqual(insp["outbox_pending"], 0)
                self.assertEqual(insp.get("outbox_blocked"), 0)
                self.assertEqual(insp["hot"], hot_before)
                self.assertEqual(adapter.small.count("bridge_macro"), 10)

    def test_plus_one_same_block_rejected_before_cool(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600 + i for i in MACRO_INDICES])
                _fill_macro(adapter)
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "SMALL49", 448, {"content": "z" * 1000},
                        bucket="bridge_macro",
                    )
                self.assertEqual(str(ctx.exception), "CAPACITY_EXHAUSTED")
                # The earlier revision is untouched: still revision 1.
                self.assertEqual(ctrl._revision(2048), 1)
                insp = ctrl.inspect(ADMIN)
                self.assertEqual(insp["outbox_pending"], 0)
                self.assertEqual(insp.get("outbox_blocked"), 0)

    def test_partial_bucket_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600 + i for i in MACRO_INDICES])
                for index in MACRO_INDICES[:9]:
                    adapter.write_and_sync(
                        "L01", "SMALL49", index, {"content": "x" * 1000},
                        bucket="bridge_macro",
                    )
                # The 10th slot still accepts a write.
                commit, _ptr = adapter.write_and_sync(
                    "L01", "SMALL49", 457, {"content": "x" * 1000},
                    bucket="bridge_macro",
                )
                self.assertEqual(commit["revision"], 1)
                self.assertEqual(adapter.small.count("bridge_macro"), 10)
                # The 11th does not.
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "SMALL49", 448, {"content": "z" * 1000},
                        bucket="bridge_macro",
                    )
                self.assertEqual(str(ctx.exception), "CAPACITY_EXHAUSTED")

    def test_capacity_check_is_per_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600 + i for i in MACRO_INDICES] + [1600])
                _fill_macro(adapter)
                # A full bridge_macro does not block an unrelated bucket.
                commit, _ptr = adapter.write_and_sync(
                    "L01", "SMALL49", 0, {"content": "history-ok"},
                    bucket="history",
                )
                self.assertEqual(commit["revision"], 1)
                self.assertEqual(adapter.small.count("history"), 1)

    def test_queued_row_reserves_a_slot(self):
        """A PENDING outbox row still needs a mirror slot when the drain
        runs, so it counts against the bucket. With 9 mirrored + 1 queued,
        the bucket is effectively full even though the store shows 9."""
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600 + i for i in MACRO_INDICES] + [2058])
                for index in MACRO_INDICES[:9]:
                    adapter.write_and_sync(
                        "L01", "SMALL49", index, {"content": "x" * 1000},
                        bucket="bridge_macro",
                    )
                self.assertEqual(adapter.small.count("bridge_macro"), 9)

                class Interrupted(Exception):
                    pass

                def stop_after_cool(stage):
                    if stage == "after_cool_before_mirror":
                        raise Interrupted()

                # Queue a 10th write without draining it.
                with self.assertRaises(Interrupted):
                    adapter.write_and_sync(
                        "L01", "SMALL49", 457, {"content": "x" * 1000},
                        bucket="bridge_macro", fault_hook=stop_after_cool,
                    )
                self.assertEqual(ctrl._revision(2057), 1)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 1)
                # Store shows 9, but the queued row holds the last slot.
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "SMALL49", 458, {"content": "y" * 1000},
                        bucket="bridge_macro",
                    )
                self.assertEqual(str(ctx.exception), "CAPACITY_EXHAUSTED")
                self.assertEqual(ctrl._revision(2058), 0)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 1)
                # Draining the queued row mirrors it; the bucket is now
                # genuinely full, so the write is still rejected afterwards.
                adapter.sync_pending_outbox()
                self.assertEqual(adapter.small.count("bridge_macro"), 10)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "SMALL49", 458, {"content": "y" * 1000},
                        bucket="bridge_macro",
                    )
                self.assertEqual(str(ctx.exception), "CAPACITY_EXHAUSTED")

    def test_interleaved_claim_from_fault_hook_is_rejected(self):
        """A claim made from inside another write's fault hook — while the
        first write is mid-flight with its row queued — is rejected by the
        queued reservation, not left to fail at mirror time. The first write
        then completes cleanly with no BLOCKED row and no orphaned revision."""
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600 + i for i in MACRO_INDICES] + [2058])
                for index in MACRO_INDICES[:9]:
                    adapter.write_and_sync(
                        "L01", "SMALL49", index, {"content": "x" * 1000},
                        bucket="bridge_macro",
                    )
                outcomes = []

                def hook(stage):
                    if stage == "after_cool_before_mirror":
                        try:
                            adapter.write_and_sync(
                                "L01", "SMALL49", 458,
                                {"content": "y" * 1000},
                                bucket="bridge_macro",
                            )
                            outcomes.append("NOT_REJECTED")
                        except Rejected as exc:
                            outcomes.append(str(exc))
                        except Exception as exc:  # noqa: BLE001
                            outcomes.append(f"{type(exc).__name__}: {exc}")

                commit, _ptr = adapter.write_and_sync(
                    "L01", "SMALL49", 457, {"content": "x" * 1000},
                    bucket="bridge_macro", fault_hook=hook,
                )
                self.assertEqual(commit["revision"], 1)
                self.assertEqual(outcomes, ["CAPACITY_EXHAUSTED"])
                self.assertEqual(ctrl._revision(2058), 0)
                self.assertEqual(adapter.small.count("bridge_macro"), 10)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)
                self.assertEqual(ctrl.inspect(ADMIN).get("outbox_blocked"), 0)

    def test_mirror_time_backstop_still_contains_direct_overflow(self):
        """A write that bypasses the bridge preflight (direct controller
        commit) can still commit to COOL and fail at mirror time; the drain
        must contain it as BLOCKED_PERMANENT without blocking valid rows."""
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600 + i for i in MACRO_INDICES] + [2058, 1600])
                _fill_macro(adapter)
                # Legacy-style direct commit into the full bucket.
                ctrl.write_hot("L01", 2058, {"content": "direct-overflow"})
                txid = ctrl.stage_to_warm("L01", 2058)
                ctrl.commit_to_cool(
                    "L01", txid, enqueue_mirror=True, mirror_bucket="bridge_macro"
                )
                self.assertEqual(ctrl._revision(2058), 1)
                # A valid write to another bucket drains the outbox.
                adapter.write_and_sync(
                    "L01", "SMALL49", 0, {"content": "history-ok"},
                    bucket="history",
                )
                self.assertEqual(adapter.small.count("history"), 1)
                blocked = ctrl.list_blocked_outbox()
                self.assertEqual(len(blocked), 1)
                self.assertEqual(blocked[0][1], 2058)
                self.assertEqual(blocked[0][7], "CAPACITY_EXHAUSTED")
                self.assertEqual(ctrl.inspect(ADMIN).get("outbox_blocked"), 1)
