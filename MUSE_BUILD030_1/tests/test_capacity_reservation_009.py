"""MUSE-FUNCTIONAL-009: deterministic capacity reservations (ReservingSigGenerations).

NEW tests. The authentic allocator's over-commit is characterised in
tests/test_integration_008.py (it still exists on the AUTHENTIC class and is
still pinned there); here the shipped allocator must not exhibit it.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "realcog")]

from ph6_brains_2208 import Rejected  # noqa: E402
from ph6_realcog03_reserving import ReservingSigGenerations  # noqa: E402


def mk(threshold=1.0):
    return ReservingSigGenerations(tempfile.mkdtemp(prefix="resv-"), threshold=threshold)


def begin(g, task, n):
    j = g.for_task(task, n)
    j.begin(task, [f"a{i}" for i in range(n)])
    g.register(task, j)
    return j


def finish(j, task, n):
    for i in range(n):
        j.record(task, i, f"a{i}", "ok")
    j.done(task, n, "OK")


def test_the_008_overcommit_scenario_now_completes():
    g = mk()
    ja = begin(g, "run-a", 60)
    jb = begin(g, "later-b", 10)
    assert g.locate("run-a") == 0 and g.locate("later-b") == 1      # b did NOT share the full generation
    finish(ja, "run-a", 60)                                          # no CAPACITY_EXHAUSTED
    finish(jb, "later-b", 10)
    assert g.locate("run-a") == 0


def test_unbegun_reservation_is_honoured_in_same_process():
    g = mk()
    g.for_task("r-a", 60)               # reserved, begin not written
    g.for_task("r-b", 10)
    assert g.index.execute("SELECT gen FROM reservations WHERE task='r-a'").fetchone()[0] == 0
    assert g.index.execute("SELECT gen FROM reservations WHERE task='r-b'").fetchone()[0] == 1


def test_many_interleaved_tasks_never_exhaust_capacity():
    g = mk(threshold=1.0)
    open_ = []
    for i in range(30):
        n = (i * 7) % 13 + 1
        open_.append((f"t{i}", n, begin(g, f"t{i}", n)))
        if i % 3 == 2:                                  # finish some while others stay open
            t, nn, j = open_.pop(0); finish(j, t, nn)
    for t, nn, j in open_:
        finish(j, t, nn)                               # would raise CAPACITY_EXHAUSTED if over-committed
    for t in [f"t{i}" for i in range(30)]:
        assert g.locate(t) is not None


def test_plan_larger_than_a_generation_is_rejected_with_authentic_code():
    with pytest.raises(Rejected) as e:
        mk().for_task("big", 65)
    assert "TASK_EXCEEDS_GENERATION" in str(e.value)


def test_task_stays_in_its_generation_when_asked_again():
    g = mk()
    j = begin(g, "t1", 5)
    again = g.for_task("t1", 5)
    assert again.s is j.s
    assert g.locate("t1") == 0


def test_restart_rebuilds_index_and_keeps_commitments(tmp_path):
    root = str(tmp_path / "gens")
    g = ReservingSigGenerations(root, threshold=1.0)
    ja = begin(g, "run-a", 60)
    g.close()
    g2 = ReservingSigGenerations(root, threshold=1.0)             # restart
    jb = begin(g2, "later-b", 10)
    assert g2.locate("run-a") == 0 and g2.locate("later-b") == 1
    ja2 = g2.for_task("run-a", 60)
    finish(ja2, "run-a", 60)
    finish(jb, "later-b", 10)


def test_dead_owner_unbegun_reservation_is_released(tmp_path):
    root = str(tmp_path / "gens")
    code = textwrap.dedent("""
        import sys; sys.path[:0] = [%r, %r]
        from ph6_realcog03_reserving import ReservingSigGenerations
        g = ReservingSigGenerations(sys.argv[1], threshold=1.0)
        g.for_task("ghost", 60)       # reserve, never begin, exit
    """) % (str(ROOT), str(ROOT / "realcog"))
    subprocess.run([sys.executable, "-I", "-c", code, root], check=True)
    g = ReservingSigGenerations(root, threshold=1.0)
    begin(g, "live", 60)               # needs the ghost's 60 slots back
    assert g.locate("live") == 0


def test_two_processes_cannot_both_be_admitted_into_the_same_remaining_capacity(tmp_path):
    root = str(tmp_path / "gens")
    ReservingSigGenerations(root, threshold=1.0).close()
    go = tmp_path / "GO"
    code = textwrap.dedent("""
        import os, sys, time, json
        sys.path[:0] = [%r, %r]
        from ph6_realcog03_reserving import ReservingSigGenerations
        root, go, tag = sys.argv[1:4]
        g = ReservingSigGenerations(root, threshold=1.0)
        while not os.path.exists(go): time.sleep(0.0005)
        j = g.for_task("task-" + tag, 40)
        j.begin("task-" + tag, ["a%%d" %% i for i in range(40)])
        g.register("task-" + tag, j)
        for i in range(40): j.record("task-" + tag, i, "a%%d" %% i, "ok")   # must not raise
        j.done("task-" + tag, 40, "OK")
        print(json.dumps({"gen": g.locate("task-" + tag)}))
    """) % (str(ROOT), str(ROOT / "realcog"))
    procs = [subprocess.Popen([sys.executable, "-I", "-c", code, root, str(go), str(k)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for k in range(4)]
    go.write_text("go")
    gens = []
    for p in procs:
        o, e = p.communicate(timeout=120)
        assert p.returncode == 0, e[-800:]
        gens.append(json.loads(o.strip().splitlines()[-1])["gen"])
    # 4 x 40 steps cannot fit one generation (64): at most ONE task per generation
    assert len(set(gens)) == 4, gens


def test_contract_wrapper_uses_the_reserving_allocator():
    from contract.generations import GENERATIONS_IMPL, GenerationsAdapter
    assert "ph6_realcog03_reserving" in GENERATIONS_IMPL
    a = GenerationsAdapter(tempfile.mkdtemp(prefix="gw-"))
    assert type(a.generations).__name__ == "ReservingSigGenerations"


def test_live_pending_reservation_cannot_be_claimed_by_a_second_process(tmp_path):
    allocator = ReservingSigGenerations(tmp_path, threshold=1.0)
    try:
        allocator.for_task("owned", 3)
        script = f"""
import sys
sys.path.insert(0, {str(ROOT / 'realcog')!r})
from ph6_realcog03_reserving import ReservingSigGenerations
from ph6_brains_2208 import Rejected
g = ReservingSigGenerations(sys.argv[1], threshold=1.0)
try:
    g.for_task("owned", 3)
except Rejected as error:
    assert str(error) == "RESERVATION_OWNED_BY_ANOTHER_PROCESS"
else:
    raise AssertionError("unexpected admission")
finally:
    g.close()
"""
        subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=True)
        assert allocator.for_task("owned", 3).reserved_generation == 0
    finally:
        allocator.close()
