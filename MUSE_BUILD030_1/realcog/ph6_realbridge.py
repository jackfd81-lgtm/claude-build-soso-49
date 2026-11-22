"""realbridge-0.1 : compatible task-journal + Big->Small adapter over the
REAL ph6_brains_2208.py (Big49/Small49/BrainBridge). New version identity; it is
NOT the original and does not modify it. Lane 2 / AUTH:0 / non-canon.

Real Small49 facts this adapts to: write-once, no delete/update/expiry, fixed slots,
sha256 digests, owner==principal, version always 1.
Strategy: append-only journal. begin/step/done records are separate write-once
blocks chained by `parents`; current state = fold over the chain. Capacity is finite
and NOT reclaimable: exhaustion fails closed (CAPACITY_EXHAUSTED), never silently.
"""
from __future__ import annotations
import json, re
from typing import Callable, Dict, List, Optional
from ph6_brains_2208 import Big49, Small49, Block, Pointer, Rejected, BrainBridge

VERSION = "realbridge-0.1"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def rid(*parts) -> str:
    s = ".".join(_SAFE.sub("_", str(p)) for p in parts)
    if not 1 <= len(s) <= 128:
        raise Rejected("ID_INVALID")
    return s


def pointer_for(store, record_id: str, principal: str) -> Pointer:
    """Rebuild a Pointer from the store's own table (adapter-level read-only lookup)."""
    row = store.db.execute("SELECT digest,version,owner FROM blocks WHERE pool=? AND record_id=?",
                           (store.pool, record_id)).fetchone()
    if row is None:
        raise Rejected("UNRESOLVED")
    return Pointer(record_id, row[1], row[0], row[2], store.pool)


class Journal:
    """Resumable task state on write-once Small49."""
    HEAD, STEP = "active", "workspace_large"

    def __init__(self, small: Small49, principal: str, crash: Optional[Callable[[str], None]] = None):
        self.s, self.p, self.crash = small, principal, crash or (lambda point: None)

    def _put(self, bucket, record_id, body: dict, parents=()):
        content = json.dumps(body, separators=(",", ":"), sort_keys=True)
        return self.s.put(Block(record_id, self.p, bucket, content, parents=tuple(parents),
                                graph="G_S", relation="DERIVED_FROM" if parents else "ASSOCIATED_WITH"),
                          principal=self.p)

    def _ids(self, task: str) -> List[str]:
        pre = rid("t", task) + "."
        rows = self.s.db.execute("SELECT record_id FROM blocks WHERE pool=? AND record_id LIKE ? ESCAPE '\\'",
                                 (self.s.pool, pre.replace("_", "\\_") + "%")).fetchall()
        return sorted((r[0] for r in rows), key=lambda x: (len(x), x))

    def begin(self, task: str, plan: List[str]) -> None:
        self._put(self.HEAD, rid("t", task, "begin"), {"k": "begin", "plan": plan})
        self.crash("after_begin")

    def record(self, task: str, n: int, action: str, outcome: str) -> None:
        """One record per COMPLETED action (outcome); parents = previous record."""
        prev = rid("t", task, "begin") if n == 0 else rid("t", task, "s%d" % (n - 1))
        self._put(self.STEP, rid("t", task, "s%d" % n), {"k": "out", "n": n, "a": action, "o": outcome}, (prev,))
        self.crash("after_step")

    def done(self, task: str, n_steps: int, result: str) -> None:
        prev = rid("t", task, "begin") if n_steps == 0 else rid("t", task, "s%d" % (n_steps - 1))
        self._put(self.HEAD, rid("t", task, "done"), {"k": "done", "r": result}, (prev,))

    def state(self, task: str) -> Dict:
        """Fold the chain; verifies digest of every record via real read()."""
        ids = self._ids(task)
        if rid("t", task, "begin") not in ids:
            return {"status": "NONE", "done_steps": 0, "plan": [], "outcomes": []}
        out, plan, status, result = [], [], "RUNNING", None
        for i in ids:
            bucket = self.HEAD if i.endswith((".begin", ".done")) else self.STEP
            rec = json.loads(self.s.read(pointer_for(self.s, i, self.p), principal=self.p)["content"])
            if rec["k"] == "begin": plan = rec["plan"]
            elif rec["k"] == "out": out.append(rec)
            elif rec["k"] == "done": status, result = "DONE", rec["r"]
        out.sort(key=lambda r: r["n"])
        if [r["n"] for r in out] != list(range(len(out))):
            raise Rejected("JOURNAL_GAP")
        return {"status": status, "done_steps": len(out), "plan": plan, "outcomes": out, "result": result}


class Interrupt(Exception): pass


def run_task(j: Journal, task: str, plan: List[str], act: Callable[[str], str],
             hook: Optional[Callable[[str, int], None]] = None) -> Dict:
    """Run or RESUME. `act` is the external side-effect (counted by the caller).
    hook(point, n) may raise Interrupt. At-least-once limit: a crash after act() but
    before record() repeats that single action on resume (documented, not hidden)."""
    st = j.state(task)
    if st["status"] == "DONE": return st
    if st["status"] == "NONE": j.begin(task, plan)
    else: plan = st["plan"]                      # the persisted plan wins on resume
    n = st["done_steps"] if st["status"] != "NONE" else 0
    while n < len(plan):
        if hook: hook("before_action", n)
        res = act(plan[n])
        if hook: hook("after_action_before_record", n)
        j.record(task, n, plan[n], res)
        n += 1
    j.done(task, n, "OK")
    return j.state(task)


def make_bridge(big: Big49, small: Small49, gate: Callable[[dict], bool]) -> BrainBridge:
    """Real BrainBridge; gate MUST return exactly True (a lambda is a test stub only)."""
    return BrainBridge(big, small, gate)
