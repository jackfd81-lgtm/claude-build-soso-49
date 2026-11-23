"""Build 42.1 next-candidate tests: truthful dispatch, seven versioned
structures, one public consult operation, and the Self-Aware Cognitive Map
(Phases A-C). Stdlib unittest, disposable state, synthetic fixtures only.
"""
import copy
import hashlib
import json
from pathlib import Path
import secrets
import tempfile
import unittest

from build040_runtime.consultation import (
    ConsultationError,
    consult,
    propose_routes,
    run_phase_c,
)
from build040_runtime.standalone import local_runtime
from contract.cognitive_map import (
    AssociationEdge,
    ClosingReview,
    CoherenceReview,
    CognitiveMap,
    ConsultationRequest,
    ConsultationResult,
    InvestigationReconciliation,
    InvestigationRecorder,
    InvocationEvent,
    OpeningSequence,
    OutcomeReview,
    ReadingMatcher,
    RelationshipMapper,
    ResolutionRecord,
    RouteAdvisor,
    ValidationError,
    load_edge,
    record_use,
    replay,
    select_edge,
    store_edge,
)
from contract.disposition_adapter import evaluate_u09
from contract.slot_specs import SLOT_MAP_VERSION, describe, execute_typed, registry
from realcog.ph6_brains_2208 import Pointer

AUTH = {"authority": 0, "task_id": "next-tests"}

TOX = {
    "med:early": {"discipline": "medical", "key": "tox_screen", "value": "negative"},
    "med:late": {"discipline": "medical", "key": "tox_screen", "value": "positive_opioid"},
}
LEGAL = {
    "leg:a": {"discipline": "legal", "key": "signed_consent", "value": "present"},
    "leg:b": {"discipline": "legal", "key": "signed_consent", "value": "absent"},
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
# 1. Truthful dispatch
# ---------------------------------------------------------------------------

class SlotSpecTests(unittest.TestCase):
    def test_registry_has_all_98_slots_with_states(self):
        specs = registry()
        self.assertEqual(len(specs), 98)
        self.assertEqual([s.function_id for s in specs],
                         [f"L{i:02d}" for i in range(1, 50)] + [f"U{i:02d}" for i in range(1, 50)])
        states = {}
        for spec in specs:
            states[spec.state] = states.get(spec.state, 0) + 1
            spec.validate()
        self.assertEqual(states, {"bound": 20, "dedicated": 1, "generic-only": 77})
        self.assertEqual(SLOT_MAP_VERSION, "42.1")

    def test_u09_is_dedicated_with_distinct_lineage(self):
        spec = describe("U09")
        self.assertEqual(spec.state, "dedicated")
        self.assertEqual(spec.callable_identity, "contract.disposition_adapter.evaluate_u09")
        self.assertIn("distinct lineage", spec.lineage)
        self.assertIn("historical", spec.lineage)
        self.assertNotIn("Compare Candidates", spec.name)

    def test_proposals_are_documented_not_assigned(self):
        for fid in ("L06", "L07", "U01", "U02"):
            self.assertIn("Proposal", describe(fid).lineage)
            self.assertEqual(describe(fid).state, "generic-only")
        for fid in ("L19", "L20", "U17", "U18"):
            self.assertIn("Proposal", describe(fid).lineage)
            self.assertEqual(describe(fid).state, "generic-only")

    def test_describe_unknown_slot_raises(self):
        with self.assertRaises(KeyError):
            describe("L50")

    def test_slot_spec_round_trip(self):
        spec = describe("L33")
        restored = type(spec).from_dict(spec.to_dict())
        self.assertEqual(restored, spec)
        with self.assertRaises(ValueError):
            type(spec).from_dict({**spec.to_dict(), "schema_version": "slot-spec/9.9"})


class ExecuteTypedTests(unittest.TestCase):
    def test_bound_operations_run_via_exact_callable(self):
        parsed = execute_typed("L03", AUTH, [{"source": "s1", "text": "dog bites cat."}])
        self.assertEqual(parsed["status"], "OK")
        self.assertEqual(parsed["handler"], "contract.functions025.parse")
        self.assertEqual(parsed["result"]["accepted"][0]["subject"], "dog")
        selected = execute_typed(
            "U12", AUTH, {"a_noise": [1, 1, 1], "z_signal": [1, 2]}, [1, 2])
        self.assertEqual(selected["result"], {"feature": "z_signal", "used_method": True})
        learned = execute_typed("U14", AUTH, [{"before": 3, "after": 1, "profile": [1, 2]}])
        self.assertEqual(learned["result"], [1, 2])

    def test_all_21_concrete_operations_execute(self):
        from contract.context_envelope import ContextEnvelope
        env = ContextEnvelope(
            investigation_id="t", originating_question="q", originating_specialty="legal",
            current_specialty="legal", consultation_purpose="consult",
            evidence_store=TOX, evidence_refs=refs_for(TOX, ["med:early", "med:late"]))
        from contract.cross_specialty import verify_evidence_records
        ev = verify_evidence_records(env)
        cases = {
            "L03": ([{"source": "s1", "text": "dog bites cat."}],),
            "L04": ([{"subject": "dog", "predicate": "bites", "value": "cat",
                      "digest": "d1", "source": "s1", "scope": "default"}],),
            "L05": ([{"digest": "d1", "reason": "observation envelope required"}],),
            "L16": ({"dog": {"facts": [{"subject": "dog", "predicate": "bites",
                                        "value": "cat", "digest": "d1"}]}},),
            "L17": ([{"subject": "dog", "predicate": "bites", "value": "cat", "digest": "d1",
                      "source": "s1", "scope": "default", "negated": False, "uncertain": False}],),
            "L18": ([],),
            "L22": ([{"digest": "d1", "event": "2026-01-01T00:00:00Z"}],),
            "L31": ([{"value": "awake", "negated": False, "digest": "d1", "uncertain": False}], [], False),
            "L33": (env, "Medical"),
            "L34": (env, {"relevant": True}, {"perspective_tag": "medical_bounded_for_legal",
                                              "uncertainty": ["tox_screen"]}),
            "U06": (["pressure", "!blocked"], [{"if": ["pressure"], "then": "alarm"}]),
            "U07": ({"H1": {"predicts": "x", "requires": ["a"]}}, ["a"]),
            "U11": ({"H1": {"a": 1}}, {}),
            "U12": ({"a": [1, 2]}, [1, 2]),
            "U13": ({"H1": {"a": 1}}, {}),
            "U14": ([{"before": 3, "after": 1, "profile": [1, 2]}],),
            "U15": ([{"profile": [1, 2], "source": "BIG49:0"}], {"a": [1, 2]}),
            "U16": ({"status": "SUPPORTED", "support": ["d1"], "answer": "awake", "negated": False},
                    [{"digest": "d1", "source": "s1"}]),
            "U32": (env, {"relevant": True}, "Medical", ev),
            "U33": (env, ev, {"checks": {"perspective_preserved": True,
                                          "relevance_established": True,
                                          "uncertainty_preserved": True}},
                    {"specialty": "Medical", "status": "BOUNDED_FINDING", "citations": ["med:early"],
                     "conflict_records": [], "uncertainty": ["tox_screen"]}, "Medical"),
        }
        for fid, args in cases.items():
            with self.subTest(fid=fid):
                out = execute_typed(fid, AUTH, *args)
                self.assertEqual(out["status"], "OK", out)
                self.assertIsNotNone(out["handler"])

    def test_generic_only_slots_return_unsupported_not_keyerror(self):
        for fid in ("L01", "L02", "L06", "U01", "U49"):
            out = execute_typed(fid, AUTH)
            self.assertEqual(out["status"], "UNSUPPORTED")
            self.assertEqual(out["code"], "UNSUPPORTED_SLOT")
            self.assertIsNone(out["handler"])

    def test_u09_runs_via_named_adapter(self):
        out = execute_typed("U09", AUTH, {"obs_id": "o1", "facts": {"current": "high", "rotation": 0}})
        self.assertEqual(out["status"], "OK")
        self.assertEqual(out["handler"], "contract.disposition_adapter.evaluate_u09")
        self.assertEqual([e["disposition"] for e in out["result"]], ["PASS", "DROP", "HOLD"])
        direct = evaluate_u09({"obs_id": "o1", "facts": {"current": "high", "rotation": 0}})
        self.assertEqual(direct, out["result"])

    def test_malformed_inputs_return_structured_rejections(self):
        cases = [
            ("L04", ([{"predicate": "bite", "value": "cat", "digest": "d1"}],), "MISSING_FIELD"),
            ("L05", ([{"digest": "d1", "reason": 123}],), "TYPE_MISMATCH"),
            ("U06", (["pressure"], ["not-a-dict"]), "TYPE_MISMATCH"),
            ("U07", ({"H1": {"requires": []}}, []), "MISSING_FIELD"),
            ("U16", ({"status": "BOGUS", "support": []}, []), "INVALID_VALUE"),
        ]
        for fid, args, code in cases:
            with self.subTest(fid=fid):
                out = execute_typed(fid, AUTH, *args)
                self.assertEqual(out["status"], "REJECTED", out)
                self.assertEqual(out["code"], code, out)
                self.assertIn("exception_type", out["error"])
                self.assertIn("message", out["error"])

    def test_auth_and_unknown_slot_rejected(self):
        self.assertEqual(execute_typed("L03", {"authority": 1, "task_id": "t"}, [])["code"], "AUTH_REQUIRED")
        self.assertEqual(execute_typed("L03", {"authority": "0", "task_id": "t"}, [])["code"], "AUTH_REQUIRED")
        self.assertEqual(execute_typed("L03", "not-a-mapping", [])["code"], "AUTH_REQUIRED")
        # require_task defaults a missing authority to 0 (inherited contract);
        # the typed boundary enforces exactly that, nothing stricter.
        self.assertEqual(execute_typed("L03", {"task_id": "t"}, [])["status"], "OK")
        self.assertEqual(execute_typed("X99", AUTH)["code"], "UNKNOWN_SLOT")

    def test_invoke_and_execute_operation_keep_current_behaviour(self):
        from contract.brains_wiring import BrainsWiringRegistry
        from contract.cross_specialty import CROSS_OPERATIONS
        reg = BrainsWiringRegistry(operations=CROSS_OPERATIONS)
        generic = reg.invoke("L03", AUTH)
        self.assertEqual(generic["status"], "OK")  # generic only; does not parse
        self.assertNotIn("accepted", generic)
        with self.assertRaises(KeyError):
            reg.execute_operation("L01", AUTH)
        with self.assertRaises(KeyError):
            reg.execute_operation("U09", AUTH)


# ---------------------------------------------------------------------------
# 2. Seven versioned structures
# ---------------------------------------------------------------------------

def _edge(**overrides):
    data = {
        "edge_id": "edge:legal-medical:tox",
        "origin_specialty": "Legal", "target_specialty": "Medical",
        "source_op": "L22", "source_output_port": "ordered_source_ids",
        "target_op": "SPEC:Medical", "target_input_port": "evidence_refs",
        "purpose": "compare supplied records",
        "permitted_scope": ["physiological_finding"],
        "excluded_scope": ["legal_negligence_determination"],
        "trigger_refs": ["med:early", "med:late"],
        "supporting_evidence_refs": ["med:early", "med:late"],
    }
    data.update(overrides)
    return AssociationEdge(**data)


def _request(**overrides):
    data = {
        "case_id": "case-1", "invocation_id": "inv-1", "expected_case_revision": 0,
        "origin_specialty": "Legal", "target_specialty": "Medical", "purpose": "consult",
        "original_question": "Compare the entries",
        "evidence_snapshot": copy.deepcopy(TOX),
        "evidence_refs": refs_for(TOX, ["med:early", "med:late"]),
    }
    data.update(overrides)
    return ConsultationRequest(**data)


def _result(**overrides):
    data = {
        "case_id": "case-1", "invocation_id": "inv-1", "edge_id": "edge:legal-medical:tox",
        "edge_version": 1, "evidence_refs": ["med:early", "med:late"],
        "trace_refs": ["inv-1:L33:out"],
        "claims": [{"claim": "tox_screen conflicts", "source_refs": ["med:early", "med:late"]}],
        "conflicting_pairs": [{"key": "tox_screen", "source_ids": ["med:early", "med:late"],
                               "propositions": ["tox_screen=negative", "tox_screen=positive_opioid"]}],
        "uncertainty": ["tox_screen"], "disposition": "CONFLICT_PRESERVED",
        "return_specialty": "Legal",
        "checks": {"relevance": True, "scope": True, "provenance": True,
                   "uncertainty": True, "perspective": True},
    }
    data.update(overrides)
    return ConsultationResult(**data)


class StructureTests(unittest.TestCase):
    def test_round_trips(self):
        objs = [
            describe("L03"),
            _edge(),
            _request(),
            _result(),
            InvocationEvent(seq=1, case_revision=0, invocation_id="inv-1", attempt_id="inv-1:a1",
                            slot_id="L33", selected="L33", input_ref="inv-1:evidence:in",
                            output_ref="inv-1:L33:out", disposition="OK"),
            ResolutionRecord(request_id="req-1", case_revision=0, required_feature="supply_voltage",
                             release_condition="supply_voltage measured"),
            CoherenceReview(origin_specialty="Legal", target_specialty="Medical", reviewer="U33",
                            verified_refs=["med:early"], result="COHERENT", reason="grounded"),
        ]
        for obj in objs:
            with self.subTest(obj=type(obj).__name__):
                obj.validate()
                restored = type(obj).from_dict(obj.to_dict())
                self.assertEqual(restored.to_dict(), obj.to_dict())

    def test_schema_files_are_stdlib_readable(self):
        schemas = Path(__file__).resolve().parent.parent / "schemas"
        names = {"slot_spec": "slot-spec/1.0", "association_edge": "association-edge/1.0",
                 "consultation_request": "consultation-request/1.0",
                 "consultation_result": "consultation-result/1.0",
                 "invocation_event": "invocation-event/1.0",
                 "resolution_record": "resolution-record/1.0",
                 "coherence_review": "coherence-review/1.0"}
        for name, version in names.items():
            with self.subTest(schema=name):
                data = json.loads((schemas / f"{name}.json").read_text())
                self.assertEqual(data["$id"], version)
                self.assertEqual(data["type"], "object")
                self.assertIn("properties", data)
                self.assertIn("required", data)

    def test_negative_fixtures(self):
        bad = [
            (lambda: _edge(origin_specialty="Legal", target_specialty="Legal"), "SAME_SPECIALTY"),
            (lambda: _edge(permitted_scope=["a"], excluded_scope=["a"]), "SCOPE_OVERLAP"),
            (lambda: _edge(max_uses=-1), "MAX_USES"),
            (lambda: _request(origin_specialty="Finance"), "ORIGIN_MISMATCH"),
            (lambda: _request(origin_specialty="Legal", target_specialty="Legal"), "CIRCULAR_ROUTE"),
            (lambda: _request(evidence_refs=[{"source_id": "med:absent",
                                               "verification_status": "VERIFIED",
                                               "key": "tox_screen", "value": "negative"}]),
             "UNQUALIFIED_REF"),
            (lambda: _request(budget={"max_stage_calls": 0}), "BUDGET"),
            (lambda: _result(disposition="BOGUS"), "DISPOSITION"),
            (lambda: _result(disposition="CONFLICT_PRESERVED", conflicting_pairs=[]), "CONFLICTS"),
            (lambda: _result(checks={"relevance": True}), "CHECKS"),
            (lambda: InvocationEvent(seq=0, case_revision=0, invocation_id="i", attempt_id="a",
                                     slot_id="L33", selected="L33", input_ref="x",
                                     output_ref="y", disposition="OK"), "SEQ"),
            (lambda: ResolutionRecord(request_id="r", case_revision=0,
                                      required_feature="f", disposition="BOGUS"), "DISPOSITION"),
            (lambda: CoherenceReview(origin_specialty="Legal", target_specialty="Medical",
                                     reviewer="U33", result="BOGUS"), "RESULT"),
        ]
        for make, code in bad:
            with self.subTest(code=code):
                with self.assertRaises(ValidationError) as ctx:
                    make().validate()
                self.assertEqual(ctx.exception.code, code)

    def test_review_rechecks_snapshot_not_specialist_assertion(self):
        specialist = {"status": "CONFLICT_PRESERVED", "finding": "conflict",
                      "perspective_tag": "medical_bounded_for_legal",
                      "citations": ["med:early", "med:late"],
                      "conflict_records": [{
                          "key": "tox_screen",
                          "records": [
                              {"source_id": "med:early", "key": "tox_screen", "value": "negative"},
                              {"source_id": "med:late", "key": "tox_screen", "value": "positive_opioid"}]}],
                      "uncertainty": ["tox_screen"]}
        review = CoherenceReview.review_specialist_output("Legal", "Medical", "U33", specialist, TOX)
        self.assertEqual(review.result, "CONFLICT_PRESERVED")
        # Forged provenance: same asserted status, different pairs.
        forged = copy.deepcopy(specialist)
        forged["conflict_records"] = [{"key": "tox_screen", "source_ids": ["forged:a", "forged:b"],
                                       "propositions": ["forged=1", "forged=2"]}]
        self.assertEqual(
            CoherenceReview.review_specialist_output("Legal", "Medical", "U33", forged, TOX).result,
            "PROVENANCE_LOST")
        # Drift: asserted SUPPORTED with a verdict tag, citations grounded
        # in the single-record snapshot so only perspective can fail.
        drifted = copy.deepcopy(specialist)
        drifted.update({"status": "SUPPORTED", "perspective_tag": "legal_verdict_overreach",
                        "citations": ["med:early"],
                        "conflict_records": [], "uncertainty": []})
        single = {"med:early": TOX["med:early"]}
        self.assertEqual(
            CoherenceReview.review_specialist_output("Legal", "Medical", "U33", drifted, single).result,
            "PERSPECTIVE_DRIFT")
        # Ungrounded citations.
        ungrounded = copy.deepcopy(specialist)
        ungrounded["citations"] = ["med:missing"]
        self.assertEqual(
            CoherenceReview.review_specialist_output("Legal", "Medical", "U33", ungrounded, TOX).result,
            "UNGROUNDED")


# ---------------------------------------------------------------------------
# 3. AssociationEdge persistence and selection
# ---------------------------------------------------------------------------

class AssociationEdgeTests(RuntimeCase):
    def test_store_load_after_reopen(self):
        self.controller.register_block_owner(1280, "L33", self._admin)
        edge = _edge()
        commit = store_edge(edge, self.closet.suit.adapter, "L33", family="BIG49", index=1280)
        self.assertEqual(commit["revision"], 1)
        # The mirror carries only a short reference (the full edge JSON is
        # over the association bucket's 512-char cap); COOL is authoritative.
        mirror_ref = commit["mirror_reference"]
        mirror = self.closet.suit.adapter.big.read(
            Pointer(mirror_ref["record_id"], mirror_ref["version"],
                    mirror_ref["digest"], "L33", "big"),
            principal="L33")
        self.assertEqual(mirror["content"], f"association-edge:{edge.edge_id}")
        self.assertFalse(mirror_ref["reconstructs_structured_payload"])
        loaded = load_edge(self.closet.suit.adapter, "L33", family="BIG49", index=1280)
        self.assertEqual(loaded.to_dict(), edge.to_dict())
        # Reopen the runtime and read the same edge back through COOL.
        self._stack.__exit__(None, None, None)
        stack2 = local_runtime(Path(self._tmp.name), self._admin)
        _, closet2, _ = stack2.__enter__()
        try:
            reopened = load_edge(closet2.suit.adapter, "L33", family="BIG49", index=1280)
            self.assertEqual(reopened.edge_id, edge.edge_id)
            self.assertEqual(reopened.uses, 0)
        finally:
            stack2.__exit__(None, None, None)
        # Re-enter the original runtime for tearDown.
        self._stack = local_runtime(Path(self._tmp.name), self._admin)
        self.controller, self.closet, _ = self._stack.__enter__()
        self.closet.suit.authorize_agent("L01", {"task.run"}, allowed_block_ids={0})

    def test_record_use_increments_and_restores(self):
        self.controller.register_block_owner(1280, "L33", self._admin)
        edge = _edge()
        store_edge(edge, self.closet.suit.adapter, "L33", family="BIG49", index=1280)
        updated = record_use(edge, self.closet.suit.adapter, "L33", family="BIG49", index=1280)
        self.assertEqual(updated.uses, 1)
        loaded = load_edge(self.closet.suit.adapter, "L33", family="BIG49", index=1280)
        self.assertEqual(loaded.uses, 1)
        self.assertEqual(self.controller._revision(1280), 2)

    def test_record_use_respects_max_uses(self):
        self.controller.register_block_owner(1280, "L33", self._admin)
        edge = _edge(max_uses=1)
        store_edge(edge, self.closet.suit.adapter, "L33", family="BIG49", index=1280)
        record_use(edge, self.closet.suit.adapter, "L33", family="BIG49", index=1280)
        with self.assertRaises(ValidationError) as ctx:
            record_use(_edge(max_uses=1, uses=1), self.closet.suit.adapter,
                       "L33", family="BIG49", index=1280)
        self.assertEqual(ctx.exception.code, "MAX_USES")

    def test_narrow_route_outranks_common_high_frequency_route(self):
        common = _edge(edge_id="edge:common", permitted_scope=["a", "b", "c", "d"], uses=50)
        narrow = _edge(edge_id="edge:narrow", permitted_scope=["a"], uses=1)
        self.assertIs(select_edge([common, narrow], "Legal", "Medical"), narrow)
        self.assertIs(select_edge([narrow, common], "Legal", "Medical"), narrow)
        # Frequency is the measured counter, independent of relevance: it
        # only breaks ties between equally narrow routes.
        tie_a = _edge(edge_id="edge:tie-a", permitted_scope=["a"], uses=3)
        tie_b = _edge(edge_id="edge:tie-b", permitted_scope=["a"], uses=9)
        self.assertIs(select_edge([tie_a, tie_b], "Legal", "Medical"), tie_b)
        disabled = _edge(edge_id="edge:off", permitted_scope=[], enabled=False)
        self.assertIsNone(select_edge([disabled], "Legal", "Medical"))
        self.assertIsNone(select_edge([common, narrow], "Medical", "Legal"))

    def test_propose_routes_is_read_only(self):
        common = _edge(edge_id="edge:common", permitted_scope=["a", "b", "c", "d"], uses=50)
        narrow = _edge(edge_id="edge:narrow", permitted_scope=["a"], uses=1)
        proposed = propose_routes([common, narrow], "Legal", "Medical")
        self.assertEqual([p["edge_id"] for p in proposed], ["edge:narrow", "edge:common"])
        self.assertEqual(common.uses, 50)
        self.assertEqual(narrow.uses, 1)


# ---------------------------------------------------------------------------
# 4. One public consult operation
# ---------------------------------------------------------------------------

class ConsultTests(RuntimeCase):
    def test_legal_to_medical_conflict_preserved(self):
        out = consult(self.closet, "L01", make_request(
            "case-1", "inv-1", "Legal", "Medical", TOX, ["med:early", "med:late"]))
        self.assertEqual(out["disposition"], "CONFLICT_PRESERVED")
        self.assertEqual(out["coherence_status"], "CONFLICT_PRESERVED")
        self.assertEqual(out["returned_to"], "Legal")
        self.assertEqual(out["result"]["checks"],
                         {"relevance": True, "scope": True, "provenance": True,
                          "uncertainty": True, "perspective": True})
        self.assertEqual([e["slot_id"] for e in out["events"]],
                         ["L33", "U32", "SPEC:Medical", "L34", "U33"])
        # Each stage links the producer's output ref to the next consumer's input ref.
        events = out["events"]
        for prev, nxt in zip(events, events[1:]):
            self.assertEqual(nxt["input_ref"], prev["output_ref"])
        self.assertTrue(events[0]["input_ref"].endswith(":evidence:in"))
        # Stages are recorded in SoSoContinuity.
        session = self.closet.retrieve_context("case-1")
        self.assertGreaterEqual(session["event_count"], 6)

    def test_legal_to_medical_coherent(self):
        out = consult(self.closet, "L01", make_request(
            "case-2", "inv-2", "Legal", "Medical",
            {"med:early": TOX["med:early"]}, ["med:early"]))
        self.assertEqual(out["disposition"], "COHERENT")
        self.assertEqual(out["review_result"], "COHERENT")

    def test_medical_to_legal_both_directions(self):
        conflict = consult(self.closet, "L01", make_request(
            "case-3", "inv-3", "Medical", "Legal", LEGAL, ["leg:a", "leg:b"]))
        self.assertEqual(conflict["disposition"], "CONFLICT_PRESERVED")
        self.assertEqual(conflict["returned_to"], "Medical")
        coherent = consult(self.closet, "L01", make_request(
            "case-4", "inv-4", "Medical", "Legal",
            {"leg:a": LEGAL["leg:a"]}, ["leg:a"]))
        self.assertEqual(coherent["disposition"], "COHERENT")
        self.assertIn("signed consent", coherent["result"]["claims"][0]["claim"])

    def test_missing_evidence_rejected(self):
        with self.assertRaises(ConsultationError) as ctx:
            consult(self.closet, "L01", make_request(
                "case-5", "inv-5", "Legal", "Medical", TOX, ["med:early"],
                evidence_refs=[{"source_id": "med:absent", "verification_status": "VERIFIED",
                                "key": "tox_screen", "value": "negative"}]))
        self.assertEqual(ctx.exception.code, "UNQUALIFIED_REF")

    def test_altered_provenance(self):
        # (a) ref value differs from the snapshot record.
        with self.assertRaises(ConsultationError) as ctx:
            consult(self.closet, "L01", make_request(
                "case-6", "inv-6", "Legal", "Medical", TOX, ["med:early"],
                evidence_refs=[{"source_id": "med:early", "verification_status": "VERIFIED",
                                "key": "tox_screen", "value": "positive_opioid"}]))
        self.assertEqual(ctx.exception.code, "UNQUALIFIED_REF")
        # (b) disputed snapshot record: verification rejects it, review holds.
        disputed = {"med:early": {**TOX["med:early"], "disputed": True}}
        out = consult(self.closet, "L01", make_request(
            "case-7", "inv-7", "Legal", "Medical", disputed, ["med:early"]))
        self.assertEqual(out["disposition"], "HOLD")
        self.assertEqual(out["review_result"], "UNGROUNDED")
        # (c) tampered conflict provenance via the test seam.
        out = consult(self.closet, "L01", make_request(
            "case-8", "inv-8", "Legal", "Medical", TOX, ["med:early", "med:late"]),
            tamper_conflict_provenance=True)
        self.assertEqual(out["disposition"], "HOLD")
        self.assertEqual(out["review_result"], "PROVENANCE_LOST")

    def test_perspective_drift_returns_hold(self):
        out = consult(self.closet, "L01", make_request(
            "case-9", "inv-9", "Legal", "Medical",
            {"med:early": TOX["med:early"]}, ["med:early"]), force_drift=True)
        self.assertEqual(out["disposition"], "HOLD")
        self.assertEqual(out["review_result"], "PERSPECTIVE_DRIFT")
        self.assertEqual(out["coherence_status"], "PERSPECTIVE_DRIFT")

    def test_unavailable_counterpart_and_circular_route(self):
        with self.assertRaises(ConsultationError) as ctx:
            consult(self.closet, "L01", make_request(
                "case-10", "inv-10", "Legal", "Finance", TOX, ["med:early"]))
        self.assertEqual(ctx.exception.code, "COUNTERPART_UNAVAILABLE")
        with self.assertRaises(ConsultationError) as ctx:
            consult(self.closet, "L01", make_request(
                "case-11", "inv-11", "Legal", "Legal", TOX, ["med:early"]))
        self.assertEqual(ctx.exception.code, "CIRCULAR_ROUTE")

    def test_duplicate_retry_and_changed_duplicate(self):
        request = make_request("case-12", "inv-12", "Legal", "Medical",
                               TOX, ["med:early", "med:late"])
        first = consult(self.closet, "L01", request)
        second = consult(self.closet, "L01", copy.deepcopy(request))
        self.assertEqual(second["ledger"], "duplicate_retry")
        self.assertEqual(second["disposition"], first["disposition"])
        self.assertEqual(second["result"], first["result"])
        changed = copy.deepcopy(request)
        changed["evidence_refs"] = refs_for(TOX, ["med:early"])
        with self.assertRaises(ConsultationError) as ctx:
            consult(self.closet, "L01", changed)
        self.assertEqual(ctx.exception.code, "DUPLICATE_CHANGED")

    def test_budget_excess_and_forbidden_identity(self):
        over = make_request("case-13", "inv-13", "Legal", "Medical",
                            {"med:early": TOX["med:early"]}, ["med:early"],
                            budget={"max_stage_calls": 2, "max_steps": 64})
        with self.assertRaises(ConsultationError) as ctx:
            consult(self.closet, "L01", over)
        self.assertEqual(ctx.exception.code, "BUDGET_EXCEEDED")
        for field in ("authority", "agent_id", "caller", "identity", "grant", "qualification"):
            bad = make_request("case-14", f"inv-14-{field}", "Legal", "Medical",
                               {"med:early": TOX["med:early"]}, ["med:early"])
            bad[field] = 0
            with self.subTest(field=field), self.assertRaises(ConsultationError) as ctx:
                consult(self.closet, "L01", bad)
            self.assertEqual(ctx.exception.code, "FORBIDDEN_IDENTITY")

    def test_consult_requires_task_run_grant(self):
        with self.assertRaises(Exception) as ctx:
            consult(self.closet, "L02", make_request(
                "case-15", "inv-15", "Legal", "Medical",
                {"med:early": TOX["med:early"]}, ["med:early"]))
        self.assertIn("not authorized", str(ctx.exception))


# ---------------------------------------------------------------------------
# 5. Self-Aware Cognitive Map — Phase A
# ---------------------------------------------------------------------------

def _pilot_events():
    return [
        InvocationEvent(seq=1, case_revision=0, invocation_id="case-A", attempt_id="case-A:a1",
                        slot_id="L22", selected="L22", input_ref="case-A:evidence:in",
                        output_ref="case-A:L22:out", disposition="OK"),
        InvocationEvent(seq=2, case_revision=0, invocation_id="case-A", attempt_id="case-A:a1",
                        slot_id="L33", selected="L33", input_ref="case-A:L22:out",
                        output_ref="case-A:L33:out", disposition="OK",
                        predecessor_id="case-A:L22:out"),
        InvocationEvent(seq=3, case_revision=0, invocation_id="case-A", attempt_id="case-A:a1",
                        slot_id="U32", selected="U32", input_ref="case-A:L33:out",
                        output_ref="case-A:U32:out", disposition="OK",
                        predecessor_id="case-A:L33:out",
                        rejected_alternatives=[{"alternative": "direct_specialist",
                                                "reason": "review required before specialist"}]),
        InvocationEvent(seq=4, case_revision=0, invocation_id="case-A", attempt_id="case-A:a1",
                        slot_id="SPEC:Medical", selected="SPEC:Medical",
                        input_ref="case-A:U32:out", output_ref="case-A:SPEC:out",
                        disposition="CONFLICT_PRESERVED", predecessor_id="case-A:U32:out"),
    ]


class CognitiveMapTests(unittest.TestCase):
    def test_replay_twice_is_identical_and_does_not_mutate(self):
        events = _pilot_events()
        case_hash = hashlib.sha256(
            json.dumps([e.to_dict() for e in events], sort_keys=True).encode()).hexdigest()
        map1, graph1 = replay(events, "case-A", "Compare the entries")
        map2, graph2 = replay(events, "case-A", "Compare the entries")
        self.assertEqual(map1, map2)
        self.assertEqual(graph1, graph2)
        self.assertEqual(map1["selected_branch"], "SPEC:Medical")
        self.assertEqual(map1["disposition"], "CONFLICT_PRESERVED")
        self.assertEqual(map1["resume_point"], "direct_specialist")
        self.assertEqual(
            hashlib.sha256(json.dumps([e.to_dict() for e in events], sort_keys=True).encode()).hexdigest(),
            case_hash)

    def test_recorder_mapper_opening_and_closing(self):
        recorder = InvestigationRecorder("case-A", "Compare the entries")
        for event in _pilot_events():
            recorder.record(event)
        cognitive_map = recorder.build_map()
        self.assertEqual(cognitive_map.case_id, "case-A")
        relations = RelationshipMapper.map_relationships(
            ["med:early", "med:late"],
            [{"claim": "tox_screen conflicts", "source_refs": ["med:early", "med:late"]}])
        self.assertTrue(all(r["relation"] == "SUPPORTS" for r in relations))
        opening = OpeningSequence.check(
            "case-A", "Compare the entries", ["compare supplied records"], TOX,
            [{"key": "tox_screen"}], ["supply_voltage"], "L22")
        self.assertEqual(opening["task"]["status"], "READY")
        self.assertEqual(opening["evidence"]["status"], "PRESENT")
        self.assertEqual(opening["conflicts"]["count"], 1)
        self.assertEqual(opening["starting_route"]["route"], "L22")
        closing = ClosingReview.review(cognitive_map)
        self.assertEqual(closing["disposition"], "CONFLICT_PRESERVED")
        self.assertEqual(closing["conflict_count"], 1)


# ---------------------------------------------------------------------------
# Phase B — advisory suggestions, matching, reconciliation
# ---------------------------------------------------------------------------

class PhaseBTests(unittest.TestCase):
    def test_route_advisor_records_common_and_rare_routes(self):
        advisor = RouteAdvisor()
        made = advisor.suggest_routes("case-B", [
            {"slot_id": "L22", "eligible": True, "reason": "common chronology route",
             "expected_effect": "orders evidence by time"},
            {"slot_id": "L33", "eligible": True, "reason": "narrower consultation route",
             "expected_effect": "bounded cross-specialty question"},
            {"slot_id": "U09", "eligible": False, "reason": "motor disposition, not relevant here",
             "expected_effect": "none"},
        ])
        self.assertEqual(len(made), 3)
        self.assertTrue(all(s["advisory"] for s in made))
        self.assertFalse(made[2]["eligible"])
        reviewed = advisor.review_outcome(made[0]["suggestion_id"], "evidence ordered", "FOLLOWED")
        self.assertEqual(reviewed["observed_effect"], "evidence ordered")
        with self.assertRaises(KeyError):
            advisor.review_outcome("case-B:suggest:99", "x", "FOLLOWED")

    def test_reading_matcher_correlates_one_pending_request(self):
        record = ResolutionRecord(request_id="req-1", case_revision=3,
                                  required_feature="supply_voltage",
                                  supporting_evidence=["ev:1"])
        observations = [
            {"feature": "temperature", "case_revision": 3, "source_id": "ev:1", "conclusive": True},
            {"feature": "supply_voltage", "case_revision": 2, "source_id": "ev:1", "conclusive": True},
            {"feature": "supply_voltage", "case_revision": 3, "source_id": "ev:1",
             "value": "24V_nominal", "conclusive": True},
        ]
        matched = ReadingMatcher.match_pending_request(record, observations)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["observation"]["value"], "24V_nominal")
        self.assertIsNone(ReadingMatcher.match_pending_request(record, observations[:2]))

    def test_resolution_record_outcomes(self):
        def fresh():
            return ResolutionRecord(request_id="req-1", case_revision=3,
                                    required_feature="supply_voltage",
                                    supporting_evidence=["ev:1"])
        valid = {"observation_id": "o1", "feature": "supply_voltage", "case_revision": 3,
                 "source_id": "ev:1", "value": "24V_nominal", "conclusive": True}
        record = fresh()
        self.assertEqual(record.apply_observation(valid)["outcome"], "RELEASED")
        self.assertEqual(record.disposition, "RELEASED")
        self.assertEqual(record.apply_observation(valid)["outcome"], "DUPLICATE")
        self.assertEqual(fresh().apply_observation(
            {**valid, "feature": "temperature"})["outcome"], "WRONG_FEATURE")
        self.assertEqual(fresh().apply_observation(
            {**valid, "case_revision": 4})["outcome"], "STALE")
        self.assertEqual(fresh().apply_observation(
            {**valid, "source_id": "ev:9"})["outcome"], "MISSING_EVIDENCE")
        inconclusive = fresh()
        self.assertEqual(inconclusive.apply_observation(
            {**valid, "conclusive": False})["outcome"], "INCONCLUSIVE")
        self.assertEqual(inconclusive.disposition, "PENDING")
        # A pending requirement survives every non-releasing outcome.
        self.assertEqual(fresh().disposition, "PENDING")

    def test_reconciliation_and_outcome_review(self):
        before = CognitiveMap(case_id="case-B", goal="g", unknowns=[{"alternative": "L33", "reason": "unasked"}],
                              disposition="OK", resume_point="L33")
        after = CognitiveMap(case_id="case-B", goal="g", evidence=["case-B:L33:out"],
                             unknowns=[], selected_branch="L33", disposition="CONFLICT_PRESERVED",
                             resume_point="complete")
        rec = InvestigationReconciliation.reconcile(before, after, [{"feature": "tox_screen"}])
        self.assertEqual(rec["resolved_unknowns"], ["L33"])
        self.assertEqual(rec["new_evidence"], ["case-B:L33:out"])
        self.assertEqual(rec["disposition_change"], {"from": "OK", "to": "CONFLICT_PRESERVED"})
        self.assertEqual(rec["unresolved_remaining"], [])
        review = OutcomeReview.review(
            OpeningSequence.check("case-B", "g", ["s"], TOX, [], ["L33"], "L22"),
            ClosingReview.review(after), rec)
        self.assertEqual(review["resolved_unknowns"], ["L33"])
        self.assertTrue(review["disposition_changed"])


# ---------------------------------------------------------------------------
# Phase C — simplification experiment, and reference cases
# ---------------------------------------------------------------------------

class PhaseCTests(RuntimeCase):
    def test_parity_conflict_fixture_legal_to_medical(self):
        result = run_phase_c(make_fixture("pc-1", "Legal", "Medical", TOX, ["med:early", "med:late"]),
                             self.closet, "L01")
        self.assertTrue(result["parity"], result["comparisons"])
        self.assertEqual(result["verdict"], "PARITY")
        self.assertEqual(result["calls"],
                         {"B": {"public": 5, "internal": 5}, "C": {"public": 1, "internal": 5}})
        self.assertEqual(result["handoffs"], {"B": 5, "C": 1})
        self.assertTrue(result["simplification"])
        self.assertEqual(result["arms"]["ablation"]["coherence_status"], "UNCHECKED")
        self.assertEqual(result["arms"]["A"]["returned_to"], "Medical")  # direct call does not return
        self.assertEqual(result["arms"]["B"]["returned_to"], "Legal")
        self.assertEqual(result["arms"]["C"]["returned_to"], "Legal")

    def test_parity_coherent_fixture_medical_to_legal(self):
        result = run_phase_c(make_fixture("pc-2", "Medical", "Legal",
                                          {"leg:a": LEGAL["leg:a"]}, ["leg:a"]),
                             self.closet, "L01")
        self.assertTrue(result["parity"], result["comparisons"])
        self.assertEqual(result["arms"]["B"]["coherence_status"], "COHERENT")
        self.assertEqual(result["arms"]["C"]["coherence_status"], "COHERENT")

    def test_parity_dimensions_cover_the_brief(self):
        result = run_phase_c(make_fixture("pc-3", "Legal", "Medical", TOX, ["med:early", "med:late"]),
                             self.closet, "L01")
        dimensions = {c["dimension"] for c in result["comparisons"]}
        self.assertEqual(dimensions, {"result_status", "scope", "citations", "origin_returned_to",
                                      "provenance_intact", "conflict_preservation", "uncertainty",
                                      "selected_route", "reconstructible_trace"})


class ReferenceCaseTests(unittest.TestCase):
    def test_lower_result_changes_higher_behaviour(self):
        # L03 -> L17/L18 -> L31 -> U16: one genuine Lower result changing Higher behaviour.
        contra = [
            {"source": "s1", "subject": "dog", "predicate": "state", "value": "awake",
             "scope": "default", "event": "2026-01-01T00:00:00Z"},
            {"source": "s2", "subject": "dog", "predicate": "state", "value": "asleep",
             "scope": "default", "event": "2026-01-01T00:00:00Z"},
        ]
        parsed = execute_typed("L03", AUTH, contra)["result"]
        pairs = execute_typed("L17", AUTH, parsed["accepted"])["result"]
        conflicts = execute_typed("L18", AUTH, pairs)["result"]
        self.assertEqual(len(conflicts), 1)
        single = execute_typed("L31", AUTH, parsed["accepted"][:1], [], False)["result"]
        self.assertEqual(single["status"], "SUPPORTED")
        with_conflict = execute_typed("L31", AUTH, parsed["accepted"], conflicts, False)["result"]
        self.assertEqual(with_conflict["status"], "CONFLICT")
        synthesised = execute_typed("U16", AUTH, with_conflict, parsed["accepted"])["result"]
        self.assertEqual(synthesised["presented_sources"], {
            "s1": [parsed["accepted"][0]["digest"]],
            "s2": [parsed["accepted"][1]["digest"]]})

    def test_pilot_stage_order_is_the_reference_route(self):
        from contract.context_envelope import ContextEnvelope, to_plain
        from contract.cross_specialty import CROSS_OPERATIONS, run_consultation
        chronology = CROSS_OPERATIONS["L22"]([
            {"digest": "med:late", "event": "2026-01-01T02:00:00Z"},
            {"digest": "med:early", "event": "2026-01-01T01:00:00Z"},
        ])
        ordered = [row["digest"] for group in chronology["groups"] for row in group["rows"]]
        self.assertEqual(ordered, ["med:early", "med:late"])
        env = ContextEnvelope(
            investigation_id="ref-pilot", originating_question="Compare the entries",
            originating_specialty="Legal", current_specialty="Legal",
            consultation_purpose="consult_medical", evidence_store=TOX,
            evidence_refs=refs_for(TOX, ordered), feature_enabled=True,
            execution_trace=({"function_id": "L22", "output": {"ordered_source_ids": ordered}},))
        out = run_consultation(env, "Medical")
        self.assertEqual(out.coherence_status, "CONFLICT_PRESERVED")
        self.assertEqual([s["function_id"] for s in to_plain(out.execution_trace)],
                         ["L22", "L33", "U32", "SPEC:Medical", "L34", "U33"])
        self.assertEqual(out.current_specialty, "Legal")


if __name__ == "__main__":
    unittest.main()
