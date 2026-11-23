"""Durable hash-chained event trace (sha256, local format; not project BLAKE2b canon). Lane 2 / AUTH:0."""
import hashlib, json, sqlite3
def _c(o): return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
class TraceLog:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, task TEXT, step INTEGER, data TEXT NOT NULL, prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL)")
        self.verify()
    def append(self, kind, task=None, step=None, data=None):
        d = _c(data or {})
        self.db.execute("BEGIN IMMEDIATE")
        try:
            r = self.db.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            prev = r[0] if r else "GENESIS"
            h = "sha256:" + hashlib.sha256(_c([prev, kind, task, step, d]).encode()).hexdigest()
            cur = self.db.execute("INSERT INTO events(kind,task,step,data,prev_hash,event_hash) VALUES (?,?,?,?,?,?)", (kind, task, step, d, prev, h))
            self.db.execute("COMMIT"); return cur.lastrowid
        except BaseException:
            self.db.execute("ROLLBACK"); raise
    def verify(self):
        prev = "GENESIS"
        for kind, task, step, d, p, h in self.db.execute("SELECT kind,task,step,data,prev_hash,event_hash FROM events ORDER BY seq"):
            if p != prev or h != "sha256:" + hashlib.sha256(_c([prev, kind, task, step, d]).encode()).hexdigest():
                raise ValueError("TRACE_CHAIN_MISMATCH")
            prev = h
    def events(self, task=None, kinds=None):
        q, a = "SELECT seq,kind,task,step,data FROM events", []
        w = []
        if task is not None: w.append("task=?"); a.append(task)
        if kinds: w.append("kind IN (%s)" % ",".join("?" * len(kinds))); a += list(kinds)
        if w: q += " WHERE " + " AND ".join(w)
        return [(s, k, t, st, json.loads(d)) for s, k, t, st, d in self.db.execute(q + " ORDER BY seq", a)]
    def close(self): self.db.close()
