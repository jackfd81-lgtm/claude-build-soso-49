import json
from pathlib import Path
import subprocess
import sys

import pytest

from eval061 import run as E
from muse024 import call, issue
from realcog.procedural_memory import identity

ROOT = Path(__file__).resolve().parents[1]


def test_investigation_evidence_atomically_hands_off_to_suit(tmp_path):
    root, tokens = E.station(tmp_path)
    E.train(root, tokens, transfers=True)
    target = next(t["target"] for t in E.TASKS if t["id"] == "P1B")
    token = issue(root, {"principal": "joint-actor", "owners": [],
        "scopes": ["procedure:execute", "procedure:write", "observation:write", "big:read", "small:read",
                   "suit:admit", "suit:execute", "suit:read"]})["token"]

    def request(operation, **kw):
        return call(root, token, {"authority": 0, "operation": operation, **kw})

    problem = json.loads((ROOT / "eval025/workload.json").read_text())["training"]
    current = request("unified_start025", run="joint", problem=problem["problem"],
                      targets={problem["answer"]: target})
    assert current["handoff"]["status"] == "HOLD"
    while current["cognitive"]["decision"]["status"] == "HOLD":
        pending = current["cognitive"]["decision"]["request"]
        payload = {"request_id": pending["id"], "feature": pending["feature"], "source": "fixture:observer",
                   "value": problem["measurements"][pending["feature"]]}
        ref = "fixture:" + identity(payload)
        step = {"authority": 0, "operation": "unified_resume025", "run": "joint", "measurement": payload,
                "evidence_ref": ref}
        with pytest.raises(ValueError, match="registered observation"):
            call(root, token, step)
        call(root, tokens["obs1"], {"operation": "suit_observe", "evidence_ref": ref,
             "observation": {"kind": "investigation", "run": "joint", "principal": "joint-actor",
                             "measurement": payload}})

        def fault(point):
            if point == "before_unified_025_commit":
                raise RuntimeError("rollback")

        with pytest.raises(RuntimeError, match="rollback"):
            call(root, token, step, fault=fault)
        current = call(root, token, step)
        assert call(root, token, step) == current
    handoff = current["handoff"]
    assert handoff["status"] == "READY" and handoff["transfer_id"]
    with pytest.raises(PermissionError, match="reserved"):
        request("show025", run=handoff["task_id"])

    with pytest.raises(ValueError, match="before learning"):
        request("unified_learn025", run="joint")

    for index, verb in enumerate(handoff["steps"]):
        ref = f"fixture:joint-step-{index}"
        assert request("suit_next", task_id=handoff["task_id"])["step"] == verb
        call(root, tokens["obs1"], {"operation": "suit_observe", "evidence_ref": ref,
             "observation": {"kind": "step", "task": handoff["task_id"], "principal": "joint-actor",
                             "index": index, "verb": verb, "outcome": "SUCCESS"}})
        request("suit_ack", task_id=handoff["task_id"], index=index, evidence_ref=ref)

    child = subprocess.run([sys.executable, "-c",
        "import json,sys; from muse024 import call; root,t=json.load(sys.stdin); "
        "print(json.dumps(call(root,t,{'operation':'unified_show025','run':'joint','authority':0})))"],
        cwd=ROOT, input=json.dumps([str(root), token]), capture_output=True, text=True, check=True)
    restored = json.loads(child.stdout)
    assert restored["task"]["status"] == "COMPLETE"
    assert restored["handoff"]["task_id"] == handoff["task_id"]
    assert restored["cognitive"]["decision"]["answer"] == problem["answer"]

    learned = request("unified_learn025", run="joint")
    method = learned["learning"]["decision"]["candidate_method"]
    assert method
    qualifier = issue(root, {"principal": "brain-qualifier", "owners": ["L15"],
                              "scopes": ["procedure:qualify", "procedure:read", "big:write", "small:write"]})["token"]
    qualified = call(root, qualifier, {"operation": "qualify025", "method": method, "authority": 0})
    assert qualified["memory"]
    new_case = json.loads((ROOT / "eval025/workload.json").read_text())["heldout"][0]
    transferred = request("unified_start025", run="reuse", problem=new_case["problem"], method=method,
                          targets={new_case["answer"]: target})
    assert transferred["cognitive"]["contributing_blocks"]
