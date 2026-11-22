"""Handshake and wiring: Suit qualification <-> Big49/Small49 <-> retrieval <-> proposal receipt <-> trace."""
import json, sqlite3, subprocess, sys
from pathlib import Path
import pytest

from eval061 import run as E
from muse024 import call, transaction
from suit61 import store
from suit61.agent import Agent

ROOT = Path(__file__).resolve().parents[1]
TASKS = {t["id"]: t for t in E.TASKS}


@pytest.fixture
def st(tmp_path):
    return E.station(tmp_path)


@pytest.fixture
def trained(st):
    root, tok = st
    E.train(root, tok, transfers=True)
    return root, tok


def links(root):
    with transaction(root) as db:
        return db.execute("SELECT kind,item FROM suit61_links ORDER BY kind,item").fetchall()


def big_small_counts(root):
    from realcog.memory024 import utilization
    with transaction(root) as db:
        u = utilization(db)
    return u["big"]["populated"], u["small"]["populated"]


def test_qualification_writes_big49_and_small49_and_a_link_in_the_same_transaction(st):
    root, tok = st
    before = big_small_counts(root)
    E.train(root, tok, transfers=False)
    big, small = big_small_counts(root)
    assert (big - before[0], small - before[1]) == (3, 3)               # one Big49 + one Small49 per qualified skill
    assert [k for k, _ in links(root)] == ["skill"] * 3


def test_qualified_transfer_is_linked_and_reuse_verifies_the_handshake_after_restart(trained):
    root, tok = trained
    assert ("transfer", "") not in links(root) and sum(1 for k, _ in links(root) if k == "transfer") == 1
    code = ("import sys,json;sys.path.insert(0,sys.argv[1]);from muse024 import call\n"
            "s,r,t=json.loads(sys.stdin.read());o=call(r,s,{'operation':'suit_retrieve','target':t})\n"
            "print(json.dumps([o['mode'],o['receipt']['seq']>0,o['execution_authorization']]))")
    p = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], input=json.dumps([tok["agent"], str(root), TASKS["P1B"]["target"]]),
                       capture_output=True, text=True, check=True)
    assert json.loads(p.stdout) == ["REUSE_QUALIFIED_TRANSFER", True, "NOT_GRANTED"]


def test_failed_small49_write_aborts_the_qualification(st, monkeypatch):
    root, tok = st
    from realcog import ph6_brains_2208 as B
    orig = B.Small49.put

    def boom(self, block, *, principal):
        if block.record_id.startswith("t061-"):
            raise B.Rejected("INJECTED")
        return orig(self, block, principal=principal)
    monkeypatch.setattr(B.Small49, "put", boom)
    s = E.SKILLS[0]
    call(root, tok["author"], {"operation": "suit_learn", "skill": s})
    for i, p in enumerate(E.TRAIN["practice"][s["skill_id"]]):
        E.record_trial(root, tok["obs1" if i % 2 == 0 else "obs2"], "suit_practice", skill_id=s["skill_id"], **p)
    with pytest.raises(Exception):
        call(root, tok["qual"], {"operation": "suit_qualify_skill", "skill_id": s["skill_id"]})
    assert links(root) == []
    assert call(root, tok["qual"], {"operation": "suit_show", "kind": "skill", "item": s["skill_id"]})["state"] == "PRACTICED"


@pytest.mark.parametrize("prefix,old,new", [("t061-%", b"contexts", b"contextz"), ("s061-%", b"record_digest", b"record_digesz")])
def test_tampered_big49_or_small49_record_fails_closed_and_releases_no_plan(trained, prefix, old, new):
    root, tok = trained
    ok = call(root, tok["agent"], {"operation": "suit_retrieve", "target": TASKS["P1B"]["target"]})
    assert ok["mode"] == "REUSE_QUALIFIED_TRANSFER"
    with transaction(root) as db:
        rows = db.execute("SELECT pool,record_id,payload FROM blocks WHERE record_id LIKE ?", (prefix,)).fetchall()
        assert rows
        for pool, rid, payload in rows:
            assert old in payload
            db.execute("UPDATE blocks SET payload=? WHERE pool=? AND record_id=?", (payload.replace(old, new), pool, rid))
    bad = call(root, tok["agent"], {"operation": "suit_retrieve", "target": TASKS["P1B"]["target"]})
    assert bad["mode"] == "HANDSHAKE_FAILED" and "plan" not in bad and bad["execution_authorization"] == "NOT_GRANTED"
    assert call(root, tok["qual"], {"operation": "suit_show", "kind": "trace"})["intact"]


def test_missing_link_for_a_qualified_item_fails_closed(trained):
    root, tok = trained
    with transaction(root) as db:
        db.execute("DELETE FROM suit61_links WHERE kind='transfer'")
    bad = call(root, tok["agent"], {"operation": "suit_retrieve", "target": TASKS["P1B"]["target"]})
    assert bad["mode"] == "HANDSHAKE_FAILED" and "no Big49/Small49 link" in bad["reason"]


def test_proposal_requires_a_retrieval_receipt_for_the_same_context(trained):
    root, tok = trained
    tr = E.TRAIN["transfers"]["P1"]
    with pytest.raises(ValueError):
        call(root, tok["agent"], {"operation": "suit_propose", "skill_id": tr["skill_id"], "target": tr["target"]})
    other = call(root, tok["agent"], {"operation": "suit_retrieve", "target": TASKS["P3"]["target"]})
    with pytest.raises(ValueError):
        call(root, tok["agent"], {"operation": "suit_propose", "skill_id": tr["skill_id"], "target": tr["target"],
                                  "retrieval_receipt": other["receipt"]})
    with pytest.raises(ValueError):
        call(root, tok["agent"], {"operation": "suit_propose", "skill_id": tr["skill_id"], "target": tr["target"],
                                  "retrieval_receipt": {"seq": 10 ** 6}})


def test_full_path_agent_to_suit_to_brains_to_memory_to_trace(st):
    root, tok = st
    E.train(root, tok, transfers=False)
    a = Agent(root, suit_token=tok["agent"], brain_token=tok["brain"], suit=True, mask=True, transfer=True)
    out = a.handle(TASKS["P3"]["target"])
    assert out["proposal_disposition"] and out["brain_review"] in ("VIOLATED", "CONSISTENT") and a.brain.invocations >= 1
    assert out["execution_authorization"] == "NOT_GRANTED" if "execution_authorization" in out else True
    trace = call(root, tok["qual"], {"operation": "suit_show", "kind": "trace"})
    assert trace["intact"] and trace["events"] >= 10
    kinds = {r[0] for r in sqlite_rows(root, "SELECT DISTINCT kind FROM suit61_trace")}
    assert {"SKILL_QUALIFIED", "RETRIEVE", "TRANSFER_PROPOSED"} <= kinds


def sqlite_rows(root, sql):
    with transaction(root) as db:
        return db.execute(sql).fetchall()
