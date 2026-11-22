"""MUSE-ROUTER-001 acceptance tests; stdlib-only and restart-safe."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from contract.router import CognitiveRouter, IMPLEMENTED_FUNCTIONS, StrategyStore
from contract.router_simulation import SimulatedExecutor


class RouterAcceptance(unittest.TestCase):
    def task(self, **extra):
        data = {"task_id": "router-fixed-001", "family": "diagnostic",
                "signature": "same-problem", "tags": ["evidence"],
                "requested_operation": "investigate"}
        data.update(extra)
        return data

    def test_profiles_have_meaningfully_different_traces(self):
        task = self.task(tags=["evidence"])
        router = CognitiveRouter()
        traces = {}
        for profile in ("doctor", "lawyer", "mechanic", "cat", "crocodile"):
            state = router.route(task, profile)
            traces[profile] = [s.function_id for s in state.steps if s.available]
        self.assertGreaterEqual(len({tuple(v) for v in traces.values()}), 3)
        self.assertEqual(traces["mechanic"][1], "L30")
        self.assertEqual(traces["doctor"][1], "U10")
        self.assertIn("L24", traces["cat"])

    def test_evidence_changes_order_and_strategy(self):
        router = CognitiveRouter()
        state = router.route(self.task(tags=["evidence"]), "lawyer")
        before = [s.function_id for s in state.steps]
        router.observe(state, {"function": "U08", "success": False,
                               "resolved": False,
                               "evidence": {"contradiction": True}})
        after = [s.function_id for s in state.steps[state.cursor:]]
        self.assertNotEqual(before, after)
        self.assertEqual(after[0], "U10")
        self.assertTrue(state.switched)

    def test_contradiction_polarity_is_not_collapsed(self):
        router = CognitiveRouter()
        false_state = router.route(
            self.task(tags=["evidence"]), "lawyer",
            evidence={"contradiction": False})
        self.assertEqual(false_state.steps[0].function_id, "L01")
        self.assertNotIn("contradiction", false_state.selection["tokens"])
        unknown_state = router.route(
            self.task(tags=["evidence"]), "lawyer",
            evidence={"contradiction": {"value": None,
                                         "state": "unknown",
                                         "provenance": "source-a"}})
        self.assertEqual(unknown_state.steps[0].function_id, "U08")
        self.assertIn("unverified contradiction", unknown_state.steps[0].reason)
        unverified_state = router.route(
            self.task(tags=["evidence"]), "lawyer",
            evidence={"contradiction": {"value": True,
                                         "state": "unverified"}})
        self.assertEqual(unverified_state.steps[0].function_id, "U08")
        confirmed_state = router.route(
            self.task(tags=["evidence"]), "lawyer",
            evidence={"contradiction": True})
        self.assertEqual(confirmed_state.steps[0].function_id, "U10")
        self.assertIn("confirmed contradiction", confirmed_state.steps[0].reason)

    def test_unavailable_functions_are_not_executed(self):
        router = CognitiveRouter(available={"L01", "U08", "U09"})
        state = router.route(self.task(), "mechanic")
        selected = []
        while (step := router.next_available(state)) is not None:
            selected.append(step.function_id)
        self.assertTrue(set(selected) <= {"L01", "U08", "U09"})
        self.assertNotIn("L30", selected)
        self.assertNotIn("L15", selected)
        self.assertNotIn("L21", selected)
        self.assertTrue(state.unavailable_skips)
        self.assertEqual(state.unavailable_skips[0]["function"], "L30")
        self.assertEqual(state.unavailable_skips[0]["fallback"], "U09")

    def test_strategy_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "strategies.json")
            task = self.task(tags=["fault", "component"])
            r1 = CognitiveRouter(store=StrategyStore(path))
            first = r1.route(task, "mechanic")
            executed = []
            for _ in range(2):
                step = r1.next_available(first)
                executed.append(step.function_id)
                r1.observe(first, {"function": step.function_id,
                                   "inputs": {"task": task["task_id"]},
                                   "output": {"ok": True},
                                   "decision": {"selected": True},
                                   "success": True, "resolved": False,
                                   "validation": "PASS"})
            r1.finish(task, first, True)
            self.assertTrue(os.path.exists(path))
            code = (
                "import json, sys; "
                "from contract.router import CognitiveRouter, StrategyStore; "
                "task=json.loads(sys.argv[2]); "
                "r=CognitiveRouter(store=StrategyStore(sys.argv[1])); "
                "s=r.route(task, 'mechanic'); "
                "print(json.dumps({'memory':s.selection['memory_retrieved'], "
                "'first':s.steps[0].function_id, 'reason':s.steps[0].reason}))"
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = os.path.dirname(os.path.dirname(__file__))
            proc = subprocess.run(
                [sys.executable, "-c", code, path, json.dumps(task)],
                check=True, capture_output=True, text=True, env=env)
            result = json.loads(proc.stdout)
            self.assertTrue(result["memory"])
            self.assertEqual(result["first"], executed[0])
            self.assertEqual(result["reason"],
                             "retrieved successful executed strategy")

    def test_unexecuted_plan_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "strategies.json")
            task = self.task(tags=["fault"])
            router = CognitiveRouter(store=StrategyStore(path))
            state = router.route(task, "mechanic")
            router.finish(task, state, True)
            self.assertFalse(os.path.exists(path))

    def test_replan_is_bounded_and_preserves_completed_history(self):
        router = CognitiveRouter(max_switches=1)
        task = self.task(tags=["evidence"])
        state = router.route(task, "lawyer")
        first = router.next_available(state)
        router.observe(state, {"function": first.function_id,
                               "output": {"interpreted": True},
                               "success": True, "resolved": False})
        second = router.next_available(state)
        router.observe(state, {"function": second.function_id,
                               "output": {"retrieved": False},
                               "success": False, "resolved": False,
                               "evidence": {"contradiction": True}})
        self.assertEqual(state.completed[:2], [first.function_id, second.function_id])
        self.assertEqual(state.switch_count, 1)
        self.assertEqual(state.steps[state.cursor].function_id, "U10")
        # A second failure cannot cause unbounded switching.
        third = router.next_available(state)
        router.observe(state, {"function": third.function_id,
                               "success": False, "resolved": False,
                               "evidence": {"conflict": True}})
        self.assertEqual(state.switch_count, 1)

    def test_registry_boundary(self):
        self.assertEqual(set(IMPLEMENTED_FUNCTIONS),
                         {"L01", "L15", "L21", "L24", "L30",
                          "U08", "U09", "U10", "U37"})
        with self.assertRaises(ValueError):
            CognitiveRouter(available={"U01"})

    def test_fixture_trace_is_json_serializable(self):
        router = CognitiveRouter()
        state = router.route(self.task(), "doctor", evidence={"symptom": "fever"})
        payload = state.to_dict()
        json.dumps(payload, sort_keys=True)
        self.assertEqual(payload["profile"], "doctor")

    def test_profile_signal_api_selects_unique_profile(self):
        router = CognitiveRouter()
        decision = router.select_profile(
            self.task(tags=["symptom", "diagnosis", "patient"]),
            requested_profile="auto")
        self.assertEqual(decision["selected"], "doctor")
        self.assertEqual(decision["reason"], "profile signal match")
        self.assertEqual(decision["scores"]["doctor"], 3)
        state = router.route(self.task(tags=["symptom", "diagnosis"]), "auto")
        self.assertEqual(state.profile, "doctor")
        self.assertEqual(state.selection["selected"], "doctor")

    def test_profile_signal_api_fails_closed_on_ambiguity(self):
        router = CognitiveRouter()
        decision = router.select_profile(
            self.task(tags=["evidence", "change"]), requested_profile="auto")
        self.assertEqual(decision["selected"], "general")
        self.assertTrue(decision["ambiguous"])
        self.assertEqual(decision["reason"], "ambiguous profile signals")

    def test_explicit_profile_wins_over_signal_match(self):
        router = CognitiveRouter()
        decision = router.select_profile(
            self.task(tags=["symptom", "diagnosis"]),
            requested_profile="lawyer")
        self.assertEqual(decision["selected"], "lawyer")
        self.assertEqual(decision["reason"], "explicit profile")

    def test_fixed_profile_adaptive_and_termination(self):
        router = CognitiveRouter()
        executor = SimulatedExecutor(router)
        task = self.task(tags=["fault", "component", "repair"])
        fixed = executor.run_fixed(task)
        profiled = executor.run_profile(task, "mechanic")
        adaptive = executor.run_adaptive(task, "lawyer")
        self.assertEqual(fixed.mode, "fixed")
        self.assertEqual(profiled.mode, "profile")
        self.assertEqual(adaptive.mode, "adaptive")
        self.assertTrue(profiled.resolved)
        self.assertLessEqual(len(profiled.trace), 9)
        self.assertIn("U10", adaptive.function_sequence)
        for result in (fixed, profiled, adaptive):
            self.assertTrue(set(result.function_sequence) <= set(IMPLEMENTED_FUNCTIONS))
            self.assertIn(result.termination, ("resolved", "evidence-insufficient"))
            for event in result.trace:
                self.assertIn("inputs", event)
                self.assertIn("output", event)
                self.assertIn("decision", event)


if __name__ == "__main__":
    unittest.main()
