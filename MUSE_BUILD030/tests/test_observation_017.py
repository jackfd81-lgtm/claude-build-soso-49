from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from soso_app import run_cli_task

class ObservationTests(unittest.TestCase):
    def test_caption_preserved_and_recalled_after_restart(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)/'state'
            payload = '{"task_type":"observe","source_id":"clip1","observations":[{"kind":"caption","time_ms":1000,"text":"A person said the event happened."},{"kind":"scene","time_ms":0,"text":"Clouds visible."}]}'
            a = run_cli_task(state,payload)
            self.assertEqual(a['status'],'RECORDED')
            self.assertFalse(a['claim_truth_evaluated'])
            b = run_cli_task(state,'{"task_type":"recall_observations","source_id":"clip1"}')
            self.assertEqual([x['text'] for x in b['observations']], ['Clouds visible.', 'A person said the event happened.'])
            run_cli_task(state,payload)
            self.assertEqual(run_cli_task(state,'{"task_type":"recall_observations","source_id":"clip1"}')['observations'], b['observations'])
    def test_bad_input_does_not_create_state(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'state'
            with self.assertRaises(ValueError):
                run_cli_task(state,'{"task_type":"observe","source_id":"x","observations":[{"kind":"caption","text":"hello","time_ms":-1}]}')
            self.assertFalse(state.exists())
    def test_distinct_sources_and_exact_words(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'state'
            for sid in ['one','two']:
                run_cli_task(state,'{"task_type":"observe","source_id":"'+sid+'","observations":[{"kind":"speech","time_ms":0,"text":"It happened!"}]}')
            self.assertEqual(len(run_cli_task(state,'{"task_type":"recall_observations","source_id":"one"}')['observations']),1)
            self.assertEqual(len(run_cli_task(state,'{"task_type":"recall_observations","source_id":"two"}')['observations']),1)

if __name__=='__main__': unittest.main()
