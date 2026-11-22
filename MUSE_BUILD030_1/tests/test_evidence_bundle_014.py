"""Directive 014 Evidence Bundle & 2,000+ Character RealCog Writer Verification Tests."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval014.realcog_writer_report import (  # noqa: E402
    REPORT_TEXT,
    persist_and_verify_realcog_report,
)
from soso_app import run_cli_realcog_status, run_cli_task  # noqa: E402


class EvidenceBundle014Tests(unittest.TestCase):
    def test_01_realcog_writer_report_exceeds_2000_chars_and_roundtrips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = persist_and_verify_realcog_report(Path(tmp) / "store", REPORT_TEXT)
            self.assertGreaterEqual(res["char_count"], 2000)
            self.assertEqual(res["char_count"], len(REPORT_TEXT))
            self.assertEqual(res["report_text"], REPORT_TEXT)
            self.assertTrue(res["verified_exact_roundtrip"])
            self.assertLessEqual(res["max_block_chars"], 512)
            self.assertEqual(res["block_count"], res["trace_event_count"])

    def test_02_report_embedded_in_every_test_record_and_full_report(self) -> None:
        report_json = json.loads((ROOT / "eval014" / "uploaded_tests_report.json").read_text(encoding="utf-8"))
        writer_md = (ROOT / "eval014" / "REALCOG_WRITER_REPORT_2000.md").read_text(encoding="utf-8")
        full_md = (ROOT / "DIRECTIVE_014_FULL_REPORT.md").read_text(encoding="utf-8")

        self.assertGreaterEqual(len(writer_md), 2000)
        self.assertEqual(report_json["realcog_writer_report"], writer_md)
        self.assertIn(writer_md.strip(), full_md)
        self.assertGreaterEqual(len(report_json["test_results"]), 8)

        expected_sha = hashlib.sha256(writer_md.encode("utf-8")).hexdigest()
        for entry in report_json["test_results"]:
            self.assertEqual(entry["realcog_writer_report"], writer_md)
            self.assertEqual(entry["realcog_writer_sha256"], expected_sha)
            self.assertGreaterEqual(entry["realcog_writer_chars"], 2000)

    def test_03_unified_app_runs_with_evidence_bundle_intact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "app_state"
            out = run_cli_task(
                state,
                json.dumps({
                    "task_id": "directive_014_verify",
                    "description": "Verify Directive 014 evidence bundle and RealCog writer report",
                    "domain": "cognitive_evaluation",
                    "signature": "directive_014_evidence_bundle",
                    "family": "evidence_verification",
                    "tags": ["directive_014", "realcog_writer", "evidence_bundle"],
                }),
            )
            self.assertEqual(out["task_id"], "directive_014_verify")
            self.assertIn("state", out)
            self.assertTrue(out["trace_certified"])
            status = run_cli_realcog_status(state)
            self.assertTrue(status["iou_db_exists"])
            self.assertTrue(status["iou_state"]["complete"])


if __name__ == "__main__":
    unittest.main()
