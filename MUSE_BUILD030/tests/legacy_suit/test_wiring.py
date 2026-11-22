from pathlib import Path
import pytest
from suit5.core import SuitConfig, Candidate, Evidence
from suit5.wiring import CognitiveWiring, TraceRecorder, QualificationAuthority

class FakeBrain:
    def __init__(self): self.calls=[]
    def invoke(self, function_id,payload):
        self.calls.append(function_id)
        return {'status':'HOLD','function':function_id}
class FakeMemory:
    def __init__(self): self.blocks={}
    def read(self,index): return self.blocks.get(index)
    def write(self,index,data): self.blocks[index]=data

def setup(tmp_path):
    l,h=FakeBrain(),FakeBrain(); b,s=FakeMemory(),FakeMemory()
    r=TraceRecorder(tmp_path/'trace.jsonl')
    return CognitiveWiring(l,h,b,s,r,authority=QualificationAuthority(b'k'*32,lambda *a:True)),l,h,b,s,r

def test_ports_and_trace(tmp_path):
    w,l,h,b,s,r=setup(tmp_path)
    assert w.invoke('L01',{})['status']=='HOLD'
    assert w.invoke('U09',{})['status']=='HOLD'
    assert l.calls==['L01'] and h.calls==['U09']
    with pytest.raises(PermissionError):w.write('Big49',0,b'data')
    w.write('Big49',0,b'data',receipt=w.authority.issue('Big49',0,b'data','E1'))   # SUIT61: receipt API replaces caller-asserted qualified=True
    assert w.read('Big49',0)==b'data'
    assert w.read('Small49',607) is None
    with pytest.raises(ValueError):w.read('Small49',608)
    assert TraceRecorder(r.path)._seq==8

def test_four_modes(tmp_path):
    for suit in (False,True):
        for mask in (False,True):
            w,l,h,b,s,r=setup(tmp_path/f'{suit}-{mask}')
            def callback(evidence,wiring):
                wiring.invoke('L01',{})
                wiring.invoke('U09',{})
                return Candidate('NO_OP')
            host=w.connect(SuitConfig(suit_enabled=suit,mask_enabled=mask),callback)
            result=host.run((Evidence.from_bytes('camera',b'frame'),))
            assert result.admitted and result.mask_calls==int(mask)
            assert len(l.calls)==len(h.calls)==int(mask)
            assert TraceRecorder(r.path)._seq>=2

def test_corrupt_trace_detected(tmp_path):
    w,*rest=setup(tmp_path)
    w.invoke('L01',{})
    p=tmp_path/'trace.jsonl'
    p.write_bytes(p.read_bytes().replace(b'FUNCTION_RESULT',b'FUNCTION_TAMPER'))
    with pytest.raises(ValueError,match='TRACE_CHAIN_CORRUPT'):TraceRecorder(p)

def test_invalid_function_and_failed_brain(tmp_path):
    w,l,h,b,s,r=setup(tmp_path)
    with pytest.raises(ValueError):w.invoke('U50',{})
    with pytest.raises(ValueError):w.invoke('L00',{})
    with pytest.raises(ValueError):w.invoke('U01',{'bad':1.2})
