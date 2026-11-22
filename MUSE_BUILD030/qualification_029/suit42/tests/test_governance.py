import concurrent.futures
from pathlib import Path
import tempfile
import unittest

from sososuit42 import Invalid, Journal, digest, govern_context, parse, run_pipeline, verify_replay


class Governance(unittest.TestCase):
    def setUp(self):
        self.context = {"task_id": "sample", "principal": "operator",
                        "allowed_actions": ["REQUEST_READING", "REQUEST_DISCRIMINATOR", "NO_OP"],
                        "evidence": {"ext:measurement": {"value": 7}}}
        self.candidate = {"producer": "AI", "candidate_id": "candidate",
                          "governed_state_digest": digest(govern_context(self.context)),
                          "actions": ["REQUEST_READING"],
                          "claims": [{"id": "int:claim", "text": "Review reading",
                                      "refs": ["ext:measurement"]}],
                          "ious": [{"id": "reading", "type": "READING",
                                    "code": "IOU_SOURCE_UNAVAILABLE", "description": "Repeat reading"}]}

    def test_replay_and_tampering(self):
        receipt = run_pipeline(self.context, self.candidate)
        self.assertEqual(receipt["disposition"]["verdict"], "ADVISORY_ACCEPT")
        self.assertEqual(receipt["disposition"]["execution_authorization"], "NOT_GRANTED")
        self.assertTrue(verify_replay(self.context, self.candidate, receipt))
        receipt["seal"]["executability"] = "EXECUTABLE"
        self.assertFalse(verify_replay(self.context, self.candidate, receipt))

    def test_non_advisory_action(self):
        self.candidate["actions"] = ["REQUEST_DISCRIMINATOR"]
        self.assertEqual(run_pipeline(self.context, self.candidate)["disposition"]["reasons"],
                         ["NON_ADVISORY_ACTION"])

    def test_binding_and_provenance(self):
        self.candidate["claims"][0]["refs"] = ["int:unbound"]
        self.assertEqual(run_pipeline(self.context, self.candidate)["disposition"]["reasons"],
                         ["UNRESOLVED_PROVENANCE"])
        self.context["evidence"]["ext:measurement"]["value"] = 8
        self.assertEqual(run_pipeline(self.context, self.candidate)["disposition"]["reasons"],
                         ["STATE_BINDING_MISMATCH"])

    def test_authority_injection_refused(self):
        self.candidate["execution_authorization"] = "GRANTED"
        self.assertEqual(run_pipeline(self.context, self.candidate)["disposition"]["verdict"], "REJECT")

    def test_no_op_escape_required(self):
        self.context["allowed_actions"].remove("NO_OP")
        with self.assertRaisesRegex(Invalid, "UNSATISFIABLE_ACTION_POLICY"):
            govern_context(self.context)

    def test_input_canonicalization(self):
        for payload in (b'{"x":1,"x":2}', br'{"e\u0301":1,"\u00e9":2}', b'1.5', b'NaN'):
            with self.subTest(payload=payload), self.assertRaises(Invalid):
                parse(payload)

    def test_concurrent_retry_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "journal.sqlite"
            journal = Journal(path)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                receipts = list(pool.map(lambda _: journal.submit(self.context, self.candidate), range(8)))
            self.assertTrue(all(receipt == receipts[0] for receipt in receipts))
            journal = Journal(path)
            pending, = journal.pending("operator")
            self.assertEqual(journal.pending("other"), [])
            key = pending["decision_id"]
            with self.assertRaisesRegex(Invalid, "UNKNOWN_IOU"):
                journal.complete_iou("other", key, "reading", "ABANDONED", "note")
            args = ("operator", key, "reading", "EVIDENCE_RECEIVED", "Source delivered")
            self.assertEqual(journal.complete_iou(*args), Journal(path).complete_iou(*args))
            with self.assertRaisesRegex(Invalid, "IOU_ALREADY_CLOSED"):
                journal.complete_iou("operator", key, "reading", "ABANDONED", "different")
            self.assertEqual(Journal(path).pending("operator"), [])

    def test_reject_never_creates_iou(self):
        self.candidate["producer"] = "GOVERNOR"
        with tempfile.TemporaryDirectory() as temp:
            journal = Journal(Path(temp) / "journal.sqlite")
            self.assertEqual(journal.submit(self.context, self.candidate)["disposition"]["verdict"], "REJECT")
            self.assertEqual(journal.pending("operator"), [])
