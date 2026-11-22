"""Integration Contract 002 — task generations over RealCog's Small49.

Wraps RealCog's SigGenerations + Journal (ph6_realcog03 /
ph6_realbridge, unmodified) with a contract journal that records a
multi-step task's full state: task identity, the plan, each step's
outcome (procedure, observation, validation, policy version, IOU),
and the result.

A task stays in one generation (RealCog's rule). Rollover happens
when the current generation fills (capacity / threshold): the
generation seals and the next task starts in a new generation.
Rollover preserves prior generations — their Small49 files remain,
and reconcile() rebuilds the task index from the begin records
after any interruption. The index is a rebuildable convenience,
never the authority: the Small49 files are.

The journal is extended by SUBCLASSING (the pattern RealCog03's
SigJournal already uses), never by editing RealCog's source. The
extra record keys (validation, policy version, IOU, step id) are
ignored by RealCog's own state() fold, so the original machinery
still reads these journals.
"""
from __future__ import annotations

import json
import fcntl
import os

from ph6_brains_2208 import Block, Rejected
from ph6_realbridge import Journal, pointer_for, rid
from ph6_realcog03_reserving import IMPL_ID as GENERATIONS_IMPL, ReservingSigGenerations

PRINCIPAL = "L30"


class ContractJournal(Journal):
    """A Journal whose begin record carries the contract task's
    identity digest (as its "sig") and policy version, and whose
    step records carry the full step outcome. Content is kept
    compact: the STEP bucket (workspace_large) allows 500 chars."""

    sig = None  # the task's identity digest

    def begin(self, task, plan, policy_version=None):
        body = {"k": "begin", "plan": plan, "sig": self.sig,
                "pv": policy_version}
        self._put(self.HEAD, rid("t", task, "begin"), body)
        self.crash("after_begin")

    def task_identity(self, task):
        rec = self.s.read(pointer_for(self.s, rid("t", task, "begin"),
                                      self.p), principal=self.p)
        return json.loads(rec["content"]).get("sig")

    def record_step(self, task, n, step_id, procedure, outcome,
                    validation, policy_version, iou=None):
        prev = rid("t", task, "begin") if n == 0 else rid("t", task, "s%d" % (n - 1))
        body = {"k": "out", "n": n, "s": step_id, "a": procedure,
                "o": outcome, "v": validation, "p": policy_version}
        if iou:
            body["i"] = iou
        self._put(self.STEP, rid("t", task, "s%d" % n), body, (prev,))
        self.crash("after_step")

    def done(self, task, n_steps, result, policy_version=None):
        prev = rid("t", task, "begin") if n_steps == 0 else rid("t", task, "s%d" % (n_steps - 1))
        body = {"k": "done", "r": result}
        if policy_version:
            body["pv"] = policy_version
        self._put(self.HEAD, rid("t", task, "done"), body, (prev,))

    def state(self, task):
        """Fold the chain (verifying every record via real read(),
        as Journal.state does), returning the contract's view:
        status, done_steps, plan, outcomes (full step records),
        identity digest, policy version, result."""
        ids = self._ids(task)
        if rid("t", task, "begin") not in ids:
            return {"status": "NONE", "done_steps": 0, "plan": [],
                    "outcomes": [], "identity_digest": None,
                    "policy_version": None, "result": None}
        out, plan, status, result = [], [], "RUNNING", None
        identity, policy_version = None, None
        for i in ids:
            rec = json.loads(self.s.read(pointer_for(self.s, i, self.p),
                                         principal=self.p)["content"])
            if rec["k"] == "begin":
                plan = rec["plan"]
                identity = rec.get("sig")
                policy_version = rec.get("pv")
            elif rec["k"] == "out":
                out.append(rec)
            elif rec["k"] == "done":
                status, result = "DONE", rec["r"]
                policy_version = rec.get("pv", policy_version)
        out.sort(key=lambda r: r["n"])
        if [r["n"] for r in out] != list(range(len(out))):
            raise Rejected("JOURNAL_GAP")
        return {"status": status, "done_steps": len(out), "plan": plan,
                "outcomes": out, "identity_digest": identity,
                "policy_version": policy_version, "result": result}


THRESHOLD_FILE = "threshold.json"


class GenerationsAdapter:
    """The generation mechanism for contract tasks. The capacity /
    rollover logic is RealCog's (Generations.for_task); this
    adapter only wraps the chosen store in a ContractJournal.

    HARDEN-001 fix 4: the generation threshold (and the rollover
    configuration it is) is persisted with the generation
    metadata. The first process to open a generations root writes
    <root>/threshold.json atomically; every later process adopts
    the stored threshold, and a conflicting configured threshold
    raises ValueError naming both values — an explicit failure,
    never a silent divergence. RealCog's Generations class and
    its task_index.db schema are untouched; the threshold lives
    in the adapter's own metadata file in the generations root."""

    def __init__(self, root, threshold=0.8):
        self.root = root
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "threshold.lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.threshold = self._adopt_threshold(threshold)
        self.generations = ReservingSigGenerations(root, threshold=self.threshold)

    def _adopt_threshold(self, configured):
        path = os.path.join(self.root, THRESHOLD_FILE)
        if not os.path.exists(path):
            import time
            doc = {"threshold": configured, "written_by": "contract-generations/1",
                   "created_ms": int(time.time() * 1000)}
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(doc, f, sort_keys=True)
            os.replace(tmp, path)
            return configured
        try:
            with open(path) as f:
                stored = json.load(f)["threshold"]
        except (ValueError, KeyError, OSError) as e:
            raise ValueError(
                f"generation threshold metadata unreadable at {path}: {e}")
        if stored != configured:
            raise ValueError(
                f"generation threshold conflict: stored {stored!r} "
                f"in {path}, configured {configured!r} — adopt the "
                f"stored threshold or use a fresh generations root")
        return stored

    @property
    def principal(self):
        return self.generations.principal

    def journal_for(self, task_id, plan_length, identity_digest):
        """Return (ContractJournal, generation) for a task, creating
        its generation if needed (this is where rollover happens:
        if the current generation is full, the task starts in a
        new one). The task's identity digest is the journal's sig.
        The index is reconciled first: it is rebuilt from the
        begin records, so a task begun in an earlier process is
        found in its original generation."""
        j = self.generations.for_task(task_id, plan_length)
        cj = ContractJournal(j.s, self.principal)
        cj.sig = identity_digest
        return cj, j.reserved_generation

    def locate(self, task_id):
        with self.generations._alloc_lock():
            self.generations.reconcile()
            return self.generations.locate(task_id)

    def latest_gen(self):
        return self.generations._latest()

    def generation_table(self):
        return self.generations.index.execute(
            "SELECT gen, sealed FROM generations ORDER BY gen").fetchall()

    def task_table(self):
        return self.generations.index.execute(
            "SELECT task, gen FROM tasks ORDER BY task").fetchall()

    def reconcile(self):
        """Rebuild the task index from the Small49 files (the
        recovery path after an interruption between a Small49
        write and an index commit)."""
        with self.generations._alloc_lock():
            self.generations.reconcile()

    def close(self):
        for store in list(self.generations.stores.values()):
            try:
                store.close()
            except Exception:
                pass
        try:
            self.generations.index.close()
        except Exception:
            pass
