"""Regression tests for the TEST 025 provenance gate (NEW, MUSE-REGRESSION-001).

Background: eval022/test025.py has NO assertions - it exits 0 whatever it
measures. In the supplied package it measured 8/12 correct with 4 false
resolutions (cases E2-E5), contradicting the "12/12, 0 false resolutions"
claim in the handoff. These tests turn the measured outcome into an enforced
one. The gold values come from the evaluator-owned case definitions in
eval022/test025.py (not edited here).
"""
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_test025():
    spec = importlib.util.spec_from_file_location("t25_mod", ROOT / "eval022" / "test025.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def result():
    t25 = _load_test025()
    cases = t25._build_families()
    out = Path(tempfile.mkdtemp(prefix="t25-"))
    res = t25._run_config("regression", t25.c2_eval, cases, out, screening_on=True)
    return {r["case_id"]: r for r in res["rows"]}, res


def test_twelve_cases_zero_false_resolutions(result):
    rows, res = result
    assert len(rows) == 12
    assert sum(r["false_resolution"] for r in rows.values()) == 0
    assert sum(r["correct"] for r in rows.values()) == 12


@pytest.mark.parametrize("case_id", ["T025_E2_stored_no_prov", "T025_E3_malformed_prov",
                                     "T025_E4_fabricated_source",
                                     "T025_E5_valid_without_evidence_ids"])
def test_unqualified_stored_claims_are_not_promoted(result, case_id):
    rows, _ = result
    assert rows[case_id]["disposition"] == "ABSTAIN"
    assert rows[case_id]["prediction"] is None


@pytest.mark.parametrize("case_id,expected", [("T025_A_double_3", 6), ("T025_H_valid_add3", 103)])
def test_legitimate_stored_claims_still_resolve(result, case_id, expected):
    """The gate must not turn into 'abstain on everything'."""
    rows, _ = result
    assert rows[case_id]["disposition"] == "RESOLVE"
    assert rows[case_id]["prediction"] == expected


def test_rejection_is_recorded_not_silent():
    from contract.mac002_adapter import stored_record_trust_defects as d
    assert d({"provenance": [], "evidence_ids": ["o1", "o2"]}) == ["missing_provenance"]
    assert d({"provenance": "not-a-list", "evidence_ids": ["o1"]}) == ["malformed_provenance"]
    assert d({"provenance": ["fake:oracle"], "evidence_ids": ["o1"]}) == ["fabricated_or_untrusted_provenance"]
    assert d({"provenance": ["s1"], "evidence_ids": []}) == ["missing_evidence_ids"]
    assert d({"provenance": ["s1"], "evidence_ids": ["o1", "o2"]}) == []


def test_single_source_of_truth_with_qualify_function():
    """qualify_record_against_observations must report the same provenance
    reasons as the shared helper (no drift between the two code paths)."""
    from contract.mac002_adapter import (provenance_defects,
                                         qualify_record_against_observations)
    rec = {"relationship_id": "r", "qualification": "VALID", "provenance": ["fake:x"],
           "evidence_ids": ["o1", "o2"],
           "structure": {"kind": "add", "input_key": "value", "output_key": "result", "delta": 3}}
    ok, reasons = qualify_record_against_observations(rec, {})
    assert ok is False
    for r in provenance_defects(rec):
        assert r in reasons
