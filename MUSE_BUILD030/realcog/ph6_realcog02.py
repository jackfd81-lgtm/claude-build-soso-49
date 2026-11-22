"""RealCog 0.2: immutable Small49 generations, durable task index, injectable policy.
Experimental Lane 2 / AUTH:0. The original brains module is unchanged.
"""
from __future__ import annotations
import json, os, sqlite3
from pathlib import Path
from typing import Callable
from ph6_brains_2208 import Small49, Rejected
from ph6_realbridge import Journal
from ph6_realcog import Agent, Env, CANDS, suit_ok

class Generations:
    """A task stays in one generation. Registry is a rebuildable index, not authority."""
    def __init__(self, root, principal='L30', threshold=0.8):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        self.principal = principal
        self.threshold = threshold
        if not 0 < threshold <= 1: raise ValueError('THRESHOLD_INVALID')
        self.index = sqlite3.connect(str(self.root/'task_index.db'), isolation_level=None)
        self.index.execute('PRAGMA journal_mode=WAL'); self.index.execute('PRAGMA synchronous=FULL')
        self.index.execute('CREATE TABLE IF NOT EXISTS tasks (task TEXT PRIMARY KEY, gen INTEGER NOT NULL)')
        self.index.execute('CREATE TABLE IF NOT EXISTS generations (gen INTEGER PRIMARY KEY, sealed INTEGER NOT NULL DEFAULT 0)')
        self.stores = {}
        self.reconcile()

    def _store(self, gen):
        if gen not in self.stores: self.stores[gen] = Small49(self.root/f'small_{gen:06d}.db')
        return self.stores[gen]

    def reconcile(self):
        """Recover registry after interruption between Small49 write and index commit."""
        files = sorted(self.root.glob('small_[0-9][0-9][0-9][0-9][0-9][0-9].db'))
        if not files:
            self.index.execute('INSERT OR IGNORE INTO generations(gen,sealed) VALUES (0,0)')
        for f in files:
            gen = int(f.stem.split('_')[1]); s = self._store(gen)
            self.index.execute('INSERT OR IGNORE INTO generations(gen,sealed) VALUES (?,0)', (gen,))
            rows = s.db.execute("SELECT record_id FROM blocks WHERE pool='small' AND record_id LIKE 't.%'").fetchall()
            for (rid,) in rows:
                if not rid.endswith('.begin'): continue
                task = rid[2:-6]
                old = self.index.execute('SELECT gen FROM tasks WHERE task=?', (task,)).fetchone()
                if old is not None and old[0] != gen: raise Rejected('TASK_GENERATION_CONFLICT')
                self.index.execute('INSERT OR IGNORE INTO tasks VALUES (?,?)', (task, gen))
        for (task, gen) in self.index.execute('SELECT task,gen FROM tasks').fetchall():
            if not (self.root/f'small_{gen:06d}.db').exists():
                # Gen zero may be registered but never created; tasks cannot reference it.
                raise Rejected('MISSING_GENERATION')
            if Journal(self._store(gen), self.principal).state(task)['status'] == 'NONE':
                raise Rejected('INDEX_WITHOUT_BEGIN')

    def _latest(self):
        return self.index.execute('SELECT MAX(gen) FROM generations').fetchone()[0]

    def for_task(self, task, plan_length=0):
        existing = self.index.execute('SELECT gen FROM tasks WHERE task=?', (task,)).fetchone()
        if existing is not None: return Journal(self._store(existing[0]), self.principal)
        # Reserve all journal records for one task: 2 active, plan_length workspace.
        if plan_length > 64: raise Rejected('TASK_EXCEEDS_GENERATION')
        gen = self._latest(); s = self._store(gen)
        active, work = s.count('active'), s.count('workspace_large')
        if active + 2 > 64 or work + plan_length > 64 or (active >= 64*self.threshold or work >= 64*self.threshold):
            self.index.execute('UPDATE generations SET sealed=1 WHERE gen=?', (gen,))
            gen += 1
            self.index.execute('INSERT INTO generations(gen,sealed) VALUES (?,0)', (gen,))
            s = self._store(gen)
        # Do not register task before begin: the index is recovered from begin records.
        return Journal(s, self.principal)

    def locate(self, task):
        row = self.index.execute('SELECT gen FROM tasks WHERE task=?', (task,)).fetchone()
        return None if row is None else row[0]

    def register(self, task, journal):
        gen = next(g for g,s in self.stores.items() if s is journal.s)
        self.index.execute('INSERT OR IGNORE INTO tasks VALUES (?,?)', (task, gen))
        if self.locate(task) != gen: raise Rejected('TASK_GENERATION_CONFLICT')

    def close(self):
        for s in self.stores.values(): s.close()
        self.index.close()

class SuitAdapter:
    def __init__(self, decision: Callable[[str,str,str], bool]): self.decision = decision
    def authorize(self, task, sig, action):
        if self.decision(task, sig, action) is not True: raise Rejected('SUIT_POLICY_DENIED')

class RealCog02(Agent):
    def __init__(self, big, generations: Generations, env: Env, suit: SuitAdapter):
        super().__init__(big, generations._store(generations._latest()), env)
        self.generations, self.suit = generations, suit

    def solve(self, task, sig, hook=None):
        from ph6_brains_2208 import Block
        from ph6_realbridge import rid, pointer_for
        known = self.lower(sig)
        # An unknown task needs the maximum permitted candidate plan reservation.
        allowed = [c for c in CANDS if self.suit.decision(task, sig, c) is True]
        plan = [known] if known in allowed else allowed
        j = self.generations.for_task(task, len(plan))
        st = j.state(task)
        if st['status'] == 'DONE': return {'path':'DONE_ALREADY'}
        if st['status'] == 'NONE':
            j.begin(task, plan)
            self.generations.register(task, j)
            path = 'LOWER' if known in allowed else 'HIGHER'
        else:
            plan = st['plan']; path = 'LOWER' if len(plan) == 1 else 'HIGHER'
        n = st['done_steps']
        solved = next((o['a'] for o in st['outcomes'] if o['o']=='VALID'), None)
        while solved is None and n < len(plan):
            fix = plan[n]
            self.suit.authorize(task, sig, fix)
            if hook: hook('before_action', n)
            ok = self.env.try_fix(sig, fix)
            if hook: hook('after_action_before_record', n)
            j.record(task, n, fix, 'VALID' if ok else 'FAIL')
            n += 1
            if ok: solved = fix
        if solved and path == 'HIGHER' and self.lower(sig) is None:
            try: self.big.put(Block(rid('proc',sig), self.generations.principal, 'cognitive', 'fix='+solved, graph='G_S',source_refs=('int:'+task,)),principal=self.generations.principal)
            except Rejected as e:
                if str(e) != 'ID_EXISTS': raise
        j.done(task, n, 'OK' if solved else 'UNSOLVED')
        return {'path':path,'fix':solved,'steps':n}

def default_suit(): return SuitAdapter(lambda task,sig,fix: suit_ok(fix))
