"""
ph6_soso_suit_monitor_v3_2_2.py
PH6 / SoSo v3.2.2 Deterministic Reference Monitor & Protocol Gate.
Authority: AUTH:0 / Advisory Research Architecture.

Delta from v3.2.1 (three-layer defense):
  1. Ingress Adapter Table & Hash — hard authority terms are rewritten to soft
     descriptive terms BEFORE the AI sees them; the mapping table is versioned
     and its hash is bound into the context commitment (ingress_adapter_hash).
  2. Closed-Vocabulary Registries — free-text fields in the candidate schema
     are replaced with enum registries: ActionID, ObservationCode,
     ConfidenceEnum, IOUCode.
  3. Reference Monitor Update — the substring-based authority scan is replaced
     with strict enum set-membership checks (a word-boundary backstop remains
     only for the surviving free-text fields); MAX_TEXT_CHARS is enforced on
     all remaining text fields; EvidenceStore validates domain_tag and schema
     compliance.
  4. IOU Ratio Control Loop — R_IOU = N_IOU / (N_Obs + N_IOU) is computed and
     recorded in the output envelope to track affordance starvation vs model
     gaming of the escape hatch.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

PROTOCOL_VERSION = "soso-suit/3.2.2"
INGRESS_ADAPTER_VERSION = "ingress-adapter/1.0"
HEX_256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

MAX_TEXT_CHARS = 4096
MAX_ITEMS = 64

# IOU ratio control-loop bands (advisory thresholds, not gate dispositions).
IOU_RATIO_NOMINAL_MAX = 0.25
IOU_RATIO_ELEVATED_MAX = 0.50


# ---------------------------------------------------------------------------
# 1. RFC 8785 (JCS) Cross-Platform Canonical Serialization (unchanged)
# ---------------------------------------------------------------------------

def normalize_string(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected string primitive")
    return unicodedata.normalize("NFC", value)


def normalize_for_canonical_digest(value: Any) -> Any:
    """Enforces deterministic canonical types. Floats strictly prohibited."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        raise TypeError("Floating-point values prohibited in canonical Suit payloads; use integer scaling.")
    if isinstance(value, str):
        return normalize_string(value)
    if isinstance(value, list):
        return [normalize_for_canonical_digest(item) for item in value]
    if isinstance(value, dict):
        normalized: Dict[str, Any] = {}
        for k, v in value.items():
            norm_k = normalize_string(k)
            if norm_k in normalized:
                raise ValueError(f"Duplicate normalized key: {norm_k}")
            normalized[norm_k] = normalize_for_canonical_digest(v)
        return normalized
    raise TypeError(f"Unsupported payload type: {type(value).__name__}")


def canonical_jcs_bytes(value: Any) -> bytes:
    """RFC 8785 compliant canonical serializer (UTF-16-BE key ordering)."""
    normalized = normalize_for_canonical_digest(value)

    def serialize(obj: Any) -> bytes:
        if obj is None:
            return b"null"
        elif isinstance(obj, bool):
            return b"true" if obj else b"false"
        elif isinstance(obj, int):
            return str(obj).encode("ascii")
        elif isinstance(obj, str):
            return json.dumps(obj, ensure_ascii=False).encode("utf-8")
        elif isinstance(obj, list):
            return b"[" + b",".join(serialize(item) for item in obj) + b"]"
        elif isinstance(obj, dict):
            sorted_keys = sorted(obj.keys(), key=lambda k: k.encode("utf-16-be"))
            members = [
                json.dumps(k, ensure_ascii=False).encode("utf-8") + b":" + serialize(obj[k])
                for k in sorted_keys
            ]
            return b"{" + b",".join(members) + b"}"
        raise TypeError(f"Cannot serialize: {type(obj)}")

    return serialize(normalized)


def canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_jcs_bytes(value)).hexdigest()


# ---------------------------------------------------------------------------
# 2. Ingress Adapter Table & Hash (NEW in v3.2.2)
# ---------------------------------------------------------------------------

# Explicit hard-authority -> soft-descriptive mapping. Applied to ingress data
# BEFORE prompt assembly. The table is versioned; its hash is committed in
# the SuitContextPacket so any mapping change is a new protocol lineage
# ("new protocol -> new hash lineage"), never a silent edit.
INGRESS_ADAPTER_TABLE: Dict[str, str] = {
    "CERTIFIED_PASS": "recorded observation matches rule",
    "CERTIFIED": "recorded observation",
    "CONFIRMED_CAUSE": "temporal correlation noted",
    "CONFIRMED_TRUE": "recorded observation",
    "CANONICAL_TRUTH": "recorded baseline data",
    "VALIDATED": "checked against rule",
    "DROP": "reading outside target window",
    "REJECT": "reading outside target window",
    "AUTHORIZED": "permitted by policy",
    "EXECUTION_APPROVED": "execution not granted",
}

# Whole-word pattern, longest keys first so CERTIFIED_PASS wins over CERTIFIED.
_INGRESS_PATTERN = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in INGRESS_ADAPTER_TABLE),
                             key=len, reverse=True)) + r")\b"
)


def apply_ingress_adapter(text: str) -> str:
    """Rewrite hard authority terms to soft descriptive terms (whole-word)."""
    return _INGRESS_PATTERN.sub(lambda m: INGRESS_ADAPTER_TABLE[m.group(1)], text)


def ingress_adapter_hash() -> str:
    """Canonical hash of the versioned adapter table. Bound into the context
    commitment. The GOLDEN RULE: the original untranslated data is hashed and
    preserved in CRAM-0 first; the AI reads only the sanitized view, while its
    observation references still point at the original raw record hash."""
    return canonical_hash({
        "adapter_version": INGRESS_ADAPTER_VERSION,
        "mappings": INGRESS_ADAPTER_TABLE,
    })


# ---------------------------------------------------------------------------
# 3. Closed-Vocabulary Registries (NEW in v3.2.2)
# ---------------------------------------------------------------------------

class ActionID(str, Enum):
    REQUEST_READING = "REQUEST_READING"
    REQUEST_DISCRIMINATOR = "REQUEST_DISCRIMINATOR"
    LOG_ADVISORY = "LOG_ADVISORY"
    NO_OP = "NO_OP"


class ObservationCode(str, Enum):
    OBS_TEMP_NOMINAL = "OBS_TEMP_NOMINAL"
    OBS_TEMP_HIGH = "OBS_TEMP_HIGH"
    OBS_TEMP_LOW = "OBS_TEMP_LOW"
    OBS_STREAM_NOMINAL = "OBS_STREAM_NOMINAL"
    OBS_STREAM_INTERRUPTED = "OBS_STREAM_INTERRUPTED"
    OBS_MOTION_DETECTED = "OBS_MOTION_DETECTED"
    OBS_MOTION_ABSENT = "OBS_MOTION_ABSENT"
    OBS_SOURCE_MISMATCH = "OBS_SOURCE_MISMATCH"
    OBS_READING_OUTSIDE_WINDOW = "OBS_READING_OUTSIDE_WINDOW"
    OBS_UNCERTAIN = "OBS_UNCERTAIN"


class ConfidenceEnum(str, Enum):
    UNQUANTIFIED = "UNQUANTIFIED"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class IOUCode(str, Enum):
    IOU_UNMAPPED_OBSERVATION = "IOU_UNMAPPED_OBSERVATION"
    IOU_MISSING_DISCRIMINATOR = "IOU_MISSING_DISCRIMINATOR"
    IOU_SOURCE_UNAVAILABLE = "IOU_SOURCE_UNAVAILABLE"
    IOU_AMBIGUOUS_READING = "IOU_AMBIGUOUS_READING"


# ---------------------------------------------------------------------------
# 4. Evidence Store with domain_tag + schema compliance (UPDATED in v3.2.2)
# ---------------------------------------------------------------------------

ALLOWED_DOMAIN_TAGS = frozenset({
    "PH6:SUIT_MANIFEST:v1",
    "PH6:SUIT_CFG:v1",
    "PH6:RUN_RECORD:v1",
    "PH6:CORPUS:v1",
    "PH6:RUNTIME:v1",
    "PH6:CONTAMINATION_REPORT:v1",
    "PH6:SCORING_ARTIFACT:v1",
    "PH6:MECHANISM_EVIDENCE:v1",
    "PH6:VERIFIED_COMPARISON:v1",
})
SCHEMA_VERSION_RE = re.compile(r"^v[0-9]+(\.[0-9]+)*$")


@dataclass(frozen=True)
class PreservedEvidenceArtifact:
    evidence_hash: str
    domain_tag: str
    raw_payload_bytes: bytes
    schema_version: str = "v1"

    def verify_integrity(self) -> bool:
        computed = "sha256:" + hashlib.sha256(self.raw_payload_bytes).hexdigest()
        return computed == self.evidence_hash

    def verify_domain(self) -> bool:
        return self.domain_tag in ALLOWED_DOMAIN_TAGS

    def verify_schema(self) -> bool:
        return bool(SCHEMA_VERSION_RE.fullmatch(self.schema_version))


class EvidenceStore:
    """Authority store: existence + digest + domain_tag + schema compliance."""
    def __init__(self) -> None:
        self._store: Dict[str, PreservedEvidenceArtifact] = {}

    def register(self, artifact: PreservedEvidenceArtifact) -> None:
        if not artifact.verify_integrity():
            raise ValueError(f"Integrity check failed for artifact: {artifact.evidence_hash}")
        if not artifact.verify_domain():
            raise ValueError(f"Domain tag not allowed: {artifact.domain_tag}")
        if not artifact.verify_schema():
            raise ValueError(f"Schema version invalid: {artifact.schema_version}")
        self._store[artifact.evidence_hash] = artifact

    def resolve(self, evidence_hash: str) -> Optional[PreservedEvidenceArtifact]:
        artifact = self._store.get(evidence_hash)
        if artifact and artifact.verify_integrity():
            return artifact
        return None


# ---------------------------------------------------------------------------
# 5. Context Commitment with Substrate, Reality Telemetry & Ingress Binding
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SubstrateProfile:
    model_id: str
    runtime_version: str
    temperature_scaled: int  # e.g., 0 for deterministic greedy sampling
    accelerator_profile: str


@dataclass(frozen=True)
class SuitContextPacket:
    task_id: str
    run_nonce: str
    substrate: SubstrateProfile
    context_tokens: List[str]
    allowed_actions: List[str]
    source_hashes: List[str]
    policy_hash: str
    ingress_adapter_hash: str  # NEW in v3.2.2: binds the adapter table version

    def __post_init__(self) -> None:
        if not ID_RE.fullmatch(self.task_id) or not ID_RE.fullmatch(self.run_nonce):
            raise ValueError("Invalid identifier format for task_id or run_nonce")
        if not HEX_256_RE.fullmatch(self.policy_hash):
            raise ValueError("policy_hash must be sha256:<64 hex>")
        if not HEX_256_RE.fullmatch(self.ingress_adapter_hash):
            raise ValueError("ingress_adapter_hash must be sha256:<64 hex>")
        if not self.allowed_actions:
            raise ValueError("allowed_actions cannot be empty")
        for a in self.allowed_actions:
            if a not in {x.value for x in ActionID}:
                raise ValueError(f"allowed_actions must use the ActionID registry: {a}")
        for h in self.source_hashes:
            if not HEX_256_RE.fullmatch(h):
                raise ValueError(f"Invalid source hash: {h}")

    @property
    def context_digest(self) -> str:
        return canonical_hash({
            "protocol_version": PROTOCOL_VERSION,
            "task_id": self.task_id,
            "run_nonce": self.run_nonce,
            "substrate": {
                "model_id": self.substrate.model_id,
                "runtime_version": self.substrate.runtime_version,
                "temperature_scaled": self.substrate.temperature_scaled,
                "accelerator_profile": self.substrate.accelerator_profile,
            },
            "context_tokens": [normalize_string(t) for t in self.context_tokens],
            "allowed_actions": sorted(self.allowed_actions, key=lambda k: k.encode("utf-16-be")),
            "source_hashes": sorted(self.source_hashes),
            "policy_hash": self.policy_hash,
            "ingress_adapter_version": INGRESS_ADAPTER_VERSION,
            "ingress_adapter_hash": self.ingress_adapter_hash,
        })

    def to_closet_prompt(self) -> str:
        actions = ", ".join(sorted(a.value for a in ActionID))
        obs_codes = ", ".join(sorted(o.value for o in ObservationCode))
        iou_codes = ", ".join(sorted(i.value for i in IOUCode))
        return (
            f"=== SOSO SUIT {PROTOCOL_VERSION} [AI CLOSET] ===\n"
            f"Authority Level: AUTH:0 (Advisory Only)\n"
            f"Task ID: {self.task_id}\n"
            f"Context Digest: {self.context_digest}\n"
            f"Ingress Adapter: {INGRESS_ADAPTER_VERSION} ({self.ingress_adapter_hash[:19]}...)\n"
            "Closed vocabularies (use ONLY these tokens):\n"
            f"  Actions: {actions}\n"
            f"  Observation codes: {obs_codes}\n"
            f"  Confidence: UNQUANTIFIED, LOW, MEDIUM, HIGH\n"
            f"  IOU codes: {iou_codes}\n"
            "REQUIREMENTS:\n"
            "1. Output exactly one raw JSON object conforming to the schema.\n"
            "2. Every observation uses an ObservationCode and references a verified Evidence Reference hash.\n"
            "3. Every interpretation declares dependencies on known observation IDs.\n"
            "4. If the codes do not fit what you observe, emit IOU_UNMAPPED_OBSERVATION\n"
            "   and request human or Lane-1 inspection instead of guessing.\n"
            "5. If a physical measurement is needed, request action 'REQUEST_DISCRIMINATOR'.\n"
        )

# ---------------------------------------------------------------------------
# 6. Deterministic Reference Monitor (PEP Gate) — UPDATED in v3.2.2
# ---------------------------------------------------------------------------

def check_text_bounds(text: Any, path: str, failures: List[Dict[str, str]]) -> bool:
    """Enforce MAX_TEXT_CHARS on every surviving free-text field."""
    if not isinstance(text, str):
        failures.append({"code": "SCHEMA_TYPE_ERROR", "detail": f"{path} must be a string"})
        return False
    if len(text) > MAX_TEXT_CHARS:
        failures.append({"code": "TEXT_LIMIT_EXCEEDED",
                         "detail": f"{path} length {len(text)} exceeds {MAX_TEXT_CHARS}"})
        return False
    return True


# Backstop authority scan for surviving free-text fields ONLY. Word-boundary
# matching fixes the v3.2.1 false positive where substring "canon" matched
# "canonical". Enum set-membership is the primary check; this is the backstop.
_BACKSTOP_TERMS = (
    "pass", "drop", "validated", "certified", "canon",
    "confirmed_true", "authorized", "execution_approved",
)
_BACKSTOP_PATTERNS = tuple(re.compile(r"\b" + re.escape(t) + r"\b")
                           for t in _BACKSTOP_TERMS)


def backstop_authority_scan(text: str) -> List[str]:
    lowered = text.casefold()
    return [t for t, p in zip(_BACKSTOP_TERMS, _BACKSTOP_PATTERNS) if p.search(lowered)]


def iou_ratio(n_obs: int, n_iou: int) -> float:
    """R_IOU = N_IOU / (N_Obs + N_IOU). Control-loop signal: high values mean
    the pick-list is starving the model (affordance starvation) or the model
    is gaming the escape hatch."""
    denom = n_obs + n_iou
    return (n_iou / denom) if denom else 0.0


def iou_ratio_milli(ratio: float) -> int:
    """Integer-scaled ratio for canonical payloads (floats are prohibited
    in canonical Suit payloads; use integer scaling)."""
    return int(round(ratio * 1000))


def iou_ratio_band(ratio: float) -> str:
    if ratio < IOU_RATIO_NOMINAL_MAX:
        return "NOMINAL"
    if ratio < IOU_RATIO_ELEVATED_MAX:
        return "ELEVATED"
    return "STARVATION_RISK"


class SoSoSuitReferenceMonitor:
    REQUIRED_ROOT_KEYS = frozenset({
        "protocol_version",
        "task_id",
        "context_digest",
        "action_requested",
        "observations",
        "interpretations",
        "unresolved_ious",
    })

    def __init__(self, context: SuitContextPacket, evidence_store: EvidenceStore):
        self.context = context
        self.evidence_store = evidence_store
        self._allowed_actions = frozenset(context.allowed_actions)
        self._allowed_sources = frozenset(context.source_hashes)

    def evaluate_candidate(self, raw_json_str: str) -> Dict[str, Any]:
        failures: List[Dict[str, str]] = []

        try:
            candidate = self._parse_json(raw_json_str)
        except Exception as err:
            return self._fail_closed([{"code": "JSON_PARSE_ERROR", "detail": str(err)}],
                                     n_obs=0, n_iou=0)

        # Key verification
        keys = frozenset(candidate.keys())
        for missing in sorted(self.REQUIRED_ROOT_KEYS - keys):
            failures.append({"code": "SCHEMA_MISSING_KEY", "detail": missing})
        for unexpected in sorted(keys - self.REQUIRED_ROOT_KEYS):
            failures.append({"code": "SCHEMA_UNEXPECTED_KEY", "detail": unexpected})

        if failures:
            return self._fail_closed(failures, n_obs=0, n_iou=0)

        # Protocol and Context Bindings
        if candidate["protocol_version"] != PROTOCOL_VERSION:
            failures.append({"code": "PROTOCOL_VERSION_MISMATCH",
                             "detail": str(candidate["protocol_version"])})
        if candidate["task_id"] != self.context.task_id:
            failures.append({"code": "TASK_BINDING_MISMATCH",
                             "detail": str(candidate["task_id"])})
        if candidate["context_digest"] != self.context.context_digest:
            failures.append({"code": "CONTEXT_DIGEST_MISMATCH",
                             "detail": str(candidate["context_digest"])})

        # Action Legality — strict ActionID registry membership
        action = candidate["action_requested"]
        if action not in {a.value for a in ActionID} or action not in self._allowed_actions:
            failures.append({"code": "ILLEGAL_ACTION_REQUEST", "detail": str(action)})

        # Referential Structure Checks (closed vocabularies)
        n_obs = self._verify_observations(candidate["observations"], failures)
        self._verify_interpretations(candidate["interpretations"],
                                     candidate["observations"], failures)
        n_iou = self._verify_ious(candidate["unresolved_ious"], action, failures)

        if failures:
            return self._fail_closed(failures, n_obs=n_obs, n_iou=n_iou)

        # Advisory Envelope Generation (with IOU control-loop signal)
        ratio = iou_ratio(n_obs, n_iou)
        advisory_payload = {
            "protocol_version": PROTOCOL_VERSION,
            "task_id": self.context.task_id,
            "context_digest": self.context.context_digest,
            "disposition": "ACCEPTED_AS_ADVISORY",
            "execution_authorization": "NOT_GRANTED",
            "action_requested": action,
            "observations": candidate["observations"],
            "interpretations": candidate["interpretations"],
            "unresolved_ious": candidate["unresolved_ious"],
            "iou_ratio_milli": iou_ratio_milli(ratio),
            "iou_ratio_band": iou_ratio_band(ratio),
            "policy_hash": self.context.policy_hash,
            "ingress_adapter_hash": self.context.ingress_adapter_hash,
            "provenance_anchors": sorted(self.context.source_hashes),
        }

        return {
            "validation_status": "VALID",
            "suit_digest": canonical_hash(advisory_payload),
            "envelope": advisory_payload,
        }

    def _verify_observations(self, obs_list: Any,
                             failures: List[Dict[str, str]]) -> int:
        if not isinstance(obs_list, list) or len(obs_list) > MAX_ITEMS:
            failures.append({"code": "SCHEMA_TYPE_ERROR",
                             "detail": "observations must be a bounded list"})
            return 0

        seen_ids: Set[str] = set()
        count = 0
        for idx, obs in enumerate(obs_list):
            path = f"observations[{idx}]"
            expected = {"observation_id", "code", "evidence_refs", "note"}
            if not isinstance(obs, dict) or not set(obs.keys()) <= expected \
                    or "observation_id" not in obs or "code" not in obs \
                    or "evidence_refs" not in obs:
                failures.append({"code": "SCHEMA_VIOLATION",
                                 "detail": f"{path} malformed structure"})
                continue

            obs_id = obs["observation_id"]
            if not isinstance(obs_id, str) or not ID_RE.fullmatch(obs_id) \
                    or obs_id in seen_ids:
                failures.append({"code": "INVALID_OBSERVATION_ID",
                                 "detail": f"{path}:{obs_id}"})
            seen_ids.add(obs_id)

            # Strict enum set-membership replaces free-text observation fields
            if obs["code"] not in {c.value for c in ObservationCode}:
                failures.append({"code": "INVALID_OBSERVATION_CODE",
                                 "detail": f"{path}:{obs['code']}"})

            # Optional note: bounded, backstop-scanned
            if "note" in obs and obs["note"] is not None:
                if check_text_bounds(obs["note"], f"{path}.note", failures):
                    for term in backstop_authority_scan(obs["note"]):
                        failures.append({"code": "AUTHORITY_LANGUAGE_DETECTED",
                                         "detail": f"{path}.note:{term}"})

            refs = obs["evidence_refs"]
            if not isinstance(refs, list) or not refs:
                failures.append({"code": "EMPTY_EVIDENCE_REFS", "detail": path})
                continue

            for ref in refs:
                if ref not in self._allowed_sources:
                    failures.append({"code": "UNCOMMITTED_EVIDENCE_REF",
                                     "detail": f"{path}:{ref}"})
                elif not self.evidence_store.resolve(ref):
                    failures.append({"code": "UNRESOLVABLE_EVIDENCE_ARTIFACT",
                                     "detail": f"{path}:{ref}"})
            count += 1
        return count

    def _verify_interpretations(self, int_list: Any, obs_list: Any,
                                failures: List[Dict[str, str]]) -> None:
        if not isinstance(int_list, list) or len(int_list) > MAX_ITEMS:
            failures.append({"code": "SCHEMA_TYPE_ERROR",
                             "detail": "interpretations must be a bounded list"})
            return

        valid_obs_ids = {o.get("observation_id") for o in obs_list
                         if isinstance(o, dict)}
        seen_ids: Set[str] = set()

        for idx, item in enumerate(int_list):
            path = f"interpretations[{idx}]"
            expected = {"interpretation_id", "text", "based_on_observation_ids",
                        "confidence"}
            if not isinstance(item, dict) or set(item.keys()) != expected:
                failures.append({"code": "SCHEMA_VIOLATION",
                                 "detail": f"{path} malformed structure"})
                continue

            int_id = item["interpretation_id"]
            if not isinstance(int_id, str) or not ID_RE.fullmatch(int_id) \
                    or int_id in seen_ids:
                failures.append({"code": "INVALID_INTERPRETATION_ID",
                                 "detail": f"{path}:{int_id}"})
            seen_ids.add(int_id)

            # Strict enum set-membership for confidence
            if item["confidence"] not in {c.value for c in ConfidenceEnum}:
                failures.append({"code": "INVALID_CONFIDENCE_ENUM",
                                 "detail": f"{path}.confidence"})

            # Surviving free-text field: bounded + backstop-scanned
            if check_text_bounds(item["text"], f"{path}.text", failures):
                for term in backstop_authority_scan(item["text"]):
                    failures.append({"code": "AUTHORITY_LANGUAGE_DETECTED",
                                     "detail": f"{path}.text:{term}"})

            bases = item["based_on_observation_ids"]
            if not isinstance(bases, list) or not bases:
                failures.append({"code": "EMPTY_INTERPRETATION_BASIS",
                                 "detail": path})
                continue

            for b in bases:
                if b not in valid_obs_ids:
                    failures.append({"code": "DANGLING_OBSERVATION_REF",
                                     "detail": f"{path}->{b}"})

    def _verify_ious(self, iou_list: Any, requested_action: Any,
                     failures: List[Dict[str, str]]) -> int:
        if not isinstance(iou_list, list) or len(iou_list) > MAX_ITEMS:
            failures.append({"code": "SCHEMA_TYPE_ERROR",
                             "detail": "unresolved_ious must be a bounded list"})
            return 0

        seen_ids: Set[str] = set()
        has_blocking = False
        count = 0

        for idx, iou in enumerate(iou_list):
            path = f"unresolved_ious[{idx}]"
            expected = {"iou_id", "code", "blocking"}
            if not isinstance(iou, dict) or set(iou.keys()) != expected:
                failures.append({"code": "SCHEMA_VIOLATION",
                                 "detail": f"{path} malformed structure"})
                continue

            iou_id = iou["iou_id"]
            if not isinstance(iou_id, str) or not ID_RE.fullmatch(iou_id) \
                    or iou_id in seen_ids:
                failures.append({"code": "INVALID_IOU_ID",
                                 "detail": f"{path}:{iou_id}"})
            seen_ids.add(iou_id)

            # Strict enum set-membership for the IOU code
            if iou["code"] not in {c.value for c in IOUCode}:
                failures.append({"code": "INVALID_IOU_CODE",
                                 "detail": f"{path}:{iou['code']}"})

            if not isinstance(iou["blocking"], bool):
                failures.append({"code": "SCHEMA_TYPE_ERROR",
                                 "detail": f"{path}.blocking must be boolean"})
            elif iou["blocking"]:
                has_blocking = True
            count += 1

        # Return to Reality contract: blocking IOUs require requesting a
        # measurement or no-op
        if has_blocking and requested_action not in {
                ActionID.REQUEST_DISCRIMINATOR.value, ActionID.NO_OP.value}:
            failures.append({
                "code": "BLOCKING_IOU_VIOLATION",
                "detail": f"Active blocking IOUs require REQUEST_DISCRIMINATOR "
                          f"or NO_OP, got: {requested_action}",
            })
        return count

    def _parse_json(self, raw_str: str) -> Dict[str, Any]:
        def reject_duplicates(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
            res: Dict[str, Any] = {}
            for k, v in pairs:
                if k in res:
                    raise ValueError(f"Duplicate key detected: {k}")
                res[k] = v
            return res

        parsed = json.loads(
            raw_str,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda c: (_ for _ in ()).throw(
                ValueError(f"Invalid constant: {c}")),
        )
        if not isinstance(parsed, dict):
            raise ValueError("Root payload must be an object")
        return normalize_for_canonical_digest(parsed)

    def _fail_closed(self, failures: List[Dict[str, str]],
                     n_obs: int, n_iou: int) -> Dict[str, Any]:
        sorted_failures = sorted(failures, key=lambda x: (x["code"], x["detail"]))
        ratio = iou_ratio(n_obs, n_iou)
        fail_payload = {
            "protocol_version": PROTOCOL_VERSION,
            "task_id": self.context.task_id,
            "context_digest": self.context.context_digest,
            "disposition": "REJECT",
            "execution_authorization": "NOT_GRANTED",
            "failure_reasons": sorted_failures,
            "iou_ratio_milli": iou_ratio_milli(ratio),
            "iou_ratio_band": iou_ratio_band(ratio),
        }
        return {
            "validation_status": "INVALID",
            "suit_digest": canonical_hash(fail_payload),
            "envelope": fail_payload,
        }
