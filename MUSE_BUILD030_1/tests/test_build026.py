import json
from pathlib import Path
import subprocess
import sys

import pytest

from eval025.run import issue_qualifier, send, solve
from eval026.run import run, simulate, validate_receipt
from muse024 import initialize, transaction
from realcog import memory025

ROOT = Path(__file__).resolve().parents[1]
WORK = json.loads((ROOT / "eval025/workload.json").read_text())


def test_restarted_joint_workflow_memory_changes_budgeted_outcome(tmp_path):
    result = run(tmp_path)
    held = result["heldout"]
    assert held["on"]["decision"] == held["restored"]["decision"]
    first = held["on"]["trace"][0]["cognitive"]
    attribution = next(event["result"] for event in first["trace"]
                       if event.get("connection") == ["U15", "U12"]
                       and event["result"].get("bus") == "memory-method")
    assert attribution["selected_feature"] == first["decision"]["request"]["feature"]
    assert attribution["selected_feature"] != held["off"]["trace"][0]["cognitive"]["decision"]["request"]["feature"]
    assert set(attribution["blocks"]) == set(held["on"]["contributing_blocks"])
    assert len(result["practice"]) == 3 and len(held["on"]["execution_evidence"]) == 7


def test_fixture_executor_rejects_wrong_order_and_measurement():
    from eval061.run import TRAIN

    target = TRAIN["transfers"]["P1"]["target"]
    with pytest.raises(ValueError, match="not enabled"):
        simulate("ALIGN", [], {})
    before = ["tool_selected", "aligned", "gripped", "rotated"]
    receipt = simulate("APPLY_FORCE", before, {"force": 26}) | {
        "task": "fixture-task", "authority": "NOT_GRANTED",
    }
    with pytest.raises(ValueError, match="outside target"):
        validate_receipt(receipt, target, "fixture-task", 4, "APPLY_FORCE")
    with pytest.raises(ValueError, match="deterministic validation"):
        validate_receipt(receipt, target, "another-task", 4, "APPLY_FORCE")


def start_qualification(root, token, method, mode):
    script = """
import json, os, sys
from muse024 import call
root, token, method, mode = json.load(sys.stdin)
def fault(point):
    if mode == 'before' and point == 'after_big_025':
        os._exit(71)
result = call(root, token, {'operation': 'qualify025', 'authority': 0, 'method': method}, fault=fault)
if mode == 'after':
    os._exit(72)
print(json.dumps(result))
"""
    child = subprocess.Popen([sys.executable, "-c", script], cwd=ROOT, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    child.stdin.write(json.dumps([str(root), token, method, mode]))
    child.stdin.close()
    child.stdin = None
    return child


def test_qualification_process_kill_and_concurrent_creation(tmp_path):
    root = tmp_path / "db"
    token = initialize(root)
    qualifier = issue_qualifier(root, token)
    training = solve(root, token, WORK["training"])
    method = send(root, token, "learn025", run=training["run"])["decision"]["candidate_method"]
    before = send(root, token, "occupancy")
    child = start_qualification(root, qualifier, method, "before")
    child.communicate(timeout=20)
    assert child.returncode == 71
    assert send(root, token, "occupancy") == before
    with transaction(root) as db:
        assert memory025.recall(db, method) == ([], [])
    children = [start_qualification(root, qualifier, method, mode) for mode in ("after", "normal", "normal")]
    outputs = []
    for process, mode in zip(children, ("after", "normal", "normal")):
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == (72 if mode == "after" else 0), stderr
        if mode == "normal":
            outputs.append(json.loads(stdout))
    assert sum(bool(row.get("replayed")) for row in outputs) >= 1
    after = send(root, token, "occupancy")
    for pool in ("big", "small"):
        assert after[pool]["allocated"] == before[pool]["allocated"] + 1
    with transaction(root) as db:
        records, reads = memory025.recall(db, method)
        assert len(records) == 1 and {row["pool"] for row in reads} == {"big", "small"}
        db.execute("UPDATE method_025 SET small=NULL WHERE id=?", (method,))
    with transaction(root) as db:
        records, reads = memory025.recall(db, method)
        assert not records and [row["operation"] for row in reads] == ["rejected"]
    assert send(root, qualifier, "revoke025", method=method)["revoked"] == 1
    with transaction(root) as db:
        assert memory025.recall(db, method) == ([], [])
