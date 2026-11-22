import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from contract.cognitive_pathway_023 import run_cognitive_023
from contract.observation_memory import recall_observations
from soso_app import run_cli_task

ROOT = Path(__file__).resolve().parents[1]


def request(text="x=3", **kwargs):
    return {"task_type": "cognitive_023", "task_id": "case", "domain": "mathematics",
            "text": text, **kwargs}


def test_restart_application_and_ablation(tmp_path):
    training = request("x=3", mode="train", expected_answer="3")
    run_cognitive_023(tmp_path, training)
    task = request("x=5")
    task["task_id"] = "heldout"
    cmd = [sys.executable, str(ROOT / "soso_app.py"), "--mode", "cli",
           "--state-dir", str(tmp_path), "--task-json", json.dumps(task)]
    output = json.loads(subprocess.check_output(cmd, text=True))
    assert output["answer"] == "5"
    assert output["chosen_strategy"] == "linear_unit_zero_offset"
    assert output["method_applied"]
    no_application = run_cognitive_023(tmp_path, {**task, "task_id": "no-apply", "mode": "no_application"})
    assert no_application["methods_retrieved"] and not no_application["method_applied"]
    assert no_application["operation_cost"] > output["operation_cost"]
    without_recall = run_cognitive_023(tmp_path, {**task, "task_id": "no-recall",
                                                 "disabled_functions": ["U08"]})
    assert without_recall["methods_retrieved"] == []
    assert without_recall["answer"] == "5"


@pytest.mark.parametrize("bad_authority", [False, True, 0.0, 1, "0", None])
def test_public_ingress_requires_exact_integer(tmp_path, bad_authority):
    for task in (request(), {"task_type": "observe"}, {"task_type": "recall_observations"}):
        with pytest.raises(PermissionError):
            run_cli_task(tmp_path, json.dumps(dict(task, authority=bad_authority)))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("text,expected", [("-x +\t2=5", "-3"), ("+x=.125", "1/8"),
                                           ("3x=1", "1/3"), ("sum=9,difference=-6", ["3/2", "15/2"])])
def test_exact_results(tmp_path, text, expected):
    assert run_cognitive_023(tmp_path, request(text))["answer"] == expected


def test_cli_malformed_json_and_equations(tmp_path):
    with pytest.raises(ValueError):
        run_cognitive_023(tmp_path, request("*x=5"))
    process = subprocess.run([sys.executable, str(ROOT / "soso_app.py"), "--mode", "cli",
                              "--state-dir", str(tmp_path), "--task-json", "{"],
                             text=True, capture_output=True)
    assert process.returncode == 2
    assert json.loads(process.stderr)["error_type"] == "JSONDecodeError"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("stage", ["after_observation", "before_execution_commit"])
def test_sqlite_rollback_and_retry(tmp_path, stage):
    task = request("ignored", mode="train", expected_answer="4", source_id="sensor",
                   derive_from_observation=True, observations=[
                       {"kind": "caption", "text": "x = 4", "time_ms": 900}])
    def inject(location):
        if location == stage:
            raise sqlite3.OperationalError("injected")
    with pytest.raises(sqlite3.OperationalError):
        run_cognitive_023(tmp_path, task, fault=inject)
    assert recall_observations(tmp_path, "sensor")["observations"] == []
    answer = run_cognitive_023(tmp_path, task)
    assert answer["storage"]["rows_written"] == 4
    assert recall_observations(tmp_path, "sensor")["observations"][0]["text"] == "x = 4"
    replayed = run_cognitive_023(tmp_path, task)
    assert replayed["replayed"] and replayed["storage"]["rows_written"] == 0
    with pytest.raises(ValueError):
        run_cognitive_023(tmp_path, {**task, "expected_answer": "6"})
    with sqlite3.connect(tmp_path / "observations.sqlite3") as db:
        assert [db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in
                ("observation", "procedures_023", "executions_023", "feedback_023")] == [1, 1, 1, 1]
