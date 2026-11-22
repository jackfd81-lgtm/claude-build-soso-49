import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

import muse024
from contract.cognitive_pathway_023 import run_cognitive_023
from realcog.ph6_brains_2208 import Big49, Small49, Block, Pointer, Rejected

ROOT = Path(__file__).resolve().parents[1]


def request(task_id, operation="evaluate", **extra):
    return dict(operation=operation, domain="mathematics", task_id=task_id, text="x=51", **extra)


@pytest.fixture
def pilot(tmp_path):
    root = tmp_path / "pilot"
    admin = muse024.initialize(root)
    qualifier = muse024.issue(root, {"principal": "verify", "scopes":
        ["procedure:qualify", "procedure:read", "big:write", "small:write"]})["token"]
    return root, admin, qualifier


def train(root, admin):
    return muse024.call(root, admin, request("training", "train", expected_answer="51"))["method_learned"]


def qualify(root, qualifier, method, **kwargs):
    return muse024.call(root, qualifier, {"operation": "qualify", "method": method}, **kwargs)


def test_qualification_and_store_ablation_restart(pilot):
    root, admin, qualifier = pilot
    method = train(root, admin)
    with pytest.raises(PermissionError):
        qualify(root, admin, method)
    baseline = muse024.call(root, admin, request("before"))
    assert baseline["method_applied"] is None
    refs = qualify(root, qualifier, method)
    assert refs["big"]["pool"] == "big" and refs["small"]["pool"] == "small"
    assert qualify(root, qualifier, method)["replayed"]
    command = [sys.executable, str(ROOT / "muse024.py"), "--state", str(root), "call", "--json",
               json.dumps({**request("held"), "text": "x=-17"})]
    fresh = json.loads(subprocess.check_output(command, env={**os.environ, "MUSE024_TOKEN": admin}, text=True))
    assert fresh["answer"] == "-17" and fresh["method_applied"] == method
    assert set(fresh["contributing_blocks"]) == {refs["big"]["record_id"], refs["small"]["record_id"]}
    for pool in ("big", "small"):
        ablated = muse024.call(root, admin, request("no-" + pool, ablate=[pool]))
        assert ablated["answer"] == "51" and ablated["operation_cost"] > fresh["operation_cost"]
        assert ablated["method_applied"] is None and not ablated["contributing_blocks"]
    ablated = muse024.call(root, admin, request("application", mode="no_application"))
    assert ablated["methods_retrieved"] == [method] and ablated["method_applied"] is None


def test_rollback_across_pools_and_deduplication(pilot):
    root, admin, qualifier = pilot
    method = train(root, admin)
    def fail(phase):
        if phase == "after_big":
            raise RuntimeError("interrupted after big")
    with pytest.raises(RuntimeError):
        qualify(root, qualifier, method, fault=fail)
    assert all(x["populated"] == 0 for x in
               muse024.call(root, admin, {"operation": "occupancy"}).values())
    qualify(root, qualifier, method)
    state = muse024.call(root, admin, {"operation": "occupancy"})
    assert [state[p]["populated"] for p in ("big", "small")] == [1, 1]
    req = request("dedup")
    muse024.call(root, admin, req)
    assert muse024.call(root, admin, req)["replayed"]
    with pytest.raises(ValueError):
        muse024.call(root, admin, {**req, "text": "x=9"})


def test_process_death_between_block_writes(pilot):
    root, admin, qualifier = pilot
    method = train(root, admin)
    code = """import os,sys,muse024
muse024.call(sys.argv[1],os.environ["MUSE024_TOKEN"],dict(operation="qualify",method=sys.argv[2]),
             fault=lambda phase: os._exit(41) if phase=="after_big" else None)
"""
    result = subprocess.run([sys.executable, "-c", code, str(root), method], cwd=ROOT,
                            env={**os.environ, "MUSE024_TOKEN": qualifier})
    assert result.returncode == 41
    assert all(x["populated"] == 0 for x in muse024.call(root, admin, {"operation": "occupancy"}).values())
    assert qualify(root, qualifier, method)["small"]["owner"] == "L15"


def test_auth_and_owner(pilot):
    root, admin, _ = pilot
    user = muse024.issue(root, {"principal": "bounded", "scopes": ["small:write", "small:read"],
                                "owners": ["L01"]})["token"]
    write = dict(operation="memory_write", pool="small", owner="L01",
                 record_id="memory-1", bucket="history", content="presented as input")
    for changed in ({"owner": "L15"}, {"pool": "big"}, {"authority": False}, {"authority": 1},
                    {"record_id": "c024-reserved"}):
        with pytest.raises(PermissionError):
            muse024.call(root, user, {**write, **changed})
    with pytest.raises(PermissionError):
        muse024.call(root, "invalid", write)
    with pytest.raises(PermissionError):
        muse024.call(root, user, request("cannot-train", "train", expected_answer="51"))
    pointer = muse024.call(root, user, write)
    assert muse024.call(root, user, dict(operation="memory_read", pool="small",
                                        owner="L01", pointer=pointer))["content"] == "presented as input"
    with pytest.raises(PermissionError):
        muse024.call(root, user, dict(operation="memory_read", pool="small", owner="L15", pointer=pointer))


@pytest.mark.parametrize("Store,bucket,quota", [(Big49, "association", 320), (Small49, "workspace_micro", 24)])
def test_bucket_capacity_and_immutable_versions(tmp_path, Store, bucket, quota):
    path = tmp_path / "pool.db"
    store = Store(path)
    try:
        first = store.put(Block("original", "L01", bucket, "original"), principal="L01")
        with pytest.raises(Rejected, match="ID_EXISTS"):
            store.put(Block("original", "L01", bucket, "new"), principal="L01")
        next_pointer = store.put(Block("replacement", "L01", bucket, "new", parents=("original",)), principal="L01")
        assert next_pointer.version == 1
        for index in range(quota - 2):
            store.put(Block("capacity-" + str(index), "L01", bucket, "fixture"), principal="L01")
        with pytest.raises(Rejected, match="CAPACITY_EXHAUSTED"):
            store.put(Block("overflow", "L01", bucket, "fixture"), principal="L01")
        with pytest.raises(Rejected, match="UNRESOLVED"):
            store.read(Pointer("missing", 1, first.digest, "L01", first.pool), principal="L01")
    finally:
        store.close()
    reopened = Store(path)
    try:
        assert reopened.count(bucket) == quota
        assert reopened.read(first, principal="L01")["content"] == "original"
        assert reopened.read(next_pointer, principal="L01")["parents"] == ["original"]
    finally:
        reopened.close()


def test_audit_pagination_digest_and_privilege(pilot):
    root, admin, _ = pilot
    muse024.call(root, admin, request("private"))
    muse024.call(root, admin, request("private2"))
    user = muse024.issue(root, {"principal": "auditor", "scopes": ["audit:read"]})["token"]
    with pytest.raises(PermissionError):
        muse024.call(root, user, {"operation": "audit", "raw": True})
    page = muse024.call(root, user, {"operation": "audit", "limit": 1})
    assert page["redacted"] and page["next"] and not page["truncated_records"]
    assert "trace" not in page["records"][0]["record"]
    raw = muse024.call(root, admin, {"operation": "audit", "after": page["next"], "raw": True})
    item = raw["records"][0]
    assert item["digest"] == muse024.procedural_memory.identity(item["record"])
    assert item["record"]["request_id"] == "bootstrap:private2"


def test_migrate_populated_backup_restore(tmp_path):
    root = tmp_path / "existing"
    root.mkdir(mode=0o700)
    legacy = run_cognitive_023(root, dict(task_type="cognitive_023", mode="train", expected_answer="8",
                                          domain="mathematics", task_id="legacy", text="x=8"))
    admin = muse024.initialize(root)
    with pytest.raises(RuntimeError):
        muse024.call(root, admin, {"operation": "migrate"},
                     fault=lambda _: (_ for _ in ()).throw(RuntimeError("migration interrupted")))
    assert muse024.call(root, admin, {"operation": "migrate"})["big"]["configured"] == 1600
    upload = tmp_path / "safe.db"
    muse024.call(root, admin, {"operation": "backup", "destination": str(upload)})
    qualify(root, admin, legacy["method_learned"])
    restored = tmp_path / "restore"
    muse024.call(root, admin, {"operation": "restore", "snapshot": str(upload), "destination": str(restored)})
    assert muse024.call(restored, admin, request("restored"))["method_applied"] is None
    qualify(restored, admin, legacy["method_learned"])
    assert muse024.call(restored, admin, request("restored2"))["method_applied"] == legacy["method_learned"]


def test_corrupt_block_fails_closed(pilot):
    root, admin, qualifier = pilot
    qualify(root, qualifier, train(root, admin))
    with sqlite3.connect(root / "observations.sqlite3") as db:
        db.execute("UPDATE blocks SET digest='corrupt' WHERE pool='big'")
    with pytest.raises(Rejected, match="RECORD_HASH_MISMATCH"):
        muse024.call(root, admin, request("bad-block"))


def test_concurrent_retry_writes_once(pilot):
    root, admin, _ = pilot
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: muse024.call(root, admin, request("same")), range(12)))
    assert sum(not result["replayed"] for result in results) == 1


def test_storage_full_rolls_back_pending_observation(pilot):
    root, _, _ = pilot
    with muse024.transaction(root) as db:
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        db.execute(f"PRAGMA max_page_count={pages}")
        with pytest.raises(sqlite3.OperationalError) as failure:
            db.execute("INSERT INTO observation VALUES (?,?,?,?,?,?)",
                       ("large", "test", 0, "caption", "x", "x" * 20_000_000))
        assert failure.value.sqlite_errorcode == sqlite3.SQLITE_FULL
    assert muse024.recall_observations(root, "test")["observations"] == []


def test_unqualified_method_rejected_and_revocation(pilot):
    root, admin, qualifier = pilot
    method = train(root, admin)
    grant = qualify(root, qualifier, method)
    assert grant["evidence"]["cases"] == ["linear-unit"]
    muse024.call(root, qualifier, {"operation": "revoke", "method": method, "reason": "fixture"})
    cold = muse024.call(root, admin, request("after-revocation"))
    assert cold["method_applied"] is None
    assert muse024.call(root, admin, {"operation": "occupancy"})["big"]["populated"] == 1


def test_backup_of_qualified_memory_restores_executable_references(pilot, tmp_path):
    root, admin, qualifier = pilot
    method = train(root, admin)
    original = qualify(root, qualifier, method)
    snapshot = tmp_path / "qualified.sqlite3"
    muse024.call(root, admin, {"operation": "backup", "destination": str(snapshot)})
    restored = tmp_path / "restored-qualified"
    muse024.call(root, admin, {"operation": "restore", "snapshot": str(snapshot), "destination": str(restored)})
    observed = muse024.call(restored, admin, request("after-restore"))
    assert observed["method_applied"] == method
    assert observed["contributing_blocks"] == [original["small"]["record_id"], original["big"]["record_id"]]


def test_operation_audit_verifies_memory_writes(pilot):
    root, admin, qualifier = pilot
    refs = qualify(root, qualifier, train(root, admin))
    page = muse024.call(root, admin, {"operation": "audit", "raw": True, "stream": "operation"})
    event = page["records"][-1]
    assert muse024.procedural_memory.identity(event["record"]) == event["digest"]
    assert {event["record"][x]["record_id"] for x in ("big", "small")} == {
        refs[x]["record_id"] for x in ("big", "small")}


def test_failed_first_migration_leaves_build023_readable(tmp_path):
    root = tmp_path / "old"
    root.mkdir(mode=0o700)
    original = dict(task_type="cognitive_023", mode="train", expected_answer="6",
                    domain="mathematics", task_id="old", text="x=6")
    saved = run_cognitive_023(root, original)
    with pytest.raises(RuntimeError):
        with muse024.transaction(root) as db:
            muse024.migrate_db(db, fault=lambda _: (_ for _ in ()).throw(RuntimeError("stop")))
    with sqlite3.connect(root / "observations.sqlite3") as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='pilot024_schema'").fetchone()
    recovered = run_cognitive_023(root, original)
    assert recovered["replayed"] and recovered["method_learned"] == saved["method_learned"]


def test_authenticated_rejection_and_interrupted_attempt_audit(pilot):
    root, admin, qualifier = pilot
    method = train(root, admin)
    with pytest.raises(PermissionError):
        qualify(root, admin, method)
    code = """import os,sys,muse024
muse024.call(sys.argv[1],os.environ["MUSE024_TOKEN"],dict(operation="qualify",method=sys.argv[2]),
             fault=lambda phase: os._exit(42) if phase=="after_big" else None)
"""
    child = subprocess.run([sys.executable, "-c", code, str(root), method], cwd=ROOT,
                           env={**os.environ, "MUSE024_TOKEN": qualifier})
    assert child.returncode == 42
    recovery = muse024.call(root, admin, {"operation": "recover_audit"})
    assert len(recovery["recovered_attempts"]) == 1
    assert recovery["requires_receipt_check"]
    page = muse024.call(root, admin, {"operation": "audit", "stream": "attempt"})
    states = [r["record"]["state"] for r in page["records"]]
    assert "FAILED" in states and "INTERRUPTED" in states
    for item in page["records"]:
        assert muse024.procedural_memory.identity(item["record"]) == item["digest"]
        assert not {"principal", "process"} & item["record"].keys()
