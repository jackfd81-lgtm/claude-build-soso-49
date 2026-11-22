import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contract.cognitive_pathway_020 import run_cognitive_pathway

class Build020Tests(unittest.TestCase):
 def test_equation_and_transfer_restart(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   a=run_cognitive_pathway(root,{'task_type':'cognitive_020','task_id':'train','cognitive_domain':'mathematics','text':'2x+5=15'})
   self.assertEqual(a['answer'],5)
   self.assertEqual(a['method_learned'],'linear_equation_v1')
   script="import json,sys;from pathlib import Path;from contract.cognitive_pathway_020 import run_cognitive_pathway;print(json.dumps(run_cognitive_pathway(Path(sys.argv[1]),{'task_type':'cognitive_020','task_id':'transfer','cognitive_domain':'mathematics','text':'7x-9=40'})))"
   p=subprocess.run([sys.executable,'-c',script,d],capture_output=True,text=True,check=True)
   b=json.loads(p.stdout)
   self.assertEqual(b['answer'],7)
   self.assertEqual(b['method_retrieved'],'linear_equation_v1')
   self.assertEqual([x['function_id'] for x in b['actual_dispatcher_calls']],['L01','U09','U37'])
 def test_observation_separate_from_claim(self):
  with tempfile.TemporaryDirectory() as d:
   r=run_cognitive_pathway(Path(d),{'task_type':'cognitive_020','task_id':'caption','cognitive_domain':'number','text':'2+3','source_id':'video1','observations':[{'kind':'caption','text':'The light was green','time_ms':1500}]})
   self.assertEqual(r['answer'],5)
   self.assertEqual(r['observations'][0]['text'],'The light was green')
 def test_authority(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(PermissionError):
    run_cognitive_pathway(Path(d),{'task_type':'cognitive_020','task_id':'bad','cognitive_domain':'number','text':'1+1','authority':1})
   self.assertFalse(Path(d,'cognitive_020.sqlite3').exists())
 def test_invalid_observation_no_write(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):
    run_cognitive_pathway(Path(d),{'task_type':'cognitive_020','task_id':'bad','cognitive_domain':'number','text':'1+1','source_id':'v','observations':[{'kind':'caption','text':'x','time_ms':-1}]})
   self.assertFalse(Path(d).exists() and list(Path(d).iterdir()))

if __name__=='__main__':unittest.main()
