"""Executable 5.0 candidate: 4.0 A-E concepts; NOT the authentic frozen 4.0 implementation.

No AI model, authentication provider, or actual 49-Brain implementation is bundled.
The supplied mask callback is the integration seam. Never expose this as a production security boundary.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any
import hashlib, json, os, tempfile, threading

SUIT_ACTIONS = frozenset(('LOG_ADVISORY','REQUEST_READING','REQUEST_DISCRIMINATOR','NO_OP'))
ADVISORY_ACTIONS = frozenset(('LOG_ADVISORY','REQUEST_READING','NO_OP'))
IOU_CODES = frozenset(('IOU_UNMAPPED_OBSERVATION','IOU_MISSING_DISCRIMINATOR','IOU_SOURCE_UNAVAILABLE','IOU_AMBIGUOUS_READING'))
IOU_TYPES = frozenset(('READING','QUESTION','REQUEST','PERMISSION','CONFIRMATION'))


def canonical(obj: Any) -> bytes:
    """Deterministic subset: NFC, no floats, sorted keys. Not a full RFC8785 implementation."""
    import unicodedata
    def normalize(x):
        if isinstance(x, float): raise ValueError('FLOAT_NOT_ALLOWED')
        if x is None or type(x) in (str,int,bool):
            return unicodedata.normalize('NFC',x) if isinstance(x,str) else x
        if isinstance(x,list): return [normalize(v) for v in x]
        if isinstance(x,dict):
            out={}
            for k,v in x.items():
                if not isinstance(k,str): raise ValueError('KEY_NOT_STRING')
                nk=unicodedata.normalize('NFC',k)
                if nk in out: raise ValueError('DUPLICATE_NORMALIZED_KEY')
                out[nk]=normalize(v)
            return out
        raise ValueError('UNSUPPORTED_TYPE')
    return json.dumps(normalize(obj),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def digest(obj:Any)->str:
    return 'sha256:'+hashlib.sha256(canonical(obj)).hexdigest()


@dataclass(frozen=True)
class Evidence:
    source: str
    content: bytes
    sha256: str
    @classmethod
    def from_bytes(cls,source:str,content:bytes)->'Evidence':
        return cls(source,content,'sha256:'+hashlib.sha256(content).hexdigest())
    def verify(self)->bool:
        return self.sha256 == 'sha256:'+hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class SuitConfig:
    suit_enabled: bool = True
    mask_enabled: bool = True
    allowed_actions: tuple[str,...] = ('LOG_ADVISORY','REQUEST_READING','REQUEST_DISCRIMINATOR','NO_OP')
    max_evidence_bytes: int = 1024*1024
    max_mask_calls: int = 1
    config_version: int = 1
    def __post_init__(self):
        if not self.allowed_actions or set(self.allowed_actions)-SUIT_ACTIONS:
            raise ValueError('INVALID_ACTIONS')
        if 'NO_OP' not in self.allowed_actions and 'REQUEST_DISCRIMINATOR' not in self.allowed_actions:
            raise ValueError('NO_IOU_ESCAPE_ACTION')
        if self.max_evidence_bytes < 1 or self.max_mask_calls < 0 or self.config_version < 1:
            raise ValueError('INVALID_LIMITS')


@dataclass(frozen=True)
class Candidate:
    action: str
    observations: tuple[dict,...] = ()
    interpretations: tuple[dict,...] = ()
    ious: tuple[dict,...] = ()
    def payload(self)->dict:
        return {'action':self.action,'observations':list(self.observations),
                'interpretations':list(self.interpretations),'ious':list(self.ious)}


@dataclass(frozen=True)
class Verdict:
    admitted: bool
    reason: str
    action: str
    candidate_digest: str
    evidence_digests: tuple[str,...]
    config_version: int
    suit_enabled: bool
    mask_enabled: bool
    mask_calls: int
    authority: str = 'AUTH:0'
    execution_authorization: str = 'NOT_GRANTED'
    def as_dict(self):
        d=self.__dict__.copy();d['evidence_digests']=list(self.evidence_digests)
        d['verdict_digest']=digest(d)
        return d


class IOURegistry:
    """Append-only, single-process persistent IOU ledger. No cross-process locking claim."""
    def __init__(self,path:Path):
        self.path=Path(path);self.lock=threading.RLock()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if not self.path.exists(): self._write({'items':{},'sequence':0})
    def _read(self): return json.loads(self.path.read_text('utf-8'))
    def _write(self,data):
        fd,name=tempfile.mkstemp(dir=self.path.parent,prefix='.iou-')
        try:
            with os.fdopen(fd,'wb') as f:
                f.write(canonical(data));f.flush();os.fsync(f.fileno())
            os.replace(name,self.path)
            dfd=os.open(self.path.parent,os.O_RDONLY)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        finally:
            if os.path.exists(name):os.unlink(name)
    def create(self,iou_id,code,kind,owner,resolution_path):
        if code not in IOU_CODES or kind not in IOU_TYPES or not resolution_path or not owner:
            raise ValueError('INVALID_IOU')
        with self.lock:
            data=self._read()
            if iou_id in data['items']:raise ValueError('DUPLICATE_IOU')
            data['sequence']+=1
            data['items'][iou_id]={'id':iou_id,'code':code,'type':kind,'owner':owner,
              'resolution_path':resolution_path,'status':'CREATED','history':[{'seq':data['sequence'],'status':'CREATED'}]}
            self._write(data)
    def transition(self,iou_id,status,actor):
        with self.lock:
            data=self._read();item=data['items'][iou_id];old=item['status']
            transitions={'CREATED':{'IN_PROGRESS','BLOCKED','ABANDONED'},
                'IN_PROGRESS':{'RESOLVED','BLOCKED','ABANDONED'},
                'BLOCKED':{'IN_PROGRESS','ABANDONED'},'RESOLVED':set(),'ABANDONED':set()}
            if status not in transitions[old]:raise ValueError('ILLEGAL_IOU_TRANSITION')
            if actor not in (item['owner'],'ADMIN'):raise PermissionError('ROLE_DENIED')
            data['sequence']+=1;item['status']=status
            item['history'].append({'seq':data['sequence'],'status':status,'actor':actor})
            self._write(data)
    def pending(self):
        with self.lock:
            return [v for v in self._read()['items'].values() if v['status'] not in ('RESOLVED','ABANDONED')]


class SuitRuntime:
    """4.0 A-E-inspired composition; mask callable supplied by host application.

    Mandatory checks apply with Suit OFF. Suit ON adds action policy and
    structural provenance checks. Callback output is untrusted in every mode.
    """
    def __init__(self,config:SuitConfig,mask:Callable[[tuple[Evidence,...]],Candidate]|None=None):
        self.config=config;self.mask=mask;self._lock=threading.RLock();self._inflight=0
    def reconfigure(self,config:SuitConfig):
        with self._lock:
            if self._inflight:raise RuntimeError('IN_FLIGHT_CONFIGURATION_CHANGE')
            if config.config_version<=self.config.config_version:raise ValueError('CONFIG_VERSION_NOT_ADVANCED')
            self.config=config
    def run(self,evidence:tuple[Evidence,...],candidate:Candidate|None=None)->Verdict:
        with self._lock:
            self._inflight+=1;config=self.config
        calls=0
        try:
            evhash=tuple(e.sha256 for e in evidence)
            def verdict(ok,reason,action='NO_OP',payload=None):
                return Verdict(ok,reason,action,digest(payload or {'action':action}),evhash,
                               config.config_version,config.suit_enabled,config.mask_enabled,calls)
            if not evidence or any(not e.verify() for e in evidence):return verdict(False,'EVIDENCE_UNRESOLVED')
            if sum(len(e.content) for e in evidence)>config.max_evidence_bytes:return verdict(False,'RESOURCE_EXCEEDED')
            if config.mask_enabled:
                if self.mask is None:return verdict(False,'MASK_UNAVAILABLE')
                if config.max_mask_calls<1:return verdict(False,'MASK_BUDGET_EXCEEDED')
                calls=1
                try:
                    candidate=self.mask(evidence)
                except Exception as exc:        # SUIT61 FIX 1: callback failure becomes a structured verdict
                    return verdict(False,'MASK_FAILURE',payload={'action':'NO_OP','error':type(exc).__name__})
            elif candidate is None:
                candidate=Candidate('NO_OP')
            if not isinstance(candidate,Candidate):return verdict(False,'SCHEMA_VIOLATION')
            p=candidate.payload();action=candidate.action
            try: canonical(p)
            except (ValueError,TypeError):return verdict(False,'SCHEMA_VIOLATION',action)
            if action not in SUIT_ACTIONS:return verdict(False,'UNREGISTERED_CODE',action,p)
            if action not in config.allowed_actions and config.suit_enabled:return verdict(False,'ACTION_NOT_ALLOWED',action,p)
            if action not in ADVISORY_ACTIONS and config.suit_enabled:return verdict(False,'NON_ADVISORY_ACTION',action,p)
            if len(candidate.observations)>64 or len(candidate.interpretations)>64 or len(candidate.ious)>64:
                return verdict(False,'RESOURCE_EXCEEDED',action,p)
            obs_ids=set()
            for obs in candidate.observations:
                if not isinstance(obs,dict) or set(obs)!={'id','evidence_sha256','code'}:
                    return verdict(False,'SCHEMA_VIOLATION',action,p)
                if not isinstance(obs['id'],str) or not obs['id'] or obs['id'] in obs_ids:
                    return verdict(False,'DUPLICATE_ID',action,p)
                obs_ids.add(obs['id'])
                if obs['evidence_sha256'] not in evhash:return verdict(False,'EVIDENCE_UNRESOLVED',action,p)
                if obs['code'] not in ('OBS_UNCERTAIN','OBS_HASH_MATCH','OBS_HASH_MISMATCH','OBS_SOURCE_MISMATCH','OBS_STREAM_NOMINAL','OBS_STREAM_INTERRUPTED'):
                    return verdict(False,'UNREGISTERED_CODE',action,p)
            for interp in candidate.interpretations:
                if not isinstance(interp,dict) or set(interp)!={'text','based_on','confidence'}:
                    return verdict(False,'SCHEMA_VIOLATION',action,p)
                if not interp['based_on'] or not set(interp['based_on']).issubset(obs_ids):
                    return verdict(False,'DANGLING_REFERENCE',action,p)
                if interp['confidence'] not in ('UNQUANTIFIED','LOW','MEDIUM','HIGH'):
                    return verdict(False,'UNREGISTERED_CODE',action,p)
                if not isinstance(interp['text'],str) or len(interp['text'])>4096:
                    return verdict(False,'RESOURCE_EXCEEDED',action,p)
            for iou in candidate.ious:
                if not isinstance(iou,dict) or set(iou)!={'id','code','type','blocking'}:
                    return verdict(False,'SCHEMA_VIOLATION',action,p)
                if iou['code'] not in IOU_CODES or iou['type'] not in IOU_TYPES or type(iou['blocking']) is not bool:
                    return verdict(False,'UNREGISTERED_CODE',action,p)
            if any(i['blocking'] for i in candidate.ious) and action not in ('REQUEST_DISCRIMINATOR','NO_OP'):
                return verdict(False,'BLOCKING_IOU_VIOLATION',action,p)
            return verdict(True,'ACCEPTED_AS_ADVISORY',action,p)
        finally:
            with self._lock:self._inflight-=1
