"""Typed derived evidence for the Build 025 investigation (adapted from Claude 010).

A pending evidence request may be answered with ``measurement.derive`` instead of a literal
``value``. The derivation runs the existing Lower/Higher pathway (L01, U08, U09, U10, U37)
and returns a TYPED result: the full cognitive status, answer, strategy, qualified method,
measured work and memory accesses. A scalar for the investigation is a *projection* that the
caller must name (``project``); it is never implied. An inconclusive result is not a value:
it is returned as such, the request stays pending (HOLD), and nothing is coerced to 0.

AUTH:0 everywhere; the caller needs the ``evaluate`` scopes in addition to the resume scopes.
"""
from fractions import Fraction

from contract.cognitive_pathway_023 import run_cognitive_023
from realcog import memory024
from realcog.procedural_memory import identity

INTERFACE = "derive/1"
TASK_KEYS = {"domain", "text", "statements", "query", "events", "before"}
CONTROL_KEYS = {"mode", "disabled_functions", "ablate"}         # experiment scope only
OTHER_KEYS = {"project", "max_work"}
PROJECTIONS = ("answer", "status", "interval_seconds")
INCONCLUSIVE = {"NEED_INFORMATION", "CONTRADICTION", "INDETERMINATE", "INCONSISTENT"}


def required_scopes(spec):
    """Scopes beyond resume025 that the derivation needs (checked by the caller before running)."""
    need = {"procedure:execute", "procedure:read", "big:read", "small:read"}
    if isinstance(spec, dict) and CONTROL_KEYS & set(spec):
        need.add("experiment")
    return need


def validate(spec):
    if not isinstance(spec, dict):
        raise ValueError("derive must be an object")
    extra = set(spec) - TASK_KEYS - CONTROL_KEYS - OTHER_KEYS
    if extra:
        raise ValueError(f"unknown derive fields {sorted(extra)}")
    if spec.get("domain") not in ("mathematics", "semantic", "temporal"):
        raise ValueError("derive.domain must be mathematics, semantic or temporal")
    if spec.get("project") not in PROJECTIONS:
        raise ValueError("derive.project must name a projection: " + ", ".join(PROJECTIONS))
    budget = spec.get("max_work")
    if budget is not None and (type(budget) is not int or budget < 0):
        raise ValueError("derive.max_work must be a non-negative integer")
    if "mode" in spec and spec["mode"] not in ("evaluate", "fixed", "no_retrieval", "no_application"):
        raise ValueError("invalid derive.mode")


def project(spec, out):
    """Return (scalar | None, reason). Never invents a value."""
    status, answer = out.get("status"), out.get("answer")
    if out.get("blocked_by") or status in INCONCLUSIVE or answer is None and spec["domain"] != "temporal":
        return None, f"INCONCLUSIVE:{status}"
    kind = spec["project"]
    if kind == "status":
        return (status, "OK") if isinstance(status, str) and len(status) <= 128 else (None, "UNPROJECTABLE")
    if spec["domain"] == "mathematics":
        if kind != "answer" or isinstance(answer, list):
            return None, "UNPROJECTABLE"
        value = Fraction(answer)
        return (int(value) if value.denominator == 1 else str(value)), "OK"
    if spec["domain"] == "semantic":
        return (answer, "OK") if kind == "answer" and status in ("SUPPORTED", "OPPOSED") else (None, "UNPROJECTABLE")
    if kind == "interval_seconds":
        return (out["interval_seconds"], "OK") if type(out.get("interval_seconds")) is int else (None, "UNPROJECTABLE")
    return (status, "OK") if kind == "answer" and status in ("BEFORE", "AFTER", "SIMULTANEOUS") else (None, "UNPROJECTABLE")


def derive(root, principal, request_identity, spec):
    validate(spec)
    request = {k: v for k, v in spec.items() if k in TASK_KEYS | {"mode", "disabled_functions"}}
    request.update(task_type="cognitive_023", mode=spec.get("mode", "evaluate"),
                   task_id=f"{principal}:d025-{request_identity[:40]}")
    out = run_cognitive_023(root, request, pilot_memory=memory024.CognitiveMemory(spec.get("ablate", [])))
    value, reason = project(spec, out)
    budget = spec.get("max_work")
    if value is not None and budget is not None and out["operation_cost"] > budget:
        value, reason = None, f"BUDGET_EXCEEDED:{out['operation_cost']}>{budget}"
    return {"interface": INTERFACE, "projection_requested": spec["project"],
            "projection": None if value is None else {"kind": spec["project"], "value": value},
            "projection_status": "PROJECTED" if value is not None else reason,
            "cognitive_status": out.get("status"), "answer": out.get("answer"), "family": out.get("family"),
            "chosen_strategy": out.get("chosen_strategy"), "method_applied": out.get("method_applied"),
            "operation_cost": out["operation_cost"], "memory_integrity": out.get("memory_integrity", []),
            "functions": [[e["function_id"], e["implementation"]] for e in out.get("trace", [])],
            "memory_accesses": out.get("memory_accesses", []), "contributing_blocks": out.get("contributing_blocks", []),
            "pathway_task": out["task_id"], "pathway_digest": identity(out.get("trace", []))}
