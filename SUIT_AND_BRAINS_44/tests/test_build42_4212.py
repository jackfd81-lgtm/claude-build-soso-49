"""Build 42.1.2 regressions: the four findings Jd verified as still open in
42.1.1.

1. Consultation retry authorization: every retry goes through Suit, and the
   ledger is bound to the authorized principal and the invocation ID.
2. Revision-bound COOL read: payload and reference come from one read, so
   they cannot describe different revisions. Lossy mirror projections (a
   payload supplying two different text fields, of which the mirror keeps
   one) are labelled non-reconstructible.
3. Phase C: each arm is compared on its own scope and outputs, both routes
   are reconstructed from recorded events, and public calls are measured
   separately from internal stages.
4. Release packaging is verified by the packaging script (fresh manifest +
   bundled wheel), not by this file.

Stdlib unittest, disposable state, synthetic fixtures only.
Run: python3 -m unittest tests.test_build42_4212 -v
"""
import copy
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from build040_runtime.consultation import (
    _reconstruct_route,
    consult,
    run_phase_c,
)
from build040_runtime.standalone import local_runtime
from crampu.controller import AuthorizationError

ADMIN = "build4212-admin-token"

TOX = {
    "med:early": {"discipline": "medical", "key": "tox_screen", "value": "negative"},
    "med:late": {"discipline": "medical", "key": "tox_screen", "value": "positive_opioid"},
}
LEGAL = {
    "leg:a": {"discipline": "legal", "key": "signed_consent", "value": "present"},
}


def refs_for(snapshot, ids):
    return [{"source_id": sid, "verification_status": "VERIFIED",
             "key": snapshot[sid]["key"], "value": snapshot[sid]["value"]} for sid in ids]


def make_request(case, invocation, origin, target, snapshot, ids, **overrides):
    request = {
        "schema_version": "consultation-request/1.0",
        "case_id": case,
        "invocation_id": invocation,
        "expected_case_revision": 0,
        "origin_specialty": origin,
        "target_specialty": target,
        "purpose": "consult",
        "original_question": "Compare the entries for a synthetic record review",
        "evidence_snapshot": copy.deepcopy(snapshot),
        "evidence_refs": refs_for(snapshot, ids),
        "scope": ["compare supplied records"],
        "budget": {"max_stage_calls": 5, "max_steps": 64},
    }
    request.update(overrides)
    return request


def make_fixture(case, origin, target, snapshot, ids):
    return {"case_id": case, "origin": origin, "target": target,
            "question": "Compare the entries for a synthetic record review",
            "evidence_store": copy.deepcopy(snapshot),
            "evidence_refs": refs_for(snapshot, ids),
            "purpose": "consult", "scope": ["compare supplied records"]}


class RuntimeCase(unittest.TestCase):
    """A disposable local_runtime with an L01 task.run grant."""

    def setUp(self):
        self._admin = secrets.token_hex(24)
        self._tmp = tempfile.TemporaryDirectory()
        self._stack = local_runtime(Path(self._tmp.name), self._admin)
        self.controller, self.closet, _ = self._stack.__enter__()
        self.closet.suit.authorize_agent("L01", {"task.run"}, allowed_block_ids={0})

    def tearDown(self):
        self._stack.__exit__(None, None, None)
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# 1. Consultation retry authorization
# ---------------------------------------------------------------------------

class ConsultationRetryAuthTests(RuntimeCase):
    def test_retry_requires_suit_authorization(self):
        request = make_request("case-r1", "inv-r1", "Legal", "Medical",
                               TOX, ["med:early", "med:late"])
        first = consult(self.closet, "L01", request)
        self.assertEqual(first["ledger"], "recorded")
        self.closet.suit.revoke_agent("L01")
        with self.assertRaises(AuthorizationError):
            consult(self.closet, "L01", copy.deepcopy(request))

    def test_unauthorized_principal_cannot_replay(self):
        request = make_request("case-r2", "inv-r2", "Legal", "Medical",
                               TOX, ["med:early", "med:late"])
        consult(self.closet, "L01", request)
        # L02 has no grant at all: the retry must fail authorization, not
        # return L01's recorded outcome.
        with self.assertRaises(AuthorizationError):
            consult(self.closet, "L02", copy.deepcopy(request))

    def test_retry_is_bound_to_principal(self):
        request = make_request("case-r3", "inv-r3", "Legal", "Medical",
                               TOX, ["med:early", "med:late"])
        first = consult(self.closet, "L01", request)
        self.assertEqual(first["ledger"], "recorded")
        self.closet.suit.authorize_agent("L02", {"task.run"}, allowed_block_ids={0})
        # Same invocation ID, same data, different authorized principal:
        # a fresh execution under L02, not L01's outcome.
        second = consult(self.closet, "L02", copy.deepcopy(request))
        self.assertEqual(second["ledger"], "recorded")
        self.assertEqual(len(self.closet.consultation_ledger), 2)
        # And L01's retry still returns L01's recorded outcome.
        third = consult(self.closet, "L01", copy.deepcopy(request))
        self.assertEqual(third["ledger"], "duplicate_retry")
        self.assertEqual(third["result"], first["result"])


# ---------------------------------------------------------------------------
# 2a. Revision-bound COOL read
# ---------------------------------------------------------------------------

class RevisionBoundReadTests(unittest.TestCase):
    def test_payload_and_reference_share_one_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            admin = secrets.token_hex(24)
            with local_runtime(Path(tmp), admin) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                ctrl.register_block_owner(1600, "L01", admin)
                closet.suit.authorize_agent(
                    "L01", {"task.run", "memory.read", "memory.write"},
                    allowed_block_ids={1600},
                )
                commit1, _ = adapter.write_and_sync(
                    "L01", "SMALL49", 0, {"content": "first"}, bucket="history")
                data1 = closet.suit.execute_memory_read("L01", "SMALL49", 0)
                self.assertEqual(data1["payload"]["content"], "first")
                self.assertEqual(data1["revision"], 1)
                self.assertEqual(data1["cool_reference"]["revision"], 1)
                self.assertEqual(data1["cool_reference"]["txid"], commit1["txid"])
                self.assertEqual(data1["cool_reference"]["sha256"], commit1["sha256"])
                commit2, _ = adapter.write_and_sync(
                    "L01", "SMALL49", 0, {"content": "second"}, bucket="history")
                data2 = closet.suit.execute_memory_read("L01", "SMALL49", 0)
                self.assertEqual(data2["payload"]["content"], "second")
                self.assertEqual(data2["revision"], 2)
                self.assertEqual(data2["cool_reference"]["revision"], 2)
                self.assertEqual(data2["cool_reference"]["txid"], commit2["txid"])
                # The read no longer uses the separate cool_reference call:
                # breaking that method must not break the read.
                with mock.patch.object(
                    ctrl, "cool_reference",
                    side_effect=AssertionError("separate reference read used"),
                ):
                    data3 = closet.suit.execute_memory_read("L01", "SMALL49", 0)
                self.assertEqual(data3["payload"]["content"], "second")
                self.assertEqual(data3["cool_reference"]["revision"], 2)


# ---------------------------------------------------------------------------
# 2b. Lossy mirror projections
# ---------------------------------------------------------------------------

class LossyMirrorTests(unittest.TestCase):
    def _write(self, adapter, payload):
        return adapter.write_and_sync(
            "L01", "SMALL49", 0, payload, bucket="history")

    def test_two_different_text_fields_are_not_reconstructible(self):
        with tempfile.TemporaryDirectory() as tmp:
            admin = secrets.token_hex(24)
            with local_runtime(Path(tmp), admin) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                ctrl.register_block_owner(1600, "L01", admin)
                closet.suit.authorize_agent(
                    "L01", {"task.run", "memory.read", "memory.write"},
                    allowed_block_ids={1600},
                )
                commit, ptr = self._write(adapter, {
                    "content": "kept-value", "text": "dropped-value",
                    "relation": "ASSOCIATED_WITH",
                })
                ref = commit["mirror_reference"]
                self.assertEqual(ref["projection_kind"], "content")
                self.assertFalse(ref["reconstructs_structured_payload"])
                # The mirror really does retain only the content field.
                stored = adapter.small.read(ptr, principal="L01")
                self.assertEqual(stored["content"], "kept-value")

    def test_matching_text_fields_remain_reconstructible(self):
        with tempfile.TemporaryDirectory() as tmp:
            admin = secrets.token_hex(24)
            with local_runtime(Path(tmp), admin) as (ctrl, closet, _):
                adapter = closet.suit.adapter
                ctrl.register_block_owner(1600, "L01", admin)
                closet.suit.authorize_agent(
                    "L01", {"task.run", "memory.read", "memory.write"},
                    allowed_block_ids={1600},
                )
                commit, _ = self._write(adapter, {
                    "content": "same-value", "text": "same-value",
                })
                self.assertTrue(commit["mirror_reference"]["reconstructs_structured_payload"])
                commit2, _ = self._write(adapter, {"content": "only-content"})
                self.assertTrue(commit2["mirror_reference"]["reconstructs_structured_payload"])


# ---------------------------------------------------------------------------
# 3. Phase C: own scope/outputs, reconstructed routes, measured calls
# ---------------------------------------------------------------------------

class PhaseCReconstructionTests(RuntimeCase):
    def test_calls_are_measured_public_vs_internal(self):
        result = run_phase_c(make_fixture("pc-4212-1", "Legal", "Medical", TOX, ["med:early", "med:late"]),
                             self.closet, "L01")
        self.assertTrue(result["parity"], result["comparisons"])
        self.assertEqual(result["calls"],
                         {"B": {"public": 5, "internal": 5}, "C": {"public": 1, "internal": 5}})
        self.assertEqual(result["handoffs"], {"B": 5, "C": 1})
        self.assertTrue(result["simplification"])
        self.assertEqual(result["arms"]["B"]["public_calls"], 5)
        self.assertEqual(result["arms"]["B"]["internal_stage_calls"], 5)
        self.assertEqual(result["arms"]["C"]["public_calls"], 1)
        self.assertEqual(result["arms"]["C"]["internal_stage_calls"], 5)

    def test_arm_c_scope_and_provenance_are_its_own(self):
        result = run_phase_c(make_fixture("pc-4212-2", "Legal", "Medical", TOX, ["med:early", "med:late"]),
                             self.closet, "L01")
        expected_scope = ["physiological_finding", "diagnostic_uncertainty", "conflict_preservation"]
        self.assertEqual(result["arms"]["C"]["scope"], expected_scope)
        self.assertEqual(result["arms"]["B"]["scope"], expected_scope)
        self.assertEqual(result["arms"]["C"]["provenance_intact"],
                         result["arms"]["B"]["provenance_intact"])
        self.assertTrue(result["arms"]["C"]["provenance_intact"])

    def test_routes_reconstruct_from_recorded_events(self):
        result = run_phase_c(make_fixture("pc-4212-3", "Legal", "Medical", TOX, ["med:early", "med:late"]),
                             self.closet, "L01")
        expected = ["L33", "U32", "SPEC:Medical", "L34", "U33"]
        self.assertTrue(result["arms"]["B"]["route_valid"])
        self.assertTrue(result["arms"]["C"]["route_valid"])
        self.assertEqual(result["arms"]["B"]["route"], expected)
        self.assertEqual(result["arms"]["C"]["route"], expected)
        by_name = {c["dimension"]: c for c in result["comparisons"]}
        self.assertTrue(by_name["selected_route"]["parity"])
        self.assertTrue(by_name["reconstructible_trace"]["parity"])
        self.assertTrue(result["parity"])

    def test_reconstruct_route_rejects_a_broken_chain(self):
        # Reconstruct from a fresh consult's own recorded events, then break
        # one link at a time and confirm the chain no longer validates.
        request = make_request("case-pc4", "inv-pc4", "Legal", "Medical",
                               TOX, ["med:early", "med:late"])
        out = consult(self.closet, "L01", request)
        good = _reconstruct_route(out["events"])
        self.assertTrue(good["valid"], good["problems"])
        broken = copy.deepcopy(out["events"])
        broken[2]["input_ref"] = "tampered:ref"
        bad = _reconstruct_route(broken)
        self.assertFalse(bad["valid"])
        self.assertTrue(bad["problems"])
        broken2 = copy.deepcopy(out["events"])
        broken2[1]["predecessor_id"] = "tampered:ref"
        self.assertFalse(_reconstruct_route(broken2)["valid"])

    def test_parity_holds_both_directions(self):
        for case, origin, target, snapshot, ids in (
            ("pc-4212-5", "Legal", "Medical", TOX, ["med:early", "med:late"]),
            ("pc-4212-6", "Medical", "Legal", {"leg:a": LEGAL["leg:a"]}, ["leg:a"]),
        ):
            result = run_phase_c(make_fixture(case, origin, target, snapshot, ids),
                                 self.closet, "L01")
            self.assertTrue(result["parity"], result["comparisons"])
            self.assertEqual(result["verdict"], "PARITY")
