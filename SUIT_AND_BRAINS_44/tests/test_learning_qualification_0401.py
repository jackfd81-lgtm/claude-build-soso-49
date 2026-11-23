"""Reproducible deterministic 30-trial learning and memory-ablation qualification."""
import tempfile
import unittest
from pathlib import Path
from build040_runtime import SoSoContinuity, SoSoSuitRuntime
from build040_runtime.learning_loop import AssistedLearningLoop
from crampu.build030_bridge import Merged49BrainsCramAdapter
from crampu.controller import CRAMPUMemoryController

class LearningQualification0401(unittest.TestCase):
    def test_thirty_unseen_trials_memory_ablation_and_negative_transfer(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            admin='qualification-admin-0401'
            controller=CRAMPUMemoryController(root/'warm',root/'cool',admin)
            controller.register_block_owner(0,'L01',admin)
            adapter=Merged49BrainsCramAdapter(controller,root/'brains')
            continuity=SoSoContinuity(root/'continuity.sqlite3')
            suit=SoSoSuitRuntime(adapter)
            suit.authorize_agent('L01',{'memory.read','memory.write'},allowed_block_ids={0})
            loop=AssistedLearningLoop(continuity,suit)
            history=[{'before':3,'after':3,'profile':[1,1,1]}, {'before':3,'after':1,'profile':[1,2]}]
            seed={'H1':{'a_noise':0,'z_signal':0},'H2':{'a_noise':1,'z_signal':1},'H3':{'a_noise':2,'z_signal':1}}
            learned=loop.assist_and_learn('qual-train','L01',seed,{},history)
            self.assertEqual(learned['learned_profile'],[1,2])
            counts={'baseline_optimal':0,'memory_optimal':0,'ablated_optimal':0,'negative_transfer_rejected':0}
            for i in range(30):
                # New entity and feature names, permuted values and class membership.
                noise=f'a_noise_{i:03d}'
                signal=f'z_signal_{i:03d}'
                isolated=i%3
                candidates={f'Entity_{i}_{j}':{noise:f'unique_{i}_{j}',signal:('isolate' if j==isolated else 'group')} for j in range(3)}
                base=loop.run_unassisted(f'base-{i}','L01',candidates,{})
                mem=loop.run_unassisted(f'mem-{i}','L01',candidates,{},memory_block=('BIG49',0))
                ablated=loop.run_unassisted(f'ablate-{i}','L01',candidates,{})
                counts['baseline_optimal']+=base['selected_feature']==signal
                counts['memory_optimal']+=mem['selected_feature']==signal
                counts['ablated_optimal']+=ablated['selected_feature']==signal
                self.assertTrue(mem['transferred_from_memory'])
                self.assertFalse(ablated['transferred_from_memory'])
            for i in range(6):
                # Four candidates: [2,2] and [1,1,1,1], neither matches learned [1,2].
                candidates={f'Neg_{i}_{j}':{f'a_{i}':j,f'z_{i}':j%2} for j in range(4)}
                outcome=loop.run_unassisted(f'negative-{i}','L01',candidates,{},memory_block=('BIG49',0))
                counts['negative_transfer_rejected']+=not outcome['transferred_from_memory']
            self.assertEqual(counts,{'baseline_optimal':0,'memory_optimal':30,'ablated_optimal':0,'negative_transfer_rejected':6})
            adapter.close();controller.close();continuity.close()
