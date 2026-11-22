"""Qualification of the existing authenticated workflow with no prequalified transfer."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from eval061 import run as fixtures
from muse024 import call, issue

ROOT = Path(__file__).resolve().parents[1]


def invoke(root, token, operation, **data):
    return call(root, token, dict(operation=operation, authority=0, **data))


def fresh(root, token, operation, **data):
    script = (
        "import json, sys; from muse024 import call; "
        "root, token, request = json.load(sys.stdin); "
        "print(json.dumps(call(root, token, request)))"
    )
    child = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT,
        input=json.dumps([str(root), token, dict(operation=operation, authority=0, **data)]),
        capture_output=True, text=True, check=True, timeout=15,
    )
    return json.loads(child.stdout)


def investigate(root, token, observer, run, case, target, request=invoke):
    result = request(root, token, "unified_start025", run=run, problem=case["problem"],
                     targets={case["answer"]: target})
    assert result["handoff"]["status"] == "HOLD"
    assert result["cognitive"]["decision"]["status"] == "HOLD"
    for _ in range(len(case["measurements"])):
        decision = result["cognitive"]["decision"]
        if decision["status"] != "HOLD":
            break
        pending = decision["request"]
        feature = pending["feature"]
        measurement = {"request_id": pending["id"], "feature": feature,
                       "source": "fixture:measurement", "value": case["measurements"][feature]}
        ref = f"fixture:{run}:investigation:{feature}"
        invoke(root, observer, "suit_observe", evidence_ref=ref, observation={
            "kind": "investigation", "run": run, "principal": "transfer-actor",
            "measurement": measurement,
        })
        result = request(root, token, "unified_resume025", run=run,
                         measurement=measurement, evidence_ref=ref)
    assert result["cognitive"]["decision"]["status"] == "SUPPORTED"
    assert result["cognitive"]["decision"]["answer"] == case["answer"]
    return result["handoff"]


def complete(root, actor, observer, run, handoff, expected_order, request=invoke):
    """Simulate a fixture executor that advances only on the expected operation."""
    task_id = handoff["task_id"]
    assert handoff["status"] == "READY"
    assert handoff["execution_authorization"] == "NOT_GRANTED"
    assert handoff["steps"] == expected_order
    for index, expected in enumerate(expected_order):
        proposal = request(root, actor, "suit_next", task_id=task_id)
        assert proposal["status"] == "STEP_PROPOSED"
        observed = proposal["step"]
        assert proposal["index"] == index and observed == expected
        ref = f"fixture:{run}:executor:{index}"
        with pytest.raises(ValueError, match="registered observation"):
            invoke(root, actor, "suit_ack", task_id=task_id, index=index, evidence_ref=ref)
        invoke(root, observer, "suit_observe", evidence_ref=ref, observation={
            "kind": "step", "task": task_id, "principal": "transfer-actor",
            "index": index, "verb": observed, "outcome": "SUCCESS",
        })
        state = request(root, actor, "suit_ack", task_id=task_id, index=index, evidence_ref=ref)
        assert state["step"] == index + 1
    assert state["status"] == "COMPLETE"


def test_unfamiliar_task_qualifies_then_completes_and_reuses_after_restart(tmp_path):
    root, tokens = fixtures.station(tmp_path)
    fixtures.train(root, tokens, transfers=False)
    actor = issue(root, {"principal": "transfer-actor", "owners": [], "scopes": [
        "procedure:execute", "observation:write", "big:read", "small:read",
        "suit:execute", "suit:admit", "suit:read",
    ]})["token"]
    training = fixtures.TRAIN["transfers"]["P1"]
    target = training["target"]
    workload = json.loads((ROOT / "eval025/workload.json").read_text())
    pending = investigate(root, actor, tokens["obs1"], "practice", workload["training"], target)
    assert pending["status"] == "HOLD"
    assert pending["retrieval"]["mode"] == "PROPOSE_TRANSFER"
    proposal = invoke(root, actor, "suit_propose", target=target, skill_id=training["skill_id"],
                      retrieval_receipt=pending["retrieval"]["receipt"])
    transfer_id = proposal["transfer_id"]
    assert proposal["state"] == "TRANSFER_PROPOSED"
    refused = invoke(root, tokens["qual"], "suit_qualify_transfer", transfer_id=transfer_id)
    assert refused["status"] == "QUALIFICATION_REFUSED"
    for i, attempt in enumerate(training["attempts"], 1):
        trial = fixtures.record_trial(root, tokens[f"obs{i}"], "suit_attempt",
                                      transfer_id=transfer_id, **attempt)
        assert trial["effective_outcome"] == "SUCCESS"
    with pytest.raises(PermissionError):
        invoke(root, actor, "suit_qualify_transfer", transfer_id=transfer_id)
    qualified = invoke(root, tokens["qual"], "suit_qualify_transfer", transfer_id=transfer_id)
    assert qualified["state"] == "TRANSFER_QUALIFIED" and qualified["memory_link"]
    assert invoke(root, actor, "unified_show025", run="practice")["task"] is None
    admitted = invoke(root, actor, "unified_retry025", run="practice")["handoff"]
    assert admitted["transfer_id"] == transfer_id
    order = training["attempts"][0]["executed_order"]
    complete(root, actor, tokens["obs1"], "practice", admitted, order)

    recovered = fresh(root, actor, "unified_show025", run="practice")
    assert recovered["task"]["status"] == "COMPLETE"
    reuse_target = next(row["target"] for row in fixtures.TASKS if row["id"] == "P1B")
    assert reuse_target != target
    reused = investigate(root, actor, tokens["obs2"], "heldout", workload["heldout"][0],
                         reuse_target, request=fresh)
    assert reused["mode"] == "REUSE_QUALIFIED_TRANSFER"
    assert reused["transfer_id"] == transfer_id
    assert reused["task_id"] != admitted["task_id"]
    complete(root, actor, tokens["obs2"], "heldout", reused, order, request=fresh)
    assert fresh(root, actor, "unified_show025", run="heldout")["task"]["status"] == "COMPLETE"

    incompatible = next(row["target"] for row in fixtures.TASKS if row["id"] == "N1")
    rejected = fresh(root, actor, "suit_retrieve", task_id="negative", target=incompatible)
    assert rejected["mode"] == "DO_NOT_TRANSFER"
    with pytest.raises((PermissionError, ValueError)):
        invoke(root, actor, "suit_begin", task_id="negative", target=incompatible,
               skill_id=training["skill_id"], transfer_id=transfer_id,
               retrieval_receipt=rejected["receipt"])
    assert fresh(root, tokens["qual"], "suit_show", kind="trace")["intact"]
