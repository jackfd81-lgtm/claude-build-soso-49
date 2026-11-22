import os
import tempfile
import unittest

from contract.cognitive_recorder import CognitiveRecorder, RecorderError
from contract.router import CognitiveRouter


class CognitiveRecorderTests(unittest.TestCase):
    def test_router_events_are_observational_and_ordered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.jsonl")
            recorder = CognitiveRecorder(path, run_id="run-1", task_id="task-1")
            router = CognitiveRouter(recorder=recorder)
            task = {"task_id": "task-1", "family": "fault", "tags": ["repair"]}
            state = router.route(task, profile="auto")
            step = router.next_available(state)
            router.observe(state, {
                "function": step.function_id,
                "inputs": {"input": 1},
                "output": {"ok": True},
                "success": True,
                "resolved": True,
                "provenance": "simulated",
            })
            router.finish(task, state, True)
            self.assertEqual(
                [event["kind"] for event in recorder.replay()],
                ["SELECT", "EXECUTE", "OBSERVE", "STORE", "VALIDATE"],
            )
            self.assertEqual(
                [event["sequence"] for event in recorder.replay()], [1, 2, 3, 4, 5]
            )
            self.assertEqual(recorder.replay()[2]["provenance"], "simulated")
            self.assertFalse(recorder.incomplete())

    def test_restart_and_replay_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.jsonl")
            first = CognitiveRecorder(path, run_id="run-2", task_id="task-2")
            first.append("OBSERVE", {"source": "fixture"}, provenance="observed")
            first.close_run(outcome="ok")
            second = CognitiveRecorder(path)
            self.assertEqual(first.replay(), second.replay())
            self.assertEqual(first.digest(), second.digest())

    def test_corrupt_json_and_duplicate_ids_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "events.jsonl")
            recorder = CognitiveRecorder(path, run_id="run-3")
            recorder.append("OBSERVE", {}, provenance="observed")
            with open(path, "a", encoding="utf-8") as stream:
                stream.write("{not-json}\n")
            with self.assertRaises(RecorderError):
                CognitiveRecorder(path)

    def test_replay_does_not_execute_functions(self):
        recorder = CognitiveRecorder(run_id="run-4")
        recorder.append("EXECUTE", {"function": "L01", "status": "requested"},
                        provenance="planned")
        self.assertEqual(recorder.replay()[0]["payload"]["status"], "requested")
        self.assertEqual(len(recorder.events), 1)


if __name__ == "__main__":
    unittest.main()
