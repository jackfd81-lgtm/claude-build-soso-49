"""Reproduce a budget-matched, restarted cognitive and Suit transfer evaluation."""
import argparse
from collections import Counter
import subprocess
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from eval061 import run as fixtures
from muse024 import issue
from realcog.procedural_memory import identity
from eval025.run import send as invoke

ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = ("SELECT_TOOL", "ALIGN", "GRIP", "ROTATE", "APPLY_FORCE", "DETECT_RESISTANCE", "VERIFY")


EFFECTS = ("tool_selected", "aligned", "gripped", "rotated", "force_applied", "resistance_checked", "released")


def simulate(verb, before, specimen):
    """Pure fixture executor with operation-specific output flags and measurements."""
    index = len(before)
    if index >= len(OPERATIONS) or OPERATIONS[index] != verb or list(before) != list(EFFECTS[:index]):
        raise ValueError("operation not enabled in the simulator")
    measured = {}
    if verb == "APPLY_FORCE":
        measured["force"] = specimen["force"]
    if verb == "VERIFY":
        measured["tolerance_um"] = specimen["tolerance_um"]
    return {"executor": "fixture-fastener-026", "operation": verb,
            "before": before, "after": before + [EFFECTS[index]], "measured": measured}


def validate_receipt(receipt, target, task, index, operation):
    measured = receipt["measured"]
    limits = target["context"]["numeric"]
    expected_measurements = {"APPLY_FORCE": {"force"}, "VERIFY": {"tolerance_um"}}.get(operation, set())
    if (receipt["executor"] != "fixture-fastener-026" or receipt["operation"] != operation
            or operation != OPERATIONS[index] or receipt["task"] != task
            or receipt["before"] != list(EFFECTS[:index]) or receipt["after"] != list(EFFECTS[:index + 1])
            or receipt["authority"] != "NOT_GRANTED" or set(measured) != expected_measurements):
        raise ValueError("simulation receipt failed deterministic validation")
    for name, value in measured.items():
        bound = limits[name]
        if (value > bound["value"] if bound["sense"] == "max" else value < bound["value"]):
            raise ValueError("simulation measurement outside target constraint")


def practice(root, tokens, training, proposal):
    order = [step["verb"] for step in proposal["plan"]["steps"]]
    transfer = proposal["transfer_id"]
    results = []
    for index, fixture in enumerate(training["attempts"], 1):
        before = []
        receipts = []
        measured = {}
        for verb in order:
            receipt = simulate(verb, before, fixture["measured"]) | {"task": transfer, "authority": "NOT_GRANTED"}
            validate_receipt(receipt, training["target"], transfer, len(before), verb)
            measured.update(receipt["measured"])
            before = receipt["after"]
            receipts.append(receipt)
        assert before[-1] == "released"
        written = fixtures.record_trial(
            root, tokens[f"obs{index}"], "suit_attempt", transfer_id=transfer,
            evidence_ref="fixture:practice:" + identity([index, receipts]), executed_order=order,
            measured=measured, outcome="SUCCESS",
        )
        assert written["effective_outcome"] == "SUCCESS"
        results.append({"receipts": receipts, "record": written})
    return results


def complete(root, actor, observer, handoff, target, specimen):
    assert handoff["status"] == "READY"
    receipts = []
    task = handoff["task_id"]
    before = []
    while len(before) < len(OPERATIONS):
        index = len(before)
        proposal = invoke(root, actor, "suit_next", task_id=task)
        receipt = simulate(proposal["step"], before, specimen) | {
            "task": task, "authority": proposal["execution_authorization"],
        }
        assert proposal["index"] == index
        validate_receipt(receipt, target, task, index, proposal["step"])
        digest = identity(receipt)
        ref = "fixture:execution:" + digest
        invoke(root, observer, "suit_observe", evidence_ref=ref, observation={
            "kind": "step", "task": task, "principal": "transfer-actor",
            "index": index, "verb": proposal["step"], "outcome": "SUCCESS",
        })
        state = invoke(root, actor, "suit_ack", task_id=task, index=index, evidence_ref=ref)
        before = receipt["after"]
        assert state["step"] == len(before)
        receipts.append({"evidence_ref": ref, "digest": digest, "validation": "ACCEPTED", "payload": receipt})
    assert state["status"] == "COMPLETE" and before[-1] == "released"
    return receipts


def run_heldout(root, actor, observer, run, case, target, method, enabled, budget):
    start = perf_counter_ns()
    trace = [invoke(root, actor, "unified_start025", run=run, problem=case["problem"],
                    targets={case["answer"]: target}, method=method, memory_enabled=enabled)]
    for _ in range(budget):
        decision = trace[-1]["cognitive"]["decision"]
        if decision["status"] != "HOLD":
            break
        pending = decision["request"]
        feature = pending["feature"]
        measurement = {"request_id": pending["id"], "feature": feature,
                       "source": "fixture:kiln-measurement", "value": case["measurements"][feature]}
        ref = f"fixture:{run}:measurement:{feature}"
        invoke(root, observer, "suit_observe", evidence_ref=ref, observation={
            "kind": "investigation", "run": run, "principal": "transfer-actor", "measurement": measurement,
        })
        trace.append(invoke(root, actor, "unified_resume025", run=run, measurement=measurement, evidence_ref=ref))
    reads = [receipt for step in trace for receipt in step["cognitive"]["memory_accesses"]]
    blocks = sorted({block for step in trace for block in step["cognitive"]["contributing_blocks"]})
    evidence = []
    handoff = trace[-1]["handoff"]
    decision = trace[-1]["cognitive"]["decision"]
    if handoff["status"] == "READY" and decision.get("answer") == case["answer"]:
        evidence = complete(root, actor, observer, handoff, target, {"force": 20, "tolerance_um": 400})
    return {"trace": trace, "decision": decision, "handoff": handoff, "elapsed_ns": perf_counter_ns() - start,
            "correct": decision.get("answer") == case["answer"], "task_complete": bool(evidence),
            "requests_delivered": len(trace) - 1, "budget": budget,
            "operations": dict(Counter(event["function"] for step in trace
                                       for event in step["cognitive"]["trace"] if event.get("function"))),
            "memory_accesses": reads, "contributing_blocks": blocks, "execution_evidence": evidence}


def run(root, *, shadow=False):
    root, tokens = fixtures.station(root)
    if shadow:
        from suit61.shadow029 import configure
        configure(root, True)
    fixtures.train(root, tokens, transfers=False)
    actor = issue(root, {"principal": "transfer-actor", "owners": [], "scopes": [
        "procedure:execute", "procedure:write", "observation:write", "big:read", "small:read",
        "suit:execute", "suit:admit", "suit:read", "experiment",
    ]})["token"]
    qualifier = issue(root, {"principal": "cognitive-qualifier", "owners": ["L15"], "scopes": [
        "procedure:qualify", "procedure:read", "big:write", "small:write",
    ]})["token"]
    workload = json.loads((ROOT / "eval025/workload.json").read_text())
    training = fixtures.TRAIN["transfers"]["P1"]
    target = training["target"]
    training_run = run_heldout(root, actor, tokens["obs1"], "training", workload["training"], target, None, True, 3)
    assert training_run["correct"]
    pending = training_run["handoff"]
    assert pending["status"] == "HOLD" and pending["retrieval"]["mode"] == "PROPOSE_TRANSFER"
    learned = invoke(root, actor, "unified_train026", run="training")
    method = learned["learning"]["decision"]["candidate_method"]
    qualification = invoke(root, qualifier, "qualify025", method=method)
    assert len(qualification["memory"]) == 2
    proposal = invoke(root, actor, "suit_propose", target=target, skill_id=training["skill_id"],
                      retrieval_receipt=pending["retrieval"]["receipt"])
    attempts = practice(root, tokens, training, proposal)
    transfer = proposal["transfer_id"]
    suit_qualification = invoke(root, tokens["qual"], "suit_qualify_transfer", transfer_id=transfer)
    assert suit_qualification["state"] == "TRANSFER_QUALIFIED" and suit_qualification["memory_link"]
    admitted = invoke(root, actor, "unified_retry025", run="training")["handoff"]
    assert admitted["transfer_id"] == transfer
    training_execution = complete(root, actor, tokens["obs1"], admitted, target, {"force": 20, "tolerance_um": 400})

    task = next(row["target"] for row in fixtures.TASKS if row["id"] == "P1B")
    other = next(row["target"] for row in fixtures.TASKS if row["id"] == "N1")
    child = subprocess_input(root, actor, tokens["obs2"], workload["heldout"][0], task, other, method)
    off, on, restored = (child[arm] for arm in ("off", "on", "restored"))
    assert child["restart"]["task"]["status"] == "COMPLETE"
    assert not off["task_complete"] and off["decision"]["status"] == "HOLD" and not off["memory_accesses"]
    assert all(row["task_complete"] and row["correct"] and row["handoff"]["transfer_id"] == transfer
               for row in (on, restored))
    assert off["requests_delivered"] == on["requests_delivered"] == 2
    assert set(on["contributing_blocks"]) == {row["record_id"] for row in qualification["memory"]}
    assert child["negative"]["mode"] == "DO_NOT_TRANSFER"
    assert child["unlimited_control"]["task_complete"]
    assert child["unlimited_control"]["requests_delivered"] == 3
    if shadow:
        from sososuit42 import Journal
        from suit61.shadow029 import drain, principal_id
        drained = drain(root)
        child["shadow_delivery"] = drained
        child["shadow_receipts"] = invoke(root, actor, "suit_shadow029")["records"]
        child["shadow_pending_ious"] = Journal(root / "sososuit42.sqlite").pending(principal_id("transfer-actor"))
    return {"training": training_run, "learned": learned, "qualification": qualification, "suit_qualification": suit_qualification,
            "practice": attempts, "training_execution": training_execution, "heldout": child}


def subprocess_input(root, actor, observer, case, task, incompatible, method):
    child = subprocess.run(
        [sys.executable, "-m", "eval026.run", "--heldout"], cwd=ROOT,
        input=json.dumps([str(root), actor, observer, case, task, incompatible, method]),
        text=True, capture_output=True, check=True, timeout=45,
    )
    return json.loads(child.stdout)


def evaluate(root, actor, observer, case, task, incompatible, method):
    result = {"restart": invoke(root, actor, "unified_show025", run="training")}
    for name, enabled, budget in (("on", True, 2), ("off", False, 2), ("restored", True, 2),
                                  ("unlimited_control", False, 3)):
        result[name] = run_heldout(root, actor, observer, name, case, task, method, enabled, budget)
    result["negative"] = invoke(root, actor, "suit_retrieve", task_id="negative026", target=incompatible)
    result["occupancy"] = invoke(root, actor, "occupancy")
    result["monitor_audit"] = invoke(root, actor, "suit_audit027")["records"]
    return result


if __name__ == "__main__":
    if sys.argv[1:] == ["--heldout"]:
        print(json.dumps(evaluate(*json.load(sys.stdin))))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("output", type=Path)
        parser.add_argument("--shadow029", action="store_true")
        options = parser.parse_args()
        with TemporaryDirectory(prefix="muse026-") as root:
            outcome = run(Path(root), shadow=options.shadow029)
        with options.output.open("x") as stream:
            json.dump(outcome, stream, indent=2)
            stream.write("\n")
