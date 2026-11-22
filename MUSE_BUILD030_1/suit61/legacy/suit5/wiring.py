"""Host-injected wiring ports for Suit 5.0. No claim of authentic 4.0 or Build025 integration."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
import hashlib, json, os, threading
from .core import Candidate, Evidence, SuitRuntime, SuitConfig, canonical

class MemoryPort(Protocol):
    def read(self, block: int) -> bytes | None: ...
    def write(self, block: int, data: bytes) -> None: ...

class BrainPort(Protocol):
    def invoke(self, function_id: str, payload: dict) -> dict: ...

@dataclass(frozen=True)
class MemoryLimits:
    big49: int = 1600
    small49: int = 608

class TraceRecorder:
    """Append-only chained JSONL with fsync.

    SUIT61 FIXES: (2) an incomplete final line (torn write) is moved to a forensic sidecar and the chain is
    truncated to its last valid event instead of blocking restart; corruption anywhere else still fails closed.
    (4) every append takes an exclusive cross-process lock and re-reads the chain tail under it, so several
    recorder instances or processes extend one chain rather than forking it.
    """
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._seq = 0
        self._previous = '0' * 64
        self.recovered_torn_bytes = 0
        with self._file_lock():
            self._refresh(recover=True)
    def _file_lock(self):
        import contextlib, fcntl
        @contextlib.contextmanager
        def lock():
            with self._lock:
                fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    yield
                finally:
                    os.close(fd)
        return lock()
    def _refresh(self, recover=False):
        self._seq, self._previous = 0, '0' * 64
        if not self.path.exists():
            return
        data = self.path.read_bytes()
        offset = 0
        for line in data.splitlines(keepends=True):
            try:
                item = json.loads(line)
                expected = hashlib.sha256(canonical({k: v for k, v in item.items() if k != 'hash'})).hexdigest()
                valid = (line.endswith(b'\n') and item['hash'] == expected and item['previous'] == self._previous
                         and item['seq'] == self._seq + 1)
            except (ValueError, KeyError, TypeError):
                valid = False
            if not valid:
                if recover and offset + len(line) == len(data) and not (line.endswith(b'\n') and self._is_whole_event(line)):
                    torn = data[offset:]
                    side = Path(str(self.path) + '.torn-%d' % (self._seq + 1))
                    side.write_bytes(torn)                         # forensic evidence is preserved, never dropped
                    with self.path.open('r+b') as f:
                        f.truncate(offset); f.flush(); os.fsync(f.fileno())
                    self.recovered_torn_bytes = len(torn)
                    return
                raise ValueError('TRACE_CHAIN_CORRUPT')
            self._previous, self._seq = item['hash'], item['seq']
            offset += len(line)
    @staticmethod
    def _is_whole_event(line):
        try:
            item = json.loads(line)
            return isinstance(item, dict) and 'hash' in item and 'seq' in item
        except ValueError:
            return False
    def record(self, kind: str, payload: dict) -> dict:
        with self._file_lock():
            self._refresh(recover=False)
            event = {'seq':self._seq + 1, 'previous':self._previous, 'kind':kind, 'payload':payload}
            event['hash'] = hashlib.sha256(canonical(event)).hexdigest()
            with self.path.open('ab') as f:
                f.write(canonical(event) + b'\n')
                f.flush(); os.fsync(f.fileno())
            self._seq, self._previous = event['seq'], event['hash']
            return event


class QualificationAuthority:
    """SUIT61 FIX 3: the trusted component that establishes qualification. It holds a secret that the calling
    code never receives; it issues receipts only for evidence it has itself checked via `checker`.
    A caller asserting qualified=True (the 5.0/6.0 API) is no longer representable."""
    def __init__(self, secret: bytes, checker):
        if not isinstance(secret, bytes) or len(secret) < 16 or not callable(checker):
            raise ValueError('INVALID_AUTHORITY')
        self._secret, self._checker = secret, checker
    def _mac(self, bank, index, data, evidence_id):
        import hmac
        msg = canonical({'bank':bank,'index':index,'sha256':hashlib.sha256(data).hexdigest(),'evidence':evidence_id})
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()
    def issue(self, bank: str, index: int, data: bytes, evidence_id: str):
        if not self._checker(bank, index, data, evidence_id):
            raise PermissionError('QUALIFICATION_EVIDENCE_NOT_ESTABLISHED')
        return QualificationReceipt(bank, index, hashlib.sha256(data).hexdigest(), evidence_id, self._mac(bank, index, data, evidence_id))
    def verify(self, receipt, bank, index, data):
        import hmac
        return (isinstance(receipt, QualificationReceipt) and receipt.bank == bank and receipt.index == index
                and receipt.sha256 == hashlib.sha256(data).hexdigest()
                and hmac.compare_digest(receipt.mac, self._mac(bank, index, data, receipt.evidence_id)))


@dataclass(frozen=True)
class QualificationReceipt:
    bank: str
    index: int
    sha256: str
    evidence_id: str
    mac: str


class CognitiveWiring:
    """Real adapter ports, not a substitute for a cognitive engine or qualified memory."""
    def __init__(self, lower: BrainPort, higher: BrainPort, big49: MemoryPort,
                 small49: MemoryPort, recorder: TraceRecorder, limits: MemoryLimits = MemoryLimits(),
                 authority: 'QualificationAuthority | None' = None):
        self.authority = authority
        self.lower, self.higher = lower, higher
        self.big49, self.small49 = big49, small49
        self.recorder, self.limits = recorder, limits
    def _memory(self, bank: str, index: int):
        if type(index) is not int or index < 0:
            raise ValueError('INVALID_MEMORY_POSITION')
        if bank == 'Big49' and index < self.limits.big49:
            return self.big49
        if bank == 'Small49' and index < self.limits.small49:
            return self.small49
        raise ValueError('INVALID_MEMORY_POSITION')
    def read(self, bank: str, index: int) -> bytes | None:
        port = self._memory(bank,index)
        try:
            data = port.read(index)
            self.recorder.record('MEMORY_READ', {'bank':bank,'position':index,'present':data is not None,
                'sha256':hashlib.sha256(data).hexdigest() if data is not None else None})
            return data
        except Exception as exc:
            self.recorder.record('MEMORY_ERROR',{'bank':bank,'position':index,'operation':'read','error':type(exc).__name__})
            raise
    def write(self, bank: str, index: int, data: bytes, *, receipt=None):
        if not isinstance(data,bytes): raise TypeError('MEMORY_DATA_MUST_BE_BYTES')
        if self.authority is None or not self.authority.verify(receipt, bank, index, data):
            self.recorder.record('MEMORY_WRITE_REJECTED',{'bank':bank,'position':index,'reason':'QUALIFICATION_REQUIRED'})
            raise PermissionError('QUALIFICATION_REQUIRED')
        port = self._memory(bank,index)
        try:
            port.write(index,data)
            self.recorder.record('MEMORY_WRITE',{'bank':bank,'position':index,'sha256':hashlib.sha256(data).hexdigest()})
        except Exception as exc:
            self.recorder.record('MEMORY_ERROR',{'bank':bank,'position':index,'operation':'write','error':type(exc).__name__})
            raise
    def invoke(self, function_id: str, payload: dict) -> dict:
        if not isinstance(function_id,str) or len(function_id)!=3 or function_id[0] not in 'LU' or not function_id[1:].isdigit() or not 1 <= int(function_id[1:]) <= 49:
            raise ValueError('INVALID_FUNCTION_ID')
        if not isinstance(payload,dict): raise TypeError('INVALID_PAYLOAD')
        canonical(payload)
        self.recorder.record('FUNCTION_REQUEST',{'function':function_id,'input_sha256':hashlib.sha256(canonical(payload)).hexdigest()})
        port = self.lower if function_id.startswith('L') else self.higher
        try:
            result = port.invoke(function_id,payload)
            if not isinstance(result,dict): raise TypeError('INVALID_FUNCTION_RESULT')
            canonical(result)
            self.recorder.record('FUNCTION_RESULT',{'function':function_id,'output_sha256':hashlib.sha256(canonical(result)).hexdigest()})
            return result
        except Exception as exc:
            self.recorder.record('FUNCTION_ERROR',{'function':function_id,'error':type(exc).__name__})
            raise
    def connect(self, config: SuitConfig, mask_callback: Callable[[tuple[Evidence,...], 'CognitiveWiring'],Candidate]):
        def guarded_mask(evidence:tuple[Evidence,...]):
            self.recorder.record('MASK_START',{'config_version':config.config_version,'evidence_sha256':[e.sha256 for e in evidence]})
            return mask_callback(evidence,self)
        return WiredSuit(SuitRuntime(config,guarded_mask),self.recorder)

class WiredSuit:
    def __init__(self, runtime:SuitRuntime, recorder:TraceRecorder):
        self.runtime, self.recorder = runtime, recorder
    def run(self, evidence:tuple[Evidence,...], candidate:Candidate|None=None):
        self.recorder.record('RUN_START',{'suit_enabled':self.runtime.config.suit_enabled,
            'mask_enabled':self.runtime.config.mask_enabled,'config_version':self.runtime.config.config_version})
        try:
            result=self.runtime.run(evidence,candidate)
            self.recorder.record('SUIT_VERDICT',result.as_dict())
            return result
        except Exception as exc:
            self.recorder.record('RUN_ERROR',{'error':type(exc).__name__})
            raise
