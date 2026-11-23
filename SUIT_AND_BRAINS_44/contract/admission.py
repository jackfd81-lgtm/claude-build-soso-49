"""Integration Contract 001 — Suit admission + independent validation.

Admission is the Suit's job and it happens BEFORE execution, on
every attempt, including retries after a resume. A higher score
never bypasses it. The three admission outcomes:
  admitted            — policy allows it and every hard
                        precondition is known and satisfied.
  unknown_preconditions — a hard precondition's value is unknown.
                        The pipeline must request the missing
                        information (or escalate), never execute.
  contradicted        — a hard precondition's known value
                        contradicts the requirement. The procedure
                        is refused, never executed.
  policy_denied       — the policy version forbids this procedure.
                        Never executed, never trialled.

The validator is INDEPENDENT: it owns its criteria table
(task_id -> correct native procedure), it never reads the
retrieval indexes, the experience store, or the trace, and it is
never consulted during selection. It answers only after an
attempt: was the observed outcome correct?
  observed SUCCESS on the criteria-correct procedure -> PASS.
  observed SUCCESS on a wrong procedure -> FAIL (a false success
    is caught, not learned).
  observed FAILURE on a wrong procedure -> PASS (a confirmed
    negative: the procedure is wrong and the observation agrees).
  observed FAILURE on the criteria-correct procedure -> INCONCLUSIVE.
  no criteria for the task -> INCONCLUSIVE.
"""
from __future__ import annotations

from .registry import check_hard_preconditions


class Policy:
    """A versioned policy. `forbidden` holds REGISTERED procedure
    ids (never native ids — the registry is the authority). A
    decision must be exact True to allow; anything else denies."""

    def __init__(self, version: str, forbidden=()):
        self.version = version
        self.forbidden = frozenset(forbidden)

    def allows(self, procedure_id: str) -> bool:
        return procedure_id not in self.forbidden

    def decision(self, procedure_id: str) -> bool:
        return self.allows(procedure_id) is True


def admit(entry, task, policy: Policy):
    """Admission check for one procedure on one task. Returns
    (admitted: bool, status: str, detail: str)."""
    if not policy.decision(entry.procedure_id):
        return False, "policy_denied", (
            f"policy {policy.version} forbids {entry.procedure_id}")
    status = check_hard_preconditions(
        entry, task.conditions, task.unknown_conditions,
        task.contradictory_conditions)
    if status == "contradicted":
        return False, "contradicted", (
            f"{entry.procedure_id} requires {entry.preconditions} "
            f"but the task has {task.conditions}")
    if status == "unknown":
        missing = [name for name, (_, hard) in entry.preconditions.items()
                   if hard and (name in task.unknown_conditions
                                or name not in task.conditions)]
        return False, "unknown_preconditions", (
            f"{entry.procedure_id} needs {missing}, which the task "
            f"does not state")
    return True, "admitted", ""


class Validator:
    """Independent validator. Criteria are evaluator-owned:
    {task_id: correct native procedure id}. The validator never
    reads anything else."""

    def __init__(self, criteria=None):
        self.criteria = dict(criteria or {})

    def set_criteria(self, task_id: str, correct_native: str):
        self.criteria[task_id] = correct_native

    def validate(self, task, procedure_native: str,
                 observed_success: bool) -> str:
        correct = self.criteria.get(task.task_id)
        if correct is None:
            return "INCONCLUSIVE"
        if observed_success and procedure_native == correct:
            return "PASS"
        if observed_success and procedure_native != correct:
            return "FAIL"
        if not observed_success and procedure_native != correct:
            return "PASS"
        return "INCONCLUSIVE"  # failed on the correct procedure

    def solution(self, task_id: str):
        return self.criteria.get(task_id)
