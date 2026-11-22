"""PATHWAY-002 — matched four-arm experiment: selector + transferable
method vs selector alone vs shuffled method vs legacy baseline.

Own fixtures, own scoring, own parity extension. pathway_experiment.py
(PATHWAY-001) is NOT modified and its outputs are NOT touched: this
module imports its parity_check, check_integrity and score_case and
reuses them for the common fields, then extends them.

What this experiment measures (design §5): does arm C (selector +
correctly retrieved method) outperform arm B (selector, no method)
and arm D (selector + shuffled method associations) on held-out
tasks, without safety regressions or unacceptable additional
evidence cost? The answer is READ OFF the numbers below — it is
not assumed here.

Two measured facts from probing (2026-11-18, before freezing)
constrain what this experiment can claim; both are recorded here,
not worked around:

1. KIND IS NEVER SELECTED. The pipeline's direct-U10 selector
   hypotheses are restricted to the procedures blocked by an
   UNKNOWN HARD precondition (P1/P2 only), so the soft `kind`
   precondition never enters direct-path selection; and a pathway
   can only replay discriminators a real learning run recorded,
   and no learning run ever records `kind` (for the same reason).
   The probe verified this even with kind values present in the
   evidence table: every learning and held-out run selected
   `firmware` only. The design's second discriminator (`kind`,
   SOFT) is therefore UNEXERCISED by the pipeline as built — the
   experiment measures the firmware discriminator only, and the
   cost table keeps a (never incurred) kind price of 3 to record
   the intent. No fixture was invented to force kind in: a
   hand-built pathway would be false provenance.

2. THE FROZEN PARITY CHECKER PREDATES THE FLAG. parity_check
   (PATHWAY-001) was written when the replay path could only
   record "resumed"/"unanswered" and the escalate_because branch
   emitted exactly one ESCALATION reason. The PATHWAY-002 pipeline
   (§4) adds the replay-contradiction abstention (flag on): the
   replay request's outcome becomes "justified_abstention" with a
   "contradicted" list, an operational INFORMATION_REQUEST row is
   appended for a replay request, and the escalate_because
   rejections are appended under the new ESCALATION reason
   "evidence contradicts every pointed-at procedure". The frozen
   checker re-derives none of that, so flag-on abstention runs
   report four raw defects; and a selector run that selects
   NOTHING (evidence unavailable) reports one raw defect, because
   the checker re-derives the unanswered request from an
   expectation that was never frozen. parity_check002 (below)
   re-derives those two dispositions INDEPENDENTLY from the trace
   (ESCALATION events, observations vs hypotheses, the
   EVIDENCE_SELECTION records, the trial recompute) and reconciles
   a raw defect ONLY when the re-derivation corroborates the
   report exactly. Reconciled defects are preserved verbatim in
   the per-case record alongside the corroboration — nothing is
   silently re-scored. Any defect that does not match one of the
   two documented patterns stays blocking and excludes the
   case/arm.
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
from .pathway_experiment import (_summarize_arm, check_integrity,
                                 parity_check, score_case)
from .registry import BY_NATIVE, procedure_identity
from .registry import get as reg_get
from .selection import MeasurementSelector
from .trace import trace_events

SHUFFLE_SEED = 20261118
SELECTION_SEED = 20261118  # Reserved; selection is deterministic
# (distinguishing power desc, cost asc, condition name asc) and
# uses no randomness, so no tie-breaking seed is consumed.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RESULTS_PATH = os.path.join(PROJECT_ROOT, "pathway002_results.json")

# The only discriminator the pipeline actually measures (see the
# module docstring, finding 1): firmware, HARD (P1=v1, P2=v2).
DISCRIMINATOR = "firmware"
SOURCE_ID = "frozen-evidence"

# Frozen cost table: one firmware measurement costs 1; a kind
# measurement would cost 3 (never incurred — kind is never
# selected; see the module docstring, finding 1).
COSTS = {(SOURCE_ID, "firmware"): 1, (SOURCE_ID, "kind"): 3}

# Cost acceptability rule (frozen): C's mean measurement cost per
# endpoint-counted case must be <= 1.5x B's mean cost per
# endpoint-counted case. If C's endpoint gain over B is zero, the
# cost comparison is moot (no advantage to justify any cost).
COST_RATIO_LIMIT = 1.5


# ------------------------------------------------------------------
# FREEZE — 2026-11-18
# ------------------------------------------------------------------
# Frozen before the first evaluation run, and not changed after
# it. Frozen: the 32 cases below (task ids, signatures, families,
# tags, correct procedures, evidence values, kinds), the 30
# learning tasks, the ground-truth evidence table rule (a
# (task_id, "firmware") entry for every task whose evidence_value
# is not None; unavailable_evidence held-out tasks carry NO entry
# — that omission IS the fixture), the cost table above, the
# seeds above, the scoring rules (score_case from PATHWAY-001,
# same endpoint shape), and the primary endpoint (correct
# evidence-grounded resolution or justified abstention, design
# §5). Any shortcoming found after the first evaluation run is
# recorded as a limitation, never fixed by re-running with
# adjusted cases, arms, or scoring.
#
# Case classes (all six, counts frozen):
#   different_answer          10  (5 groups x 2)
#   missing_evidence           5  (5 groups x 1 resolve)
#   abstention_required        5  (5 groups x 1 abstain)
#   contradictory_observations 4  (2 groups x 2)
#   unavailable_evidence       4  (2 groups x 2)
#   misleading_prior           4  (2 groups x 2)
# Total 32 cases; 30 learning tasks (2 per group except the
# misleading_prior groups, which have exactly ONE learning task
# each — the single opposite-firmware pathway is the fixture).
#
# Class mechanisms (all measured in probing before freezing):
# - different_answer: tags point at P5/P7 (route/detour/ground),
#   never at P1/P2, so arm B has no U10 path and must trial; arms
#   C/D replay firmware and resolve. Same pattern as PATHWAY-001.
# - missing_evidence: tags point at P1/P2 (valve); evidence v1/v2;
#   every evidence arm resolves in 1 attempt.
# - abstention_required: tags point at P1/P2; evidence v3
#   contradicts both. Arm B justifies abstention via the direct
#   U10 path; arms C/D via the replay path + the
#   replay_contradiction_abstention flag (parity pattern R1).
# - contradictory_observations: different-answer tags, evidence
#   v3. The replayed observation contradicts the firmware
#   hypotheses, but the pointed-at procedures (P5/P7, kind only)
#   are NOT contradicted, so no abstention is manufactured: every
#   arm ends UNSOLVED after trial, with zero false resolutions.
# - unavailable_evidence: tags point at P1/P2; the firmware entry
#   is ABSENT from the evidence table. The selector selects
#   nothing (available() False); the run escalates unanswered
#   with 0 attempts in every evidence arm (parity pattern R2).
# - misleading_prior: the group's ONLY learning pathway comes
#   from a task whose correct procedure is the OPPOSITE firmware
#   (P2/v2 pathway for P1/v1 held-out tasks and vice versa). Arm
#   C replays it; the fresh answer must win over the stored
#   method — the run must resolve to ITS correct procedure, never
#   the pathway's. Arm D gets no replay at all for the second
#   task in each group (a single pathway cannot be shuffled to
#   two tasks), so it falls back to the direct U10 path.
# ------------------------------------------------------------------

_VALVE_TAGS = ["valve", "stuck", "seal", "pressure"]


def _case(task_id, signature, family, tags, correct, evidence_value, kind,
          group=None):
    return {"task_id": task_id, "signature": signature, "family": family,
            "tags": list(tags), "conditions": {},
            "unknown_conditions": ("firmware",),
            "requested_operation": "repair",
            "provenance": ("int:pathway-002",), "correct": correct,
            "evidence_value": evidence_value, "kind": kind,
            "group": group or signature}


CASE_COUNTS = {"different_answer": 10, "missing_evidence": 5,
               "abstention_required": 5, "contradictory_observations": 4,
               "unavailable_evidence": 4, "misleading_prior": 4}

CASES = []

# different_answer: 5 groups x 2 (P1/v1, P2/v2). Tags share words
# with P7/P5 descriptions (route/ground/detour/probe) but NOT
# with P1/P2 descriptions (no valve/reseat/firmware/manual).
_DA_SPECS = [
    ("pw2-da-b", "da2_blocked_a", "blocked",
     ["route", "blocked", "ground", "detour"]),
    ("pw2-da-s", "da2_slope_a", "slope",
     ["slope", "ground", "route", "detour"]),
    ("pw2-da-n", "da2_narrow_a", "narrow",
     ["narrow", "ground", "route", "probe"]),
    ("pw2-da-p", "da2_pump_a", "pump",
     ["pump", "ground", "route", "detour"]),
    ("pw2-da-e", "da2_sensor_a", "sensor",
     ["sensor", "ground", "route", "detour"]),
]
for _prefix, _sig, _fam, _tags in _DA_SPECS:
    CASES.append(_case(_prefix + "-p1", _sig, _fam, _tags,
                       "P1", "v1", "different_answer"))
    CASES.append(_case(_prefix + "-p2", _sig, _fam, _tags,
                       "P2", "v2", "different_answer"))

# missing_evidence (resolve) + abstention_required: 5 groups x 2,
# one resolve task and one abstention task per group. Tags point
# at P1/P2 (valve/stuck/seal/pressure).
for _i in range(5):
    _letter = chr(97 + _i)
    _sig = f"p2_me_valve_{_letter}"
    CASES.append(_case(f"pw2-me-{_letter}-res", _sig, "valve",
                       _VALVE_TAGS, "P1" if _i % 2 == 0 else "P2",
                       "v1" if _i % 2 == 0 else "v2",
                       "missing_evidence"))
    CASES.append(_case(f"pw2-me-{_letter}-abs", _sig, "valve",
                       _VALVE_TAGS, None, "v3", "abstention_required"))

# contradictory_observations: 2 groups x 2, different-answer
# tags, evidence v3, no correct procedure.
_CT_SPECS = [
    ("pw2-ct-s", "p2_ct_slope_a", "slope",
     ["slope", "ground", "route", "detour"]),
    ("pw2-ct-b", "p2_ct_blocked_a", "blocked",
     ["blocked", "ground", "route", "detour"]),
]
for _prefix, _sig, _fam, _tags in _CT_SPECS:
    CASES.append(_case(_prefix + "-1", _sig, _fam, _tags,
                       None, "v3", "contradictory_observations"))
    CASES.append(_case(_prefix + "-2", _sig, _fam, _tags,
                       None, "v3", "contradictory_observations"))

# unavailable_evidence: 2 groups x 2, valve tags, correct P1/P2,
# evidence_value None — the firmware entry is OMITTED from the
# evidence table (the omission is the fixture).
for _letter in ("a", "b"):
    _sig = f"p2_un_valve_{_letter}"
    CASES.append(_case(f"pw2-un-{_letter}-p1", _sig, "valve",
                       _VALVE_TAGS, "P1", None, "unavailable_evidence"))
    CASES.append(_case(f"pw2-un-{_letter}-p2", _sig, "valve",
                       _VALVE_TAGS, "P2", None, "unavailable_evidence"))

# misleading_prior: 2 groups x 2. Group x holds only a P2/v2
# learning pathway and only P1/v1 held-out tasks; group y the
# mirror image. The replayed method's source procedure is the
# OPPOSITE of the held-out task's correct procedure.
CASES.append(_case("pw2-mp-x-p1", "p2_mp_valve_x", "valve", _VALVE_TAGS,
                   "P1", "v1", "misleading_prior"))
CASES.append(_case("pw2-mp-x-p1b", "p2_mp_valve_x", "valve", _VALVE_TAGS,
                   "P1", "v1", "misleading_prior"))
CASES.append(_case("pw2-mp-y-p2", "p2_mp_valve_y", "valve", _VALVE_TAGS,
                   "P2", "v2", "misleading_prior"))
CASES.append(_case("pw2-mp-y-p2b", "p2_mp_valve_y", "valve", _VALVE_TAGS,
                   "P2", "v2", "misleading_prior"))

assert len(CASES) == sum(CASE_COUNTS.values()) == 32
assert Counter(c["kind"] for c in CASES) == Counter(CASE_COUNTS)

# Learning tasks: 2 per group (P1/v1, P2/v2) except the
# misleading_prior groups, which have exactly ONE each (the
# opposite-firmware pathway). Same (signature, family) as the
# group; tags overlap the group's held-out tasks AND point at
# P1/P2 (valve), firmware unknown. Run under arm-B conditions in
# the learning phase; their pathways are what arms C/D replay.
# Task ids are kept short on purpose (see PATHWAY-001): the
# pathway record's compact form must fit the 512-char bucket.
_LEARNING_SHORT = {"blocked": "b", "slope": "s", "narrow": "n",
                   "pump": "p", "sensor": "e"}
LEARNING_TASKS = []
for _prefix, _sig, _fam, _tags in _DA_SPECS:
    _ltags = ["valve"] + [t for t in _tags if t != _tags[0]][:2]
    _short = _LEARNING_SHORT[_fam]
    LEARNING_TASKS.append(_case(f"da2-{_short}-l1", _sig, _fam, _ltags,
                                "P1", "v1", "learning"))
    LEARNING_TASKS.append(_case(f"da2-{_short}-l2", _sig, _fam, _ltags,
                                "P2", "v2", "learning"))
for _i in range(5):
    _letter = chr(97 + _i)
    _sig = f"p2_me_valve_{_letter}"
    LEARNING_TASKS.append(_case(f"me2-{_letter}-l1", _sig, "valve",
                                _VALVE_TAGS, "P1", "v1", "learning"))
    LEARNING_TASKS.append(_case(f"me2-{_letter}-l2", _sig, "valve",
                                _VALVE_TAGS, "P2", "v2", "learning"))
for _prefix, _sig, _fam, _tags in _CT_SPECS:
    _short = _fam[0]
    _ltags = ["valve", _fam, "ground"]
    LEARNING_TASKS.append(_case(f"ct2-{_short}-l1", _sig, _fam, _ltags,
                                "P1", "v1", "learning"))
    LEARNING_TASKS.append(_case(f"ct2-{_short}-l2", _sig, _fam, _ltags,
                                "P2", "v2", "learning"))
for _letter in ("a", "b"):
    _sig = f"p2_un_valve_{_letter}"
    LEARNING_TASKS.append(_case(f"un2-{_letter}-l1", _sig, "valve",
                                _VALVE_TAGS, "P1", "v1", "learning"))
    LEARNING_TASKS.append(_case(f"un2-{_letter}-l2", _sig, "valve",
                                _VALVE_TAGS, "P2", "v2", "learning"))
# Misleading-prior groups: exactly one learning task each, of the
# OPPOSITE firmware to the group's held-out tasks.
LEARNING_TASKS.append(_case("mp2-x-l1", "p2_mp_valve_x", "valve",
                            _VALVE_TAGS, "P2", "v2", "learning"))
LEARNING_TASKS.append(_case("mp2-y-l1", "p2_mp_valve_y", "valve",
                            _VALVE_TAGS, "P1", "v1", "learning"))

assert len(LEARNING_TASKS) == 30

ALL_TASKS = list(CASES) + list(LEARNING_TASKS)


def build_evidence_table():
    """The frozen ground-truth evidence table: a (task_id,
    "firmware") entry for every task whose evidence_value is not
    None. unavailable_evidence held-out tasks carry NO entry —
    that omission is the fixture. No kind entries exist: no
    pathway or selector path requests kind (module docstring,
    finding 1)."""
    return {(t["task_id"], DISCRIMINATOR): t["evidence_value"]
            for t in ALL_TASKS if t["evidence_value"] is not None}


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
# Parity extension (design §5)
# ------------------------------------------------------------------
# parity_check (PATHWAY-001, frozen) covers the common fields.
# parity_check002 calls it, then additionally checks the
# selection records, the measurement costs, and the dispositions
# — and reconciles, ONLY by independent re-derivation from the
# trace, the two raw-defect patterns the frozen checker cannot
# model (see the module docstring, finding 2):
#   R1 — replay-contradiction abstention (flag on): the replay
#        request's outcome becomes "justified_abstention" with a
#        "contradicted" list, an operational INFORMATION_REQUEST
#        row is appended for a replay request, and the
#        escalate_because rejections are appended under the new
#        ESCALATION reason. Raw defects: rejected, information_
#        requests, operational INFORMATION_REQUEST rows,
#        pathway_cost (four, all from the one cause).
#   R2 — empty selection, unanswered (evidence unavailable):
#        the selector selects nothing, so no expectation is ever
#        frozen, and the checker re-derives the unanswered
#        request without the wrapper keys the report carries.
#        Raw defect: information_requests (one).
# A raw defect is reconciled only when EVERY sub-check below
# passes; anything else stays in blocking_defects and excludes
# the case/arm from scoring. Reconciled defects are preserved
# verbatim in the per-case record — never deleted, never
# silently re-scored.
# ------------------------------------------------------------------

_FLAG_ESCALATION_REASON = "evidence contradicts every pointed-at procedure"
_UNKNOWN_ESCALATION_REASON = "unknown required condition"


def _hard_contradicted(procedure_id, observations):
    """Is this procedure contradicted by the observations? Hard
    only (mirrors Pipeline._procedure_contradicted_by)."""
    entry = reg_get(procedure_id)
    for name, obs in (observations or {}).items():
        req = entry.preconditions.get(name)
        if req is not None and req[1] and obs["value"] != req[0]:
            return True
    return False


def _selection_events(events):
    """[(seq, data)] for the EVIDENCE_SELECTION events."""
    return [(seq, ev.get("data") or {}) for seq, ev in events
            if ev["event_type"] == "EVIDENCE_SELECTION"]


def _check_selection(report, events):
    """The selection records: every selected measurement's cost
    matches the frozen COSTS table, the selected list follows the
    frozen ranking (distinguishing power desc, cost asc,
    condition asc), every selected entry has a matching useful
    evaluation, every evaluation's useful flag is consistent
    with its six dimensions, and every expectation covered by a
    selection is preceded by it (the certify rule). Returns
    {"ok", "defects", "n_selections", "measurement_cost",
    "cost_by_condition", "selected_conditions"}."""
    defects = []
    sels = _selection_events(events)
    total = 0
    by_condition = {}
    selected_conditions = []
    for seq, data in sels:
        selected = data.get("selected") or []
        evaluations = data.get("evaluations") or []
        by_key = {(e.get("condition"), e.get("source_id")): e
                  for e in evaluations if isinstance(e, dict)}
        for entry in selected:
            cond = entry.get("condition")
            src = entry.get("source_id")
            expected_cost = COSTS.get((src, cond), 1)
            if entry.get("cost") != expected_cost:
                defects.append(
                    f"selection {data.get('selection_id')}: selected "
                    f"{cond} from {src} costs {entry.get('cost')!r}, "
                    f"frozen table says {expected_cost!r}")
            total += entry.get("cost") or 0
            by_condition[cond] = by_condition.get(cond, 0) + (
                entry.get("cost") or 0)
            selected_conditions.append(cond)
            ev = by_key.get((cond, src))
            if ev is None:
                defects.append(
                    f"selection {data.get('selection_id')}: selected "
                    f"{cond} from {src} has no evaluation")
            elif not ev.get("useful"):
                defects.append(
                    f"selection {data.get('selection_id')}: selected "
                    f"{cond} from {src} is not useful in its evaluation")
        ranked = sorted(selected, key=lambda e: (
            -e.get("distinguishing_power", 0), e.get("cost", 0),
            e.get("condition", "")))
        if [e.get("condition") for e in ranked] != \
                [e.get("condition") for e in selected]:
            defects.append(
                f"selection {data.get('selection_id')}: selected order "
                f"{[e.get('condition') for e in selected]} does not "
                f"follow the frozen ranking "
                f"{[e.get('condition') for e in ranked]}")
        for ev in evaluations:
            want_useful = (ev.get("distinguishing_power", 0) >= 2
                           and ev.get("available")
                           and ev.get("admissible")
                           and not ev.get("already_known"))
            if bool(ev.get("useful")) != bool(want_useful):
                defects.append(
                    f"selection {data.get('selection_id')}: evaluation "
                    f"for {ev.get('condition')} from "
                    f"{ev.get('source_id')} marks useful="
                    f"{ev.get('useful')!r} but its dimensions imply "
                    f"{want_useful!r}")
            if ev.get("cost") != COSTS.get(
                    (ev.get("source_id"), ev.get("condition")), 1):
                defects.append(
                    f"selection {data.get('selection_id')}: evaluation "
                    f"for {ev.get('condition')} costs "
                    f"{ev.get('cost')!r}, frozen table says "
                    f"{COSTS.get((ev.get('source_id'), ev.get('condition')), 1)!r}")
    # Certify rule: an expectation covered by a selection must be
    # preceded by it (same check as trace.certify).
    covering = [(s, {e.get("condition") for e in (d.get("selected") or [])
                     if isinstance(e, dict)})
                for s, d in sels]
    for seq, ev in events:
        if ev["event_type"] != "EVIDENCE_EXPECTATION":
            continue
        cond = (ev.get("data") or {}).get("condition")
        cov = [s for s, conds in covering if cond in conds]
        if cov and not any(s < seq for s in cov):
            defects.append(
                "expectation "
                f"{(ev.get('data') or {}).get('expectation_id')} for "
                f"{cond} is covered by a selection but not preceded "
                f"by it")
    if report.get("trace_certified") is not True:
        defects.append(
            "report trace_certified is not True: "
            f"{report.get('trace_certification_gaps')}")
    return {"ok": not defects, "defects": defects,
            "n_selections": len(sels), "measurement_cost": total,
            "cost_by_condition": by_condition,
            "selected_conditions": selected_conditions}


def _check_dispositions(report, events):
    """The dispositions: TASK_END agrees with the report, and
    every justified_abstention request is corroborated from the
    trace — the observation contradicts every procedure it
    names, and an ESCALATION event with the contradiction reason
    and the same observation exists. A "resumed" replay request
    may carry a fully-contradicting observation (the replay
    break case: contradictory_observations) — that is flow, not
    a disposition defect, and is not flagged here."""
    defects = []
    ends = [(seq, ev) for seq, ev in events
            if ev["event_type"] == "TASK_END"]
    if not ends:
        defects.append("no TASK_END event in the trace")
    else:
        end = (ends[-1][1].get("data") or {})
        if end.get("outcome") != report.get("state"):
            defects.append(
                f"TASK_END outcome {end.get('outcome')!r} != report "
                f"state {report.get('state')!r}")
        if end.get("stage") != report.get("stage"):
            defects.append(
                f"TASK_END stage {end.get('stage')!r} != report stage "
                f"{report.get('stage')!r}")
        if end.get("selected") != report.get("selected"):
            defects.append(
                f"TASK_END selected {end.get('selected')!r} != report "
                f"selected {report.get('selected')!r}")
    escs = [(ev.get("data") or {}) for _s, ev in events
            if ev["event_type"] == "ESCALATION"]
    for req in report.get("information_requests") or []:
        if req.get("outcome") != "justified_abstention":
            continue
        obs = req.get("observation") or {}
        if not obs:
            defects.append(
                "justified_abstention request carries no observation")
            continue
        named = req.get("contradicted") or req.get("procedures") or []
        for proc in named:
            if not _hard_contradicted(proc, obs):
                defects.append(
                    f"justified_abstention names {proc} but the "
                    f"observation does not contradict it")
        matching = [d for d in escs
                    if d.get("reason") == _FLAG_ESCALATION_REASON
                    and d.get("observation") == obs]
        if not matching:
            defects.append(
                "justified_abstention has no ESCALATION event with "
                "the contradiction reason and the same observation")
        elif req.get("contradicted") is not None and \
                sorted(req["contradicted"]) != sorted(
                    matching[0].get("contradicted") or []):
            defects.append(
                "justified_abstention contradicted list does not "
                "match its ESCALATION event")
    return {"ok": not defects, "defects": defects}


def _derived_replay_request(events, expectation_id):
    """Re-derive one pathway-replay information request from the
    trace, exactly as parity_check derives it (origin from the
    enclosing span's purpose, outcome "resumed"/"unanswered").
    Returns (entry, expectation_data, observation_dict) or None
    when the expectation is not a replay expectation."""
    purposes = {}
    for _seq, ev in events:
        if ev["event_type"] == "FUNCTION_ENTER":
            purposes[ev.get("span_id")] = (ev.get("data") or {}).get("purpose")
    for seq, ev in events:
        if ev["event_type"] != "EVIDENCE_EXPECTATION":
            continue
        d = ev.get("data") or {}
        if d.get("expectation_id") != expectation_id:
            continue
        if purposes.get(ev.get("span_id")) != \
                "replay stored pathway discriminators":
            return None
        obs = None
        for _s2, ev2 in events:
            if ev2["event_type"] == "EVIDENCE_OBSERVED" and \
                    (ev2.get("data") or {}).get("expectation_id") == expectation_id:
                obs = ev2.get("data") or {}
        cond = d.get("condition")
        entry = {"missing": [cond], "question": d.get("measurement"),
                 "procedures": sorted(
                     h.get("procedure") for h in d.get("hypotheses") or []),
                 "source": "pathway-replay", "answered": obs is not None,
                 "expectation_id": {cond: expectation_id},
                 "expectation": {cond: d},
                 "observation": ({cond: obs} if obs else None),
                 "outcome": ("resumed" if obs is not None
                             else "unanswered")}
        return entry, d, ({cond: obs} if obs else None)
    return None


def _rederive_rejected(report, events, raw_rows, task_dict,
                       trial_candidates_fn):
    """Re-derive the rejected multiset from the trace + the trial
    recompute, mirroring parity_check — except the
    escalate_because trigger ALSO fires on the flag-path
    ESCALATION reason ("evidence contradicts every pointed-at
    procedure" after answered replay evidence), which the frozen
    checker does not model. Returns a Counter, or None when the
    inputs needed for the re-derivation are absent."""
    if trial_candidates_fn is None or task_dict is None:
        return None
    derived = Counter(
        (d.get("procedure"), d.get("reason"))
        for _s, ev in events
        if ev["event_type"] == "CANDIDATE_REJECTED"
        for d in [ev.get("data") or {}])
    proposed = [{f: (ev.get("data") or {}).get(f) for f in
                 ("procedure", "native", "source")}
                for _s, ev in events
                if ev["event_type"] == "CANDIDATE_PROPOSED"]
    for p in proposed:
        if p.get("source") == "transfer" \
                and p.get("native") not in BY_NATIVE:
            derived[(p.get("native"),
                     "not in the procedure registry")] += 1
    # u10_blocked: a targeted_investigation request that did not
    # resume, or the "unknown required condition" escalation.
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
    u10_blocked = False
    updated_conds = dict(task_dict.get("conditions") or {})
    updated_unknown = [c for c in task_dict.get("unknown_conditions") or []]
    for eid, (seq, d, purpose) in expectations.items():
        obs = observations.get(eid)
        if obs is not None:
            updated_conds[obs[1].get("condition")] = obs[1].get("value")
            if obs[1].get("condition") in updated_unknown:
                updated_unknown.remove(obs[1].get("condition"))
        if purpose == "targeted_investigation":
            if obs is None:
                outcome = "unanswered"
            else:
                hyps = d.get("hypotheses") or []
                contradicted = bool(hyps) and all(
                    h.get("requires") != obs[1].get("value") for h in hyps)
                outcome = ("justified_abstention" if contradicted
                           else "resumed")
            if outcome != "resumed":
                u10_blocked = True
    esc_reasons = {(ev.get("data") or {}).get("reason")
                   for _s, ev in events
                   if ev["event_type"] == "ESCALATION"}
    if _UNKNOWN_ESCALATION_REASON in esc_reasons:
        u10_blocked = True
    trial = trial_candidates_fn(_task_record(task_dict))
    if u10_blocked:
        for proc_id, reason in trial.get("pointed_blocks") or []:
            derived[(proc_id, reason)] += 1
    # escalate_because: re-derived on the task as known after all
    # trace-derived observations (the frozen checker's rule),
    # triggered by EITHER escalation reason — the legacy one, or
    # the flag-path one (which only fires after answered replay
    # evidence, checked by the caller).
    if "every pointed-at procedure blocked" in esc_reasons or \
            _FLAG_ESCALATION_REASON in esc_reasons:
        updated = dict(task_dict)
        updated["conditions"] = updated_conds
        updated["unknown_conditions"] = tuple(updated_unknown)
        trial_updated = trial_candidates_fn(_task_record(updated))
        for proc_id, reason in trial_updated.get("escalate_because") or []:
            derived[(proc_id, reason)] += 1
    for _seq, kind, _t, _step, data in raw_rows:
        if kind == "REVALIDATION_DENIED":
            derived[(data.get("action"), data.get("reason"))] += 1
    return derived


def _rederive_pathway_cost(report, events, derived_rejected):
    """Recompute pathway_cost the way run() computes it, from the
    trace-derived proposed / attempted / (re-derived) rejected
    sets. Returns the dict, or None when the attempt pairing
    fails (OBSERVATION/VALIDATION counts differ)."""
    proposed = []
    for _s, ev in events:
        if ev["event_type"] == "CANDIDATE_PROPOSED":
            d = ev.get("data") or {}
            proposed.append({"procedure": d.get("procedure"),
                             "native": d.get("native"),
                             "source": d.get("source"),
                             "rank": d.get("rank")})
    obs = [(d.get("procedure")) for _s, ev in events
           if ev["event_type"] == "OBSERVATION"
           for d in [ev.get("data") or {}]]
    val = [(d.get("procedure")) for _s, ev in events
           if ev["event_type"] == "VALIDATION"
           for d in [ev.get("data") or {}]]
    if len(obs) != len(val):
        return None
    seen = {}
    for p in proposed:
        if p["procedure"] in seen:
            seen[p["procedure"]]["sources"].append(p.get("source"))
            continue
        seen[p["procedure"]] = {"procedure": p["procedure"],
                                "native": p.get("native"),
                                "sources": [p.get("source")],
                                "rank": p.get("rank")}
    attempted = set(obs)
    rejected = {proc for proc, _r in derived_rejected}
    resolved = report.get("state") == "RESOLVED"
    n_unattempted = 0
    for proc in seen:
        if proc in attempted or proc in rejected:
            continue
        n_unattempted += 1
    return {"n_proposed": len(proposed),
            "n_proposed_procedures": len(seen),
            "n_attempted": len(obs),
            "n_rejected": sum(derived_rejected.values()),
            "n_unattempted": n_unattempted}


def _reconcile_replay_abstention(report, events, raw_rows, task_dict,
                                 trial_candidates_fn):
    """Pattern R1: is every raw defect in this run exactly the
    flag-path abstention the frozen checker cannot model? All
    sub-checks must pass; any failure reconciles nothing.
    Returns (True, note) or (False, reason)."""
    replay_reqs = [r for r in report.get("information_requests") or []
                   if r.get("source") == "pathway-replay"
                   and r.get("outcome") == "justified_abstention"]
    if len(replay_reqs) != 1:
        return False, ("R1 needs exactly one justified_abstention "
                       f"replay request, found {len(replay_reqs)}")
    req = replay_reqs[0]
    if not isinstance(req.get("contradicted"), list) \
            or not req["contradicted"]:
        return False, "R1 replay request carries no contradicted list"
    if len(report.get("information_requests") or []) != 1:
        return False, "R1 needs exactly one information request"
    # (a) The ESCALATION event names the contradiction with the
    # same observation and contradicted list.
    obs = req.get("observation") or {}
    escs = [(ev.get("data") or {}) for _s, ev in events
            if ev["event_type"] == "ESCALATION"]
    flag_escs = [d for d in escs
                 if d.get("reason") == _FLAG_ESCALATION_REASON]
    if len(flag_escs) != 1:
        return False, (f"R1 needs exactly one flag ESCALATION event, "
                       f"found {len(flag_escs)}")
    if flag_escs[0].get("observation") != obs:
        return False, "R1 ESCALATION observation != request observation"
    if sorted(flag_escs[0].get("contradicted") or []) != \
            sorted(req["contradicted"]):
        return False, "R1 ESCALATION contradicted != request contradicted"
    # (b) The observation contradicts every hypothesis in the
    # replay expectation, and the contradicted list is exactly
    # the escalate_because procedures the observation
    # contradicts (all of them).
    derived = _derived_replay_request(
        events, (req.get("expectation_id") or {}).get(DISCRIMINATOR))
    if derived is None:
        return False, "R1 replay expectation not found in the trace"
    entry, exp_data, _obs = derived
    if not obs:
        return False, "R1 replay request carries no observation"
    value = next(iter(obs.values()))["value"]
    hyps = exp_data.get("hypotheses") or []
    if not hyps or not all(h.get("requires") != value for h in hyps):
        return False, ("R1 observation does not contradict every "
                       "hypothesis in the replay expectation")
    if trial_candidates_fn is None or task_dict is None:
        return False, "R1 needs the trial recompute (not supplied)"
    updated_conds = dict(task_dict.get("conditions") or {})
    for _c, o in obs.items():
        updated_conds[o.get("condition")] = o.get("value")
    updated = dict(task_dict)
    updated["conditions"] = updated_conds
    updated["unknown_conditions"] = tuple(
        c for c in task_dict.get("unknown_conditions") or []
        if c not in updated_conds)
    trial_updated = trial_candidates_fn(_task_record(updated))
    esc_procs = [p for p, _r in
                 trial_updated.get("escalate_because") or []]
    if not esc_procs:
        return False, "R1 trial recompute yields no escalate_because"
    want = sorted(p for p in esc_procs
                  if _hard_contradicted(p, obs))
    if want != sorted(req["contradicted"]) or len(want) != len(esc_procs):
        return False, ("R1 contradicted list != the contradicted "
                       "escalate_because procedures")
    # (c) The report's replay request is exactly the
    # trace-derived request with the flag update applied.
    expected = dict(entry)
    expected["outcome"] = "justified_abstention"
    expected["contradicted"] = sorted(req["contradicted"])
    if req != expected:
        return False, ("R1 report replay request != trace-derived "
                       "request with the flag update applied")
    # (d) The rejected multiset and pathway_cost re-derive
    # exactly once the flag trigger is admitted.
    derived_rejected = _rederive_rejected(
        report, events, raw_rows, task_dict, trial_candidates_fn)
    if derived_rejected is None:
        return False, "R1 rejected re-derivation unavailable"
    rep_rejected = Counter((r.get("procedure"), r.get("reason"))
                           for r in report.get("rejected") or [])
    if derived_rejected != rep_rejected:
        return False, ("R1 re-derived rejected "
                       f"{dict(derived_rejected)} != report "
                       f"{dict(rep_rejected)}")
    derived_cost = _rederive_pathway_cost(report, events, derived_rejected)
    if derived_cost is None or derived_cost != dict(
            report.get("pathway_cost") or {}):
        return False, ("R1 re-derived pathway_cost "
                       f"{derived_cost} != report "
                       f"{report.get('pathway_cost')}")
    # (e) Every operational INFORMATION_REQUEST row matches a
    # report request (replay included) and the counts agree.
    op_reqs = [data for _s, kind, _t, _st, data in raw_rows
               if kind == "INFORMATION_REQUEST"]
    rep_reqs = list(report.get("information_requests") or [])
    if len(op_reqs) != len(rep_reqs):
        return False, ("R1 operational INFORMATION_REQUEST rows "
                       f"{len(op_reqs)} != report requests "
                       f"{len(rep_reqs)}")
    for op in op_reqs:
        if op not in rep_reqs:
            return False, ("R1 operational INFORMATION_REQUEST row "
                            f"{op} matches no report request")
    return True, ("R1 corroborated from the trace: flag ESCALATION "
                  "event + contradicting observation + re-derived "
                  "rejected/pathway_cost + operational row all agree "
                  "with the report")


def _reconcile_empty_selection(report, events):
    """Pattern R2: is the run's single raw defect exactly the
    empty-selection unanswered request the frozen checker cannot
    model? All sub-checks must pass; any failure reconciles
    nothing. Returns (True, note) or (False, reason)."""
    reqs = list(report.get("information_requests") or [])
    if len(reqs) != 1:
        return False, f"R2 needs exactly one information request, found {len(reqs)}"
    req = reqs[0]
    if req.get("source") != "targeted_investigation":
        return False, "R2 request is not a targeted_investigation request"
    if req.get("outcome") != "unanswered":
        return False, "R2 request outcome is not unanswered"
    if req.get("observation") is not None:
        return False, "R2 unanswered request carries an observation"
    if req.get("expectation_id") != {} or req.get("expectation") != {}:
        return False, "R2 unanswered request carries expectations"
    # The pipeline records answered=True vacuously when the
    # selector selects nothing (no measurement was attempted, so
    # no answer failed). The outcome "unanswered" is what the
    # run means; the vacuous True is recorded here as a known
    # reporting wart, not hidden.
    if req.get("answered") is not True:
        return False, "R2 request answered is not the vacuous True"
    missing = req.get("missing") or []
    if not missing:
        return False, "R2 request names no missing condition"
    sels = _selection_events(events)
    if not sels:
        return False, "R2 run has no EVIDENCE_SELECTION event"
    for _seq, data in sels:
        if data.get("selected"):
            return False, "R2 selection event selected a measurement"
        for ev in data.get("evaluations") or []:
            if ev.get("condition") in missing and (
                    ev.get("available") or ev.get("useful")):
                return False, ("R2 evaluation for missing condition "
                               f"{ev.get('condition')} is available/useful")
    if any(ev["event_type"] == "EVIDENCE_EXPECTATION"
           for _s, ev in events):
        return False, "R2 run froze an expectation"
    escs = [(ev.get("data") or {}) for _s, ev in events
            if ev["event_type"] == "ESCALATION"]
    if not any(d.get("reason") == _UNKNOWN_ESCALATION_REASON
               and d.get("missing") == missing for d in escs):
        return False, ("R2 run has no unknown-required-condition "
                       "ESCALATION for the missing conditions")
    if report.get("trace_certified") is not True:
        return False, "R2 run is not trace-certified"
    return True, ("R2 corroborated from the trace: every selection "
                  "selected nothing with the missing condition "
                  "unavailable, no expectation was frozen, the run "
                  "escalated unanswered (answered=True is the "
                  "pipeline's vacuous value when nothing was selected)")


_R1_CATEGORIES = ("rejected mismatch", "information_requests mismatch",
                  "operational INFORMATION_REQUEST rows",
                  "pathway_cost mismatch")


def parity_check002(report, trace_log, task_dict=None, policy=None,
                    trial_candidates_fn=None, latest_per_pair=None):
    """The PATHWAY-002 parity check: the frozen PATHWAY-001
    parity_check for the common fields, plus the selection,
    cost, and disposition checks above, plus the R1/R2
    reconciliations. Returns {"ok", "raw", "selection", "cost",
    "disposition", "reconciled", "blocking_defects", "compared",
    "scope_limits"}. "ok" is True only when there are no
    blocking defects AND the extension checks pass. The raw
    defects are preserved verbatim; reconciled ones are listed
    with their pattern and corroboration note."""
    raw = parity_check(report, trace_log, task_dict=task_dict,
                       policy=policy,
                       trial_candidates_fn=trial_candidates_fn,
                       latest_per_pair=latest_per_pair)
    events = trace_events(trace_log, report.get("task_id"),
                          report.get("execution_id"))
    try:
        raw_rows = trace_log.events(report.get("task_id"))
    except Exception:
        raw_rows = []
    selection = _check_selection(report, events)
    disposition = _check_dispositions(report, events)
    cost_defects = [d for d in selection["defects"]
                    if "cost" in d.lower()]
    cost = {"ok": not cost_defects,
            "defects": cost_defects,
            "measurement_cost": selection["measurement_cost"],
            "cost_by_condition": selection["cost_by_condition"],
            "selected_conditions": selection["selected_conditions"],
            "n_selections": selection["n_selections"]}
    reconciled = []
    blocking = []
    r1_ok, r1_note = _reconcile_replay_abstention(
        report, events, raw_rows, task_dict, trial_candidates_fn)
    r2_ok, r2_note = _reconcile_empty_selection(report, events)
    for defect in raw["defects"]:
        if r1_ok and defect.startswith(_R1_CATEGORIES):
            reconciled.append({"pattern": "R1", "defect": defect,
                               "note": r1_note})
        elif r2_ok and defect.startswith("information_requests mismatch"):
            reconciled.append({"pattern": "R2", "defect": defect,
                               "note": r2_note})
        else:
            blocking.append(defect)
    if raw["scope_limits"]:
        blocking.extend(
            f"parity scope incomplete: {s}" for s in raw["scope_limits"])
    ok = (not blocking and selection["ok"] and disposition["ok"])
    return {"ok": ok, "raw": raw, "selection": selection, "cost": cost,
            "disposition": disposition, "reconciled": reconciled,
            "blocking_defects": blocking,
            "compared": raw["compared"] + ["selection", "cost",
                                            "disposition"],
            "scope_limits": raw["scope_limits"]}


# ------------------------------------------------------------------
# Scoring (the frozen §5 endpoint, applied per case)
# ------------------------------------------------------------------

def _intended_b002(case, arm_b):
    """Does arm B behave as the case's intended class requires?
    Returns (True, "") or (False, reason). This is the freezing
    criterion re-verified at runtime: a case whose measured A/B
    behavior contradicts its intended class after freezing is a
    case defect and is excluded from scoring in every arm, never
    silently re-tuned."""
    kind = case["kind"]
    if kind == "different_answer":
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
    if kind == "missing_evidence":
        if arm_b["state"] != "RESOLVED" \
                or arm_b["selected_native"] != case["correct"]:
            return False, ("arm B did not resolve a missing-evidence "
                           "case to its correct procedure")
        return True, ""
    if kind == "abstention_required":
        reqs = [r for r in arm_b["information_requests"]
                if r.get("outcome") == "justified_abstention"]
        if arm_b["state"] != "ESCALATED" or not reqs \
                or arm_b["n_attempts"] != 0:
            return False, ("arm B did not justify abstention "
                           "(ESCALATED, 0 attempts) on an "
                           "abstention-required case")
        return True, ""
    if kind == "contradictory_observations":
        if arm_b["state"] != "UNSOLVED":
            return False, (f"arm B state {arm_b['state']} != UNSOLVED "
                           f"for a contradictory-observations case")
        if arm_b["information_requests"]:
            return False, ("arm B made an information request on a "
                           "contradictory-observations case")
        if arm_b["n_attempts"] == 0:
            return False, ("arm B made no trial attempt on a "
                           "contradictory-observations case")
        return True, ""
    if kind == "unavailable_evidence":
        reqs = arm_b["information_requests"]
        if arm_b["state"] != "ESCALATED" or len(reqs) != 1 \
                or reqs[0].get("outcome") != "unanswered" \
                or arm_b["n_attempts"] != 0:
            return False, ("arm B did not escalate unanswered (0 "
                           "attempts) on an unavailable-evidence case")
        if any(ev["event_type"] == "EVIDENCE_OBSERVED"
               for _s, ev in arm_b["events"]):
            return False, ("arm B observed evidence on an "
                           "unavailable-evidence case")
        return True, ""
    if kind == "misleading_prior":
        if arm_b["state"] != "RESOLVED" \
                or arm_b["selected_native"] != case["correct"]:
            return False, ("arm B did not resolve a misleading-prior "
                           "case to its correct procedure")
        return True, ""
    return True, ""


def score_case002(case, report, events, parity002, integrity, arm_b=None,
                  retrieval=None):
    """Score one case in one arm: the PATHWAY-001 score_case
    (same endpoint shape) fed with the PATHWAY-002 parity
    verdict, plus the measurement-cost and pathway-retrieval
    fields. A blocking parity defect OR a failed extension
    check excludes the case/arm from scoring. The raw parity
    defects and their reconciliations are preserved in the
    record."""
    parity_for_scoring = {
        "ok": parity002["ok"],
        "defects": parity002["raw"]["defects"],
        "compared": parity002["compared"],
        "scope_limits": parity002["raw"]["scope_limits"]}
    scored_case = score_case(case, report, events, parity_for_scoring,
                             integrity, arm_b=arm_b)
    retrieval = retrieval or {"method_retrieved": False,
                              "method_pathway_id": None,
                              "method_source_task_id": None,
                              "method_discriminators": [],
                              "method_measurements": []}
    sel = parity002["selection"]
    cost = parity002["cost"]
    scored_case["parity_raw_ok"] = parity002["raw"]["ok"]
    scored_case["parity_raw_defects"] = parity002["raw"]["defects"]
    scored_case["parity_reconciled"] = parity002["reconciled"]
    scored_case["parity_blocking_defects"] = parity002["blocking_defects"]
    scored_case["selection_ok"] = sel["ok"]
    scored_case["selection_defects"] = sel["defects"]
    scored_case["disposition_ok"] = parity002["disposition"]["ok"]
    scored_case["disposition_defects"] = parity002["disposition"]["defects"]
    scored_case["measurement_cost"] = cost["measurement_cost"]
    scored_case["cost_by_condition"] = cost["cost_by_condition"]
    scored_case["selected_conditions"] = cost["selected_conditions"]
    scored_case["n_selections"] = cost["n_selections"]
    scored_case["pathway_retrieval"] = retrieval
    method_conds = retrieval.get("method_measurements") or []
    scored_case["method_measurements_selected"] = bool(
        retrieval.get("method_retrieved") and method_conds
        and all(c in cost["selected_conditions"] for c in method_conds))
    if not parity002["ok"] and scored_case["scored"]:
        scored_case["scored"] = False
        scored_case["endpoint_counts"] = False
        scored_case["excluded_reason"] = (
            "parity extension failed: "
            + "; ".join(parity002["blocking_defects"]
                        + sel["defects"]
                        + parity002["disposition"]["defects"]))
    return scored_case


def _summarize_arm002(cases):
    """The PATHWAY-001 arm summary, extended with measurement
    cost, the cost-per-endpoint comparison inputs, pathway
    retrieval, and the parity reconciliation counts."""
    summary = _summarize_arm(cases)
    scored = [c for c in cases.values() if c["scored"]]
    endpoint_cases = [c for c in scored if c["endpoint_counts"]]
    total_cost = sum(c["measurement_cost"] for c in scored)
    endpoint_cost = sum(c["measurement_cost"] for c in endpoint_cases)
    summary["measurement_cost_total"] = total_cost
    summary["measurement_cost_mean"] = (
        total_cost / len(scored) if scored else None)
    summary["measurement_cost_per_endpoint_case"] = (
        endpoint_cost / len(endpoint_cases) if endpoint_cases else None)
    summary["endpoint_cost_total"] = endpoint_cost
    summary["n_parity_raw_defects"] = sum(
        len(c["parity_raw_defects"]) for c in cases.values())
    summary["n_parity_reconciled"] = sum(
        len(c["parity_reconciled"]) for c in cases.values())
    summary["parity_reconciled_by_pattern"] = dict(Counter(
        r["pattern"] for c in cases.values()
        for r in c["parity_reconciled"]))
    summary["n_parity_blocking"] = sum(
        len(c["parity_blocking_defects"]) for c in cases.values())
    summary["n_method_retrieved"] = sum(
        1 for c in cases.values()
        if c["pathway_retrieval"]["method_retrieved"])
    summary["n_method_measurements_selected"] = sum(
        1 for c in cases.values() if c["method_measurements_selected"])
    summary["n_replay_abstentions"] = sum(
        1 for c in cases.values()
        if c["endpoint_counts"] and c["endpoint_kind"] == "abstention"
        and any(r.get("source") == "pathway-replay"
                and r.get("outcome") == "justified_abstention"
                for r in c["information_requests"]))
    return summary


# ------------------------------------------------------------------
# The matched four-arm experiment
# ------------------------------------------------------------------

def _run_arm002(arm, arm_root, source):
    """Reopen a copied trained+learned root and run the 32 cases
    in one arm. A = no evidence source, no selector, no provider
    (PATHWAY-001 arm-A behavior); B = evidence source + selector,
    no provider; C = source + selector + PathwayReplayProvider;
    D = source + selector + ShuffledPathwayProvider (seed
    20261118). PATHWAY-002 runs use replay_contradiction_
    abstention=True; the selector is constructed with the frozen
    cost table. The method retrieved for each case (arms C/D) is
    recorded BEFORE the run, read-only, from the same provider
    the pipeline will consult."""
    if arm == "A":
        h = Harness(root=arm_root, tasks=ALL_TASKS)
    else:
        h = Harness(root=arm_root, tasks=ALL_TASKS, evidence_source=source,
                    measurement_selector=MeasurementSelector(
                        sources=[source], costs=COSTS),
                    measurement_costs=COSTS,
                    replay_contradiction_abstention=True)
    try:
        for t in TRAINING:
            h.add_task(t)
        for x in TRANSFER_TASKS.values():
            h.add_task(x)
        store = PathwayStore(h.pipe.big)
        provider = None
        association = None
        if arm == "C":
            provider = PathwayReplayProvider(store)
            h.pipe.pathway_provider = provider
        elif arm == "D":
            provider = ShuffledPathwayProvider(
                store, [_public_task(c) for c in CASES], seed=SHUFFLE_SEED)
            h.pipe.pathway_provider = provider
            association = dict(provider.association)
        raw = {}
        for case in CASES:
            task_dict = _public_task(case)
            if provider is not None:
                method = provider.method_for(task_dict)
                retrieval = {
                    "method_retrieved": method is not None,
                    "method_pathway_id": (method["pathway_id"]
                                          if method else None),
                    "method_source_task_id": (method["source_task_id"]
                                              if method else None),
                    "method_discriminators": (
                        list(method.get("discriminators") or [])
                        if method else []),
                    "method_measurements": (
                        [m.get("condition") for m in
                         method.get("measurements") or []]
                        if method else [])}
            else:
                retrieval = {"method_retrieved": False,
                             "method_pathway_id": None,
                             "method_source_task_id": None,
                             "method_discriminators": [],
                             "method_measurements": []}
            rep = h.pipe.run(task_dict)
            latest = h.pipe.experiences.latest_per_pair()
            events = trace_events(h.pipe.trace, case["task_id"],
                                  rep["execution_id"])
            parity = parity_check002(
                rep, h.pipe.trace, task_dict=task_dict, policy=h.policy,
                trial_candidates_fn=lambda task, _p=h.pipe: (
                    _p._trial_candidates(task)),
                latest_per_pair=latest)
            integrity = check_integrity(rep, events, task_dict, h.policy)
            raw[case["task_id"]] = (case, rep, events, parity, integrity,
                                    retrieval)
        return {"raw": raw, "association": association,
                "store": {"n_pathways": store.count(),
                          "pathway_ids": [r["pathway_id"]
                                          for r in store.all()]}}
    finally:
        h.close()


def run_pathway002_experiment(root=None, save_path=None):
    """Train ONCE, run the pathway-learning phase on that root,
    then copytree per arm and run the same 32 cases in each
    arm. Returns the full result dict (JSON-serialisable) and
    saves it to pathway002_results.json (project root) unless
    save_path is None and the caller passes save_path=False to
    skip writing.

    Primary endpoint (frozen, design §5, same shape as
    PATHWAY-001 §5): correct evidence-grounded resolution or
    justified abstention. A resolution counts only when selected
    == correct (native comparison) AND the decision is grounded
    in the recorded evidence (expectation + observation in the
    trace, an answered information request, resolution after
    it). An abstention counts only when outcome ==
    "justified_abstention" with the observation in the trace.
    Integrity (no false success, no prohibited execution): a
    single violation fails the arm's result whatever the
    endpoint says. A parity defect that is not reconciled by the
    R1/R2 re-derivations excludes that task/arm from scoring.
    """
    tmp = root or tempfile.mkdtemp(prefix="pw2-")
    own_tmp = root is None
    base_root = os.path.join(tmp, "root")
    # The pathway record's "root" is provenance, not a path the
    # store re-opens: the experiment tmpdir basename (short — the
    # compact record must fit the 512-char bucket).
    root_label = os.path.basename(tmp)
    source = FrozenEvidenceSource(build_evidence_table(),
                                  source_id=SOURCE_ID)
    try:
        # Train once (arm-B conditions: the evidence source and
        # the selector are present but training tasks carry known
        # firmware, so neither is consulted during training).
        h = Harness(root=base_root, tasks=ALL_TASKS, evidence_source=source,
                    measurement_selector=MeasurementSelector(
                        sources=[source], costs=COSTS),
                    measurement_costs=COSTS,
                    replay_contradiction_abstention=True)
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
                    "measurements": [m["condition"] for m in
                                     record.get("measurements") or []],
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
                    "measurements": [m["condition"] for m in
                                     r.get("measurements") or []],
                    "outcome": r["outcome"]} for r in store.all()]}
        finally:
            h.close()
        arm_raw = {}
        arm_meta = {}
        for arm in ("A", "B", "C", "D"):
            arm_root = os.path.join(tmp, f"arm-{arm}")
            shutil.copytree(base_root, arm_root)
            out = _run_arm002(arm, arm_root, source)
            arm_raw[arm] = out["raw"]
            arm_meta[arm] = {"association": out["association"],
                             "store": out["store"]}
        # Score: the freezing criterion is re-verified from arms
        # A and B (A must not resolve any case; B must behave as
        # the case's intended class requires). A case that fails
        # it after freezing is a case defect and is excluded from
        # scoring in every arm.
        arms = {}
        for arm in ("A", "B", "C", "D"):
            cases = {}
            for task_id, (case, rep, events, parity, integrity,
                          retrieval) in arm_raw[arm].items():
                b_case, b_rep, b_ev, _b_par, _b_int, _b_ret = \
                    arm_raw["B"][task_id]
                arm_b = {
                    "state": b_rep["state"],
                    "selected_native": _native(b_rep.get("selected")),
                    "information_requests":
                        b_rep.get("information_requests") or [],
                    "n_attempts": len(b_rep.get("attempts") or []),
                    "events": b_ev}
                ok_b, reason_b = _intended_b002(case, arm_b)
                a_rep = arm_raw["A"][task_id][1]
                case_defect_extra = None
                if a_rep["state"] == "RESOLVED":
                    case_defect_extra = ("arm A resolved a case that "
                                         "was frozen as unresolvable "
                                         "without evidence/pathways")
                scored_case = score_case002(case, rep, events, parity,
                                            integrity, arm_b=arm_b,
                                            retrieval=retrieval)
                if not ok_b and scored_case["case_defect"] is None:
                    scored_case["case_defect"] = reason_b
                    scored_case["scored"] = False
                    scored_case["endpoint_counts"] = False
                    scored_case["excluded_reason"] = (
                        f"case defect: {reason_b}")
                if case_defect_extra is not None \
                        and scored_case["case_defect"] is None:
                    scored_case["case_defect"] = case_defect_extra
                    scored_case["scored"] = False
                    scored_case["endpoint_counts"] = False
                    scored_case["excluded_reason"] = (
                        f"case defect: {case_defect_extra}")
                cases[task_id] = scored_case
            arms[arm] = {"cases": cases,
                         "summary": _summarize_arm002(cases),
                         "association": arm_meta[arm]["association"],
                         "store": arm_meta[arm]["store"]}
        b_cost = arms["B"]["summary"]["measurement_cost_per_endpoint_case"]
        c_cost = arms["C"]["summary"]["measurement_cost_per_endpoint_case"]
        c_gain = (arms["C"]["summary"]["endpoint"]
                  - arms["B"]["summary"]["endpoint"])
        if c_gain == 0:
            cost_verdict = ("moot: C's endpoint gain over B is zero, "
                            "so the cost comparison does not apply")
            cost_ratio = None
        elif b_cost in (None, 0):
            cost_ratio = None
            cost_verdict = ("undefined: B has no endpoint-counted cost "
                            "to compare against")
        else:
            cost_ratio = c_cost / b_cost
            cost_verdict = (
                "acceptable" if cost_ratio <= COST_RATIO_LIMIT
                else "UNACCEPTABLE: exceeds the frozen 1.5x limit")
        result = {
            "experiment": "PATHWAY-002 matched four-arm",
            "seed": SHUFFLE_SEED,
            "selection_seed": SELECTION_SEED,
            "endpoint_definition": (
                "correct evidence-grounded resolution or justified "
                "abstention (design §5, frozen before evaluation, "
                "same shape as PATHWAY-001 §5): a resolution counts "
                "only when selected == correct (native) AND the "
                "trace holds the expectation + observation and the "
                "run resolved after it; an abstention counts only "
                "when outcome == 'justified_abstention' with the "
                "observation in the trace. Raw correct resolutions "
                "are reported separately; resolutions without "
                "evidence do not count toward the endpoint."),
            "cost_rule": (
                "C's mean measurement cost per endpoint-counted "
                "case must be <= 1.5x B's mean cost per "
                "endpoint-counted case; if C's endpoint gain over B "
                "is zero, the cost comparison is moot."),
            "costs": {f"{src}:{cond}": cost
                      for (src, cond), cost in sorted(COSTS.items())},
            "case_counts": dict(CASE_COUNTS),
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
            "cost_comparison": {
                "b_mean_cost_per_endpoint_case": b_cost,
                "c_mean_cost_per_endpoint_case": c_cost,
                "c_endpoint_gain_over_b": c_gain,
                "cost_ratio_c_over_b": cost_ratio,
                "limit": COST_RATIO_LIMIT,
                "verdict": cost_verdict},
            "notes": [
                "Kind (SOFT) was never selected in any run: the "
                "pipeline's direct-U10 selector hypotheses are "
                "restricted to the procedures blocked by an unknown "
                "HARD precondition (P1/P2), and no learning run "
                "records kind, so no pathway replays it. The kind "
                "leg of the design is unexercised; the cost table "
                "keeps a never-incurred kind price of 3.",
                "The frozen PATHWAY-001 parity checker predates the "
                "replay-contradiction flag and the empty-selection "
                "path; its raw defects on those runs are reconciled "
                "by parity_check002 only when an independent "
                "trace re-derivation corroborates the report "
                "exactly (patterns R1/R2). Reconciled defects are "
                "preserved verbatim in the per-case records.",
                "The pipeline records answered=True vacuously when "
                "the selector selects nothing (unavailable-"
                "evidence runs): no measurement was attempted, so "
                "no answer failed. The outcome 'unanswered' is "
                "what the run means; the vacuous True is a known "
                "reporting wart, recorded in the R2 reconciliation "
                "note, not hidden.",
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
