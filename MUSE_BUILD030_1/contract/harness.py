"""Integration Contract 001 — non-destructive end-to-end harness.

The harness runs the contract pipeline against temporary roots.
It never writes to either baseline: SoSo's app directory and
RealCog's staging directory are read-only inputs (imported, not
modified), and all durable state lives under the harness root
(Big49, IOU ledger, trace). The frozen observations and the
evaluator-owned criteria are supplied by the caller (the tests),
so the same draws can be replayed exactly.

Training the baseline THROUGH the pipeline (not by backdoor
writes): each training problem is solved once by trial, then
qualified by a transfer task — exercising the real lifecycle
(trial -> CANDIDATE -> UNDER_TEST -> QUALIFIED) before any
acceptance test runs.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
_REALCOG_DIR = _PKG_ROOT / "realcog"
for _p in (str(_PKG_ROOT), str(_REALCOG_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters import durable_memory as _dm
from adapters import cognitive_layer as _cl

SOSO_MODULES = {"durable_memory": _dm, "cognitive_layer": _cl}

from contract.admission import Policy
from contract.generations import GenerationsAdapter
from contract.multistep import MultiStepRunner
from contract.pipeline import Pipeline

# The shared problem set (the Phase 3 comparison's problems, in
# contract form). Each entry: task fields + correct native
# procedure (the evaluator-owned criteria, never read by
# selection) + frozen observation (only the correct procedure
# succeeds — noise-free, because the acceptance tests measure the
# pipeline, not noise robustness).
#
# Three variants per procedure: a procedure needs at least two
# demonstrations before the transfer layer can form an
# applicability set for it (min_support 2 — the same guard SoSo's
# own design uses against single-noise-success generalization),
# so the baseline is taught with three overlapping variants per
# family rather than one.
def _train_task(tid, sig, fam, tags, cond, correct):
    return {"task_id": tid, "signature": sig, "family": fam,
            "tags": tags, "conditions": cond,
            "requested_operation": "repair",
            "provenance": ("int:contract-training",), "correct": correct}


TRAINING = [
    _train_task("ct-train-valve-a", "disk_full", "valve",
                ["valve", "firmware_v2", "stuck", "pressure"],
                {"firmware": "v2"}, "P2"),
    _train_task("ct-train-valve-b", "disk_full", "valve",
                ["valve", "firmware_v2", "stuck", "jammed"],
                {"firmware": "v2"}, "P2"),
    _train_task("ct-train-valve-c", "disk_full", "valve",
                ["valve", "firmware_v2", "stuck", "flow"],
                {"firmware": "v2"}, "P2"),
    _train_task("ct-train-sensor-a", "svc_hung", "sensor",
                ["sensor", "temperature", "drift", "readings"],
                {"kind": "sensor"}, "P3"),
    _train_task("ct-train-sensor-b", "svc_hung", "sensor",
                ["sensor", "temperature", "drift", "probe"],
                {"kind": "sensor"}, "P3"),
    _train_task("ct-train-sensor-c", "svc_hung", "sensor",
                ["sensor", "temperature", "drift", "thermal"],
                {"kind": "sensor"}, "P3"),
    _train_task("ct-train-pump-a", "cfg_bad", "pump",
                ["pump", "intermittent", "vibration", "bearing"],
                {"kind": "pump"}, "P4"),
    _train_task("ct-train-pump-b", "cfg_bad", "pump",
                ["pump", "intermittent", "vibration", "stalling"],
                {"kind": "pump"}, "P4"),
    _train_task("ct-train-pump-c", "cfg_bad", "pump",
                ["pump", "intermittent", "vibration", "housing"],
                {"kind": "pump"}, "P4"),
    _train_task("ct-train-blocked-a", "cache_big", "blocked",
                ["route", "blocked", "debris", "rubble"],
                {"kind": "detour"}, "P7"),
    _train_task("ct-train-blocked-b", "cache_big", "blocked",
                ["route", "blocked", "debris", "obstacle"],
                {"kind": "detour"}, "P7"),
    _train_task("ct-train-blocked-c", "cache_big", "blocked",
                ["route", "blocked", "debris", "fallen"],
                {"kind": "detour"}, "P7"),
]

# Transfer tasks for qualification: a different task, same
# correct procedure, new wording. The procedure must be selected
# by the transfer stage (not by trial) for qualification to pass.
TRANSFER_TASKS = {
    "P2": {"task_id": "ct-xfer-valve", "signature": "disk_full",
           "family": "valve", "tags": ["valve", "firmware_v2", "jammed", "flow"],
           "conditions": {"firmware": "v2"}, "requested_operation": "repair",
           "provenance": ("int:contract-training",), "correct": "P2"},
    "P3": {"task_id": "ct-xfer-sensor", "signature": "svc_hung",
           "family": "sensor", "tags": ["sensor", "temperature", "probe", "wandering"],
           "conditions": {"kind": "sensor"}, "requested_operation": "repair",
           "provenance": ("int:contract-training",), "correct": "P3"},
    "P4": {"task_id": "ct-xfer-pump", "signature": "cfg_bad",
           "family": "pump", "tags": ["pump", "intermittent", "stalling", "housing"],
           "conditions": {"kind": "pump"}, "requested_operation": "repair",
           "provenance": ("int:contract-training",), "correct": "P4"},
    "P7": {"task_id": "ct-xfer-blocked", "signature": "cache_big",
           "family": "blocked", "tags": ["route", "blocked", "obstacle", "fallen"],
           "conditions": {"kind": "detour"}, "requested_operation": "repair",
           "provenance": ("int:contract-training",), "correct": "P7"},
}


class Harness:
    """A pipeline instance over a fresh temporary root, with the
    frozen observations and criteria for a given task set."""

    def __init__(self, root=None, policy=None, tasks=(),
                 generations_threshold=0.8, pre_execute_hook=None,
                 post_intent_hook=None, descriptions="default",
                 profile="general", disabled_functions=frozenset(),
                 fault_hook=None, recording_mode="best_effort",
                 trial_ranker="existing", graph_scores=None,
                 evidence_source=None, pathway_provider=None,
                 measurement_selector=None, measurement_costs=None,
                 replay_contradiction_abstention=False):
        self.root = root or tempfile.mkdtemp(prefix="contract001-")
        self._own_root = root is None
        self.criteria = {}
        self.observations = {}
        for t in tasks:
            self.criteria[t["task_id"]] = t["correct"]
            # Frozen observations: the correct procedure succeeds
            # on this task; every other procedure fails. The
            # observation table is consulted only by the executor.
            for native in ("P1", "P2", "P3", "P4", "P5", "P6", "P7",
                           "P8", "P9", "P10", "P11", "clear_cache",
                           "restart_svc", "rotate_logs", "wipe_disk",
                           "reset_cfg"):
                self.observations[(t["task_id"], native)] = (native == t["correct"])
        self.policy = policy or Policy("contract-001-v1",
                                       forbidden=("RC-FIX-WIPE-DISK",))
        # TRACE-002: the ablation set, the fault hook and the
        # recording mode are passed through to the pipeline.
        self.recording_mode = recording_mode
        self.pipe = Pipeline(self.root, self.policy,
                             criteria=self.criteria,
                             observations=self.observations,
                             soso_modules=SOSO_MODULES,
                             pre_execute_hook=pre_execute_hook,
                             post_intent_hook=post_intent_hook,
                             descriptions=descriptions,
                             profile=profile,
                             disabled_functions=disabled_functions,
                             fault_hook=fault_hook,
                             recording_mode=recording_mode,
                             trial_ranker=trial_ranker,
                             graph_scores=graph_scores,
                             evidence_source=evidence_source,
                             pathway_provider=pathway_provider,
                             measurement_selector=measurement_selector,
                             measurement_costs=measurement_costs,
                             replay_contradiction_abstention=(
                                 replay_contradiction_abstention))
        # Contract 002 — generations + multi-step runner over the
        # same root (Small49 files under <root>/generations/).
        self.generations = GenerationsAdapter(
            os.path.join(self.root, "generations"),
            threshold=generations_threshold)
        self.runner = MultiStepRunner(self.pipe, self.generations)

    def add_task(self, t):
        self.criteria[t["task_id"]] = t["correct"]
        for native in ("P1", "P2", "P3", "P4", "P5", "P6", "P7",
                       "P8", "P9", "P10", "P11", "clear_cache",
                       "restart_svc", "rotate_logs", "wipe_disk",
                       "reset_cfg"):
            self.observations[(t["task_id"], native)] = (native == t["correct"])
        self.pipe.validator.set_criteria(t["task_id"], t["correct"])
        self.pipe.observations.update(
            {(t["task_id"], n): (n == t["correct"]) for n in
             ("P1", "P2", "P3", "P4", "P5", "P6", "P7",
              "P8", "P9", "P10", "P11", "clear_cache",
              "restart_svc", "rotate_logs", "wipe_disk", "reset_cfg")})

    def train_baseline(self):
        """Teach the four training problems through the pipeline:
        three trial runs per procedure (-> CANDIDATE each), then
        transfer qualification of the first variant (-> QUALIFIED).
        Returns the per-procedure reports."""
        for t in TRAINING:
            self.add_task(t)
        for x in TRANSFER_TASKS.values():
            self.add_task(x)
        reports = {}
        for proc in ("P2", "P3", "P4", "P7"):
            variants = [t for t in TRAINING if t["correct"] == proc]
            for t in variants:
                r = self.pipe.run({k: v for k, v in t.items() if k != "correct"})
                assert r["state"] == "RESOLVED", (t["task_id"], r)
                assert r["stage"] == "higher-trial", (t["task_id"], r)
            x = TRANSFER_TASKS[proc]
            q = self.pipe.qualify(variants[0]["task_id"], "SOSO-" + proc, x)
            assert q["state"] == "RESOLVED", (variants[0]["task_id"], q)
            assert q["stage"] == "soso-transfer", (variants[0]["task_id"], q)
            # Re-present the other variants: they now resolve by
            # exact qualified reuse, and their outcomes are
            # recorded QUALIFIED — so the transfer layer holds
            # three trusted successes per procedure and can form
            # an applicability set.
            for t in variants[1:]:
                r2 = self.pipe.run({k: v for k, v in t.items() if k != "correct"})
                assert r2["state"] == "RESOLVED", (t["task_id"], r2)
                assert r2["stage"] == "lower-exact", (t["task_id"], r2)
            reports[proc] = q
        return reports

    def close(self):
        self.pipe.close()
        self.generations.close()
        if self._own_root:
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


# ------------------------------------------------------------------
# Contract 002 — the end-to-end demonstration task.
#
# An unfamiliar multi-step repair job. Each step is a full
# Contract 001 pipeline task (task_id "<task>.<step_id>"). The
# steps are independent work items: an unresolved step is PENDING
# and the runner continues to the next one.
#   s1-valve    valve v2      -> P2, Lower exact (admitted, reversible)
#   s2-config   configuration -> reset_cfg, REQUIRES_AUTHORIZATION:
#                                unresolved + IOU, PENDING
#   s3-synonym  slope, new wording -> P7, trial (the crash step)
#   s4-valve-v1 valve v1      -> P1 (P2 contradicted), trial
#   s5-blocked  blocked       -> P7, Lower exact
# ------------------------------------------------------------------

def _demo_step(step_id, sig, fam, tags, cond, correct):
    return {"task_id": f"ct-demo-repair.{step_id}", "step_id": step_id,
            "signature": sig, "family": fam, "tags": tags,
            "conditions": cond, "requested_operation": "repair",
            "provenance": ("int:contract-demo",), "correct": correct}


DEMO_TASK = {
    "task_id": "ct-demo-repair", "signature": "demo_repair",
    "family": "repair",
    "tags": ["repair", "pump-line", "restore"],
    "conditions": {}, "requested_operation": "repair",
    "provenance": ("int:contract-demo",),
    "steps": [
        _demo_step("s1-valve", "disk_full", "valve",
                   ["valve", "firmware_v2", "stuck", "pressure"],
                   {"firmware": "v2"}, "P2"),
        _demo_step("s2-config", "net_down", "config",
                   ["configuration", "bad", "network"],
                   {}, "reset_cfg"),
        _demo_step("s3-synonym", "diskfull", "slope",
                   ["hillside", "terrain", "traverse", "ascend"],
                   {}, "P7"),
        _demo_step("s4-valve-v1", "disk_full", "valve",
                   ["valve", "firmware_v1", "stuck", "pressure"],
                   {"firmware": "v1"}, "P1"),
        _demo_step("s5-blocked", "cache_big", "blocked",
                   ["route", "blocked", "debris", "rubble"],
                   {"kind": "detour"}, "P7"),
    ],
}


def register_demo(harness):
    """Register the demo task's steps (criteria + frozen
    observations) in a harness. Returns DEMO_TASK."""
    for step in DEMO_TASK["steps"]:
        harness.add_task(step)
    return DEMO_TASK
