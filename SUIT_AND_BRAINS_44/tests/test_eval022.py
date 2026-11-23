import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "selection_work"
RESULTS = SELECTION / "EVAL022_RESULTS.json"
FIXTURES = SELECTION / "eval022_fixtures.json"
TRACE_DIR = SELECTION / "EVAL022_TRACES"


def load_eval022():
    spec = importlib.util.spec_from_file_location("eval022", SELECTION / "eval022.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gold_isolation_and_no_prediction_shortcuts_in_cases():
    fixtures = json.loads(FIXTURES.read_text())
    forbidden = {"memory_prediction", "baseline_prediction", "gold_prediction", "gold_disposition", "baseline_disposition", "evidence_resolves"}
    for case in fixtures["cases"]:
        assert not (forbidden & set(case.keys())), f"Shortcut key in case {case['case_id']}"
    assert "evaluator_gold" in fixtures and "evidence_oracle" in fixtures


def test_real_transformations_computed_and_zero_false_resolutions_in_c_d():
    data = json.loads(RESULTS.read_text())
    assert data["gold_isolation_enforced"] is True
    assert data["execution_boundary"] == "REAL_RELATIONSHIP_ENGINE_TRANSFORMATION_WITH_EXPERIMENTAL_EPISTEMIC_POLICY"
    assert data["summaries"]["C_candidate_applicability"]["false_resolutions"] == 0
    assert data["summaries"]["D_candidate_evidence"]["false_resolutions"] == 0
    assert data["summaries"]["D_candidate_evidence"]["correct_decisions"] == 7
    # Verify negative-1 in Arm C applied multiply*2 (100 -> 200) rather than stale add+3 (103)
    neg1_c = next(r for r in data["results"]["C_candidate_applicability"] if r["case_id"] == "negative-1")
    assert neg1_c["disposition"] == "RESOLVE" and neg1_c["prediction"] == 200


def test_arm_d_real_evidence_request_response_and_reevaluation():
    data = json.loads(RESULTS.read_text())
    rows = data["results"]["D_candidate_evidence"]
    ev1 = next(r for r in rows if r["case_id"] == "evidence-1")
    assert ev1["evidence_requests"] == ["relation_kind", "tags"]
    assert ev1["evidence_response"] == {"relation_kind": "linear", "tags": ["linear"]}
    assert ev1["disposition"] == "RESOLVE" and ev1["prediction"] == 503 and ev1["correct"] is True


def test_fresh_vs_shared_store_comparison_present():
    data = json.loads(RESULTS.read_text())
    cmp = data["fresh_vs_shared_store_comparison"]
    assert set(cmp["fresh_store_summaries"]) == {"A_no_memory", "B_existing_memory", "C_candidate_applicability", "D_candidate_evidence"}
    assert set(cmp["shared_store_summaries"]) == {"A_no_memory", "B_existing_memory", "C_candidate_applicability", "D_candidate_evidence"}
    assert isinstance(cmp["cross_case_contamination_detected"], bool)


def test_recorder_traces_preserve_candidates_provenance_and_reevaluate():
    mod = load_eval022()
    path = TRACE_DIR / "fresh_D_candidate_evidence.events.jsonl"
    events = mod.CognitiveRecorder.load(str(path))
    assert events and events[-1]["kind"] == "VALIDATE" and events[-1]["payload"]["run_complete"] is True
    stages = {e["payload"].get("stage") for e in events if e["kind"] == "EVALUATE"}
    assert "EVIDENCE_REQUEST" in stages and "RE_EVALUATE" in stages


def test_regeneration_is_reproducible():
    before = RESULTS.read_bytes()
    subprocess.run([sys.executable, str(SELECTION / "eval022.py")], cwd=ROOT, check=True,
                   env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
    assert hashlib.sha256(before).hexdigest() == hashlib.sha256(RESULTS.read_bytes()).hexdigest()
