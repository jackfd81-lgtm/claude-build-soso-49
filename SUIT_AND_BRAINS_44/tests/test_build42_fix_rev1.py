"""FIX-REV1 regression tests for the two RC1 defects found by Muse verification.

- bridge_macro mirror truncation (1000 -> 512)
- invalid bucket leaving a committed COOL revision 1 with a PENDING outbox row

Run with the focused suite: python3 run_build42_tests.py (add this file to
SELECTIONS there) or: python3 -m unittest tests.test_build42_fix_rev1 -v
"""
import tempfile
import unittest
from pathlib import Path

from build040_runtime.standalone import local_runtime
from realcog.ph6_brains_2208 import Rejected

ADMIN = "fix-rev1-admin-token-1234"


class FixRev1Tests(unittest.TestCase):
    def test_bridge_macro_mirror_preserves_1000_chars(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                ctrl.register_block_owner(1600 + 448, "L01", ADMIN)
                closet.suit.authorize_agent(
                    "L01", {"task.run", "memory.read", "memory.write"},
                    allowed_block_ids={1600 + 448},
                )
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

    # FIX-REV2: this test previously asserted that a 600-char cognitive write
    # was silently sliced to 512 in the mirror. That behaviour is superseded:
    # over-cap content is now explicitly rejected before COOL (see
    # tests/test_build42_fix_rev2.py). The assertion below replaces the old
    # slicing assertion; the test count for this file stays at 3.
    def test_cognitive_over_cap_rejected_before_cool_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                ctrl.register_block_owner(0, "L01", ADMIN)
                closet.suit.authorize_agent(
                    "L01", {"task.run", "memory.read", "memory.write"},
                    allowed_block_ids={0},
                )
                with self.assertRaises(Rejected) as ctx:
                    adapter.write_and_sync(
                        "L01", "BIG49", 0, {"content": "y" * 600}, bucket="cognitive"
                    )
                self.assertEqual(str(ctx.exception), "BLOCK_CHAR_LIMIT")
                self.assertEqual(ctrl._revision(0), 0)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)

    def test_invalid_bucket_rejected_before_cool_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                ctrl.register_block_owner(0, "L01", ADMIN)
                closet.suit.authorize_agent(
                    "L01", {"task.run", "memory.read", "memory.write"},
                    allowed_block_ids={0},
                )
                with self.assertRaises(Rejected):
                    adapter.write_and_sync(
                        "L01", "BIG49", 0, {"content": "bad"}, bucket="BUCKET_INVALID"
                    )
                # No COOL revision was committed and no outbox row was enqueued.
                self.assertEqual(ctrl._revision(0), 0)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)


if __name__ == "__main__":
    unittest.main()
