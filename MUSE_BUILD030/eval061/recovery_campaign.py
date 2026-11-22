"""Bounded local qualification campaign for the trusted admission and acknowledgement."""
import argparse
import json
from multiprocessing import get_context
from pathlib import Path
import tempfile
from time import monotonic

from eval061 import run as E
from muse024 import call, transaction

TARGET = next(t["target"] for t in E.TASKS if t["id"] == "P1B")


def admit(root, token, task):
    receipt = call(root, token, {"operation": "suit_retrieve", "task_id": task, "target": TARGET})
    return call(root, token, {"operation": "suit_begin", "task_id": task, "target": TARGET,
                             "skill_id": receipt["skill_id"], "transfer_id": receipt["transfer_id"],
                             "retrieval_receipt": receipt["receipt"]})


def writer(root, token, start, results):
    start.wait()
    try:
        result = admit(root, token, "race")
        results.put(result["status"])
    except ValueError as exc:
        results.put(str(exc))


class Injected(Exception):
    pass


def fault(point):
    if point == "before_suit61_ack_commit":
        raise Injected


def run(directory, iterations, processes):
    root, tok = E.station(directory)
    E.train(root, tok, transfers=True)
    ctx = get_context("fork")
    release, results = ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=writer, args=(root, tok["agent"], release, results)) for _ in range(processes)]
    start = monotonic()
    for child in workers:
        child.start()
    release.set()
    results_seen = [results.get(timeout=60) for _ in workers]
    for child in workers:
        child.join(15)
        assert child.exitcode == 0
    assert results_seen.count("READY") == 1 and results_seen.count("task exists") == processes - 1
    race_elapsed = monotonic() - start
    initial = admit(root, tok["agent"], "fault-test")
    evidence = {"kind": "step", "task": "fault-test", "principal": "agent", "index": 0,
                "verb": initial["steps"][0], "outcome": "SUCCESS"}
    call(root, tok["obs1"], {"operation": "suit_observe", "evidence_ref": "fixture:campaign", "observation": evidence})
    request = {"operation": "suit_ack", "task_id": "fault-test", "index": 0, "evidence_ref": "fixture:campaign"}
    start = monotonic()
    for _ in range(iterations):
        try:
            call(root, tok["agent"], request, fault=fault)
        except Injected:
            pass
        else:
            raise AssertionError("fault injection did not stop transaction")
    observed = call(root, tok["agent"], {"operation": "suit_resume", "task_id": "fault-test"})
    assert observed["step"] == 0 and not observed["completed"]
    assert call(root, tok["agent"], request)["step"] == 1
    assert call(root, tok["agent"], request)["duplicate"]
    with transaction(root) as db:
        count, = db.execute("SELECT COUNT(*) FROM suit61_trace WHERE kind='TASK_STEP_ACK'").fetchone()
    assert count == 1
    return {"race_processes": processes, "race_admitted": 1, "race_conflicts": processes - 1,
            "race_seconds": round(race_elapsed, 3), "injected_ack_rollbacks": iterations,
            "ack_campaign_seconds": round(monotonic() - start, 3), "committed_step_events": count,
            "trace": call(root, tok["qual"], {"operation": "suit_show", "kind": "trace"})}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--processes", type=int, default=50)
    args = parser.parse_args()
    if not 1 <= args.processes <= 50 or not 1 <= args.iterations <= 10_000:
        parser.error("campaign counts outside supported bounds")
    with tempfile.TemporaryDirectory() as directory:
        print(json.dumps(run(Path(directory), args.iterations, args.processes), sort_keys=True))
