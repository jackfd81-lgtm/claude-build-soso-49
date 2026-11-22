"""Typed opt-in adapter integrating Candidate 002's adaptive 3-class selection,
Light-mode contradiction screening, and strict evidence-reference qualification
into the PATHWAY-002 Pipeline without replacing PATHWAY-002's backbone.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from contract.relationship_engine import RelationshipEngine

from contract.provenance import (  # noqa: F401  (re-exported: public names kept)
    _UNTRUSTED_SOURCES, evidence_id_defects, provenance_defects, stored_record_trust_defects,
)


def _canonical_obs(obs: Mapping[str, Any]) -> str:
    return json.dumps({"input": obs.get("input", {}), "output": obs.get("output", {})}, sort_keys=True, separators=(",", ":"))


def qualify_record_against_observations(
    record: Mapping[str, Any],
    observation_store: Mapping[str, Mapping[str, Any]],
    minimum_distinct_support: int = 2,
) -> tuple[bool, list[str]]:
    """Verify provenance AND resolve evidence_ids to >=2 distinct valid observations."""
    reasons: list[str] = []

    if record.get("qualification") != "VALID":
        reasons.append("historical_qualification_not_valid")

    reasons.extend(provenance_defects(record))

    ev_ids = record.get("evidence_ids")
    if evidence_id_defects(record):
        reasons.append("missing_evidence_ids")
        return False, sorted(set(reasons))

    eng = RelationshipEngine()
    structure = record.get("structure", {})
    ik = structure.get("input_key")
    ok = structure.get("output_key")
    distinct_pairs: set[str] = set()

    for eid in ev_ids:
        obs = observation_store.get(eid)
        if obs is None:
            reasons.append("unresolvable_evidence_id")
            continue
        if obs.get("deleted") or obs.get("stale") or obs.get("superseded"):
            reasons.append("stale_or_deleted_evidence")
            continue
        if ik and ok and ik in obs.get("input", {}) and ok in obs.get("output", {}):
            pred = eng._apply_structure(structure, obs["input"][ik])
            if pred != obs["output"][ok]:
                reasons.append("evidence_observation_contradicted")
                continue
            distinct_pairs.add(_canonical_obs(obs))
        else:
            reasons.append("unrelated_evidence_observation")

    if len(distinct_pairs) < minimum_distinct_support:
        reasons.append("insufficient_distinct_supporting_observations")

    if reasons:
        return False, sorted(set(reasons))
    return True, []


def check_task_applicability(record: Mapping[str, Any], task_ctx: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Check structure, numeric/key constraints, local observations, and domain/tag evidence."""
    reasons: list[str] = []
    structure = record.get("structure", {})
    app = record.get("applicability", {})
    ik = structure.get("input_key")
    ok = structure.get("output_key")
    task_input = task_ctx.get("input", {})

    req_in = set(app.get("input_keys", [ik] if ik else []))
    if not req_in.issubset(task_input.keys()):
        reasons.append("missing_required_input_key")
    elif structure.get("kind") in {"add", "multiply", "negate"}:
        val = task_input.get(ik)
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            reasons.append("non_numeric_input_for_arithmetic_rule")

    local_obs = task_ctx.get("local_observations") or []
    if local_obs and ik and ok:
        eng = RelationshipEngine()
        for obs in local_obs:
            if ik in obs.get("input", {}) and ok in obs.get("output", {}):
                if eng._apply_structure(structure, obs["input"][ik]) != obs["output"][ok]:
                    reasons.append("local_observation_contradicted")
                    break

    hist_tags = set(app.get("tags", []))
    curr_tags = set(task_ctx.get("tags", []))
    rel = app.get("relation_kind")
    if rel and task_ctx.get("relation_kind") and rel != task_ctx["relation_kind"]:
        reasons.append("relation_kind_contradicted")
    if hist_tags and curr_tags and not hist_tags.intersection(curr_tags):
        reasons.append("tags_no_overlap")

    if reasons:
        return "CONTRADICTED", sorted(set(reasons))
    if not rel or not task_ctx.get("relation_kind") or not curr_tags or not hist_tags:
        return "UNRESOLVED", ["missing_applicability_evidence"]
    return "SUPPORTED", []


class Mac002PathwayAdapter:
    """Opt-in memory selection & evidence-qualification adapter for PATHWAY-002 Pipeline."""

    def __init__(
        self,
        *,
        enable_screening: bool = True,
        enable_qualification: bool = True,
        minimum_distinct_support: int = 2,
        enforce_provenance: bool = True,
    ) -> None:
        # enforce_provenance=False is an ABLATION control only (it disables the
        # shared apply-boundary gate in RelationshipEngine so the adapter's own
        # screening / qualification gates can still be measured in isolation).
        self.enforce_provenance = enforce_provenance
        self.enable_screening = enable_screening
        self.enable_qualification = enable_qualification
        self.minimum_distinct_support = minimum_distinct_support

    def select_initial_class(self, task_ctx: Mapping[str, Any], retrieved_count: int) -> int:
        if task_ctx.get("consequential") or retrieved_count == 0:
            return 1
        has_rel = bool(task_ctx.get("relation_kind"))
        has_tags = bool(task_ctx.get("tags"))
        if has_rel and has_tags:
            return 3
        if has_rel or has_tags:
            return 2
        return 1

    def evaluate(
        self,
        task_ctx: Mapping[str, Any],
        records: list[dict[str, Any]],
        observation_store: Mapping[str, Mapping[str, Any]],
        recorder: Any = None,
        evidence_provider: Callable[[str, list[str]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        eng = RelationshipEngine(recorder=recorder, enforce_provenance=self.enforce_provenance)
        ordered = sorted(records, key=lambda r: float(r.get("confidence", 0.0)), reverse=True)
        initial_class = self.select_initial_class(task_ctx, len(ordered))
        active_class = initial_class
        escalations: list[dict[str, Any]] = []
        work_ops = 1
        candidates: list[dict[str, Any]] = []

        if recorder:
            recorder.append("OBSERVE", {"phase": "pre_check", "class": initial_class, "task_id": task_ctx.get("case_id") or task_ctx.get("task_id")}, provenance="observed")

        for idx, rec in enumerate(ordered):
            work_ops += 1
            reasons: list[str] = []
            if self.enable_qualification:
                q_ok, q_reasons = qualify_record_against_observations(rec, observation_store, self.minimum_distinct_support)
                if not q_ok:
                    reasons.extend(q_reasons)
            app_status, app_reasons = check_task_applicability(rec, task_ctx)
            reasons.extend(app_reasons)

            if reasons:
                status = "UNRESOLVED" if (not any("contradicted" in r or "missing_provenance" in r or "malformed" in r or "fabricated" in r or "unresolvable" in r or "stale" in r or "insufficient" in r or "not_valid" in r or "unrelated" in r or "missing_required" in r or "non_numeric" in r for r in reasons)) else "CONTRADICTED"
                if app_status == "UNRESOLVED" and not [r for r in reasons if r != "missing_applicability_evidence"]:
                    status = "UNRESOLVED"
            else:
                status = "SUPPORTED"

            candidates.append({
                "relationship_id": rec["relationship_id"],
                "status": status,
                "reasons": sorted(set(reasons)),
                "_record": rec,
            })

            if active_class == 3 and status != "SUPPORTED":
                active_class = 1 if status == "UNRESOLVED" else 2
                esc = {"from_class": 3, "to_class": active_class, "reason": f"primary_{status.lower()}"}
                escalations.append(esc)
                if recorder:
                    recorder.append("SELECT", {"escalation": esc}, provenance="executed")
            elif active_class == 3 and status == "SUPPORTED" and idx == 0:
                if self.enable_screening:
                    conflict = False
                    p_in = rec.get("structure", {}).get("input_key")
                    p_out = rec.get("structure", {}).get("output_key")
                    for alt in ordered[1:]:
                        work_ops += 1
                        alt_q_ok = True
                        if self.enable_qualification:
                            alt_q_ok, _ = qualify_record_against_observations(alt, observation_store, self.minimum_distinct_support)
                        alt_status, _ = check_task_applicability(alt, task_ctx)
                        if alt_q_ok and alt_status == "SUPPORTED":
                            conflict = True
                            esc = {"from_class": 3, "to_class": 1, "reason": f"competing_supported:{alt['relationship_id']}"}
                            escalations.append(esc)
                            if recorder:
                                recorder.append("SELECT", {"escalation": esc}, provenance="executed")
                            active_class = 1
                            break
                        if p_in == alt.get("structure", {}).get("input_key") and p_out == alt.get("structure", {}).get("output_key") and alt_status == "UNRESOLVED":
                            conflict = True
                            esc = {"from_class": 3, "to_class": 2, "reason": f"unresolved_same_io:{alt['relationship_id']}"}
                            escalations.append(esc)
                            if recorder:
                                recorder.append("SELECT", {"escalation": esc}, provenance="executed")
                            active_class = 2
                            break
                    if not conflict:
                        break
                else:
                    break

        supported = [c for c in candidates if c["status"] == "SUPPORTED"]
        unresolved = [c for c in candidates if c["status"] == "UNRESOLVED"]

        if len(supported) > 1 and active_class > 1:
            esc = {"from_class": active_class, "to_class": 1, "reason": "multiple_supported_candidates"}
            escalations.append(esc)
            if recorder:
                recorder.append("SELECT", {"escalation": esc}, provenance="executed")
            active_class = 1

        disposition = "ABSTAIN"
        prediction = None
        selected_id = None

        if len(supported) == 1:
            chosen = supported[0]["_record"]
            res = eng.retrieve_and_apply(chosen, {"input": task_ctx["input"]})
            work_ops += 1
            if res.get("success"):
                disposition = "RESOLVE"
                prediction = res["predicted"][chosen["structure"]["output_key"]]
                selected_id = chosen["relationship_id"]
        elif not supported and unresolved and evidence_provider is not None:
            req = [k for k in ("relation_kind", "tags") if not task_ctx.get(k)]
            if req:
                if active_class > 1:
                    esc = {"from_class": active_class, "to_class": 1, "reason": "unresolved_requires_evidence"}
                    escalations.append(esc)
                    if recorder:
                        recorder.append("SELECT", {"escalation": esc}, provenance="executed")
                    active_class = 1
                work_ops += len(req)
                ev = evidence_provider(task_ctx.get("case_id") or task_ctx.get("task_id") or "", req) or {}
                if ev:
                    updated = {**task_ctx, **ev}
                    re_sup = []
                    for c in candidates:
                        q_ok = True
                        if self.enable_qualification:
                            q_ok, _ = qualify_record_against_observations(c["_record"], observation_store, self.minimum_distinct_support)
                        st2, _ = check_task_applicability(c["_record"], updated)
                        if q_ok and st2 == "SUPPORTED":
                            c["status"] = "SUPPORTED"
                            re_sup.append(c)
                        work_ops += 1
                    if len(re_sup) == 1:
                        chosen = re_sup[0]["_record"]
                        res = eng.retrieve_and_apply(chosen, {"input": updated["input"]})
                        work_ops += 1
                        if res.get("success"):
                            disposition = "RESOLVE"
                            prediction = res["predicted"][chosen["structure"]["output_key"]]
                            selected_id = chosen["relationship_id"]

        if recorder:
            recorder.append("OBSERVE", {"phase": "post_check", "disposition": disposition, "remaining_uncertainty": disposition == "ABSTAIN"}, provenance="observed")

        return {
            "initial_class": initial_class,
            "final_class": active_class,
            "escalations": escalations,
            "disposition": disposition,
            "prediction": prediction,
            "selected_relationship_id": selected_id,
            "work_ops": work_ops,
            "candidates": [{k: v for k, v in c.items() if k != "_record"} for c in candidates],
        }
