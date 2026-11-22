"""Pure structural comparison of a practiced skill against a target task (no I/O, deterministic).

Element states are never collapsed into one similarity score:
  REUSABLE / ADAPTATION_REQUIRED / UNSUPPORTED / INCOMPATIBLE.
Names of professions, domains, tools and tasks are informational and are NEVER compared.
"""
from realcog.procedural_memory import identity

VERBS = frozenset(("ALIGN", "GRIP", "ROTATE", "APPLY_FORCE", "INSERT", "REMOVE", "POSITION", "VERIFY",
                   "DETECT_RESISTANCE", "STOP_ON", "CONFIRM", "INSPECT", "SELECT_TOOL", "PREPARE"))
SENSE = ("max", "min")
R, A, U, I = "REUSABLE", "ADAPTATION_REQUIRED", "UNSUPPORTED", "INCOMPATIBLE"


def _ident(v, limit=80):
    if not isinstance(v, str) or not v or len(v) > limit:
        raise ValueError("invalid identifier")
    return v


def _ints(d, what):
    if not isinstance(d, dict) or len(d) > 32:
        raise ValueError(what)
    for k, v in d.items():
        _ident(k)
        if type(v) is not int:
            raise ValueError(what + " must be integer-valued")
    return d


def normalize_context(c):
    """Shared schema for a practiced skill's operating envelope and a target task's requirements."""
    if not isinstance(c, dict):
        raise ValueError("context must be an object")
    numeric = c.get("numeric", {})
    if not isinstance(numeric, dict) or len(numeric) > 32:
        raise ValueError("invalid numeric constraints")
    for k, v in numeric.items():
        _ident(k)
        if not isinstance(v, dict) or set(v) - {"value", "sense", "feasible"} or type(v.get("value")) is not int \
                or v.get("sense") not in SENSE or ("feasible" in v and type(v["feasible"]) is not int):
            raise ValueError("invalid numeric constraint")
    categorical = c.get("categorical", {})
    if not isinstance(categorical, dict) or any(not isinstance(v, str) for v in categorical.values()):
        raise ValueError("invalid categorical constraints")
    for k in categorical:
        _ident(k)
    caps = c.get("capabilities", [])
    effects = c.get("effects", [])
    pre = c.get("preconditions", [])
    for name, seq in (("capabilities", caps), ("effects", effects), ("preconditions", pre)):
        if not isinstance(seq, list) or len(seq) > 32 or any(not isinstance(x, str) or not x for x in seq):
            raise ValueError("invalid " + name)
    for key in ("substitutable", "incompatible"):
        m = c.get(key, {})
        if not isinstance(m, dict) or any(not isinstance(v, list) or any(not isinstance(x, str) for x in v)
                                          for v in m.values()):
            raise ValueError("invalid " + key)
    prec = c.get("precedence", [])
    if not isinstance(prec, list) or len(prec) > 64 or any(not isinstance(p, list) or len(p) != 2 or
                                                          p[0] not in VERBS or p[1] not in VERBS for p in prec):
        raise ValueError("invalid precedence")
    for key in ("hazard",):
        if type(c.get(key, 0)) is not int or not 0 <= c.get(key, 0) <= 3:
            raise ValueError("invalid hazard level")
    auth = c.get("authorization", [])
    if not isinstance(auth, list) or any(not isinstance(x, str) for x in auth):
        raise ValueError("invalid authorization")
    return {"numeric": {k: dict(v) for k, v in sorted(numeric.items())},
            "categorical": dict(sorted(categorical.items())), "capabilities": sorted(set(caps)),
            "effects": sorted(set(effects)), "preconditions": sorted(set(pre)),
            "substitutable": {k: sorted(v) for k, v in sorted(c.get("substitutable", {}).items())},
            "incompatible": {k: sorted(v) for k, v in sorted(c.get("incompatible", {}).items())},
            "hazard": c.get("hazard", 0), "authorization": sorted(set(auth)),
            "precedence": sorted([list(p) for p in prec])}


def normalize_skill(skill):
    if not isinstance(skill, dict) or set(skill) - {"skill_id", "label", "steps", "context", "failure_modes"}:
        raise ValueError("invalid skill")
    steps = skill.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 64:
        raise ValueError("a skill needs 1-64 ordered steps")
    norm = []
    for s in steps:
        if not isinstance(s, dict) or set(s) - {"verb", "params"} or s.get("verb") not in VERBS:
            raise ValueError("step verb must be a registered primitive")
        norm.append({"verb": s["verb"], "params": dict(sorted(_ints(s.get("params", {}), "step params").items()))})
    fm = skill.get("failure_modes", [])
    if not isinstance(fm, list) or any(not isinstance(x, str) for x in fm) or len(fm) > 32:
        raise ValueError("invalid failure modes")
    return {"skill_id": _ident(skill.get("skill_id")), "label": str(skill.get("label", ""))[:120],
            "steps": norm, "context": normalize_context(skill.get("context", {})), "failure_modes": sorted(set(fm))}


def normalize_target(t):
    if not isinstance(t, dict) or set(t) - {"label", "required_verbs", "precedence", "context", "available_preconditions"}:
        raise ValueError("invalid target")
    rv, pr = t.get("required_verbs"), t.get("precedence", [])
    if not isinstance(rv, list) or not 1 <= len(rv) <= 64 or any(v not in VERBS for v in rv):
        raise ValueError("required_verbs must be registered primitives")
    if not isinstance(pr, list) or len(pr) > 64 or any(not isinstance(p, list) or len(p) != 2 or p[0] not in VERBS
                                                         or p[1] not in VERBS or p[0] == p[1] for p in pr):
        raise ValueError("invalid precedence")
    ap = t.get("available_preconditions", [])
    if not isinstance(ap, list) or any(not isinstance(x, str) for x in ap):
        raise ValueError("invalid available_preconditions")
    return {"label": str(t.get("label", ""))[:120], "required_verbs": list(rv), "precedence": [list(p) for p in pr],
            "context": normalize_context(t.get("context", {})), "available_preconditions": sorted(set(ap))}


def context_digest(target):
    """Identity of the operating context a transfer was tested for (labels excluded)."""
    t = {k: v for k, v in target.items() if k != "label"}
    return identity(t)


def satisfies(order, precedence):
    pos = {}
    for i, v in enumerate(order):
        pos.setdefault(v, i)
    return all(a in pos and b in pos and pos[a] < pos[b] for a, b in precedence)


def reorder(verbs, precedence):
    """Stable topological repair of a verb order so every precedence pair holds, else None."""
    remaining, out = list(verbs), []
    pred = {v: {a for a, b in precedence if b == v and a in verbs} for v in verbs}
    while remaining:
        ready = [v for v in remaining if not (pred[v] - set(out))]
        if not ready:
            return None
        out.append(ready[0]); remaining.remove(ready[0])
    return out


def compare(skill, target, ordering=None):
    """skill/target normalized. ordering: None (not evaluated) | CONSISTENT | VIOLATED | NEED_INFORMATION."""
    sc, tc = skill["context"], target["context"]
    src_verbs = [s["verb"] for s in skill["steps"]]
    els = []

    def add(kind, name, state, why, **extra):
        els.append({"kind": kind, "name": name, "state": state, "why": why, **extra})

    for v in dict.fromkeys(target["required_verbs"]):
        add("primitive", v, R if v in src_verbs else U, "practiced" if v in src_verbs else "no practice of this primitive")
    for cap in tc["capabilities"]:
        add("tool_capability", cap, R if cap in sc["capabilities"] else U,
            "tool offers capability" if cap in sc["capabilities"] else "capability not evidenced for practiced tool")
    for e in tc["effects"]:
        add("effect", e, R if e in sc["effects"] else U, "expected effect practiced" if e in sc["effects"] else "effect not evidenced")
    for p in sc["preconditions"]:
        ok = p in target["available_preconditions"]
        add("precondition", p, R if ok else U, "available" if ok else "source precondition not shown to hold")
    for name, c in tc["numeric"].items():
        s = sc["numeric"].get(name)
        if s is None:
            add("constraint", name, U, "target constraint outside practiced envelope")
        elif s["sense"] != c["sense"]:
            add("constraint", name, I, "constraint sense differs")
        else:
            ok = c["value"] >= s["value"] if c["sense"] == "max" else c["value"] <= s["value"]
            floor = s.get("feasible")
            beyond = floor is not None and (c["value"] < floor if c["sense"] == "max" else c["value"] > floor)
            if ok:
                add("constraint", name, R, "target limit not stricter than practiced limit")
            elif beyond:
                add("constraint", name, I, "target limit below the level at which the procedure works", target=c["value"], source=s["value"])
            else:
                add("constraint", name, A, "target limit stricter: setpoint must change",
                    adapted=c["value"], source=s["value"], sense=c["sense"])
    for name, val in tc["categorical"].items():
        sv = sc["categorical"].get(name)
        if sv == val:
            add("constraint", name, R, "same")
        elif val in sc["incompatible"].get(name, []):
            add("constraint", name, I, "declared incompatible with practiced procedure", target=val, source=sv)
        elif sv is not None and val in sc["substitutable"].get(name, []):
            add("constraint", name, A, "declared substitutable with changes", target=val, source=sv)
        else:
            add("constraint", name, U, "no practice with this value", target=val, source=sv)
    th, sh = tc["hazard"], sc["hazard"]
    add("consequence", "hazard_level", R if th <= sh else (A if th == sh + 1 else I),
        "consequence level within practice" if th <= sh else "consequences more severe than practiced",
        target=th, source=sh)
    missing = [a for a in tc["authorization"] if a not in sc["authorization"]]
    add("authorization", "required_authorization", I if missing else R,
        "transfer cannot confer authorization: " + ",".join(missing) if missing else "no additional authorization needed")
    if target["precedence"]:
        if all(p in sc["precedence"] for p in target["precedence"]):
            add("ordering", "precedence", R, "order evidenced by the practiced procedure itself")
        elif ordering is None:
            add("ordering", "precedence", U, "ordering not evaluated (no cognitive review supplied)")
        elif ordering == "CONSISTENT":
            add("ordering", "precedence", R, "practiced order satisfies target precedence (reviewed)")
        elif ordering == "VIOLATED":
            fixed = reorder(src_verbs, target["precedence"])
            add("ordering", "precedence", A if fixed else I, "practiced order violates target precedence",
                reordered=fixed)
        else:
            add("ordering", "precedence", U, "ordering review inconclusive")
    states = [e["state"] for e in els]
    disposition = ("DO_NOT_TRANSFER" if I in states else
                   "TRANSFER_PROPOSED_NEEDS_EVIDENCE" if U in states else
                   "TRANSFER_PROPOSED_ADAPTATION" if A in states else "REUSE_CANDIDATE")
    n = len(els)
    shared = sum(1 for e in els if e["state"] == R)
    return {"elements": els, "disposition": disposition, "counts": {s: states.count(s) for s in (R, A, U, I)},
            "similarity_ppm": (1000000 * shared) // n if n else 0,       # informational ONLY; never sets a state
            "shared_primitives": sorted(set(target["required_verbs"]) & set(src_verbs)),
            "relevance": len(set(target["required_verbs"]) & set(src_verbs)) +
                         sum(1 for e in els if e["kind"] in ("tool_capability", "effect") and e["state"] == R)}


def adapted_plan(skill, target, comparison):
    """The procedure to test: source steps with stricter limits and a verified order."""
    order = [s["verb"] for s in skill["steps"]]
    for e in comparison["elements"]:
        if e["kind"] == "ordering" and e.get("reordered"):
            order = e["reordered"]
    limits = {e["name"]: e["adapted"] for e in comparison["elements"] if e["state"] == A and "adapted" in e}
    params = {}
    for s in skill["steps"]:
        params.setdefault(s["verb"], s["params"])
    return {"steps": [{"verb": v, "params": params.get(v, {})} for v in order], "limits": limits,
            "from_skill": skill["skill_id"]}


def covers(qualified_target, new_target):
    """A qualified transfer covers a new target only inside the envelope that was actually tested."""
    q, n = qualified_target["context"], new_target["context"]
    if not set(new_target["required_verbs"]) <= set(qualified_target["required_verbs"]):
        return False
    if not set(n["capabilities"]) <= set(q["capabilities"]) or not set(n["effects"]) <= set(q["effects"]):
        return False
    if set(n["numeric"]) - set(q["numeric"]) or set(n["categorical"]) - set(q["categorical"]):
        return False
    for name, c in n["numeric"].items():
        t = q["numeric"][name]
        if t["sense"] != c["sense"] or (c["value"] < t["value"] if c["sense"] == "max" else c["value"] > t["value"]):
            return False
    if any(q["categorical"][k] != v for k, v in n["categorical"].items()):
        return False
    if n["hazard"] > q["hazard"] or set(n["authorization"]) - set(q["authorization"]):
        return False
    return all(p in qualified_target["precedence"] for p in new_target["precedence"]) 
