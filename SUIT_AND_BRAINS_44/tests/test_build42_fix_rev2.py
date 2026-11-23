"""FIX-REV2 regression tests for the storage repairs (brief section 2).

Extends Fix Rev 1 (per-bucket mirror cap; bucket validated before COOL) with:
- a full preflight in write_and_sync (family/index/bucket/content, NFC +
  char/byte caps) before any HOT/WARM/COOL mutation;
- a non-blocking outbox drain: a permanent per-entry validation failure is
  marked BLOCKED_PERMANENT and the drain continues to unrelated rows, while
  order is preserved for the same address; legacy IntegrityError rows still
  fail closed (test_pending_legacy_entry_fails_closed);
- honest COOL / mirror references on writes, reads and learning results.

Disposable state only. Run: python3 -m unittest tests.test_build42_fix_rev2 -v
"""
import tempfile
import unittest
from pathlib import Path

from build040_runtime.standalone import local_runtime
from crampu.controller import AuthorizationError, ConflictError, IntegrityError
from realcog.ph6_brains_2208 import Rejected

ADMIN = "fix-rev2-admin-token-1234"


class Interrupted(Exception):
    pass


def stop_after_cool(stage):
    if stage == "after_cool_before_mirror":
        raise Interrupted()


def stop_after_mirror(stage):
    if stage == "after_mirror_before_ack":
        raise Interrupted()


def _setup(ctrl, closet, blocks):
    for block_id in blocks:
        ctrl.register_block_owner(block_id, "L01", ADMIN)
    closet.suit.authorize_agent(
        "L01", {"task.run", "memory.read", "memory.write"},
        allowed_block_ids=set(blocks),
    )


def _legacy_invalid_write(ctrl, block_id):
    """Create exactly the row RC1 would create for an invalid bucket: commit
    through the controller path, bypassing the bridge preflight."""
    ctrl.write_hot("L01", block_id, {"content": "legacy-bad"})
    txid = ctrl.stage_to_warm("L01", block_id)
    return ctrl.commit_to_cool(
        "L01", txid, enqueue_mirror=True, mirror_bucket="BUCKET_INVALID"
    )


class FixRev2Tests(unittest.TestCase):
    def test_macro_1000_chars_mirrors_in_full_with_honest_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600 + 448])
                fixture = "x" * 1000
                commit, mirror = adapter.write_and_sync(
                    "L01", "SMALL49", 448, {"content": fixture}, bucket="bridge_macro"
                )
                self.assertEqual(commit["revision"], 1)
                self.assertEqual(
                    len(adapter.read("L01", "SMALL49", 448)["payload"]["content"]), 1000
                )
                self.assertEqual(
                    len(adapter.small.read(mirror, principal="L01")["content"]), 1000
                )
                cool = commit["cool_reference"]
                self.assertEqual(
                    (cool["block_id"], cool["address"], cool["revision"]),
                    (2048, 2048, 1),
                )
                self.assertEqual(cool["txid"], commit["txid"])
                self.assertEqual(cool["sha256"], commit["sha256"])
                ref = commit["mirror_reference"]
                self.assertEqual(ref["pool"], "small")
                self.assertEqual(ref["record_id"], "crampu-small49-448-r1")
                self.assertEqual(ref["version"], 1)
                self.assertEqual(ref["owner"], "L01")
                self.assertEqual(ref["digest"], mirror.digest)
                self.assertEqual(ref["projection_kind"], "content")
                self.assertTrue(ref["reconstructs_structured_payload"])

    def test_macro_1001_chars_rejected_before_cool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with local_runtime(root, ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600 + 448])
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "SMALL49", 448, {"content": "x" * 1001},
                        bucket="bridge_macro",
                    )
                self.assertEqual(str(ctx.exception), "BLOCK_CHAR_LIMIT")
                self.assertEqual(ctrl._revision(1600 + 448), 0)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)
                # No HOT/WARM residue.
                self.assertEqual(ctrl.inspect(ADMIN)["hot"], 0)
                self.assertEqual(ctrl.inspect(ADMIN)["staged"], 0)
                self.assertEqual(list((root / "warm").glob("*.json")), [])

    def test_invalid_bucket_rejected_before_cool(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [0])
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "BIG49", 0, {"content": "bad"}, bucket="BUCKET_INVALID"
                    )
                self.assertEqual(str(ctx.exception), "BUCKET_INVALID")
                self.assertEqual(ctrl._revision(0), 0)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)

    def test_invalid_empty_string_bucket_rejected_before_cool(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [0])
                # Absent (None) resolves to the default bucket; empty is invalid.
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "BIG49", 0, {"content": "bad"}, bucket=""
                    )
                self.assertEqual(str(ctx.exception), "BUCKET_INVALID")
                self.assertEqual(ctrl._revision(0), 0)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)

    def test_non_string_bucket_rejected_before_cool(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [0])
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "BIG49", 0, {"content": "bad"}, bucket=123
                    )
                self.assertEqual(str(ctx.exception), "BUCKET_INVALID")
                self.assertEqual(ctrl._revision(0), 0)

    def test_non_string_content_rejected_before_cool(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [0])
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "BIG49", 0, {"content": 123}, bucket="cognitive"
                    )
                self.assertEqual(str(ctx.exception), "CONTENT_INVALID")
                self.assertEqual(ctrl._revision(0), 0)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)

    def test_legacy_invalid_row_does_not_block_unrelated_valid_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600, 1601])
                _legacy_invalid_write(ctrl, 1600)
                self.assertEqual(ctrl._revision(1600), 1)
                # The valid write drains the whole outbox: the invalid row must
                # be blocked, not abort the drain.
                commit, mirror = adapter.write_and_sync(
                    "L01", "SMALL49", 1, {"content": "valid"}, bucket="history"
                )
                self.assertEqual(commit["revision"], 1)
                self.assertEqual(adapter.small.count("history"), 1)
                self.assertEqual(
                    adapter.small.read(mirror, principal="L01")["content"], "valid"
                )
                blocked = ctrl.list_blocked_outbox()
                self.assertEqual(len(blocked), 1)
                self.assertEqual(blocked[0][1], 1600)  # block_id
                self.assertEqual(blocked[0][5], "PENDING")  # status unchanged
                self.assertEqual(blocked[0][7], "BUCKET_INVALID")
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_blocked"], 1)
                acked = ctrl.list_outbox(statuses=("ACKNOWLEDGED",))
                self.assertEqual([row[1] for row in acked], [1601])
                # Both COOL revisions stay 1; the legacy record is preserved.
                self.assertEqual(ctrl._revision(1600), 1)
                self.assertEqual(ctrl._revision(1601), 1)
                self.assertEqual(
                    ctrl.load_from_cool(1600, "L01")["payload"]["content"],
                    "legacy-bad",
                )
                # A second drain has nothing left to do.
                self.assertEqual(adapter.sync_pending_outbox(), {})

    def test_same_address_ordering_blocked_row_holds_later_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600])
                _legacy_invalid_write(ctrl, 1600)
                ctrl.write_hot("L01", 1600, {"content": "later-valid"})
                txid = ctrl.stage_to_warm("L01", 1600)
                ctrl.commit_to_cool(
                    "L01", txid, enqueue_mirror=True, mirror_bucket="history"
                )
                self.assertEqual(ctrl._revision(1600), 2)
                self.assertEqual(adapter.sync_pending_outbox(), {})
                self.assertEqual(adapter.small.count("history"), 0)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_blocked"], 1)
                pending = ctrl.list_outbox(statuses=("PENDING",))
                pending_by_block = {row[1]: row for row in pending}
                self.assertIn(1600, pending_by_block)
                # The later row was never attempted: no diagnostic, still PENDING.
                later = [row for row in pending if row[2] == 2]
                self.assertEqual(len(later), 1)
                self.assertIsNone(later[0][7])

    def test_fault_after_cool_replays_on_next_drain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with local_runtime(root, ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600])
                with self.assertRaises(Interrupted):
                    adapter.write_and_sync(
                        "L01", "SMALL49", 0, {"content": "after-cool"},
                        bucket="history", fault_hook=stop_after_cool,
                    )
                self.assertEqual(ctrl._revision(1600), 1)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 1)
                self.assertEqual(adapter.small.count("history"), 0)
                synced = adapter.sync_pending_outbox()
                self.assertEqual(len(synced), 1)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)
            with local_runtime(root, ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                self.assertEqual(
                    adapter.read("L01", "SMALL49", 0)["payload"]["content"],
                    "after-cool",
                )
                adapter.verify_all()

    def test_fault_after_mirror_before_ack_replay_digest_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with local_runtime(root, ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [1600])
                with self.assertRaises(Interrupted):
                    adapter.write_and_sync(
                        "L01", "SMALL49", 0, {"content": "already mirrored"},
                        bucket="history", fault_hook=stop_after_mirror,
                    )
                self.assertEqual(adapter.small.count("history"), 1)
                self.assertEqual(len(ctrl.list_outbox(statuses=("APPLIED",))), 1)
            with local_runtime(root, ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                txid, block_id, revision, agent, sha = (
                    ctrl.list_outbox(statuses=("APPLIED",))[0][:5]
                )
                payload, bucket = ctrl.load_mirror_revision(
                    txid, block_id, revision, agent, sha
                )
                # Repeated replay of the same transaction: same digest.
                ptr1 = adapter._apply_mirror(
                    agent, "SMALL49", 0, revision, txid, payload, bucket=bucket
                )
                ptr2 = adapter._apply_mirror(
                    agent, "SMALL49", 0, revision, txid, payload, bucket=bucket
                )
                self.assertEqual(ptr1.digest, ptr2.digest)
                self.assertEqual(ptr1.record_id, "crampu-small49-0-r1")
                synced = adapter.sync_pending_outbox()
                self.assertEqual(len(synced), 1)
                self.assertEqual(synced[txid].digest, ptr1.digest)
                self.assertEqual(adapter.small.count("history"), 1)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)
                self.assertEqual(adapter.sync_pending_outbox(), {})

    def test_owner_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                ctrl.register_block_owner(0, "L01", ADMIN)
                closet.suit.authorize_agent(
                    "L01", {"task.run", "memory.read", "memory.write"},
                    allowed_block_ids={0},
                )
                closet.suit.authorize_agent(
                    "L02", {"task.run", "memory.read", "memory.write"},
                    allowed_block_ids={0},
                )
                with self.assertRaises(AuthorizationError):
                    adapter.write_and_sync(
                        "L02", "BIG49", 0, {"content": "not mine"},
                        bucket="cognitive",
                    )
                self.assertEqual(ctrl._revision(0), 0)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)
                with self.assertRaises(ConflictError):
                    ctrl.register_block_owner(0, "L02", ADMIN)

    def test_repeated_retries_second_drain_empty_digest_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [0])
                _, mirror = adapter.write_and_sync(
                    "L01", "BIG49", 0, {"content": "retry me"}, bucket="cognitive"
                )
                digest = mirror.digest
                self.assertEqual(adapter.sync_pending_outbox(), {})
                self.assertEqual(adapter.sync_pending_outbox(), {})
                self.assertEqual(
                    adapter.big.read(mirror, principal="L01")["content"], "retry me"
                )
                self.assertEqual(mirror.digest, digest)
                adapter.verify_all()

    def test_reopen_reads_cool_and_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with local_runtime(root, ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [0, 1600])
                adapter.write_and_sync(
                    "L01", "BIG49", 0, {"content": "big"}, bucket="cognitive"
                )
                adapter.write_and_sync(
                    "L01", "SMALL49", 0, {"content": "small"}, bucket="history"
                )
            with local_runtime(root, ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                self.assertEqual(
                    adapter.read("L01", "BIG49", 0)["payload"]["content"], "big"
                )
                self.assertEqual(
                    adapter.read("L01", "SMALL49", 0)["payload"]["content"], "small"
                )
                self.assertEqual(adapter.big.count(), 1)
                self.assertEqual(adapter.small.count("history"), 1)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_blocked"], 0)
                adapter.verify_all()

    def test_unicode_char_and_byte_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [0, 1, 1600 + 448])
                # 600 two-byte chars: over the 512-char cognitive cap.
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "BIG49", 0, {"content": "é" * 600},
                        bucket="cognitive",
                    )
                self.assertEqual(str(ctx.exception), "BLOCK_CHAR_LIMIT")
                self.assertEqual(ctrl._revision(0), 0)
                # 1000 four-byte chars: 1000 chars, 4000 bytes — accepted.
                # The 4096-byte limit is unreachable under the current caps
                # (max cap 1000 x 4 bytes = 4000), so no failing byte case is
                # forced here; see FIXREV2_NOTES.md.
                _, mirror = adapter.write_and_sync(
                    "L01", "SMALL49", 448, {"content": "\U0001F600" * 1000},
                    bucket="bridge_macro",
                )
                mirrored = adapter.small.read(mirror, principal="L01")["content"]
                self.assertEqual(len(mirrored), 1000)
                self.assertEqual(len(mirrored.encode("utf-8")), 4000)

    def test_nfc_normalization_measured_after_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                _setup(ctrl, closet, [0, 1])
                # e + combining acute normalizes to a single 'é' per pair.
                _, mirror = adapter.write_and_sync(
                    "L01", "BIG49", 0, {"content": "e\u0301" * 10},
                    bucket="cognitive",
                )
                self.assertEqual(
                    adapter.big.read(mirror, principal="L01")["content"], "é" * 10
                )
                # 600 decomposed pairs normalize to 600 chars: over the cap.
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "BIG49", 1, {"content": "e\u0301" * 600},
                        bucket="cognitive",
                    )
                self.assertEqual(str(ctx.exception), "BLOCK_CHAR_LIMIT")
                self.assertEqual(ctrl._revision(1), 0)

    def test_honest_references_learned_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, learning):
                _setup(ctrl, closet, [0, 1600])
                cases = {
                    "H1": {"a_noise": 1, "z_signal": "hit"},
                    "H2": {"a_noise": 2, "z_signal": "miss"},
                    "H3": {"a_noise": 3, "z_signal": "miss"},
                }
                trained = learning.assist_and_learn(
                    "train", "L01", cases, {},
                    [{"before": 3, "after": 1, "profile": [1, 2]}],
                )
                self.assertEqual(trained["learned_profile"], [1, 2])
                self.assertEqual(trained["pointer"], "crampu-big49-0-r1")
                self.assertEqual(trained["projection_kind"], "txid-reference")
                cool = trained["cool_reference"]
                self.assertEqual(
                    (cool["block_id"], cool["address"], cool["revision"]), (0, 0, 1)
                )
                self.assertEqual(cool["txid"], trained["cool_reference"]["txid"])
                ref = trained["mirror_reference"]
                self.assertEqual(ref["record_id"], "crampu-big49-0-r1")
                self.assertEqual(ref["pool"], "big")
                self.assertFalse(ref["reconstructs_structured_payload"])
                # The learner reads the structured COOL payload, not the mirror.
                rec = closet.suit.execute_memory_read("L01", "BIG49", 0)
                self.assertEqual(rec["payload"]["learned_profile"], [1, 2])
                self.assertEqual(rec["cool_reference"], cool)
                learned = closet.run_task(
                    "reopened", "L01",
                    {"version": "42.0-task/1", "operation": "select_discriminator",
                     "candidates": cases, "observed": {}, "memory_block": ["BIG49", 0]},
                )
                self.assertEqual(learned["selected_feature"], "z_signal")


if __name__ == "__main__":
    unittest.main()
