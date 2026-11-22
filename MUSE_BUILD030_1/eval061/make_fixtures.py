"""Generate the frozen Suit 6.1 fixtures. Labels (mechanic, technician, ...) are informational analogies only.
All 'observations' are SYNTHETIC fixture records: nothing here performs or demonstrates physical competence."""
import json, sys
from pathlib import Path
OUT = Path(sys.argv[1])


def ctx(**kw):
    base = {"capabilities": [], "effects": [], "numeric": {}, "categorical": {}, "hazard": 0, "authorization": [],
            "preconditions": [], "substitutable": {}, "incompatible": {}, "precedence": []}
    base.update(kw)
    return base


def num(v, sense="max", feasible=None):
    d = {"value": v, "sense": sense}
    if feasible is not None:
        d["feasible"] = feasible
    return d


def steps(*vs):
    return [{"verb": v, "params": ({"setpoint": 40} if v == "APPLY_FORCE" else {})} for v in vs]


FASTEN = ctx(capabilities=["grip_torque", "alignment_guide"], effects=["fastener_released"],
             numeric={"force": num(60, "max", 15), "tolerance_um": num(500, "max", 50)},
             categorical={"material": "steel"}, substitutable={"material": ["aluminium"]}, incompatible={"material": ["glass"]},
             hazard=1, authorization=["basic_tools"], preconditions=["tool_selected"], precedence=[["ALIGN", "APPLY_FORCE"]])
SEAT = ctx(capabilities=["fine_positioning"], effects=["component_seated"],
           numeric={"force": num(20, "max", 5), "tolerance_um": num(100, "max", 20)}, categorical={"material": "plastic"},
           hazard=1, authorization=["basic_tools"], precedence=[["POSITION", "INSERT"]])
INSPECT = ctx(capabilities=["illumination", "magnification"], effects=["connection_verified"], hazard=0, authorization=[])
skills = [
    {"skill_id": "fastener-release-v1", "label": "mechanic: remove a fastener with a hand driver",
     "steps": steps("SELECT_TOOL", "ALIGN", "GRIP", "ROTATE", "APPLY_FORCE", "DETECT_RESISTANCE", "VERIFY"),
     "context": FASTEN, "failure_modes": ["strip_thread"]},
    {"skill_id": "component-seat-v1", "label": "assembler: position a part and verify seating",
     "steps": steps("PREPARE", "POSITION", "ALIGN", "INSERT", "VERIFY", "CONFIRM"), "context": SEAT, "failure_modes": ["misseat"]},
    {"skill_id": "connection-inspect-v1", "label": "inspector: examine a connection under magnification",
     "steps": steps("INSPECT", "DETECT_RESISTANCE", "VERIFY", "CONFIRM"), "context": INSPECT, "failure_modes": []},
]


def target(label, verbs, precedence=(), **kw):
    return {"label": label, "required_verbs": verbs, "precedence": [list(p) for p in precedence],
            "available_preconditions": kw.pop("pre", ["tool_selected"]), "context": ctx(**kw)}


F = ["SELECT_TOOL", "ALIGN", "GRIP", "ROTATE", "APPLY_FORCE", "DETECT_RESISTANCE", "VERIFY"]
SEATV = ["PREPARE", "POSITION", "ALIGN", "INSERT", "VERIFY", "CONFIRM"]
fa = dict(capabilities=["grip_torque", "alignment_guide"], effects=["fastener_released"], categorical={"material": "steel"},
          hazard=1, authorization=["basic_tools"])
tasks = [
    # ---- Experiment 1: familiar reuse
    {"id": "F1", "kind": "familiar", "gold": "REUSE_QUALIFIED_SKILL",
     "target": target("same-context fastener task", F, [["ALIGN", "APPLY_FORCE"]], numeric={"force": num(60), "tolerance_um": num(500)}, **fa)},
    {"id": "F2", "kind": "familiar", "gold": "REUSE_QUALIFIED_SKILL",
     "target": target("same-context seating task", SEATV, [["POSITION", "INSERT"]], capabilities=["fine_positioning"],
                      effects=["component_seated"], numeric={"force": num(20), "tolerance_um": num(100)}, categorical={"material": "plastic"},
                      hazard=1, authorization=["basic_tools"], pre=[])},
    {"id": "F3", "kind": "familiar", "gold": "REUSE_QUALIFIED_SKILL",
     "target": target("same-context inspection", ["INSPECT", "DETECT_RESISTANCE", "VERIFY", "CONFIRM"],
                      capabilities=["illumination", "magnification"], effects=["connection_verified"], pre=[])},
    # ---- Experiment 2/6: positive cross-domain transfer
    {"id": "P4", "kind": "positive_reuse_subset", "gold": "REUSE_QUALIFIED_SKILL",
     "target": target("cross-domain inspection needing only a subset", ["INSPECT", "VERIFY"], capabilities=["magnification"],
                      effects=["connection_verified"], pre=[])},
    {"id": "P1B", "kind": "transfer_learned_reuse", "gold": "REUSE_QUALIFIED_TRANSFER",
     "target": target("looser small-screw task in a different domain", F, [["ALIGN", "APPLY_FORCE"]],
                      numeric={"force": num(30), "tolerance_um": num(500)}, **{**fa, "categorical": {"material": "aluminium"}})},
    {"id": "P1C", "kind": "positive_transfer", "gold": "PROPOSE_TRANSFER:TRANSFER_PROPOSED_ADAPTATION",
     "target": target("stricter small-screw task (outside the qualified envelope)", F, [["ALIGN", "APPLY_FORCE"]],
                      numeric={"force": num(18), "tolerance_um": num(500)}, **{**fa, "categorical": {"material": "aluminium"}})},
    {"id": "P3", "kind": "positive_transfer_reorder", "gold": "PROPOSE_TRANSFER:TRANSFER_PROPOSED_ADAPTATION",
     "target": target("seat a delicate part: verify alignment before insertion", SEATV, [["VERIFY", "INSERT"]],
                      capabilities=["fine_positioning"], effects=["component_seated"], numeric={"force": num(20), "tolerance_um": num(100)},
                      categorical={"material": "plastic"}, hazard=1, authorization=["basic_tools"], pre=[])},
    {"id": "P5", "kind": "positive_transfer", "gold": "PROPOSE_TRANSFER:TRANSFER_PROPOSED_ADAPTATION",
     "target": target("fastener task with one level higher consequences", F, [["ALIGN", "APPLY_FORCE"]],
                      numeric={"force": num(60), "tolerance_um": num(500)}, **{**fa, "hazard": 2})},
    # ---- previously failed transfer context must not be silently retried
    {"id": "X2", "kind": "failed_transfer_retained", "gold": "DO_NOT_TRANSFER",
     "target": target("tolerance-tight fastener task (an earlier attempt failed)", F, [["ALIGN", "APPLY_FORCE"]],
                      numeric={"force": num(60), "tolerance_um": num(200)}, **fa)},
    # ---- Experiment 3: misleading similarity (same verbs, tool capabilities, looks the same)
    {"id": "N1", "kind": "negative", "gold": "DO_NOT_TRANSFER",
     "target": target("looks identical, but needs a sterile-procedure authorization", F, [["ALIGN", "APPLY_FORCE"]],
                      numeric={"force": num(60), "tolerance_um": num(500)}, **{**fa, "authorization": ["basic_tools", "sterile_procedure"]})},
    {"id": "N2", "kind": "negative", "gold": "DO_NOT_TRANSFER",
     "target": target("same operation, severe consequences", F, [["ALIGN", "APPLY_FORCE"]],
                      numeric={"force": num(60), "tolerance_um": num(500)}, **{**fa, "hazard": 3})},
    {"id": "N3", "kind": "negative", "gold": "DO_NOT_TRANSFER",
     "target": target("force budget below the level at which the procedure works", F, [["ALIGN", "APPLY_FORCE"]],
                      numeric={"force": num(8), "tolerance_um": num(500)}, **fa)},
    {"id": "N4", "kind": "negative", "gold": "DO_NOT_TRANSFER",
     "target": target("declared-incompatible material", F, [["ALIGN", "APPLY_FORCE"]],
                      numeric={"force": num(60), "tolerance_um": num(500)}, **{**fa, "categorical": {"material": "glass"}})},
    {"id": "N5", "kind": "negative", "gold": "DO_NOT_TRANSFER",
     "target": target("minimum-force requirement where the skill is limited by a maximum", F, [["ALIGN", "APPLY_FORCE"]],
                      numeric={"force": num(60, "min"), "tolerance_um": num(500)}, **fa)},
    {"id": "N6", "kind": "negative", "gold": "DO_NOT_TRANSFER",
     "target": target("tolerance tighter than the procedure can achieve", SEATV, [["POSITION", "INSERT"]], capabilities=["fine_positioning"],
                      effects=["component_seated"], numeric={"force": num(20), "tolerance_um": num(10)},
                      categorical={"material": "plastic"}, hazard=1, authorization=["basic_tools"], pre=[])},
    # ---- Experiment 5: unfamiliar tools/procedures (not prohibited)
    {"id": "U1", "kind": "unfamiliar", "gold": "UNFAMILIAR_REDISCOVERY_REQUIRED",
     "target": target("specialised suction instrument", ["REMOVE", "STOP_ON"], capabilities=["suction"], effects=["particle_cleared"], pre=[])},
    {"id": "U2", "kind": "unfamiliar", "gold": "UNFAMILIAR_REDISCOVERY_REQUIRED",
     "target": target("unfamiliar instrument, permitted by the environment", ["REMOVE", "STOP_ON"], capabilities=["suction"],
                      effects=["particle_cleared"], hazard=2, pre=[])},
]
# training evidence (SYNTHETIC fixture observations recorded by independent observer principals)
training = {
    "practice": {s["skill_id"]: [{"outcome": "SUCCESS", "evidence_ref": f"fixture-obs:{s['skill_id']}:{i}"} for i in range(2)] for s in skills},
    "transfers": {
        "P1": {"target_task": "P1", "skill_id": "fastener-release-v1",
               "target": target("small-screw task in a different domain (training)", F, [["ALIGN", "APPLY_FORCE"]],
                                numeric={"force": num(25), "tolerance_um": num(500)}, **{**fa, "categorical": {"material": "aluminium"}}),
               "attempts": [{"outcome": "SUCCESS", "evidence_ref": f"fixture-obs:P1:{i}", "executed_order": F,
                             "measured": {"force": 20 + i % 2, "tolerance_um": 400}} for i in range(3)]},
        "X2": {"target_task": "X2", "skill_id": "fastener-release-v1", "target": [t for t in tasks if t["id"] == "X2"][0]["target"],
               "attempts": [{"outcome": "SUCCESS", "evidence_ref": "fixture-obs:X2:0", "executed_order": F, "measured": {"force": 40, "tolerance_um": 150}},
                            {"outcome": "SUCCESS", "evidence_ref": "fixture-obs:X2:1", "executed_order": F, "measured": {"force": 41, "tolerance_um": 380}},
                            {"outcome": "SUCCESS", "evidence_ref": "fixture-obs:X2:2", "executed_order": F, "measured": {"force": 40, "tolerance_um": 120}}]},
    },
}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "skills.json").write_text(json.dumps(skills, indent=1, sort_keys=True) + "\n")
(OUT / "tasks.json").write_text(json.dumps(tasks, indent=1, sort_keys=True) + "\n")
(OUT / "training.json").write_text(json.dumps(training, indent=1, sort_keys=True) + "\n")
print(len(skills), len(tasks))
