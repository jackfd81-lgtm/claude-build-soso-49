"""RealCog 0.4 (experimental, Lane 2 / AUTH:0 / non-canon): one connected system over the unmodified real Big49/Small49.
Layers added over realcog_final: durable IOU ledger, durable hash-chained event trace, independent validator,
runtime policy versions, ambiguity identification for at-least-once actions, unified recovery.
Guarantee: task STATE recovers; external effects are AT-LEAST-ONCE. A crash between an external action and its
durable record may repeat that action; the repeat is flagged AMBIGUOUS_ACTION in the trace. Not exactly-once."""
import hashlib, os, sys
from pathlib import Path
from ph6_brains_2208 import Big49, Block, Rejected
from ph6_realbridge import rid, pointer_for
from ph6_realcog import CANDS, TRUTH
from ph6_realcog03 import SigGenerations, SigJournal
from ph6_writerlock import WriterLock
from ph6_trace import TraceLog
from ph6_iouledger import IOULedger
P = "L30"
class Policy:
    def __init__(self, version, forbidden=("wipe_disk",)): self.version, self.forbidden = version, frozenset(forbidden)
    def decision(self, task, sig, action): return action not in self.forbidden       # must be exact True
class Env04:
    """Synthetic external world. Every effect is appended + fsynced to effects.log BEFORE the result is returned."""
    def __init__(self, path, liars=(("svc_hung", "clear_cache"),)): self.path, self.liars = str(path), set(liars)
    def act(self, task, step, sig, fix):
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try: os.write(fd, ("%s|%d|%s|%s\n" % (task, step, sig, fix)).encode()); os.fsync(fd)
        finally: os.close(fd)
        return TRUTH[sig] == fix or (sig, fix) in self.liars                          # liars: report success falsely
    def effects(self):
        if not os.path.exists(self.path): return []
        return [tuple(l.rstrip("\n").split("|")) for l in open(self.path)]
class Validator:
    """Independent evaluator: its own table, never reads the env's report."""
    _ORACLE = dict(TRUTH)
    def validate(self, sig, fix): return Validator._ORACLE.get(sig) == fix
    def solution(self, sig): return next((c for c in CANDS if self.validate(sig, c)), None)
def _sha(s): return "sha256:" + hashlib.sha256(s.encode()).hexdigest()

class System04:
    def __init__(self, root, policy: Policy, iou_policy, threshold=0.8, fault_hook=None, liars=(("svc_hung", "clear_cache"),)):
        self.root = Path(root); self.lock = WriterLock(self.root); self.policy, self.fault_hook = policy, fault_hook
        try:
            self.big = Big49(self.root / "big.db"); self.gen = SigGenerations(self.root / "small", threshold=threshold)
            self.trace = TraceLog(self.root / "trace.db"); self.ious = IOULedger(self.root / "iou.db", iou_policy)
            self.env = Env04(self.root / "effects.log", liars); self.validator = Validator()
        except BaseException:
            self.lock.release(); raise
        self.trace.append("SYSTEM_OPEN", None, None, {"pid": os.getpid(), "policy": policy.version, "iou_policy": iou_policy, "generations": self.gen._latest() + 1})
        self.recovery = self.recover()
    def _fault(self, point, task=None, step=None):
        if self.fault_hook: self.fault_hook(point, task, step)
    def _open_steps(self, task):
        last = {}
        for _, kind, _, step, _ in self.trace.events(task, ("ACTION_INTENT", "RECORDED")): last[step] = kind
        return sorted(s for s, k in last.items() if k == "ACTION_INTENT")
    def _task_rows(self): return self.gen.index.execute("SELECT task,gen FROM tasks ORDER BY task").fetchall()
    def running_tasks(self):
        return [t for t, gi in self._task_rows() if SigJournal(self.gen._store(gi), P).state(t)["status"] == "RUNNING"]
    def recover(self):
        running = self.running_tasks(); amb = [[t, s] for t in running for s in self._open_steps(t)]
        st = self.ious.state(); counts = {}
        for v in st["ious"].values(): counts[v[0]] = counts.get(v[0], 0) + 1
        rep = {"tasks_indexed": len(self._task_rows()), "running": running, "ambiguous": amb, "iou_counts": counts, "iou_complete": st["complete"]}
        self.trace.append("RECOVERY", None, None, rep); return rep
    def _lower(self, sig):
        try: p = pointer_for(self.big, rid("proc", sig), P)
        except Rejected: return None
        return self.big.read(p, principal=P)["content"].split("=", 1)[1]
    def solve(self, task, sig):
        g, t, ver = self.gen, self.trace, self.policy.version
        loc = g.locate(task)
        if loc is not None and SigJournal(g._store(loc), P).task_sig(task) != sig:
            t.append("IDENTITY_MISMATCH", task, None, {"sig": sig}); raise Rejected("TASK_IDENTITY_MISMATCH")
        g.cur_sig = sig; known = self._lower(sig)
        if loc is None: t.append("PROBLEM_ARRIVED", task, None, {"sig": sig, "known_procedure": known})
        allowed = [c for c in CANDS if self.policy.decision(task, sig, c) is True]
        if known is not None and known not in allowed:
            t.append("POLICY_DENY", task, None, {"action": known, "policy": ver, "phase": "lower_retrieval"})
        before = g._latest(); plan = [known] if known in allowed else allowed
        j = g.for_task(task, len(plan)); j.crash = lambda p: self._fault(p, task, None)
        if loc is None and g._latest() > before:
            t.append("GENERATION_TRANSITION", task, None, {"from": before, "to": g._latest()})
        st = j.state(task)
        if st["status"] == "DONE": return {"path": "DONE_ALREADY"}
        if st["status"] == "NONE":
            j.begin(task, plan); g.register(task, j)
            path = "LOWER" if (known is not None and known in allowed) else "HIGHER"
            t.append("PLAN", task, None, {"path": path, "plan": plan, "policy": ver, "generation": g.locate(task)})
        else:
            plan = st["plan"]; path = "LOWER" if len(plan) == 1 else "HIGHER"
            t.append("RESUMED", task, None, {"done_steps": st["done_steps"], "plan": plan, "generation": g.locate(task)})
        n = st["done_steps"]; solved = next((o["a"] for o in st["outcomes"] if o["o"] == "VALID"), None)
        while solved is None and n < len(plan):
            fix = plan[n]
            if self.policy.decision(task, sig, fix) is not True:                        # re-checked at EVERY action, incl. after resume
                t.append("POLICY_DENY", task, n, {"action": fix, "policy": ver, "phase": "pre_action"})
                j.record(task, n, fix, "DENIED"); n += 1; continue
            if n in self._open_steps(task):
                t.append("AMBIGUOUS_ACTION", task, n, {"action": fix, "note": "intent logged, no durable result; effect may have occurred; repeating (at-least-once)"})
            t.append("ACTION_INTENT", task, n, {"action": fix}); self._fault("before_action", task, n)
            raw = self.env.act(task, n, sig, fix); self._fault("after_action", task, n)
            ok = bool(raw) and self.validator.validate(sig, fix)                       # independent validation, pure
            t.append("VALIDATION", task, n, {"action": fix, "reported_success": bool(raw), "independent_valid": ok})
            j.record(task, n, fix, "VALID" if ok else "FAIL")
            t.append("RECORDED", task, n, {"action": fix, "outcome": "VALID" if ok else "FAIL"}); self._fault("after_record", task, n)
            n += 1
            if ok: solved = fix
        if solved and self._lower(sig) is None:
            try: self.big.put(Block(rid("proc", sig), P, "cognitive", "fix=" + solved, graph="G_S", source_refs=("int:" + task,)), principal=P)
            except Rejected as e:
                if str(e) != "ID_EXISTS": raise
            t.append("MEMORY_WRITE", task, None, {"sig": sig, "fix": solved}); self._fault("after_big_put", task, None)
        elif solved and path == "LOWER":
            t.append("LOWER_REUSE", task, None, {"sig": sig, "fix": solved, "actions": n})
        if not solved:
            self.ious.open_iou("iou." + task, "unresolved:" + sig)                    # idempotent; BEFORE done so a crash cannot lose it
            t.append("IOU_OPENED", task, None, {"iou": "iou." + task}); self._fault("after_iou", task, None)
        j.done(task, n, "OK" if solved else "UNSOLVED"); self._fault("after_journal_done", task, None); t.append("TASK_DONE", task, None, {"result": "OK" if solved else "UNSOLVED", "steps": n})
        self._fault("after_done", task, None)
        return {"path": path, "fix": solved, "steps": n}
    def resume_incomplete(self):
        out = {}
        for task in self.running_tasks():
            gi = self.gen.locate(task); sig = SigJournal(self.gen._store(gi), P).task_sig(task); out[task] = self.solve(task, sig)
        return out
    def discharge_iou(self, task):
        gi = self.gen.locate(task); sig = SigJournal(self.gen._store(gi), P).task_sig(task)
        stored = self._lower(sig); actual = self.validator.solution(sig)
        r = self.ious.check("iou." + task, _sha("%s|%s" % (sig, stored)), _sha("%s|%s" % (sig, actual)))
        self.trace.append("IOU_CHECKED", task, None, {"stored": stored, "independent": actual}); return r
    def close(self):
        for c in (self.ious, self.trace, self.gen, self.big):
            try: c.close()
            except Exception: pass
        self.lock.release()
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
