"""Portable tests for Build 025's inherited advisory capability boundary."""
from contextlib import closing
import sqlite3

import pytest

from adapters.integration import suit_ae_evaluate


@pytest.mark.parametrize("action", ["execute_lane1", "modify_evidence", "ratify_production"])
def test_advisory_suit_refuses_authoritative_actions(action):
    with closing(sqlite3.connect(":memory:")) as db:
        result = suit_ae_evaluate(db, {"agent_id": "agent-1", "role": "investigator"},
                                  ["read"], action, ["read"], True, True, [])
    assert result["decision"] == "REFUSED"
    assert result["execution_authorization"] == "NOT_GRANTED"


def test_pending_iou_keeps_advisory_adapter_held():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE iou_ledger(record_id TEXT, state TEXT)")
        db.execute("INSERT INTO iou_ledger VALUES ('iou.1', 'PENDING')")
        identity = {"agent_id": "agent-1", "role": "investigator"}
        result = suit_ae_evaluate(db, identity, ["read"], "read", ["read"], True, True, ["iou.1"])
        clear = suit_ae_evaluate(db, identity, ["read"], "read", ["read"], True, True, [])
    assert result["decision"] == "HELD"
    assert clear["decision"] == "ADVISORY_ONLY"
    assert clear["execution_authorization"] == "NOT_GRANTED"
    assert "NOT_CONNECTED" in clear["implementation"]
