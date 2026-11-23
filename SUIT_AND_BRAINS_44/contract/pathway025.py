"""Transactional advisory request, evidence, and resumption entry point."""
import json
from importlib.resources import files

from contract.authority import require_task
from contract.brains_wiring import BrainsWiringRegistry
from contract.provenance import provenance_defects
from contract.functions025 import OPERATIONS, gaps, instant, label, scalar, select, evaluate_cause
from realcog import memory025
from realcog.procedural_memory import canonical, identity

ARMS = dict(zip("ABCDEFGHIJK", (
    "normal", "memory-off", "apply-off", "qualified", "shuffled", "irrelevant",
    "lower-off", "higher-off", "routing-off", "resumption-off", "cold",
)))


def require_list(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"expected list of up to {maximum} elements")
    return value


class Trace:
    def __init__(self, event, disabled=None):
        self.event = event
        self.disabled = disabled
        self.events = []
        self.results = {}
        self.dispatcher = BrainsWiringRegistry(operations=OPERATIONS)

    def run(self, fid, inputs, consumers, dependencies=(), *, fallback=None):
        if fid == self.disabled:
            if fallback is None:
                raise ValueError(f"no fallback for {fid}")
            output = fallback()
        else:
            output = self.dispatcher.execute_operation(fid, {"authority": 0}, *inputs)
        index = len(self.events)
        result_id = identity([self.event, index, fid])
        self.events.append({"id": result_id, "authority": 0,
                            "function": fid if fid != self.disabled else None,
                            "ablated_function": fid if fid == self.disabled else None,
                            "version": "025.1", "input": inputs, "output": output,
                            "input_digest": identity(inputs), "dependencies": [self.results[dep] for dep in dependencies],
                            "possible_consumers": consumers})
        self.results[fid] = result_id
        return output

    def route(self, source, target, disposition, result):
        self.events.append({"id": identity([self.event, len(self.events), source, target]), "authority": 0,
                            "connection": [source, target], "source_event": self.results.get(source),
                            "disposition": disposition, "result": result})


def semantic(problem, observations, trace, arm):
    query = problem.get("query")
    if not isinstance(query, dict):
        raise ValueError("query required")
    subject, predicate, scope = (label(query.get("subject")), label(query.get("predicate")),
                                 label(query.get("scope", "default")))
    parsed = trace.run("L03", [observations], ["L04", "L05"],
                       fallback=lambda: {"accepted": [], "rejected": [
                           {"digest": identity(row), "reason": "observation parser unavailable"}
                           for row in observations]})
    if parsed["rejected"]:
        trace.route("L03", "L05", "DROP", parsed["rejected"])
        if arm != "I":
            trace.route("L05", "client", "PASS", trace.run("L05", [parsed["rejected"]], ["client"], ["L03"], fallback=lambda: {}))
    trace.route("L03", "L04", "PASS" if parsed["accepted"] else "DROP",
                {"accepted": [row["digest"] for row in parsed["accepted"]]})
    def exact_entities():
        return {"index": {row["digest"]: {"facts": [row]} for row in parsed["accepted"]}, "rejected": []}
    known = trace.run("L04", [parsed["accepted"]], ["L16", "L05"], ["L03"], fallback=exact_entities)
    if known["rejected"]:
        trace.route("L04", "L05", "DROP", known["rejected"])
        if arm != "I":
            trace.route("L05", "client", "PASS", trace.run("L05", [known["rejected"]], ["client"], ["L04"], fallback=lambda: {}))
    trace.route("L04", "L16", "PASS" if known["index"] else "DROP", {"entities": sorted(known["index"])})
    relations = trace.run("L16", [known["index"]], ["L22", "L17"], ["L04"],
                          fallback=lambda: [row for entry in known["index"].values() for row in entry["facts"]])
    timed = problem["pathway"] == "B"
    matching = [row for row in relations
                if (row["subject"], row["predicate"], row["scope"]) == (subject, predicate, scope)]
    blocked = bool(parsed["rejected"] or known["rejected"])
    if timed:
        chronology = trace.run("L22", [matching], ["L17", "L31"], ["L16"], fallback=lambda: {
            "groups": [], "undated": [row["digest"] for row in matching]})
        blocked |= bool(chronology["undated"])
        matching = ([dict(row, event=chronology["groups"][-1]["instant"])
                     for row in chronology["groups"][-1]["rows"]] if chronology["groups"] else [])
    if not timed:
        normalized = []
        for row in matching:
            try:
                normalized.append(dict(row, event=instant(row["event"])) if row["event"] is not None else row)
            except ValueError:
                blocked = True
        matching = normalized
        blocked |= len({row["event"] for row in matching}) > 1
    if arm == "I":
        return {"status": "ABSTAIN", "reason": "connection routing disabled"}
    compared = trace.run("L17", [matching], ["L18"], ["L22" if timed else "L16"],
                         fallback=lambda: [])
    if trace.disabled == "L17" and len(matching) > 1:
        blocked = True
    if arm == "G":
        inconsistent = []
        blocked |= any(not (pair["same_value"] and pair["same_polarity"]) for pair in compared)
    else:
        inconsistent = trace.run("L18", [compared], ["L31"], ["L17"], fallback=lambda: [])
        if trace.disabled == "L18":
            blocked |= any(not (pair["same_value"] and pair["same_polarity"]) for pair in compared)
    decision = trace.run("L31", [matching, inconsistent, blocked], ["U16"], ["L17" if arm == "G" else "L18"],
                         fallback=lambda: {"status": "ABSTAIN", "reason": "no sufficiency assessment"})
    decision = trace.run("U16", [decision, matching], ["client"], ["L31"],
                         fallback=lambda: decision)
    trace.route("U16", "client", "PASS" if decision["status"] == "SUPPORTED" else "DROP", decision)
    return decision


def validate_model(problem):
    templates = problem.get("hypotheses")
    if not isinstance(templates, dict) or not 2 <= len(templates) <= 12:
        raise ValueError("2 to 12 explicit hypothesis templates required")
    for key, template in templates.items():
        label(key)
        if not isinstance(template, dict) or not isinstance(template.get("predicts"), dict):
            raise ValueError("predictions must be a mapping")
        predictions = template["predicts"]
        if not 1 <= len(predictions) <= 24:
            raise ValueError("1 to 24 predictions required per hypothesis")
        for feature, value in predictions.items():
            label(feature)
            scalar(value)
        for value in require_list(template.get("requires", []), 24):
            label(value)
    premises = require_list(problem.get("premises", []), 48)
    for value in premises:
        label(value)
    rules = require_list(problem.get("rules", []), 48)
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("invalid rule")
        label(rule.get("then"))
        for value in require_list(rule.get("if"), 24):
            label(value)
    return templates, premises, rules


def investigate(db, problem, observed, trace, arm, method, history, previous):
    templates, premises, rules = validate_model(problem)
    inferred = trace.run("U06", [premises, rules], ["U07"], fallback=lambda: {
        "facts": premises, "contradiction": any(p.startswith("!") and p[1:] in premises for p in premises)})
    if inferred["contradiction"]:
        return {"status": "ABSTAIN", "reason": "inconsistent premises"}, [], []
    hypotheses = trace.run("U07", [templates, inferred["facts"]], ["U13"], ["U06"], fallback=dict)
    parsed = trace.run("L03", [[{**record, "subject": "investigation", "predicate": key}
                                for key, record in observed.items()]], ["U13"], fallback=lambda: {"accepted": [], "rejected": []})
    evidence = {record["predicate"]: record["value"] for record in parsed["accepted"]}
    assessment = trace.run("U13", [hypotheses, evidence], ["U11", "client"], ["U07", "L03"],
                           fallback=lambda: {"remaining": hypotheses, "causation": "UNDETERMINED"})
    remaining = assessment["remaining"]
    if previous and previous["feature"] in observed:
        history.append({"before": previous["remaining"], "after": len(remaining),
                        "profile": previous["profile"], "evidence": identity(observed[previous["feature"]])})
    if len(remaining) <= 1:
        decision = ({"status": "SUPPORTED", "answer": next(iter(remaining)), "basis": assessment["basis"],
                     "causation": assessment["causation"]} if len(remaining) == 1 and evidence and len(hypotheses) > 1 else
                    {"status": "ABSTAIN", "reason": "observations have not discriminated one eligible hypothesis"})
        trace.route("U13", "client", "PASS" if decision["status"] == "SUPPORTED" else "DROP", decision)
        return decision, [], []
    partitions = trace.run("U11", [remaining, evidence], ["U15", "U12"], ["U13"], fallback=dict)
    if not partitions or arm == "I":
        decision = {"status": "ABSTAIN", "reason": "no routed discriminating observation"}
        trace.route("U11", "client", "DROP", decision)
        return decision, [], []
    records, reads = ([], []) if arm in ("B", "K") else memory025.recall(db, method)
    # Controls change the candidate applicability profile, while leaving
    # prediction filtering and the fallback measurement solver available.
    if arm == "E":
        alternatives = [profile for profile in partitions.values()
                        if records and profile != records[0]["profile"]]
        if alternatives:
            records = [dict(records[0], profile=alternatives[0], blocks=[], control="shuffled")]
    if arm == "F":
        records = [{"profile": [99, 99], "blocks": [], "control": "irrelevant"}]
    resolved = trace.run("U15", [records, partitions], ["U12"], ["U11"], fallback=lambda: None)
    preference = resolved["profile"] if resolved and arm != "C" else None
    if arm == "H":
        chosen = {"feature": min(partitions), "used_method": False}
        trace.route("U11", "evidence_request", "PASS", {"fallback": "first eligible discriminator"})
    else:
        chosen = trace.run("U12", [partitions, preference], ["evidence_request"], ["U11", "U15"],
                           fallback=lambda: {"feature": min(partitions), "used_method": False})
    feature = chosen["feature"]
    if chosen["used_method"] and resolved["blocks"]:
        trace.route("U15", "U12", "PASS", {
            "method": resolved["method"] if "method" in resolved else None,
            "blocks": resolved["blocks"], "profile": resolved["profile"],
            "selected_feature": feature, "bus": "memory-method",
        })
    request = {"id": identity([trace.event, "evidence", feature]), "feature": feature, "remaining": len(remaining), "profile": partitions[feature],
               "release": "matching sourced measurement", "termination": "explicit expire or activation budget"}
    trace.route("U11" if arm == "H" else "U12", "evidence_request", "HOLD", request)
    return {"status": "HOLD", "request": request}, reads, resolved["blocks"] if chosen["used_method"] else []


def score_profile(profile):
    cases = json.loads(files("realcog").joinpath("qualification_025.json").read_text())
    results = []
    for case in cases:
        partitions = gaps(case["predictions"], {})
        choice = select(partitions, profile)
        feature = choice["feature"]
        remaining = evaluate_cause(case["predictions"], {feature: case["observed"][feature]})["remaining"]
        if feature != case["preferred"] or sorted(remaining) != case["surviving"]:
            raise ValueError("method failed independent discriminator qualification")
        results.append({"case": case["id"], "fixture": identity(case), "selected": feature, "remaining": sorted(remaining)})
    if not results:
        raise ValueError("empty qualification suite")
    return results


def handle(db, principal, task, audit, fault=None, derivation=None):
    if len(canonical(task).encode("utf-8")) > 65536:
        raise ValueError("request exceeds the 64 KiB cognitive envelope")
    memory025.configure(db)
    db.execute("""CREATE TABLE IF NOT EXISTS pathway_025 (
        principal TEXT, run TEXT, problem TEXT NOT NULL, state TEXT NOT NULL,
        PRIMARY KEY(principal,run))""")
    db.execute("""CREATE TABLE IF NOT EXISTS events_025 (
        principal TEXT, request TEXT, output TEXT NOT NULL, PRIMARY KEY(principal,request))""")
    action = task["operation"]
    method = task.get("method")
    if method is not None and (not isinstance(method, str) or len(method) > 80):
        raise ValueError("invalid method identity")
    if action == "qualify025":
        return audit(memory025.qualify(db, method, principal, score_profile, fault))
    if action == "revoke025":
        return audit({"revoked": db.execute("UPDATE method_025 SET revoked=1 WHERE id=? AND revoked=0",
                                            (method,)).rowcount})
    run = task.get("run")
    label(run)
    if action == "show025":
        row = db.execute("SELECT state FROM pathway_025 WHERE principal=? AND run=?",
                         (principal, run)).fetchone()
        if row is None:
            raise KeyError("investigation not found")
        state = json.loads(row[0])
        result = {"authority": 0, "run": run, "decision": state["decision"], "activation_count": state["steps"]}
        if "intake_decision" in state:
            result["intake_decision"] = state["intake_decision"]
        return result
    event = identity([principal, task])
    replay = db.execute("SELECT output FROM events_025 WHERE principal=? AND request=?", (principal, event)).fetchone()
    if replay:
        return json.loads(replay[0])
    existing = db.execute("SELECT problem,state FROM pathway_025 WHERE principal=? AND run=?",
                          (principal, run)).fetchone()
    trace = Trace(event)
    reads, blocks = [], []
    if action == "start025":
        if existing:
            raise ValueError("run already exists")
        problem = task.get("problem")
        if not isinstance(problem, dict) or problem.get("pathway") not in ("A", "B", "C"):
            raise ValueError("pathway A, B or C required")
        require_task(problem)
        arm = task.get("arm", "A")
        if arm not in ARMS:
            raise ValueError("unknown experimental arm")
        disabled = require_list(task.get("disabled_functions", []), 1)
        if any(not isinstance(fid, str) or fid not in OPERATIONS for fid in disabled):
            raise ValueError("invalid cognitive ablation")
        trace.disabled = next(iter(disabled), None)
        state = {"arm": arm, "steps": 1, "observed": {}, "history": [], "method": method,
                 "disabled": trace.disabled}
        if problem["pathway"] in ("A", "B"):
            decision = semantic(problem, require_list(problem.get("observations"), 32), trace, arm)
        else:
            intake = problem.get("intake")
            if intake is not None:
                if not isinstance(intake, dict):
                    raise ValueError("invalid intake branch")
                state["intake_decision"] = semantic({**intake, "pathway": "A"},
                    require_list(intake.get("observations"), 32), trace, arm)
            decision, reads, blocks = investigate(db, problem, {}, trace, arm, method, state["history"], None)
    else:
        if not existing:
            raise ValueError("unknown run")
        problem, state = map(json.loads, existing)
        trace.disabled = state.get("disabled")
        previous = state["decision"]
        decision = previous
        if action == "learn025":
            if (problem["pathway"] != "C" or decision["status"] != "SUPPORTED" or state["arm"] != "A"
                    or trace.disabled not in (None, "U14")):
                raise ValueError("successful normal investigation required")
            profile = trace.run("U14", [state["history"]], ["RealCog"], fallback=lambda: None)
            if profile is None:
                trace.route("U14", "RealCog", "DROP", {"reason": "no demonstrated discriminator"})
                decision = dict(decision, candidate_method=None)
            else:
                learned = memory025.record(db, principal, profile, {"problem": problem, "state": state})
                trace.route("U14", "RealCog", "PASS", {"method": learned, "profile": profile})
                decision = dict(decision, candidate_method=learned)
        elif previous["status"] != "HOLD":
            raise ValueError("run has no pending evidence")
        elif action == "expire025":
            decision = {"status": "ABSTAIN", "reason": "request expired without evidence"}
            trace.route("evidence_request", "client", "DROP", decision)
        elif action == "resume025":
            measurement = task.get("measurement")
            if not isinstance(measurement, dict):
                raise ValueError("sourced measurement required")
            require_task(measurement)
            label(measurement.get("source"))
            if provenance_defects({"provenance": [measurement["source"]]}):
                raise ValueError("measurement source failed structural provenance checks")
            if derivation is not None:
                if "value" in measurement:
                    raise ValueError("a measurement carries either value or derive, not both")
                projected = derivation["projection"]
                value = scalar(projected["value"]) if projected else None
            else:
                value = scalar(measurement.get("value"))
            pending = previous["request"]
            if (measurement.get("request_id") != pending["id"]
                    or measurement.get("feature") != pending["feature"]
                    or measurement["feature"] in state["observed"]):
                raise ValueError("measurement does not match pending condition")
            if value is None:
                # Inconclusive derivation (status, projection or budget): not evidence. The request stays
                # pending with its release conditions; nothing is coerced into a value.
                decision = previous
                trace.route("evidence_request", "client", "HOLD", {
                    "request": pending["id"], "derivation": derivation["projection_status"],
                    "cognitive_status": derivation["cognitive_status"]})
            elif state["arm"] == "J" or state["steps"] >= 24:
                decision = {"status": "ABSTAIN", "reason": "reactivation unavailable or activation budget exhausted"}
                trace.route("evidence_request", "client", "DROP", decision)
            else:
                state["observed"][measurement["feature"]] = {"value": value, "source": measurement["source"]}
                state["steps"] += 1
                trace.route("evidence_request", "U06", "PASS", {"request": pending["id"],
                                                                "evidence": identity(measurement)})
                decision, reads, blocks = investigate(db, problem, state["observed"], trace, state["arm"],
                                                      state["method"], state["history"], pending)
    if action != "learn025":
        state["decision"] = decision
        db.execute("""INSERT INTO pathway_025 VALUES (?,?,?,?)
                      ON CONFLICT(principal,run) DO UPDATE SET state=excluded.state""",
                   (principal, run, canonical(problem), canonical(state)))
    result = {"authority": 0, "run": run, "event": event, "decision": decision, "trace": trace.events,
              "memory_accesses": reads, "contributing_blocks": blocks, "activation_count": state["steps"],
              "input": task}
    if "intake_decision" in state:
        result["intake_decision"] = state["intake_decision"]
    if derivation is not None:
        result["derivation"] = derivation
    db.execute("INSERT INTO events_025 VALUES (?,?,?)", (principal, event, canonical(result)))
    if fault:
        fault("before_pathway_025_commit")
    return audit(result)
