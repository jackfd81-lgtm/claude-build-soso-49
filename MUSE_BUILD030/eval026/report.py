"""Summarize observed Big49/Small49 cognitive use and matched outcomes."""
import argparse
import json
from pathlib import Path


def summarize(joint, cognition):
    held = joint["heldout"]
    pools = {}
    for pool, occupancy in held["occupancy"].items():
        writes = [row for row in joint["qualification"]["memory"] if row["pool"] == pool]
        identified = {row["record_id"] for row in writes}
        arms = {}
        for name in ("on", "off", "restored", "unlimited_control"):
            arm = held[name]
            reads = [row for row in arm["memory_accesses"] if row.get("pool") == pool and row["operation"] == "read"]
            consumed = sorted(identified.intersection(arm["contributing_blocks"]))
            arms[name] = {
                "reads": len(reads), "unique_blocks_read": sorted({row["record_id"] for row in reads}),
                "consumed_blocks": consumed, "persisted_and_consumed_after_restart": bool(consumed),
                "decision": arm["decision"]["status"], "task_complete": arm["task_complete"],
                "requests": arm["requests_delivered"], "elapsed_ns": arm["elapsed_ns"],
            }
        pools[pool] = {**occupancy, "valid_addressable": occupancy["addressable_by_pointer"],
                       "instrumented_cognitive_writes": len(writes), "write_receipts": writes,
                       "arms": arms}
    return {
        "scope": "Cognitive read/write receipts and total pool occupancy. Suit reads and writes are not included in receipt counters.",
        "pools": pools,
        "rejected_cognitive_retrievals_observed_in_joint_runs": sum(
            row["operation"] == "rejected"
            for arm in ("on", "off", "restored", "unlimited_control")
            for row in held[arm]["memory_accesses"]),
        "recovery_faults": "See executed test qualification_process_kill_and_concurrent_creation",
        "cognition_only": {arm: {k: row[k] for k in (
            "condition", "correct", "abstain", "incorrect", "total", "requests", "invocations", "memory_reads")}
            for arm, row in cognition["arms"].items()},
    }


if __name__ == "__main__":
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("joint", type=Path)
    command.add_argument("cognition", type=Path)
    command.add_argument("output", type=Path)
    args = command.parse_args()
    result = summarize(json.loads(args.joint.read_text()), json.loads(args.cognition.read_text()))
    with args.output.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
