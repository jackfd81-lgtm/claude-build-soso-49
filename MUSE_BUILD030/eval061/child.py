"""Evaluation phase executed in a FRESH process (a real restart): no in-memory state from training."""
import json, sys
from pathlib import Path
from time import perf_counter_ns, process_time_ns
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from suit61.agent import Agent

cfg = json.loads(sys.stdin.read())
tasks = json.loads(Path(cfg["fixtures"], "tasks.json").read_text())
agent = Agent(Path(cfg["root"]), suit_token=cfg.get("suit_token"), brain_token=cfg.get("brain_token"),
              suit=cfg["suit"], mask=cfg["mask"], transfer=cfg["transfer"], mode_label=cfg["label"])
rows, w0, c0 = [], perf_counter_ns(), process_time_ns()
for t in tasks:
    out = agent.handle(t["target"], permitted=True)
    sig = out["mode"] + (":" + out["proposal_disposition"] if out["mode"] == "PROPOSE_TRANSFER" else "")
    rows.append({"id": t["id"], "kind": t["kind"], "gold": t["gold"], "outcome": sig, "correct": sig == t["gold"],
                 "usable_plan": out["usable_plan"], "evidence_requests": out["evidence_requests"],
                 "brain_review": out.get("brain_review"), "familiarity": out.get("familiarity"),
                 "operational_permission": out.get("operational_permission")})
b = agent.brain
print(json.dumps({"rows": rows, "wall_ms": round((perf_counter_ns() - w0) / 1e6, 1), "cpu_ms": round((process_time_ns() - c0) / 1e6, 1),
                  "cognitive_invocations": b.invocations if b else 0, "function_steps": b.functions if b else 0,
                  "cognitive_operation_cost": b.operation_cost if b else 0, "cognitive_memory_reads": b.memory_reads if b else 0,
                  "suit_sql": agent.sql}))
