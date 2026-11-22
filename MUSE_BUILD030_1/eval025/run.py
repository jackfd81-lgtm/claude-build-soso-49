"""Reproduce the matched Build 025 investigation experiment."""
import argparse
import json
from pathlib import Path
import tempfile
import subprocess
import sys
from time import perf_counter_ns, process_time_ns

from contract.pathway025 import ARMS
from muse024 import call, initialize


def send(root, token, operation, **data):
    return call(root, token, {"authority": 0, "operation": operation, **data})


def issue_qualifier(root, token):
    return send(root, token, "issue", principal="independent-qualifier",
                scopes=["procedure:qualify", "procedure:read", "big:write", "small:write"], owners=["L15"])["token"]


def solve(root, token, case, *, arm="A", method=None, disabled=None):
    run = f'{arm}-{case["id"]}' + (f"-{disabled}" if disabled else "")
    start_wall, start_cpu = perf_counter_ns(), process_time_ns()
    output = send(root, token, "start025", run=run, problem=case["problem"], arm=arm, method=method,
                  disabled_functions=[disabled] if disabled else [])
    transcript = [output]
    while output["decision"]["status"] == "HOLD":
        request = output["decision"]["request"]
        feature = request["feature"]
        output = send(root, token, "resume025", run=run,
                      measurement={"source": f'fixture:{case["id"]}', "feature": feature, "request_id": request["id"],
                                   "value": case["measurements"][feature]})
        transcript.append(output)
    return {"id": case["id"], "run": run, "expected": case["answer"], "decision": output["decision"],
            "requests": len(transcript) - 1,
            "invocations": sum(bool(event.get("function")) for step in transcript for event in step["trace"]),
            "memory_reads": sum(record["operation"] == "read" for step in transcript
                                for record in step["memory_accesses"]),
            "unique_blocks_read": sorted({record["record_id"] for step in transcript
                                          for record in step["memory_accesses"] if record["operation"] == "read"}),
            "contributing_blocks": sorted({block for step in transcript for block in step["contributing_blocks"]}),
            "wall_ns": perf_counter_ns() - start_wall, "cpu_ns": process_time_ns() - start_cpu,
            "trace": transcript}


def experiment(directory):
    data = json.loads(Path(__file__).with_name("workload.json").read_text())
    directory.mkdir()
    root = directory / "trained"
    token = initialize(root)
    qualifier = issue_qualifier(root, token)
    cold_root = directory / "cold"
    cold_token = initialize(cold_root)
    training = solve(root, token, data["training"])
    learned = send(root, token, "learn025", run=training["run"])
    method = learned["decision"]["candidate_method"]
    qualified = send(root, qualifier, "qualify025", method=method)
    baseline_occupancy = send(root, token, "occupancy")
    # A new interpreter opens the same persisted state. Secrets stay on stdin.
    request = {"root": str(root), "token": token, "cold_root": str(cold_root),
               "cold_token": cold_token, "heldout": data["heldout"], "method": method}
    child = subprocess.run([sys.executable, "-m", "eval025.run", "--evaluate"],
                           input=json.dumps(request), capture_output=True, text=True, check=True)
    groups = json.loads(child.stdout)
    return {"training": training, "learned": learned, "qualification": qualified,
            "evaluated_in_new_process": True, "occupancy": baseline_occupancy,
            "post_evaluation_occupancy": send(root, token, "occupancy"), "arms": groups,
            "semantic_temporal_controls": semantic_temporal_controls(root, token),
            "function_ablations": ablations(root, token, data, method)}


def evaluate(root, token, cold_root, cold_token, heldout, method):
    groups = {}
    for arm, condition in ARMS.items():
        state, secret = (cold_root, cold_token) if arm == "K" else (root, token)
        rows = [solve(state, secret, case, arm=arm, method=method if arm == "D" else None)
                for case in heldout]
        correct = sum(row["decision"].get("answer") == row["expected"] for row in rows)
        abstain = sum(row["decision"]["status"] == "ABSTAIN" for row in rows)
        groups[arm] = {"condition": condition, "correct": correct, "abstain": abstain,
                       "incorrect": len(rows) - correct - abstain, "total": len(rows),
                       **{key: sum(row[key] for row in rows)
                          for key in ("requests", "invocations", "memory_reads", "wall_ns", "cpu_ns")},
                       "rows": rows}
    return groups


def ablations(root, token, data, method):
    fixtures = json.loads(Path(__file__).with_name("semantic_temporal.json").read_text())
    semantic_cases = {"L03": 1, "L04": 1, "L16": 1, "L17": 0, "L18": 0, "L22": 2, "L31": 1}
    outcomes = {}
    for fid, index in semantic_cases.items():
        problem = fixtures[index]["problem"]
        normal = send(root, token, "start025", run="comparison-" + fid, problem=problem)
        disabled = send(root, token, "start025", run="ablation-" + fid, problem=problem,
                        disabled_functions=[fid])
        outcomes[fid] = {"normal": normal, "ablated": disabled,
                         "changed_outcome": normal["decision"] != disabled["decision"]}
    held = data["heldout"][0]
    normal = solve(root, token, dict(held, id="function-comparison"), arm="D", method=method)
    for fid in ("U06", "U07", "U11", "U12", "U13", "U15"):
        disabled = solve(root, token, held, arm="D", method=method, disabled=fid)
        outcomes[fid] = {"normal": normal, "ablated": disabled,
                         "changed_outcome": normal["decision"] != disabled["decision"],
                         "changed_requests": normal["requests"] != disabled["requests"]}
    training = solve(root, token, data["training"], disabled="U14")
    disabled = send(root, token, "learn025", run=training["run"])
    normal = send(root, token, "learn025", run="A-" + data["training"]["id"])
    outcomes["U14"] = {"normal": normal, "ablated": disabled,
                       "changed_outcome": normal["decision"] != disabled["decision"]}
    return outcomes


def semantic_temporal_controls(root, token):
    fixtures = json.loads(Path(__file__).with_name("semantic_temporal.json").read_text())
    return [{"fixture": fixture["id"], "arm": arm, "expected": fixture["expected"],
             "result": send(root, token, "start025", run=f'{arm}-{fixture["id"]}',
                            problem=fixture["problem"], arm=arm)}
            for fixture in fixtures for arm in ("A", "G", "I")]



if __name__ == "__main__":
    if sys.argv[1:] == ["--evaluate"]:
        print(json.dumps(evaluate(**json.load(sys.stdin))))
        sys.exit(0)
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("output", type=Path)
    args = cli.parse_args()
    with tempfile.TemporaryDirectory(prefix="muse025-") as temporary:
        report = experiment(Path(temporary) / "state")
    with args.output.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
