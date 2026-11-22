"""Cross-Specialty V4.1: exact conflict provenance, constrained specialist claims, and equivalent Arm A baseline."""
from __future__ import annotations

from typing import Any

from .authority import require_auth0
from .brains_wiring import BrainsWiringRegistry
from .context_envelope import ContextEnvelope, to_plain
from .functions025 import OPERATIONS


def verify_evidence_records(envelope: ContextEnvelope) -> dict[str, Any]:
    """Repair A: Resolve and verify evidence_refs against evidence_store."""
    verified = []
    rejected = []
    values_by_key: dict[str, list[dict[str, Any]]] = {}
    for ref in envelope.evidence_refs:
        sid = ref.get("source_id", "")
        vstat = ref.get("verification_status")
        if not isinstance(sid, str) or not sid.strip():
            rejected.append({"ref": to_plain(ref), "reason": "empty_source_id"})
            continue
        if vstat != "VERIFIED":
            rejected.append({"source_id": sid, "reason": f"invalid_verification_status:{vstat}"})
            continue
        if sid not in envelope.evidence_store:
            rejected.append({"source_id": sid, "reason": "unresolved_source_id"})
            continue
        stored = envelope.evidence_store[sid]
        if stored.get("missing") or stored.get("disputed") or stored.get("uncertain"):
            rejected.append({"source_id": sid, "reason": "stored_record_missing_disputed_or_uncertain"})
            continue
        if ref.get("key") != stored.get("key") or ref.get("value") != stored.get("value"):
            rejected.append({"source_id": sid, "reason": "proposition_mismatch_with_stored_record"})
            continue
        rec = {
            "source_id": sid,
            "discipline": stored.get("discipline", "general"),
            "key": stored["key"],
            "value": stored["value"],
        }
        verified.append(rec)
        values_by_key.setdefault(rec["key"], []).append(rec)

    conflicts = []
    for key, recs in values_by_key.items():
        distinct_vals = {r["value"] for r in recs}
        if len(distinct_vals) > 1:
            conflicts.append({
                "key": key,
                "records": recs,
                "source_ids": [r["source_id"] for r in recs],
                "propositions": [f"{key}={r['value']}" for r in recs],
                "preserved": True,
            })
    return {"verified": verified, "rejected": rejected, "conflicts": conflicts}


def associate_L33(envelope: ContextEnvelope, target_specialty: str) -> dict[str, Any]:
    orig = envelope.originating_specialty.lower()
    targ = target_specialty.lower()
    if orig == "legal" and targ == "medical":
        return {
            "function_id": "L33",
            "layer": "LOWER_49",
            "relevant": True,
            "bounded_question": "What do verified clinical findings indicate within the supplied records, and what remains unresolved?",
            "permitted_scope": ["physiological_finding", "diagnostic_uncertainty", "conflict_preservation"],
            "excluded_scope": ["legal_negligence_determination", "civil_liability_verdict"],
        }
    if orig == "medical" and targ == "legal":
        return {
            "function_id": "L33",
            "layer": "LOWER_49",
            "relevant": True,
            "bounded_question": "What documentation or disclosure considerations are raised by the supplied verified records?",
            "permitted_scope": ["disclosure_obligation", "documentation_duty", "statutory_uncertainty"],
            "excluded_scope": ["clinical_diagnosis", "medical_treatment_plan"],
        }
    return {
        "function_id": "L33",
        "layer": "LOWER_49",
        "relevant": False,
        "bounded_question": envelope.originating_question,
        "permitted_scope": [],
        "excluded_scope": [],
    }


def associate_audit_U32(envelope: ContextEnvelope, l33_out: dict[str, Any], target_specialty: str, ev_check: dict[str, Any]) -> dict[str, Any]:
    scope_disjoint = not (set(l33_out.get("permitted_scope", [])) & set(l33_out.get("excluded_scope", [])))
    cross_discipline = envelope.originating_specialty.lower() != target_specialty.lower()
    circular = target_specialty in envelope.visited_specialties
    counterpart_available = target_specialty.lower() in ("medical", "legal")
    approved = bool(l33_out.get("relevant")) and scope_disjoint and cross_discipline and not circular and counterpart_available
    return {
        "function_id": "U32",
        "layer": "HIGHER_49",
        "approved": approved,
        "circular_detected": circular,
        "counterpart_available": counterpart_available,
        "scope_disjoint": scope_disjoint,
        "conflicting_keys": [c["key"] for c in ev_check["conflicts"]],
    }


def medical_specialist(
    envelope: ContextEnvelope,
    ev_check: dict[str, Any],
    association: dict[str, Any] | None,
    *,
    force_drift: bool = False,
    override_citations: list[str] | None = None,
    drop_conflict_provenance: bool = False,
    tamper_conflict_provenance: bool = False,
) -> dict[str, Any]:
    verified = ev_check["verified"]
    conflicts = ev_check["conflicts"]
    if not verified:
        return {
            "specialty": "Medical",
            "status": "UNSUPPORTED",
            "finding": "No verified clinical evidence in supplied records.",
            "perspective_tag": "medical_only",
            "citations": override_citations if override_citations is not None else [],
            "conflict_records": [],
            "uncertainty": ["missing_verified_evidence"],
        }
    cites = override_citations if override_citations is not None else [r["source_id"] for r in verified]
    if force_drift:
        return {
            "specialty": "Medical",
            "status": "SUPPORTED",
            "finding": "Surgeon is liable for civil negligence.",
            "perspective_tag": "legal_verdict_overreach",
            "citations": cites,
            "conflict_records": [],
            "uncertainty": [],
        }
    if conflicts:
        if drop_conflict_provenance:
            crecs = []
        elif tamper_conflict_provenance:
            crecs = [{"key": c["key"], "source_ids": ["forged:a", "forged:b"], "propositions": ["forged=1", "forged=2"]} for c in conflicts]
        else:
            crecs = conflicts
        return {
            "specialty": "Medical",
            "status": "CONFLICT_PRESERVED",
            "finding": f"Conflicting clinical observations for {[c['key'] for c in conflicts]}; unresolved within supplied records.",
            "perspective_tag": "medical_bounded_for_legal" if (association and association.get("relevant")) else "medical_native_clinical",
            "citations": cites,
            "conflict_records": crecs,
            "uncertainty": [c["key"] for c in conflicts],
        }
    facts = {r["key"]: r["value"] for r in verified}
    if association and association.get("relevant"):
        if facts.get("troponin_i") == "normal" and facts.get("ecg") == "normal":
            finding = "Within the supplied records, troponin_i and ECG are normal; this does not independently rule out myocardial injury across all clinical timelines."
        else:
            finding = f"Within the supplied records ({facts}), biomarker/ECG findings are consistent with physiological stress; legal causation remains unestablished."
        return {
            "specialty": "Medical",
            "status": "BOUNDED_FINDING",
            "finding": finding,
            "perspective_tag": "medical_bounded_for_legal",
            "citations": cites,
            "conflict_records": [],
            "uncertainty": ["clinical_timeline_and_operative_causation_unestablished"],
        }
    return {
        "specialty": "Medical",
        "status": "NATIVE_CLINICAL",
        "finding": f"Clinical review noted for {facts}; further clinical workup required.",
        "perspective_tag": "medical_native_clinical",
        "citations": cites,
        "conflict_records": [],
        "uncertainty": ["incomplete_clinical_context"],
    }


def legal_specialist(
    envelope: ContextEnvelope,
    ev_check: dict[str, Any],
    association: dict[str, Any] | None,
    *,
    force_drift: bool = False,
    override_citations: list[str] | None = None,
    drop_conflict_provenance: bool = False,
) -> dict[str, Any]:
    verified = ev_check["verified"]
    conflicts = ev_check["conflicts"]
    if not verified:
        return {
            "specialty": "Legal",
            "status": "UNSUPPORTED",
            "finding": "No verified clinical predicate for legal duty evaluation.",
            "perspective_tag": "legal_only",
            "citations": override_citations if override_citations is not None else [],
            "conflict_records": [],
            "uncertainty": ["missing_verified_evidence"],
        }
    cites = override_citations if override_citations is not None else [r["source_id"] for r in verified]
    if force_drift:
        return {
            "specialty": "Legal",
            "status": "SUPPORTED",
            "finding": "Administer anticoagulation immediately.",
            "perspective_tag": "clinical_treatment_overreach",
            "citations": cites,
            "conflict_records": [],
            "uncertainty": [],
        }
    if conflicts:
        crecs = [] if drop_conflict_provenance else conflicts
        return {
            "specialty": "Legal",
            "status": "CONFLICT_PRESERVED",
            "finding": f"Conflicting factual records for {[c['key'] for c in conflicts]}; disclosure scope remains disputed.",
            "perspective_tag": "legal_bounded_for_medical" if (association and association.get("relevant")) else "legal_native_litigation",
            "citations": cites,
            "conflict_records": crecs,
            "uncertainty": [c["key"] for c in conflicts],
        }
    facts = {r["key"]: r["value"] for r in verified}
    if association and association.get("relevant"):
        if facts.get("signed_consent") == "present":
            finding = "A signed consent record is present in the supplied file; full statutory and institutional reporting obligations depend on jurisdiction and clinical context."
        else:
            finding = f"Supplied verified event ({facts}) raises documentation and factual disclosure considerations; full statutory scope depends on jurisdiction."
        return {
            "specialty": "Legal",
            "status": "BOUNDED_FINDING",
            "finding": finding,
            "perspective_tag": "legal_bounded_for_medical",
            "citations": cites,
            "conflict_records": [],
            "uncertainty": ["jurisdiction_specific_statutory_scope"],
        }
    return {
        "specialty": "Legal",
        "status": "NATIVE_LEGAL",
        "finding": f"Evaluate civil standard-of-care exposure for {facts}.",
        "perspective_tag": "legal_native_litigation",
        "citations": cites,
        "conflict_records": [],
        "uncertainty": ["jurisdiction_specific_statutory_scope"],
    }


def coherence_L34(envelope: ContextEnvelope, association: dict[str, Any] | None, specialist_out: dict[str, Any]) -> dict[str, Any]:
    tag = specialist_out.get("perspective_tag", "")
    orig = envelope.originating_specialty.lower()
    perspective_preserved = (
        (orig == "legal" and tag == "medical_bounded_for_legal")
        or (orig == "medical" and tag == "legal_bounded_for_medical")
    )
    return {
        "function_id": "L34",
        "layer": "LOWER_49",
        "checks": {
            "perspective_preserved": perspective_preserved,
            "relevance_established": bool(association and association.get("relevant")),
            "uncertainty_preserved": bool(specialist_out.get("uncertainty")),
        },
    }


def coherence_audit_U33(
    envelope: ContextEnvelope,
    ev_check: dict[str, Any],
    l34_out: dict[str, Any],
    specialist_out: dict[str, Any],
    target_specialty: str,
) -> dict[str, Any]:
    """Repair B & C: Grounded citations, discipline check, perspective check, exact conflict provenance."""
    verified_ids = {r["source_id"] for r in ev_check["verified"]}
    verified_disciplines = {r["source_id"]: r["discipline"] for r in ev_check["verified"]}
    cites = list(specialist_out.get("citations", []))
    nonempty_cites = len(cites) > 0
    all_cites_resolved = nonempty_cites and all(cid in verified_ids for cid in cites)
    wrong_discipline = [
        cid for cid in cites
        if cid in verified_disciplines and verified_disciplines[cid] not in ("general", target_specialty.lower())
    ]
    citations_grounded = all_cites_resolved and not wrong_discipline

    # Exact Repair C: Compare retained conflict source_ids and propositions against expected conflicts
    expected_conflicts = ev_check["conflicts"]
    retained_conflicts = specialist_out.get("conflict_records", [])
    conflict_provenance_intact = True
    if expected_conflicts:
        ret_by_key = {c.get("key"): c for c in retained_conflicts}
        for exp in expected_conflicts:
            ret = ret_by_key.get(exp["key"])
            if not ret:
                conflict_provenance_intact = False
                break
            exp_pairs = sorted((r["source_id"], f"{r['key']}={r['value']}") for r in exp.get("records", []))
            ret_pairs = sorted((r.get("source_id"), f"{r.get('key')}={r.get('value')}") for r in ret.get("records", []))
            if not exp_pairs or ret_pairs != exp_pairs or set(ret.get("source_ids", [])) != set(exp["source_ids"]):
                conflict_provenance_intact = False
                break

    perspective_ok = bool(l34_out["checks"]["perspective_preserved"])
    if not ev_check["verified"] or specialist_out.get("status") == "UNSUPPORTED" or not citations_grounded:
        status = "UNSUPPORTED_OR_UNGROUNDED"
    elif expected_conflicts and not conflict_provenance_intact:
        status = "CONFLICT_PROVENANCE_LOST"
    elif not perspective_ok:
        status = "PERSPECTIVE_DRIFT"
    elif expected_conflicts:
        status = "CONFLICT_PRESERVED"
    else:
        status = "COHERENT"

    return {
        "function_id": "U33",
        "layer": "HIGHER_49",
        "coherence_status": status,
        "dimensions": {
            "relevance": l34_out["checks"]["relevance_established"],
            "coherence": perspective_ok,
            "evidentiary_support": bool(ev_check["verified"]) and citations_grounded,
            "domain_competence": specialist_out.get("specialty", "").lower() == target_specialty.lower(),
            "factual_correctness": "BOUNDED_TO_VERIFIED_STORE",
        },
        "citations_grounded": citations_grounded,
        "wrong_discipline_citations": wrong_discipline,
        "conflict_provenance_intact": conflict_provenance_intact,
    }


CROSS_OPERATIONS = {
    **OPERATIONS,
    "L33": lambda env, target: associate_L33(env, target),
    "U32": lambda env, l33_out, target, ev_check: associate_audit_U32(env, l33_out, target, ev_check),
    "L34": lambda env, assoc, out: coherence_L34(env, assoc, out),
    "U33": lambda env, ev_check, l34_out, out, target: coherence_audit_U33(env, ev_check, l34_out, out, target),
}


def run_consultation(
    envelope: ContextEnvelope,
    target_specialty: str,
    *,
    use_association: bool = True,
    use_coherence: bool = True,
    bypass_intermediaries: bool = False,
    force_drift: bool = False,
    override_citations: list[str] | None = None,
    drop_conflict_provenance: bool = False,
    tamper_conflict_provenance: bool = False,
) -> ContextEnvelope:
    require_auth0(envelope.authority)
    ev_check = verify_evidence_records(envelope)

    if not envelope.feature_enabled:
        # Equivalent Arm A baseline: same evidence + direct specialist call without L33/U32 or L34/U33
        if target_specialty.lower() == "medical":
            spec_out = medical_specialist(envelope, ev_check, None)
        elif target_specialty.lower() == "legal":
            spec_out = legal_specialist(envelope, ev_check, None)
        else:
            return envelope.with_step(coherence_status="COUNTERPART_UNAVAILABLE")
        return envelope.with_step(
            current_specialty=target_specialty,
            interpretation=spec_out,
            coherence_status="BASELINE_DIRECT_SPECIALIST",
            trace_entry={"function_id": f"BASELINE_SPEC:{target_specialty}", "output": spec_out},
        )

    if bypass_intermediaries and envelope.required_intermediaries and (not use_association or not use_coherence):
        return envelope.with_step(
            coherence_status="INTERMEDIARY_BYPASS_REJECTED",
            trace_entry={"function_id": "ROUTER_GATE", "output": {"rejected": "mandatory_intermediary_bypass"}},
        )

    reg = BrainsWiringRegistry(operations=CROSS_OPERATIONS)
    auth_payload = {"authority": envelope.authority, "task_id": envelope.investigation_id}

    assoc = None
    if use_association:
        assoc = reg.execute_operation("L33", auth_payload, envelope, target_specialty)
        envelope = envelope.with_step(trace_entry={"function_id": "L33", "output": assoc})
        u32_out = reg.execute_operation("U32", auth_payload, envelope, assoc, target_specialty, ev_check)
        envelope = envelope.with_step(trace_entry={"function_id": "U32", "output": u32_out})
        if not u32_out["approved"]:
            reason = (
                "CIRCULAR_ROUTE_REJECTED" if u32_out["circular_detected"]
                else "COUNTERPART_UNAVAILABLE" if not u32_out["counterpart_available"]
                else "ASSOCIATION_REJECTED"
            )
            return envelope.with_step(coherence_status=reason)

    if target_specialty.lower() == "medical":
        spec_out = medical_specialist(
            envelope, ev_check, assoc,
            force_drift=force_drift, override_citations=override_citations,
            drop_conflict_provenance=drop_conflict_provenance,
            tamper_conflict_provenance=tamper_conflict_provenance,
        )
    elif target_specialty.lower() == "legal":
        spec_out = legal_specialist(
            envelope, ev_check, assoc,
            force_drift=force_drift, override_citations=override_citations,
            drop_conflict_provenance=drop_conflict_provenance,
        )
    else:
        return envelope.with_step(coherence_status="COUNTERPART_UNAVAILABLE")

    envelope = envelope.with_step(
        current_specialty=target_specialty,
        interpretation=spec_out,
        visited_specialty=target_specialty,
        trace_entry={"function_id": f"SPEC:{target_specialty}", "output": spec_out},
    )

    if use_coherence:
        l34_out = reg.execute_operation("L34", auth_payload, envelope, assoc, spec_out)
        envelope = envelope.with_step(trace_entry={"function_id": "L34", "output": l34_out})
        u33_out = reg.execute_operation("U33", auth_payload, envelope, ev_check, l34_out, spec_out, target_specialty)
        envelope = envelope.with_step(
            current_specialty=envelope.originating_specialty,
            coherence_status=u33_out["coherence_status"],
            trace_entry={"function_id": "U33", "output": u33_out},
        )
    else:
        envelope = envelope.with_step(coherence_status="UNCHECKED")

    return envelope
