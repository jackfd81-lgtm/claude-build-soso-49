import pytest
from pathlib import Path
from suit5 import *

def e():return Evidence.from_bytes('sensor:1',b'raw observation')
def runtime(suit=True,mask=True,fn=None):
    return SuitRuntime(SuitConfig(suit_enabled=suit,mask_enabled=mask),fn)
def test_integrity_with_suit_off():
    bad=Evidence('x',b'fake',e().sha256)
    assert runtime(False,False).run((bad,)).reason=='EVIDENCE_UNRESOLVED'
def test_no_mask_invocation_when_off():
    count=[]
    r=runtime(True,False,lambda _:count.append(1))
    assert r.run((e(),)).admitted and count==[]
def test_mask_invoked_when_on():
    count=[]
    def mask(ev):count.append(1);return Candidate('LOG_ADVISORY')
    assert runtime(True,True,mask).run((e(),)).admitted and count==[1]
def test_advisory_guard():
    assert runtime(True,False).run((e(),),Candidate('REQUEST_DISCRIMINATOR')).reason=='NON_ADVISORY_ACTION'
def test_suit_off_optional_guard():
    assert runtime(False,False).run((e(),),Candidate('REQUEST_DISCRIMINATOR')).admitted
def test_unknown_action_always_denied():
    assert runtime(False,False).run((e(),),Candidate('execute_lane1')).reason=='UNREGISTERED_CODE'
def test_blocking_iou():
    c=Candidate('LOG_ADVISORY',ious=({'id':'x','code':'IOU_MISSING_DISCRIMINATOR','type':'READING','blocking':True},))
    assert runtime(False,False).run((e(),),c).reason=='BLOCKING_IOU_VIOLATION'
def test_no_escape_config():
    with pytest.raises(ValueError,match='NO_IOU_ESCAPE_ACTION'):SuitConfig(allowed_actions=('LOG_ADVISORY',))
def test_evidence_binding():
    c=Candidate('LOG_ADVISORY',observations=({'id':'a','evidence_sha256':'sha256:'+'0'*64,'code':'OBS_UNCERTAIN'},))
    assert runtime(True,False).run((e(),),c).reason=='EVIDENCE_UNRESOLVED'
def test_interpretation_basis():
    c=Candidate('LOG_ADVISORY',interpretations=({'text':'x','based_on':['missing'],'confidence':'LOW'},))
    assert runtime(True,False).run((e(),),c).reason=='DANGLING_REFERENCE'
def test_authority():
    v=runtime(False,False).run((e(),)).as_dict()
    assert v['authority']=='AUTH:0' and v['execution_authorization']=='NOT_GRANTED'
def test_config_version():
    r=runtime(False,False)
    with pytest.raises(ValueError):r.reconfigure(SuitConfig(config_version=1))
    r.reconfigure(SuitConfig(config_version=2));assert r.run((e(),)).config_version==2
def test_canonical():
    assert digest({'b':2,'a':1})==digest({'a':1,'b':2})
    with pytest.raises(ValueError):canonical({'x':0.1})
def test_iou_restart(tmp_path):
    path=tmp_path/'ious.json';reg=IOURegistry(path)
    reg.create('i1','IOU_SOURCE_UNAVAILABLE','READING','agent','request source')
    assert len(IOURegistry(path).pending())==1
    with pytest.raises(PermissionError):reg.transition('i1','IN_PROGRESS','intruder')
    reg.transition('i1','IN_PROGRESS','agent');reg.transition('i1','RESOLVED','agent')
    assert IOURegistry(path).pending()==[]
def test_iou_terminal(tmp_path):
    reg=IOURegistry(tmp_path/'x');reg.create('x','IOU_AMBIGUOUS_READING','QUESTION','a','ask')
    reg.transition('x','ABANDONED','a')
    with pytest.raises(ValueError):reg.transition('x','IN_PROGRESS','a')
def test_resource_limit():
    r=SuitRuntime(SuitConfig(mask_enabled=False,max_evidence_bytes=1))
    assert r.run((e(),)).reason=='RESOURCE_EXCEEDED'
