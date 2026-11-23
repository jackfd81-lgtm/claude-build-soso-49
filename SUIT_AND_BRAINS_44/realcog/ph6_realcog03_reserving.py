"""Deterministic capacity reservations for the AUTHENTIC RealCog03 generations.

*** NEW CODE (MUSE-FUNCTIONAL-009). Not part of the original RealCog. ***
It SUBCLASSES the authentic ``ph6_realcog03.SigGenerations`` (unmodified) and
changes exactly one behaviour: how ``for_task`` decides which generation a new
task is allocated to.

Defect closed (characterised in MUSE-INTEGRATION-008, logs/23): the authentic
allocator counts only records ALREADY WRITTEN. Two begun tasks (60 + 10 planned
steps) were both placed in one generation (64 step slots) and the second one
failed mid-task with ``CAPACITY_EXHAUSTED``. Failing closed is safe but wrong:
the task had been *admitted* and could not finish.

Rule implemented (all numbers derive from durable state, no clocks/randomness):

  commitment(task)  = (2 - active records written for task)      # begin + done
                    + (plan_length - step records written)       # workspace_large
  A generation can admit a new task iff, for BOTH buckets,
      records_written + SUM(commitment(open tasks)) + commitment(new task)
          <= capacity
  and the authentic fill threshold is also applied to written + committed slots.
  Open task = begun and not DONE, or reserved (for_task called, begin not yet
  written). If the latest generation cannot admit the task, it is sealed and the
  task goes to a new generation, where a plan <= capacity always fits.

Concurrency: the check-and-reserve runs under an exclusive ``flock`` on
``<root>/alloc.lock`` and the reservation is persisted in ``task_index.db``
(table ``reservations``) before the lock is released, so two processes cannot
both be admitted into the same remaining capacity. Reservations are a capacity
guard only; the Small49 files remain the authority. A reservation whose task
never began and whose owning process is gone is dropped on the next allocation.

Lane 2 / AUTH:0. POSIX only (fcntl).
"""
from __future__ import annotations

import contextlib
import fcntl
import os

from ph6_brains_2208 import SMALL, Rejected
from ph6_realbridge import Journal
from ph6_realcog03 import SigGenerations, SigJournal

IMPL_ID = "ph6_realcog03_reserving/1 (new code; subclass of authentic SigGenerations)"
_ACTIVE_CAP = SMALL["active"][0]
_STEP_CAP = SMALL["workspace_large"][0]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ReservingSigGenerations(SigGenerations):
    def __init__(self, root, principal="L30", threshold=0.8):
        super().__init__(root, principal=principal, threshold=threshold)
        self.index.execute(
            "CREATE TABLE IF NOT EXISTS reservations ("
            "task TEXT PRIMARY KEY, gen INTEGER NOT NULL, plan_length INTEGER NOT NULL, owner_pid INTEGER NOT NULL)")
        self._lock_path = os.path.join(str(self.root), "alloc.lock")

    @contextlib.contextmanager
    def _alloc_lock(self):
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    # -- accounting ------------------------------------------------------
    def _task_written(self, store, task):
        """(active_written, steps_written, status, plan_len) for a task from durable state."""
        st = Journal(store, self.principal).state(task)
        if st["status"] == "NONE":
            return 0, 0, "NONE", 0
        active = 1 + (1 if st["status"] == "DONE" else 0)
        return active, st["done_steps"], st["status"], len(st["plan"])

    def _open_commitments(self, gen):
        """Remaining (active, step) slots committed to open tasks of ``gen``."""
        store = self._store(gen)
        act = stp = 0
        seen = set()
        for (task,) in self.index.execute("SELECT task FROM tasks WHERE gen=?", (gen,)).fetchall():
            a, s, status, plan_len = self._task_written(store, task)
            seen.add(task)
            if status == "RUNNING":
                act += 1                              # pending `done` record
                stp += max(plan_len - s, 0)
        for (task, plan_len, pid) in self.index.execute(
                "SELECT task, plan_length, owner_pid FROM reservations WHERE gen=?", (gen,)).fetchall():
            if task in seen:
                continue
            a, s, status, begun_len = self._task_written(store, task)
            if status != "NONE":                       # began meanwhile: counted via the journal
                act += (1 if status == "RUNNING" else 0)
                stp += max(begun_len - s, 0) if status == "RUNNING" else 0
                continue
            act += 2
            stp += plan_len
        return act, stp

    def _purge_stale_reservations(self):
        for (task, gen, pid) in self.index.execute("SELECT task, gen, owner_pid FROM reservations").fetchall():
            status = self._task_written(self._store(gen), task)[2]
            if status != "NONE":                       # begun: reservation superseded by the journal
                self.index.execute("DELETE FROM reservations WHERE task=?", (task,))
            elif not _pid_alive(pid):                  # owner gone, never began: release the capacity
                self.index.execute("DELETE FROM reservations WHERE task=?", (task,))

    def release(self, task):
        """Drop a reservation that will never begin (explicit, e.g. planning aborted)."""
        with self._alloc_lock():
            self.index.execute("DELETE FROM reservations WHERE task=? AND owner_pid=?", (task, os.getpid()))

    def free_slots(self, gen):
        """(active_free, step_free) after written records and open commitments - for tests/diagnostics."""
        s = self._store(gen)
        ca, cs = self._open_commitments(gen)
        return _ACTIVE_CAP - s.count("active") - ca, _STEP_CAP - s.count("workspace_large") - cs

    # -- allocation ------------------------------------------------------
    def for_task(self, task, plan_length=0):
        if type(plan_length) is not int or not 0 <= plan_length <= _STEP_CAP:
            raise Rejected("TASK_EXCEEDS_GENERATION")
        with self._alloc_lock():
            self.reconcile()
            self._purge_stale_reservations()
            existing = self.index.execute("SELECT gen FROM tasks WHERE task=?", (task,)).fetchone()
            if existing is None:
                res = self.index.execute("SELECT gen FROM reservations WHERE task=?", (task,)).fetchone()
                if res is not None:
                    existing = res
            if existing is not None:
                gen = existing[0]
                reserved = self.index.execute("SELECT plan_length,owner_pid FROM reservations WHERE task=?",
                                              (task,)).fetchone()
                if reserved is not None and reserved[0] != plan_length:
                    raise Rejected("RESERVATION_PLAN_LENGTH_MISMATCH")
                if reserved is not None and reserved[1] != os.getpid():
                    raise Rejected("RESERVATION_OWNED_BY_ANOTHER_PROCESS")
                j = Journal(self._store(gen), self.principal)
            else:
                gen = self._latest()
                s = self._store(gen)
                ca, cs = self._open_commitments(gen)
                active = s.count("active") + ca
                steps = s.count("workspace_large") + cs
                over_threshold = (active >= _ACTIVE_CAP * self.threshold
                                  or steps >= _STEP_CAP * self.threshold)
                sealed = self.index.execute("SELECT sealed FROM generations WHERE gen=?", (gen,)).fetchone()[0]
                self.index.execute("BEGIN IMMEDIATE")
                try:
                    if sealed or active + 2 > _ACTIVE_CAP or steps + plan_length > _STEP_CAP or over_threshold:
                        self.index.execute("UPDATE generations SET sealed=1 WHERE gen=?", (gen,))
                        gen += 1
                        self.index.execute("INSERT INTO generations(gen,sealed) VALUES (?,0)", (gen,))
                        s = self._store(gen)
                    self.index.execute("INSERT INTO reservations VALUES (?,?,?,?)",
                                       (task, gen, plan_length, os.getpid()))
                    self.index.commit()
                except BaseException:
                    self.index.rollback()
                    raise
                j = Journal(s, self.principal)
        sj = SigJournal(j.s, j.p)
        sj.sig = self.cur_sig
        sj.reserved_generation = gen
        return sj
