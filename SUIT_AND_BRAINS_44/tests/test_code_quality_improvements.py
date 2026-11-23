"""Internal tests for Pipeline.__init__ resource cleanup, sys.path hygiene, and CLI JSON boundary validation."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract.pipeline import Pipeline, Policy  # noqa: E402
from soso_app import run_cli_deliver_evidence, run_cli_task  # noqa: E402


class CodeQualityImprovementTests(unittest.TestCase):
    def test_01_pipeline_does_not_duplicate_realcog_on_sys_path(self) -> None:
        rc_dir = str((ROOT / "realcog").resolve())
        before = sys.path.count(rc_dir)
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(5):
                p = Pipeline(Path(tmp) / f"run_{i}", Policy("v1"))
                p.close()
        after = sys.path.count(rc_dir)
        self.assertLessEqual(after, max(1, before))

    def test_02_pipeline_releases_lock_and_closes_dbs_on_init_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bad_init"
            with self.assertRaises(Exception):
                Pipeline(root, Policy("v1"), iou_policy="INVALID_POLICY")
            # Lock must be released so a valid Pipeline can immediately open the same directory
            p = Pipeline(root, Policy("v1"), iou_policy="TERMINAL")
            p.close()

    def test_03_cli_rejects_non_object_json_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            with self.assertRaises(ValueError):
                run_cli_task(state, '["not", "an", "object"]')
            with self.assertRaises(ValueError):
                run_cli_deliver_evidence(state, '"not_an_object"')
            self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
