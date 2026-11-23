"""PATHWAY-001 — experiment half: parity harness, 20 frozen cases,
matched four-arm experiment.

The cases were FROZEN only after measuring arm-A and arm-B
behavior on each candidate (probe, 2026-11-18): arm A resolves
none of the 20 (different-answer tasks end UNSOLVED after 8
failed attempts; missing-evidence tasks escalate with 0
attempts); arm B resolves the 5 missing-evidence resolve tasks
in 1 attempt, justifies abstention on the 5 abstention tasks
(ESCALATED, 0 attempts), and still ends UNSOLVED on all 10
different-answer tasks (U10 never fires — the tags do not point
at P1/P2). The learning tasks (2 per group, P1/v1 and P2/v2)
all resolve under arm-B conditions. Those measured behaviors
are re-verified at runtime by run_pathway_experiment(); a case
whose measured A/B behavior contradicts its intended class
after freezing is reported as a case defect and excluded from
scoring, never silently re-tuned.

Interface notes — the two PATHWAY-001 halves as built (not as
briefed):
- Duplicate pathway writes FAIL CLOSED (Big49 is write-once;
  PathwayStore.write raises on a duplicate id). The recorder's
  existing_ids counter rule is used, so collisions cannot
  occur in this experiment.
- Pathway blocks use a compact encoding (encode_record /
  decode_record); the in-memory record always carries the full
  keys the brief specifies.
- Pipeline._answer_condition returns a 3-tuple
  (updated_task, observation, expectation); the experiment
  never calls it directly.
- PathwayStore wraps a Big49 (PathwayStore(big)); this
  experiment wraps the pipeline's own big.db, so the pathway
  blocks travel with the root on copytree and never pollute
  the experience projections (ExperienceStore.read_all filters
  record_id LIKE 'exp.%').
- ShuffledPathwayProvider as built DOES express the arm-D
  shuffle (task_id -> pathway of a DIFFERENT task with the
  same (signature, family), deterministic under the seed,
  auditable `association`), so it is used as built; no fix to
  pathway.py was needed. One honest limit, recorded in the
  results: every pathway in a group names the same single
  discriminator ("firmware"), so the shuffle changes which
  pathway is offered, not which discriminator is asked — D
  matching C is the association-independence finding, not a
  vacuous control (each group holds 2 pathways, so the
  assignment itself is a genuine shuffle).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import Counter

from . import task as task_mod
from .evidence import FrozenEvidenceSource
from .harness import Harness, TRAINING, TRANSFER_TASKS
from .pathway import (PathwayRecorder, PathwayReplayProvider,
                      PathwayStore, ShuffledPathwayProvider)
from .registry import BY_NATIVE, check_hard_preconditions
from .registry import get as reg_get
from .registry import procedure_identity
from .trace import trace_events

SHUFFLE_SEED = 20261118
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RESULTS_PATH = os.path.join(PROJECT_ROOT, "pathway001_results.json")

# The only hard-preconditioned discriminator in this milestone.
DISCRIMINATOR = "firmware"


# ------------------------------------------------------------------
# The 20 frozen cases
# ------------------------------------------------------------------
# Each case record: the task fields the pipeline runs, plus the
# evaluator-owned "correct" (native id, or None for the
# abstention tasks) and the frozen "evidence_value" the source
# answers for this task's firmware. The task dict passed to the
# pipeline carries NO "correct" key (criteria/observations are
# registered separately via Harness.add_task). All cases are
# noise-free with frozen observations.
#
# Different-answer groups (5 groups x 2 tasks, same
# (signature, family), different correct procedure P1 vs P2):
# tags share words with P7/P5 descriptions (route/ground/
# detour/probe) but NOT with P1/P2 descriptions (no
# valve/reseat/firmware/manual), and share >=1 tag with the
# group's learning tasks (the tag gate for C retrieval).
# Missing-evidence groups (5 groups x 2 tasks, family valve):
# tags DO point at P1/P2 (valve/stuck/seal/pressure); one task
# resolves (evidence v1/v2), one justifies abstention (v3).

def _case(task_id, signature, family, tags, correct, evidence_value, kind):
    return {"task_id": task_id, "signature": signature, "family": family,
            "tags": list(tags), "conditions": {},
            "unknown_conditions": ("firmware",),
            "requested_operation": "repair",
            "provenance": ("int:pathway-001",), "correct": correct,
            "evidence_value": evidence_value, "kind": kind,
            "group": signature}


_DA_SPECS = [
    ("pw-da-blocked", "da_blocked_a", "blocked",
     ["route", "blocked", "ground", "detour"]),
    ("pw-da-slope", "da_slope_a", "slope",
     ["slope", "ground", "route", "detour"]),
    ("pw-da-narrow", "da_narrow_a", "narrow",
     ["narrow", "ground", "route", "probe"]),
    ("pw-da-pump", "da_pump_a", "pump",
     ["pump", "ground", "route", "detour"]),
    ("pw-da-sensor", "da_sensor_a", "sensor",
     ["sensor", "ground", "route", "detour"]),
]

CASES = []
for _prefix, _sig, _fam, _tags in _DA_SPECS:
    CASES.append(_case(_prefix + "-p1", _sig, _fam, _tags,
                       "P1", "v1", "different_answer"))
    CASES.append(_case(_prefix + "-p2", _sig, _fam, _tags,
                       "P2", "v2", "different_answer"))

_ME_RESOLVE = ["P1", "P2", "P1", "P2", "P1"]
for _i, _correct in enumerate(_ME_RESOLVE):
    _letter = chr(97 + _i)
    _sig = f"me_valve_{_letter}"
    _tags = ["valve", "stuck", "seal", "pressure"]
    CASES.append(_case(f"pw-me-{_letter}-res", _sig, "valve", _tags,
                       _correct, "v1" if _correct == "P1" else "v2",
                       "missing_evidence"))
    CASES.append(_case(f"pw-me-{_letter}-abs", _sig, "valve", _tags,
                       None, "v3", "missing_evidence"))

# Learning tasks: 2 per group (P1/v1, P2/v2), same
# (signature, family) as the group, tags overlapping the
# group's test tasks AND pointing at P1/P2 (valve), firmware
# unknown. Run under arm-B conditions in the learning phase;
# their pathways are what arms C/D replay. Task ids are kept
# short on purpose: the pathway record's compact form must fit
# the Big49 cognitive bucket (512 chars), and the id appears
# three times in the record (pathway id, source task id,
# evidence provenance).
_LEARNING_SHORT = {"blocked": "b", "slope": "s", "narrow": "n",
                   "pump": "p", "sensor": "e"}
LEARNING_TASKS = []
for _prefix, _sig, _fam, _tags in _DA_SPECS:
    _ltags = ["valve"] + [t for t in _tags if t != _tags[0]][:2]
    _short = _LEARNING_SHORT[_fam]
    LEARNING_TASKS.append(_case(f"da-{_short}-l1", _sig, _fam, _ltags,
                                "P1", "v1", "learning"))
    LEARNING_TASKS.append(_case(f"da-{_short}-l2", _sig, _fam, _ltags,
                                "P2", "v2", "learning"))
for _i in range(5):
    _letter = chr(97 + _i)
    _sig = f"me_valve_{_letter}"
    _tags = ["valve", "stuck", "seal", "pressure"]
    LEARNING_TASKS.append(_case(f"me-{_letter}-l1", _sig, "valve",
                                _tags, "P1", "v1", "learning"))
    LEARNING_TASKS.append(_case(f"me-{_letter}-l2", _sig, "valve",
                                _tags, "P2", "v2", "learning"))

ALL_TASKS = list(CASES) + list(LEARNING_TASKS)


def _public_task(case):
    """The task dict the pipeline runs: no evaluator-owned
    fields (criteria/observations are registered separately)."""
    return {k: v for k, v in case.items()
            if k not in ("correct", "evidence_value", "kind", "group")}


def _native(procedure_id):
    if procedure_id is None:
        return None
    return procedure_identity(procedure_id)["native"]


def _task_record(task_dict):
    return task_mod.make_task(**{k: v for k, v in task_dict.items()
                                 if k in ("task_id", "signature", "family",
                                          "tags", "conditions",
                                          "unknown_conditions",
                                          "contradictory_conditions",
                                          "requested_operation",
                                          "observed", "provenance")})


# ------------------------------------------------------------------
# Execution parity
# ------------------------------------------------------------------

_PROPOSED_FIELDS = ("procedure", "native", "source", "rank",
                    "rank_components")
# ESCALATION reasons the pipeline emits, and the state each one
# belongs to. "trial not allowed by profile ..." is matched by
# prefix below.
_ESCALATION_STATE = {
    "unknown required condition": "ESCALATED",
    "evidence contradicts every pointed-at procedure": "ESCALATED",
    "every pointed-at procedure blocked": "UNSOLVED",
}


def parity_check(report, trace_log, task_dict=None, policy=None,
                 trial_candidates_fn=None, latest_per_pair=None):
    """Re-derive the benchmark decision record from the trace
    events of report["execution_id"] plus the committed reports
    (the experience store's latest_per_pair), and diff it field
    by field against the live report.

    Returns {"ok", "defects", "compared", "scope_limits"}.
    A defect blocks scoring for that task/arm only.

    The re-derivation reads NOTHING from the live report except
    as the comparand: proposed from CANDIDATE_PROPOSED events;
    rejected from CANDIDATE_REJECTED events plus the KNOWN
    non-event rejections (transfer "not in the procedure
    registry", re-derived from the proposed list; the U10
    pointed_blocks and the escalate_because list, re-derived by
    recomputing _trial_candidates from the task + registry +
    policy — a legitimate re-derivation, not re-scoring); the
    REVALIDATION_DENIED operational events (a refusal is a
    rejection with no CANDIDATE_REJECTED event); attempts from
    OBSERVATION + VALIDATION events, with qualification_after
    from the committed experience record; information_requests
    from EVIDENCE_EXPECTATION / EVIDENCE_OBSERVED events (the
    request origin — pathway-replay vs targeted_investigation —
    from the enclosing span's purpose) plus the unanswered
    arm-A request re-derived from the ESCALATION event and the
    trial recompute; the operational INFORMATION_REQUEST events
    corroborate the non-replay requests; unattempted and
    pathway_cost are recomputed the way run() computes them;
    abstention from the TASK_END event + the ESCALATION events;
    policy disposition from the ADMISSION events.

    Optional inputs: task_dict + trial_candidates_fn (a
    callable taking a TaskRecord and returning the pipeline's
    _trial_candidates dict) are needed to re-derive the
    non-event rejections and the unanswered requests; policy
    is needed to check the ADMISSION policy version;
    latest_per_pair is needed to check qualification_after.
    When an input is absent, the fields it gates are NOT
    compared and are listed in scope_limits — never silently
    matched, never a false defect.
    """
    defects, compared, scope_limits = [], [], []
    task_id = report.get("task_id")
    execution_id = report.get("execution_id")
    events = trace_events(trace_log, task_id, execution_id)

    def _data(event_type):
        return [(seq, ev.get("data") or {})
                for seq, ev in events if ev["event_type"] == event_type]

    def _defect(msg):
        defects.append(msg)

    # -- TASK_BEGIN context: the run must be for this task --
    begins = [ev for _s, ev in events if ev["event_type"] == "TASK_BEGIN"]
    if not begins:
        _defect("no TASK_BEGIN event in the trace")
    elif task_dict is not None:
        ctx = (begins[-1].get("data") or {}).get("context") or {}
        for field in ("signature", "family"):
            if ctx.get(field) != task_dict.get(field):
                _defect(f"TASK_BEGIN context {field} {ctx.get(field)!r} != "
                        f"task {task_dict.get(field)!r}")
        if sorted(ctx.get("tags") or []) != sorted(task_dict.get("tags") or []):
            _defect("TASK_BEGIN context tags != task tags")
        if dict(ctx.get("conditions") or {}) != dict(task_dict.get("conditions") or {}):
            _defect("TASK_BEGIN context conditions != task conditions")
        compared.append("task_begin")

    # -- proposed: ordered, from CANDIDATE_PROPOSED events --
    derived_proposed = []
    for _seq, d in _data("CANDIDATE_PROPOSED"):
        derived_proposed.append({f: d.get(f) for f in _PROPOSED_FIELDS})
    rep_proposed = list(report.get("proposed") or [])
    compared.append("proposed")
    if derived_proposed != rep_proposed:
        _defect(f"proposed mismatch: re-derived {len(derived_proposed)} "
                f"entries vs report {len(rep_proposed)}; first difference: "
                f"{_first_diff(derived_proposed, rep_proposed)}")

    # -- information_requests: EVIDENCE_* events + the unanswered --
    # (computed BEFORE the rejected comparison: whether the U10
    # pointed_blocks were appended depends on the trace-derived
    # outcome of the U10 request, not on the report's outcome
    # field.)
    purposes = {}
    for _seq, ev in events:
        if ev["event_type"] == "FUNCTION_ENTER":
            purposes[ev.get("span_id")] = (ev.get("data") or {}).get("purpose")
    expectations = {}
    for seq, ev in events:
        if ev["event_type"] == "EVIDENCE_EXPECTATION":
            d = ev.get("data") or {}
            expectations[d.get("expectation_id")] = (seq, d, purposes.get(
                ev.get("span_id")))
    observations = {}
    for seq, ev in events:
        if ev["event_type"] == "EVIDENCE_OBSERVED":
            d = ev.get("data") or {}
            observations[d.get("expectation_id")] = (seq, d)
    derived_reqs = []  # (sort_seq, entry)
    u10_blocked = False  # the U10 request did NOT resume
    for eid, (seq, d, purpose) in expectations.items():
        cond = d.get("condition")
        obs = observations.get(eid)
        origin = ("pathway-replay" if purpose
                  == "replay stored pathway discriminators"
                  else "targeted_investigation")
        entry = {"missing": [cond], "question": d.get("measurement"),
                 "procedures": sorted(
                     h.get("procedure") for h in d.get("hypotheses") or []),
                 "source": origin, "answered": obs is not None,
                 "expectation_id": {cond: eid},
                 "expectation": {cond: d},
                 "observation": ({cond: obs[1]} if obs else None)}
        if obs is None:
            entry["outcome"] = "unanswered"
        elif origin == "pathway-replay":
            # The replay path records "resumed"/"unanswered"
            # only; a contradicting replayed observation stops
            # the replay and the normal flow decides (see the
            # design gap noted in run_pathway_experiment).
            entry["outcome"] = "resumed"
        else:
            hyps = d.get("hypotheses") or []
            contradicted = bool(hyps) and all(
                h.get("requires") != obs[1].get("value") for h in hyps)
            entry["outcome"] = ("justified_abstention" if contradicted
                                else "resumed")
        if origin == "targeted_investigation" \
                and entry["outcome"] != "resumed":
            u10_blocked = True
        derived_reqs.append((seq, entry))
    esc_reasons = {d.get("reason") for _s, d in _data("ESCALATION")}
    if "unknown required condition" in esc_reasons:
        # The no-evidence branch: ask and escalate, never
        # answered — the pointed_blocks were appended.
        u10_blocked = True
    have_trial = (trial_candidates_fn is not None and task_dict is not None)
    if have_trial:
        trial = trial_candidates_fn(_task_record(task_dict))
        blocked = trial.get("blocked_by_unknown") or {}
        for seq, d in _data("ESCALATION"):
            if d.get("reason") == "unknown required condition":
                derived_reqs.append((seq, {
                    "missing": blocked.get("missing"),
                    "question": blocked.get("question"),
                    "procedures": blocked.get("procedures"),
                    "source": "targeted_investigation"}))
    else:
        if "unknown required condition" in esc_reasons:
            scope_limits.append("unanswered arm-A information request "
                                "not re-derived: no trial_candidates_fn / "
                                "task_dict supplied")
    derived_reqs = [entry for _s, entry in sorted(derived_reqs,
                                                   key=lambda p: p[0])]

    # -- rejected: events + known non-event rejections (multiset) --
    derived_rejected = Counter(
        (d.get("procedure"), d.get("reason"))
        for _s, d in _data("CANDIDATE_REJECTED"))
    if have_trial:
        # Transfer "not in the procedure registry": the report
        # appends it with NO event; re-derive from the
        # (event-verified) proposed transfer candidates whose
        # native id is not registered.
        for p in derived_proposed:
            if p.get("source") == "transfer" \
                    and p.get("native") not in BY_NATIVE:
                derived_rejected[(p.get("native"),
                                  "not in the procedure registry")] += 1
        # U10 pointed_blocks: appended (with no event) whenever
        # a targeted_investigation request does NOT resume
        # (trace-derived above, never read off the report). The
        # pointed_blocks come from the trial recompute on the
        # ORIGINAL task (the run was carrying them when it
        # asked).
        if u10_blocked:
            for proc_id, reason in trial.get("pointed_blocks") or []:
                derived_rejected[(proc_id, reason)] += 1
        # escalate_because: appended (with no event) on the
        # "every pointed-at procedure blocked" path. That path
        # runs AFTER any replayed observation was applied, so
        # it must be re-derived on the task as known after all
        # trace-derived observations — recomputing on the
        # original task (firmware still unknown) yields no
        # escalate_because at all and would be a false defect.
        if "every pointed-at procedure blocked" in esc_reasons:
            updated = dict(task_dict)
            conds = dict(task_dict.get("conditions") or {})
            unknown = [c for c in task_dict.get("unknown_conditions") or []]
            for entry in derived_reqs:
                for _c, obs in (entry.get("observation") or {}).items():
                    if obs is not None:
                        conds[obs.get("condition")] = obs.get("value")
                        if obs.get("condition") in unknown:
                            unknown.remove(obs.get("condition"))
            updated["conditions"] = conds
            updated["unknown_conditions"] = tuple(unknown)
            trial_updated = trial_candidates_fn(_task_record(updated))
            for proc_id, reason in trial_updated.get("escalate_because") or []:
                derived_rejected[(proc_id, reason)] += 1
    else:
        scope_limits.append(
            "non-event rejections (U10 pointed_blocks / "
            "escalate_because / transfer 'not in the procedure "
            "registry') not re-derived: no trial_candidates_fn / "
            "task_dict supplied")
    # REVALIDATION_DENIED is an operational ledger row, not a
    # TRACE_EVENT: read the raw ledger for it. A refusal is a
    # rejection with no CANDIDATE_REJECTED event.
    try:
        raw_rows = trace_log.events(task_id)
    except Exception:
        raw_rows = []
        scope_limits.append("raw ledger unreadable: REVALIDATION_DENIED "
                            "rejections not checked")
    for _seq, kind, _t, _step, data in raw_rows:
        if kind == "REVALIDATION_DENIED":
            derived_rejected[(data.get("action"), data.get("reason"))] += 1
    rep_rejected = Counter((r.get("procedure"), r.get("reason"))
                           for r in report.get("rejected") or [])
    if have_trial:
        compared.append("rejected")
        if derived_rejected != rep_rejected:
            _defect(f"rejected mismatch: re-derived "
                    f"{dict(derived_rejected)} vs report "
                    f"{dict(rep_rejected)}")
    else:
        scope_limits.append("rejected not compared: trial recompute "
                            "unavailable (see above)")

    # -- attempts: OBSERVATION + VALIDATION, paired in order --
    obs = [(d.get("procedure"), d.get("observed"))
           for _s, d in _data("OBSERVATION")]
    val = [(d.get("procedure"), d.get("validation"))
           for _s, d in _data("VALIDATION")]
    compared.append("attempts")
    derived_attempts = []
    if len(obs) != len(val):
        _defect(f"attempt mismatch: {len(obs)} OBSERVATION events but "
                f"{len(val)} VALIDATION events")
    else:
        for (oproc, observed), (vproc, validation) in zip(obs, val):
            if oproc != vproc:
                _defect(f"attempt mismatch: OBSERVATION names {oproc!r} "
                        f"but the paired VALIDATION names {vproc!r}")
                continue
            entry = {"procedure": oproc, "native": _native(oproc),
                     "observed": observed, "validation": validation}
            if latest_per_pair is not None:
                rec = latest_per_pair.get((task_id, oproc))
                if rec is None:
                    _defect(f"attempt {oproc}: no committed experience "
                            f"record for ({task_id}, {oproc})")
                else:
                    entry["qualification_after"] = rec.get("qual")
            derived_attempts.append(entry)
    rep_attempts = list(report.get("attempts") or [])
    if latest_per_pair is not None:
        if derived_attempts != rep_attempts:
            _defect(f"attempts mismatch: re-derived {derived_attempts} "
                    f"vs report {rep_attempts}")
    else:
        scope_limits.append("qualification_after not checked: no "
                            "latest_per_pair supplied")
        stripped = [{k: v for k, v in a.items()
                     if k != "qualification_after"} for a in rep_attempts]
        if derived_attempts != stripped:
            _defect(f"attempts mismatch (ignoring qualification_after): "
                    f"re-derived {derived_attempts} vs report {stripped}")

    # -- information_requests: compare the trace-derived entries --
    rep_reqs = list(report.get("information_requests") or [])
    compared.append("information_requests")
    if derived_reqs != rep_reqs:
        _defect(f"information_requests mismatch: re-derived "
                f"{derived_reqs} vs report {rep_reqs}")
    # Operational INFORMATION_REQUEST rows corroborate the
    # non-replay requests (the replay path writes no such row).
    op_reqs = [data for _s, kind, _t, _st, data in raw_rows
               if kind == "INFORMATION_REQUEST"]
    non_replay = [r for r in rep_reqs if r.get("source") != "pathway-replay"]
    if len(op_reqs) != len(non_replay):
        _defect(f"operational INFORMATION_REQUEST rows: {len(op_reqs)} "
                f"vs {len(non_replay)} non-replay report requests")
    else:
        for op in op_reqs:
            if op not in non_replay:
                _defect(f"operational INFORMATION_REQUEST row {op} "
                        f"matches no report request")

    # -- unattempted + pathway_cost: recomputed as run() does --
    seen = {}
    for p in derived_proposed:
        if p["procedure"] in seen:
            seen[p["procedure"]]["sources"].append(p.get("source"))
            continue
        seen[p["procedure"]] = {"procedure": p["procedure"],
                                "native": p.get("native"),
                                "sources": [p.get("source")],
                                "rank": p.get("rank")}
    attempted = {a["procedure"] for a in derived_attempts}
    rejected = {proc for proc, _r in derived_rejected}
    resolved = report.get("state") == "RESOLVED"
    derived_unatt = []
    for proc, entry in seen.items():
        if proc in attempted or proc in rejected:
            continue
        reason = ("not reached: an earlier candidate resolved the task"
                  if resolved else
                  "not reached: the run ended before this candidate")
        derived_unatt.append({**entry, "reason": reason})
    compared.append("unattempted")
    if derived_unatt != list(report.get("unattempted") or []):
        _defect(f"unattempted mismatch: re-derived {derived_unatt} vs "
                f"report {report.get('unattempted')}")
    derived_cost = {"n_proposed": len(derived_proposed),
                    "n_proposed_procedures": len(seen),
                    "n_attempted": len(derived_attempts),
                    "n_rejected": sum(derived_rejected.values()),
                    "n_unattempted": len(derived_unatt)}
    compared.append("pathway_cost")
    if derived_cost != dict(report.get("pathway_cost") or {}):
        _defect(f"pathway_cost mismatch: re-derived {derived_cost} vs "
                f"report {report.get('pathway_cost')}")

    # -- abstention: TASK_END + ESCALATION events --
    ends = _data("TASK_END")
    compared.append("abstention")
    if not ends:
        _defect("no TASK_END event in the trace")
    else:
        end = ends[-1][1]
        if end.get("outcome") != report.get("state"):
            _defect(f"TASK_END outcome {end.get('outcome')!r} != report "
                    f"state {report.get('state')!r}")
        if end.get("stage") != report.get("stage"):
            _defect(f"TASK_END stage {end.get('stage')!r} != report stage "
                    f"{report.get('stage')!r}")
        if end.get("selected") != report.get("selected"):
            _defect(f"TASK_END selected {end.get('selected')!r} != report "
                    f"selected {report.get('selected')!r}")
    esc = _data("ESCALATION")
    for _s, d in esc:
        reason = d.get("reason") or ""
        if reason in _ESCALATION_STATE:
            if _ESCALATION_STATE[reason] != report.get("state"):
                _defect(f"ESCALATION reason {reason!r} belongs to state "
                        f"{_ESCALATION_STATE[reason]!r} but report state "
                        f"is {report.get('state')!r}")
        elif reason.startswith("trial not allowed by profile"):
            if report.get("state") != "UNSOLVED":
                _defect(f"ESCALATION reason {reason!r} belongs to "
                        f"UNSOLVED but report state is "
                        f"{report.get('state')!r}")
        else:
            _defect(f"unrecognised ESCALATION reason {reason!r}")
    if report.get("state") == "ESCALATED" and not esc:
        _defect("report state ESCALATED with no ESCALATION event")

    # -- policy disposition: ADMISSION events --
    admissions = _data("ADMISSION")
    compared.append("policy")
    policies = {d.get("policy") for _s, d in admissions}
    if len(policies) > 1:
        _defect(f"ADMISSION events span multiple policy versions: "
                f"{sorted(policies)}")
    if policy is not None and policies and policies != {policy.version}:
        _defect(f"ADMISSION policy {sorted(policies)} != harness policy "
                f"{policy.version!r}")
    obs_seqs = {}
    for seq, d in _data("OBSERVATION"):
        obs_seqs.setdefault(d.get("procedure"), seq)
    for proc, _observed in obs:
        prior = [(s, d) for s, d in admissions
                 if d.get("procedure") == proc and s < obs_seqs[proc]]
        if not prior:
            _defect(f"attempt {proc} has no preceding ADMISSION event")
        elif prior[-1][1].get("status") != "admitted":
            _defect(f"attempt {proc} has ADMISSION status "
                    f"{prior[-1][1].get('status')!r}, not 'admitted'")

    return {"ok": not defects, "defects": defects,
            "compared": compared, "scope_limits": scope_limits}


def _first_diff(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return {"index": i, "re-derived": x, "report": y}
    if len(a) != len(b):
        return {"lengths": (len(a), len(b))}
    return None


# ------------------------------------------------------------------
# Integrity: no false success, no prohibited execution
# ------------------------------------------------------------------

def check_integrity(report, events, task_dict, policy):
    """Integrity for one run, from report + trace + registry
    (never from the criteria — the correct procedure is NOT
    consulted here; the caller passes the report only). The
    false-success check needs the correct procedure, so it is
    done by score_case(); this function checks the execution
    rule: every attempt has a preceding ADMISSION event in
    this execution, the procedure was not policy-forbidden,
    and its hard preconditions were satisfied under the task
    conditions as known after all observations (task
    conditions + every observed evidence value). Returns
    {"ok", "violations"}."""
    violations = []
    admissions = [(seq, d) for seq, ev in events
                  if ev["event_type"] == "ADMISSION"
                  for d in [ev.get("data") or {}]]
    obs_seqs = {}
    for seq, ev in events:
        if ev["event_type"] == "OBSERVATION":
            obs_seqs.setdefault((ev.get("data") or {}).get("procedure"), seq)
    # Conditions as known after all observations.
    known = dict(task_dict.get("conditions") or {})
    unknown = [c for c in task_dict.get("unknown_conditions") or []]
    for req in report.get("information_requests") or []:
        for _cond, obs in (req.get("observation") or {}).items():
            if obs is not None:
                known[obs.get("condition")] = obs.get("value")
                if obs.get("condition") in unknown:
                    unknown.remove(obs.get("condition"))
    for att in report.get("attempts") or []:
        proc = att["procedure"]
        prior = [(s, d) for s, d in admissions
                 if d.get("procedure") == proc
                 and s < obs_seqs.get(proc, float("inf"))]
        if not prior:
            violations.append(f"attempt {proc}: no preceding ADMISSION "
                              f"event in this execution")
            continue
        if prior[-1][1].get("status") != "admitted":
            violations.append(f"attempt {proc}: ADMISSION status "
                              f"{prior[-1][1].get('status')!r}")
        if policy is not None and not policy.decision(proc):
            violations.append(f"attempt {proc}: forbidden by policy "
                              f"{policy.version}")
        entry = reg_get(proc)
        status = check_hard_preconditions(
            entry, known, tuple(unknown),
            tuple(task_dict.get("contradictory_conditions") or ()))
        if status != "satisfied":
            violations.append(
                f"attempt {proc}: hard preconditions {status} under "
                f"conditions as known after all observations {known}")
    return {"ok": not violations, "violations": violations}


# ------------------------------------------------------------------
# Scoring (the frozen §5 endpoint, applied per case)
# ------------------------------------------------------------------

def _evidence_grounded(report, events):
    """Is there an answered information request with an
    observation, backed by EVIDENCE_EXPECTATION + EVIDENCE_
    OBSERVED events in this execution, with the observation
    preceding the end of the run?"""
    exp_ids = { (ev.get("data") or {}).get("expectation_id")
                for _s, ev in events
                if ev["event_type"] == "EVIDENCE_EXPECTATION"}
    obs = [(seq, (ev.get("data") or {}).get("expectation_id"))
           for seq, ev in events
           if ev["event_type"] == "EVIDENCE_OBSERVED"]
    obs_ids = {eid for _s, eid in obs}
    if not (exp_ids & obs_ids):
        return False
    answered = any(req.get("answered") and req.get("observation")
                   for req in report.get("information_requests") or [])
    if not answered:
        return False
    ends = [(seq, ev) for seq, ev in events
            if ev["event_type"] == "TASK_END"]
    if not ends:
        return False
    return all(seq < ends[-1][0] for seq, _eid in obs)


def _intended_b(case, arm_b):
    """Does arm B behave as the case's intended class requires?
    Returns (True, "") or (False, reason). This is the
    freezing criterion re-verified at runtime: A-not-resolving
    is checked separately (see score_case)."""
    if case["kind"] == "different_answer":
        if arm_b["state"] == "RESOLVED":
            return False, ("arm B resolved a different-answer case "
                           "(U10 must never fire for it)")
        if arm_b["state"] != "UNSOLVED":
            return False, (f"arm B state {arm_b['state']} != UNSOLVED "
                           f"for a different-answer case")
        if arm_b["information_requests"]:
            return False, ("arm B made an information request on a "
                           "different-answer case")
        return True, ""
    if case["kind"] == "missing_evidence":
        if case["correct"] is not None:
            if arm_b["state"] != "RESOLVED" \
                    or arm_b["selected_native"] != case["correct"]:
                return False, ("arm B did not resolve a missing-evidence "
                               "resolve case to its correct procedure")
            return True, ""
        reqs = [r for r in arm_b["information_requests"]
                if r.get("outcome") == "justified_abstention"]
        if arm_b["state"] != "ESCALATED" or not reqs \
                or arm_b["n_attempts"] != 0:
            return False, ("arm B did not justify abstention (ESCALATED, "
                           "0 attempts) on a missing-evidence abstention "
                           "case")
        return True, ""
    return True, ""


def score_case(case, report, events, parity, integrity, arm_b=None):
    """Score one case in one arm. A parity defect excludes the
    case/arm from scoring (the defect is recorded, never
    re-scored from the live report alone). Returns the per-case
    record."""
    correct = case["correct"]
    selected_native = _native(report.get("selected"))
    resolved_correct = (report.get("state") == "RESOLVED"
                        and correct is not None
                        and selected_native == correct)
    grounded = _evidence_grounded(report, events)
    endpoint_resolution = bool(resolved_correct and grounded)
    endpoint_abstention = bool(
        any(r.get("outcome") == "justified_abstention"
            for r in report.get("information_requests") or [])
        and any(ev["event_type"] == "EVIDENCE_OBSERVED"
                for _s, ev in events))
    endpoint_counts = endpoint_resolution or endpoint_abstention
    false_success = bool(
        report.get("state") == "RESOLVED"
        and (correct is None or selected_native != correct))
    wrong_attempts = sum(
        1 for a in report.get("attempts") or []
        if _native(a.get("procedure")) != correct)
    # Known design gap (brief): a replayed observation that
    # contradicts every procedure yields UNSOLVED via the
    # normal escalate_because path, NOT outcome
    # "justified_abstention". It is NOT endpoint-counted, NOT
    # a false success — it is flagged here, not manufactured.
    replay_gap = bool(
        report.get("state") == "UNSOLVED"
        and any(r.get("source") == "pathway-replay"
                and r.get("answered")
                for r in report.get("information_requests") or [])
        and any((ev.get("data") or {}).get("reason")
                == "every pointed-at procedure blocked"
                for _s, ev in events
                if ev["event_type"] == "ESCALATION"))
    case_defect = None
    if arm_b is not None:
        ok_b, reason_b = _intended_b(case, arm_b)
        if not ok_b:
            case_defect = reason_b
    scored = bool(parity["ok"] and case_defect is None)
    return {
        "task_id": case["task_id"], "kind": case["kind"],
        "group": case["group"], "correct": correct,
        "evidence_value": case["evidence_value"],
        "state": report.get("state"), "stage": report.get("stage"),
        "selected": report.get("selected"),
        "selected_native": selected_native,
        "attempts": [a["procedure"] for a in report.get("attempts") or []],
        "n_attempts": len(report.get("attempts") or []),
        "wrong_attempts": wrong_attempts,
        "n_evidence_requests": len(report.get("information_requests") or []),
        "information_requests": [
            {"missing": r.get("missing"), "source": r.get("source"),
             "answered": r.get("answered"),
             "outcome": r.get("outcome")}
            for r in report.get("information_requests") or []],
        "resolved_correct": resolved_correct,
        "evidence_grounded": grounded,
        "endpoint_counts": bool(endpoint_counts and scored),
        "endpoint_kind": ("resolution" if endpoint_resolution
                          else "abstention" if endpoint_abstention
                          else None),
        "raw_correct_resolution": resolved_correct,
        "false_success": false_success,
        "replay_contradiction_unsolved": replay_gap,
        "parity_ok": parity["ok"], "parity_defects": parity["defects"],
        "parity_scope_limits": parity["scope_limits"],
        "integrity_ok": integrity["ok"],
        "integrity_violations": integrity["violations"],
        "case_defect": case_defect,
        "scored": scored,
        "excluded_reason": (None if scored else
                            ("parity defect" if not parity["ok"]
                             else f"case defect: {case_defect}")),
    }


def _summarize_arm(cases):
    scored = [c for c in cases.values() if c["scored"]]
    n = len(scored)
    endpoint = sum(1 for c in scored if c["endpoint_counts"])
    return {
        "n_cases": len(cases), "n_scored": n,
        "n_excluded": len(cases) - n,
        "excluded": sorted(t for t, c in cases.items() if not c["scored"]),
        "endpoint": endpoint,
        "endpoint_resolutions": sum(
            1 for c in scored
            if c["endpoint_counts"] and c["endpoint_kind"] == "resolution"),
        "endpoint_abstentions": sum(
            1 for c in scored
            if c["endpoint_counts"] and c["endpoint_kind"] == "abstention"),
        "raw_correct_resolutions": sum(
            1 for c in scored if c["raw_correct_resolution"]),
        "raw_correct_resolutions_all": sum(
            1 for c in cases.values() if c["raw_correct_resolution"]),
        "wrong_attempts": sum(c["wrong_attempts"] for c in scored),
        "wrong_attempts_per_task": (
            sum(c["wrong_attempts"] for c in scored) / n if n else None),
        "evidence_requests_total": sum(
            c["n_evidence_requests"] for c in scored),
        "evidence_requests_mean": (
            sum(c["n_evidence_requests"] for c in scored) / n if n else None),
        "false_successes": sum(1 for c in cases.values()
                               if c["false_success"]),
        "prohibited_executions": sum(
            1 for c in cases.values() if not c["integrity_ok"]),
        "integrity_violations": [v for c in cases.values()
                                 for v in c["integrity_violations"]],
        "integrity_ok": all(c["integrity_ok"] for c in cases.values())
                        and not any(c["false_success"]
                                    for c in cases.values()),
        "parity_blocked": sorted(t for t, c in cases.items()
                                 if not c["parity_ok"]),
        "parity_defects": {t: c["parity_defects"] for t, c in cases.items()
                           if not c["parity_ok"]},
        "case_defects": {t: c["case_defect"] for t, c in cases.items()
                         if c["case_defect"]},
        "replay_contradiction_unsolved": sum(
            1 for c in cases.values() if c["replay_contradiction_unsolved"]),
    }


# ------------------------------------------------------------------
# The matched four-arm experiment
# ------------------------------------------------------------------

def _run_arm(arm, arm_root, source):
    """Reopen a copied trained+learned root and run the 20
    cases in one arm. A = no evidence source, no provider
    (current behavior); B = evidence source, no provider; C =
    evidence source + PathwayReplayProvider; D = evidence
    source + ShuffledPathwayProvider (seed 20261118)."""
    if arm == "A":
        h = Harness(root=arm_root, tasks=ALL_TASKS)
    else:
        h = Harness(root=arm_root, tasks=ALL_TASKS, evidence_source=source)
    try:
        for t in TRAINING:
            h.add_task(t)
        for x in TRANSFER_TASKS.values():
            h.add_task(x)
        store = PathwayStore(h.pipe.big)
        association = None
        if arm == "C":
            h.pipe.pathway_provider = PathwayReplayProvider(store)
        elif arm == "D":
            provider = ShuffledPathwayProvider(
                store, [_public_task(c) for c in CASES], seed=SHUFFLE_SEED)
            h.pipe.pathway_provider = provider
            association = dict(provider.association)
        raw = {}
        for case in CASES:
            task_dict = _public_task(case)
            rep = h.pipe.run(task_dict)
            latest = h.pipe.experiences.latest_per_pair()
            events = trace_events(h.pipe.trace, case["task_id"],
                                  rep["execution_id"])
            parity = parity_check(
                rep, h.pipe.trace, task_dict=task_dict, policy=h.policy,
                trial_candidates_fn=lambda task, _p=h.pipe: (
                    _p._trial_candidates(task)),
                latest_per_pair=latest)
            integrity = check_integrity(rep, events, task_dict, h.policy)
            raw[case["task_id"]] = (case, rep, events, parity, integrity)
        return {"raw": raw, "association": association,
                "store": {"n_pathways": store.count(),
                          "pathway_ids": [r["pathway_id"]
                                          for r in store.all()]}}
    finally:
        h.close()


def run_pathway_experiment(root=None, save_path=None):
    """Train ONCE, run the pathway-learning phase on that root,
    then copytree per arm and run the same 20 cases in each
    arm. Returns the full result dict (JSON-serialisable) and
    saves it to pathway001_results.json (project root) unless
    save_path is None and the caller passes save_path=False
    to skip writing.

    Primary endpoint (frozen, design §5): correct
    evidence-grounded resolution or justified abstention.
    A resolution counts only when selected == correct (native
    comparison) AND the decision is grounded in the recorded
    evidence (expectation + observation in the trace, an
    answered information request, resolution after it). An
    abstention counts only when outcome == "justified_
    abstention" with the observation in the trace. Integrity
    (no false success, no prohibited execution): a single
    violation fails the arm's result whatever the endpoint
    says. A parity defect excludes that task/arm from scoring.
    """
    tmp = root or tempfile.mkdtemp(prefix="pw1-")
    own_tmp = root is None
    base_root = os.path.join(tmp, "root")
    # The pathway record's "root" is provenance, not a path
    # the store re-opens: the experiment tmpdir basename (short
    # — the compact record must fit the 512-char bucket).
    root_label = os.path.basename(tmp)
    source = FrozenEvidenceSource(
        {(t["task_id"], DISCRIMINATOR): t["evidence_value"]
         for t in ALL_TASKS})
    try:
        # Train once (arm-B conditions: the evidence source is
        # present but training tasks carry known firmware, so
        # it is never consulted during training).
        h = Harness(root=base_root, tasks=ALL_TASKS, evidence_source=source)
        try:
            h.train_baseline()
            # Pathway-learning phase, same root: each learning
            # task runs under arm-B conditions and its pathway
            # is recorded into the store. The learning runs'
            # experience writes (trial success -> CANDIDATE)
            # are part of the shared root state — identical
            # for all arms.
            store = PathwayStore(h.pipe.big)
            recorder = PathwayRecorder()
            learning = {}
            for t in LEARNING_TASKS:
                task_dict = _public_task(t)
                rep = h.pipe.run(task_dict)
                events = trace_events(h.pipe.trace, t["task_id"],
                                      rep["execution_id"])
                record = recorder.record(
                    task_dict, rep, events, root=root_label,
                    existing_ids={r["pathway_id"] for r in store.all()})
                store.write(record)
                learning[t["task_id"]] = {
                    "pathway_id": record["pathway_id"],
                    "signature": record["signature"],
                    "family": record["family"],
                    "discriminators": record["discriminators"],
                    "state": rep["state"], "stage": rep["stage"],
                    "selected": rep["selected"],
                    "n_attempts": len(rep["attempts"]),
                    "evidence_requests": [
                        {"missing": r.get("missing"),
                         "outcome": r.get("outcome")}
                        for r in rep["information_requests"]],
                    "outcome": record["outcome"]}
            store_summary = {
                "n_pathways": store.count(),
                "pathway_ids": [r["pathway_id"] for r in store.all()],
                "records": [{
                    "pathway_id": r["pathway_id"],
                    "source_task_id": r["source_task_id"],
                    "signature": r["signature"], "family": r["family"],
                    "tags": r["tags"],
                    "discriminators": r["discriminators"],
                    "outcome": r["outcome"]} for r in store.all()]}
        finally:
            h.close()
        arm_raw = {}
        arm_meta = {}
        for arm in ("A", "B", "C", "D"):
            arm_root = os.path.join(tmp, f"arm-{arm}")
            shutil.copytree(base_root, arm_root)
            out = _run_arm(arm, arm_root, source)
            arm_raw[arm] = out["raw"]
            arm_meta[arm] = {"association": out["association"],
                             "store": out["store"]}
        # Score: the freezing criterion is re-verified from
        # arms A and B (A must not resolve any case; B must
        # behave as the case's intended class requires). A
        # case that fails it after freezing is a case defect
        # and is excluded from scoring in every arm.
        arms = {}
        for arm in ("A", "B", "C", "D"):
            cases = {}
            for task_id, (case, rep, events, parity, integrity) in \
                    arm_raw[arm].items():
                # The freezing criterion is re-verified from arms
                # A and B for EVERY arm (including B itself): a
                # case that fails it is a case defect and is
                # excluded from scoring in every arm.
                b_case, b_rep, _b_ev, _b_par, _b_int = \
                    arm_raw["B"][task_id]
                arm_b = {
                    "state": b_rep["state"],
                    "selected_native": _native(b_rep.get("selected")),
                    "information_requests":
                        b_rep.get("information_requests") or [],
                    "n_attempts": len(b_rep.get("attempts") or [])}
                # Arm-A non-resolution is part of the freezing
                # criterion too.
                a_rep = arm_raw["A"][task_id][1]
                case_defect_extra = None
                if a_rep["state"] == "RESOLVED":
                    case_defect_extra = ("arm A resolved a case that "
                                         "was frozen as unresolvable "
                                         "without evidence/pathways")
                scored_case = score_case(case, rep, events, parity,
                                         integrity, arm_b=arm_b)
                if case_defect_extra is not None \
                        and scored_case["case_defect"] is None:
                    scored_case["case_defect"] = case_defect_extra
                    scored_case["scored"] = False
                    scored_case["endpoint_counts"] = False
                    scored_case["excluded_reason"] = (
                        f"case defect: {case_defect_extra}")
                cases[task_id] = scored_case
            arms[arm] = {"cases": cases,
                         "summary": _summarize_arm(cases),
                         "association": arm_meta[arm]["association"],
                         "store": arm_meta[arm]["store"]}
        result = {
            "experiment": "PATHWAY-001 matched four-arm",
            "seed": SHUFFLE_SEED,
            "endpoint_definition": (
                "correct evidence-grounded resolution or justified "
                "abstention (design §5, frozen before evaluation): "
                "a resolution counts only when selected == correct "
                "(native) AND the trace holds the expectation + "
                "observation and the run resolved after it; an "
                "abstention counts only when outcome == "
                "'justified_abstention' with the observation in the "
                "trace. Raw correct resolutions are reported "
                "separately; resolutions without evidence do not "
                "count toward the endpoint."),
            "cases": [{k: case[k] for k in
                       ("task_id", "signature", "family", "tags", "kind",
                        "group", "correct", "evidence_value")}
                      for case in CASES],
            "learning_tasks": [{k: t[k] for k in
                                ("task_id", "signature", "family", "tags",
                                 "correct", "evidence_value")}
                               for t in LEARNING_TASKS],
            "learning": learning,
            "store": store_summary,
            "arms": arms,
            "notes": [
                "Known design gap (brief): a replayed observation "
                "contradicting every procedure yields UNSOLVED via "
                "the normal escalate_because path, NOT outcome "
                "'justified_abstention'. Such runs are scored as "
                "NOT endpoint-counted and NOT a false success, and "
                "are flagged replay_contradiction_unsolved in the "
                "per-case records.",
                "Arm-D shuffle limit: every pathway in a group "
                "names the same single discriminator ('firmware'), "
                "so the shuffle changes which pathway is offered, "
                "not which discriminator is asked. D matching C is "
                "the association-independence finding.",
                "No case was re-tuned after freezing; every "
                "observation, failed pathway and correction is "
                "preserved in the per-case records.",
            ],
        }
    finally:
        if own_tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    if save_path is not False:
        path = save_path or DEFAULT_RESULTS_PATH
        with open(path, "w") as f:
            json.dump(result, f, indent=2, sort_keys=True)
    return result
