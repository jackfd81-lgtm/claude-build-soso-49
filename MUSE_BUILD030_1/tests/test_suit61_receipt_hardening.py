"""Adversarial receipt binding regressions for Suit 6.1."""
import pytest
from eval061 import run as E
from muse024 import call

TASKS = {t['id']: t for t in E.TASKS}


def test_unfamiliar_receipt_cannot_be_used_to_propose(tmp_path):
    root, tokens = E.station(tmp_path)
    E.train(root, tokens, transfers=False)
    target = TASKS['U1']['target']
    got = call(root, tokens['agent'], {'operation': 'suit_retrieve', 'target': target})
    assert got['mode'] == 'UNFAMILIAR_REDISCOVERY_REQUIRED'
    with pytest.raises(ValueError, match='retrieval receipt'):
        call(root, tokens['agent'], {'operation': 'suit_propose',
             'skill_id': E.SKILLS[0]['skill_id'], 'target': target,
             'retrieval_receipt': got['receipt']})


def test_receipt_cannot_be_retargeted_to_other_skill(tmp_path):
    root, tokens = E.station(tmp_path)
    E.train(root, tokens, transfers=False)
    target = TASKS['P3']['target']
    got = call(root, tokens['agent'], {'operation': 'suit_retrieve', 'target': target})
    assert got['mode'] in ('PROPOSE_TRANSFER', 'DO_NOT_TRANSFER')
    other = next(s['skill_id'] for s in E.SKILLS if s['skill_id'] != got['skill_id'])
    with pytest.raises(ValueError, match='retrieval receipt'):
        call(root, tokens['agent'], {'operation': 'suit_propose', 'skill_id': other,
             'target': target, 'retrieval_receipt': got['receipt']})


def test_receipt_field_forgery_rejected(tmp_path):
    root, tokens = E.station(tmp_path)
    E.train(root, tokens, transfers=False)
    target = TASKS['P3']['target']
    got = call(root, tokens['agent'], {'operation': 'suit_retrieve', 'target': target})
    receipt = dict(got['receipt'])
    receipt['skill_digest'] = '0' * 64
    with pytest.raises(ValueError, match='retrieval receipt'):
        call(root, tokens['agent'], {'operation': 'suit_propose', 'skill_id': got['skill_id'],
             'target': target, 'retrieval_receipt': receipt})
