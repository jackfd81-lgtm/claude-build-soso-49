"""Run candidate and focused Build 040 regressions with standard-library unittest."""
from pathlib import Path
import unittest

SELECTIONS = (
    "test_build42_candidate.py",
    "test_build040_integration_20.py",
    "test_ai_interrogation_protocols.py",
    "test_learning_loop_u11_u15.py",
    "test_learning_qualification_0401.py",
    "test_precision_105_build040.py",
    "test_crampu_controller.py",
    "test_merged_crampu_build030_1.py",
)


def main():
    tests = Path(__file__).resolve().parent / "tests"
    suite = unittest.TestSuite()
    for name in SELECTIONS:
        suite.addTests(unittest.TestLoader().discover(str(tests), pattern=name))
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
