"""Integration Contract 002 — persistent multi-step task execution.

A multi-step task is a plan of ordered steps. Each step is a full
Contract 001 pipeline task (its own task id, signature, tags,
conditions, criteria). The task's progress lives in RealCog's
Small49 journal (via the generations adapter): begin (plan +
identity digest + policy version), one record per completed step
(procedure, outcome, validation, policy version, IOU), done.

Run-or-resume (RealCog's run_task semantics): the runner always
resumes from the journal state. Completed steps are never
re-executed. Each pending step is classified before it runs:
  COMPLETED      — a journal step record exists. Skipped.
  RECONSTRUCTABLE — no journal record, but the pipeline trace has
                   the step's TASK_DONE (the run finished, the
                   journal record did not land). The outcome is
                   rebuilt from the trace and the journal record
                   is written; the step is NOT re-executed.
  AMBIGUOUS      — the trace has an ACTION_INTENT for the step but
                   no TASK_DONE. The action may or may not have
                   executed. The step is re-run (at-least-once:
                   the action may execute twice — documented, not
                   hidden; exactly-once external effects are NOT
                   claimed).
  UNCOMMITTED    — no journal record and no trace intent. Run
                   normally.

An unresolved step is PENDING (its IOU is recorded in the step
record); the runner continues to the next step by default
(steps are independent work items in a repair job) unless
stop_on_unresolved is set, in which case it stops at the first
pending step. Either way, pending steps are resumed later — they
are never silently dropped.
"""
from __future__ import annotations

from .task import make_task
from .trace import Tracer, certify, child_executions


class MultiStepRunner:
    def __init__(self, pipe, generations, stop_on_unresolved=False):
        self.pipe = pipe
        self.generations = generations
        self.stop_on_unresolved = stop_on_unresolved
        self._tracer = None

    def _journal_write(self, task_id, block, bucket, detail=None):
        """TRACE-001: record a Small49 journal write on the parent
        execution's root span (the journal is the multi-step
        task's durable state; each step's pipe.run() is a separate
        execution referenced by the parent)."""
        if self._tracer is None:
            return
        self._tracer.memory("root", "write", "small49", block,
                            bucket=bucket, detail=detail)

    # ---- intake ----
    def begin(self, task_spec):
        task = make_task(**{k: v for k, v in task_spec.items()
                            if k in ("task_id", "signature", "family", "tags",
                                     "conditions", "unknown_conditions",
                                     "contradictory_conditions",
                                     "requested_operation", "observed",
                                     "provenance")})
        plan = [s["step_id"] for s in task_spec["steps"]]
        journal, _gen = self.generations.journal_for(
            task.task_id, len(plan), task.identity_digest())
        st = journal.state(task.task_id)
        if st["status"] == "NONE":
            journal.begin(task.task_id, plan,
                          policy_version=self.pipe.policy.version)
        return journal.state(task.task_id)

    # ---- classification ----
    def _classify_step(self, journal, task_id, step_task_id):
        st = journal.state(task_id)
        done = {r["s"] for r in st["outcomes"]}
        step_id = step_task_id.rsplit(".", 1)[-1]
        if step_id in done:
            return "COMPLETED"
        kinds = [k for _, k, t, _, _ in self.pipe.trace.events(step_task_id)]
        if "TASK_DONE" in kinds:
            return "RECONSTRUCTABLE"
        if "ACTION_INTENT" in kinds:
            return "AMBIGUOUS"
        return "UNCOMMITTED"

    def _reconstruct(self, journal, task_id, step, n):
        """Rebuild a step's journal record from the pipeline trace
        (the run finished; only the journal record is missing)."""
        step_task_id = step["task_id"]
        done_ev = None
        iou = None
        for _, kind, _, _, data in self.pipe.trace.events(step_task_id):
            if kind == "TASK_DONE":
                done_ev = data or {}
            if kind == "IOU_OPENED":
                iou = (data or {}).get("iou")
        result = (done_ev or {}).get("result", "UNSOLVED")
        procedure = (done_ev or {}).get("procedure") or ""
        outcome = "SUCCESS" if result == "RESOLVED" else "FAILURE"
        journal.record_step(task_id, n, step["step_id"], procedure, outcome,
                            "PASS" if result == "RESOLVED" else "INCONCLUSIVE",
                            self.pipe.policy.version, iou=iou)
        self._journal_write(task_id, f"t.{task_id}.s{n}", "workspace_large",
                            detail={"kind": "reconstruct", "step": step["step_id"]})
        return {"step_id": step["step_id"], "classification": "RECONSTRUCTABLE",
                "outcome": outcome, "procedure": procedure,
                "validation": "PASS" if result == "RESOLVED" else "INCONCLUSIVE",
                "iou": iou, "policy_version": self.pipe.policy.version}

    # ---- run / resume ----
    def run(self, task_spec, crash_hook=None):
        task = make_task(**{k: v for k, v in task_spec.items()
                            if k in ("task_id", "signature", "family", "tags",
                                     "conditions", "unknown_conditions",
                                     "contradictory_conditions",
                                     "requested_operation", "observed",
                                     "provenance")})
        steps = {s["step_id"]: s for s in task_spec["steps"]}
        # TRACE-001: the parent multi-step execution. Its trace
        # records the Small49 journal writes (begin / step /
        # done); each step's pipe.run() is a separate execution
        # referenced from the parent by its task id.
        # TRACE-002, Correction E: each step's execution carries
        # the parent's execution_id (parent_execution_id), so the
        # parent/child linkage is verifiable from the ledger —
        # not from parsing task names. Correction C: the parent
        # execution is certified at the end of the run.
        tracer = Tracer(self.pipe.trace, task.task_id,
                        domain=self.pipe.profile["domain"],
                        profile=self.pipe._profile_id,
                        mode=self.pipe.recording_mode)
        tracer.begin_task(context={
            "family": task.family, "signature": task.signature,
            "steps": [s["step_id"] for s in task_spec["steps"]],
            "step_executions": [s["task_id"] for s in task_spec["steps"]]})
        self._tracer = tracer
        try:
            return self._run_inner(task_spec, task, steps, crash_hook, tracer)
        finally:
            self._tracer = None

    def _run_inner(self, task_spec, task, steps, crash_hook, tracer):
        journal, gen = self.generations.journal_for(
            task.task_id, len(task_spec["steps"]), task.identity_digest())
        st = journal.state(task.task_id)
        if st["status"] == "NONE":
            journal.begin(task.task_id, [s["step_id"] for s in task_spec["steps"]],
                          policy_version=self.pipe.policy.version)
            self._journal_write(task.task_id, f"t.{task.task_id}.begin",
                                "active", detail={"kind": "begin"})
            st = journal.state(task.task_id)
        if st["status"] == "DONE":
            # TRACE-002: a resumed DONE task still ends its own
            # parent execution (otherwise the resume's TASK_BEGIN
            # has no TASK_END and cannot certify) and returns the
            # stored step outcomes, so a caller can see what the
            # task did without re-running any step.
            report_steps = [
                {"step_id": r["s"], "classification": "COMPLETED",
                 "outcome": r["o"], "procedure": r["a"],
                 "validation": r.get("v"), "iou": None,
                 "policy_version": self.pipe.policy.version}
                for r in st["outcomes"]]
            tracer.end_task("DONE", stage="multi-step",
                            detail={"result": st["result"],
                                    "generation": gen,
                                    "resumed": True})
            children = child_executions(self.pipe.trace,
                                        tracer.execution_id)
            certification = certify(self.pipe.trace, task.task_id,
                                    tracer.execution_id)
            return {"task_id": task.task_id, "status": "DONE",
                    "generation": gen,
                    "steps": report_steps, "result": st["result"],
                    "identity_digest": task.identity_digest(),
                    "execution_id": tracer.execution_id,
                    "child_executions": children,
                    "trace_certified": certification["certified"],
                    "trace_certification_gaps": certification["gaps"]}
        plan = st["plan"]  # the persisted plan wins on resume
        report_steps = []
        n = st["done_steps"]
        for idx, step_id in enumerate(plan):
            step = steps[step_id]
            classification = self._classify_step(
                journal, task.task_id, step["task_id"])
            if crash_hook is not None:
                crash_hook("before_step", idx, classification)
            if classification == "COMPLETED":
                rec = [r for r in st["outcomes"] if r["s"] == step_id][0]
                report_steps.append(
                    {"step_id": step_id, "classification": "COMPLETED",
                     "outcome": rec["o"], "procedure": rec["a"],
                     "validation": rec.get("v"),
                     "iou": rec.get("i"),
                     "policy_version": rec.get("p")})
                continue
            if classification == "RECONSTRUCTABLE":
                report_steps.append(
                    self._reconstruct(journal, task.task_id, step, n))
                n += 1
                continue
            # AMBIGUOUS or UNCOMMITTED: run the step through the
            # Contract 001 pipeline (at-least-once for AMBIGUOUS).
            # TRACE-002, Correction E: the step's execution
            # carries the parent's execution_id.
            rep = self.pipe.run({k: v for k, v in step.items()
                                 if k != "correct"},
                                parent_execution_id=tracer.execution_id)
            if crash_hook is not None:
                crash_hook("after_step_run", idx, classification)
            outcome = "SUCCESS" if rep["state"] == "RESOLVED" else "FAILURE"
            procedure = rep["selected"] or ""
            validation = "PASS" if rep["state"] == "RESOLVED" else "INCONCLUSIVE"
            if rep["attempts"]:
                validation = rep["attempts"][-1]["validation"]
            journal.record_step(task.task_id, n, step_id, procedure, outcome,
                                validation, self.pipe.policy.version,
                                iou=rep["iou"])
            self._journal_write(task.task_id, f"t.{task.task_id}.s{n}",
                                "workspace_large",
                                detail={"kind": "step", "step": step_id,
                                        "procedure": procedure,
                                        "outcome": outcome})
            report_steps.append(
                {"step_id": step_id, "classification": classification,
                 "outcome": outcome, "procedure": procedure,
                 "validation": validation, "iou": rep["iou"],
                 "policy_version": self.pipe.policy.version})
            n += 1
            if crash_hook is not None:
                crash_hook("after_step_record", idx, classification)
            if rep["state"] != "RESOLVED" and self.stop_on_unresolved:
                break
        st = journal.state(task.task_id)
        if st["done_steps"] == len(plan) and st["status"] != "DONE":
            pending = [r for r in report_steps if r["outcome"] != "SUCCESS"]
            result = "OK" if not pending else "PARTIAL"
            journal.done(task.task_id, len(plan), result,
                         policy_version=self.pipe.policy.version)
            self._journal_write(task.task_id, f"t.{task.task_id}.done",
                                "active",
                                detail={"kind": "done", "result": result})
            st = journal.state(task.task_id)
        tracer.end_task(st["status"], stage="multi-step",
                        detail={"result": st["result"],
                                "generation": gen})
        # TRACE-002, Correction E + C: the parent's child
        # executions (read from the ledger, not from task names)
        # and the parent's certification.
        children = child_executions(self.pipe.trace, tracer.execution_id)
        certification = certify(self.pipe.trace, task.task_id,
                                tracer.execution_id)
        return {"task_id": task.task_id, "status": st["status"],
                "generation": gen,
                "steps": report_steps, "result": st["result"],
                "identity_digest": task.identity_digest(),
                "execution_id": tracer.execution_id,
                "child_executions": children,
                "trace_certified": certification["certified"],
                "trace_certification_gaps": certification["gaps"]}
