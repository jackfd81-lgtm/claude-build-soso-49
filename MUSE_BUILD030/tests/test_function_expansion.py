"""Cognitive Function Expansion (W3) — tests + ablation for the six
new functions (U01, U02, U03, U04, U05, L02).

Per function: (a) it executes — its span appears in the trace with
a FUNCTION_RESULT payload; (b) it produces meaningful output — the
payload content is checked, not just its presence; (c) it changes
processing when appropriate — an enabled-vs-disabled comparison on
a case whose outcome or pathway differs; (d) an ablation arm.
Training always runs clean; the ablation is applied only for the
test tasks (the run_ablation pattern from contract/trace002.py).

U03/U05 only bite when an evidence source / measurement selector
is configured, so those tests configure one exactly as
test_evidence001 / test_selection002 do.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.admission import Policy
from contract.evidence import FrozenEvidenceSource
from contract.harness import Harness
from contract.pipeline import _condition_status
from contract.registry import get as reg_get
from contract.selection import MeasurementSelector
from contract.trace import (FUNCTIONS, IMPLEMENTED_IDS, function_states,
                            reconstruct, trace_events)
from contract.trace002 import MATCHED_TASKS


def _trained(**kw):
    h = Harness(**kw)
    h.train_baseline()
    return h


def _run(h, task):
    h.add_task(task)
    return h.pipe.run({k: v for k, v in task.items() if k != "correct"})


def _events(h, rep, event_type=None):
    evs = trace_events(h.pipe.trace, rep["task_id"], rep["execution_id"])
    if event_type is None:
        return evs
    return [(s, e) for s, e in evs if e["event_type"] == event_type]


def _results(h, rep, fid):
    return [e["data"] for _, e in _events(h, rep, "FUNCTION_RESULT")
            if e["data"].get("function") == fid]


def _disabled(h, rep, fid):
    return [e["data"] for _, e in _events(h, rep, "FUNCTION_DISABLED")
            if e["data"].get("function") == fid]


def _functions(h, rep):
    return reconstruct(h.pipe.trace, rep["task_id"],
                       rep["execution_id"])["functions"]


def _evidence_task(task_id="fx-ev1", correct="P1"):
    return {"task_id": task_id, "signature": "disk_full",
            "family": "valve",
            "tags": ["valve", "reseat", "stuck", "pressure"],
            "conditions": {},
            "unknown_conditions": ("firmware",),
            "requested_operation": "repair",
            "provenance": ("int:function-expansion",), "correct": correct}


class _CountingSource(FrozenEvidenceSource):
    """Frozen source that counts answer() calls (proves U03's
    disabled arm never consults the source)."""

    def __init__(self, table, source_id="frozen-evidence"):
        super().__init__(table, source_id=source_id)
        self.answer_calls = []

    def answer(self, task_id, condition):
        self.answer_calls.append((task_id, condition))
        return super().answer(task_id, condition)


class _ListProvider:
    """Minimal pathway provider: names discriminators, never answers."""

    def __init__(self, discriminators):
        self._discriminators = list(discriminators)

    def discriminators_for(self, task):
        return list(self._discriminators)


# ------------------------------------------------------------------
# Registry: six new functions, implemented count 9 -> 15
# ------------------------------------------------------------------

def test_registry_counts_fifteen_implemented():
    assert len(FUNCTIONS) == 15
    assert set(IMPLEMENTED_IDS) == {
        "U08", "U09", "U37", "L01", "L15", "L21", "L30", "L24", "U10",
        "U01", "U02", "U03", "U04", "U05", "L02"}
    assert FUNCTIONS["U01"][0] == "Evaluate Demonstrated Conditions"
    assert FUNCTIONS["U02"][0] == "Authorize Execution"
    assert FUNCTIONS["U03"][0] == "Answer With Frozen Expectation"
    assert FUNCTIONS["U04"][0] == "Gate Trial Candidates"
    assert FUNCTIONS["U05"][0] == "Select Measurements"
    assert FUNCTIONS["L02"][0] == "Learn Applicability"
    # U02 is the Suit admission capability (GOVERNANCE), not a
    # brain function: 14 brain + 1 admission = 15 entries.
    states = function_states([])
    assert states["implemented"] == 15
    assert states["registered_only_count"] == 147 - 15


# ------------------------------------------------------------------
# U01 — Evaluate Demonstrated Conditions
# ------------------------------------------------------------------

def test_u01_executes_and_reports_satisfied():
    """Exact re-presentation: the U01 span runs under L30 and its
    FUNCTION_RESULT carries the tri-state status, the reason and
    the demonstrated conditions (not a stub)."""
    h = _trained()
    try:
        rep = _run(h, dict(MATCHED_TASKS[0]))  # valve v2, P2
        assert rep["state"] == "RESOLVED" and rep["stage"] == "lower-exact"
        assert "U01" in _functions(h, rep)
        results = _results(h, rep, "U01")
        assert results, "no U01 FUNCTION_RESULT"
        assert results[0]["procedure"] == "SOSO-P2"
        assert results[0]["status"] == "satisfied"
        assert results[0]["reason"] == ""
        assert results[0]["demonstrated"] == {"firmware": "v2"}
    finally:
        h.close()


def test_u01_tri_state_direct():
    """The wrapped check itself is tri-state: satisfied, unknown
    (a relevant condition never demonstrated), contradicted."""
    entry = reg_get("SOSO-P2")
    assert _condition_status(
        entry, SimpleNamespace(conditions={"firmware": "v2"}),
        {"firmware": "v2"}) == ("satisfied", "")
    status, reason = _condition_status(
        entry, SimpleNamespace(conditions={"firmware": "v2",
                                           "kind": "sensor"}), {})
    assert status == "unknown" and "no demonstrated value" in reason
    status, reason = _condition_status(
        entry, SimpleNamespace(conditions={"firmware": "v1"}),
        {"firmware": "v2"})
    assert status == "contradicted"
    assert "contradicts" in reason


def test_u01_contradicted_firmware_refused():
    """A v1 task: P2 (demonstrated v2) is refused with the
    contradiction named at exact AND transfer — never attempted —
    and P1 resolves by trial."""
    h = _trained()
    try:
        task = {"task_id": "fx-u01-v1", "signature": "disk_full",
                "family": "valve",
                "tags": ["valve", "firmware_v1", "stuck", "flow"],
                "conditions": {"firmware": "v1"},
                "requested_operation": "repair",
                "provenance": ("int:function-expansion",), "correct": "P1"}
        rep = _run(h, task)
        assert rep["state"] == "RESOLVED" and rep["selected"] == "SOSO-P1"
        assert [a["procedure"] for a in rep["attempts"]] == ["SOSO-P1"]
        p2 = [r for r in _results(h, rep, "U01")
              if r["procedure"] == "SOSO-P2"]
        assert len(p2) == 2  # exact + transfer sites
        assert all(r["status"] == "contradicted" for r in p2)
        assert all(r["demonstrated"] == {"firmware": "v2"} for r in p2)
        assert any(r["procedure"] == "SOSO-P2"
                   and "contradicted" in r["reason"]
                   for r in rep["rejected"])
    finally:
        h.close()


def test_u01_ablation_soft_contradiction_attempts_stale_procedure():
    """The ablation that matters: kind is SOFT, so admission
    would allow P7 — only U01 blocks it. Task kind=traverse
    contradicts P7's demonstrated kind=detour; tags share
    "route" with the P7 demonstration and the signature
    matches, so without U01 the stale procedure is attempted.
    Control: 1 attempt (P6 only). U01 disabled: 2 attempts
    (P7 wrong attempt first, then P6)."""
    task = {"task_id": "fx-u01-kind", "signature": "cache_big",
            "family": "narrow",
            "tags": ["route", "traverse", "narrow", "passage"],
            "conditions": {"kind": "traverse"},
            "requested_operation": "repair",
            "provenance": ("int:function-expansion",), "correct": "P6"}
    h = _trained()
    try:
        rep = _run(h, dict(task))
        assert rep["state"] == "RESOLVED" and rep["selected"] == "SOSO-P6"
        assert [(a["procedure"], a["observed"])
                for a in rep["attempts"]] == [("SOSO-P6", True)]
        assert any(r["procedure"] == "SOSO-P7"
                   and "contradicted" in r["reason"]
                   for r in rep["rejected"])
    finally:
        h.close()
    h = _trained()
    try:
        h.pipe.set_disabled_functions({"U01"})
        rep = _run(h, dict(task))
        assert rep["state"] == "RESOLVED" and rep["selected"] == "SOSO-P6"
        assert [(a["procedure"], a["observed"])
                for a in rep["attempts"]] == [("SOSO-P7", False),
                                              ("SOSO-P6", True)]
        assert _results(h, rep, "U01") == []
        assert _disabled(h, rep, "U01"), "no FUNCTION_DISABLED for U01"
        assert not any(r["procedure"] == "SOSO-P7"
                       for r in rep["rejected"])
    finally:
        h.close()


# ------------------------------------------------------------------
# U02 — Authorize Execution (GOVERNANCE, not a brain function)
# ------------------------------------------------------------------

def test_u02_executes_and_admits():
    """Exact re-presentation: the U02 span runs and its
    FUNCTION_RESULT records admitted=True, status "admitted"."""
    h = _trained()
    try:
        rep = _run(h, dict(MATCHED_TASKS[0]))
        assert rep["state"] == "RESOLVED" and rep["stage"] == "lower-exact"
        assert "U02" in _functions(h, rep)
        results = _results(h, rep, "U02")
        assert results, "no U02 FUNCTION_RESULT"
        assert all(r["procedure"] == "SOSO-P2" for r in results)
        assert all(r["admitted"] is True for r in results)
        assert all(r["status"] == "admitted" for r in results)
        assert all(r["detail"] == "" for r in results)
    finally:
        h.close()


def _u02_policy_run(disabled):
    """Valve-v2 task under a policy that forbids SOSO-P2
    (swapped in after clean training, so the QUALIFIED P2
    record exists). Returns (harness, report)."""
    h = _trained()
    if disabled:
        h.pipe.set_disabled_functions({"U02"})
    h.pipe.policy = Policy("fx-policy-v2", forbidden=("SOSO-P2",))
    rep = _run(h, dict(MATCHED_TASKS[0]))
    return h, rep


def test_u02_denies_policy_forbidden_procedure():
    h, rep = _u02_policy_run(disabled=False)
    try:
        assert rep["state"] == "UNSOLVED"
        assert rep["attempts"] == []
        results = _results(h, rep, "U02")
        assert results, "no U02 FUNCTION_RESULT"
        assert all(r["admitted"] is False for r in results)
        assert all(r["status"] == "policy_denied" for r in results)
        assert all("forbids SOSO-P2" in r["detail"] for r in results)
        assert any(r["procedure"] == "SOSO-P2"
                   and "policy_denied" in r["reason"]
                   or r["procedure"] == "SOSO-P2"
                   and "forbids SOSO-P2" in r["reason"]
                   for r in rep["rejected"])
    finally:
        h.close()


def test_u02_ablation_admits_policy_forbidden_procedure():
    """U02 disabled (test-only): the policy-denied procedure
    executes — UNSOLVED becomes RESOLVED via lower-exact.
    This is the function's contribution, demonstrated by
    ablation; it does not weaken the live pipeline."""
    h, rep = _u02_policy_run(disabled=True)
    try:
        assert rep["state"] == "RESOLVED", rep
        assert rep["stage"] == "lower-exact"
        assert rep["selected"] == "SOSO-P2"
        assert [(a["procedure"], a["observed"])
                for a in rep["attempts"]] == [("SOSO-P2", True)]
        assert _results(h, rep, "U02") == []
        assert _disabled(h, rep, "U02"), "no FUNCTION_DISABLED for U02"
        assert not any(r["procedure"] == "SOSO-P2"
                       for r in rep["rejected"])
    finally:
        h.close()


# ------------------------------------------------------------------
# U03 — Answer With Frozen Expectation
# ------------------------------------------------------------------

def test_u03_executes_and_answers():
    """Evidence task, source answers v1: the U03 span runs, its
    FUNCTION_RESULT carries condition / expectation_id /
    answered / value, and the expectation event id matches the
    observation's — frozen before the source was consulted."""
    src = FrozenEvidenceSource({("fx-ev1", "firmware"): "v1"})
    h = _trained(evidence_source=src)
    try:
        rep = _run(h, _evidence_task())
        assert rep["state"] == "RESOLVED" and rep["selected"] == "SOSO-P1"
        assert "U03" in _functions(h, rep)
        results = _results(h, rep, "U03")
        assert len(results) == 1
        assert results[0]["condition"] == "firmware"
        assert results[0]["answered"] is True
        assert results[0]["value"] == "v1"
        exp = _events(h, rep, "EVIDENCE_EXPECTATION")
        obs = _events(h, rep, "EVIDENCE_OBSERVED")
        assert len(exp) == 1 and len(obs) == 1
        assert exp[0][0] < obs[0][0]
        assert results[0]["expectation_id"] == exp[0][1]["event_id"]
        assert obs[0][1]["data"]["expectation_id"] == exp[0][1]["event_id"]
        assert rep["information_requests"][0]["outcome"] == "resumed"
    finally:
        h.close()


def test_u03_unanswered_source_keyerror_records_honest_unanswered():
    """Source has no answer (KeyError): U03 still runs, records
    answered=False, freezes the expectation, emits no
    observation — and the run escalates rather than guessing."""
    src = FrozenEvidenceSource({})
    h = _trained(evidence_source=src)
    try:
        rep = _run(h, _evidence_task())
        assert rep["state"] == "ESCALATED"
        results = _results(h, rep, "U03")
        assert len(results) == 1
        assert results[0]["answered"] is False
        assert "value" not in results[0]
        assert len(_events(h, rep, "EVIDENCE_EXPECTATION")) == 1
        assert _events(h, rep, "EVIDENCE_OBSERVED") == []
        assert rep["information_requests"][0]["outcome"] == "unanswered"
        assert rep["information_requests"][0]["answered"] is False
    finally:
        h.close()


def test_u03_ablation_source_never_consulted():
    """U03 disabled: the expectation is still built and frozen
    (the audit property is preserved) but the source is NOT
    consulted — zero answer() calls — so the run escalates
    unanswered and no U03 span exists."""
    src = _CountingSource({("fx-ev1", "firmware"): "v1"})
    h = _trained(evidence_source=src)
    try:
        h.pipe.set_disabled_functions({"U03"})
        rep = _run(h, _evidence_task())
        assert rep["state"] == "ESCALATED", rep
        assert rep["stage"] == "unresolved"
        assert "U03" not in _functions(h, rep)
        assert _results(h, rep, "U03") == []
        assert _disabled(h, rep, "U03"), "no FUNCTION_DISABLED for U03"
        assert src.answer_calls == []
        assert len(_events(h, rep, "EVIDENCE_EXPECTATION")) == 1
        assert _events(h, rep, "EVIDENCE_OBSERVED") == []
        assert rep["information_requests"][0]["outcome"] == "unanswered"
        assert rep["trace_certified"] is True
    finally:
        h.close()


def test_u03_u05_absent_without_evidence_configuration():
    """Scope: with no evidence source and no selector, neither
    U03 nor U05 runs at all on the same unknown-firmware task."""
    h = _trained()
    try:
        rep = _run(h, _evidence_task())
        assert rep["state"] == "ESCALATED"
        funcs = _functions(h, rep)
        assert "U03" not in funcs and "U05" not in funcs
        assert _results(h, rep, "U03") == []
        assert _results(h, rep, "U05") == []
    finally:
        h.close()


# ------------------------------------------------------------------
# U04 — Gate Trial Candidates
# ------------------------------------------------------------------

def test_u04_executes_ask_branch():
    """Unknown firmware, no source: U04's FUNCTION_RESULT names
    the ask branch — empty candidates, blocked_by_unknown
    [P1, P2], pointed_blocks unknown_preconditions — and the
    run escalates with 0 attempts instead of guessing."""
    h = _trained()
    try:
        rep = _run(h, _evidence_task())
        assert rep["state"] == "ESCALATED"
        assert rep["attempts"] == []
        assert len(rep["information_requests"]) == 1
        assert "U04" in _functions(h, rep)
        results = _results(h, rep, "U04")
        assert len(results) == 1
        assert results[0]["candidates"] == []
        assert results[0]["blocked_by_unknown"] == ["SOSO-P1", "SOSO-P2"]
        assert results[0]["escalate_because"] is None
        assert results[0]["pointed_blocks"] == [
            ["SOSO-P1", "unknown_preconditions"],
            ["SOSO-P2", "unknown_preconditions"]]
    finally:
        h.close()


def test_u04_executes_escalate_branch():
    """Firmware v3 contradicts both pointed-at procedures:
    U04's FUNCTION_RESULT names the escalate branch — every
    pointed-at procedure blocked, empty candidates — and the
    run is UNSOLVED with 0 attempts (no flailing)."""
    h = _trained()
    try:
        task = {"task_id": "fx-u04-esc", "signature": "disk_full",
                "family": "valve",
                "tags": ["valve", "firmware_v3", "stuck", "pressure"],
                "conditions": {"firmware": "v3"},
                "requested_operation": "repair",
                "provenance": ("int:function-expansion",), "correct": "P1"}
        rep = _run(h, task)
        assert rep["state"] == "UNSOLVED"
        assert rep["attempts"] == []
        results = _results(h, rep, "U04")
        assert len(results) == 1
        assert results[0]["candidates"] == []
        assert results[0]["blocked_by_unknown"] is None
        assert results[0]["escalate_because"] == [
            ["SOSO-P1", "contradicted"], ["SOSO-P2", "contradicted"]]
        assert results[0]["pointed_blocks"] == [
            ["SOSO-P1", "contradicted"], ["SOSO-P2", "contradicted"]]
    finally:
        h.close()


def test_u04_ablation_ask_branch_trials_instead_of_asking():
    """U04 disabled on the ask-branch task: no information
    request is made; the trial guesses at 8 unrelated eligible
    procedures instead (ESCALATED -> UNSOLVED, 0 -> 8 attempts)."""
    h = _trained()
    try:
        h.pipe.set_disabled_functions({"U04"})
        rep = _run(h, _evidence_task())
        assert rep["state"] == "UNSOLVED", rep
        assert len(rep["attempts"]) == 8
        assert rep["information_requests"] == []
        assert _results(h, rep, "U04") == []
        assert _disabled(h, rep, "U04"), "no FUNCTION_DISABLED for U04"
    finally:
        h.close()


def test_u04_ablation_escalate_branch_trials_barred_family():
    """U04 disabled on the every-pointed-blocked task: the
    trial flails through the same 8 unrelated procedures
    instead of escalating without trialling."""
    h = _trained()
    try:
        h.pipe.set_disabled_functions({"U04"})
        task = {"task_id": "fx-u04-esc2", "signature": "disk_full",
                "family": "valve",
                "tags": ["valve", "firmware_v3", "stuck", "pressure"],
                "conditions": {"firmware": "v3"},
                "requested_operation": "repair",
                "provenance": ("int:function-expansion",), "correct": "P1"}
        rep = _run(h, task)
        assert rep["state"] == "UNSOLVED", rep
        assert len(rep["attempts"]) == 8
        assert [a["procedure"] for a in rep["attempts"]] == [
            "RC-FIX-CLEAR-CACHE", "RC-FIX-RESTART-SVC",
            "RC-FIX-ROTATE-LOGS", "SOSO-P3", "SOSO-P4", "SOSO-P5",
            "SOSO-P6", "SOSO-P7"]
        assert _results(h, rep, "U04") == []
    finally:
        h.close()


# ------------------------------------------------------------------
# U05 — Select Measurements
# ------------------------------------------------------------------

def _u05_direct_harness(disabled=False):
    src = FrozenEvidenceSource({("fx-ev1", "firmware"): "v1"})
    sel = MeasurementSelector(sources=[src])
    h = _trained(evidence_source=src, measurement_selector=sel)
    if disabled:
        h.pipe.set_disabled_functions({"U05"})
    return h


def test_u05_executes_direct_path():
    """Evidence task with a selector (no pathway provider): the
    U05 span runs, its FUNCTION_RESULT carries the selection
    id, the selected conditions and the evaluation count, and
    the EVIDENCE_SELECTION event precedes the expectation."""
    h = _u05_direct_harness()
    try:
        rep = _run(h, _evidence_task())
        assert rep["state"] == "RESOLVED" and rep["selected"] == "SOSO-P1"
        assert "U05" in _functions(h, rep)
        results = _results(h, rep, "U05")
        assert len(results) == 1
        assert results[0]["selected"] == ["firmware"]
        assert results[0]["n_evaluations"] >= 1
        sel_ev = _events(h, rep, "EVIDENCE_SELECTION")
        assert len(sel_ev) == 1
        assert sel_ev[0][1]["data"]["selection_id"] == \
            results[0]["selection_id"]
        assert results[0]["selection_id"].startswith("sel-")
        assert results[0]["n_evaluations"] == \
            len(sel_ev[0][1]["data"]["evaluations"])
        assert [s["condition"] for s in
                sel_ev[0][1]["data"]["selected"]] == ["firmware"]
        exp = _events(h, rep, "EVIDENCE_EXPECTATION")
        assert sel_ev[0][0] < exp[0][0]
    finally:
        h.close()


def test_u05_disabled_direct_path_falls_back_to_sorted_missing():
    """U05 disabled on the direct path: the documented
    no-selector fallback runs — every missing condition,
    sorted — so the task still resolves, with a
    FUNCTION_DISABLED event and no EVIDENCE_SELECTION. The
    outcome is unchanged here by design (one missing
    condition); the measurement-order change is shown by the
    replay test below."""
    h = _u05_direct_harness(disabled=True)
    try:
        rep = _run(h, _evidence_task())
        assert rep["state"] == "RESOLVED" and rep["selected"] == "SOSO-P1"
        assert "U05" not in _functions(h, rep)
        assert _results(h, rep, "U05") == []
        assert _events(h, rep, "EVIDENCE_SELECTION") == []
        assert _disabled(h, rep, "U05"), "no FUNCTION_DISABLED for U05"
    finally:
        h.close()


def _u05_replay_harness(disabled=False):
    """Replay path: provider names firmware then kind; the
    selector ranks kind first (cost 1 vs firmware cost 9),
    so the answer order differs from the provider order."""
    src = FrozenEvidenceSource({("fx-ev1", "firmware"): "v1",
                                ("fx-ev1", "kind"): "sensor"})
    sel = MeasurementSelector(
        sources=[src],
        costs={("frozen-evidence", "firmware"): 9,
               ("frozen-evidence", "kind"): 1})
    h = _trained(evidence_source=src,
                 pathway_provider=_ListProvider(["firmware", "kind"]),
                 measurement_selector=sel)
    if disabled:
        h.pipe.set_disabled_functions({"U05"})
    return h


def test_u05_ablation_replay_order_and_selection():
    """Selector arm: expectations are answered kind-then-
    firmware (ranked selection, not provider order), with a
    U05 FUNCTION_RESULT recording selected [kind, firmware].
    U05 disabled: firmware-then-kind (provider order), no
    selection event, no U05 span. Both arms resolve — the
    difference is which measurement the system chose first,
    i.e. the measurement cost paid (2 answers either way,
    in a different order)."""
    h = _u05_replay_harness()
    try:
        rep = _run(h, _evidence_task())
        assert rep["state"] == "RESOLVED" and rep["selected"] == "SOSO-P1"
        order = [e["data"]["condition"] for _, e in
                 _events(h, rep, "EVIDENCE_EXPECTATION")]
        assert order == ["kind", "firmware"]
        results = _results(h, rep, "U05")
        assert len(results) == 1
        assert results[0]["selected"] == ["kind", "firmware"]
        assert results[0]["n_evaluations"] == 2
        assert len(_events(h, rep, "EVIDENCE_SELECTION")) == 1
    finally:
        h.close()
    h = _u05_replay_harness(disabled=True)
    try:
        rep = _run(h, _evidence_task())
        assert rep["state"] == "RESOLVED" and rep["selected"] == "SOSO-P1"
        order = [e["data"]["condition"] for _, e in
                 _events(h, rep, "EVIDENCE_EXPECTATION")]
        assert order == ["firmware", "kind"]
        assert _results(h, rep, "U05") == []
        assert _events(h, rep, "EVIDENCE_SELECTION") == []
        assert _disabled(h, rep, "U05"), "no FUNCTION_DISABLED for U05"
    finally:
        h.close()


# ------------------------------------------------------------------
# L02 — Learn Applicability
# ------------------------------------------------------------------

def test_l02_executes_nested_in_u08_with_applicability():
    """Cross-family slope transfer: exactly two L02 spans, both
    NESTED_IN the U08 span, and each FUNCTION_RESULT carries
    the real input digest and the learned applicability per
    procedure (non-empty tag sets for all four trained
    procedures — not a stub return)."""
    h = _trained()
    try:
        rep = _run(h, dict(MATCHED_TASKS[4]))  # ab-xfer-slope-a
        assert rep["state"] == "RESOLVED"
        assert rep["stage"] == "soso-transfer"
        pathway = reconstruct(h.pipe.trace, rep["task_id"],
                              rep["execution_id"])
        spans = {s["span_id"]: s for s in pathway["spans"]}
        l02 = [s for s in pathway["spans"] if s["function"] == "L02"]
        assert len(l02) == 2, [s["function"] for s in pathway["spans"]]
        nested = {(r["from"], r["to"]) for r in pathway["relationships"]
                  if r["relation"] == "NESTED_IN"}
        for s in l02:
            parents = [t for (f, t) in nested if f == s["span_id"]]
            assert len(parents) == 1
            assert spans[parents[0]]["function"] == "U08"
        results = _results(h, rep, "L02")
        assert len(results) == 2
        for r in results:
            assert r["input"]["tags"] == ["cross", "ground", "slope",
                                          "steep"]
            assert r["input"]["min_support"] == 2
            assert r["input"]["min_precision_pct"] == 70
            assert r["input"]["match_pct"] == 67
            assert r["input"]["validated_only"] is False
            assert set(r["output"]) == {"P2", "P3", "P4", "P7"}
            assert all(r["output"][p] for p in ("P2", "P3", "P4", "P7"))
        assert results[0]["output"]["P7"] == [
            "blocked", "debris", "fallen", "obstacle", "route"]
    finally:
        h.close()


def test_l02_ablation_kills_transfer():
    """L02 disabled: recognise returns [] and cross-task
    transfer is gated off, so the slope task no longer
    resolves by transfer — it falls through to higher-trial
    (same resolution, different pathway; 0 false). No L02
    span or result exists; a FUNCTION_DISABLED event records it."""
    h = _trained()
    try:
        rep = _run(h, dict(MATCHED_TASKS[4]))
        assert rep["stage"] == "soso-transfer"
    finally:
        h.close()
    h = _trained()
    try:
        h.pipe.set_disabled_functions({"L02"})
        rep = _run(h, dict(MATCHED_TASKS[4]))
        assert rep["state"] == "RESOLVED", rep
        assert rep["stage"] == "higher-trial", rep
        assert rep["selected"] == "SOSO-P7"
        assert not any(a["observed"] and a["validation"] == "FAIL"
                       for a in rep["attempts"])
        assert "L02" not in _functions(h, rep)
        assert _results(h, rep, "L02") == []
        assert _disabled(h, rep, "L02"), "no FUNCTION_DISABLED for L02"
    finally:
        h.close()


# ------------------------------------------------------------------
# FUNCTION_RESULT mechanism: recorder pairing intact
# ------------------------------------------------------------------

def test_function_result_does_not_break_recorder_pairing():
    """A run exercising the new spans (exact + transfer +
    evidence) still pairs every OBSERVATION with exactly one
    VALIDATION, with one attempt per pair — FUNCTION_RESULT
    records add no phantom attempts. All three runs certify."""
    src = FrozenEvidenceSource({("fx-ev1", "firmware"): "v1"})
    h = _trained(evidence_source=src)
    try:
        tasks = [dict(MATCHED_TASKS[0]), dict(MATCHED_TASKS[4]),
                 _evidence_task()]
        for task in tasks:
            rep = _run(h, task)
            assert rep["state"] == "RESOLVED", (task["task_id"], rep)
            assert rep["trace_certified"] is True, \
                rep["trace_certification_gaps"]
            evs = _events(h, rep)
            obs = [e for _, e in evs if e["event_type"] == "OBSERVATION"]
            val = [e for _, e in evs if e["event_type"] == "VALIDATION"]
            frr = [e for _, e in evs
                   if e["event_type"] == "FUNCTION_RESULT"]
            assert len(obs) == len(val) == len(rep["attempts"]), (
                task["task_id"], len(obs), len(val), len(rep["attempts"]))
            assert frr, f"no FUNCTION_RESULT on {task['task_id']}"
            assert all(e["event_type"] != "OBSERVATION" for e in frr)
    finally:
        h.close()
