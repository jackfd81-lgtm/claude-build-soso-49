"""Integration Contract 001 — shared procedure registry.

Every procedure either system can execute is registered here
once, under a unique registered identity. RealCog's native fixes
and SoSo's P1–P11 live in separate namespaces (RC-FIX-* and
SOSO-P*) and are NEVER treated as equivalent because their names
look similar — the registry records no cross-mapping, and any
future equivalence claim would need its own evidence and its own
registry entry.

Each entry carries what the pipeline needs to decide whether the
procedure may be selected, admitted, or trialled:
  - preconditions, each marked hard or soft. HARD preconditions
    (firmware) gate execution: unknown or contradicted means no
    execution, only an information request. SOFT preconditions
    (kind) guide selection but do not gate execution — SoSo's own
    design treats kind as a classification aid, not a gate.
  - reversibility and trial_safety. A trial is permitted only
    when trial_safety is SAFE, the policy allows the procedure,
    and no hard precondition is unknown or contradicted. SAFE
    means the trial is reversible and bounded with no irreversible
    external effect. REQUIRES_AUTHORIZATION means the trial needs
    a separate authorization (the pipeline records an
    authorization request instead of executing). FORBIDDEN means
    the procedure is never trialled and never executed under any
    pipeline stage (the Suit policy also forbids it).
"""
from __future__ import annotations

from dataclasses import dataclass, field

REGISTRY_VERSION = "contract-registry/1"

TRIAL_SAFE = "SAFE"
TRIAL_REQUIRES_AUTHORIZATION = "REQUIRES_AUTHORIZATION"
TRIAL_FORBIDDEN = "FORBIDDEN"


@dataclass(frozen=True)
class ProcedureEntry:
    procedure_id: str
    version: str
    origin: str            # "realcog-0.4" or "soso-rev1.0"
    native_id: str         # "rotate_logs" / "P7"
    description: str
    # preconditions: name -> (required value, hard?)
    preconditions: dict = field(default_factory=dict)
    capabilities: tuple = ()
    expected_effects: str = ""
    reversibility: str = ""   # "reversible" | "bounded" | "irreversible"
    trial_safety: str = TRIAL_REQUIRES_AUTHORIZATION
    validation_criteria: str = ""
    adapter: str = ""         # "realcog" | "soso"
    # Contract 002 — registered task-family relevance. A family is
    # listed only when measured evidence supports it (a resolved
    # task in that family, or the Phase 3 comparison). The family
    # is a RANKING signal for the trial list, never a filter:
    # cross-family candidates stay eligible. family_evidence names
    # the measurement that supports each family.
    families: frozenset = frozenset()
    family_evidence: str = ""


def _rc(pid, native, desc, caps, effects, reversibility, safety,
        families=(), evidence=""):
    return ProcedureEntry(
        procedure_id=pid, version="1", origin="realcog-0.4",
        native_id=native, description=desc, preconditions={},
        capabilities=caps, expected_effects=effects,
        reversibility=reversibility, trial_safety=safety,
        validation_criteria=f"validator: {native} is the true fix for the task signature",
        adapter="realcog", families=frozenset(families),
        family_evidence=evidence)


def _soso(pid, native, desc, preconditions, caps, effects, safety,
          families=(), evidence=""):
    return ProcedureEntry(
        procedure_id=pid, version="1", origin="soso-rev1.0",
        native_id=native, description=desc,
        preconditions=preconditions, capabilities=caps,
        expected_effects=effects,
        reversibility="bounded", trial_safety=safety,
        validation_criteria=f"validator: {native} resolves the task under its demonstrated conditions",
        adapter="soso", families=frozenset(families),
        family_evidence=evidence)


# --- RealCog native fixes (CANDS order preserved as registry order) ---
# Trial safety is judged on the action itself in the synthetic
# world: clearing a cache and rotating logs are reversible;
# restarting a service is bounded; rewriting configuration needs
# a separate authorization (a backup must exist); wiping a disk
# is irreversible and forbidden.
_RC = [
    _rc("RC-FIX-CLEAR-CACHE", "clear_cache", "clear the cache",
        ("CAP_CACHE",), "cache emptied; rebuilds on demand",
        "reversible", TRIAL_SAFE,
        ("cache",), "Phase 3: resolved cache_big (cache family)"),
    _rc("RC-FIX-RESTART-SVC", "restart_svc", "restart the service",
        ("CAP_SERVICE",), "service restarts; transient state lost",
        "bounded", TRIAL_SAFE,
        ("service",), "Phase 3: resolved svc_hung (service family)"),
    _rc("RC-FIX-ROTATE-LOGS", "rotate_logs", "rotate the logs",
        ("CAP_LOGS",), "logs rotated; old logs archived",
        "reversible", TRIAL_SAFE,
        ("disk",), "Phase 3: resolved disk_full / disk_almost_full / diskfull"),
    _rc("RC-FIX-WIPE-DISK", "wipe_disk", "wipe the disk",
        ("CAP_DISK",), "disk wiped; data unrecoverable",
        "irreversible", TRIAL_FORBIDDEN,
        ("disk",), "Phase 3: the only fix for wipe_trap (never executed)"),
    _rc("RC-FIX-RESET-CFG", "reset_cfg", "reset the configuration",
        ("CAP_CONFIG",), "configuration reset to defaults",
        "bounded", TRIAL_REQUIRES_AUTHORIZATION,
        ("config",), "Phase 3: resolved cfg_bad / net_down (config family)"),
]

# --- SoSo P1–P11 (descriptions mirror cognition.PROCEDURES) ---
# Firmware preconditions are HARD (SoSo's admission gate);
# kind preconditions are SOFT (classification aids).
# The legacy traps P8–P11 are never safe to trial blind: they are
# the quick procedures whose unexamined use is what the traps
# exploit, so they require separate authorization.
_SOSO = [
    _soso("SOSO-P1", "P1", "valve reseat, firmware v1 manual",
          {"firmware": ("v1", True)}, ("CAP_VALVE",), "valve reseated", TRIAL_SAFE,
          ("valve",), "Contract 001: resolved valve-v1 tasks (live path)"),
    _soso("SOSO-P2", "P2", "valve reseat, firmware v2 manual",
          {"firmware": ("v2", True)}, ("CAP_VALVE",), "valve reseated", TRIAL_SAFE,
          ("valve",), "Contract 001: resolved valve-v2 training tasks"),
    _soso("SOSO-P3", "P3", "temperature sensor recalibration",
          {"kind": ("sensor", False)}, ("CAP_SENSOR",), "sensor recalibrated", TRIAL_SAFE,
          ("sensor",), "Contract 001: resolved sensor training tasks"),
    _soso("SOSO-P4", "P4", "pump inspection and bearing check",
          {"kind": ("pump", False)}, ("CAP_PUMP",), "pump inspected", TRIAL_SAFE,
          ("pump",), "Contract 001: resolved pump training tasks"),
    _soso("SOSO-P5", "P5", "alternate route planning",
          {"kind": ("route", False)}, ("CAP_ROUTE",), "route planned", TRIAL_SAFE,
          ("blocked",), "MEM-004/Phase 3: the considered alternative for blocked routes"),
    _soso("SOSO-P6", "P6", "slow traverse with clearance probe",
          {"kind": ("traverse", False)}, ("CAP_ROUTE",), "passage traversed", TRIAL_SAFE,
          ("narrow",), "MEM-004: the correct procedure for the narrow family"),
    _soso("SOSO-P7", "P7", "detour with ground probe",
          {"kind": ("detour", False)}, ("CAP_ROUTE",), "detour completed", TRIAL_SAFE,
          ("blocked", "slope"), "Contract 001: resolved blocked training tasks; "
          "MEM-004/Phase 3: cross-family transfer to slope 10/12"),
    _soso("SOSO-P8", "P8", "temperature sensor drift reset, legacy",
          {"kind": ("sensor", False)}, ("CAP_SENSOR",), "sensor reset", TRIAL_REQUIRES_AUTHORIZATION,
          ("sensor",), "MEM-001: legacy sensor procedure"),
    _soso("SOSO-P9", "P9", "pump vibration quick check, legacy",
          {"kind": ("pump", False)}, ("CAP_PUMP",), "pump checked", TRIAL_REQUIRES_AUTHORIZATION,
          ("pump",), "MEM-001: legacy pump procedure"),
    _soso("SOSO-P10", "P10", "narrow passage fast traverse, legacy",
          {"kind": ("traverse", False)}, ("CAP_ROUTE",), "passage traversed", TRIAL_REQUIRES_AUTHORIZATION,
          ("narrow",), "MEM-001: legacy narrow procedure"),
    _soso("SOSO-P11", "P11", "slope crossing direct route, legacy",
          {"kind": ("route", False)}, ("CAP_ROUTE",), "slope crossed", TRIAL_REQUIRES_AUTHORIZATION,
          ("slope",), "MEM-001: legacy slope procedure"),
]

REGISTRY = {e.procedure_id: e for e in _RC + _SOSO}
BY_NATIVE = {e.native_id: e for e in _RC + _SOSO}
# Native ids are unique across both systems by construction; the
# registry asserts it rather than assuming it.
assert len(BY_NATIVE) == len(REGISTRY), "native procedure ids must be unique"


def get(procedure_id: str) -> ProcedureEntry:
    return REGISTRY[procedure_id]


def by_native(native_id: str) -> ProcedureEntry:
    return BY_NATIVE[native_id]


# TRACE-002 correction, item 4: procedure identity is two-sided.
# A procedure has a NATIVE id (the system's own name: "P2",
# "clear_cache") and a REGISTERED id (the shared contract
# identity: "SOSO-P2", "RC-FIX-CLEAR-CACHE"). The trace, the
# reports and the experience store have mixed them — attempts
# carry the registered id, criteria carry the native id,
# experience blocks carry the native id — which made it
# possible to read two different ids as two different
# procedures. procedure_identity() normalises either form to
# both, with the origin and adapter, so every consumer can
# record both and no identity is ever ambiguous. Raises
# KeyError for an unknown id in either form.
def procedure_identity(procedure_id: str) -> dict:
    """Normalise a procedure id (native or registered) to both
    forms plus the origin and adapter. Returns {registered,
    native, origin, adapter, description}. Both ids are
    preserved wherever a procedure is named."""
    if procedure_id in REGISTRY:
        entry = REGISTRY[procedure_id]
    elif procedure_id in BY_NATIVE:
        entry = BY_NATIVE[procedure_id]
    else:
        raise KeyError(f"unknown procedure id {procedure_id!r}")
    return {"registered": entry.procedure_id, "native": entry.native_id,
            "origin": entry.origin, "adapter": entry.adapter,
            "description": entry.description}


def registered_of(procedure_id: str) -> str:
    """The registered id for a native or registered id."""
    return procedure_identity(procedure_id)["registered"]


def native_of(procedure_id: str) -> str:
    """The native id for a native or registered id."""
    return procedure_identity(procedure_id)["native"]


def hard_preconditions(entry: ProcedureEntry) -> dict:
    return {k: v for k, (v, hard) in entry.preconditions.items() if hard}


def check_hard_preconditions(entry: ProcedureEntry, conditions: dict,
                             unknown: tuple = (),
                             contradictory: tuple = ()) -> str:
    """'satisfied' | 'unknown' | 'contradicted' for the entry's
    HARD preconditions against the task's known conditions.
    A condition named in `contradictory` (the task record's
    contradictory_conditions — e.g. both firmware versions present)
    contradicts every procedure that requires that condition.
    Unknown conditions are listed separately on the task record.
    A hard precondition whose value is unknown is 'unknown'; one
    whose known value differs is 'contradicted'. Soft
    preconditions never affect this result."""
    for name, (value, hard) in entry.preconditions.items():
        if not hard:
            continue
        if name in contradictory:
            return "contradicted"
        if name in unknown or name not in conditions:
            return "unknown"
        if conditions[name] != value:
            return "contradicted"
    return "satisfied"


def trial_eligible(entry: ProcedureEntry, conditions: dict,
                   unknown: tuple = (), policy_allows: bool = True,
                   contradictory: tuple = ()) -> bool:
    """A trial is permitted only when ALL of these hold: the
    procedure is trial-SAFE, the policy allows it, and no hard
    precondition is unknown or contradicted. Nothing else — no
    score, no rank, no urgency — authorizes a trial."""
    if entry.trial_safety != TRIAL_SAFE:
        return False
    if not policy_allows:
        return False
    return check_hard_preconditions(entry, conditions, unknown,
                                    contradictory) == "satisfied"
