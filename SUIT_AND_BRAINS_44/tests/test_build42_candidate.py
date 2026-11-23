from pathlib import Path
import tempfile
import unittest

from build040_runtime.standalone import demo, function_registry, local_runtime
from crampu.controller import AuthorizationError, IntegrityError


ADMIN = "qualification-only-local-admin"
REQUEST = {"version": "42.0-task/1", "operation": "select_discriminator",
           "candidates": {"A": {"x": 1}, "B": {"x": 2}}, "observed": {}}


class Interrupted(Exception):
    pass


def stop_after_commit(stage):
    if stage == "after_cool_before_mirror":
        raise Interrupted()


class Build42CandidateTests(unittest.TestCase):
    def test_demo_wires_closet_suit_learning_and_both_stores(self):
        result = demo()
        self.assertEqual(result["cold"]["selected_feature"], "a_noise")
        self.assertEqual(result["reopened"]["selected_feature"], "z_signal")
        self.assertEqual(result["small49"]["content"], "small")
        self.assertEqual(result["pending_mirrors"], 0)

    def test_registry_discloses_passthroughs(self):
        slots = function_registry()
        self.assertEqual(len({entry["id"] for entry in slots}), 98)
        self.assertEqual(sum(entry["status"] == "IMPLEMENTED" for entry in slots), 21)

    def test_task_requires_explicit_grant_and_disallows_client_authority(self):
        with tempfile.TemporaryDirectory() as tmp, local_runtime(Path(tmp), ADMIN) as (_, closet, _):
            with self.assertRaises(AuthorizationError):
                closet.run_task("no-grant", "L01", REQUEST)
            closet.suit.authorize_agent("L01", {"task.run"}, max_steps=1)
            with self.assertRaises(ValueError):
                closet.run_task("bad", "L01", {**REQUEST, "authority": 1})
            self.assertEqual(closet.run_task("good", "L01", REQUEST)["authority"], 0)
            with self.assertRaises(AuthorizationError):
                closet.run_task("exhausted", "L01", REQUEST)

    def test_parent_revocation_and_shared_budget(self):
        with tempfile.TemporaryDirectory() as tmp, local_runtime(Path(tmp), ADMIN) as (_, closet, _):
            suit = closet.suit
            suit.authorize_agent("L01", {"spawn_child", "task.run"}, max_steps=2)
            suit.request_child_agent("L01", "L02", {"task.run"})
            closet.run_task("child", "L02", REQUEST)
            suit.revoke_agent("L01")
            with self.assertRaises(AuthorizationError):
                closet.run_task("revoked-child", "L02", REQUEST)
            suit.authorize_agent("L01", {"spawn_child", "task.run"}, max_steps=1)
            suit.request_child_agent("L01", "L03", {"task.run"})
            closet.run_task("child-after-regrant", "L03", REQUEST)
            with self.assertRaises(AuthorizationError):
                closet.run_task("parent-exhausted", "L01", REQUEST)

    def test_outbox_replays_exact_revisions_and_buckets_after_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with local_runtime(root, ADMIN) as (ctrl, closet, _):
                ctrl.register_block_owner(0, "L01", ADMIN)
                for content, bucket in (("first", "association"), ("second", "cognitive")):
                    with self.assertRaises(Interrupted):
                        closet.suit.adapter.write_and_sync(
                            "L01", "BIG49", 0, {"content": content},
                            bucket=bucket, fault_hook=stop_after_commit,
                        )
            with local_runtime(root, ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                pointers = list(adapter.sync_pending_outbox().values())
                records = [adapter.big.read(ptr, principal="L01") for ptr in pointers]
                self.assertEqual([(r["content"], r["bucket"]) for r in records],
                                 [("first", "association"), ("second", "cognitive")])
                self.assertEqual(adapter.sync_pending_outbox(), {})
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)
                adapter.verify_all()

    def test_pending_legacy_entry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp, local_runtime(Path(tmp), ADMIN) as (ctrl, closet, _):
            ctrl.register_block_owner(0, "L01", ADMIN)
            adapter = closet.suit.adapter
            with self.assertRaises(Interrupted):
                adapter.write_and_sync("L01", "BIG49", 0, {"content": "pending"},
                                       fault_hook=stop_after_commit)
            ctrl._db.execute("DELETE FROM mirror_revisions")
            with self.assertRaises(IntegrityError):
                adapter.sync_pending_outbox()
            self.assertEqual(adapter.big.count(), 0)
            self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 1)

    def test_profile_permission_failure_is_reported_and_propagates(self):
        with tempfile.TemporaryDirectory() as tmp, local_runtime(Path(tmp), ADMIN) as (_, closet, _):
            closet.suit.authorize_agent("L01", {"task.run"})
            with self.assertRaises(AuthorizationError):
                closet.run_task("no-read", "L01", {**REQUEST, "memory_block": ["BIG49", 0]})
            self.assertEqual(closet.continuity.get_session("no-read")["status"], "INTERRUPTED")

    def test_targeted_l05_u06_u07_contracts(self):
        from contract.functions025 import OPERATIONS

        request = OPERATIONS["L05"]([{"reason": "relation label", "digest": "source-1"}])
        self.assertEqual(request["required_fields"], ["predicate", "subject", "value"])
        model = OPERATIONS["U06"](["pressure", "!blocked"], [
            {"if": ["pressure"], "then": "high"},
            {"if": ["high"], "then": "blocked"},
        ])
        self.assertTrue(model["contradiction"])
        predictions = OPERATIONS["U07"]({
            "applicable": {"requires": ["high"], "predicts": {"line": "blocked"}},
            "unsupported": {"requires": ["low"], "predicts": {"line": "open"}},
        }, model["facts"])
        self.assertEqual(predictions, {"applicable": {"line": "blocked"}})

    def test_replay_after_mirror_before_ack_is_idempotent(self):
        def stop_after_mirror(stage):
            if stage == "after_mirror_before_ack":
                raise Interrupted()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with local_runtime(root, ADMIN) as (ctrl, closet, _):
                ctrl.register_block_owner(1600, "L01", ADMIN)
                with self.assertRaises(Interrupted):
                    closet.suit.adapter.write_and_sync(
                        "L01", "SMALL49", 0, {"content": "already mirrored"},
                        bucket="history", fault_hook=stop_after_mirror,
                    )
                self.assertEqual(closet.suit.adapter.small.count("history"), 1)
            with local_runtime(root, ADMIN) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                self.assertEqual(len(adapter.sync_pending_outbox()), 1)
                self.assertEqual(adapter.small.count("history"), 1)
                self.assertEqual(ctrl.inspect(ADMIN)["outbox_pending"], 0)
