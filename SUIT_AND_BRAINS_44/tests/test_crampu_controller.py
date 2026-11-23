import json
import tempfile
import threading
import unittest
from pathlib import Path
from crampu.controller import CRAMPUMemoryController, AuthorizationError, ConflictError, IntegrityError, block_type
from crampu.adapters import SuitMemoryAdapter, Brain49MemoryAdapter

SECRET = 'local-admin-test-secret-0301'
class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.warm, self.cool = root/'warm',root/'cool'
        self.c = CRAMPUMemoryController(self.warm,self.cool,SECRET)
        self.c.register_block_owner(100,'alpha',SECRET)
    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()
    def test_boundaries(self):
        self.assertEqual(block_type(1599),'BIG49')
        self.assertEqual(block_type(1600),'SMALL49')
        self.assertEqual(block_type(2207),'SMALL49')
        for n in (-1,2208,1.5,True):
            with self.assertRaises(ValueError): block_type(n)
    def test_roundtrip_restart_and_acl(self):
        self.c.write_hot('alpha',100,{'token':'✓','value':1})
        tx = self.c.stage_to_warm('alpha',100)
        r = self.c.commit_to_cool('alpha',tx)
        self.assertEqual(r['revision'],1)
        self.c.close()
        self.c = CRAMPUMemoryController(self.warm,self.cool,SECRET)
        with self.assertRaises(AuthorizationError): self.c.load_from_cool(100,'intruder')
        self.assertEqual(self.c.load_from_cool(100,'alpha')['payload']['token'],'✓')
    def test_unowned_denied(self):
        with self.assertRaises(AuthorizationError): self.c.write_hot('intruder',101,{})
        with self.assertRaises(AuthorizationError): self.c.register_block_owner(101,'intruder','wrong')
    def test_snapshot_not_hot(self):
        self.c.write_hot('alpha',100,{'v':1})
        tx=self.c.stage_to_warm('alpha',100)
        self.c.write_hot('alpha',100,{'v':2})
        self.c.commit_to_cool('alpha',tx)
        self.assertEqual(self.c.load_from_cool(100,'alpha')['payload'],{'v':1})
    def test_revision_conflict(self):
        self.c.write_hot('alpha',100,{'v':1})
        a=self.c.stage_to_warm('alpha',100)
        b=self.c.stage_to_warm('alpha',100)
        self.c.commit_to_cool('alpha',a)
        with self.assertRaises(ConflictError): self.c.commit_to_cool('alpha',b)
    def test_tamper_staged(self):
        self.c.write_hot('alpha',100,{'v':1})
        tx=self.c.stage_to_warm('alpha',100)
        path=self.warm/(tx+'.json')
        data=json.loads(path.read_text()); data['payload']['v']=9; path.write_text(json.dumps(data))
        with self.assertRaises(IntegrityError): self.c.commit_to_cool('alpha',tx)
    def test_warm_restart_not_committed(self):
        self.c.write_hot('alpha',100,{'v':1})
        self.c.stage_to_warm('alpha',100)
        self.c.close(); self.c=CRAMPUMemoryController(self.warm,self.cool,SECRET)
        with self.assertRaises(KeyError): self.c.load_from_cool(100,'alpha')
    def test_suit_and_brain(self):
        self.c.register_block_owner(1600,'alpha',SECRET)
        allow=lambda a,op,b: a=='alpha' and op in ('memory.read','memory.write')
        adapter=Brain49MemoryAdapter(SuitMemoryAdapter(self.c,allow))
        adapter.write('alpha','SMALL49',0,{'concept':'test'})
        self.assertEqual(adapter.read('alpha','SMALL49',0)['payload']['concept'],'test')
        with self.assertRaises(AuthorizationError): adapter.read('rogue','SMALL49',0)
    def test_concurrent_staged_commits(self):
        self.c.write_hot('alpha',100,{'v':1})
        txs=[self.c.stage_to_warm('alpha',100) for _ in range(8)]
        outcomes=[]
        def run(tx):
            try: self.c.commit_to_cool('alpha',tx); outcomes.append('ok')
            except ConflictError: outcomes.append('conflict')
        threads=[threading.Thread(target=run,args=(t,)) for t in txs]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(outcomes.count('ok'),1)
        self.assertEqual(outcomes.count('conflict'),7)
    def test_non_authority_and_ledger(self):
        self.c.write_hot('alpha',100,{'x':1})
        self.c.commit_to_cool('alpha',self.c.stage_to_warm('alpha',100))
        record=self.c.load_from_cool(100,'alpha')
        self.assertEqual(record['authority_level'],'COGNITIVE_ADVISORY')
        self.assertEqual(len(self.c.transitions(SECRET)),1)

if __name__ == '__main__': unittest.main()
