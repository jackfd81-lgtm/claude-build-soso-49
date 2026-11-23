"""Regression tests for the four defects named in the directive.

Each defect is (a) reproduced against the ORIGINAL uploaded 6.0 sources, executed in a clean subprocess
(historical evidence that the defect existed), and (b) shown fixed in the Suit 6.1 copies."""
import json, os, subprocess, sys, threading
from pathlib import Path
import pytest
from suit5.core import SuitConfig, Candidate, Evidence
from suit5.wiring import CognitiveWiring, TraceRecorder, QualificationAuthority

ROOT = Path(__file__).resolve().parents[2]
ORIG = ROOT / "source_suits" / "MUSE_SOSO_SUIT_6_0_PROCEDURAL_CANDIDATE_2" / "MUSE_SOSO_SUIT_6_0"
LEGACY = ROOT / "suit61" / "legacy"


def run_original(code, *args):
    return subprocess.run([sys.executable, "-I", "-c", "import sys;sys.path.insert(0,%r)\n" % str(ORIG) + code, *map(str, args)],
                          capture_output=True, text=True)


class Brain:
    def invoke(self, f, p): return {"status": "OK"}


class Mem:
    def __init__(self): self.d = {}
    def read(self, i): return self.d.get(i)
    def write(self, i, v): self.d[i] = v


# ------------------------------------------------------------ defect 1: callback exceptions
def test_d1_original_callback_exception_escapes():
    r = run_original("from suit5.wiring import *;from suit5.core import *\nimport tempfile,pathlib\n"
                     "w=CognitiveWiring(None,None,None,None,TraceRecorder(pathlib.Path(tempfile.mkdtemp())/'t'))\n"
                     "def bad(ev,wr): raise RuntimeError('boom')\n"
                     "w.connect(SuitConfig(),bad).run((Evidence.from_bytes('x',b'1'),))")
    assert r.returncode != 0 and "RuntimeError" in r.stderr


def test_d1_fixed_callback_exception_becomes_structured_verdict(tmp_path):
    rec = TraceRecorder(tmp_path / "t.jsonl")
    w = CognitiveWiring(Brain(), Brain(), Mem(), Mem(), rec)
    def bad(ev, wr): raise RuntimeError("boom")
    v = w.connect(SuitConfig(), bad).run((Evidence.from_bytes("x", b"1"),))
    assert (v.admitted, v.reason, v.mask_calls, v.authority, v.execution_authorization) == (False, "MASK_FAILURE", 1, "AUTH:0", "NOT_GRANTED")
    assert TraceRecorder(tmp_path / "t.jsonl")._seq >= 3                      # failure is on the chain


# ------------------------------------------------------------ defect 2: torn trace
def torn(path):
    r = TraceRecorder(path)
    r.record("A", {"i": 1}); r.record("B", {"i": 2})
    with Path(path).open("ab") as f:
        f.write(b'{"seq":3,"previous":"ab')
    return r


def test_d2_original_torn_write_blocks_restart():
    r = run_original("from suit5.wiring import *\nimport tempfile,pathlib\np=pathlib.Path(tempfile.mkdtemp())/'t'\n"
                     "x=TraceRecorder(p);x.record('A',{});x.record('B',{})\n"
                     "open(p,'ab').write(b'{\"seq\":3,\"prev')\nTraceRecorder(p)")
    assert r.returncode != 0 and ("JSONDecodeError" in r.stderr or "TRACE_CHAIN_CORRUPT" in r.stderr)


def test_d2_fixed_torn_tail_recovered_and_preserved_as_evidence(tmp_path):
    p = tmp_path / "t.jsonl"
    torn(p)
    again = TraceRecorder(p)
    assert again._seq == 2 and again.recovered_torn_bytes > 0
    assert (tmp_path / "t.jsonl.torn-3").read_bytes().startswith(b'{"seq":3')       # forensic copy kept
    again.record("C", {"i": 3})
    assert TraceRecorder(p)._seq == 3


def test_d2_fixed_still_rejects_mid_chain_tamper(tmp_path):
    p = tmp_path / "t.jsonl"
    r = TraceRecorder(p); r.record("A", {}); r.record("B", {}); r.record("C", {})
    p.write_bytes(p.read_bytes().replace(b'"B"', b'"X"'))
    with pytest.raises(ValueError, match="TRACE_CHAIN_CORRUPT"):
        TraceRecorder(p)


# ------------------------------------------------------------ defect 3: caller-asserted qualification
def test_d3_original_accepts_caller_asserted_qualification():
    r = run_original("from suit5.wiring import *\nimport tempfile,pathlib\nclass M:\n d={}\n def read(s,i):return None\n def write(s,i,v):s.d[i]=v\n"
                     "m=M();w=CognitiveWiring(None,None,m,m,TraceRecorder(pathlib.Path(tempfile.mkdtemp())/'t'))\n"
                     "w.write('Big49',0,b'x',qualified=True);print(len(m.d))")
    assert r.returncode == 0 and r.stdout.strip() == "1"


def test_d3_fixed_caller_cannot_self_qualify(tmp_path):
    mem = Mem()
    auth = QualificationAuthority(b"s" * 32, lambda bank, i, data, ev: ev == "E-REAL")
    w = CognitiveWiring(Brain(), Brain(), mem, mem, TraceRecorder(tmp_path / "t"), authority=auth)
    with pytest.raises(TypeError):
        w.write("Big49", 0, b"x", qualified=True)                      # legacy assertion no longer representable
    with pytest.raises(PermissionError):
        w.write("Big49", 0, b"x")
    with pytest.raises(PermissionError):
        auth.issue("Big49", 0, b"x", "E-FAKE")                         # trusted component refuses unestablished evidence
    forged = type(auth.issue("Big49", 0, b"x", "E-REAL"))("Big49", 0, "00", "E-REAL", "f" * 64)
    with pytest.raises(PermissionError):
        w.write("Big49", 0, b"x", receipt=forged)
    receipt = auth.issue("Big49", 0, b"x", "E-REAL")
    with pytest.raises(PermissionError):
        w.write("Big49", 0, b"DIFFERENT", receipt=receipt)             # receipt is bound to the exact data
    with pytest.raises(PermissionError):
        w.write("Big49", 1, b"x", receipt=receipt)                     # ... and to the exact position
    w.write("Big49", 0, b"x", receipt=receipt)
    assert mem.d == {0: b"x"}


# ------------------------------------------------------------ defect 4: multiple recorders / processes
def test_d4_original_two_recorders_fork_the_chain():
    r = run_original("from suit5.wiring import *\nimport tempfile,pathlib\np=pathlib.Path(tempfile.mkdtemp())/'t'\n"
                     "a=TraceRecorder(p);b=TraceRecorder(p);a.record('A',{});b.record('B',{});a.record('C',{})\nTraceRecorder(p)")
    assert r.returncode != 0 and "TRACE_CHAIN_CORRUPT" in r.stderr


def test_d4_fixed_two_recorders_one_chain(tmp_path):
    p = tmp_path / "t.jsonl"
    a, b = TraceRecorder(p), TraceRecorder(p)
    a.record("A", {}); b.record("B", {}); a.record("C", {})
    assert TraceRecorder(p)._seq == 3


def test_d4_fixed_multiprocess_and_threads_keep_one_chain(tmp_path):
    p = tmp_path / "t.jsonl"
    code = ("import sys;sys.path.insert(0,%r)\nfrom suit5.wiring import TraceRecorder\nr=TraceRecorder(sys.argv[1])\n"
            "for i in range(15): r.record('P',{'i':i})" % str(LEGACY))
    procs = [subprocess.Popen([sys.executable, "-I", "-c", code, str(p)]) for _ in range(4)]
    shared = TraceRecorder(p)
    threads = [threading.Thread(target=lambda: [shared.record("T", {}) for _ in range(10)]) for _ in range(3)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert all(x.wait(timeout=60) == 0 for x in procs)
    assert TraceRecorder(p)._seq == 4 * 15 + 3 * 10


def test_d4_fixed_ledger_revision_cas_across_processes(tmp_path):
    from suit6 import TaskLedger
    led = TaskLedger(tmp_path / "tasks.json")
    led.put("t", {"x": 0}, expected_revision=None)
    code = ("import sys;sys.path.insert(0,%r)\nfrom suit6 import TaskLedger\nl=TaskLedger(sys.argv[1])\n"
            "try:\n l.put('t',{'x':1},expected_revision=1);print('won')\nexcept ValueError as e:print('lost')" % str(LEGACY))
    outs = [subprocess.Popen([sys.executable, "-I", "-c", code, str(tmp_path / "tasks.json")], stdout=subprocess.PIPE, text=True) for _ in range(4)]
    results = sorted(o.communicate()[0].strip() for o in outs)
    assert results == ["lost", "lost", "lost", "won"]
