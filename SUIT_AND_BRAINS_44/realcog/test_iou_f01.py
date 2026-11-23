import os, signal, sqlite3, subprocess, sys, tempfile, threading, unittest
from pathlib import Path
from ph6.core.memory.iou_controller import *
HERE = Path(__file__).resolve().parent
DDL = "CREATE TABLE IF NOT EXISTS durable_iou_ledger(iou_id TEXT PRIMARY KEY,payload TEXT NOT NULL,timestamp DATETIME NOT NULL,status TEXT NOT NULL)"
def mkdb(path):
    c = sqlite3.connect(path, check_same_thread=False); c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA synchronous=FULL"); c.execute(DDL); c.commit(); return c
def boom(): raise RuntimeError("injected")
class F01(unittest.TestCase):
    def setUp(self): self.p = os.path.join(tempfile.mkdtemp(), "iou.db"); self.c = mkdb(self.p)
    def rows(self): return self.c.execute("SELECT COUNT(*) FROM durable_iou_ledger").fetchone()[0]
    def ctl(self, **f): return IOUAtomicityController(self.c, f)
    def test01_fail_before_txn(self):
        k = self.ctl(before_begin=boom)
        with self.assertRaises(RuntimeError): k.record_iou_atomic("a", {"x": 1})
        self.assertEqual((self.rows(), k.state), (0, {}))
    def test02_fail_after_begin(self):
        k = self.ctl(after_begin=boom)
        with self.assertRaises(IOUCommitFailureError): k.record_iou_atomic("a", {"x": 1})
        self.assertEqual((self.rows(), k.state), (0, {}))
    def test03_fail_after_write_before_commit(self):
        k = self.ctl(after_write=boom)
        with self.assertRaises(IOUCommitFailureError): k.record_iou_atomic("a", {"x": 1})
        self.assertEqual((self.rows(), k.state), (0, {}))
    def test04_commit_error_not_published(self):
        k = self.ctl(commit=boom)
        with self.assertRaises(IOUCommitFailureError): k.record_iou_atomic("a", {"x": 1})
        self.assertEqual((self.rows(), k.state), (0, {}))
    def test05_restart_reconstructs(self):
        self.ctl().record_iou_atomic("a", {"x": 1}); self.c.close(); self.c = mkdb(self.p)
        self.assertEqual(self.ctl().state["a"][0], "COMMITTED")
    def test06_publish_failure_never_rolls_back(self):
        k = self.ctl(publish=boom); self.assertTrue(k.record_iou_atomic("a", {"x": 1}))
        self.assertEqual((self.rows(), k.state["a"][0]), (1, "COMMITTED"))
    def test07_same_iou_race(self):
        k = self.ctl(); res = []
        def w(p):
            try: k.record_iou_atomic("same", p); res.append("ok")
            except IOUAtomicityError as e: res.append(str(e))
        ts = [threading.Thread(target=w, args=({"v": i % 2},)) for i in range(6)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(self.rows(), 1); self.assertGreaterEqual(res.count("ok"), 1); self.assertTrue(set(res) <= {"ok", "IOU_CONFLICT"})
    def test08_idempotent_retry(self):
        k = self.ctl(); k.record_iou_atomic("a", {"x": 1}); self.assertTrue(k.record_iou_atomic("a", {"x": 1})); self.assertEqual(self.rows(), 1)
        with self.assertRaises(IOUAtomicityError): k.record_iou_atomic("a", {"x": 2})
    def test09_oversize_refused_not_truncated(self):
        with self.assertRaises(IOUAtomicityError) as c: self.ctl().record_iou_atomic("a", {"x": "y" * 600})
        self.assertEqual((str(c.exception), self.rows()), ("PAYLOAD_TOO_LARGE", 0))
    def test09b_floats_refused(self):
        with self.assertRaises(IOUAtomicityError): self.ctl().record_iou_atomic("a", {"x": 1.5})
    def test11_sigkill_after_commit_before_publish(self):
        code = ("import sys,os,signal,sqlite3;sys.path.insert(0,%r)\n"
                "from ph6.core.memory.iou_controller import *\n"
                "c=sqlite3.connect(%r,check_same_thread=False);c.execute('PRAGMA synchronous=FULL')\n"
                "k=IOUAtomicityController(c,{'after_commit':lambda:os.kill(os.getpid(),signal.SIGKILL)})\n"
                "k.record_iou_atomic('crash1',{'n':1})\n") % (str(HERE), self.p)
        r = subprocess.run([sys.executable, "-c", code]); self.assertEqual(r.returncode, -signal.SIGKILL)
        c2 = mkdb(self.p); k2 = IOUAtomicityController(c2)
        self.assertEqual(k2.state["crash1"][0], "COMMITTED"); self.assertTrue(k2.record_iou_atomic("crash1", {"n": 1}))
        self.assertEqual(c2.execute("SELECT COUNT(*) FROM durable_iou_ledger").fetchone()[0], 1)
if __name__ == "__main__": unittest.main()
