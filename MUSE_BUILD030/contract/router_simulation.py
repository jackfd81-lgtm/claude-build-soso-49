"""Self-contained simulated execution for MUSE-ROUTER-001.

This is a test harness, not RealCog and not a substitute for the external
runtime. It calls the nine established IDs as named simulated functions and
records their inputs, outputs, routing decisions, and termination reason.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping

from .router import CognitiveRouter, RouteState

FIXED_SEQUENCE = ("L01", "L30", "U08", "U09", "U10", "U37", "L15", "L21")

@dataclass
class SimulationResult:
    mode: str
    profile: str
    resolved: bool
    termination: str
    trace: list[dict]
    switch_count: int = 0

    @property
    def function_sequence(self):
        return [x["function"] for x in self.trace]

    def to_dict(self):
        return {"execution": "SIMULATED", "mode": self.mode,
                "profile": self.profile, "resolved": self.resolved,
                "termination": self.termination, "trace": self.trace,
                "function_sequence": [x["function"] for x in self.trace],
                "switch_count": self.switch_count}

class SimulatedExecutor:
    def __init__(self, router: CognitiveRouter):
        self.router = router

    def _call(self, fid: str, task: Mapping[str, Any], evidence: dict) -> dict:
        tags = {str(x).lower() for x in task.get("tags", [])}
        if fid == "L01":
            return {"interpreted": True, "family": task.get("family")}
        if fid == "L30":
            return {"memory_hit": "fault" in tags or "component" in tags}
        if fid == "U08":
            return {"retrieved": True, "evidence_items": len(evidence)}
        if fid == "U09":
            return {"candidates_compared": 2, "contradiction": "contradiction" in tags}
        if fid == "U10":
            return {"conditions_disambiguated": True, "confidence": 0.8}
        if fid == "U37":
            return {"decision": "bounded-execution-eligible"}
        if fid == "L15":
            return {"executed": True, "success": bool(tags & {"fault", "component", "repair"})}
        if fid == "L21":
            return {"recorded": True}
        if fid == "L24":
            return {"recognized": bool(tags & {"novelty", "motion", "environment"})}
        return {"unavailable": True}

    def run_fixed(self, task: Mapping[str, Any]) -> SimulationResult:
        return self._run("fixed", task, "general", list(FIXED_SEQUENCE), None)

    def run_profile(self, task: Mapping[str, Any], profile: str) -> SimulationResult:
        state = self.router.route(task, profile)
        return self._run("profile", task, profile, None, state)

    def run_adaptive(self, task: Mapping[str, Any], profile: str, evidence=None) -> SimulationResult:
        state = self.router.route(task, profile, evidence=evidence)
        return self._run("adaptive", task, state.profile, None, state, adaptive=True)

    def _run(self, mode, task, profile, fixed, state: RouteState | None, adaptive=False):
        trace, evidence = [], dict((task.get("evidence") or {}))
        resolved = False
        sequence = iter(fixed) if fixed is not None else None
        while True:
            if sequence is not None:
                fid = next(sequence, None)
                step = None if fid is None else type("Step", (), {"function_id": fid})()
            else:
                step = self.router.next_available(state)
            if step is None:
                break
            fid = step.function_id
            inputs = {"task_id": task.get("task_id"), "tags": list(task.get("tags", [])), "evidence": dict(evidence)}
            output = self._call(fid, task, evidence)
            decision = {"selected": True, "reason": getattr(step, "reason", "fixed sequence")}
            if adaptive and fid == "U08":
                # A new contradiction is introduced after retrieval. The
                # Retrieval is available but insufficient. A subsequent
                # verification produces confirmed contradiction evidence;
                # the router then reprioritizes U10 in the remaining suffix.
                event = {"function": fid, "inputs": inputs, "output": output,
                         "decision": decision, "success": False,
                         "resolved": False,
                         "evidence": {"contradiction": {"value": True,
                                                         "state": "true",
                                                         "provenance": "simulated-verification"}}}
            else:
                resolved = resolved or (fid == "L15" and bool(output.get("success")))
                event = {"function": fid, "inputs": inputs, "output": output,
                         "decision": decision, "success": bool(output.get("success", True)),
                         "resolved": resolved,
                         "validation": output.get("validation")}
            trace.append(event)
            if state is not None:
                self.router.observe(state, event)
            if fid == "L21" and resolved:
                return SimulationResult(mode, profile, True, "resolved", trace,
                                        state.switch_count if state else 0)
        return SimulationResult(mode, profile, resolved,
                                "resolved" if resolved else "evidence-insufficient", trace,
                                state.switch_count if state else 0)
