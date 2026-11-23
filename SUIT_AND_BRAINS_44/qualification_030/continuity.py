"""Fresh-process qualification for the durable investigation and advisory outbox."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from eval025.run import send
from eval061.run import TASKS
from muse024 import initialize, issue, transaction
from sososuit42 import Journal
from suit61 import shadow029, store

ROOT = Path(__file__).resolve().parents[1]
PRINCIPAL = "continuity-actor"
RUN = "build030-continuity"
CRASH_POINTS = ("before_journal_submit", "after_journal_commit")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run_stage(stage, work, crash_point):
    state = work / "state"
    secret_path = work / "credentials.json"
    case = json.loads((ROOT / "eval025/workload.json").read_text())["training"]
    if stage == "setup":
        initialize(state)
        actor = issue(state, {"principal": PRINCIPAL, "scopes": [
            "procedure:execute", "observation:write", "big:read", "small:read",
            "suit:execute", "suit:admit", "suit:read"], "owners": []})["token"]
        observer = issue(state, {"principal": "continuity-observer",
                                "scopes": ["suit:observe"], "owners": []})["token"]
        secret_path.write_text(json.dumps({"actor": actor, "observer": observer}))
        shadow029.configure(state, True)
        target = next(task["target"] for task in TASKS if task["id"] == "P1B")
        output = send(state, actor, "unified_start025", run=RUN, problem=case["problem"],
                      targets={case["answer"]: target})
        require(output["cognitive"]["decision"]["status"] == "HOLD", "expected a committed HOLD")
        return {"initial": "HOLD"}

    tokens = json.loads(secret_path.read_text())
    actor = tokens["actor"]
    if stage == "interrupt":
        def fault(point):
            if point == crash_point:
                os._exit(79)
        shadow029.drain(state, fault=fault)
        raise RuntimeError("expected injected process exit")

    if stage == "recover":
        record, = send(state, actor, "suit_shadow029")["records"]
        require(record["status"] == "PENDING", "interrupted delivery lost outbox work")
        journal = Journal(state / "sososuit42.sqlite")
        subject = shadow029.principal_id(PRINCIPAL)
        previous = journal.pending(subject)
        require(len(previous) == int(crash_point == "after_journal_commit"), "unexpected crash-window journal state")
        recovered, = shadow029.drain(state)
        final = journal.pending(subject)
        require(recovered["status"] == "ACKED" and len(final) == 1, "delivery recovery failed")
        require(not previous or previous == final, "recovery changed the committed IOU")
        require(shadow029.drain(state) == [], "duplicate delivery after retry")
        return {"status": "ACKED", "ious_before": len(previous), "ious_after": len(final)}

    if stage == "resume":
        for count in range(len(case["measurements"]) + 1):
            result = send(state, actor, "unified_show025", run=RUN)
            decision = result["cognitive"]["decision"]
            if decision["status"] != "HOLD":
                require(decision.get("answer") == case["answer"] and decision["status"] == "SUPPORTED",
                        "unexpected terminal result")
                return {"status": decision["status"], "answer": decision["answer"], "measurements": count}
            request = decision["request"]
            feature = request["feature"]
            measurement = {"request_id": request["id"], "feature": feature,
                           "source": "fixture:continuity", "value": case["measurements"][feature]}
            reference = f"fixture:{RUN}:{request['id']}"
            send(state, tokens["observer"], "suit_observe", evidence_ref=reference,
                 observation={"kind": "investigation", "run": RUN, "principal": PRINCIPAL,
                              "measurement": measurement})
            send(state, actor, "unified_resume025", run=RUN, measurement=measurement, evidence_ref=reference)
        raise RuntimeError("continuity fixture did not reach a terminal decision")

    if stage == "audit":
        terminal = send(state, actor, "unified_show025", run=RUN)["cognitive"]["decision"]
        require(terminal["status"] == "SUPPORTED" and terminal.get("answer") == case["answer"],
                "terminal decision was not durable")
        shadow029.drain(state)
        with transaction(state) as db:
            trace = store.verify_trace(db)
        require(trace["intact"] and trace["events"] > 0, "invalid trace chain")
        rows = send(state, actor, "suit_shadow029")["records"]
        ids = set()
        for row in rows:
            require(row["status"] == "ACKED", "undelivered work")
            receipt = row["receipt"]
            disposition = receipt["disposition"]
            require(disposition["authority"] == "AUTH:0" and disposition["execution_authorization"] == "NOT_GRANTED"
                    and receipt["seal"]["executability"] == "NON_EXECUTABLE", "authority boundary violated")
            ids.add(receipt["seal"]["digest"]["sha256"])
        journal = Journal(state / "sososuit42.sqlite")
        pending = journal.pending(shadow029.principal_id(PRINCIPAL))
        require(rows and len(rows) == len(ids) == len(pending) and ids == {item["decision_id"] for item in pending},
                "receipt and IOU identities did not reconcile")
        return {"receipts": len(rows), "pending_advisory_ious": len(pending),
                "authority": "AUTH:0", "trace": trace}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("setup", "interrupt", "recover", "resume", "audit"))
    parser.add_argument("--work", type=Path)
    parser.add_argument("--crash-point", choices=CRASH_POINTS)
    args = parser.parse_args()
    os.umask(0o077)
    if args.stage:
        if args.work is None or args.crash_point is None:
            parser.error("a stage requires --work and --crash-point")
        print(json.dumps({"pid": os.getpid(), **run_stage(args.stage, args.work, args.crash_point)}))
        return

    outcomes = {}
    for point in CRASH_POINTS:
        with TemporaryDirectory(prefix="muse030-continuity-") as tmp:
            results = {}
            for stage in ("setup", "interrupt", "recover", "resume", "audit"):
                child = subprocess.run(
                    [sys.executable, "-m", "qualification_030.continuity", "--stage", stage,
                     "--work", tmp, "--crash-point", point],
                    cwd=ROOT, text=True, capture_output=True, timeout=30,
                )
                expected = 79 if stage == "interrupt" else 0
                require(child.returncode == expected, f"{stage}: rc={child.returncode}: {child.stderr}")
                results[stage] = {"exit": 79} if stage == "interrupt" else json.loads(child.stdout)
            outcomes[point] = results
    print(json.dumps(outcomes, indent=2))


if __name__ == "__main__":
    main()
