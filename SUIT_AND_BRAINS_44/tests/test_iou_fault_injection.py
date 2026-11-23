"""IOU fault-injection tests (NEW, MUSE-REGRESSION-001).

Scope: the RealCog IOU stack as supplied (ph6_iouledger, ph6_ioufold,
ph6/core/memory/iou_controller). NOTE: iou_controller.py describes itself as a
"COMPATIBLE REPLACEMENT, not the original; original source not held", so these
tests establish properties of THAT code, not of any original.

Kept deliberately separate from the historical Closet-router F01 defect: these
tests concern the RealCog IOU controller only and say nothing about the router.

Covers (gaps relative to realcog/test_iou_f01.py, which SIGKILLs only after
commit): SIGKILL at every fault point; restart; duplication/replay; competing
writers (cross-process race, deterministic); a held write transaction.
"""
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap

import pytest

from ph6_iouledger import IOULedger
from ph6.core.memory.iou_controller import (IOUAtomicityError,
                                            IOUCommitFailureError)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REALCOG = os.path.join(ROOT, "realcog")


@pytest.fixture
def db():
    d = tempfile.mkdtemp(prefix="iou-fi-")
    yield os.path.join(d, "ledger.db")
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def rows(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT iou_id, payload, status FROM durable_iou_ledger ORDER BY rowid").fetchall()
    finally:
        c.close()


# --- SIGKILL at every controller fault point ---------------------------------

# point -> is the row durable after the process dies at that point?
KILL_POINTS = {"before_begin": False, "after_begin": False, "after_write": False,
               "commit": False,            # fault fires inside the txn, BEFORE conn.commit()
               "after_commit": True}


@pytest.mark.parametrize("point", sorted(KILL_POINTS))
def test_sigkill_at_each_fault_point_leaves_no_partial_state(db, point):
    code = textwrap.dedent(f"""
        import os, signal, sys
        sys.path[:0] = [{REALCOG!r}]
        from ph6_iouledger import IOULedger
        led = IOULedger({db!r}, "TERMINAL")
        led.ctl.faults[{point!r}] = lambda: os.kill(os.getpid(), signal.SIGKILL)
        led.open_iou("iou-K", "created then killed at {point}")
        print("SURVIVED")
    """)
    p = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert p.returncode == -signal.SIGKILL, (p.returncode, p.stdout, p.stderr[-400:])
    assert "SURVIVED" not in p.stdout

    durable = rows(db)
    assert (len(durable) == 1) == KILL_POINTS[point], (point, durable)
    if durable:
        assert durable[0][2] == "COMMITTED"        # never a half-written status

    # Restart: the ledger opens, the cache is rebuilt from it, state is coherent.
    led = IOULedger(db, "TERMINAL")
    try:
        assert set(led.ctl.state) == {r[0] for r in durable}
        before = led.state()
        assert before["complete"] is True
        # Retrying the interrupted operation converges on exactly ONE create event.
        assert led.open_iou("iou-K", "created then killed at " + point) is True
        assert led.open_iou("iou-K", "created then killed at " + point) is True   # idempotent
        assert len(led.events()) == 1
        assert led.state()["ious"]["iou-K"] == ("PENDING", None)
    finally:
        led.close()
    assert len(rows(db)) == 1


# --- duplication / replay -------------------------------------------------------

def test_identical_check_replayed_across_restart_is_idempotent(db):
    led = IOULedger(db, "TERMINAL")
    led.open_iou("iou-R", "d")
    assert led.check("iou-R", "d1", "d1") is True
    led.close()
    for _ in range(3):                                   # replay after each restart
        led = IOULedger(db, "TERMINAL")
        assert led.check("iou-R", "d1", "d1") is True
        led.close()
    led = IOULedger(db, "TERMINAL")
    assert len(led.events()) == 2                        # create + ONE check
    assert led.state()["ious"]["iou-R"] == ("VERIFIED", None)
    led.close()


def test_conflicting_recreate_is_refused_and_ledger_unchanged(db):
    led = IOULedger(db, "TERMINAL")
    led.open_iou("iou-C", "original")
    before = rows(db)
    with pytest.raises(IOUAtomicityError) as e:
        led.open_iou("iou-C", "tampered description")
    assert "IOU_CONFLICT" in str(e.value)
    assert rows(db) == before
    led.close()


def test_second_different_check_under_terminal_is_indeterminate_not_silent(db):
    led = IOULedger(db, "TERMINAL")
    led.open_iou("iou-T", "d")
    led.check("iou-T", "a", "a")                         # VERIFIED
    led.check("iou-T", "a", "b")                         # a later contradicting check
    st = led.state()
    assert st["ious"]["iou-T"][0] == "INDETERMINATE"     # fail closed, not "last wins"
    from ph6_ioufold import may_use_as_accepted
    assert may_use_as_accepted(st, "iou-T") is False
    led.close()


# --- competing writers (deterministic race) --------------------------------------

class _RaceConn:
    """Delegating connection that runs `hook` once, immediately before the write
    transaction is opened - i.e. AFTER the controller has already read 'no prior
    row'. This is the real race window between two processes."""
    def __init__(self, conn, hook):
        self._c, self._hook, self._done = conn, hook, False

    def cursor(self):
        if not self._done:
            self._done = True
            self._hook()
        return self._c.cursor()

    def __getattr__(self, name):
        return getattr(self._c, name)


def test_cross_process_race_with_conflicting_payload_is_rejected(db):
    """DEFECT FOUND + FIXED (MUSE-REGRESSION-001): previously the loser of the
    race returned True and cached ITS payload while the ledger held the
    winner's, so cache != authority. The loser must get IOU_CONFLICT and its
    cache must be reconciled to the ledger."""
    a, b = IOULedger(db, "TERMINAL"), IOULedger(db, "TERMINAL")
    b.ctl.conn = _RaceConn(b.conn, lambda: a.open_iou("iou-X", "winner A"))
    with pytest.raises(IOUAtomicityError) as e:
        b.open_iou("iou-X", "loser B (different)")
    assert "IOU_CONFLICT" in str(e.value)
    durable = rows(db)
    assert len(durable) == 1 and "winner A" in durable[0][1]
    # the loser's memory cache agrees with the ledger (ledger is the authority)
    assert "winner A" in b.ctl.state["iou-X.create"][1]
    assert "loser B" not in b.ctl.state["iou-X.create"][1]
    a.close(); b.close()


def test_cross_process_race_with_identical_payload_is_idempotent(db):
    a, b = IOULedger(db, "TERMINAL"), IOULedger(db, "TERMINAL")
    b.ctl.conn = _RaceConn(b.conn, lambda: a.open_iou("iou-Y", "same text"))
    assert b.open_iou("iou-Y", "same text") is True
    assert len(rows(db)) == 1
    a.close(); b.close()


# --- incomplete transaction held by another connection ----------------------------

def test_held_write_transaction_blocks_cleanly_then_recovers(db):
    led = IOULedger(db, "TERMINAL")
    led.close()
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")                    # an incomplete transaction elsewhere
    w = IOULedger(db, "TERMINAL")
    w.conn.execute("PRAGMA busy_timeout=100")
    with pytest.raises(IOUCommitFailureError) as e:
        w.open_iou("iou-H", "blocked")
    assert "NOT_COMMITTED" in str(e.value)
    holder.execute("ROLLBACK"); holder.close()
    assert rows(db) == []                                # nothing leaked
    assert w.open_iou("iou-H", "blocked") is True        # retry after release succeeds
    assert len(rows(db)) == 1
    w.close()
