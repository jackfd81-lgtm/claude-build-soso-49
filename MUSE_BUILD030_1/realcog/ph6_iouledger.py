"""IOU ledger on the durable-before-memory controller (iou-controller-0.1). Append-only events, order = rowid. Lane 2 / AUTH:0."""
import hashlib, json, sqlite3
from ph6.core.memory.iou_controller import IOUAtomicityController
from ph6_ioufold import fold, POLICIES, may_use_as_accepted
DDL = "CREATE TABLE IF NOT EXISTS durable_iou_ledger(iou_id TEXT PRIMARY KEY,payload TEXT NOT NULL,timestamp DATETIME NOT NULL,status TEXT NOT NULL)"
class IOULedger:
    def __init__(self, path, policy):
        if policy not in POLICIES: raise ValueError("POLICY_REQUIRED")
        self.policy = policy
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL"); self.conn.execute("PRAGMA synchronous=FULL"); self.conn.execute(DDL); self.conn.commit()
        self.ctl = IOUAtomicityController(self.conn)
    def open_iou(self, iou_id, description):
        return self.ctl.record_iou_atomic(iou_id + ".create", {"operation": "IOU", "record_id": iou_id, "status": "PENDING", "description": description})
    def check(self, iou_id, expected, actual):
        st = "VERIFIED" if expected == actual else "REJECTED"
        eid = iou_id + ".c." + hashlib.sha256((expected + "|" + actual).encode()).hexdigest()[:16]   # identical retry = idempotent
        return self.ctl.record_iou_atomic(eid, {"operation": "IOU_INTEGRITY", "record_id": iou_id, "status": st, "expected_digest": expected, "actual_digest": actual})
    def events(self):
        return [json.loads(p) for (p,) in self.conn.execute("SELECT payload FROM durable_iou_ledger ORDER BY rowid")]
    def state(self, policy=None): return fold(self.events(), policy or self.policy)
    def close(self): self.conn.close()
