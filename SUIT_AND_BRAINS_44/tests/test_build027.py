"""Exercise both monitor contracts and the authenticated Suit audit boundary."""
from dataclasses import replace

import pytest

from eval061 import run as E
from eval026.run import run
from muse024 import call
from suit61 import audit027, disposition
from suit61.closet_port import ClosetPort

TASKS = {task["id"]: task["target"] for task in E.TASKS}


def audit(root, token, **fields):
    return call(root, token, {"operation": "suit_audit027", **fields})["records"]


def test_joint_retrieval_retains_budget_and_has_monitored_admission(tmp_path):
    report = run(tmp_path)["heldout"]
    assert report["on"]["task_complete"] and report["restored"]["task_complete"]
    assert not report["off"]["task_complete"]
    sources = report["monitor_audit"]
    task = report["on"]["handoff"]["task_id"]
    assert any(row["source"]["task_id"] == task and row["source"]["kind"] == "TASK_BEGIN"
               and row["status"] == "ACKED" for row in sources)
    assert sum(row["source"]["task_id"] == task and row["source"]["kind"] == "TASK_STEP_ACK"
               for row in sources) == 7
    assert any(message["response_kind"] == "IOU_ACK" for row in sources for message in row["messages"])
    for row in sources:
        assert row["source"]["trace_digest"] == row["disposition"]["evidence_ref"]["sha256"]
        assert row["authority"] == "AUTH:0" and row["execution_authorization"] == "NOT_GRANTED"


def test_pending_closet_receipt_recovers_with_same_transaction_identity(tmp_path, monkeypatch):
    root, tok = E.station(tmp_path)
    E.train(root, tok, transfers=False)
    original = audit027.ClosetPort

    class FailingPort(original):
        def decision(self, *args):
            message = super().decision(*args)
            return message | {"status": "FAILED", "error": "TEST_CLOSET_UNAVAILABLE"}

    monkeypatch.setattr(audit027, "ClosetPort", FailingPort)
    out = call(root, tok["agent"], {"operation": "suit_retrieve", "target": TASKS["U1"]})
    pending, = audit(root, tok["agent"])
    assert pending["status"] == "PENDING"
    assert out["mode"] == "UNFAMILIAR_REDISCOVERY_REQUIRED"
    assert not audit(root, tok["qual"])
    with pytest.raises(PermissionError):
        audit(root, tok["obs1"])
    assert audit(root, tok["agent"], after=pending["source"]["seq"]) == []
    monkeypatch.setattr(audit027, "ClosetPort", original)
    from tests.test_suit61_unfamiliar_transfer import fresh
    recovered, = fresh(root, tok["agent"], "suit_audit027", retry=True)["records"]
    assert recovered["status"] == "ACKED"
    assert recovered["messages"][0]["transaction_id"] == pending["messages"][0]["transaction_id"]
    assert audit(root, tok["agent"], retry=True) == [recovered]


def test_adapter_exception_preserves_decision_and_records_audit_gap(tmp_path, monkeypatch):
    def unsupported(*args):
        raise ValueError("unsupported")

    root, tok = E.station(tmp_path)
    monkeypatch.setattr(disposition, "record_for", unsupported)
    result = call(root, tok["agent"], {"operation": "suit_retrieve", "target": TASKS["U1"]})
    row, = audit(root, tok["agent"])
    assert result["execution_authorization"] == "NOT_GRANTED"
    assert row["status"] == "PENDING" and row["error"] == "ValueError"


def test_closed_codes_and_genuine_candidate_protocol():
    assert all(disposition.verify_vendored().values())
    with pytest.raises(ValueError, match="mapping"):
        disposition.record_for("UNKNOWN", {"seq": 1}, "ctx")
    record = disposition.record_for("HANDSHAKE_FAILED", {"seq": 1}, "ctx")
    assert record.as_dict()["observation"] == "OBS_UNCERTAIN"
    with pytest.raises(ValueError, match="member"):
        replace(record, observation="INVALID")
    source = {"seq": 1, "trace_digest": "f" * 64}
    assert audit027.audit_control(source)["validation_status"] == "VALID"
    assert audit027.audit_control(source, '{"task_id":"wrong"}')["validation_status"] != "VALID"


def test_reference_router_idempotency_and_authenticated_envelope():
    port = ClosetPort("bound-principal")
    source = {"seq": 9, "trace_digest": "a" * 64, "task_id": "t"}
    first = port.decision(source, "context", "EVIDENCE_HOLD")
    again = port.decision(source, "context", "EVIDENCE_HOLD")
    assert first["status"] == "ACKED" and again["replay"]
    assert len(port.dispatched) == 1
    iou = port.evidence_gap(source, "context", 1, "EVIDENCE_HOLD")
    assert port.pending_ious()[iou["obligation_id"]] == "PENDING"
    assert {entry.client_id for entry in port.router.ledger._entries.values()} == {"bound-principal"}
