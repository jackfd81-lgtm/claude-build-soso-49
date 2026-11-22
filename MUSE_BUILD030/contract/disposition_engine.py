"""Integrated PASS / DROP / HOLD Cognitive Disposition Engine for SoSo / 49 Brains.

Extends the recovered prototype with:
1. Strict AUTH:0 enforcement at event construction (nonzero authority rejected, never downgraded).
2. Full structured disposition records (function_id, input_evidence_refs, disposition,
   reason_code, result_payload, authorized_destination, missing_evidence, next_operation,
   execution_id, causal_parent_id).
3. Durable HoldStore + actual second-invocation reactivation across OS process restarts,
   with duplicate-release prevention, contradiction handling, and multi-HOLD support.
4. CognitiveRecorder causal tracing and 49-Brains pathway explanation integration.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from contract.cognitive_recorder import CognitiveRecorder


@dataclass(frozen=True)
class EdgeRule:
    edge_id: str
    source_function: str
    destination_function: str
    authorized_for_result_routing: bool = True


def make_disposition_event(
    event_id: str,
    source_function: str,
    destination_function: str,
    input_reference: str | list[str],
    disposition: str,
    reason: str,
    result: Mapping[str, Any],
    *,
    authority: int = 0,
    execution_id: str = "exec-001",
    causal_parent_id: str | None = None,
    authorized_destination: str | None = None,
) -> dict[str, Any]:
    if authority != 0 or isinstance(authority, bool):
        raise PermissionError(f"AUTH:0 violation: nonzero or invalid authority {authority!r} rejected")
    if disposition not in ("PASS", "DROP", "HOLD"):
        raise ValueError(f"Invalid disposition: {disposition!r}")

    refs = [input_reference] if isinstance(input_reference, str) else list(input_reference)
    missing = list(result.get("missing_evidence") or result.get("pending_conditions") or [])
    next_op = result.get("recommended_next_operation")

    return {
        "event_id": event_id,
        "function_id": source_function,
        "source_function": source_function,
        "destination_function": destination_function,
        "authorized_destination": authorized_destination or destination_function,
        "input_evidence_refs": refs,
        "input_reference": refs[0] if len(refs) == 1 else refs,
        "disposition": disposition,
        "reason": reason,
        "reason_code": reason,
        "result": copy.deepcopy(dict(result)),
        "result_payload": copy.deepcopy(dict(result)),
        "missing_evidence": missing,
        "next_operation": next_op,
        "payload_forwarded": disposition == "PASS",
        "execution_id": execution_id,
        "causal_parent_id": causal_parent_id,
        "authority": 0,
    }


def evaluate_motor_branches(
    observation: Mapping[str, Any],
    *,
    execution_id: str = "exec-motor-001",
    causal_parent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Evaluate motor observation across Mechanical (PASS), Thermal (DROP), Electrical (HOLD)."""
    obs_id = str(observation["obs_id"])
    facts = observation.get("facts", {})
    events: list[dict[str, Any]] = []

    # 1. Mechanical branch -> PASS
    if facts.get("current") == "high" and facts.get("rotation") == 0:
        events.append(make_disposition_event(
            event_id=f"{execution_id}-EVT-001",
            source_function="U09",
            destination_function="U37_MECH",
            input_reference=obs_id,
            disposition="PASS",
            reason="MECHANICAL_HYPOTHESIS_SUPPORTED",
            result={
                "finding": "Mechanical obstruction remains a plausible hypothesis",
                "transformed_hypothesis": "shaft_jam_or_bearing_lock",
                "recommended_next_operation": "CHECK_SHAFT_FREEDOM",
            },
            authority=0,
            execution_id=execution_id,
            causal_parent_id=causal_parent_id,
        ))

    # 2. Thermal branch -> DROP
    if "temperature_measurement" not in facts:
        events.append(make_disposition_event(
            event_id=f"{execution_id}-EVT-002",
            source_function="U09",
            destination_function="L24_THERMAL",
            input_reference=obs_id,
            disposition="DROP",
            reason="INSUFFICIENT_EVIDENCE",
            result={
                "finding": "Temperature conclusion unsupported from current alone",
                "missing_evidence": ["temperature_measurement"],
                "recommended_next_operation": "REQUEST_EVIDENCE",
                "alternative_hypothesis": "mechanical_or_electrical_fault",
            },
            authority=0,
            execution_id=execution_id,
            causal_parent_id=causal_parent_id,
        ))

    # 3. Electrical branch -> HOLD
    if "supply_voltage" not in facts:
        events.append(make_disposition_event(
            event_id=f"{execution_id}-EVT-003",
            source_function="U09",
            destination_function="U10_ELECTRICAL",
            input_reference=obs_id,
            disposition="HOLD",
            reason="PENDING_CONDITION",
            result={
                "finding": "Electrical fault remains unresolved",
                "pending_conditions": ["supply_voltage"],
                "release_rule": "supply_voltage_measured",
                "recommended_next_operation": "REQUEST_SUPPLY_VOLTAGE",
            },
            authority=0,
            execution_id=execution_id,
            causal_parent_id=causal_parent_id,
        ))
    else:
        # When supply_voltage IS present (e.g. upon reactivation), re-evaluate electrical branch!
        sv = facts["supply_voltage"]
        if isinstance(sv, (list, tuple)) or sv == "contradictory":
            events.append(make_disposition_event(
                event_id=f"{execution_id}-EVT-003R",
                source_function="U10",
                destination_function="U37_ELECTRICAL",
                input_reference=obs_id,
                disposition="DROP",
                reason="CONTRADICTORY_EVIDENCE",
                result={
                    "finding": "Conflicting supply_voltage measurements",
                    "missing_evidence": ["verified_supply_voltage"],
                    "recommended_next_operation": "ABSTAIN_AND_VERIFY",
                },
                authority=0,
                execution_id=execution_id,
                causal_parent_id=causal_parent_id,
            ))
        elif sv == "24V_nominal":
            events.append(make_disposition_event(
                event_id=f"{execution_id}-EVT-003R",
                source_function="U10",
                destination_function="U37_ELECTRICAL",
                input_reference=obs_id,
                disposition="DROP",
                reason="SUPPLY_VOLTAGE_NOMINAL_NO_FAULT",
                result={
                    "finding": "Supply voltage nominal; electrical brownout ruled out",
                    "missing_evidence": [],
                    "recommended_next_operation": "CLOSE_ELECTRICAL_HYPOTHESIS",
                },
                authority=0,
                execution_id=execution_id,
                causal_parent_id=causal_parent_id,
            ))
        else:
            events.append(make_disposition_event(
                event_id=f"{execution_id}-EVT-003R",
                source_function="U10",
                destination_function="U37_ELECTRICAL",
                input_reference=obs_id,
                disposition="PASS",
                reason="SUPPLY_VOLTAGE_ABNORMAL_CONFIRMED",
                result={
                    "finding": f"Abnormal supply voltage detected: {sv}",
                    "recommended_next_operation": "INSPECT_POWER_RAIL",
                },
                authority=0,
                execution_id=execution_id,
                causal_parent_id=causal_parent_id,
            ))
    return events


class HoldStore:
    """Durable JSON store for suspended HOLD invocations and reactivation history."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"holds": {}, "delivered_evidence": []})

    def _read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def save_hold(self, hold_record: dict[str, Any]) -> None:
        data = self._read()
        data["holds"][hold_record["hold_id"]] = copy.deepcopy(hold_record)
        self._write(data)

    def get_hold(self, hold_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self._read()["holds"].get(hold_id))

    def list_holds(self, status: str | None = None) -> list[dict[str, Any]]:
        holds = list(self._read()["holds"].values())
        if status is not None:
            holds = [h for h in holds if h["status"] == status]
        return copy.deepcopy(holds)

    def update_hold(self, hold_id: str, updates: Mapping[str, Any]) -> dict[str, Any]:
        data = self._read()
        if hold_id not in data["holds"]:
            raise KeyError(f"Unknown hold_id: {hold_id}")
        data["holds"][hold_id].update(copy.deepcopy(dict(updates)))
        self._write(data)
        return copy.deepcopy(data["holds"][hold_id])


def propagate_authorized_results(
    events: list[dict[str, Any]],
    authorized_edges: set[tuple[str, str]],
    *,
    observation: Mapping[str, Any] | None = None,
    hold_store: HoldStore | None = None,
    evidence_available: Mapping[str, Any] | None = None,
    label_only_control: bool = False,
    recorder: CognitiveRecorder | None = None,
) -> dict[str, Any]:
    """Route disposition results along authorized edges; persist HOLDs in HoldStore."""
    downstream_actions: list[dict[str, Any]] = []
    evidence_requests: list[dict[str, Any]] = []
    rejected_edges: list[dict[str, Any]] = []
    persisted_holds: list[str] = []
    external_exec_blocked = 0

    for ev in events:
        if ev.get("authority", 0) != 0:
            raise PermissionError("AUTH:0 violation during propagation")
        edge = (ev["source_function"], ev["destination_function"])
        if edge not in authorized_edges:
            rejected_edges.append({"event_id": ev["event_id"], "edge": list(edge), "reason": "UNAUTHORIZED_EDGE"})
            if recorder:
                recorder.append("EVALUATE", {"stage": "EDGE_REJECTED", "event_id": ev["event_id"], "edge": list(edge)}, provenance="executed")
            continue

        external_exec_blocked += 1  # AUTH:0 never grants external hardware execution

        if ev["disposition"] == "PASS":
            action_rec = {
                "triggered_by": ev["event_id"],
                "branch": ev["destination_function"],
                "action": ev["next_operation"],
                "payload_forwarded": True,
                "result_payload": ev["result_payload"],
            }
            downstream_actions.append(action_rec)
            if recorder:
                recorder.append("TRANSFER", {"stage": "DISPOSITION_PASS", "event_id": ev["event_id"], "destination": ev["destination_function"]}, provenance="executed")

        elif not label_only_control and ev["disposition"] == "DROP":
            if recorder:
                recorder.append("EVALUATE", {"stage": "DISPOSITION_DROP_SUPPRESSED", "event_id": ev["event_id"], "reason": ev["reason_code"]}, provenance="executed")
            for m in ev.get("missing_evidence", []):
                obs_val = (evidence_available or {}).get(m)
                evidence_requests.append({
                    "triggered_by": ev["event_id"],
                    "disposition": "DROP",
                    "requested_measurement": m,
                    "observed_value": obs_val,
                    "unblocks_branch": None,
                })

        elif not label_only_control and ev["disposition"] == "HOLD":
            pending = list(ev.get("missing_evidence", []))
            hold_id = f"HOLD-{ev['event_id']}"
            if hold_store is not None:
                hold_store.save_hold({
                    "hold_id": hold_id,
                    "status": "PENDING",
                    "source_event_id": ev["event_id"],
                    "execution_id": ev["execution_id"],
                    "function_id": ev["destination_function"],
                    "source_function": ev["source_function"],
                    "missing_evidence": pending,
                    "continuation_context": {
                        "obs_id": (observation or {}).get("obs_id", ev["input_reference"]),
                        "facts": copy.deepcopy((observation or {}).get("facts", {})),
                    },
                    "reactivation_count": 0,
                    "reactivated_events": [],
                })
                persisted_holds.append(hold_id)
            if recorder:
                recorder.append("EVALUATE", {"stage": "DISPOSITION_HOLD_PERSISTED", "hold_id": hold_id, "missing_evidence": pending}, provenance="executed")
            for m in pending:
                obs_val = (evidence_available or {}).get(m)
                evidence_requests.append({
                    "triggered_by": ev["event_id"],
                    "hold_id": hold_id,
                    "disposition": "HOLD",
                    "requested_measurement": m,
                    "observed_value": obs_val,
                    "unblocks_branch": ev["destination_function"] if obs_val is not None else None,
                })

    return {
        "downstream_actions": downstream_actions,
        "evidence_requests": evidence_requests,
        "rejected_edges": rejected_edges,
        "persisted_holds": persisted_holds,
        "external_exec_blocked_auth0": external_exec_blocked,
    }


def deliver_evidence_and_reactivate(
    hold_store: HoldStore,
    new_evidence: Mapping[str, Any],
    *,
    evaluator: Callable[[dict[str, Any], str, str], list[dict[str, Any]]] | None = None,
    authorized_edges: set[tuple[str, str]] | None = None,
    recorder: CognitiveRecorder | None = None,
) -> dict[str, Any]:
    """Deliver new evidence to HoldStore; reactivate eligible PENDING HOLDs via a real second invocation."""
    if evaluator is None:
        evaluator = lambda obs, eid, pid: evaluate_motor_branches(obs, execution_id=eid, causal_parent_id=pid)

    all_holds = hold_store.list_holds()
    reactivated: list[dict[str, Any]] = []
    still_pending: list[str] = []
    duplicate_blocked: list[str] = []

    for h in all_holds:
        hid = h["hold_id"]
        if h["status"] != "PENDING":
            # Prevent duplicate release / duplicate downstream execution!
            if any(k in new_evidence for k in h["missing_evidence"]):
                duplicate_blocked.append(hid)
                if recorder:
                    recorder.append("VALIDATE", {"stage": "DUPLICATE_HOLD_RELEASE_BLOCKED", "hold_id": hid}, provenance="executed")
            continue

        needed = set(h["missing_evidence"])
        if not needed.issubset(new_evidence.keys()):
            still_pending.append(hid)
            if recorder:
                recorder.append("OBSERVE", {"stage": "HOLD_EVIDENCE_INSUFFICIENT_OR_UNRELATED", "hold_id": hid, "received_keys": sorted(new_evidence.keys())}, provenance="observed")
            continue

        # Merge continuation context + new evidence into updated observation (without mutating original)
        ctx = copy.deepcopy(h["continuation_context"])
        ctx["facts"].update({k: new_evidence[k] for k in needed})
        new_exec_id = f"{h['execution_id']}-RESUME-{h['reactivation_count'] + 1}"

        # ACTUAL SECOND FUNCTION INVOCATION
        new_events = evaluator(ctx, new_exec_id, h["source_event_id"])
        # Filter to resumed electrical/target branch event(s)
        resumed_evs = [e for e in new_events if e["causal_parent_id"] == h["source_event_id"] and e["event_id"].endswith("R")]
        if not resumed_evs:
            resumed_evs = new_events

        forwarded = []
        if authorized_edges is not None:
            for re_ev in resumed_evs:
                if (re_ev["source_function"], re_ev["destination_function"]) in authorized_edges and re_ev["disposition"] == "PASS":
                    forwarded.append(re_ev["event_id"])

        hold_store.update_hold(hid, {
            "status": "REACTIVATED",
            "reactivation_count": h["reactivation_count"] + 1,
            "reactivated_events": resumed_evs,
            "forwarded_event_ids": forwarded,
        })
        if recorder:
            recorder.append("VALIDATE", {
                "stage": "HOLD_REACTIVATED",
                "hold_id": hid,
                "new_execution_id": new_exec_id,
                "causal_parent_id": h["source_event_id"],
                "new_dispositions": [e["disposition"] for e in resumed_evs],
                "forwarded": forwarded,
            }, provenance="executed")

        reactivated.append({
            "hold_id": hid,
            "new_execution_id": new_exec_id,
            "causal_parent_id": h["source_event_id"],
            "events": resumed_evs,
            "forwarded": forwarded,
        })

    return {
        "reactivated": reactivated,
        "still_pending": still_pending,
        "duplicate_blocked": duplicate_blocked,
    }


def explain_disposition_pathway(
    events: list[dict[str, Any]],
    propagation: dict[str, Any],
    reactivation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a structured 49-Brains disposition pathway explanation from actual events."""
    functions_executed = sorted({e["function_id"] for e in events})
    path = [
        {
            "event_id": e["event_id"],
            "function_id": e["function_id"],
            "destination": e["destination_function"],
            "disposition": e["disposition"],
            "reason_code": e["reason_code"],
            "missing_evidence": e["missing_evidence"],
            "payload_forwarded": e["payload_forwarded"],
            "causal_parent_id": e["causal_parent_id"],
        }
        for e in events
    ]
    if reactivation:
        for r in reactivation.get("reactivated", []):
            for e in r["events"]:
                if e["function_id"] not in functions_executed:
                    functions_executed.append(e["function_id"])
                path.append({
                    "event_id": e["event_id"],
                    "function_id": e["function_id"],
                    "destination": e["destination_function"],
                    "disposition": e["disposition"],
                    "reason_code": e["reason_code"],
                    "missing_evidence": e["missing_evidence"],
                    "payload_forwarded": e["payload_forwarded"],
                    "causal_parent_id": e["causal_parent_id"],
                })
    return {
        "schema": "muse-disposition-explanation/1.0",
        "functions_executed": sorted(set(functions_executed)),
        "path": path,
        "downstream_actions": propagation.get("downstream_actions", []),
        "evidence_requests": propagation.get("evidence_requests", []),
        "rejected_edges": propagation.get("rejected_edges", []),
        "persisted_holds": propagation.get("persisted_holds", []),
        "reactivation_summary": reactivation,
    }
