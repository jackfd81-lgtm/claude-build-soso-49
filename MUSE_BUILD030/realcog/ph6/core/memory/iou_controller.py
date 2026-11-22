"""iou-controller-0.1 (COMPATIBLE REPLACEMENT, not the original; original source not held).
Durable-before-memory IOU ledger controller. Lane 2 / AUTH:0 / non-canon. Experimental.
Order: validate -> per-IOU lock -> BEGIN IMMEDIATE -> persist full transition -> COMMIT (outcome verified
by read-back) -> publish to memory -> release. Ledger is the only authority; memory is a rebuildable cache.
No silent truncation: oversize payload is refused (CR-02 rule). Ambiguous commit -> INDETERMINATE."""
from __future__ import annotations
import contextlib, json, threading, unicodedata

MAX_PAYLOAD_CHARS = 512

class IOUAtomicityError(Exception): pass
class IOUCommitFailureError(IOUAtomicityError): pass

def _canon(payload) -> str:
    def chk(v):
        if v is None or type(v) in (bool, int): return v
        if isinstance(v, str): return unicodedata.normalize("NFC", v)
        if isinstance(v, (list, tuple)): return [chk(x) for x in v]
        if isinstance(v, dict):
            if any(not isinstance(k, str) for k in v): raise IOUAtomicityError("NON_STRING_KEY")
            result = {}
            for key, item in v.items():
                key = unicodedata.normalize("NFC", key)
                if key in result:
                    raise IOUAtomicityError("DUPLICATE_NORMALIZED_KEY")
                result[key] = chk(item)
            return result
        raise IOUAtomicityError("UNSUPPORTED_TYPE")                 # no floats in authoritative paths
    return json.dumps(chk(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

class IOUAtomicityController:
    def __init__(self, conn, faults=None):
        self.conn, self.faults = conn, dict(faults or {})
        self._dbl = threading.RLock(); self._guard = threading.Lock(); self._locks = {}
        self.state = {}                                              # memory cache; NEVER authoritative
        self.recover()

    def _fault(self, point):
        f = self.faults.get(point)
        if f: f()

    def _iou_lock(self, iou_id):
        with self._guard: return self._locks.setdefault(iou_id, threading.Lock())

    @contextlib.contextmanager
    def atomic_iou_transaction(self):
        with self._dbl:
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                yield cur
                self._fault("commit")
                self.conn.commit()
            except BaseException:
                try: self.conn.rollback()
                except Exception: pass
                raise

    def recover(self):
        """Rebuild memory from the ledger (restart / resync). Ledger wins."""
        with self._dbl:
            rows = self.conn.execute("SELECT iou_id,payload,status FROM durable_iou_ledger").fetchall()
        self.state = {r[0]: (r[2], r[1]) for r in rows}

    def _read(self, iou_id):
        return self.conn.execute("SELECT payload,status FROM durable_iou_ledger WHERE iou_id=?", (iou_id,)).fetchone()

    def record_iou_atomic(self, iou_id, payload) -> bool:
        if not isinstance(iou_id, str) or not iou_id: raise IOUAtomicityError("IOU_ID_INVALID")
        text = _canon(payload)
        if len(text) > MAX_PAYLOAD_CHARS: raise IOUAtomicityError("PAYLOAD_TOO_LARGE")   # refuse, never truncate
        self._fault("before_begin")
        with self._iou_lock(iou_id):
            with self._dbl:
                prior = self._read(iou_id)
                if prior is not None:
                    if prior[0] == text and prior[1] == "COMMITTED":
                        self.state[iou_id] = ("COMMITTED", text); return True        # idempotent retry, no new row
                    raise IOUAtomicityError("IOU_CONFLICT")
                try:
                    with self.atomic_iou_transaction() as cur:
                        self._fault("after_begin")
                        cur.execute("INSERT INTO durable_iou_ledger(iou_id,payload,timestamp,status) "
                                    "VALUES (?,?,CURRENT_TIMESTAMP,'COMMITTED')", (iou_id, text))
                        self._fault("after_write")
                except IOUAtomicityError: raise
                except Exception as exc:
                    # outcome check: is the row durable? ledger decides.
                    try: row = self._read(iou_id)
                    except Exception:
                        self.state[iou_id] = ("INDETERMINATE", None)
                        raise IOUCommitFailureError("INDETERMINATE") from exc
                    if row is None:
                        raise IOUCommitFailureError("NOT_COMMITTED") from exc
                    # Reconcile against the winning durable row, even when
                    # another process won the same primary key.
                    if row[0] != text or row[1] != "COMMITTED":
                        self.state[iou_id] = (row[1], row[0])
                        raise IOUAtomicityError("IOU_CONFLICT") from exc
                self._fault("after_commit")
                try:
                    self._fault("publish"); self.state[iou_id] = ("COMMITTED", text)
                except Exception:
                    self.recover()                                                  # ledger authoritative, never roll back
        return True
