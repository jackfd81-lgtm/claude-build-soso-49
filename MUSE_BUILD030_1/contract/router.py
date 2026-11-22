"""MUSE Cognitive Router 002.

The router selects only the nine implemented function IDs exposed by
``contract.trace.FUNCTIONS``. It is advisory: policy, validation, admission,
and AUTH:0 governance remain outside this module.

Router 002 adds:
- public automatic profile selection via PROFILE_SIGNALS;
- structured evidence polarity (true/false/unknown/unverified);
- bounded replanning of the unexecuted suffix;
- append-only execution-path strategy memory.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from . import profiles as profiles_mod
from .cognitive_recorder import CognitiveRecorder
from .trace import FUNCTIONS

ROUTER_VERSION = "muse-router/2"
_CORE_9 = {"L01", "L15", "L21", "L24", "L30", "U08", "U09", "U10", "U37"}
IMPLEMENTED_FUNCTIONS = tuple(sorted(fid for fid in FUNCTIONS if fid in _CORE_9))
EXPANSION_REGISTERED_IDS = tuple(sorted(fid for fid in FUNCTIONS if fid not in _CORE_9))

# Signal metadata is intentionally separate from function plans. Plans are
# derived from profiles.py below; this is the only signal-to-profile table.
PROFILE_SIGNALS = {
    "doctor": {"symptom", "symptoms", "diagnosis", "patient", "medical", "hypothesis", "test"},
    "lawyer": {"claim", "claims", "evidence", "legal", "contradiction", "witness", "argument"},
    "mechanic": {"fault", "failure", "component", "repair", "machine", "mechanic", "causal"},
    "cat": {"novelty", "motion", "attention", "environment", "change", "visual", "surprise"},
    "crocodile": {"track", "tracking", "timing", "distance", "observe", "persistent", "trajectory"},
}

# Compatibility view: no function plans are authored here. Existing callers
# that import PROFILE_PLANS still see the canonical profiles.py values.
PROFILE_PLANS = MappingProxyType({
    pid: tuple(data.get("preferred_functions", ()))
    for pid, data in profiles_mod.PROFILES.items()
})

_TRUE = {"true", "yes", "confirmed", "present", "observed", "pass", "success"}
_FALSE = {"false", "no", "refuted", "absent", "not_observed", "fail", "failure"}
_UNKNOWN = {"unknown", "uncertain", "missing", "undetermined"}
_UNVERIFIED = {"unverified", "reported", "hypothesis", "predicted", "unconfirmed"}


def _state_from(value: Any, explicit: Any = None) -> str:
    marker = explicit if explicit is not None else value
    if isinstance(marker, bool):
        return "true" if marker else "false"
    if marker is None:
        return "unknown"
    text = str(marker).strip().lower()
    if text in _TRUE:
        return "true"
    if text in _FALSE:
        return "false"
    if text in _UNKNOWN:
        return "unknown"
    if text in _UNVERIFIED:
        return "unverified"
    # A non-empty scalar is an observed value, but not a Boolean claim.
    return "true"


def normalize_evidence(evidence: Mapping[str, Any] | None) -> dict:
    """Normalize evidence while preserving value and supplied metadata.

    Accepted forms include ``{"contradiction": False}`` and structured
    records such as ``{"value": True, "state": "unverified",
    "provenance": "sensor-1", "timestamp": "...", "confidence": .7}``.
    """
    normalized = {}
    for raw_key, raw in (evidence or {}).items():
        key = str(raw_key).lower()
        if isinstance(raw, Mapping):
            value = raw.get("value", raw.get("observation", raw.get("result")))
            explicit = raw.get("state", raw.get("status"))
            record = {"value": value, "state": _state_from(value, explicit)}
            for field_name in ("provenance", "source", "timestamp", "confidence"):
                if field_name in raw:
                    record[field_name] = raw[field_name]
        else:
            value = raw
            record = {"value": value, "state": _state_from(value)}
        normalized[key] = record
    return normalized


def _active(evidence: Mapping[str, dict], names: set[str],
            states: set[str] | None = None) -> bool:
    states = states or {"true", "unknown", "unverified"}
    return any(k in names and v.get("state") in states
               for k, v in evidence.items())


@dataclass(frozen=True)
class RouteStep:
    function_id: str
    reason: str
    score: float
    available: bool = True

    def to_dict(self) -> dict:
        return {"function": self.function_id, "reason": self.reason,
                "score": self.score, "available": self.available}


@dataclass
class RouteState:
    task_id: str
    profile: str
    steps: list[RouteStep]
    cursor: int = 0
    evidence: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    switched: bool = False
    selection: dict = field(default_factory=dict)
    task_context: dict = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)
    switch_count: int = 0
    max_switches: int = 3
    switch_history: list[dict] = field(default_factory=list)
    execution_history: list[dict] = field(default_factory=list)
    unavailable_skips: list[dict] = field(default_factory=list)
    termination: str | None = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "profile": self.profile,
            "steps": [s.to_dict() for s in self.steps], "cursor": self.cursor,
            "evidence": self.evidence, "failures": self.failures,
            "switched": self.switched, "selection": self.selection,
            "task_context": self.task_context, "completed": self.completed,
            "switch_count": self.switch_count, "max_switches": self.max_switches,
            "switch_history": self.switch_history,
            "execution_history": self.execution_history,
            "unavailable_skips": self.unavailable_skips,
            "termination": self.termination,
        }


class StrategyStore:
    """Append-only JSON strategy memory containing executed successful paths."""
    def __init__(self, path: str | None = None):
        self.path = path
        self.records: list[dict] = []
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.records = json.load(f)

    def remember(self, task: Mapping[str, Any], profile: str,
                 steps: Iterable[RouteStep] = (), success: bool = False,
                 state: RouteState | None = None):
        if not success:
            return
        # A successful plan with no execution callbacks is not a successful
        # pathway. Fail closed even when a legacy caller supplies `steps`.
        if state is None or not state.execution_history:
            return
        pathway = list(state.execution_history)
        functions = [e["function"] for e in pathway]
        termination = state.termination or "resolved"
        switches = list(state.switch_history)
        rec = {
            "task_key": CognitiveRouter.task_key(task), "profile": profile,
            "functions": functions, "pathway": pathway,
            "switches": switches, "termination": termination,
            "saved_at": time.time(),
        }
        self.records.append(rec)
        if self.path:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.records, f, sort_keys=True, indent=2)
            os.replace(tmp, self.path)

    def retrieve(self, task: Mapping[str, Any], profile: str) -> list[str] | None:
        key = CognitiveRouter.task_key(task)
        matches = [r for r in self.records
                   if r.get("task_key") == key and r.get("profile") == profile]
        if not matches:
            return None
        return list(matches[-1].get("functions") or [])

    def retrieve_record(self, task: Mapping[str, Any], profile: str) -> dict | None:
        key = CognitiveRouter.task_key(task)
        matches = [r for r in self.records
                   if r.get("task_key") == key and r.get("profile") == profile]
        return dict(matches[-1]) if matches else None


class CognitiveRouter:
    """Select, adapt, and remember strategies over implemented function IDs."""
    def __init__(self, available: Iterable[str] | None = None,
                 store: StrategyStore | None = None, max_switches: int = 3,
                 recorder: CognitiveRecorder | None = None):
        self.available = set(available if available is not None else IMPLEMENTED_FUNCTIONS)
        self.store = store or StrategyStore()
        self.max_switches = max(0, int(max_switches))
        self.recorder = recorder
        unknown = self.available - set(IMPLEMENTED_FUNCTIONS)
        if unknown:
            raise ValueError(f"router received unknown implemented functions: {sorted(unknown)}")

    def _record(self, kind: str, payload: Mapping[str, Any],
                provenance: str = "observed", task_id: str | None = None) -> None:
        if self.recorder is not None:
            self.recorder.append(kind, payload, provenance=provenance,
                                 task_id=task_id)

    @staticmethod
    def task_key(task: Mapping[str, Any]) -> str:
        material = {k: task.get(k) for k in
                    ("family", "tags", "signature", "requested_operation")}
        material["tags"] = sorted(material.get("tags") or [])
        return hashlib.sha256(json.dumps(
            material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _tokens(self, task: Mapping[str, Any], evidence: Mapping[str, Any] | None) -> set[str]:
        vals = list(task.get("tags") or []) + [task.get("family", ""),
                                                  task.get("requested_operation", "")]
        normalized = normalize_evidence(evidence)
        for key, record in normalized.items():
            # False observations do not contribute a signal. The key itself
            # is retained for true/unknown/unverified structured observations.
            if record["state"] != "false":
                vals.append(key)
                if isinstance(record.get("value"), str):
                    vals.append(record["value"])
        return {str(v).lower() for v in vals if v}

    def select_profile(self, task: Mapping[str, Any],
                       evidence: Mapping[str, Any] | None = None,
                       requested_profile: str | None = "auto") -> dict:
        """Return a deterministic profile-selection decision.

        ``auto`` selects a unique highest-overlap signal profile. Ties and
        no-match cases fail closed to ``general``. Explicit profiles are never
        overridden. ``None`` is treated as explicit ``general`` by route() to
        preserve Router 001 default behavior.
        """
        tokens = self._tokens(task, evidence)
        scores = {pid: len(tokens & signals)
                  for pid, signals in PROFILE_SIGNALS.items()}
        if requested_profile not in (None, "auto"):
            if requested_profile not in PROFILE_PLANS:
                raise ValueError(f"unknown router profile {requested_profile!r}")
            return {"requested": requested_profile, "selected": requested_profile,
                    "reason": "explicit profile", "tokens": sorted(tokens),
                    "scores": scores, "ambiguous": False}
        best = max(scores.values(), default=0)
        winners = sorted(pid for pid, score in scores.items()
                         if score == best and score > 0)
        if len(winners) == 1:
            selected, reason, ambiguous = winners[0], "profile signal match", False
        elif len(winners) > 1:
            selected, reason, ambiguous = "general", "ambiguous profile signals", True
        else:
            selected, reason, ambiguous = "general", "no profile signal", False
        return {"requested": "auto" if requested_profile is None else requested_profile,
                "selected": selected, "reason": reason,
                "tokens": sorted(tokens), "scores": scores,
                "ambiguous": ambiguous}

    @staticmethod
    def _profile_plan(profile: str) -> tuple:
        return tuple(profiles_mod.get(profile)["preferred_functions"])

    @staticmethod
    def _move_front(values, value):
        return [value] + [v for v in values if v != value]

    @staticmethod
    def _dedup(values):
        seen = set()
        for value in values:
            if value not in seen:
                seen.add(value)
                yield value

    def _priority_plan(self, profile: str, evidence: Mapping[str, dict]) -> tuple[list[str], str]:
        plan = list(self._profile_plan(profile))
        confirmed = _active(evidence, {"contradiction", "contradicted", "conflict", "failed", "failure"},
                            states={"true"})
        if confirmed:
            return self._move_front(plan, "U10"), "confirmed contradiction/failure requires disambiguation"
        nonconfirmed_contradiction = any(
            key in {"contradiction", "contradicted", "conflict"}
            and record.get("state") in {"unknown", "unverified"}
            for key, record in evidence.items())
        if nonconfirmed_contradiction:
            return self._move_front(plan, "U08"), "unverified contradiction requires verification"
        if _active(evidence, {"unknown", "uncertain", "missing", "unverified"}):
            return self._move_front(plan, "U08"), "unknown/unverified evidence requires retrieval"
        if _active(evidence, {"fault", "component", "causal"}):
            return self._move_front(plan, "L30"), "fault evidence requires experience isolation"
        return plan, "profile/task strategy"

    def route(self, task: Mapping[str, Any], profile: str | None = None,
              evidence: Mapping[str, Any] | None = None) -> RouteState:
        # None remains the Router 001 default: explicit general, not auto.
        requested = "general" if profile is None else profile
        selection = self.select_profile(task, evidence, requested)
        selected = selection["selected"]
        normalized = normalize_evidence(evidence)
        plan, reason = self._priority_plan(selected, normalized)
        remembered = self.store.retrieve(task, selected)
        memory_used = bool(remembered)
        if remembered:
            plan = remembered + [f for f in plan if f not in remembered]
            reason = "retrieved successful executed strategy"
        selection = {**selection, "memory_retrieved": memory_used}
        task_id = str(task.get("task_id", "unknown"))
        self._record("SELECT", {"profile": selected, "selection": selection,
                                 "requested_functions": list(plan)},
                     provenance="planned", task_id=task_id)
        steps = [RouteStep(fid, reason if rank == 0 else "profile-ranked follow-up",
                           float(len(plan) - rank), fid in self.available)
                 for rank, fid in enumerate(self._dedup(plan))
                 if fid in IMPLEMENTED_FUNCTIONS]
        return RouteState(
            str(task.get("task_id", "unknown")), selected, steps,
            evidence=normalized, selection=selection,
            task_context={k: task.get(k) for k in
                          ("task_id", "family", "signature", "tags", "requested_operation")},
            max_switches=self.max_switches)

    def next_available(self, state: RouteState) -> RouteStep | None:
        while state.cursor < len(state.steps):
            step = state.steps[state.cursor]
            state.cursor += 1
            if step.available:
                self._record("EXECUTE", {"function": step.function_id,
                                          "status": "requested"},
                             provenance="planned", task_id=state.task_id)
                return step
            fallback = next((candidate.function_id for candidate in state.steps[state.cursor:]
                             if candidate.available), None)
            state.unavailable_skips.append({
                "function": step.function_id,
                "reason": "function unavailable",
                "fallback": fallback,
            })
            self._record("EXECUTE", {"function": step.function_id,
                                      "status": "skipped",
                                      "reason": "function unavailable",
                                      "fallback": fallback},
                         provenance="observed", task_id=state.task_id)
        return None

    def observe(self, state: RouteState, event: Mapping[str, Any]) -> RouteState:
        """Record one result and boundedly replan only the remaining suffix."""
        before_evidence = dict(state.evidence)
        incoming = normalize_evidence(event.get("evidence") or {})
        state.evidence.update(incoming)
        function = event.get("function")
        if function:
            state.completed.append(str(function))
        success = event.get("success")
        if function and success is False and str(function) not in state.failures:
            state.failures.append(str(function))
        state.execution_history.append({
            "function": function,
            "inputs": event.get("inputs"),
            "output": event.get("output"),
            "decision": event.get("decision"),
            "success": success,
            "resolved": event.get("resolved"),
            "validation": event.get("validation"),
            "evidence_before": before_evidence,
            "evidence_after": dict(state.evidence),
        })
        self._record("OBSERVE", {"function": function, "event": dict(event),
                                  "evidence_before": before_evidence,
                                  "evidence_after": dict(state.evidence)},
                     provenance=str(event.get("provenance", "observed")),
                     task_id=state.task_id)
        if event.get("resolved") is True:
            state.termination = "resolved"
        evidence_changed = before_evidence != state.evidence
        needs_replan = (success is False or event.get("resolved") is False or evidence_changed)
        if not needs_replan:
            return state
        if state.switch_count >= state.max_switches:
            state.switch_history.append({"reason": "switch limit reached",
                                         "remaining": [s.function_id for s in state.steps[state.cursor:]]})
            return state
        remaining_before = [s.function_id for s in state.steps[state.cursor:]]
        plan, reason = self._priority_plan(state.profile, state.evidence)
        completed = set(state.completed)
        failed = set(state.failures)
        plan = [fid for fid in plan if fid not in completed and fid not in failed]
        new_steps = [RouteStep(fid, reason if i == 0 else "adaptive replan follow-up",
                               float(len(plan) - i), fid in self.available)
                     for i, fid in enumerate(self._dedup(plan))]
        remaining_after = [s.function_id for s in new_steps]
        if remaining_after != remaining_before:
            state.steps[state.cursor:] = new_steps
            state.switch_count += 1
            state.switched = True
            state.switch_history.append({
                "reason": reason, "trigger_function": function,
                "remaining_before": remaining_before,
                "remaining_after": remaining_after,
                "evidence": dict(state.evidence),
                "switch_number": state.switch_count,
            })
            self._record("EVALUATE", {"reason": reason,
                                       "trigger_function": function,
                                       "remaining_before": remaining_before,
                                       "remaining_after": remaining_after},
                         provenance="observed", task_id=state.task_id)
        return state

    def finish(self, task: Mapping[str, Any], state: RouteState, success: bool,
               termination: str | None = None):
        if termination:
            state.termination = termination
        elif state.termination is None:
            state.termination = "resolved" if success else "evidence-insufficient"
        self.store.remember(task, state.profile, state.steps, success, state=state)
        self._record("STORE", {"success": success, "termination": state.termination,
                                "executed_functions": [e.get("function")
                                                        for e in state.execution_history]},
                     provenance="observed", task_id=state.task_id)
        self._record("VALIDATE", {"success": success, "termination": state.termination,
                                   "run_complete": True},
                     provenance="observed", task_id=state.task_id)
