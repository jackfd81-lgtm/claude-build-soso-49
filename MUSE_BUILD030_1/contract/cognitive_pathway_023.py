"""Executable procedure discovery, persistent recall and bounded coordination."""
from contextlib import closing

from .authority import require_task
from .brains_wiring import BrainsWiringRegistry
from .cognitive_domains_023 import FAMILIES, applicable, execute, parse
from .observation_memory import _connect, insert_rows, observation_rows
from realcog import procedural_memory as memory

DOMAINS = {"linear": "mathematics", "sum_difference": "mathematics",
           "semantic": "semantic", "temporal": "temporal"}
FUNCTIONS = {"L01", "U08", "U09", "U10", "U37"}


def _signature(family, data):
    if family == "linear":
        return "unit_zero" if data[:2] == (1, 0) else "general"
    return family


def _strategy(record, family, signature):
    if (record.get("version") != 1 or record.get("validation") != "VALIDATED"
            or record.get("family") != family or record.get("precondition") != signature):
        return None
    strategy = record.get("operation")
    if (strategy in FAMILIES[family] and record.get("parameters") == {"strategy": strategy}
            and type(record.get("training_cost")) is int and record["training_cost"] >= 0):
        return strategy
    return None


def _select(allowed, records, family, signature, mode):
    if mode == "evaluate":
        matched = [(strategy, method) for method in records
                   if (strategy := _strategy(method, family, signature)) in allowed]
        if matched:
            # Qualification-scale cost is comparable across methods; raw training cost is not (it is
            # measured on whatever size the training task happened to have). Fall back to it only for
            # methods qualified without scale evidence.
            strategy, method = min(matched, key=lambda item: (item[1].get("qualified_cost", item[1]["training_cost"]),
                                                                item[1]["id"]))
            return [strategy], method
    return allowed[:1] if mode == "fixed" else allowed, None


def _reconcile(trials):
    _, strategy, result = min(trials, key=lambda item: (item[0], item[1]))
    if any(option != result for _, _, option in trials):
        result = {"status": "CONTRADICTION", "answer": None, "reason": "strategy disagreement"}
    return strategy, result


DISPATCHER = BrainsWiringRegistry(operations={
    "L01": parse, "U08": memory.recall, "U09": _select,
    "U10": execute, "U37": _reconcile,
})


def run_cognitive_023(root, task, *, fault=None, pilot_memory=None):
    require_task(task)
    if task.get("task_type") != "cognitive_023":
        raise ValueError("expected cognitive_023 task")
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 160:
        raise ValueError("expected bounded nonempty task_id")
    mode = task.get("mode", "evaluate")
    if mode not in ("train", "evaluate", "fixed", "no_retrieval", "no_application"):
        raise ValueError("unsupported mode")
    disabled = task.get("disabled_functions", [])
    if not isinstance(disabled, list) or not all(isinstance(x, str) and x in FUNCTIONS for x in disabled):
        raise ValueError("unknown function ablation")
    if mode == "train" and disabled:
        raise ValueError("training requires complete pathway")
    observations = task.get("observations", [])
    if not isinstance(observations, list) or len(observations) > 64:
        raise ValueError("expected no more than 64 observations")
    rows = observation_rows({"task_type": "observe", "source_id": task.get("source_id"),
                             "observations": observations}) if observations else []
    payload = dict(task)
    if task.get("derive_from_observation", False):
        if not rows or len(rows) != 1:
            raise ValueError("task derivation requires exactly one observation")
        if task.get("domain") == "mathematics":
            payload["text"] = rows[0][4]
        elif task.get("domain") == "semantic":
            payload["statements"] = [rows[0][4]]
        else:
            raise ValueError("observation derivation supports math and semantics")
    def operation(fid, *args):
        return DISPATCHER.execute_operation(fid, payload, *args)

    if "L01" in disabled:
        family, data = parse(payload)  # Validate ingress only. The parsed result is withheld.
    else:
        family, data = operation("L01", payload)
    if mode == "train" and "expected_answer" not in task:
        raise ValueError("training requires independent expected_answer")
    digest = memory.identity(dict(task) if pilot_memory is None else {"pilot024": True, "task": dict(task)})
    trace = []
    signature = _signature(family, data)
    def event(fid, implementation, input_data, output, consumer, effect, accessed=()):
        trace.append({"function_id": fid, "implementation": implementation,
                      "input": input_data, "output": output, "consumer": consumer,
                      "effect": effect, "memory": list(accessed)})
    with closing(_connect(root)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            memory.configure(db)
            previous = db.execute("SELECT task_digest, output FROM executions_023 WHERE id=?",
                                  (task_id,)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise ValueError("task_id already belongs to a different input")
                return {**memory.json.loads(previous[1]), "replayed": True,
                        "storage": {"read_queries": 1, "rows_written": 0}}
            observation_writes = insert_rows(db, rows)
            if fault:
                fault("after_observation")
            event("L01", "parse", {"domain": task.get("domain"), "observation_ids": [r[0] for r in rows]},
                  {"family": family, "signature": signature}, "U08/U09", "classifies task")
            methods, scanned = [], 0
            lookup = mode not in ("fixed", "no_retrieval", "train") and "U08" not in disabled
            if lookup and "L01" not in disabled:
                if pilot_memory is None:
                    methods, scanned = operation("U08", db, family)
                else:
                    methods, scanned = pilot_memory.recall(db, family, signature)
                event("U08", "procedural_memory.recall" if pilot_memory is None else "memory024.CognitiveMemory.recall",
                      family, [m["id"] for m in methods],
                      "U09", "supplies durable method records", [m["id"] for m in methods])
            allowed = [s for s in FAMILIES[family] if applicable(s, data)]
            chosen = None
            trials, work, writes = [], 0, 0
            strategy = None
            for fid in disabled:
                event(fid, "disabled", None, None, "coordinator", "operation disabled by intervention")
            blockers = set(disabled) & {"L01", "U09", "U10", "U37"}
            if blockers:
                result = {"status": "NEED_INFORMATION", "answer": None,
                          "blocked_by": sorted(blockers)}
                # Parsing above is ingress validation. Remove the cognitive
                # parse event when L01's result is withheld.
                if "L01" in blockers:
                    trace = [e for e in trace if e["implementation"] != "parse"]
            else:
                candidates, chosen = operation("U09", allowed, methods, family, signature, mode)
                event("U09", "select_procedure", {"family": family, "precondition": signature},
                      candidates, "U10", "selects executable strategy candidates")
                for strategy_candidate in candidates:
                    answer, cost = operation("U10", family, strategy_candidate, data)
                    trials.append((cost, strategy_candidate, answer))
                    work += cost
                    event("U10", "execute:" + strategy_candidate,
                          {"task_digest": digest, "task": payload}, answer, "U37",
                          "computes candidate output", [chosen["id"]] if chosen else [])
                strategy, result = operation("U37", trials)
                event("U37", "reconcile_candidates", [a for _, _, a in trials], result,
                      "execution store", "commits result or abstention")
            learned = None
            if mode == "train":
                if result.get("answer") != task["expected_answer"] or result["status"] == "NEED_INFORMATION":
                    raise ValueError("training evidence failed independent answer validation")
                record = {"version": 1, "family": family, "precondition": signature,
                          "operation": strategy, "parameters": {"strategy": strategy},
                          "validation": "VALIDATED", "training_cost": min(t[0] for t in trials),
                          "training_evidence": {"task_id": task_id, "digest": digest,
                                                "observations": [r[0] for r in rows],
                                                "expected": task["expected_answer"]}}
                learned = memory.identity(record)
                writes += memory.remember(db, {"id": learned, **record})
            if chosen or learned:
                outcome = "TRAINING_VALIDATED" if learned else result["status"]
                writes += db.execute("INSERT INTO feedback_023 VALUES (?, ?, ?)",
                                     (task_id, learned or chosen["id"], outcome)).rowcount
            output = {"task_id": task_id, **result, "family": family,
                      "chosen_strategy": strategy, "method_applied": chosen["id"] if chosen else None,
                      "methods_retrieved": [m["id"] for m in methods], "method_learned": learned,
                      "observation_ids": [r[0] for r in rows], "operation_cost": work,
                      "trace": trace, "replayed": False,
                      "storage": {"read_queries": 1 + int(lookup and "L01" not in disabled),
                                  "method_rows_scanned": scanned, "rows_written": writes + observation_writes + 1}}
            if pilot_memory is not None:
                pilot_memory.finish(output, chosen, task_id)
            if fault:
                fault("before_execution_commit")
            db.execute("INSERT INTO executions_023 VALUES (?, ?, ?)",
                       (task_id, digest, memory.canonical(output)))
            return output
