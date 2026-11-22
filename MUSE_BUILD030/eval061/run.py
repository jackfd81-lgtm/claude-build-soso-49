"""Controlled arms on frozen fixtures. Usage: python run.py OUTDIR"""
import json, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from muse024 import call, initialize, issue
from suit61 import compare as C

FIX = HERE / "fixtures"
SKILLS = json.loads((FIX / "skills.json").read_text())
TASKS = json.loads((FIX / "tasks.json").read_text())
TRAIN = json.loads((FIX / "training.json").read_text())
ROLES = {"author": ["suit:write"], "obs1": ["suit:write", "suit:observe"], "obs2": ["suit:write", "suit:observe"], "obs3": ["suit:write", "suit:observe"],
         "qual": ["suit:qualify", "suit:read"], "agent": ["suit:execute", "suit:read", "suit:admit"],
         "brain": ["procedure:execute", "procedure:read", "big:read", "small:read"]}


def station(tmp):
    root = Path(tmp) / "st"
    initialize(root)
    return root, {n: issue(root, {"principal": n, "scopes": s, "owners": []})["token"] for n, s in ROLES.items()}


def propose(root, token, skill_id, target, **extra):
    """Retrieve first; the proposal must cite that retrieval's receipt (hop handshake)."""
    got = call(root, token, {"operation": "suit_retrieve", "target": target})
    return call(root, token, {"operation": "suit_propose", "skill_id": skill_id, "target": target,
                              "retrieval_receipt": got["receipt"], **extra})


def record_trial(root, token, operation, **data):
    mode = operation.removeprefix("suit_")
    body = {"kind": mode, "item": data["skill_id" if mode == "practice" else "transfer_id"],
            "outcome": data["outcome"]}
    if mode == "attempt":
        body.update(executed_order=data.get("executed_order", []), measured=data.get("measured", {}))
    call(root, token, {"operation": "suit_observe", "evidence_ref": data["evidence_ref"], "observation": body})
    return call(root, token, {"operation": operation, **data})


def train(root, tok, *, transfers):
    log = {"skills": {}, "transfers": {}}
    for s in SKILLS:
        call(root, tok["author"], {"operation": "suit_learn", "skill": s})
        for i, p in enumerate(TRAIN["practice"][s["skill_id"]]):
            record_trial(root, tok["obs1" if i % 2 == 0 else "obs2"], "suit_practice", skill_id=s["skill_id"], **p)
        log["skills"][s["skill_id"]] = call(root, tok["qual"], {"operation": "suit_qualify_skill", "skill_id": s["skill_id"]})["state"]
    if transfers:
        for key, tr in TRAIN["transfers"].items():
            prop = propose(root, tok["agent"], tr["skill_id"], tr["target"])
            for i, a in enumerate(tr["attempts"]):
                record_trial(root, tok["obs%d" % (1 + i % 3)], "suit_attempt", transfer_id=prop["transfer_id"], **a)
            q = call(root, tok["qual"], {"operation": "suit_qualify_transfer", "transfer_id": prop["transfer_id"]})
            log["transfers"][key] = {"state": q["state"], "status": q.get("status"), "reasons": q.get("reasons")}
    return log


def naive(threshold=600000):
    """Control: transfer whenever structural similarity >= threshold (states collapsed into one score)."""
    from suit61.compare import normalize_skill, normalize_target, compare
    rows = []
    for t in TASKS:
        tgt = normalize_target(t["target"])
        best = max((compare(normalize_skill(s), tgt, "CONSISTENT")["similarity_ppm"] for s in SKILLS))
        outcome = "NAIVE_TRANSFER" if best >= threshold else "NAIVE_NO_TRANSFER"
        rows.append({"id": t["id"], "gold": t["gold"], "kind": t["kind"], "outcome": outcome, "similarity_ppm": best,
                     "negative_transfer": t["gold"] == "DO_NOT_TRANSFER" and outcome == "NAIVE_TRANSFER"})
    return rows


def run_arm(name, *, suit, mask, transfer):
    with tempfile.TemporaryDirectory() as tmp:
        root, tok = station(tmp)
        log = train(root, tok, transfers=transfer) if suit else {}
        cfg = {"root": str(root), "fixtures": str(FIX), "suit": suit, "mask": mask, "transfer": transfer, "label": name,
               "suit_token": tok["agent"], "brain_token": tok["brain"]}
        p = subprocess.run([sys.executable, str(HERE / "child.py")], input=json.dumps(cfg), capture_output=True, text=True, check=True)
        res = json.loads(p.stdout)
        res["training"] = log
        if suit:
            res["trace"] = call(root, tok["qual"], {"operation": "suit_show", "kind": "trace"})
    rows = res["rows"]
    res["summary"] = {
        "arm": name, "tasks": len(rows), "correct_disposition": sum(r["correct"] for r in rows),
        "usable_plans_supplied": sum(r["usable_plan"] for r in rows),
        "negative_transfers": sum(1 for r in rows if r["gold"] == "DO_NOT_TRANSFER" and (r["outcome"].startswith("REUSE_") or r["outcome"].startswith("PROPOSE_TRANSFER"))),
        "critical_difference_recognised": f'{sum(1 for r in rows if r["gold"] == "DO_NOT_TRANSFER" and r["outcome"] == "DO_NOT_TRANSFER")}/{sum(1 for r in rows if r["gold"] == "DO_NOT_TRANSFER")}',
        "evidence_requests": sum(r["evidence_requests"] for r in rows), "cognitive_invocations": res["cognitive_invocations"],
        "function_steps": res["function_steps"], "cognitive_op_cost": res["cognitive_operation_cost"],
        "suit_sql_reads": res["suit_sql"]["reads"], "suit_sql_writes": res["suit_sql"]["writes"],
        "cpu_ms": res["cpu_ms"], "wall_ms": res["wall_ms"]}
    return res


def main():
    out = Path(sys.argv[1]); (out / "traces").mkdir(parents=True, exist_ok=True)
    arms = [("S0M0_baseline", dict(suit=False, mask=False, transfer=False)),
            ("A_brains_alone_S0M1", dict(suit=False, mask=True, transfer=False)),
            ("B_suit_alone_S1M0", dict(suit=True, mask=False, transfer=True)),
            ("C_brains_suit_no_transfer_S1M1", dict(suit=True, mask=True, transfer=False)),
            ("D_brains_suit_transfer_S1M1", dict(suit=True, mask=True, transfer=True))]
    summary = []
    for name, kw in arms:
        res = run_arm(name, **kw)
        (out / "traces" / f"{name}.json").write_text(json.dumps(res, indent=1, sort_keys=True))
        summary.append(res["summary"])
    nv = naive()
    (out / "traces" / "N_naive_similarity_control.json").write_text(json.dumps(nv, indent=1))
    summary.append({"arm": "N_naive_similarity_control", "tasks": len(nv), "negative_transfers": sum(r["negative_transfer"] for r in nv),
                    "transfers_taken": sum(r["outcome"] == "NAIVE_TRANSFER" for r in nv)})
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    for s in summary:
        print(json.dumps(s, sort_keys=True))


if __name__ == "__main__":
    main()
