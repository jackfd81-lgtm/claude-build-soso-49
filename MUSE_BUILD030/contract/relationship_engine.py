"""Standalone TEST 020 relationship construction and transfer engine.

This module is independent of RealCog. It operates only on structured
observations supplied by the caller and records standalone engine operations.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Any, Iterable, Mapping

from contract.provenance import stored_record_trust_defects

QUALIFICATIONS = {"VALID", "PARTIALLY_VALID", "INVALID", "INSUFFICIENT_EVIDENCE"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _id(material: Any) -> str:
    return "rel-" + hashlib.sha256(_canonical(material).encode()).hexdigest()[:16]


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass
class RelationshipCandidate:
    relationship_id: str
    structure: dict[str, Any]
    evidence_ids: list[str]
    provenance: list[str]
    confidence: float = 0.0
    qualification: str = "INSUFFICIENT_EVIDENCE"
    applicability: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RelationshipEngine:
    """Construct, qualify, and apply competing structured relationships."""

    def __init__(self, recorder=None, disabled_functions: Iterable[str] = (), *,
                 enforce_provenance: bool = True):
        self.enforce_provenance = enforce_provenance   # False = ablation control only
        self.recorder = recorder
        self.disabled_functions = set(disabled_functions)
        self.operations: list[dict[str, Any]] = []

    def _event(self, kind: str, payload: Mapping[str, Any], provenance: str = "executed"):
        event = {"engine": "standalone-relationship-engine", **dict(payload)}
        self.operations.append({"kind": kind, **event})
        if self.recorder:
            self.recorder.append(kind, event, provenance=provenance)

    @staticmethod
    def _obs_id(obs: Mapping[str, Any], index: int) -> str:
        return str(obs.get("observation_id") or f"obs-{index:03d}")

    @staticmethod
    def _pairs(observations: list[Mapping[str, Any]]):
        for index, obs in enumerate(observations):
            inp, out = obs.get("input", {}), obs.get("output", {})
            for input_key, input_value in inp.items():
                for output_key, output_value in out.items():
                    yield index, input_key, input_value, output_key, output_value

    def construct(self, observations: Iterable[Mapping[str, Any]]) -> list[RelationshipCandidate]:
        observations = [dict(o) for o in observations]
        if "L24" in self.disabled_functions:
            self._event("OBSERVE", {"operation": "construct", "status": "disabled"})
            return []
        candidates: dict[str, RelationshipCandidate] = {}
        grouped: dict[tuple[str, str], list[tuple[int, Any, Any]]] = {}
        for index, ik, iv, ok, ov in self._pairs(observations):
            grouped.setdefault((ik, ok), []).append((index, iv, ov))
        for (ik, ok), values in sorted(grouped.items()):
            base = values[0]
            specs: list[dict[str, Any]] = [{"kind": "copy", "input_key": ik, "output_key": ok}]
            if all(_num(iv) and _num(ov) for _, iv, ov in values):
                delta = base[2] - base[1]
                ratio = (base[2] / base[1]) if base[1] != 0 else None
                if all(ov - iv == delta for _, iv, ov in values):
                    specs.append({"kind": "add", "input_key": ik, "output_key": ok, "delta": delta})
                if ratio is not None and all(ov == iv * ratio for _, iv, ov in values):
                    specs.append({"kind": "multiply", "input_key": ik, "output_key": ok, "factor": ratio})
                if all(ov == -iv for _, iv, ov in values):
                    specs.append({"kind": "negate", "input_key": ik, "output_key": ok})
            for structure in specs:
                rid = _id(structure)
                candidates[rid] = RelationshipCandidate(
                    rid, structure, [self._obs_id(observations[i], i) for i, _, _ in values],
                    [str(observations[i].get("source", "unknown")) for i, _, _ in values],
                    confidence=0.0,
                    applicability={"input_keys": [ik], "output_keys": [ok]},
                )
        result = list(candidates.values())
        self._event("OBSERVE", {"operation": "construct", "candidate_ids": [c.relationship_id for c in result],
                                 "candidate_count": len(result)})
        return result

    def _apply_structure(self, structure: Mapping[str, Any], input_value: Any) -> Any:
        kind = structure["kind"]
        if kind == "copy": return input_value
        if kind == "add": return input_value + structure["delta"]
        if kind == "multiply": return input_value * structure["factor"]
        if kind == "negate": return -input_value
        raise ValueError(f"unsupported relationship kind: {kind}")

    def qualify(self, candidates: Iterable[RelationshipCandidate], validation: Iterable[Mapping[str, Any]],
                *, minimum_support: int = 2) -> list[RelationshipCandidate]:
        validation = [dict(v) for v in validation]
        if "U10" in self.disabled_functions or "U08" in self.disabled_functions or "U09" in self.disabled_functions:
            for c in candidates: c.qualification = "INSUFFICIENT_EVIDENCE"
            self._event("VALIDATE", {"operation": "qualify", "status": "disabled",
                                      "disabled_functions": sorted(self.disabled_functions)})
            return list(candidates)
        qualified = []
        for candidate in candidates:
            structure = candidate.structure
            input_key, output_key = structure["input_key"], structure["output_key"]
            checked = 0; matches = 0; mismatches = 0
            for obs in validation:
                if input_key not in obs.get("input", {}) or output_key not in obs.get("output", {}):
                    continue
                checked += 1
                predicted = self._apply_structure(structure, obs["input"][input_key])
                if predicted == obs["output"][output_key]: matches += 1
                else: mismatches += 1
            if checked < minimum_support:
                state = "INSUFFICIENT_EVIDENCE"
            elif matches == checked and matches >= minimum_support:
                state = "VALID"
            elif matches > 0:
                state = "PARTIALLY_VALID"
            else:
                state = "INVALID"
            candidate.qualification = state
            candidate.confidence = round(matches / checked, 3) if checked else 0.0
            candidate.validation = {"checked": checked, "matches": matches, "mismatches": mismatches}
            self._event("VALIDATE", {"operation": "qualify", "relationship_id": candidate.relationship_id,
                                      "qualification": state, "validation": candidate.validation})
            qualified.append(candidate)
        return qualified

    def retrieve_and_apply(self, relationship: Mapping[str, Any], observation: Mapping[str, Any]) -> dict[str, Any]:
        if "U37" in self.disabled_functions:
            self._event("TRANSFER", {"operation": "apply", "status": "disabled",
                                      "relationship_id": relationship.get("relationship_id")})
            return {"success": False, "status": "DISABLED"}
        if relationship.get("qualification") != "VALID":
            self._event("TRANSFER", {"operation": "apply", "status": "rejected",
                                      "relationship_id": relationship.get("relationship_id")})
            return {"success": False, "status": "NOT_VALID"}
        # MUSE-FUNCTIONAL-009: VALID is a claim, not evidence. Enforced HERE, the
        # single execution boundary shared by the HTTP runtime, the CLI, eval022,
        # the Candidate-002 adapter and the investigation engine.
        defects = stored_record_trust_defects(relationship) if self.enforce_provenance else []
        if defects:
            self._event("TRANSFER", {"operation": "apply", "status": "rejected_untrusted_record",
                                      "relationship_id": relationship.get("relationship_id"),
                                      "reasons": defects})
            return {"success": False, "status": "UNQUALIFIED_PROVENANCE", "reasons": defects}
        structure = relationship["structure"]
        ik, ok = structure["input_key"], structure["output_key"]
        if ik not in observation.get("input", {}):
            return {"success": False, "status": "MISSING_INPUT"}
        predicted = self._apply_structure(structure, observation["input"][ik])
        expected = observation.get("expected_output", {}).get(ok)
        success = expected is None or predicted == expected
        result = {"success": success, "status": "TRANSFERRED" if success else "TRANSFER_MISMATCH",
                  "relationship_id": relationship["relationship_id"], "predicted": {ok: predicted},
                  "expected": {ok: expected} if expected is not None else {}}
        self._event("TRANSFER", {"operation": "apply", **result})
        return result
