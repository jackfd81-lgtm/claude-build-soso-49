"""SoSo Suit 6.0: procedural competence and task continuity atop Suit 5 candidate.

Suit 4.0 A-E is the architectural reference, not an imported authentic engine.
The Suit proposes practiced steps; it never asserts professional certification or
performs physical actions. 49-Brain reasoning remains a separate injected port.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable
import hashlib, json, os, tempfile, threading
from suit5.core import SuitRuntime, SuitConfig, Candidate, Evidence, canonical
from suit5.wiring import CognitiveWiring, TraceRecorder


def atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.suit6-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(canonical(obj)); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


@dataclass(frozen=True)
class Skill:
    skill_id: str
    profession: str
    tool: str
    task: str
    steps: tuple[str, ...]
    prerequisites: tuple[str, ...] = ()
    # Distinguishes a practiced workflow from merely recognizing a tool.
    qualification: str = 'DEMONSTRATION_ONLY'
    def __post_init__(self):
        if not all((self.skill_id, self.profession, self.tool, self.task)) or not self.steps:
            raise ValueError('INVALID_SKILL')
        if any(not isinstance(s, str) or not s for s in self.steps):
            raise ValueError('INVALID_SKILL_STEPS')


class SkillLibrary:
    def __init__(self, skills: tuple[Skill, ...]):
        self.skills = {}
        for skill in skills:
            if skill.skill_id in self.skills: raise ValueError('DUPLICATE_SKILL')
            self.skills[skill.skill_id] = skill
    def match(self, profession: str, task: str, tool: str) -> Skill | None:
        matches = [s for s in self.skills.values() if (s.profession, s.task, s.tool) == (profession, task, tool)]
        if len(matches) > 1: raise ValueError('AMBIGUOUS_SKILL')
        return matches[0] if matches else None


class TaskLedger:
    """Atomic single-process task checkpointing; no cross-process concurrency claim."""
    def __init__(self, path: Path):
        self.path = Path(path); self.lock = threading.RLock()
        if not self.path.exists(): atomic_json(self.path, {'tasks': {}})
    def _load(self): return json.loads(self.path.read_text('utf-8'))
    def get(self, task_id: str) -> dict | None:
        with self.lock: return self._load()['tasks'].get(task_id)
    def put(self, task_id: str, state: dict, *, expected_revision: int | None):
        import fcntl
        with self.lock, open(str(self.path) + '.lock', 'a+') as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)         # SUIT61: compare-and-swap is atomic across processes too
            db = self._load(); old = db['tasks'].get(task_id)
            actual = old['revision'] if old else None
            if actual != expected_revision: raise ValueError('TASK_REVISION_CONFLICT')
            new = dict(state, revision=(actual or 0) + 1)
            db['tasks'][task_id] = new; atomic_json(self.path, db)
            return new


class ProceduralSuit6:
    """Practice selector + resumable workflow, with optional 49-Brain and Suit 5 ports.

    Unknown tools are not forbidden. Permission and competence are independent.
    """
    def __init__(self, library: SkillLibrary, ledger: TaskLedger, recorder: TraceRecorder,
                 config: SuitConfig = SuitConfig(), wiring: CognitiveWiring | None = None):
        self.library, self.ledger, self.recorder = library, ledger, recorder
        self.config, self.wiring = config, wiring
        self._lock = threading.RLock()

    def begin(self, task_id: str, profession: str, task: str, tool: str,
              *, permitted: bool = True, prerequisites_met: tuple[str, ...] = ()) -> dict:
        with self._lock:
            if self.ledger.get(task_id) is not None: raise ValueError('TASK_EXISTS')
            skill = self.library.match(profession, task, tool) if self.config.suit_enabled else None
            missing = [p for p in (skill.prerequisites if skill else ()) if p not in prerequisites_met]
            if not permitted: status = 'PERMISSION_DENIED'
            elif missing: status = 'PREREQUISITE_HOLD'
            elif skill is None: status = 'UNFAMILIAR_PROCEDURE'
            else: status = 'READY'
            state = {'task_id':task_id,'profession':profession,'task':task,'tool':tool,
                     'skill_id':skill.skill_id if skill else None,'step':0,'status':status,
                     'config_version':self.config.config_version,'missing':missing,
                     'authority':'AUTH:0','execution_authorization':'NOT_GRANTED'}
            state = self.ledger.put(task_id,state,expected_revision=None)
            self.recorder.record('SUIT6_TASK_BEGIN',state)
            return state

    def next_step(self, task_id: str) -> dict:
        with self._lock:
            state = self.ledger.get(task_id)
            if state is None: raise KeyError('UNKNOWN_TASK')
            if state['status'] not in ('READY','ACTIVE'): return {'status':state['status'],'step':None}
            skill = self.library.skills[state['skill_id']]
            if state['step'] >= len(skill.steps): return {'status':'COMPLETE','step':None}
            # This returns a suggestion; it does not operate a tool.
            response = {'status':'STEP_PROPOSED','step':skill.steps[state['step']],
                        'index':state['step'],'skill_id':skill.skill_id,
                        'qualification':skill.qualification,'execution_authorization':'NOT_GRANTED'}
            self.recorder.record('SUIT6_STEP_PROPOSED',{'task_id':task_id,**response})
            return response

    def acknowledge(self, task_id: str, *, observed_completion: bool, evidence_ref: str) -> dict:
        with self._lock:
            state = self.ledger.get(task_id)
            if state is None: raise KeyError('UNKNOWN_TASK')
            if state['status'] not in ('READY','ACTIVE'): raise ValueError('TASK_NOT_ACTIVE')
            if not observed_completion or not evidence_ref: raise ValueError('COMPLETION_EVIDENCE_REQUIRED')
            skill = self.library.skills[state['skill_id']]
            state['step'] += 1
            state['status'] = 'COMPLETE' if state['step'] == len(skill.steps) else 'ACTIVE'
            updated = self.ledger.put(task_id,state,expected_revision=state['revision'])
            self.recorder.record('SUIT6_STEP_ACK',{'task_id':task_id,'step':updated['step'],
                'evidence_ref':evidence_ref,'status':updated['status']})
            return updated

    def resume(self, task_id: str) -> dict:
        state = self.ledger.get(task_id)
        if state is None: raise KeyError('UNKNOWN_TASK')
        self.recorder.record('SUIT6_RESUME',{'task_id':task_id,'step':state['step']})
        return state

    def reason(self, function_id: str, payload: dict) -> dict:
        if not self.config.mask_enabled: return {'status':'MASK_DISABLED','function_calls':0}
        if self.wiring is None: return {'status':'BRAIN_NOT_CONNECTED','function_calls':0}
        try: return {'status':'OK','result':self.wiring.invoke(function_id,payload),'function_calls':1}
        except Exception as exc:
            self.recorder.record('SUIT6_BRAIN_FAILURE',{'function':function_id,'error':type(exc).__name__})
            return {'status':'BRAIN_FAILURE','error':type(exc).__name__,'function_calls':1}

    def govern_candidate(self, evidence: tuple[Evidence, ...], candidate: Candidate) -> dict:
        # The 5.0 candidate checks are a bounded subset of 4.0 A-E.
        try: return SuitRuntime(SuitConfig(suit_enabled=self.config.suit_enabled,
            mask_enabled=False,allowed_actions=self.config.allowed_actions,
            max_evidence_bytes=self.config.max_evidence_bytes,
            config_version=self.config.config_version)).run(evidence,candidate).as_dict()
        except Exception as exc:
            self.recorder.record('SUIT6_GOVERN_FAILURE',{'error':type(exc).__name__})
            return {'admitted':False,'reason':'GOVERNANCE_FAILURE','authority':'AUTH:0'}
