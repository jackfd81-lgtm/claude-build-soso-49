import tempfile, unittest
from pathlib import Path
from suit5.core import SuitConfig, Candidate, Evidence
from suit5.wiring import TraceRecorder
from suit6 import Skill, SkillLibrary, TaskLedger, ProceduralSuit6

class Suit6Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.lib=SkillLibrary((Skill('coolant-check','MECHANIC','pressure-tester','diagnose-cooling',
            ('Inspect tool','Connect tester','Record reading'),('engine-cooled',)),))
        self.ledger=TaskLedger(self.root/'tasks.json')
        self.rec=TraceRecorder(self.root/'trace.jsonl')
        self.s=ProceduralSuit6(self.lib,self.ledger,self.rec)
    def test_known_procedure(self):
        s=self.s.begin('t','MECHANIC','diagnose-cooling','pressure-tester',prerequisites_met=('engine-cooled',))
        self.assertEqual(s['status'],'READY'); self.assertEqual(self.s.next_step('t')['step'],'Inspect tool')
    def test_unknown_tool_not_denied(self):
        self.assertEqual(self.s.begin('t','MECHANIC','diagnose-cooling','scalpel')['status'],'UNFAMILIAR_PROCEDURE')
    def test_permission_independent(self):
        self.assertEqual(self.s.begin('t','MECHANIC','diagnose-cooling','pressure-tester',permitted=False)['status'],'PERMISSION_DENIED')
    def test_prerequisite_hold(self):
        self.assertEqual(self.s.begin('t','MECHANIC','diagnose-cooling','pressure-tester')['status'],'PREREQUISITE_HOLD')
    def test_restart_resume(self):
        self.s.begin('t','MECHANIC','diagnose-cooling','pressure-tester',prerequisites_met=('engine-cooled',))
        self.s.acknowledge('t',observed_completion=True,evidence_ref='observation:1')
        new=ProceduralSuit6(self.lib,TaskLedger(self.root/'tasks.json'),TraceRecorder(self.root/'trace.jsonl'))
        self.assertEqual(new.resume('t')['step'],1)
        self.assertEqual(new.next_step('t')['step'],'Connect tester')
    def test_completion_requires_evidence(self):
        self.s.begin('t','MECHANIC','diagnose-cooling','pressure-tester',prerequisites_met=('engine-cooled',))
        with self.assertRaises(ValueError):self.s.acknowledge('t',observed_completion=True,evidence_ref='')
    def test_complete(self):
        self.s.begin('t','MECHANIC','diagnose-cooling','pressure-tester',prerequisites_met=('engine-cooled',))
        for i in range(3): self.s.acknowledge('t',observed_completion=True,evidence_ref=f'obs:{i}')
        self.assertEqual(self.s.resume('t')['status'],'COMPLETE')
    def test_mask_off(self):
        s=ProceduralSuit6(self.lib,self.ledger,self.rec,SuitConfig(mask_enabled=False))
        self.assertEqual(s.reason('L01',{})['function_calls'],0)
    def test_no_fake_brain(self):self.assertEqual(self.s.reason('U09',{})['status'],'BRAIN_NOT_CONNECTED')
    def test_suit_off_not_familiar(self):
        s=ProceduralSuit6(self.lib,self.ledger,self.rec,SuitConfig(suit_enabled=False))
        self.assertEqual(s.begin('t','MECHANIC','diagnose-cooling','pressure-tester')['status'],'UNFAMILIAR_PROCEDURE')
    def test_authority(self):
        state=self.s.begin('t','MECHANIC','diagnose-cooling','pressure-tester')
        self.assertEqual(state['authority'],'AUTH:0')
    def test_governance(self):
        e=Evidence.from_bytes('source',b'abc')
        result=self.s.govern_candidate((e,),Candidate('LOG_ADVISORY'))
        self.assertTrue(result['admitted'])
    def test_revision_conflict(self):
        self.s.begin('t','MECHANIC','diagnose-cooling','pressure-tester')
        with self.assertRaises(ValueError):self.ledger.put('t',{},expected_revision=0)
    def test_duplicate_task(self):
        self.s.begin('t','MECHANIC','diagnose-cooling','pressure-tester')
        with self.assertRaises(ValueError):self.s.begin('t','MECHANIC','diagnose-cooling','pressure-tester')
if __name__=='__main__': unittest.main()
