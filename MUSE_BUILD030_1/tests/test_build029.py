"""Build 029 advisory integration and crash-window validation."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from contract import unified025
from eval026.run import run
from eval061.run import TASKS
from muse024 import call, initialize, issue, transaction
import sososuit42
from suit61 import shadow029, store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def station(tmp_path):
    root = tmp_path / "state"
    initialize(root)
    scopes = sorted(unified025.SCOPES["unified_start025"] | {"suit:read"})
    actor = issue(root, {"principal": "actor", "scopes": scopes, "owners": []})["token"]
    other = issue(root, {"principal": "other", "scopes": ["suit:read"], "owners": []})["token"]
    shadow029.configure(root, True)
    return root, actor, other


def start(root, token, fault=None):
    case = json.loads((ROOT / "eval025/workload.json").read_text())["training"]
    target = next(task["target"] for task in TASKS if task["id"] == "P1B")
    return call(root, token, {"operation": "unified_start025", "run": "training",
                             "problem": case["problem"], "targets": {case["answer"]: target}}, fault=fault)


def receipts(root, token):
    return call(root, token, {"operation": "suit_shadow029"})["records"]


def test_trace_capture_and_outbox_rollback_are_atomic(station):
    root, token, other = station

    def fail(point):
        if point == "before_unified_025_commit":
            raise RuntimeError("fixture rollback")

    with pytest.raises(RuntimeError, match="fixture rollback"):
        start(root, token, fail)
    assert receipts(root, token) == []
    with transaction(root) as db:
        assert db.execute("SELECT count(*) FROM suit61_trace WHERE kind='INVESTIGATION_HOLD'").fetchone()[0] == 0
    start(root, token)
    start(root, token)
    row, = receipts(root, token)
    assert row["status"] == "PENDING"
    assert receipts(root, other) == []
    shadow029.configure(root, False)
    assert shadow029.drain(root) == [] and receipts(root, token) == [row]
    shadow029.configure(root, True)
    assert shadow029.drain(root)[0]["status"] == "ACKED"
    receipt = receipts(root, token)[0]["receipt"]
    assert receipt["state"]["principal"] == shadow029.principal_id("actor")
    assert receipt["seal"]["executability"] == "NON_EXECUTABLE"
    assert receipt["disposition"]["execution_authorization"] == "NOT_GRANTED"
    assert receipt["disposition"]["authority"] == "AUTH:0"


@pytest.mark.parametrize("point", ["before_journal_submit", "after_journal_commit"])
def test_recover_actual_process_exit(station, point):
    root, token, _ = station
    start(root, token)
    script = """import os, sys
from suit61.shadow029 import drain
def fault(point):
    if point == sys.argv[2]:
        os._exit(79)
drain(sys.argv[1], fault=fault)
"""
    child = subprocess.run([sys.executable, "-c", script, str(root), point], cwd=ROOT, timeout=15)
    assert child.returncode == 79
    assert receipts(root, token)[0]["status"] == "PENDING"
    journal = sososuit42.Journal(root / "sososuit42.sqlite")
    previous = journal.pending(shadow029.principal_id("actor"))
    assert len(previous) == (point == "after_journal_commit")
    restarted = subprocess.run([sys.executable, "-m", "suit61.shadow029", str(root), "drain"],
                               cwd=ROOT, capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(restarted.stdout)[0]["status"] == "ACKED"
    final = journal.pending(shadow029.principal_id("actor"))
    assert len(final) == 1 and (not previous or final == previous)
    assert shadow029.drain(root) == []


def test_concurrent_workers_deduplicate_and_replay(station):
    root, token, _ = station
    start(root, token)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: shadow029.drain(root), range(4)))
    assert sum(len(result) for result in results) == 1
    with transaction(root) as db:
        con, raw_candidate, receipt = db.execute(
            "SELECT context,candidate,receipt FROM shadow029_work").fetchone()
    context, candidate = sososuit42.parse(con), sososuit42.parse(raw_candidate)
    persisted = sososuit42.parse(receipt)
    assert sososuit42.verify_replay(context, candidate, persisted)
    context["evidence"]["ext:source"]["feature"] = "different reading"
    assert not sososuit42.verify_replay(context, candidate, persisted)
    with sqlite3.connect(root / "sososuit42.sqlite") as db:
        assert db.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 1


def test_unsupported_source_and_refused_verdict_create_no_iou(station, monkeypatch):
    root, token, _ = station
    with transaction(root) as db:
        store.trace(db, "actor", "INVESTIGATION_HOLD", {"task": "fixture", "request": {}})
    assert receipts(root, token)[0]["status"] == "DIAGNOSTIC"
    start(root, token)
    original = shadow029.candidate_for

    def refused(context, governed_digest):
        return original(context, governed_digest) | {"actions": ["NO_OP"]}

    monkeypatch.setattr(shadow029, "candidate_for", refused)
    assert shadow029.drain(root)[0]["status"] == "REFUSED"
    assert sososuit42.Journal(root / "sososuit42.sqlite").pending(shadow029.principal_id("actor")) == []


def test_malformed_source_binding_stops_delivery(station):
    root, token, _ = station
    start(root, token)
    with transaction(root) as db:
        db.execute("UPDATE shadow029_work SET context=?", (b"{}",))
    assert shadow029.drain(root)[0]["diagnostic"] == "SOURCE_BINDING_MISMATCH"
    assert not (root / "sososuit42.sqlite").exists()


def test_shadow_fixture_keeps_cognitive_and_suit_results(tmp_path):
    off = run(tmp_path / "off")["heldout"]
    on = run(tmp_path / "on", shadow=True)["heldout"]
    for arm in ("on", "off", "restored", "unlimited_control"):
        for field in ("decision", "handoff", "trace", "memory_accesses", "execution_evidence"):
            assert on[arm][field] == off[arm][field]
    assert on["monitor_audit"] == off["monitor_audit"]
    assert on["negative"] == off["negative"]
    assert len(on["shadow_receipts"]) == len(on["shadow_delivery"]) == 12
    assert all(row["status"] == "ACKED" for row in on["shadow_receipts"])
    ids = {row["receipt"]["seal"]["digest"]["sha256"] for row in on["shadow_receipts"]}
    assert len(ids) == len(on["shadow_pending_ious"]) == 12
    assert {row["decision_id"] for row in on["shadow_pending_ious"]} == ids


def test_principal_mapping_conflict_is_rejected(station):
    root, token, _ = station
    subject = shadow029.principal_id("actor")
    with transaction(root) as db:
        db.execute("INSERT INTO shadow029_principal VALUES (?, ?)", (subject, "other"))
    start(root, token)
    record, = receipts(root, token)
    assert record["status"] == "DIAGNOSTIC"
    assert record["diagnostic"] == "PRINCIPAL_MAPPING_CONFLICT"
    assert not shadow029.drain(root)
