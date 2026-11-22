import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from eval011.run_visual_cognition_011 import run_all_ten_images, run_tests_a_to_e_and_abcd  # noqa: E402


class VisualCognition011Tests(unittest.TestCase):
    def test_01_all_ten_images_processed_with_provenance_and_unsupported_native_vision(self):
        with tempfile.TemporaryDirectory() as td:
            recs = run_all_ten_images(Path(td))
            self.assertEqual(len(recs), 10)
            for r in recs:
                self.assertEqual(r["native_soso_vision_status"], "UNSUPPORTED")
                self.assertEqual(r["reconciliation_disposition"], "HOLD")
                self.assertTrue(r["l24_activated"])
                self.assertTrue(r["u10_consumed"])
                self.assertTrue(r["u09_consumed"])

    def test_02_tests_a_to_e_and_four_arm_c_beats_d_across_os_restart(self):
        with tempfile.TemporaryDirectory() as td:
            summ = run_tests_a_to_e_and_abcd(Path(td))
            ta = summ["test_a_cross_image"]
            self.assertEqual(ta["exact_duplicate"]["relationship_type"], "EXACT_BYTE_DUPLICATE")
            self.assertEqual(ta["composite_screenshot"]["relationship_type"], "COMPOSITE_SCREENSHOT_ARTIFACT")
            self.assertEqual(ta["same_location_diff_lighting"]["relationship_type"], "SAME_LOCATION_DIFFERENT_CONDITIONS")
            self.assertEqual(ta["unrelated_scenes"]["relationship_type"], "UNRELATED_SCENES")
            self.assertEqual(summ["test_b_reconciliation"]["disposition"], "DROP")
            self.assertTrue(summ["test_b_reconciliation"]["conflicts"])
            abcd = summ["test_c_d_four_arm"]
            self.assertTrue(abcd["c_outperforms_d"])
            self.assertEqual(abcd["C"]["correct"], 4)
            self.assertEqual(abcd["D"]["correct"], 0)
            self.assertEqual(abcd["A"]["correct"], 0)
            self.assertEqual(abcd["B"]["correct"], 0)
            self.assertFalse(summ["test_e_temporal_chronology_inferred_from_brightness"])


if __name__ == "__main__":
    unittest.main()
