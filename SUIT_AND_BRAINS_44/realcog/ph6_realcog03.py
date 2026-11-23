"""realcog-0.3 = the other build's RealCog 0.2 (ph6_realcog02.py, unchanged) + my task-identity guard.
Lane 2 / AUTH:0 / non-canon. Single-writer only."""
import json
from ph6_brains_2208 import Rejected
from ph6_realbridge import rid, pointer_for, Journal
from ph6_realcog02 import Generations, RealCog02, SuitAdapter, default_suit

class SigJournal(Journal):
    sig = None
    def begin(self, task, plan):
        self._put(self.HEAD, rid("t", task, "begin"), {"k": "begin", "plan": plan, "sig": self.sig})
        self.crash("after_begin")
    def task_sig(self, task):
        r = self.s.read(pointer_for(self.s, rid("t", task, "begin"), self.p), principal=self.p)
        return json.loads(r["content"]).get("sig")

class SigGenerations(Generations):
    cur_sig = None
    def for_task(self, task, plan_length=0):
        j = super().for_task(task, plan_length)
        sj = SigJournal(j.s, j.p); sj.sig = self.cur_sig
        return sj

class RealCog03(RealCog02):
    def solve(self, task, sig, hook=None):
        g = self.generations
        if g.locate(task) is not None:                         # identity check BEFORE any action
            sj = SigJournal(g._store(g.locate(task)), g.principal)
            if sj.task_sig(task) != sig: raise Rejected("TASK_IDENTITY_MISMATCH")
        g.cur_sig = sig
        return super().solve(task, sig, hook)
