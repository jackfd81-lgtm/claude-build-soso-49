"""Build 043 — four-mode investigation orchestration over the 42.1.2 baseline.

Modes (selected when the investigation starts, stored with the case, fixed
across HOLD and resume; joined_lower_first is the proposed default):

- lower_only: Lower works independently; anything it cannot resolve is
  recorded as an explicit outcome. Higher is never invoked.
- higher_only: Higher works with the evidence supplied to it or already
  available to its route; anything it still needs is recorded as an explicit
  outcome. Lower is never invoked.
- joined_lower_first: Lower produces a result that Higher actually consumes
  before the task can finish successfully.
- joined_higher_first: Higher produces a concrete question, requirement or
  work item that Lower actually consumes; Lower can return evidence for
  Higher to review.

Named responsibilities (no new numeric slot assignments in 43 — the handoff
reserves those for stable jobs with executed evidence and a documented
migration):

- OpeningSequence (C06 seven checks, from contract.cognitive_map).
- Association / Join (AssociationEdge where the case declares origin/target
  specialties; otherwise the same typed link without a specialty edge).
- InformationRequirement (ResolutionRecord + its matching predicates).
- TransitionRecorder (InvocationEvent + InvestigationRecorder).
- MultiSlotReview (outputs with origins; disagreements preserved;
  CoherenceReview / L34-U33 for specialist routes).
- ReadingMatcher (ResolutionRecord.apply_observation + ReadingMatcher).
- Gap Review / Path Suggestions (U11 gaps, U12 discriminator, via
  execute_typed).
- Reconciliation / Closing Review (InvestigationReconciliation,
  ClosingReview, OutcomeReview).

Every slot execution in an investigation goes through the typed boundary
(contract.slot_specs.execute_typed). A generic-only slot returns
UNSUPPORTED there and can never satisfy a crossing: a join is CONSUMED only
when the consumer slot executed with status OK and its input reference is
the producer's output reference. A default region response (registry
invoke) is never a crossing.

Short-term state: the case working view (mode, revision, completed outputs,
waiting consumer slots, outstanding requirements, resume point) is the live
case state. It is persisted through the existing durable path — the CRAM-PU
bridge (COOL authoritative; the mirror holds only a short reference, the
same pattern as the 42.1.0 AssociationEdge) — so a case can be restored
after a restart with its mode, revision and HOLD intact. The case revision
is the investigation's logical revision: it advances only when an accepted
observation is consumed once. The COOL revision is the storage revision and
advances on every save; the two are recorded separately and must not be
confused. Reusable long-term experience (learned profiles) stays distinct
from the current investigation's workspace.

Bounded resumption: a case accepts at most MAX_RESUMPTIONS accepted
observations; a resume that consumes nothing new twice in a row is recorded
as NO_PROGRESS and the case closes UNRESOLVED. Denied, waiting, expired
and unresolved runs remain valid recorded outcomes.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from contract.cognitive_map import (
    AssociationEdge,
    ClosingReview,
    CoherenceReview,
    CognitiveMap,
    InvestigationReconciliation,
    InvestigationRecorder,
    InvocationEvent,
    OpeningSequence,
    OutcomeReview,
    ReadingMatcher,
    ResolutionRecord,
    RouteAdvisor,
    ValidationError,
)
from contract.slot_specs import SLOT_MAP_VERSION, describe, execute_typed

MODES = ("lower_only", "higher_only", "joined_lower_first", "joined_higher_first")
MODE_DEFAULT = "joined_lower_first"
INDEPENDENT_MODES = ("lower_only", "higher_only")
# A case in one of these statuses is terminal: no further slot runs, in any
# mode. Independent modes have no supported resume/reopen path, so a later
# observation leaves an independent case terminal (see receive_observation).
TERMINAL_STATUSES = ("COMPLETED", "UNRESOLVED", "EXPIRED", "DENIED")

CASE_SCHEMA_VERSION = "case-state/43.0"
CASE_FAMILY = "SMALL49"
CASE_BUCKET = "workspace_large"
# Short-lived workspace region: Small49 local indices 384..447 (global
# blocks 1984..2047). Each case gets one block; each save consumes one
# mirror slot in the workspace_large bucket (64 slots per state directory),
# so a state directory supports 64 case saves in total. Tests and the proof
# use fresh state per run and stay well under that bound.
CASE_BLOCK_BASE = 384
CASE_BLOCK_COUNT = 64
MAX_RESUMPTIONS = 8
MAX_NO_PROGRESS = 2


def case_block_id(n: int) -> int:
    """Global block id for the n-th case block (0-based)."""
    if not 0 <= n < CASE_BLOCK_COUNT:
        raise ValueError(f"case block {n} out of range 0..{CASE_BLOCK_COUNT - 1}")
    return 1600 + CASE_BLOCK_BASE + n


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _tier(slot_id: str) -> str:
    return "LOWER" if slot_id.startswith("L") else "HIGHER"


def _auth(case_id: str) -> dict[str, Any]:
    # AUTH:0, as in the 42.1 brief: claims and provenance remain AUTH:0.
    return {"authority": 0, "task_id": f"build43:{case_id}"}


def new_case(case_id: str, *, mode: str = MODE_DEFAULT, goal: str,
             evidence_snapshot: dict[str, Any] | None = None,
             evidence_refs: list[dict[str, Any]] | None = None,
             scope: list[str] | None = None,
             origin_specialty: str | None = None,
             target_specialty: str | None = None,
             max_resumptions: int = MAX_RESUMPTIONS) -> dict[str, Any]:
    """Build the working view for a new case (not yet persisted)."""
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode!r} (expected one of {MODES})")
    if not goal:
        raise ValueError("a case needs a goal")
    snapshot = evidence_snapshot or {}
    opening = OpeningSequence.check(
        case_id, goal, scope or [], snapshot, [], [],
        f"{mode}:start",
    )
    return {
        "schema_version": CASE_SCHEMA_VERSION,
        "case_id": case_id,
        "mode": mode,
        "goal": goal,
        "revision": 0,
        "status": "OPEN",
        "inputs": {"evidence_snapshot": snapshot, "evidence_refs": evidence_refs or []},
        "scope": list(scope or []),
        "opening": opening,
        "outputs": [],
        "joins": [],
        "requirements": [],
        "waiting": [],
        "observations": [],
        "events": [],
        "invocations": [],
        "decision": None,
        "decision_history": [],
        "reconciliation": None,
        "reviews": [],
        "resume_point": "start",
        "resumptions": 0,
        "max_resumptions": max_resumptions,
        "no_progress_streak": 0,
        "durable": None,
        "origin_specialty": origin_specialty,
        "target_specialty": target_specialty,
        "slot_map_version": SLOT_MAP_VERSION,
    }


def _validate_case(case: dict[str, Any]) -> None:
    if case.get("schema_version") != CASE_SCHEMA_VERSION:
        raise ValidationError(
            f"unsupported case-state version: {case.get('schema_version')!r}", "VERSION")
    if case.get("mode") not in MODES:
        raise ValidationError(f"unknown mode in case state: {case.get('mode')!r}", "MODE")
    if not case.get("case_id"):
        raise ValidationError("case_id is required", "CASE_ID")


class Investigation:
    """Runs four-mode investigations over a SoSoCloset (continuity + Suit).

    ``closet`` is the object yielded by ``build040_runtime.standalone.
    local_runtime`` (``.continuity``, ``.suit``); ``agent_id`` is the Suit
    agent that owns the case blocks. The caller authorizes that agent for
    the case blocks it will use (``case_block_id(n)`` for n = 0, 1, ... in
    creation order) before starting cases.
    """

    def __init__(self, closet: Any, agent_id: str, *,
                 max_resumptions: int = MAX_RESUMPTIONS) -> None:
        self.closet = closet
        self.agent_id = agent_id
        self.max_resumptions = max_resumptions
        self._next_block = 0
        self._cases: dict[str, dict[str, Any]] = {}
        self._case_blocks: dict[str, int] = {}
        self._recorders: dict[str, InvestigationRecorder] = {}

    # ------------------------------------------------------------------
    # Persistence (existing durable path: CRAM-PU bridge, COOL authoritative)
    # ------------------------------------------------------------------
    def _alloc_block(self, case_id: str) -> int:
        if case_id in self._case_blocks:
            return self._case_blocks[case_id]
        if self._next_block >= CASE_BLOCK_COUNT:
            raise ValidationError("no free case block in the workspace_large region", "CASE_BLOCK")
        n = self._next_block
        self._next_block += 1
        self._case_blocks[case_id] = n
        return n

    def save_case(self, case: dict[str, Any]) -> dict[str, Any]:
        """Persist the working view via the bridge; return the COOL commit.

        The full state lives under the ``case_state`` payload key (COOL
        authoritative). The mirror ``content`` is only a short reference,
        so the preflight char cap never rejects a case save; the mirror
        reference therefore reports reconstructs_structured_payload False.
        The case's ``durable`` field is updated in memory with this save's
        commit record; the persisted copy carries the previous save's
        durable record (a save cannot contain its own commit record).
        """
        _validate_case(case)
        n = self._alloc_block(case["case_id"])
        block_id = case_block_id(n)
        payload = {
            "content": f"case-state:{case['case_id']}:r{case['revision']}",
            "case_state": copy_state(case),
            "relation": "ASSOCIATED_WITH",
        }
        # Through the Suit path (grant enforced: a revoked agent is DENIED
        # here and the denial is recorded on the case). The mirror bucket
        # is workspace_large; the mirror content is only the short reference.
        try:
            commit, pointer = self.closet.suit.execute_memory_write(
                self.agent_id, CASE_FAMILY, CASE_BLOCK_BASE + n, payload,
                bucket=CASE_BUCKET,
            )
        except Exception as exc:  # noqa: BLE001 - the denial is the outcome
            from crampu.controller import AuthorizationError
            if isinstance(exc, AuthorizationError):
                case["status"] = "DENIED"
                case["save_error"] = {"exception_type": type(exc).__name__, "message": str(exc)}
            raise
        case["durable"] = {
            "family": CASE_FAMILY,
            "local_index": CASE_BLOCK_BASE + n,
            "bucket": CASE_BUCKET,
            "block_id": block_id,
            "cool_revision": int(commit["revision"]),
            "txid": commit.get("txid"),
            "sha256": commit.get("sha256"),
            "cool_reference": commit.get("cool_reference"),
            "mirror_reference": commit.get("mirror_reference"),
            "pointer": {"record_id": pointer.record_id, "pool": pointer.pool,
                        "digest": pointer.digest, "version": pointer.version},
        }
        return commit

    def load_case(self, case_id: str, block_n: int) -> dict[str, Any]:
        """Restore a case from COOL after a restart.

        The restored case keeps its mode, logical revision and HOLD status
        exactly as saved. Its ``durable`` field is set from the read itself
        (COOL revision, txid, digest), not from the saved copy.
        """
        # Through the Suit path (revision-bound read: payload and reference
        # describe the same revision).
        data = self.closet.suit.execute_memory_read(
            self.agent_id, CASE_FAMILY, CASE_BLOCK_BASE + block_n)
        payload = data["payload"]
        if not isinstance(payload.get("case_state"), dict):
            raise ValidationError(f"block {case_block_id(block_n)} holds no case state", "CASE_STATE")
        case = payload["case_state"]
        _validate_case(case)
        case["invocations"] = _rebuild_invocations(case)
        if case["case_id"] != case_id:
            raise ValidationError(
                f"block {case_block_id(block_n)} holds case {case['case_id']!r}, not {case_id!r}",
                "CASE_ID")
        case["durable"] = {
            "family": CASE_FAMILY,
            "local_index": CASE_BLOCK_BASE + block_n,
            "bucket": CASE_BUCKET,
            "block_id": case_block_id(block_n),
            "cool_revision": int(data["revision"]),
            "txid": data.get("txid"),
            "sha256": data.get("sha256"),
            "cool_reference": data.get("cool_reference"),
            "mirror_reference": None,  # loaded from COOL; the mirror is only a projection
            "pointer": None,
        }
        self._cases[case_id] = case
        self._case_blocks[case_id] = block_n
        self._next_block = max(self._next_block, block_n + 1)
        self._recorders[case_id] = InvestigationRecorder(case_id, case["goal"])
        for ev_dict in case["events"]:
            self._recorders[case_id].record(InvocationEvent.from_dict(ev_dict))
        return case

    def resume_after_restart(self, case_id: str, block_n: int) -> dict[str, Any]:
        """Load a case after a restart and report what was restored."""
        case = self.load_case(case_id, block_n)
        self.closet.continuity.record_event(
            case_id, "ROUTING_DECISION", "investigation",
            {"decision": "RESUME", "mode": case["mode"], "revision": case["revision"],
             "status": case["status"], "resume_point": case["resume_point"],
             "cool_revision": case["durable"]["cool_revision"]})
        return case

    def get_case(self, case_id: str) -> dict[str, Any]:
        try:
            return self._cases[case_id]
        except KeyError:
            raise KeyError(f"unknown case (not started or loaded here): {case_id!r}") from None

    # ------------------------------------------------------------------
    # Typed execution + transition recording
    # ------------------------------------------------------------------
    def _record_event(self, case: dict[str, Any], *, slot_id: str, input_ref: str,
                      output_ref: str, disposition: str, branch_reason: str = "",
                      parent_id: str | None = None) -> InvocationEvent:
        seq = len(case["events"]) + 1
        predecessor = case["events"][-1]["output_ref"] if case["events"] else None
        event = InvocationEvent(
            seq=seq,
            case_revision=case["revision"],
            invocation_id=f"{case['case_id']}:inv:{seq}",
            attempt_id=f"{case['case_id']}:att:{seq}",
            slot_id=slot_id,
            selected=slot_id,
            input_ref=input_ref,
            output_ref=output_ref,
            disposition=disposition,
            parent_id=parent_id,
            predecessor_id=predecessor,
            registry_version=SLOT_MAP_VERSION,
            rejected_alternatives=[],
        )
        event.validate()
        case["events"].append(event.to_dict())
        self._recorders[case["case_id"]].record(event)
        # Continuity kinds are a closed set; a slot execution is recorded
        # as MODEL_OUTPUT (the slot's output), a HOLD/resume as
        # ROUTING_DECISION (a routing/state decision).
        self.closet.continuity.record_event(
            case["case_id"], "MODEL_OUTPUT", slot_id,
            {"input_ref": input_ref, "output_ref": output_ref, "disposition": disposition,
             "case_revision": case["revision"], "branch_reason": branch_reason})
        return event

    def _execute(self, case: dict[str, Any], slot_id: str, *args: Any,
                 input_ref: str, output_ref: str, branch_reason: str = "",
                 disposition: str | None = None) -> dict[str, Any]:
        """Run one slot through the typed boundary and record the crossing.

        Returns the execute_typed result ({function_id, handler, status, code,
        result|error}). A generic-only slot returns UNSUPPORTED here and is
        never recorded as a produced output. ``disposition`` overrides the
        recorded event disposition (e.g. CONFLICT_PRESERVED for L18).
        """
        out = execute_typed(slot_id, _auth(case["case_id"]), *args)
        status = out["status"]
        if disposition is None:
            if status == "OK":
                disposition = "OK"
            elif status == "UNSUPPORTED":
                disposition = "UNSUPPORTED"
            else:
                disposition = "REJECTED"
        self._record_event(case, slot_id=slot_id, input_ref=input_ref,
                           output_ref=output_ref, disposition=disposition,
                           branch_reason=branch_reason)
        case["invocations"].append({
            "slot_id": slot_id, "tier": _tier(slot_id), "status": status,
            "code": out.get("code"), "handler": out.get("handler"),
            "input_ref": input_ref, "output_ref": output_ref,
            "case_revision": case["revision"],
        })
        if status == "OK":
            case["outputs"].append({
                "slot_id": slot_id, "output_ref": output_ref,
                "result_summary": _summarize(out.get("result")),
                "revision": case["revision"], "consumed_by": [],
            })
        return out

    # ------------------------------------------------------------------
    # Opening + mode dispatch
    # ------------------------------------------------------------------
    def start_case(self, case_id: str, *, mode: str = MODE_DEFAULT, goal: str,
                   evidence_snapshot: dict[str, Any] | None = None,
                   evidence_refs: list[dict[str, Any]] | None = None,
                   scope: list[str] | None = None,
                   origin_specialty: str | None = None,
                   target_specialty: str | None = None,
                   acceptable_sources: list[str] | None = None,
                   hypotheses: dict[str, Any] | None = None,
                   observed: dict[str, Any] | None = None) -> dict[str, Any]:
        if case_id in self._cases:
            raise ValueError(f"case already started here: {case_id!r}")
        case = new_case(case_id, mode=mode, goal=goal,
                        evidence_snapshot=evidence_snapshot,
                        evidence_refs=evidence_refs, scope=scope,
                        origin_specialty=origin_specialty,
                        target_specialty=target_specialty,
                        max_resumptions=self.max_resumptions)
        if acceptable_sources is not None:
            case["inputs"]["acceptable_sources"] = list(acceptable_sources)
        if hypotheses is not None:
            case["inputs"]["hypotheses"] = hypotheses
        if observed is not None:
            case["inputs"]["observed"] = observed
        self._cases[case_id] = case
        self._recorders[case_id] = InvestigationRecorder(case_id, goal)
        self.closet.continuity.start_session(case_id, f"Start {case_id} ({mode})")
        self.save_case(case)
        return case

    def _tiers_invoked(self, case: dict[str, Any]) -> set[str]:
        return {inv["tier"] for inv in case["invocations"]}

    def _record_outcome(self, case: dict[str, Any], *, status: str, outcome: str,
                        requirement: dict[str, Any] | None = None) -> dict[str, Any]:
        case["status"] = status
        if requirement is not None:
            case["requirements"].append(requirement)
        case["resume_point"] = f"{status.lower()}:{outcome}"
        self.save_case(case)
        return case


    # ------------------------------------------------------------------
    # Routes (typed execution only; no generic registry responses)
    # ------------------------------------------------------------------
    def _lower_route(self, case: dict[str, Any],
                     observations: list[dict[str, Any]], *,
                     work_ref: str | None = None) -> dict[str, Any] | None:
        """Lower route: L03 parse -> L04 entities -> L16 relations -> L22
        order -> L17 compare -> L18 conflicts -> L31 sufficiency.

        Returns the Lower result ({decision, accepted, conflicts, groups,
        output_ref}) or None when the route cannot run (no parseable
        observation), in which case the caller records the requirement.
        """
        cid = case["case_id"]
        rev = case["revision"]
        parsed = self._execute(
            case, "L03", observations,
            input_ref=work_ref or f"{cid}:evidence:in", output_ref=f"{cid}:lower:L03:r{rev}")
        if parsed["status"] != "OK":
            return None
        accepted = parsed["result"].get("accepted", [])
        if not accepted:
            return None
        entities = self._execute(
            case, "L04", accepted,
            input_ref=f"{cid}:lower:L03:r{rev}", output_ref=f"{cid}:lower:L04:r{rev}")
        index = entities["result"].get("index", {}) if entities["status"] == "OK" else {}
        self._execute(
            case, "L16", index,
            input_ref=f"{cid}:lower:L04:r{rev}", output_ref=f"{cid}:lower:L16:r{rev}")
        self._execute(
            case, "L22", accepted,
            input_ref=f"{cid}:lower:L16:r{rev}", output_ref=f"{cid}:lower:L22:r{rev}")
        pairs = self._execute(
            case, "L17", accepted,
            input_ref=f"{cid}:lower:L22:r{rev}", output_ref=f"{cid}:lower:L17:r{rev}")
        pair_list = pairs["result"] if pairs["status"] == "OK" else []
        conflicts_out = self._execute(
            case, "L18", pair_list,
            input_ref=f"{cid}:lower:L17:r{rev}", output_ref=f"{cid}:lower:L18:r{rev}",
            disposition="CONFLICT_PRESERVED" if pairs["status"] == "OK" and _has_conflicts(pair_list) else None)
        conflicts = conflicts_out["result"] if conflicts_out["status"] == "OK" else []
        decision_out = self._execute(
            case, "L31", accepted, conflicts, False,
            input_ref=f"{cid}:lower:L18:r{rev}", output_ref=f"{cid}:lower:L31:r{rev}")
        if decision_out["status"] != "OK":
            return None
        decision = decision_out["result"]
        return {
            "decision": decision, "accepted": accepted, "conflicts": conflicts,
            "output_ref": f"{cid}:lower:L31:r{rev}",
        }

    def _higher_route(self, case: dict[str, Any], *, hypotheses: dict[str, Any],
                      observed: dict[str, Any],
                      relevant_rows: list[dict[str, Any]],
                      lower_decision: dict[str, Any] | None = None,
                      lower_output_ref: str | None = None) -> dict[str, Any]:
        """Higher route: U11 gaps -> U12 discriminator -> U13 evaluate causes
        -> U16 synthesize. When a Lower decision is supplied (joined modes)
        it is the decision U16 consumes; otherwise the decision is derived
        from the remaining candidates (one remaining -> SUPPORTED, none or
        several -> ABSTAIN).
        """
        cid = case["case_id"]
        rev = case["revision"]
        gaps_out = self._execute(
            case, "U11", hypotheses, observed,
            input_ref=f"{cid}:higher:evidence:in", output_ref=f"{cid}:higher:U11:r{rev}")
        partitions = gaps_out["result"] if gaps_out["status"] == "OK" else {}
        self._annotate_last_event(case, rejected_alternatives=[
            {"alternative": f"missing:{feature}", "reason": "unobserved discriminator"}
            for feature in partitions])
        disc_out = self._execute(
            case, "U12", partitions, _preferred_profile(partitions),
            input_ref=f"{cid}:higher:U11:r{rev}", output_ref=f"{cid}:higher:U12:r{rev}")
        discriminator = disc_out["result"] if disc_out["status"] == "OK" else {"feature": None, "used_method": False}
        eval_out = self._execute(
            case, "U13", hypotheses, observed,
            input_ref=f"{cid}:higher:U12:r{rev}", output_ref=f"{cid}:higher:U13:r{rev}")
        remaining = eval_out["result"].get("remaining", {}) if eval_out["status"] == "OK" else {}
        if lower_decision is not None:
            decision = lower_decision
        elif len(remaining) == 1:
            only = next(iter(remaining))
            decision = {"status": "SUPPORTED", "answer": only, "negated": False,
                        "support": [row["digest"] for row in relevant_rows]}
        elif len(remaining) == 0:
            decision = {"status": "ABSTAIN", "support": [],
                        "reason": "no consistent candidate"}
        else:
            decision = {"status": "ABSTAIN", "support": [],
                        "reason": "multiple candidate conclusions"}
        # The U16 input reference is the Lower output it consumes (when a
        # Lower decision is supplied), so the crossing is verifiable in the
        # invocation log: consumer input ref == producer output ref.
        synth_out = self._execute(
            case, "U16", decision, relevant_rows,
            input_ref=lower_output_ref or f"{cid}:higher:U13:r{rev}",
            output_ref=f"{cid}:higher:U16:r{rev}")
        synthesized = synth_out["result"] if synth_out["status"] == "OK" else None
        return {
            "decision": decision, "synthesized": synthesized,
            "gaps": partitions, "discriminator": discriminator,
            "remaining": remaining,
            "output_ref": f"{cid}:higher:U16:r{rev}",
        }

    # ------------------------------------------------------------------
    # Joins (typed producer output -> typed consumer input)
    # ------------------------------------------------------------------
    def _make_join(self, case: dict[str, Any], *, producer_slot: str,
                   producer_output_ref: str, consumer_slot: str,
                   input_port: str, direction: str,
                   activation_condition: str,
                   required_inputs: list[dict[str, Any]]) -> dict[str, Any]:
        join_id = f"{case['case_id']}:join:{len(case['joins']) + 1}"
        edge = None
        # An AssociationEdge crosses specialties (its validation requires
        # origin != target). A join inside one specialty, or with no
        # declared specialties, is the same typed link without an edge.
        if (case.get("origin_specialty") and case.get("target_specialty")
                and case["origin_specialty"] != case["target_specialty"]):
            edge = AssociationEdge(
                edge_id=join_id,
                origin_specialty=case["origin_specialty"],
                target_specialty=case["target_specialty"],
                source_op=producer_slot,
                target_op=consumer_slot,
                purpose=f"Build 043 join {direction}: {producer_slot} -> {consumer_slot}",
                source_output_port=producer_output_ref,
                target_input_port=input_port,
                permitted_scope=list(case.get("scope", [])),
            )
            edge.validate()
        join = {
            "join_id": join_id,
            "producer": {"slot_id": producer_slot, "output_ref": producer_output_ref},
            "consumer": {"slot_id": consumer_slot, "input_port": input_port},
            "direction": direction,
            "activation_condition": activation_condition,
            "required_inputs": required_inputs,
            "status": "WAITING",
            "edge": edge.to_dict() if edge is not None else None,
        }
        case["joins"].append(join)
        return join

    def create_join(self, case: dict[str, Any], *, producer_slot: str,
                    producer_output_ref: str, consumer_slot: str,
                    input_port: str, direction: str,
                    activation_condition: str,
                    required_inputs: list[dict[str, Any]]) -> dict[str, Any]:
        """Create a join (Association/Join responsibility) without consuming
        it. A join may wait for several specified inputs; it is ready only
        when every required input is present among the case outputs."""
        join = self._make_join(
            case, producer_slot=producer_slot, producer_output_ref=producer_output_ref,
            consumer_slot=consumer_slot, input_port=input_port,
            direction=direction, activation_condition=activation_condition,
            required_inputs=required_inputs)
        self.save_case(case)
        return join

    def check_join_ready(self, case: dict[str, Any], join: dict[str, Any]) -> bool:
        """True when every required input of the join is present among the
        case's produced outputs."""
        produced_refs = {o["output_ref"] for o in case["outputs"]}
        return all(req.get("output_ref") in produced_refs
                   for req in join["required_inputs"])

    def join_lower_to_higher(self, case: dict[str, Any], *,
                             lower_output_ref: str, consumer_slot: str = "U16",
                             input_port: str = "decision",
                             join: dict[str, Any] | None = None) -> dict[str, Any]:
        """Cross a Lower result into Higher. The join is CONSUMED only when
        the consumer slot executes with status OK and its input reference is
        the producer's output reference. A generic-only consumer returns
        UNSUPPORTED and the crossing fails (case UNRESOLVED). When ``join``
        is given, that join is consumed (its required inputs must all be
        present); otherwise a single-input join is created for the crossing.
        """
        cid = case["case_id"]
        produced = [o for o in case["outputs"] if o["output_ref"] == lower_output_ref]
        if not produced:
            raise ValueError(f"no produced output with ref {lower_output_ref!r}")
        producer_slot = produced[0]["slot_id"]
        if join is None:
            join = self._make_join(
                case, producer_slot=producer_slot, producer_output_ref=lower_output_ref,
                consumer_slot=consumer_slot, input_port=input_port,
                direction="LOWER_TO_HIGHER", activation_condition="producer_output_present",
                required_inputs=[{"slot_id": producer_slot, "output_ref": lower_output_ref}])
        else:
            if not self.check_join_ready(case, join):
                join["status"] = "WAITING"
                self.save_case(case)
                return join
        if case["mode"] in INDEPENDENT_MODES:
            # A join is a crossing by definition; an independent-mode case
            # has no crossing. No slot runs and the case status is untouched.
            join["status"] = "FAILED"
            join["failure"] = {"code": "MODE_MISMATCH",
                               "message": f"case mode {case['mode']!r} is independent; "
                                          "a Lower-to-Higher crossing is not available"}
            self.save_case(case)
            return join
        spec = describe(consumer_slot)
        if spec.state == "generic-only":
            join["status"] = "FAILED"
            join["failure"] = {"code": "UNSUPPORTED_SLOT",
                               "message": f"{consumer_slot} is generic-only; no bound operation to consume the Lower result"}
            case["status"] = "UNRESOLVED"
            case["resume_point"] = f"join-failed:{join['join_id']}"
            self.save_case(case)
            return join
        join["status"] = "SATISFIED"
        # The consumer input is the Lower decision itself (U16 consumes a
        # decision + the relevant rows). The decision is recovered from the
        # produced output's recorded summary (the L31 decision: status,
        # support, answer), so the join does not depend on the case decision
        # having been set by a mode dispatch first.
        lower_decision = produced[0].get("result_summary")
        expected_ref = f"{cid}:lower:L31:r{case['revision']}"
        invalid = (
            consumer_slot != "U16"
            or input_port != "decision"
            or producer_slot != "L31"
            or lower_output_ref != expected_ref
            or not isinstance(lower_decision, dict)
            or lower_decision.get("status") not in {"SUPPORTED", "CONFLICT", "ABSTAIN"}
            or not isinstance(lower_decision.get("support"), list)
        )
        if invalid:
            join["status"] = "FAILED"
            join["failure"] = {"code": "INVALID_DECISION_HANDOFF"}
            case["status"] = "UNRESOLVED"
            case["resume_point"] = f"join-failed:{join['join_id']}"
            self.save_case(case)
            return join
        relevant = case["inputs"].get("accepted_rows", [])
        higher = self._higher_route(
            case, hypotheses=case["inputs"].get("hypotheses", {}),
            observed=case["inputs"].get("observed", {}),
            relevant_rows=relevant, lower_decision=lower_decision,
            lower_output_ref=lower_output_ref)
        if higher["synthesized"] is not None and any(
            item["slot_id"] == "U16" and item["status"] == "OK"
            and item["input_ref"] == lower_output_ref
            and item["output_ref"] == higher["output_ref"]
            and item["case_revision"] == case["revision"]
            for item in case["invocations"]
        ):
            join["status"] = "CONSUMED"
            join["consumed_output_ref"] = higher["output_ref"]
            for output in case["outputs"]:
                if output["output_ref"] == lower_output_ref:
                    output["consumed_by"].append(consumer_slot)
            # The case decision is the Higher synthesis (the consumed Lower
            # decision plus the presented sources), not the Lower decision.
            self._set_decision(case, higher["synthesized"], disposition="CONSUMED")
        else:
            join["status"] = "FAILED"
            join["failure"] = {"code": "CONSUMER_FAILED",
                               "message": f"{consumer_slot} did not return a synthesis"}
            case["status"] = "UNRESOLVED"
            case["resume_point"] = f"join-failed:{join['join_id']}"
        self.save_case(case)
        return join

    def _annotate_last_event(self, case: dict[str, Any], **fields: Any) -> None:
        """Annotate the most recent transition event (both the recorded
        InvocationEvent object and the persisted event dict) after the fact
        — e.g. the gap features a U11 call found, which are the case's open
        unknowns in the replayed map."""
        recorder = self._recorders[case["case_id"]]
        if not recorder.events or not case["events"]:
            return
        event = recorder.events[-1]
        for key, value in fields.items():
            setattr(event, key, value)
        event.validate()
        case["events"][-1] = event.to_dict()

    def _set_decision(self, case: dict[str, Any], decision: dict[str, Any],
                      disposition: str) -> None:
        case["decision_history"].append({
            "revision": case["revision"], "decision": decision,
            "disposition": disposition,
            "uncertainty": case.get("uncertainty", []),
            "evidence_refs": [o["output_ref"] for o in case["outputs"]],
        })
        case["decision"] = decision

    # ------------------------------------------------------------------
    # Information requirements + HOLD
    # ------------------------------------------------------------------
    def request_information(self, case: dict[str, Any], *, consumer_slot: str,
                            required_feature: str, source_ids: list[str],
                            units: str = "", time_window: str = "",
                            release_condition: str = "",
                            hold: bool = True) -> dict[str, Any]:
        """Create a pending requirement.

        The requirement identifies the request id, case and revision,
        intended consumer, required feature, acceptable source/scope, units
        and time window when applicable, and the release condition. With
        ``hold=True`` (joined modes) the case goes on HOLD with a resume
        point and the consumer waits. With ``hold=False`` (independent modes)
        the requirement is recorded as an explicit outcome: the case does
        not wait and no waiting entry is added; the caller sets the final
        status (UNRESOLVED).
        """
        record = ResolutionRecord(
            request_id=f"{case['case_id']}:req:{len(case['requirements']) + 1}",
            case_revision=case["revision"],
            required_feature=required_feature,
            scope_predicate=f"source_id in {sorted(source_ids)}",
            units_predicate=units,
            time_predicate=time_window,
            release_condition=release_condition or f"conclusive reading for {required_feature}",
            supporting_evidence=list(source_ids),
            disposition="PENDING",
        )
        record.validate()
        case["requirements"].append(record.to_dict())
        if hold:
            case["waiting"].append({"slot_id": consumer_slot, "requirement_id": record.request_id})
            case["status"] = "HOLD"
            case["resume_point"] = f"requirement:{record.request_id}"
            case["hold_event_seq"] = len(case["events"])
            self.closet.continuity.record_event(
                case["case_id"], "ROUTING_DECISION", consumer_slot,
                {"decision": "HOLD", "request_id": record.request_id,
                 "required_feature": required_feature,
                 "case_revision": case["revision"], "resume_point": case["resume_point"]})
        else:
            self.closet.continuity.record_event(
                case["case_id"], "ROUTING_DECISION", consumer_slot,
                {"decision": "UNRESOLVED_REQUIREMENT", "request_id": record.request_id,
                 "required_feature": required_feature,
                 "case_revision": case["revision"]})
        self.save_case(case)
        return record.to_dict()

    def classify_availability(self, case: dict[str, Any],
                              required_features: list[str],
                              optional_features: list[str] | None = None) -> dict[str, Any]:
        """Classify availability separately from required / optional /
        irrelevant. ``required`` are the features the case still needs,
        ``available`` are required features already present in the evidence
        snapshot, ``optional`` are present but not required, ``irrelevant``
        are neither required nor present.
        """
        snapshot = case["inputs"].get("evidence_snapshot", {})
        present = set()
        for entry in snapshot.values():
            if isinstance(entry, dict) and "key" in entry:
                present.add(entry["key"])
        required = set(required_features)
        optional = set(optional_features or [])
        return {
            "required": sorted(required - present),
            "available": sorted(required & present),
            "optional": sorted((optional & present) - required),
            "irrelevant": sorted((set(snapshot.keys()) | present) - required - optional),
            "missing": sorted(required - present),
        }

    def join_higher_to_lower(self, case: dict[str, Any], *,
                             requirement: dict[str, Any], consumer_slot: str,
                             input_port: str = "requirement") -> dict[str, Any]:
        """Cross a Higher requirement into Lower: the requirement itself is
        the producer output, and the Lower consumer waits for a reading that
        releases it. The join is CONSUMED when the reading arrives and the
        Lower slot executes on it (see receive_observation).
        """
        join = self._make_join(
            case, producer_slot="U12", producer_output_ref=requirement["request_id"],
            consumer_slot=consumer_slot, input_port=input_port,
            direction="HIGHER_TO_LOWER",
            activation_condition="requirement_released_by_matching_reading",
            required_inputs=[{"slot_id": "U12", "output_ref": requirement["request_id"],
                              "feature": requirement["required_feature"]}])
        if case["mode"] in INDEPENDENT_MODES:
            # Same rule as join_lower_to_higher: an independent-mode case
            # has no crossing, so the join fails without waiting or running.
            join["status"] = "FAILED"
            join["failure"] = {"code": "MODE_MISMATCH",
                               "message": f"case mode {case['mode']!r} is independent; "
                                          "a Higher-to-Lower crossing is not available"}
            self.save_case(case)
            return join
        join["status"] = "SATISFIED"
        self.save_case(case)
        return join

    # ------------------------------------------------------------------
    # Reading matcher + resumption (the HOLD / resume path)
    # ------------------------------------------------------------------
    def receive_observation(self, case_id: str, observation: dict[str, Any]) -> dict[str, Any]:
        """Match one incoming observation against the case's open requirements.

        A mismatch (wrong feature, stale revision, duplicate, missing
        evidence, inconclusive) is recorded as an observation and leaves the
        request open; the case stays on HOLD and its logical revision does
        not advance. A match releases the requirement, consumes the
        observation once, advances the revision, and dispatches the waiting
        slot.

        The saved mode governs this entry point: an independent-mode case
        (``lower_only`` / ``higher_only``) has no supported resume/reopen
        path — its requirement is a recorded outcome, not a wait — so a
        later observation is recorded but never consumed, no slot runs, and
        the case keeps its mode, status, revision and resumption count. A
        terminal case reports outcome ``TERMINAL``; a non-terminal
        independent case reports ``NOT_RESUMABLE``. The mode and status are
        read from the saved case, so this holds after a restart as well as
        in the same run.
        """
        case = self.get_case(case_id)
        obs = dict(observation)
        obs_id = obs.get("observation_id") or f"{case_id}:obs:{len(case['observations']) + 1}"
        obs["observation_id"] = obs_id
        if case["mode"] in INDEPENDENT_MODES:
            outcome = ("TERMINAL" if case["status"] in TERMINAL_STATUSES
                       else "NOT_RESUMABLE")
            record = {**obs, "outcome": outcome, "consumed": False}
            case["observations"].append(record)
            self.save_case(case)
            return {"outcome": outcome, "case": case,
                    "detail": {"mode": case["mode"], "status": case["status"],
                               "reason": "independent mode has no resume path"}}
        open_reqs = [r for r in case["requirements"] if r["disposition"] == "PENDING"]
        if not open_reqs:
            record = {**obs, "outcome": "NO_OPEN_REQUIREMENT", "consumed": False}
            case["observations"].append(record)
            self.save_case(case)
            return {"outcome": "NO_OPEN_REQUIREMENT", "case": case}
        req_index = next(
            i for i, r in enumerate(case["requirements"])
            if r["request_id"] == open_reqs[0]["request_id"])
        record_req = ResolutionRecord.from_dict(open_reqs[0])
        outcome = record_req.apply_observation(obs)
        case["requirements"][req_index] = record_req.to_dict()
        record = {**obs, "outcome": outcome["outcome"], "consumed": False}
        case["observations"].append(record)
        if outcome["outcome"] != "RELEASED":
            self.save_case(case)
            return {"outcome": outcome["outcome"], "detail": outcome, "case": case}
        # Consumed exactly once: advance the logical revision, then run the
        # waiting slot. The COOL revision advances on every save; the case
        # revision advances only here. The released reading joins the
        # observed facts, so the Higher route can eliminate candidates.
        record["consumed"] = True
        case["revision"] += 1
        case["resumptions"] += 1
        case["inputs"]["observed"] = {
            **case["inputs"].get("observed", {}),
            obs["feature"]: obs["value"],
        }
        if case["resumptions"] > case["max_resumptions"]:
            case["status"] = "EXPIRED"
            case["resume_point"] = f"expired:{record_req.request_id}"
            self.save_case(case)
            return {"outcome": "EXPIRED", "detail": outcome, "case": case}
        waiting = [w for w in case["waiting"]
                   if w["requirement_id"] == record_req.request_id]
        case["waiting"] = [w for w in case["waiting"]
                           if w["requirement_id"] != record_req.request_id]
        before = (json.loads(json.dumps(case["decision"], sort_keys=True, default=str))
                  if case.get("decision") is not None else None)
        before_map = self._current_map(case)
        request_join = next(
            (item for item in case["joins"]
             if item["direction"] == "HIGHER_TO_LOWER"
             and item["producer"]["output_ref"] == record_req.request_id),
            None,
        )
        lower = self._lower_route(
            case, self._observations_for(case, obs),
            work_ref=record_req.request_id if request_join else None,
        )
        if lower is None:
            case["status"] = "UNRESOLVED"
            case["resume_point"] = f"unresolved:{record_req.request_id}"
            self.save_case(case)
            return {"outcome": "RELEASED_BUT_LOWER_FAILED", "detail": outcome, "case": case}
        if request_join:
            request_join["status"] = "CONSUMED"
            request_join["consumed_output_ref"] = (
                f"{case_id}:lower:L03:r{case['revision']}"
            )
        self._set_decision(case, lower["decision"], disposition="RESUMED")
        # The Lower result returns to Higher for review (joined Higher first).
        join = self._make_join(
            case, producer_slot="L31", producer_output_ref=lower["output_ref"],
            consumer_slot="U16", input_port="decision",
            direction="LOWER_TO_HIGHER",
            activation_condition="producer_output_present",
            required_inputs=[{"slot_id": "L31", "output_ref": lower["output_ref"]}])
        higher = self._higher_route(
            case, hypotheses=case["inputs"].get("hypotheses", {}),
            observed=case["inputs"].get("observed", {}),
            relevant_rows=lower["accepted"], lower_decision=lower["decision"],
            lower_output_ref=lower["output_ref"])
        if higher["synthesized"] is not None and any(
            item["slot_id"] == "U16" and item["status"] == "OK"
            and item["input_ref"] == lower["output_ref"]
            and item["output_ref"] == higher["output_ref"]
            and item["case_revision"] == case["revision"]
            for item in case["invocations"]
        ):
            join["status"] = "CONSUMED"
            join["consumed_output_ref"] = higher["output_ref"]
            for output in case["outputs"]:
                if output["output_ref"] == lower["output_ref"]:
                    output["consumed_by"].append("U16")
            self._set_decision(case, higher["synthesized"], disposition="CONSUMED")
        else:
            join["status"] = "FAILED"
        # The final disposition first, so the reconciliation records it.
        remaining = [r for r in case["requirements"] if r["disposition"] == "PENDING"]
        if join["status"] != "CONSUMED":
            case["status"] = "UNRESOLVED"
            case["resume_point"] = f"join-failed:{join['join_id']}"
        elif case["status"] == "HOLD" and not remaining:
            if case["mode"] == "joined_higher_first" and request_join is None:
                case["status"] = "UNRESOLVED"
                case["resume_point"] = "unresolved:missing-handoff"
            else:
                case["status"] = "COMPLETED"
                case["resume_point"] = "complete"
        case["reconciliation"] = self._reconcile(case, before=before, before_map=before_map,
                                                 observations=[obs])
        self._check_progress(case, before)
        self.save_case(case)
        return {"outcome": "RELEASED", "detail": outcome, "case": case,
                "join": join, "lower": lower, "higher": higher}

    def _observations_for(self, case: dict[str, Any],
                          new_obs: dict[str, Any]) -> list[dict[str, Any]]:
        """Observation rows for the Lower route: the case's evidence snapshot
        as rows, plus the newly released reading as a row.

        The rows use the exclusive ``state`` predicate and one shared event,
        the same convention as the reference conflict fixture (L03/L17/L18/
        L31 -> U16): L17 compares readings of the same state, so two
        readings of one feature at the same event with different values are
        a genuine conflict. The reading's arrival time is recorded on the
        observation itself, not in the row event.
        """
        event = new_obs.get("event", "2026-01-01T00:00:00Z")
        rows: list[dict[str, Any]] = []
        snapshot = case["inputs"].get("evidence_snapshot", {})
        for source_id, entry in snapshot.items():
            if isinstance(entry, dict) and "key" in entry:
                rows.append({
                    "source": source_id, "subject": entry["key"],
                    "predicate": "state", "value": entry["value"],
                    "scope": "default",
                    "event": event,
                })
        new_row = {
            "source": new_obs.get("source_id"), "subject": new_obs.get("feature"),
            "predicate": "state", "value": new_obs.get("value"),
            "scope": "default",
            "event": event,
        }
        # A reading for a source already in the snapshot replaces that row
        # (the new value supersedes the old one); otherwise it is appended.
        replaced = False
        for i, row in enumerate(rows):
            if row["source"] == new_row["source"]:
                rows[i] = new_row
                replaced = True
        if not replaced:
            rows.append(new_row)
        return rows

    def _current_map(self, case: dict[str, Any]) -> dict[str, Any]:
        recorder = self._recorders[case["case_id"]]
        return recorder.build_map().to_dict()

    def _reconcile(self, case: dict[str, Any], *, before: dict[str, Any] | None,
                   before_map: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
        after_map = self._current_map(case)
        before_cog = CognitiveMap(
            case_id=case["case_id"], goal=case["goal"],
            evidence=before_map.get("evidence", []),
            conflicts=before_map.get("conflicts", []),
            unknowns=before_map.get("unknowns", []),
            alternatives=before_map.get("alternatives", []),
            selected_branch=before_map.get("selected_branch", ""),
            disposition=before_map.get("disposition", ""),
            resume_point=before_map.get("resume_point", ""))
        after_cog = CognitiveMap(
            case_id=case["case_id"], goal=case["goal"],
            evidence=after_map.get("evidence", []),
            conflicts=after_map.get("conflicts", []),
            unknowns=after_map.get("unknowns", []),
            alternatives=after_map.get("alternatives", []),
            selected_branch=after_map.get("selected_branch", ""),
            disposition=after_map.get("disposition", ""),
            resume_point=after_map.get("resume_point", ""))
        rec = InvestigationReconciliation.reconcile(before_cog, after_cog, observations)
        after = case.get("decision")
        changed_fields = []
        if (before or {}).get("status") != (after or {}).get("status"):
            changed_fields.append("decision.status")
        if (before or {}).get("answer") != (after or {}).get("answer"):
            changed_fields.append("decision.answer")
        # The replayed map is cumulative: a gap recorded at opening stays in
        # the after map's unknowns. An unknown is resolved here when the
        # resumed observation supplies its feature (e.g. "missing:tox_screen"
        # is resolved by a tox_screen reading).
        resolved_by_obs = {f"missing:{obs['feature']}" for obs in observations
                           if isinstance(obs, dict) and "feature" in obs}
        before_unknowns = {u.get("alternative") for u in before_map.get("unknowns", [])}
        after_unknowns = {u.get("alternative") for u in after_map.get("unknowns", [])}
        resolved = sorted((before_unknowns - after_unknowns) | (before_unknowns & resolved_by_obs))
        remaining = sorted(after_unknowns - resolved_by_obs)
        return {
            **rec,
            "resolved_unknowns": resolved,
            "unresolved_remaining": remaining,
            "decision_before": before,
            "decision_after": after,
            "decision_changed": bool(changed_fields),
            "changed_fields": changed_fields,
            "remaining_uncertainty": remaining,
            "disposition": case["status"],
            "resume_point": case["resume_point"],
        }

    def _check_progress(self, case: dict[str, Any], before: dict[str, Any] | None) -> None:
        """Repeated route without progress: a resume that leaves the decision
        unchanged (same status and answer, nothing new consumed) counts once;
        two in a row close the case UNRESOLVED."""
        after = case.get("decision")
        same = (before or {}).get("status") == (after or {}).get("status") and \
            (before or {}).get("answer") == (after or {}).get("answer")
        if same:
            case["no_progress_streak"] += 1
        else:
            case["no_progress_streak"] = 0
        if case["no_progress_streak"] >= MAX_NO_PROGRESS:
            case["status"] = "UNRESOLVED"
            case["resume_point"] = "no-progress"
            case["reconciliation"] = {
                **(case.get("reconciliation") or {}),
                "outcome": "NO_PROGRESS",
            }

    # ------------------------------------------------------------------
    # Multi-slot review, gap review, closing
    # ------------------------------------------------------------------
    def multi_slot_review(self, case: dict[str, Any],
                          outputs: list[dict[str, Any]], *,
                          specialist: bool = False,
                          origin: str | None = None, target: str | None = None,
                          snapshot: dict[str, Any] | None = None,
                          specialist_output: dict[str, Any] | None = None) -> dict[str, Any]:
        """Combine several outputs with their origins and preserve
        disagreements. For specialist routes the existing L34/U33 coherence
        operations are used (via CoherenceReview); the general multi-input
        review is separate.
        """
        by_feature: dict[str, list[dict[str, Any]]] = {}
        for out in outputs:
            by_feature.setdefault(out.get("feature", ""), []).append(out)
        disagreements = []
        for feature, group in by_feature.items():
            values = {json.dumps(g.get("value"), sort_keys=True, default=str) for g in group}
            if len(values) > 1:
                disagreements.append({
                    "feature": feature,
                    "values": [{"slot_id": g["slot_id"], "value": g.get("value"),
                                "output_ref": g.get("output_ref")} for g in group],
                })
        review: dict[str, Any] = {
            "case_id": case["case_id"],
            "outputs": [{"slot_id": o["slot_id"], "feature": o.get("feature"),
                         "value": o.get("value"), "output_ref": o.get("output_ref")}
                        for o in outputs],
            "disagreements": disagreements,
            "disagreement_count": len(disagreements),
        }
        if specialist:
            if origin is None or target is None or snapshot is None or specialist_output is None:
                raise ValueError("specialist review needs origin, target, snapshot and specialist_output")
            spec_review = CoherenceReview.review_specialist_output(
                origin, target, "U33", specialist_output, snapshot)
            review["specialist_review"] = spec_review.to_dict()
        case["reviews"].append(review)
        self.save_case(case)
        return review

    def review_gaps(self, case: dict[str, Any], *, templates: dict[str, Any] | None = None,
                    hypotheses: dict[str, Any] | None = None,
                    observed: dict[str, Any] | None = None,
                    facts: list[str] | None = None) -> dict[str, Any]:
        """Gap review / path suggestions: U11 for gaps, U12 for a
        discriminator. Suggestions are advisory only (RouteAdvisor): they
        never select a route.
        """
        cid = case["case_id"]
        rev = case["revision"]
        live = hypotheses or {}
        if templates is not None:
            u07 = self._execute(
                case, "U07", templates, facts or [],
                input_ref=f"{cid}:higher:templates:in",
                output_ref=f"{cid}:higher:U07:r{rev}")
            if u07["status"] == "OK":
                live = {k: {"prediction": v} for k, v in u07["result"].items()}
        gaps_out = self._execute(
            case, "U11", hypotheses or live, observed or {},
            input_ref=f"{cid}:higher:hypotheses:in",
            output_ref=f"{cid}:higher:U11:r{rev}")
        partitions = gaps_out["result"] if gaps_out["status"] == "OK" else {}
        self._annotate_last_event(case, rejected_alternatives=[
            {"alternative": f"missing:{feature}", "reason": "unobserved discriminator"}
            for feature in partitions])
        disc_out = self._execute(
            case, "U12", partitions, _preferred_profile(partitions),
            input_ref=f"{cid}:higher:U11:r{rev}",
            output_ref=f"{cid}:higher:U12:r{rev}")
        discriminator = disc_out["result"] if disc_out["status"] == "OK" else {"feature": None, "used_method": False}
        advisor = RouteAdvisor(registry_version=SLOT_MAP_VERSION)
        suggestions = advisor.suggest_routes(cid, [{
            "slot_id": "L03", "eligible": discriminator.get("feature") is not None,
            "reason": f"discriminator {discriminator.get('feature')} unobserved",
            "expected_effect": "Lower returns the missing reading for Higher review",
        }]) if discriminator.get("feature") else []
        return {"live_hypotheses": live, "gaps": partitions,
                "discriminator": discriminator, "suggestions": suggestions}

    def close_case(self, case_id: str, *, disposition: str) -> dict[str, Any]:
        """Closing review: record how resumed evidence changed the decision,
        the remaining uncertainty, the final disposition and the exact resume
        point if the case is still open.
        """
        case = self.get_case(case_id)
        recorder = self._recorders[case_id]
        cognitive_map = recorder.build_map()
        closing = ClosingReview.review(cognitive_map)
        outcome = OutcomeReview.review(case["opening"], closing,
                                       case.get("reconciliation") or {})
        case["status"] = disposition
        if disposition == "COMPLETED":
            case["resume_point"] = "complete"
        self.save_case(case)
        return {"case": case, "closing": closing, "outcome": outcome}

    # ------------------------------------------------------------------
    # Mode dispatch
    # ------------------------------------------------------------------
    def execute_mode(self, case_id: str, *, observations: list[dict[str, Any]] | None = None,
                     hypotheses: dict[str, Any] | None = None,
                     observed: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the case's mode. Returns the updated case."""
        case = self.get_case(case_id)
        mode = case["mode"]
        if mode == "lower_only":
            return self._mode_lower_only(case, observations or [])
        if mode == "higher_only":
            return self._mode_higher_only(case, hypotheses or {}, observed or {})
        if mode == "joined_lower_first":
            return self._mode_joined_lower_first(case, observations or [],
                                                 hypotheses or {}, observed or {})
        if mode == "joined_higher_first":
            return self._mode_joined_higher_first(case, hypotheses or {}, observed or {})
        raise ValueError(f"unknown mode: {mode!r}")

    def _mode_lower_only(self, case: dict[str, Any],
                         observations: list[dict[str, Any]]) -> dict[str, Any]:
        lower = self._lower_route(case, observations)
        if lower is None:
            req = self.request_information(
                case, consumer_slot="L03", required_feature="parseable_observation",
                source_ids=[], release_condition="at least one parseable observation",
                hold=False)
            case["status"] = "UNRESOLVED"
            case["resume_point"] = f"unresolved:{req['request_id']}"
            self.save_case(case)
            return case
        self._set_decision(case, lower["decision"], disposition="COMPLETED")
        case["inputs"]["accepted_rows"] = lower["accepted"]
        if lower["decision"]["status"] == "ABSTAIN":
            # Lower cannot resolve it alone: record what is still needed as
            # an explicit outcome (independent mode: UNRESOLVED, not HOLD).
            req = self.request_information(
                case, consumer_slot="L03", required_feature="additional_observation",
                source_ids=[],
                release_condition="an observation that resolves the abstention",
                hold=False)
            case["status"] = "UNRESOLVED"
            case["resume_point"] = f"unresolved:{req['request_id']}"
            self.save_case(case)
            return case
        case["status"] = "COMPLETED"
        case["resume_point"] = "complete"
        self.save_case(case)
        return case

    def _mode_higher_only(self, case: dict[str, Any], hypotheses: dict[str, Any],
                          observed: dict[str, Any]) -> dict[str, Any]:
        relevant = case["inputs"].get("accepted_rows", [])
        higher = self._higher_route(case, hypotheses=hypotheses, observed=observed,
                                    relevant_rows=relevant)
        self._set_decision(case, higher["synthesized"] or higher["decision"],
                           disposition="COMPLETED")
        if higher["decision"]["status"] == "ABSTAIN":
            # Higher cannot decide on what it has: record the missing
            # discriminator (or the absence of a consistent candidate) as an
            # explicit outcome.
            missing = sorted(higher["gaps"].keys())
            feature = missing[0] if missing else "consistent_candidate"
            req = self.request_information(
                case, consumer_slot="U13", required_feature=feature,
                source_ids=[], release_condition=f"evidence for {feature}",
                hold=False)
            case["status"] = "UNRESOLVED"
            case["resume_point"] = f"unresolved:{req['request_id']}"
            self.save_case(case)
            return case
        case["status"] = "COMPLETED"
        case["resume_point"] = "complete"
        self.save_case(case)
        return case

    def _mode_joined_lower_first(self, case: dict[str, Any],
                                 observations: list[dict[str, Any]],
                                 hypotheses: dict[str, Any],
                                 observed: dict[str, Any]) -> dict[str, Any]:
        lower = self._lower_route(case, observations)
        if lower is None:
            req = self.request_information(
                case, consumer_slot="L03", required_feature="parseable_observation",
                source_ids=[], release_condition="at least one parseable observation")
            # Joined mode waits for the evidence (HOLD), unlike the
            # independent mode which records UNRESOLVED.
            case["status"] = "HOLD"
            case["resume_point"] = f"requirement:{req['request_id']}"
            self.save_case(case)
            return case
        self._set_decision(case, lower["decision"], disposition="LOWER_PRODUCED")
        case["inputs"]["accepted_rows"] = lower["accepted"]
        case["inputs"]["hypotheses"] = hypotheses
        case["inputs"]["observed"] = observed
        join = self.join_lower_to_higher(case, lower_output_ref=lower["output_ref"])
        if join["status"] == "CONSUMED":
            case["status"] = "COMPLETED"
            case["resume_point"] = "complete"
        self.save_case(case)
        return case

    def _mode_joined_higher_first(self, case: dict[str, Any],
                                  hypotheses: dict[str, Any],
                                  observed: dict[str, Any]) -> dict[str, Any]:
        case["inputs"]["hypotheses"] = hypotheses
        case["inputs"]["observed"] = observed
        review = self.review_gaps(case, hypotheses=hypotheses, observed=observed)
        feature = review["discriminator"].get("feature")
        if not feature:
            # A joined case still needs a concrete Lower work item.
            # Record the unmet crossing when Higher cannot formulate one.
            return self._record_outcome(
                case, status="UNRESOLVED", outcome="missing-lower-work-item")
        req = self.request_information(
            case, consumer_slot="L03", required_feature=feature,
            source_ids=list(case["inputs"].get("acceptable_sources", [feature])),
            release_condition=f"conclusive reading for {feature}")
        self.join_higher_to_lower(case, requirement=req, consumer_slot="L03")
        return case


def _has_conflicts(pairs: list[dict[str, Any]]) -> bool:
    from contract.functions025 import conflicts
    return bool(conflicts(pairs))


def _preferred_profile(partitions: dict[str, Any]) -> list[Any]:
    """The U12 preference: the profile of the most disputed gap feature
    (the feature whose candidates disagree most), so the discriminator is
    the most informative missing feature, not merely the first name."""
    if not partitions:
        return []
    best = max(sorted(partitions), key=lambda f: len(set(partitions[f])))
    return list(partitions[best])


def copy_state(case: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a case state for persistence (JSON-safe).

    The ``invocations`` list is not persisted: it is derivable from the
    transition events (tier from the slot prefix, status from the event
    disposition, handler from the slot map) and is rebuilt on load. This
    keeps the persisted document from duplicating the route record.
    """
    state = json.loads(json.dumps(case, sort_keys=True, default=str))
    state.pop("invocations", None)
    return state


def _rebuild_invocations(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Rebuild the invocation log from the persisted transition events."""
    rebuilt = []
    for ev in case.get("events", []):
        slot_id = ev["slot_id"]
        disposition = ev["disposition"]
        status = "OK" if disposition in ("OK", "CONFLICT_PRESERVED") else disposition
        try:
            handler = describe(slot_id).callable_identity
        except KeyError:
            handler = None
        rebuilt.append({
            "slot_id": slot_id, "tier": _tier(slot_id), "status": status,
            "code": None, "handler": handler,
            "input_ref": ev["input_ref"], "output_ref": ev["output_ref"],
            "case_revision": ev["case_revision"],
        })
    return rebuilt


def _summarize(result: Any) -> Any:
    """Short, JSON-safe summary of a slot result for the case outputs list."""
    if isinstance(result, dict):
        keep = {k: result[k] for k in ("status", "answer", "feature", "support",
                                       "presented_sources", "remaining", "causation")
                if k in result}
        return keep if keep else {"keys": sorted(result.keys())[:8]}
    if isinstance(result, list):
        return {"count": len(result)}
    return result



def _summarize(result: Any) -> Any:
    """Short, JSON-safe summary of a slot result for the case outputs list."""
    if isinstance(result, dict):
        keep = {k: result[k] for k in ("status", "answer", "feature", "support",
                                       "presented_sources", "remaining", "causation")
                if k in result}
        return keep if keep else {"keys": sorted(result.keys())[:8]}
    if isinstance(result, list):
        return {"count": len(result)}
    return result
