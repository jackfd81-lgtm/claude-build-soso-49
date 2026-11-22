"""Run additional refusal probes through authenticated operations in scratch state."""
import json
from pathlib import Path
import tempfile

from eval061 import run as E
from muse024 import call, transaction
from suit61.agent import Agent


def rejected(action):
    try:
        action()
    except (ValueError, PermissionError) as error:
        return type(error).__name__
    raise AssertionError("expected rejection")


def run(root):
    root, tokens = E.station(root)
    E.train(root, tokens, transfers=True)
    target = next(row["target"] for row in E.TASKS if row["id"] == "P1B")
    def invoke(role, operation, **args):
        return call(root, tokens[role], {"operation": operation, **args})
    view = invoke("agent", "suit_retrieve", target=target, task_id="probe")
    args = dict(task_id="probe", target=target, skill_id=view["skill_id"],
                transfer_id=view["transfer_id"], retrieval_receipt=view["receipt"])
    result = {
        "changed_task_receipt": rejected(lambda: invoke("agent", "suit_begin", **(args | {"task_id": "other"}))),
        "higher_authority": rejected(lambda: invoke("agent", "suit_begin", authority=1, **args)),
        "caller_plan_override": rejected(lambda: invoke("agent", "suit_begin", plan=view["plan"], **args)),
    }
    agent = Agent(root, suit_token=tokens["agent"], brain_token=tokens["brain"])
    task = agent.admit("probe", target)["task"]
    wrong = {"kind": "step", "task": "wrong", "principal": "agent", "index": 0,
             "verb": task["steps"][0], "outcome": "SUCCESS"}
    invoke("obs1", "suit_observe", evidence_ref="fixture:wrong-task", observation=wrong)
    result["cross_task_evidence"] = rejected(lambda: invoke("agent", "suit_ack", task_id="probe", index=0,
                                                          evidence_ref="fixture:wrong-task"))
    with transaction(root) as db:
        db.execute("UPDATE suit61_tasks SET revision=revision+1 WHERE task_id='probe'")
    result["injected_ledger_revision"] = rejected(lambda: invoke("agent", "suit_next", task_id="probe"))
    # A forged plan inside a qualified transfer must disagree with the memory reference.
    with transaction(root) as db:
        db.execute("UPDATE suit61_transfers SET plan=? WHERE transfer_id=?",
                   (json.dumps({"steps": [{"verb": "VERIFY"}], "limits": {}}), view["transfer_id"]))
    observed = invoke("agent", "suit_retrieve", target=target, task_id="tampered")
    assert observed["mode"] == "HANDSHAKE_FAILED" and "plan" not in observed
    result["tampered_transfer_plan"] = observed["mode"]
    return result


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        print(json.dumps(run(Path(directory)), sort_keys=True))
