"""V4 T12 oracle: HOLD -> evidence -> terminal SUPPORTED, plus negative controls.
Run with cwd = Build029 source root:  python3 t12_oracle.py <out.json>
A first valid measurement returning HOLD is NOT success; we follow requested evidence
to terminal status or report BUDGET_EXHAUSTED / FIXTURE_LIMIT explicitly."""
import json, sys, tempfile
from pathlib import Path
from muse024 import initialize
from eval025.run import send

MAX_STEPS = 12

def meas(case, req, **over):
    m = {"source": f'fixture:{case["id"]}', "feature": req["feature"], "request_id": req["id"],
         "value": case["measurements"][req["feature"]]}
    m.update(over); return m

def attempt(root, token, run, m):
    try:
        r = send(root, token, "resume025", run=run, measurement=m)
        return {"outcome": "ACCEPTED", "status": r["decision"]["status"], "decision": r["decision"]}
    except Exception as e:
        return {"outcome": "REJECTED", "error": type(e).__name__}

def one_case(case, fault_injection):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "state"; token = initialize(root)
        run = "t12-" + case["id"]
        out = {"case": case["id"], "expected": case["answer"], "steps": [], "negatives": {}}
        d = send(root, token, "start025", run=run, problem=case["problem"])["decision"]
        out["initial"] = d["status"]
        if d["status"] != "HOLD":
            out["terminal"] = d["status"]; out["verdict"] = "FIXTURE_LIMIT_NO_HOLD"; return out
        first_req = d["request"]
        if fault_injection:  # rejections must not alter state: later run must be identical to control
            out["negatives"]["wrong_request_id"] = attempt(root, token, run, meas(case, first_req, request_id="not-the-request"))
            out["negatives"]["wrong_feature"] = attempt(root, token, run, meas(case, first_req, feature="not-the-feature"))
        n = 0; seen = []; last = None
        while d["status"] == "HOLD" and n < MAX_STEPS:
            req = d["request"]; seen.append(req["id"]); last = meas(case, req)
            r = send(root, token, "resume025", run=run, measurement=last)
            d = r["decision"]; n += 1
            out["steps"].append({"request_feature": req["feature"], "status_after": d["status"]})
        out["requests"] = n; out["terminal"] = d["status"]; out["answer"] = d.get("answer")
        if d["status"] == "HOLD": out["verdict"] = "BUDGET_EXHAUSTED"
        elif d["status"] == "SUPPORTED" and d.get("answer") == case["answer"]: out["verdict"] = "SUPPORTED_CORRECT"
        else: out["verdict"] = "TERMINAL_UNEXPECTED"
        if fault_injection and seen:
            stale = {"source": f'fixture:{case["id"]}', "feature": first_req["feature"], "request_id": seen[0],
                     "value": case["measurements"][first_req["feature"]]}
            out["negatives"]["duplicate_after_terminal"] = attempt(root, token, run, stale)
            conflict = dict(stale); conflict["value"] = 999
            out["negatives"]["conflicting_after_terminal"] = attempt(root, token, run, conflict)
            out["negatives"]["stale_wrong_id_after_terminal"] = attempt(root, token, run, {**stale, "request_id": "stale-id"})
            # Terminal decision must survive all of the above: re-deliver the LAST valid evidence
            # (idempotent replay) and require the identical terminal decision.
            again = attempt(root, token, run, last)
            out["terminal_unchanged_after_negatives"] = (
                again.get("outcome") == "ACCEPTED" and again.get("decision") == d)
            # Observation (not asserted): replaying an EARLY request after terminal returns its historical decision.
            out["replay_of_early_request_returns"] = out["negatives"]["duplicate_after_terminal"].get("status")
    return out

def main(path):
    data = json.loads((Path("eval025") / "workload.json").read_text())
    cases = [data["training"]] + (data["heldout"] if isinstance(data["heldout"], list) else [data["heldout"]])
    rows = []
    for c in cases:
        control = one_case(c, False); faulty = one_case(c, True)
        faulty["state_not_mutated_by_rejections"] = (
            control.get("terminal") == faulty.get("terminal") and control.get("requests") == faulty.get("requests"))
        neg = faulty.get("negatives", {})
        faulty["terminal_survives"] = faulty.get("terminal_unchanged_after_negatives") is True
        faulty["rejections_ok"] = all(neg.get(k, {}).get("outcome") == "REJECTED" for k in ("wrong_request_id", "wrong_feature", "conflicting_after_terminal", "stale_wrong_id_after_terminal"))
        rows.append({"control": control, "fault_injected": faulty})
    ok = all(r["control"]["verdict"] == "SUPPORTED_CORRECT" and r["fault_injected"]["verdict"] == "SUPPORTED_CORRECT"
             and r["fault_injected"]["rejections_ok"] and r["fault_injected"]["terminal_survives"] and r["fault_injected"]["state_not_mutated_by_rejections"] for r in rows)
    Path(path).write_text(json.dumps({"assertion_status": "PASS" if ok else "FAIL", "cases": rows}, indent=2))
    print("T12 assertion_status:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
