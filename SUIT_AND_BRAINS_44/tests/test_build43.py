"""Build 043 tests: four modes, joins, requirements, HOLD/resume, multi-slot
review, terminal cases, and the case-path capacity check. Stdlib unittest,
disposable state, synthetic fixtures only.
"""
import copy
import json
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build040_runtime.investigation import (  # noqa: E402
    Investigation,
    MODE_DEFAULT,
    MODES,
    case_block_id,
)
from build040_runtime.standalone import local_runtime  # noqa: E402
from contract.slot_specs import SLOT_MAP_VERSION, registry  # noqa: E402
from realcog.ph6_brains_2208 import Rejected  # noqa: E402

ADMIN = secrets.token_hex(24)
AGENT = "L01"
TREE = Path(__file__).resolve().parent.parent

TOX_EARLY = {"med:early": {"discipline": "medical", "key": "tox_screen", "value": "negative"}}
TOX = {**TOX_EARLY,
       "med:late": {"discipline": "medical", "key": "tox_screen", "value": "positive_opioid"}}
HYPOTHESES = {"H1": {"tox_screen": "negative"}, "H2": {"tox_screen": "positive_opioid"}}
# Reference conflict fixture (L03/L17/L18/L31 -> U16): two readings of one
# state, same event, different values.
CONTRA = [
    {"source": "s1", "subject": "dog", "predicate": "state", "value": "awake",
     "scope": "default", "event": "2026-01-01T00:00:00Z"},
    {"source": "s2", "subject": "dog", "predicate": "state", "value": "asleep",
     "scope": "default", "event": "2026-01-01T00:00:00Z"},
]
COHERENT = [dict(CONTRA[0])]


def _investigation(closet, n_blocks=4, **kwargs):
    for i in range(n_blocks):
        closet.suit.adapter.controller.register_block_owner(case_block_id(i), AGENT, ADMIN)
    closet.suit.authorize_agent(
        AGENT, {"memory.read", "memory.write", "task.run"},
        allowed_block_ids={case_block_id(i) for i in range(n_blocks)},
        max_payload_bytes=65536)
    return Investigation(closet, AGENT, **kwargs)


def _matching_obs(feature, source_id, value, revision=0, obs_id="obs-1", conclusive=True):
    return {"observation_id": obs_id, "feature": feature, "source_id": source_id,
            "value": value, "case_revision": revision, "conclusive": conclusive,
            "event": "2026-01-01T02:00:00Z"}


class ModeTests(unittest.TestCase):
    def test_modes_are_four_and_default_is_joined_lower_first(self):
        self.assertEqual(MODES, ("lower_only", "higher_only", "joined_lower_first",
                                 "joined_higher_first"))
        self.assertEqual(MODE_DEFAULT, "joined_lower_first")

    def test_baseline_precision_105_and_controller_tests_still_pass(self):
        # The 42.1.2 replay wiring still works on Build 043: run the bundled
        # test files by path, outside the tree, against the tree on PYTHONPATH.
        import os
        env = dict(os.environ, PYTHONPATH=str(TREE))
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("test_precision_105_build040.py", "test_crampu_controller.py"):
                with self.subTest(name=name):
                    res = subprocess.run(
                        [sys.executable, str(TREE / "tests" / name)],
                        cwd=tmp, env=env, capture_output=True, text=True, timeout=600)
                    self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_no_new_numeric_assignments(self):
        # Build 043 adds named responsibilities, not numeric slots: the 98
        # IDs and their bindings are unchanged from 42.1.2.
        specs = registry()
        self.assertEqual(len(specs), 98)
        states = {s.state for s in specs}
        bound = [s for s in specs if s.state == "bound"]
        dedicated = [s for s in specs if s.state == "dedicated"]
        generic = [s for s in specs if s.state == "generic-only"]
        self.assertEqual((len(bound), len(dedicated), len(generic)), (20, 1, 77))
        self.assertEqual(SLOT_MAP_VERSION, "42.1")
        self.assertEqual(states, {"bound", "dedicated", "generic-only"})


class LowerOnlyTests(unittest.TestCase):
    def test_lower_only_never_invokes_higher(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                case = inv.start_case("lo-1", mode="lower_only", goal="order the dog state",
                                      evidence_snapshot={}, scope=["dog"])
                case = inv.execute_mode("lo-1", observations=copy.deepcopy(CONTRA))
                self.assertEqual(case["status"], "COMPLETED")
                self.assertEqual(case["decision"]["status"], "CONFLICT")
                tiers = {i["tier"] for i in case["invocations"]}
                self.assertEqual(tiers, {"LOWER"})
                slots = [i["slot_id"] for i in case["invocations"]]
                self.assertEqual(slots, ["L03", "L04", "L16", "L22", "L17", "L18", "L31"])

    def test_lower_only_unresolved_is_an_explicit_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                inv.start_case("lo-2", mode="lower_only", goal="nothing to parse",
                               evidence_snapshot={}, scope=[])
                case = inv.execute_mode("lo-2", observations=[])
                self.assertEqual(case["status"], "UNRESOLVED")
                self.assertEqual(len(case["requirements"]), 1)
                self.assertEqual(case["requirements"][0]["required_feature"],
                                 "parseable_observation")
                self.assertEqual(case["waiting"], [])  # not waiting: terminal outcome
                self.assertEqual({i["tier"] for i in case["invocations"]}, {"LOWER"})


class HigherOnlyTests(unittest.TestCase):
    def test_higher_only_never_invokes_lower(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                inv.start_case("ho-1", mode="higher_only",
                               goal="decide the tox explanation",
                               evidence_snapshot=copy.deepcopy(TOX), scope=["tox_screen"])
                case = inv.execute_mode(
                    "ho-1", hypotheses=copy.deepcopy(HYPOTHESES),
                    observed={"tox_screen": "positive_opioid"})
                self.assertEqual(case["status"], "COMPLETED")
                self.assertEqual(case["decision"]["status"], "SUPPORTED")
                self.assertEqual(case["decision"]["answer"], "H2")
                self.assertEqual({i["tier"] for i in case["invocations"]}, {"HIGHER"})

    def test_higher_only_unresolved_records_the_missing_discriminator(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                inv.start_case("ho-2", mode="higher_only",
                               goal="decide with no reading",
                               evidence_snapshot=copy.deepcopy(TOX_EARLY), scope=["tox_screen"])
                case = inv.execute_mode(
                    "ho-2", hypotheses=copy.deepcopy(HYPOTHESES), observed={})
                self.assertEqual(case["status"], "UNRESOLVED")
                self.assertEqual(case["decision"]["status"], "ABSTAIN")
                self.assertEqual(case["requirements"][0]["required_feature"], "tox_screen")
                self.assertEqual({i["tier"] for i in case["invocations"]}, {"HIGHER"})


class JoinedLowerFirstTests(unittest.TestCase):
    def _run(self, inv, case_id, observations):
        inv.start_case(case_id, mode="joined_lower_first",
                       goal="Lower result consumed by Higher",
                       evidence_snapshot=copy.deepcopy(TOX), scope=["tox_screen"],
                       hypotheses=copy.deepcopy(HYPOTHESES), observed={})
        return inv.execute_mode(case_id, observations=copy.deepcopy(observations),
                                hypotheses=copy.deepcopy(HYPOTHESES), observed={})

    def test_crossing_consumed_and_higher_changes_with_lower(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                coherent = self._run(inv, "jlf-1", COHERENT)
                conflict = self._run(inv, "jlf-2", CONTRA)
                for case in (coherent, conflict):
                    self.assertEqual(case["status"], "COMPLETED")
                    joins = [j for j in case["joins"] if j["direction"] == "LOWER_TO_HIGHER"]
                    self.assertEqual(len(joins), 1)
                    self.assertEqual(joins[0]["status"], "CONSUMED")
                    # The consumer's input reference is the producer's output reference.
                    u16 = [i for i in case["invocations"] if i["slot_id"] == "U16"]
                    self.assertEqual(len(u16), 1)
                    self.assertEqual(u16[0]["input_ref"], joins[0]["producer"]["output_ref"])
                # The Higher result changes when the Lower result changes:
                # the synthesis presents one source for the coherent run and
                # both sources for the conflicting run.
                self.assertEqual(coherent["decision"]["status"], "SUPPORTED")
                self.assertEqual(conflict["decision"]["status"], "CONFLICT")
                self.assertNotEqual(coherent["decision"], conflict["decision"])
                self.assertEqual(
                    sorted(coherent["decision"].get("presented_sources", {})), ["s1"])
                self.assertEqual(
                    sorted(conflict["decision"].get("presented_sources", {})),
                    ["s1", "s2"])

    def test_unavailable_counterpart_is_unresolved_not_a_crossing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                case = inv.start_case("jlf-3", mode="joined_lower_first",
                                      goal="Lower result with no Higher consumer",
                                      evidence_snapshot={}, scope=["dog"])
                inv._lower_route(case, copy.deepcopy(CONTRA))
                case["inputs"]["hypotheses"] = copy.deepcopy(HYPOTHESES)
                case["inputs"]["observed"] = {}
                lower_ref = [o for o in case["outputs"] if o["slot_id"] == "L31"][0]["output_ref"]
                join = inv.join_lower_to_higher(case, lower_output_ref=lower_ref,
                                                consumer_slot="U01")
                self.assertEqual(join["status"], "FAILED")
                self.assertEqual(join["failure"]["code"], "UNSUPPORTED_SLOT")
                self.assertEqual(case["status"], "UNRESOLVED")


class JoinedHigherFirstTests(unittest.TestCase):
    def _setup(self, closet):
        inv = _investigation(closet)
        inv.start_case("jhf-1", mode="joined_higher_first",
                       goal="Decide which tox-screen explanation holds",
                       evidence_snapshot=copy.deepcopy(TOX_EARLY), scope=["tox_screen"],
                       origin_specialty="Medical", target_specialty="Medical",
                       acceptable_sources=["med:late"],
                       hypotheses=copy.deepcopy(HYPOTHESES), observed={})
        return inv

    def test_proof_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = self._setup(closet)
                case = inv.execute_mode("jhf-1", hypotheses=copy.deepcopy(HYPOTHESES),
                                        observed={})
                self.assertEqual(case["status"], "HOLD")
                req = case["requirements"][0]
                self.assertEqual(req["required_feature"], "tox_screen")
                self.assertEqual(req["supporting_evidence"], ["med:late"])
                self.assertEqual(case["resume_point"], f"requirement:{req['request_id']}")
                # Unrelated reading: mismatch, request stays open, revision unchanged.
                out = inv.receive_observation(
                    "jhf-1", _matching_obs("signed_consent", "leg:a", "present",
                                           obs_id="obs-unrelated"))
                self.assertEqual(out["outcome"], "WRONG_FEATURE")
                self.assertEqual(case["status"], "HOLD")
                self.assertEqual(case["revision"], 0)
                # Matching reading: released, Lower runs, Higher consumes.
                out = inv.receive_observation(
                    "jhf-1", _matching_obs("tox_screen", "med:late", "positive_opioid",
                                           obs_id="obs-matching"))
                self.assertEqual(out["outcome"], "RELEASED")
                case = out["case"]
                self.assertEqual(case["status"], "COMPLETED")
                self.assertEqual(case["revision"], 1)
                self.assertEqual(out["lower"]["decision"]["status"], "CONFLICT")
                self.assertEqual(len(out["lower"]["conflicts"]), 1)
                self.assertEqual(out["higher"]["decision"]["status"], "CONFLICT")
                self.assertEqual(out["higher"]["remaining"], {"H2": {"tox_screen": "positive_opioid"}})
                self.assertEqual(out["join"]["status"], "CONSUMED")
                rec = case["reconciliation"]
                self.assertTrue(rec["decision_changed"])
                self.assertIsNone(rec["decision_before"])
                self.assertEqual(rec["decision_after"]["status"], "CONFLICT")
                self.assertEqual(rec["resolved_unknowns"], ["missing:tox_screen"])
                self.assertEqual(rec["unresolved_remaining"], [])

    def test_proof_with_restart_restores_mode_hold_and_revision(self):
        root = Path(tempfile.mkdtemp())
        with local_runtime(root, ADMIN) as (_ctrl, closet, _loop):
            inv = self._setup(closet)
            case = inv.execute_mode("jhf-1", hypotheses=copy.deepcopy(HYPOTHESES), observed={})
            self.assertEqual(case["status"], "HOLD")
            cool_after_hold = case["durable"]["cool_revision"]
        # Genuine restart: a fresh runtime on the same durable directories.
        with local_runtime(root, ADMIN) as (_ctrl, closet, _loop):
            inv2 = _investigation(closet)
            case = inv2.resume_after_restart("jhf-1", 0)
            self.assertEqual(case["mode"], "joined_higher_first")
            self.assertEqual(case["status"], "HOLD")
            self.assertEqual(case["revision"], 0)
            self.assertEqual(case["durable"]["cool_revision"], cool_after_hold)
            out = inv2.receive_observation(
                "jhf-1", _matching_obs("tox_screen", "med:late", "positive_opioid",
                                       obs_id="obs-matching"))
            self.assertEqual(out["outcome"], "RELEASED")
            self.assertEqual(out["case"]["status"], "COMPLETED")
            self.assertEqual(out["case"]["decision"]["status"], "CONFLICT")

    def test_stale_revision_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = self._setup(closet)
                inv.execute_mode("jhf-1", hypotheses=copy.deepcopy(HYPOTHESES), observed={})
                out = inv.receive_observation(
                    "jhf-1", _matching_obs("tox_screen", "med:late", "positive_opioid",
                                           revision=99, obs_id="obs-stale"))
                self.assertEqual(out["outcome"], "STALE")
                self.assertEqual(out["case"]["status"], "HOLD")
                self.assertEqual(out["case"]["revision"], 0)

    def test_duplicate_and_inconclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = self._setup(closet)
                inv.execute_mode("jhf-1", hypotheses=copy.deepcopy(HYPOTHESES), observed={})
                # Inconclusive: valid and matching, but not conclusive — the
                # requirement stays pending and the observation id is recorded.
                out = inv.receive_observation(
                    "jhf-1", _matching_obs("tox_screen", "med:late", "positive_opioid",
                                           obs_id="obs-inc", conclusive=False))
                self.assertEqual(out["outcome"], "INCONCLUSIVE")
                self.assertEqual(out["case"]["status"], "HOLD")
                # Same observation id again: duplicate, no advance.
                out = inv.receive_observation(
                    "jhf-1", _matching_obs("tox_screen", "med:late", "positive_opioid",
                                           obs_id="obs-inc", conclusive=False))
                self.assertEqual(out["outcome"], "DUPLICATE")
                self.assertEqual(out["case"]["status"], "HOLD")
                # A conclusive reading under a new id releases it.
                out = inv.receive_observation(
                    "jhf-1", _matching_obs("tox_screen", "med:late", "positive_opioid",
                                           obs_id="obs-final"))
                self.assertEqual(out["outcome"], "RELEASED")
                self.assertEqual(out["case"]["status"], "COMPLETED")

    def test_expired_after_max_resumptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet, max_resumptions=0)
                inv.start_case("jhf-exp", mode="joined_higher_first",
                               goal="bounded resumption",
                               evidence_snapshot=copy.deepcopy(TOX_EARLY), scope=["tox_screen"],
                               acceptable_sources=["med:late"],
                               hypotheses=copy.deepcopy(HYPOTHESES), observed={})
                inv.execute_mode("jhf-exp", hypotheses=copy.deepcopy(HYPOTHESES), observed={})
                out = inv.receive_observation(
                    "jhf-exp", _matching_obs("tox_screen", "med:late", "positive_opioid",
                                             obs_id="obs-1"))
                self.assertEqual(out["outcome"], "EXPIRED")
                self.assertEqual(out["case"]["status"], "EXPIRED")

    def test_no_progress_closes_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                case = inv.start_case("jhf-np", mode="joined_higher_first",
                                      goal="repeated route without progress",
                                      evidence_snapshot=copy.deepcopy(TOX_EARLY),
                                      scope=["tox_screen"],
                                      acceptable_sources=["med:late"],
                                      hypotheses=copy.deepcopy(HYPOTHESES), observed={})
                # Three requirements, each for a different feature; every
                # release leaves the Lower decision at ABSTAIN (rows with
                # unrelated values), so the second and third resumes make no
                # progress.
                for feature, source in (("signed_consent", "leg:a"),
                                        ("blood_pressure", "med:bp"),
                                        ("heart_rate", "med:hr")):
                    inv.request_information(case, consumer_slot="L03",
                                            required_feature=feature, source_ids=[source])
                self.assertEqual(case["status"], "HOLD")
                for i, (feature, source, value) in enumerate(
                        (("signed_consent", "leg:a", "present"),
                         ("blood_pressure", "med:bp", "120"),
                         ("heart_rate", "med:hr", "72"))):
                    out = inv.receive_observation(
                        "jhf-np", _matching_obs(feature, source, value, obs_id=f"obs-{i}"))
                    self.assertEqual(out["outcome"], "RELEASED", out)
                    self.assertEqual(out["case"]["decision"]["status"], "ABSTAIN")
                case = out["case"]
                self.assertEqual(case["status"], "UNRESOLVED")
                self.assertEqual(case["reconciliation"]["outcome"], "NO_PROGRESS")
                self.assertEqual(case["resumptions"], 3)


class IndependentFollowUpTests(unittest.TestCase):
    """A later observation must not reopen an independent-mode case.

    Jd's finding on 43.0.0: a higher_only case reported terminal UNRESOLVED,
    then a matching observation ran both Higher and Lower while the case
    still said higher_only. The saved mode now governs every entry point:
    the observation is recorded but never consumed, no slot runs, the case
    keeps its mode, status, revision and resumption count, and the
    requirement stays PENDING. Same rule after a genuine restart, where the
    mode comes from the saved case. The joined Higher-first HOLD -> restart
    -> resume test above stays the control that resume still works where
    the product supports it.
    """

    def _terminal(self, inv, case, tiers):
        """Shared assertions for a terminal independent case after a
        matching follow-up observation."""
        self.assertEqual(case["status"], "UNRESOLVED")
        self.assertEqual(case["revision"], 0)
        self.assertEqual(case["resumptions"], 0)
        self.assertEqual({i["tier"] for i in case["invocations"]}, tiers)
        self.assertEqual(case["requirements"][0]["disposition"], "PENDING")
        last = case["observations"][-1]
        self.assertEqual(last["outcome"], "TERMINAL")
        self.assertFalse(last["consumed"])

    def test_higher_only_follow_up_stays_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                inv.start_case("ho-f1", mode="higher_only",
                               goal="decide with no reading",
                               evidence_snapshot=copy.deepcopy(TOX_EARLY),
                               scope=["tox_screen"])
                case = inv.execute_mode("ho-f1", hypotheses=copy.deepcopy(HYPOTHESES),
                                        observed={})
                self.assertEqual(case["status"], "UNRESOLVED")
                self.assertEqual(case["requirements"][0]["required_feature"], "tox_screen")
                decision_before = copy.deepcopy(case["decision"])
                cool_before = case["durable"]["cool_revision"]
                n_invocations = len(case["invocations"])
                out = inv.receive_observation(
                    "ho-f1", _matching_obs("tox_screen", "med:late", "positive_opioid",
                                           obs_id="obs-ho-f1"))
                self.assertEqual(out["outcome"], "TERMINAL")
                case = out["case"]
                self._terminal(inv, case, {"HIGHER"})
                self.assertEqual(case["mode"], "higher_only")
                self.assertEqual(case["decision"], decision_before)
                self.assertEqual(len(case["invocations"]), n_invocations)
                # The arrival is recorded (one save), nothing is consumed.
                self.assertEqual(case["durable"]["cool_revision"], cool_before + 1)
                # A second matching observation is refused the same way.
                out = inv.receive_observation(
                    "ho-f1", _matching_obs("tox_screen", "med:late", "positive_opioid",
                                           obs_id="obs-ho-f2"))
                self.assertEqual(out["outcome"], "TERMINAL")
                self.assertEqual({i["tier"] for i in out["case"]["invocations"]},
                                 {"HIGHER"})

    def test_higher_only_follow_up_stays_terminal_after_restart(self):
        root = Path(tempfile.mkdtemp())
        with local_runtime(root, ADMIN) as (_ctrl, closet, _loop):
            inv = _investigation(closet)
            inv.start_case("ho-f2", mode="higher_only",
                           goal="decide with no reading",
                           evidence_snapshot=copy.deepcopy(TOX_EARLY),
                           scope=["tox_screen"])
            case = inv.execute_mode("ho-f2", hypotheses=copy.deepcopy(HYPOTHESES),
                                    observed={})
            self.assertEqual(case["status"], "UNRESOLVED")
        with local_runtime(root, ADMIN) as (_ctrl, closet, _loop):
            inv2 = _investigation(closet)
            case = inv2.resume_after_restart("ho-f2", 0)
            self.assertEqual(case["mode"], "higher_only")
            self.assertEqual(case["status"], "UNRESOLVED")
            self.assertEqual(case["revision"], 0)
            tiers_before = {i["tier"] for i in case["invocations"]}
            n_before = len(case["invocations"])
            out = inv2.receive_observation(
                "ho-f2", _matching_obs("tox_screen", "med:late", "positive_opioid",
                                       obs_id="obs-ho-restart"))
            self.assertEqual(out["outcome"], "TERMINAL")
            case = out["case"]
            self.assertEqual(case["mode"], "higher_only")
            self.assertEqual(case["status"], "UNRESOLVED")
            self.assertEqual(case["revision"], 0)
            self.assertEqual(case["requirements"][0]["disposition"], "PENDING")
            self.assertEqual({i["tier"] for i in case["invocations"]}, tiers_before)
            self.assertEqual(len(case["invocations"]), n_before)

    def test_lower_only_follow_up_stays_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                inv.start_case("lo-f1", mode="lower_only", goal="nothing to parse",
                               evidence_snapshot={}, scope=[])
                case = inv.execute_mode("lo-f1", observations=[])
                self.assertEqual(case["status"], "UNRESOLVED")
                self.assertEqual(case["requirements"][0]["required_feature"],
                                 "parseable_observation")
                n_invocations = len(case["invocations"])
                out = inv.receive_observation(
                    "lo-f1", _matching_obs("parseable_observation", "s1", "awake",
                                           obs_id="obs-lo-f1"))
                self.assertEqual(out["outcome"], "TERMINAL")
                case = out["case"]
                self._terminal(inv, case, {"LOWER"})
                self.assertEqual(case["mode"], "lower_only")
                self.assertEqual(len(case["invocations"]), n_invocations)

    def test_lower_only_follow_up_stays_terminal_after_restart(self):
        root = Path(tempfile.mkdtemp())
        with local_runtime(root, ADMIN) as (_ctrl, closet, _loop):
            inv = _investigation(closet)
            inv.start_case("lo-f2", mode="lower_only", goal="nothing to parse",
                           evidence_snapshot={}, scope=[])
            case = inv.execute_mode("lo-f2", observations=[])
            self.assertEqual(case["status"], "UNRESOLVED")
        with local_runtime(root, ADMIN) as (_ctrl, closet, _loop):
            inv2 = _investigation(closet)
            case = inv2.resume_after_restart("lo-f2", 0)
            self.assertEqual(case["mode"], "lower_only")
            self.assertEqual(case["status"], "UNRESOLVED")
            tiers_before = {i["tier"] for i in case["invocations"]}
            n_before = len(case["invocations"])
            out = inv2.receive_observation(
                "lo-f2", _matching_obs("parseable_observation", "s1", "awake",
                                       obs_id="obs-lo-restart"))
            self.assertEqual(out["outcome"], "TERMINAL")
            case = out["case"]
            self.assertEqual(case["mode"], "lower_only")
            self.assertEqual(case["status"], "UNRESOLVED")
            self.assertEqual(case["revision"], 0)
            self.assertEqual(case["requirements"][0]["disposition"], "PENDING")
            self.assertEqual({i["tier"] for i in case["invocations"]}, tiers_before)
            self.assertEqual(len(case["invocations"]), n_before)

    def test_join_against_independent_case_fails_mode_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                # Lower-only case with a produced Lower result: the crossing
                # is refused, Higher never runs, status is untouched.
                case = inv.start_case("lo-j", mode="lower_only",
                                      goal="order the dog state",
                                      evidence_snapshot={}, scope=["dog"])
                case = inv.execute_mode("lo-j", observations=copy.deepcopy(CONTRA))
                self.assertEqual(case["status"], "COMPLETED")
                lower_ref = [o for o in case["outputs"]
                             if o["slot_id"] == "L31"][0]["output_ref"]
                join = inv.join_lower_to_higher(case, lower_output_ref=lower_ref)
                self.assertEqual(join["status"], "FAILED")
                self.assertEqual(join["failure"]["code"], "MODE_MISMATCH")
                self.assertEqual(case["status"], "COMPLETED")
                self.assertEqual({i["tier"] for i in case["invocations"]}, {"LOWER"})
                # Higher-only case: the Higher-to-Lower crossing is refused
                # the same way.
                inv.start_case("ho-j", mode="higher_only",
                               goal="decide with no reading",
                               evidence_snapshot=copy.deepcopy(TOX_EARLY),
                               scope=["tox_screen"])
                case = inv.execute_mode("ho-j", hypotheses=copy.deepcopy(HYPOTHESES),
                                        observed={})
                self.assertEqual(case["status"], "UNRESOLVED")
                join = inv.join_higher_to_lower(
                    case, requirement=case["requirements"][0], consumer_slot="L03")
                self.assertEqual(join["status"], "FAILED")
                self.assertEqual(join["failure"]["code"], "MODE_MISMATCH")
                self.assertEqual(case["status"], "UNRESOLVED")
                self.assertEqual({i["tier"] for i in case["invocations"]}, {"HIGHER"})


class MultiSlotReviewTests(unittest.TestCase):
    def test_multi_input_join_waits_for_both_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                case = inv.start_case("ms-1", mode="joined_lower_first",
                                      goal="join waits for two Lower outputs",
                                      evidence_snapshot=copy.deepcopy(TOX), scope=["tox_screen"],
                                      hypotheses=copy.deepcopy(HYPOTHESES), observed={})
                join = inv.create_join(
                    case, producer_slot="L03",
                    producer_output_ref="ms-1:lower:L03:r0",
                    consumer_slot="U16", input_port="decision",
                    direction="LOWER_TO_HIGHER",
                    activation_condition="all_inputs_present",
                    required_inputs=[
                        {"slot_id": "L03", "output_ref": "ms-1:lower:L03:r0"},
                        {"slot_id": "L31", "output_ref": "ms-1:lower:L31:r0"},
                    ])
                self.assertEqual(join["status"], "WAITING")
                self.assertFalse(inv.check_join_ready(case, join))
                lower = inv._lower_route(case, copy.deepcopy(CONTRA))
                self.assertIsNotNone(lower)
                self.assertTrue(inv.check_join_ready(case, join))
                consumed = inv.join_lower_to_higher(
                    case, lower_output_ref=lower["output_ref"], join=join)
                self.assertEqual(consumed["status"], "CONSUMED")

    def test_multi_slot_review_preserves_conflicting_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                case = inv.start_case("ms-2", mode="joined_lower_first",
                                      goal="preserve a disagreement",
                                      evidence_snapshot={}, scope=["dog"])
                review = inv.multi_slot_review(case, [
                    {"slot_id": "L31", "feature": "state", "value": "awake",
                     "output_ref": "ms-2:lower:L31:a"},
                    {"slot_id": "L31", "feature": "state", "value": "asleep",
                     "output_ref": "ms-2:lower:L31:b"},
                    {"slot_id": "L31", "feature": "state", "value": "awake",
                     "output_ref": "ms-2:lower:L31:c"},
                ])
                self.assertEqual(review["disagreement_count"], 1)
                self.assertEqual(review["disagreements"][0]["feature"], "state")
                # Every output is preserved (including the repeated "awake");
                # the distinct values are the disagreement.
                self.assertEqual(len(review["disagreements"][0]["values"]), 3)
                self.assertEqual(
                    sorted({v["value"] for v in review["disagreements"][0]["values"]}),
                    ["asleep", "awake"])

    def test_specialist_multi_slot_review_uses_l34_u33(self):
        specialist = {"status": "CONFLICT_PRESERVED", "finding": "conflict",
                      "perspective_tag": "medical_bounded_for_legal",
                      "citations": ["med:early", "med:late"],
                      "conflict_records": [{
                          "key": "tox_screen",
                          "records": [
                              {"source_id": "med:early", "key": "tox_screen", "value": "negative"},
                              {"source_id": "med:late", "key": "tox_screen", "value": "positive_opioid"}]}],
                      "uncertainty": ["tox_screen"]}
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                case = inv.start_case("ms-3", mode="joined_lower_first",
                                      goal="specialist coherence review",
                                      evidence_snapshot=copy.deepcopy(TOX), scope=["tox_screen"])
                review = inv.multi_slot_review(
                    case, [], specialist=True, origin="Legal", target="Medical",
                    snapshot=copy.deepcopy(TOX), specialist_output=copy.deepcopy(specialist))
                self.assertEqual(review["specialist_review"]["result"], "CONFLICT_PRESERVED")
                forged = copy.deepcopy(specialist)
                forged["conflict_records"] = [{"key": "tox_screen", "source_ids": ["forged:a", "forged:b"],
                                               "propositions": ["forged=1", "forged=2"]}]
                review2 = inv.multi_slot_review(
                    case, [], specialist=True, origin="Legal", target="Medical",
                    snapshot=copy.deepcopy(TOX), specialist_output=forged)
                self.assertEqual(review2["specialist_review"]["result"], "PROVENANCE_LOST")


class CasePathTests(unittest.TestCase):
    def test_capacity_rejection_leaves_case_revision_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet, n_blocks=64)
                case = inv.start_case("cap-1", mode="joined_lower_first",
                                      goal="capacity on the case path",
                                      evidence_snapshot={}, scope=[])
                rev_before = case["revision"]
                cool_before = case["durable"]["cool_revision"]
                # Fill the remaining 63 workspace_large mirror slots with
                # direct adapter writes (same bucket, distinct blocks).
                adapter = closet.suit.adapter
                for i in range(1, 64):
                    adapter.write_and_sync(
                        AGENT, "SMALL49", 384 + i, {"content": "fill"},
                        bucket="workspace_large")
                with self.assertRaises(Rejected) as ctx:
                    inv.save_case(case)
                self.assertIn("CAPACITY_EXHAUSTED", str(ctx.exception))
                restored = inv.load_case("cap-1", 0)
                self.assertEqual(restored["revision"], rev_before)
                self.assertEqual(restored["durable"]["cool_revision"], cool_before)
                self.assertEqual(restored["status"], "OPEN")

    def test_denied_agent_records_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            with local_runtime(Path(tmp), ADMIN) as (_ctrl, closet, _loop):
                inv = _investigation(closet)
                case = inv.start_case("den-1", mode="joined_lower_first",
                                      goal="denied save", evidence_snapshot={}, scope=[])
                closet.suit.revoke_agent(AGENT)
                from crampu.controller import AuthorizationError
                with self.assertRaises(AuthorizationError):
                    inv.save_case(case)
                self.assertEqual(case["status"], "DENIED")
                self.assertIn("save_error", case)

    def test_case_state_survives_restart_with_mode_and_hold(self):
        root = Path(tempfile.mkdtemp())
        with local_runtime(root, ADMIN) as (_ctrl, closet, _loop):
            inv = _investigation(closet)
            inv.start_case("rst-1", mode="joined_higher_first",
                           goal="restart preserves the working view",
                           evidence_snapshot=copy.deepcopy(TOX_EARLY), scope=["tox_screen"],
                           acceptable_sources=["med:late"],
                           hypotheses=copy.deepcopy(HYPOTHESES), observed={})
            case = inv.execute_mode("rst-1", hypotheses=copy.deepcopy(HYPOTHESES), observed={})
            self.assertEqual(case["status"], "HOLD")
            # The mirror holds only a short reference, not the state: the
            # payload carries a "content" key (the reference string), so the
            # projection is "content", but the structured state is not
            # reconstructible from it (the payload has a case_state key).
            mirror = case["durable"]["mirror_reference"]
            self.assertEqual(mirror["projection_kind"], "content")
            self.assertFalse(mirror["reconstructs_structured_payload"])
        with local_runtime(root, ADMIN) as (_ctrl, closet, _loop):
            inv2 = _investigation(closet)
            restored = inv2.resume_after_restart("rst-1", 0)
            self.assertEqual(restored["mode"], "joined_higher_first")
            self.assertEqual(restored["status"], "HOLD")
            self.assertEqual(restored["revision"], 0)
            self.assertEqual(restored["resume_point"],
                             "requirement:rst-1:req:1")
            self.assertEqual(restored["requirements"][0]["required_feature"], "tox_screen")
            self.assertEqual(restored["durable"]["family"], "SMALL49")
            self.assertEqual(restored["durable"]["bucket"], "workspace_large")
            # Loaded from COOL (the authoritative record); the mirror is
            # only a projection, so no mirror reference is carried on load.
            self.assertIsNone(restored["durable"]["mirror_reference"])


if __name__ == "__main__":
    unittest.main()
