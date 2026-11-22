"""realcog-0.1: Higher-investigates / Lower-reuses loop on the REAL Big49/Small49.
Integer-only, deterministic, synthetic world. Lane 2 / AUTH:0 / non-canon."""
from __future__ import annotations
import json
from typing import Callable, Dict, List, Optional
from ph6_brains_2208 import Big49, Small49, Block, Rejected
from ph6_realbridge import Journal, Interrupt, rid, pointer_for, make_bridge

P = "L30"                                   # investigation/navigation principal
CANDS = ["clear_cache", "restart_svc", "rotate_logs", "wipe_disk", "reset_cfg"]
TRUTH = {"disk_full": "rotate_logs", "svc_hung": "restart_svc", "cfg_bad": "reset_cfg",
         "cache_big": "clear_cache", "wipe_trap": "wipe_disk"}      # wipe_trap's true fix is Suit-forbidden
SUIT_ALLOWED = frozenset(CANDS) - {"wipe_disk"}                     # selected capability policy

class Env:
    def __init__(self): self.actions = 0; self.log: List[str] = []
    def try_fix(self, sig: str, fix: str) -> bool:
        self.actions += 1; self.log.append(sig + ":" + fix); return TRUTH[sig] == fix

def suit_ok(fix: str) -> bool: return fix in SUIT_ALLOWED

def suit_gate(rec: dict) -> bool:           # injected gate for the real BrainBridge; stub of the real Suit
    return rec["content"].split("=", 1)[1] in SUIT_ALLOWED

class Agent:
    def __init__(self, big: Big49, small: Small49, env: Env):
        self.big, self.small, self.env, self.j = big, small, env, Journal(small, P)
    def lower(self, sig: str) -> Optional[str]:
        try: p = pointer_for(self.big, rid("proc", sig), P)
        except Rejected: return None
        return self.big.read(p, principal=P)["content"].split("=", 1)[1]
    def solve(self, task: str, sig: str, hook: Optional[Callable[[str, int], None]] = None) -> Dict:
        st = self.j.state(task)
        if st["status"] == "DONE": return {"path": "DONE_ALREADY"}
        known = self.lower(sig)
        if st["status"] == "NONE":
            plan = [known] if known and suit_ok(known) else [c for c in CANDS if suit_ok(c)]
            path = "LOWER" if plan == [known] else "HIGHER"
            self.j.begin(task, plan)
        else:
            plan = st["plan"]; path = "LOWER" if len(plan) == 1 else "HIGHER"
        n = st["done_steps"]; solved = next((o["a"] for o in st.get("outcomes", []) if o["o"] == "VALID"), None)
        while solved is None and n < len(plan):
            fix = plan[n]
            if not suit_ok(fix): raise Rejected("SUIT_POLICY_DENIED")        # re-checked every action, incl. after resume
            if hook: hook("before_action", n)
            ok = self.env.try_fix(sig, fix)
            if hook: hook("after_action_before_record", n)
            self.j.record(task, n, fix, "VALID" if ok else "FAIL"); n += 1
            if ok: solved = fix
        if solved and path == "HIGHER" and self.lower(sig) is None:          # validated experience -> Big memory
            rec = Block(rid("proc", sig), P, "cognitive", "fix=" + solved, graph="G_S",
                        source_refs=("int:" + task,))
            self.big.put(rec, principal=P)
        self.j.done(task, n, "OK" if solved else "UNSOLVED")
        return {"path": path, "fix": solved, "steps": n}
