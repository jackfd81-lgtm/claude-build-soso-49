import unittest
from brain49_path_explain import explain


class PathExplanationTests(unittest.TestCase):
    def test_real_span_and_unobserved_not_invented(self):
        source = {'task_id': 'T1', 'execution_id': 'R1', 'spans': [
            {'function': 'L01', 'namespace': 'lower49', 'span_id': 's1',
             'purpose': 'interpret task', 'begin_seq': 1, 'end_seq': 3,
             'events': [{'event_type': 'FUNCTION_RESULT', 'stage': 'intake'}],
             'memory': [], 'result': 'ok'}], 'outcome': {'outcome': 'UNSOLVED'}}
        result = explain(source)
        self.assertEqual(result['function_inventory']['invoked_ids'], ['L01'])
        self.assertEqual(result['path'][0]['purpose_recorded'], 'interpret task')
        self.assertEqual(result['function_inventory']['registered_positions'], 147)
        self.assertNotIn('L25', result['function_inventory']['invoked_ids'])

    def test_incomplete_span_visible(self):
        result = explain({'spans': [{'function': 'U08', 'begin_seq': 2,
                                     'end_seq': None}]})
        self.assertFalse(result['path'][0]['complete'])

    def test_missing_spans_rejected(self):
        with self.assertRaises(ValueError):
            explain({})


if __name__ == '__main__':
    unittest.main()
