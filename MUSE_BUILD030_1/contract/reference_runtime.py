"""Portable, model-independent reference runtime for MUSE cognitive pathways.

Unifies ReferenceRuntime (CognitiveRouter, RelationshipEngine, HoldStore) with
the full SoSo / 49 Brains Pipeline, Mac002PathwayAdapter, and RealCog IOULedger.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "realcog")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters import cognitive_layer as real_cl, durable_memory as real_dm  # noqa: E402
from ph6_iouledger import IOULedger  # noqa: E402

from .authority import require_auth0, require_task
from .cognitive_recorder import CognitiveRecorder
from .disposition_engine import (
    HoldStore,
    deliver_evidence_and_reactivate,
    evaluate_motor_branches,
    explain_disposition_pathway,
    propagate_authorized_results,
)
from .mac002_adapter import Mac002PathwayAdapter
from .pipeline import Pipeline, Policy
from .seven_domain_cognition import evaluate_seven_domain_task
from .relationship_engine import RelationshipCandidate, RelationshipEngine
from .relationship_store import RelationshipStore
from .router import CognitiveRouter

RUNTIME_VERSION = "muse-realcog-unified/0.2"
API_VERSION = "v1"
DEFAULT_DISPOSITION_EDGES = {
    ("U09", "U37_MECH"),
    ("U09", "L24_THERMAL"),
    ("U09", "U10_ELECTRICAL"),
    ("U10", "U37_ELECTRICAL"),
}


class ReferenceRuntime:
    """Stable library API for CLI, HTTP, and other-AI clients."""

    def __init__(self, data_dir: str | os.PathLike[str] = ".realcog-data"):
        self.data_dir = Path(data_dir)
        self.trace_dir = self.data_dir / "traces"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.pipeline_dir = self.data_dir / "pipeline_state"
        self.pipeline_dir.mkdir(parents=True, exist_ok=True)
        self._relationship_store = RelationshipStore(str(self.data_dir / "relationships.json"))
        self.hold_store = HoldStore(self.data_dir / "disposition_holds.json")
        self.runs: dict[str, dict[str, Any]] = {}

    def _recorder(self, run_id: str, task_id: str = "unknown") -> CognitiveRecorder:
        return CognitiveRecorder(str(self.trace_dir / f"{run_id}.jsonl"), run_id=run_id, task_id=task_id)

    def _run(self, run_id: str) -> dict[str, Any]:
        if run_id not in self.runs:
            path = self.trace_dir / f"{run_id}.jsonl"
            if not path.exists():
                raise KeyError(f"unknown run_id: {run_id}")
            self.runs[run_id] = {"task": {}, "task_id": "unknown"}
        return self.runs[run_id]

    def health(self) -> dict[str, Any]:
        return {
            "runtime": RUNTIME_VERSION,
            "api": API_VERSION,
            "execution_boundary": "UNIFIED_REALCOG_SOSO_STANDALONE",
            "relationship_store": str(self.data_dir / "relationships.json"),
            "disposition_hold_store": str(self.hold_store.path),
            "pipeline_state_dir": str(self.pipeline_dir),
            "unified_subsystems": {
                "pipeline": True,
                "mac002": True,
                "disposition_engine": True,
                "realcog_2208_and_iou": True,
            },
        }

    def pipeline_run(self, task: Mapping[str, Any], use_mac002: bool = True) -> dict[str, Any]:
        """Execute the full SoSo / 49 Brains / RealCog Pipeline with unified HoldStore."""
        require_task(task)
        if task.get("task_type") in {"cognitive_023", "cognitive_020", "observe", "recall_observations"}:
            from soso_app import run_cli_task
            import json
            return run_cli_task(self.data_dir, json.dumps(task), use_mac002)
        adapter = Mac002PathwayAdapter(enable_screening=True, enable_qualification=True) if use_mac002 else None
        pipe = Pipeline(
            str(self.pipeline_dir),
            Policy("v1"),
            soso_modules={"durable_memory": real_dm, "cognitive_layer": real_cl},
            mac002_adapter=adapter,
        )
        pipe.disposition_store_path = str(self.hold_store.path)
        try:
            return pipe.run(dict(task))
        finally:
            pipe.close()

    def realcog_status(self) -> dict[str, Any]:
        """Inspect RealCog IOULedger and unified HoldStore state."""
        iou_db = self.pipeline_dir / "iou.db"
        iou_state = {}
        iou_events = 0
        if iou_db.exists():
            ledger = IOULedger(str(iou_db), "TERMINAL")
            try:
                iou_events = len(ledger.events())
                iou_state = ledger.state()
            finally:
                ledger.close()
        return {
            "pipeline_state_dir": str(self.pipeline_dir),
            "iou_db_exists": iou_db.exists(),
            "iou_event_count": iou_events,
            "iou_state": iou_state,
            "holds": self.hold_store.list_holds(),
        }


    def handshake(self, inject_fault: str | None = None, optional_fault: str | None = None) -> dict[str, Any]:
        """Run behavioral I/O qualification across all 14 designated units (Directive 013)."""
        from .unified_lifecycle import run_behavioral_handshake
        return run_behavioral_handshake(self.data_dir, inject_fault=inject_fault, optional_fault=optional_fault)

    def status(self, inject_fault: str | None = None, optional_fault: str | None = None) -> dict[str, Any]:
        """Report unified lifecycle state (READY / DEGRADED / FAILED) and subsystem inventory."""
        hs = self.handshake(inject_fault=inject_fault, optional_fault=optional_fault)
        return {
            "lifecycle_state": hs["lifecycle_state"],
            "handshake_status": hs["handshake_status"],
            "mandatory_passed": hs["mandatory_passed"],
            "mandatory_total": hs["mandatory_total"],
            "optional_passed": hs["optional_passed"],
            "optional_total": hs["optional_total"],
            "memory_inventory": hs["memory_inventory"],
            "native_vision_status": hs["native_vision_status"],
            "realcog_status": self.realcog_status(),
        }

    def task(self, task: Mapping[str, Any], profile: str | None = "auto",
             evidence: Mapping[str, Any] | None = None, run_id: str | None = None) -> dict[str, Any]:
        require_task(task)
        run_id = run_id or uuid.uuid4().hex
        task = dict(task); task.setdefault("task_id", run_id)
        recorder = self._recorder(run_id, str(task["task_id"]))
        router = CognitiveRouter(recorder=recorder)
        state = router.route(task, profile=profile, evidence=evidence)
        self.runs[run_id] = {"task": task, "task_id": str(task["task_id"]),
                             "state": state, "router": router}
        out = {"run_id": run_id, "runtime": RUNTIME_VERSION, "state": state.to_dict(),
               "next": state.steps[0].to_dict() if state.steps else None}
        if "disposition_observation" in task:
            obs = dict(task["disposition_observation"])
            obs.setdefault("obs_id", str(task["task_id"]))
            events = evaluate_motor_branches(obs, execution_id=run_id)
            prop = propagate_authorized_results(
                events,
                DEFAULT_DISPOSITION_EDGES,
                observation=obs,
                hold_store=self.hold_store,
                evidence_available=evidence,
                recorder=recorder,
            )
            out["disposition"] = {
                "events": events,
                "propagation": prop,
                "explanation": explain_disposition_pathway(events, prop),
                "causal_trace": recorder.replay(),
                "trace_digest": recorder.digest(),
            }
        if "cognitive_domain" in task:
            cog_res = evaluate_seven_domain_task(task, execution_id=run_id)
            recorder.append("EVALUATE", {
                "stage": "SEVEN_DOMAIN_EVALUATED",
                "domain": cog_res["domain"],
                "status": cog_res["status"],
                "disposition": cog_res["disposition_event"]["disposition"],
            }, provenance="executed")
            out["cognitive_result"] = cog_res
        return out

    def deliver_evidence(self, evidence: Mapping[str, Any], run_id: str | None = None,
                         authority: int = 0) -> dict[str, Any]:
        require_auth0(authority)
        run_id = run_id or f"resume-{uuid.uuid4().hex[:12]}"
        recorder = self._recorder(run_id, task_id=run_id)
        reactivation = deliver_evidence_and_reactivate(
            self.hold_store,
            evidence,
            authorized_edges=DEFAULT_DISPOSITION_EDGES,
            recorder=recorder,
        )
        return {
            "run_id": run_id,
            "reactivation": reactivation,
            "holds": self.hold_store.list_holds(),
            "causal_trace": recorder.replay(),
            "trace_digest": recorder.digest(),
        }

    def list_holds(self, status: str | None = None) -> dict[str, Any]:
        return {"holds": self.hold_store.list_holds(status=status)}

    def observe(self, run_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        require_task(event)
        run = self._run(run_id)
        router = run.get("router")
        recorder = getattr(router, "recorder", None) or self._recorder(run_id, run.get("task_id", "unknown"))
        router = router or CognitiveRouter(recorder=recorder)
        state = run.get("state")
        if state is None:
            raise KeyError("run state unavailable; create task first in this process")
        router.observe(state, dict(event))
        run.update({"state": state, "router": router})
        return {"run_id": run_id, "state": state.to_dict()}

    def finish(self, run_id: str, success: bool, termination: str | None = None) -> dict[str, Any]:
        run = self._run(run_id); router = run.get("router")
        recorder = getattr(router, "recorder", None) or self._recorder(run_id, run.get("task_id", "unknown"))
        router = router or CognitiveRouter(recorder=recorder)
        state = run["state"]; router.finish(run["task"], state, bool(success), termination)
        recorder.close_run(outcome=bool(success), validation={"termination": state.termination})
        return {"run_id": run_id, "state": state.to_dict()}

    def _engine(self, run_id: str | None = None, disabled=()):
        return RelationshipEngine(recorder=self._recorder(run_id) if run_id else None,
                                  disabled_functions=disabled)

    def relationship_construct(self, observations: list[Mapping[str, Any]], run_id: str | None = None,
                               disabled=()) -> dict[str, Any]:
        engine = self._engine(run_id, disabled)
        candidates = engine.construct(observations)
        return {"runtime": RUNTIME_VERSION, "candidates": [c.to_dict() for c in candidates]}

    def relationship_qualify(self, candidates: list[Mapping[str, Any]], validation: list[Mapping[str, Any]],
                             run_id: str | None = None, disabled=()) -> dict[str, Any]:
        engine = self._engine(run_id, disabled)
        objects = [RelationshipCandidate(**dict(candidate)) for candidate in candidates]
        qualified = engine.qualify(objects, validation)
        return {"runtime": RUNTIME_VERSION, "relationships": [c.to_dict() for c in qualified]}

    def relationship_store(self, relationships: list[Mapping[str, Any]]) -> dict[str, Any]:
        self._relationship_store.save_many(relationships)
        return {"stored": [r["relationship_id"] for r in relationships], "count": len(relationships)}

    def relationship_retrieve(self, qualification: str = "VALID", input_keys=()) -> dict[str, Any]:
        records = self._relationship_store.retrieve(qualification=qualification, input_keys=input_keys)
        return {"relationships": records, "count": len(records)}

    def relationship_apply(self, relationship: Mapping[str, Any], observation: Mapping[str, Any],
                           run_id: str | None = None, disabled=()) -> dict[str, Any]:
        engine = self._engine(run_id, disabled)
        return engine.retrieve_and_apply(relationship, observation)

    def trace(self, run_id: str) -> dict[str, Any]:
        recorder = self._recorder(run_id)
        return {"run_id": run_id, "events": recorder.replay(), "digest": recorder.digest(),
                "incomplete": recorder.incomplete()}

    def replay(self, run_id: str) -> dict[str, Any]:
        trace = self.trace(run_id)
        return {"run_id": run_id, "event_count": len(trace["events"]),
                "digest": trace["digest"], "events": trace["events"],
                "reexecuted": False}
