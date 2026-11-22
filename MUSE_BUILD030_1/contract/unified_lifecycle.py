"""Unified Runtime Lifecycle & 14-Unit Behavioral Handshake (Directive 013).

Provides a single deterministic startup sequence (READY / DEGRADED / FAILED) and
behavioral I/O assertions for every designated SoSo / RealCog / Suit / Closet unit.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from adapters import cognitive_layer as cl, durable_memory as dm
from brain49_path_explain import explain as explain_49
from contract.cognitive_recorder import CognitiveRecorder
from contract.disposition_engine import (
    HoldStore,
    deliver_evidence_and_reactivate,
    evaluate_motor_branches,
    make_disposition_event,
    propagate_authorized_results,
)
from contract.mac002_adapter import Mac002PathwayAdapter
from contract.pipeline import DEFAULT_DISPOSITION_EDGES, Pipeline, Policy
from contract.registry import REGISTRY
from contract.relationship_engine import RelationshipEngine
from contract.router import CognitiveRouter
from contract.seven_domain_cognition import evaluate_seven_domain_task
from contract.visual_evidence_adapter import ingest_dual_stream
from ph6_brains_2208 import Big49, Block, Small49
from ph6_iouledger import IOULedger
from realcog.brain_bridge_suit_gate import make_bridge, suit_gate_for_big_payload


@dataclass(frozen=True)
class RuntimeConfig:
    auth_level: int = 0
    big49_capacity: int = 1600
    small49_capacity: int = 608
    iou_policy: str = "TERMINAL"
    recording_mode: str = "best_effort"


def _unit_record(
    unit_id: str,
    *,
    mandatory: bool,
    present: bool,
    initialized: bool,
    executable_vs_spec: str,
    capability: str,
    dependency_status: str,
    behavioral_test_passed: bool,
    last_error: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "mandatory": mandatory,
        "present": present,
        "initialized": initialized,
        "executable_vs_spec": executable_vs_spec,
        "capability": capability,
        "dependency_status": dependency_status,
        "behavioral_test_passed": behavioral_test_passed,
        "last_error": last_error,
        "details": details or {},
    }


def run_behavioral_handshake(
    data_dir: Path,
    *,
    inject_fault: str | None = None,
    optional_fault: str | None = None,
) -> dict[str, Any]:
    """Execute 12-step lifecycle startup and behavioral I/O checks across all 14 units."""
    cfg = RuntimeConfig()
    data_dir = Path(data_dir)
    pipeline_dir = data_dir / "pipeline_state"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    trace_dir = data_dir / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)

    units: list[dict[str, Any]] = []

    def _run_unit(unit_id: str, mandatory: bool, executable_vs_spec: str, capability: str, fn) -> None:
        if inject_fault == unit_id or optional_fault == unit_id:
            units.append(_unit_record(
                unit_id,
                mandatory=mandatory,
                present=False if inject_fault == unit_id else True,
                initialized=False,
                executable_vs_spec=executable_vs_spec,
                capability=capability,
                dependency_status="FAULT_INJECTED",
                behavioral_test_passed=False,
                last_error=f"Injected failure on {unit_id}",
            ))
            return
        try:
            details = fn()
            units.append(_unit_record(
                unit_id,
                mandatory=mandatory,
                present=True,
                initialized=True,
                executable_vs_spec=executable_vs_spec,
                capability=capability,
                dependency_status="OK",
                behavioral_test_passed=True,
                last_error=None,
                details=details,
            ))
        except Exception as exc:
            units.append(_unit_record(
                unit_id,
                mandatory=mandatory,
                present=True,
                initialized=False,
                executable_vs_spec=executable_vs_spec,
                capability=capability,
                dependency_status="ERROR",
                behavioral_test_passed=False,
                last_error=f"{type(exc).__name__}: {exc}",
            ))

    # 1. adapters.durable_memory
    def _chk_dm():
        c = sqlite3.connect(":memory:")
        dm.ensure_tables(c)
        d = dm.chain_append(c, "cognitive", {"kind": "HS_PROBE", "v": 1})
        rows = dm.chain_rows(c, "cognitive")
        c.close()
        assert len(rows) == 1 and rows[0]["digest"] == d
        return {"rows_written": 1, "digest": d[:12]}

    _run_unit("adapters.durable_memory", True, "EXECUTABLE", "Durable SQLite hash-chain memory", _chk_dm)

    # 2. adapters.cognitive_layer (learn + select + L24 + disposition gate)
    def _chk_cl():
        c = sqlite3.connect(":memory:")
        dm.ensure_tables(c)
        cl.learn_attempts(c, "hs-c1", ["probe_tag", "hs"], [("P6", "SUCCESS")], validator=lambda *a: True)
        cl.learn_attempts(c, "hs-c2", ["probe_tag", "hs"], [("P6", "SUCCESS")], validator=lambda *a: True)
        sel = cl.select_with_policy(c, frozenset(["probe_tag", "hs"]), policy="LOWER_FIRST")
        gate = cl.apply_disposition_gate(sel, execution_id="hs-cl", obs_id="OBS-HS")
        c.close()
        assert "P6" in sel["procedures"] and gate["event"]["disposition"] == "PASS"
        return {"selected": sel["procedures"], "gate_disposition": gate["event"]["disposition"]}

    _run_unit("adapters.cognitive_layer", True, "EXECUTABLE", "Lower/Higher selection, L24 & disposition gate", _chk_cl)

    # 3. contract.pipeline.Pipeline + L24/U10/U09
    pipe_res_holder: dict[str, Any] = {}
    def _chk_pipe():
        with tempfile.TemporaryDirectory() as td:
            p = Pipeline(td, Policy("v1"), criteria={"HS-PIPE": "P6"}, observations={("HS-PIPE", "P6"): True},
                         soso_modules={"durable_memory": dm, "cognitive_layer": cl})
            try:
                res = p.run({
                    "task_id": "HS-PIPE",
                    "signature": "sig-hs-pipe",
                    "family": "handshake",
                    "tags": ["handshake"],
                    "conditions": {"x": 1},
                    "l24_features": ["handshake", "probe"],
                    "l24_method_records": [{
                        "source_case_id": "HS-SRC",
                        "source_features": ["handshake", "probe"],
                        "method": "hs_method",
                        "discriminator": "x",
                        "candidate_map": {"1": "SOSO-P6"},
                    }],
                })
                assert res["state"] == "RESOLVED" and res["selected"] == "SOSO-P6" and res["trace_certified"]
                pipe_res_holder["res"] = res
                return {"execution_id": res["execution_id"], "selected": res["selected"], "l24_activated": res["l24_report"]["l24_activated"]}
            finally:
                p.close()

    _run_unit("contract.pipeline.Pipeline", True, "EXECUTABLE", "End-to-end Lower/SoSo/Higher/L24 pipeline", _chk_pipe)

    # 4. contract.disposition_engine (AUTH:0 rejection + HOLD -> PASS reactivation)
    def _chk_disp():
        auth_blocked = False
        try:
            make_disposition_event("e1", "U09", "U37", "obs", "PASS", "ok", {}, authority=1)
        except PermissionError:
            auth_blocked = True
        assert auth_blocked
        with tempfile.TemporaryDirectory() as td:
            hs = HoldStore(Path(td) / "holds.json")
            evs = evaluate_motor_branches({"obs_id": "OBS-HS", "facts": {"current": "high", "rotation": 0}}, execution_id="hs-disp")
            prop = propagate_authorized_results(evs, DEFAULT_DISPOSITION_EDGES, observation={"obs_id": "OBS-HS", "facts": {"current": "high", "rotation": 0}}, hold_store=hs)
            assert len(prop["persisted_holds"]) == 1
            react = deliver_evidence_and_reactivate(hs, {"supply_voltage": "0V_open_circuit"}, authorized_edges=DEFAULT_DISPOSITION_EDGES)
            assert react["reactivated"][0]["events"][0]["disposition"] == "PASS"
            return {"auth0_blocked": True, "hold_reactivated": react["reactivated"][0]["hold_id"]}

    _run_unit("contract.disposition_engine", True, "EXECUTABLE", "AUTH:0 strict PASS/DROP/HOLD & reactivation", _chk_disp)

    # 5. contract.seven_domain_cognition (answered + honest UNSUPPORTED)
    def _chk_7d():
        num = evaluate_seven_domain_task({"task_id": "HS-NUM", "cognitive_domain": "number", "text": "2 + 3 * 4"}, execution_id="hs-7d")
        vis = evaluate_seven_domain_task({"task_id": "HS-VIS", "cognitive_domain": "pictures", "text": "describe image"}, execution_id="hs-7d")
        assert num["answer"] == 14 and vis["status"] == "UNSUPPORTED"
        return {"number_answer": num["answer"], "pictures_status": vis["status"]}

    _run_unit("contract.seven_domain_cognition", True, "EXECUTABLE", "7-domain deterministic solver & UNSUPPORTED vision gate", _chk_7d)

    # 6. contract.visual_evidence_adapter (Stream A vs B conflict detection)
    def _chk_vis_adapter():
        ve = ingest_dual_stream(
            {"filename": "hs_dark.jpg", "sha256": "0" * 64, "mean_lum_rec709": 45.0, "exif_available": False},
            {"source": "gemini_multimodal_vision", "lighting_condition": "bright_sunny_daylight"},
            execution_id="hs-vis",
        )
        assert ve["disposition_event"]["disposition"] == "DROP" and ve["native_soso_vision_status"] == "UNSUPPORTED"
        return {"conflict_disposition": ve["disposition_event"]["disposition"], "conflicts": ve["conflicts"]}

    _run_unit("contract.visual_evidence_adapter", True, "EXECUTABLE", "Dual-stream provenance & conflict reconciliation", _chk_vis_adapter)

    # 7. contract.router.CognitiveRouter
    def _chk_router():
        r = CognitiveRouter()
        st = r.route({"task_id": "HS-RTR", "tags": ["linear"]}, profile="general")
        r.observe(st, {"kind": "note", "v": 1})
        r.finish({"task_id": "HS-RTR"}, st, True, "RESOLVED")
        assert st.termination == "RESOLVED"
        return {"steps": len(st.steps), "termination": st.termination}

    _run_unit("contract.router.CognitiveRouter", True, "EXECUTABLE", "Profile-aware cognitive routing", _chk_router)

    # 8. contract.cognitive_recorder.CognitiveRecorder
    def _chk_rec():
        with tempfile.TemporaryDirectory() as td:
            rec = CognitiveRecorder(str(Path(td) / "t.jsonl"), run_id="hs-rec", task_id="HS-REC")
            rec.append("EVALUATE", {"ok": True}, provenance="executed")
            rep = rec.replay()
            dig = rec.digest()
            assert len(rep) == 1 and len(dig) == 64
            return {"events": len(rep), "digest_prefix": dig[:12]}

    _run_unit("contract.cognitive_recorder.CognitiveRecorder", True, "EXECUTABLE", "Deterministic JSONL causal trace & replay", _chk_rec)

    # 9. contract.relationship_engine.RelationshipEngine
    def _chk_rel():
        eng = RelationshipEngine()
        cands = eng.construct([
            {"input": {"value": 1}, "output": {"result": 4}, "source": "fixture:train-1"},
            {"input": {"value": 2}, "output": {"result": 5}, "source": "fixture:train-2"},
        ])
        qual = eng.qualify(cands, [{"input": {"value": 10}, "output": {"result": 13}}, {"input": {"value": 20}, "output": {"result": 23}}])
        valid = [q for q in qual if q.qualification == "VALID"]
        assert len(valid) >= 1
        applied = eng.retrieve_and_apply(valid[0].to_dict(), {"input": {"value": 100}, "expected_output": {"result": 103}})
        assert applied["success"] is True
        return {"qualified_count": len(valid), "applied_success": applied["success"]}

    _run_unit("contract.relationship_engine.RelationshipEngine", True, "EXECUTABLE", "Linear relationship construction, qualification & apply", _chk_rel)

    # 10. realcog.ph6_brains_2208 (Big49 + Small49 real put/get/verify)
    big_populated = 0
    small_populated = 0
    def _chk_brains():
        nonlocal big_populated, small_populated
        with tempfile.TemporaryDirectory() as td:
            big = Big49(str(Path(td) / "big.db"))
            small = Small49(str(Path(td) / "small.db"))
            try:
                blk = Block(
                    record_id="hs-blk-01",
                    owner="L15",
                    bucket="cognitive",
                    content=json.dumps({"relation": "ASSOCIATED_WITH", "causal_basis": "hs_probe"}),
                    relation="ASSOCIATED_WITH",
                    causal_basis="hs_probe",
                    authority=0,
                )
                ptr = big.put(blk, principal="L15")
                got = big.read(ptr, principal="L15")
                big.verify()
                small.verify()
                assert got["record_id"] == "hs-blk-01"
                big_populated = 1
                return {"big_capacity": cfg.big49_capacity, "small_capacity": cfg.small49_capacity, "verified_record": got["record_id"]}
            finally:
                big.close()
                small.close()

    _run_unit("realcog.ph6_brains_2208.Big49_Small49", True, "EXECUTABLE", "1,600 Big49 + 608 Small49 tamper-evident stores", _chk_brains)

    # 11. realcog.ph6_iouledger.IOULedger
    def _chk_iou():
        with tempfile.TemporaryDirectory() as td:
            led = IOULedger(str(Path(td) / "iou.db"), cfg.iou_policy)
            try:
                led.open_iou("iou-hs-1", "verify missing evidence")
                led.check("iou-hs-1", "abc", "abc")
                st = led.state()
                assert st["ious"].get("iou-hs-1") == ("VERIFIED", None)
                return {"iou_state": st["ious"]["iou-hs-1"][0]}
            finally:
                led.close()

    _run_unit("realcog.ph6_iouledger.IOULedger", True, "EXECUTABLE", "Durable IOU obligation ledger", _chk_iou)

    # 12. realcog.brain_bridge_suit_gate (SoSo Suit & Closet boundary)
    def _chk_suit_closet():
        with tempfile.TemporaryDirectory() as td:
            big = Big49(str(Path(td) / "big.db"))
            small = Small49(str(Path(td) / "small.db"))
            try:
                bridge = make_bridge(big, small)
                # Suit gate must allow CAUSES with valid causal_basis and deny CAUSES without basis
                allow_ok = suit_gate_for_big_payload({"record_id": "s1", "relation": "CAUSES", "causal_basis": "bench_obs"})
                deny_res = suit_gate_for_big_payload({"record_id": "s2", "relation": "CAUSES", "causal_basis": "UNDETERMINED"})
                assert allow_ok is True and deny_res is False and bridge is not None
                return {"suit_gate_allowed_valid": True, "suit_gate_denied_undetermined": True, "closet_auth": cfg.auth_level}
            finally:
                big.close()
                small.close()

    _run_unit("realcog.suit_and_closet_gate", True, "EXECUTABLE_SUBSET", "GTML OD-05 Suit causal gate & AUTH:0 Closet boundary", _chk_suit_closet)

    # 13. Optional: contract.mac002_adapter
    def _chk_mac002():
        ad = Mac002PathwayAdapter(enable_screening=True, enable_qualification=True)
        rep = ad.evaluate({"task_id": "HS-M", "tags": ["valve"], "conditions": {}}, [], {})
        assert isinstance(rep, dict)
        return {"report_keys": sorted(rep.keys())}

    _run_unit("contract.mac002_adapter", False, "EXECUTABLE", "MAC-002 adaptive 3-class pathway adapter", _chk_mac002)

    # 14. Optional: brain49_path_explain
    def _chk_b49():
        ex = explain_49({"spans": [
            {"span_id": "s1", "function_id": "L01", "purpose": "interpret"},
            {"span_id": "s2", "function_id": "U09", "purpose": "compare"},
        ]})
        assert len(ex.get("path", [])) == 2
        return {"explained_spans": len(ex["path"])}

    _run_unit("brain49_path_explain", False, "EXECUTABLE", "49-Brains causal path explanation", _chk_b49)

    mandatory_failed = [u["unit_id"] for u in units if u["mandatory"] and not u["behavioral_test_passed"]]
    optional_failed = [u["unit_id"] for u in units if not u["mandatory"] and not u["behavioral_test_passed"]]

    if mandatory_failed:
        lifecycle_state = "FAILED"
        handshake_status = "FAILED"
    elif optional_failed:
        lifecycle_state = "DEGRADED"
        handshake_status = "DEGRADED"
    else:
        lifecycle_state = "READY"
        handshake_status = "ALL_UNITS_COHERENT"

    return {
        "lifecycle_state": lifecycle_state,
        "handshake_status": handshake_status,
        "config": asdict(cfg),
        "registered_cognitive_functions": len(REGISTRY),
        "memory_inventory": {
            "big49_configured_capacity": cfg.big49_capacity,
            "small49_configured_capacity": cfg.small49_capacity,
            "big49_probe_populated": big_populated,
            "small49_probe_populated": small_populated,
            "demonstrated_cognitive_use": any(u["unit_id"] == "contract.pipeline.Pipeline" and u["behavioral_test_passed"] for u in units),
        },
        "mandatory_passed": sum(1 for u in units if u["mandatory"] and u["behavioral_test_passed"]),
        "mandatory_total": sum(1 for u in units if u["mandatory"]),
        "optional_passed": sum(1 for u in units if not u["mandatory"] and u["behavioral_test_passed"]),
        "optional_total": sum(1 for u in units if not u["mandatory"]),
        "mandatory_failed": mandatory_failed,
        "optional_failed": optional_failed,
        "units": units,
        "native_vision_status": "UNSUPPORTED",
    }
