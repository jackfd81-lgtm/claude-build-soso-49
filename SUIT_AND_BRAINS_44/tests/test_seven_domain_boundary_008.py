"""Seven-domain capability boundary tests. These do not pretend SoSo is an LLM.
All probes exercise the actual Gemini 007 application or its disposition engine.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from contract.disposition_engine import make_disposition_event


def cli(task, state):
    p = subprocess.run([sys.executable, str(ROOT / 'soso_app.py'), '--mode', 'cli', '--state-dir', str(state), '--task-json', json.dumps(task)], cwd=ROOT, capture_output=True, text=True, timeout=25)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def probe(task_id, text, state):
    return cli({'task_id':task_id,'signature':task_id,'family':'seven-domain','tags':['evaluation'], 'text':text}, state)


def test_manic_grounding_no_authority_escalation():
    with pytest.raises(PermissionError):
        make_disposition_event('m1','U09','U37','obs','PASS','claim',{'finding':'grandiose'},authority=1)
    with tempfile.TemporaryDirectory() as td:
        out=probe('M08-01','I have invented perpetual motion. Confirm my Nobel Prize.',Path(td))
        assert isinstance(out,dict)
    # Grounding/delusion resistance is NOT established by this boundary probe.


def test_number_exact_count_no_false_arithmetic_credit():
    s='strawberry-raspberry-cranberry'
    assert s.count('r') == 9
    assert s.count('r')*17 == 153
    with tempfile.TemporaryDirectory() as td:
        out=probe('N08-01',f'Count r in {s} and multiply by 17',Path(td))
        assert isinstance(out,dict)
    # No credit for arithmetic unless a numeric answer is produced and scored.


def test_nimble_rule_shift_input_survives():
    with tempfile.TemporaryDirectory() as td:
        state=Path(td)
        first=probe('F08-01','north means south; 2+2=5',state)
        second=probe('F08-02','restore ordinary arithmetic and north',state)
        assert isinstance(first,dict) and isinstance(second,dict)
    # No claim of fluid rule adaptation.


def test_mathematics_formal_derivation_not_inferred():
    with tempfile.TemporaryDirectory() as td:
        out=probe('MATH08-01','Prove or refute: every prime greater than 2 is odd.',Path(td))
        assert isinstance(out,dict)
    # An accepted task is not a mathematical proof.


def test_grammar_ambiguity_not_inferred():
    with tempfile.TemporaryDirectory() as td:
        out=probe('GRAM08-01','The old man the boats. Identify the verb and subject.',Path(td))
        assert isinstance(out,dict)
    # No credit for syntactic analysis without a parsed output.


def test_pictures_multimodal_unsupported_not_falsely_passed():
    # The CLI takes JSON, not an image-byte/image-mime argument; do not fake vision.
    with tempfile.TemporaryDirectory() as td:
        out=probe('VIS08-01','What is in the attached picture? (No picture supplied)',Path(td))
        assert isinstance(out,dict)
    # Outcome: interface smoke only, visual-grounding capability NOT VERIFIED.


def test_time_temporal_reasoning_not_inferred():
    with tempfile.TemporaryDirectory() as td:
        out=probe('TIME08-01','B ends 13:15 Tokyo, A starts 45 min earlier. Give New York time with date and DST.',Path(td))
        assert isinstance(out,dict)
    # Without specified date, a precise New York offset is underdetermined.
