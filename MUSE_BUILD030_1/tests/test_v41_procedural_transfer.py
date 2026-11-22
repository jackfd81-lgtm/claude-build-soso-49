"""End-to-end 10-stage Cross-Specialty V4.1 + RealCog Procedural Transfer test across OS process restart."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract.context_envelope import ContextEnvelope
from contract.cross_specialty import run_consultation
from muse024 import call, initialize

STORE = {
    "med:trop_high": {"discipline": "medical", "key": "troponin_i", "value": "0.85 ng/mL"},
    "med:tox_neg": {"discipline": "medical", "key": "tox_screen", "value": "negative"},
    "med:tox_pos": {"discipline": "medical", "key": "tox_screen", "value": "positive_opioid"},
    "leg:bleed": {"discipline": "legal", "key": "bleed_event", "value": "gi_hemorrhage"},
}


class V41ProceduralTransferTests(unittest.TestCase):
    def test_swapped_conflict_pair_rejected(self):
        """Paired (source_id, proposition) check catches swapped pairings."""
        env = ContextEnvelope(
            investigation_id="swap-check", originating_question="Q", originating_specialty="Legal",
            current_specialty="Legal", consultation_purpose="consult_medical",
            evidence_store=STORE,
            evidence_refs=[
                {"source_id": "med:tox_neg", "verification_status": "VERIFIED", "key": "tox_screen", "value": "negative"},
                {"source_id": "med:tox_pos", "verification_status": "VERIFIED", "key": "tox_screen", "value": "positive_opioid"},
            ],
        )
        # Normal preserves exact pairs -> CONFLICT_PRESERVED
        ok_out = run_consultation(env, "Medical")
        self.assertEqual(ok_out.coherence_status, "CONFLICT_PRESERVED")
        # Tampered -> CONFLICT_PROVENANCE_LOST
        bad_out = run_consultation(env, "Medical", tamper_conflict_provenance=True)
        self.assertEqual(bad_out.coherence_status, "CONFLICT_PROVENANCE_LOST")

    def test_10_stage_procedural_transfer_across_restart(self):
        """Investigation -> L33/U32 -> SPEC -> L34/U33 -> Qualify -> Persist -> Restart -> Retrieve -> Reuse -> Revalidate."""
        workload = json.loads((ROOT / "eval025/workload.json").read_text())
        with tempfile.TemporaryDirectory(prefix="v42-xfer-") as td:
            state_dir = Path(td) / "state"
            tok = initialize(state_dir)
            qual_tok = call(state_dir, tok, {
                "authority": 0, "operation": "issue", "principal": "v42-qual",
                "scopes": ["procedure:qualify", "procedure:read", "big:write", "small:write"], "owners": ["L15"],
            })["token"]

            # Stage 1: Investigation on training case
            tr = workload["training"]
            out = call(state_dir, tok, {"authority": 0, "operation": "start025", "run": "train-01", "problem": tr["problem"], "arm": "A"})
            while out["decision"]["status"] == "HOLD":
                req = out["decision"]["request"]
                out = call(state_dir, tok, {"authority": 0, "operation": "resume025", "run": "train-01",
                    "measurement": {"source": "train", "feature": req["feature"], "request_id": req["id"], "value": tr["measurements"][req["feature"]]}})
            self.assertEqual(out["decision"]["status"], "SUPPORTED")

            # Stages 2-4: Cross-specialty consultation (L33 -> U32 -> SPEC -> L34 -> U33)
            env_tr = ContextEnvelope(
                investigation_id="train-consult", originating_question="Legal->Medical assessment",
                originating_specialty="Legal", current_specialty="Legal",
                consultation_purpose="consult_medical", evidence_store=STORE,
                evidence_refs=[{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}],
            )
            c_tr = run_consultation(env_tr, "Medical")
            self.assertEqual(c_tr.coherence_status, "COHERENT")

            # Stages 5-6: Qualify method & persist to Big49/Small49
            learned = call(state_dir, tok, {"authority": 0, "operation": "learn025", "run": "train-01"})
            qual = call(state_dir, qual_tok, {"authority": 0, "operation": "qualify025", "method": learned["decision"]["candidate_method"]})
            self.assertIn("qualification", qual)
            self.assertEqual(len(qual["memory"]), 2)

            # Stages 7-10: Restart in fresh OS subprocess, retrieve & reuse on heldout (memory-on vs memory-off), revalidate via L34/U33
            sub_code = """
import json, sys
from pathlib import Path
from muse024 import call
from contract.context_envelope import ContextEnvelope
from contract.cross_specialty import run_consultation

req = json.loads(sys.stdin.read())
root, tok, h = Path(req['root']), req['token'], req['heldout']

def run_arm(arm_code):
    out = call(root, tok, {'authority': 0, 'operation': 'start025', 'run': f'held-{arm_code}', 'problem': h['problem'], 'arm': arm_code})
    reqs, mem_reads = [], 0
    while out['decision']['status'] == 'HOLD':
        mem_reads += sum(1 for m in out.get('memory_accesses', []) if m.get('operation') == 'read')
        r = out['decision']['request']
        reqs.append(r['feature'])
        out = call(root, tok, {'authority': 0, 'operation': 'resume025', 'run': f'held-{arm_code}',
            'measurement': {'source': 'heldout', 'feature': r['feature'], 'request_id': r['id'], 'value': h['measurements'][r['feature']]}})
    return {'answer': out['decision'].get('answer'), 'requests': len(reqs), 'mem_reads': mem_reads}

arm_on = run_arm('A')
arm_off = run_arm('B')

# Stage 10: Revalidate cross-specialty output post-restart
env_h = ContextEnvelope(
    investigation_id='post-restart-consult', originating_question='Medical->Legal post-restart',
    originating_specialty='Medical', current_specialty='Medical',
    consultation_purpose='consult_legal', evidence_store=req['store'],
    evidence_refs=[{'source_id': 'leg:bleed', 'verification_status': 'VERIFIED', 'key': 'bleed_event', 'value': 'gi_hemorrhage'}],
)
reval = run_consultation(env_h, 'Legal')
print(json.dumps({'pid': __import__('os').getpid(), 'arm_on': arm_on, 'arm_off': arm_off, 'revalidated_status': reval.coherence_status}))
"""
            p = subprocess.run(
                [sys.executable, "-c", sub_code],
                input=json.dumps({"root": str(state_dir), "token": tok, "heldout": workload["heldout"][0], "store": STORE}),
                capture_output=True, text=True, check=True, env={**os.environ, "PYTHONPATH": str(ROOT)},
            )
            sub_res = json.loads(p.stdout)
            self.assertNotEqual(sub_res["pid"], os.getpid())
            self.assertEqual(sub_res["arm_on"]["requests"], 2)
            self.assertEqual(sub_res["arm_off"]["requests"], 3)
            self.assertGreater(sub_res["arm_on"]["mem_reads"], 0)
            self.assertEqual(sub_res["arm_off"]["mem_reads"], 0)
            self.assertEqual(sub_res["revalidated_status"], "COHERENT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
