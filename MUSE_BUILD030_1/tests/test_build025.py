import json
import subprocess
import sys
from pathlib import Path

import pytest

from contract.functions025 import OPERATIONS
from eval025.run import experiment, issue_qualifier, send, solve
from muse024 import call, initialize, transaction

SOURCE = Path(__file__).resolve().parents[1]


@pytest.fixture
def station(tmp_path):
    root = tmp_path / "station"
    return root, initialize(root)


def observe(value, **extra):
    return {"subject": "Station", "predicate": "state", "value": value, "source": "sensor", **extra}


def semantic(station, name, rows, pathway="A", query=None, arm="A"):
    return send(*station, "start025", run=name, arm=arm, problem={
        "pathway": pathway, "observations": rows, "query": query or {"subject": "station", "predicate": "state"}})


def case_file():
    return json.loads((SOURCE / "eval025/workload.json").read_text())


def test_parser_and_direction(station):
    result = semantic(station, "direction", [{"source": "a", "text": "The dog bit the man."},
                                             {"source": "b", "text": "The man bit the dog."}],
                      query={"subject": "dog", "predicate": "bite"})
    assert result["decision"]["answer"] == "man"
    relations = next(row["output"] for row in result["trace"] if row.get("function") == "L16")
    assert {(row["subject"], row["value"]) for row in relations} == {("dog", "man"), ("man", "dog")}


def test_paraphrase_and_normalization(station):
    rows = [{"source": "a", "text": "The crate holds the spool."},
            {"source": "b", "subject": " CRATE ", "predicate": "contains", "value": " Spool "}]
    result = semantic(station, "paraphrase", rows, query={"subject": "Crate", "predicate": "contains"})
    assert result["decision"]["answer"] == "spool"
    assert len(result["decision"]["support"]) == 2
    entities = next(row["output"]["index"] for row in result["trace"] if row.get("function") == "L04")
    assert entities["crate"]["spellings"] == ["crate", " CRATE "]


def test_quantified_contradiction(station):
    rows = [{"source": "a", "text": "All sensors passed."},
            {"source": "b", "text": "Not all sensors passed."}]
    result = semantic(station, "quantified", rows, query={"subject": "sensors", "predicate": "passed"})
    assert result["decision"]["status"] == "CONFLICT"
    assert result["decision"]["support"][0]["same_source"] is False


def test_conflicting_and_uncertain_observations(station):
    assert semantic(station, "conflict", [observe("open"), observe("closed")])["decision"]["status"] == "CONFLICT"
    assert semantic(station, "uncertain", [observe("open", uncertain=True)])["decision"]["status"] == "ABSTAIN"
    assert semantic(station, "overheat", [observe("open")], query={
        "subject": "station", "predicate": "temperature"})["decision"]["status"] == "ABSTAIN"


def test_late_and_simultaneous_evidence(station):
    recent = observe("closed", event="2026-11-20T11:08:00Z", arrival="2026-11-20T11:08:02Z")
    stale = observe("open", event="2026-11-20T11:07:00Z", arrival="2026-11-20T11:09:00Z")
    conflict = observe("open", event="2026-11-20T05:08:00-06:00", arrival="2026-11-20T11:10:00Z")
    assert semantic(station, "late", [recent, stale], "B")["decision"]["answer"] == "closed"
    assert semantic(station, "opposite", [dict(recent, event=stale["event"]), dict(stale, event=recent["event"])],
                    "B")["decision"]["answer"] == "open"
    assert semantic(station, "simultaneous", [recent, conflict], "B")["decision"]["status"] == "CONFLICT"
    assert semantic(station, "undated", [recent, observe("open")], "B")["decision"]["status"] == "ABSTAIN"


def test_drop_activates_alternative(station):
    result = semantic(station, "drop", [observe("open"), {"source": "sensor", "text": "Ambiguous."}])
    assert result["decision"]["status"] == "ABSTAIN"
    assert any(row.get("connection") == ["L03", "L05"] and row["disposition"] == "DROP" for row in result["trace"])
    assert any(row.get("function") == "L05" and "subject" in row["output"]["required_fields"] for row in result["trace"])
    assert any(row.get("connection") == ["L03", "L04"] and row["disposition"] == "PASS" for row in result["trace"])


def test_exact_authority_and_scope(station):
    root, token = station
    for value in (False, None, 1, "0"):
        with pytest.raises((ValueError, PermissionError)):
            call(root, token, {"authority": value, "operation": "start025", "run": "invalid"})
    reader = send(root, token, "issue", principal="readonly", scopes=["big:read"], owners=["L01"])["token"]
    with pytest.raises(PermissionError):
        send(root, reader, "start025", run="denied", problem={"pathway": "C"})


def test_inference_rejects_affirming_consequent():
    rule = {"if": ["wet"], "then": "damp"}
    assert OPERATIONS["U06"](["damp"], [rule])["facts"] == ["damp"]
    assert OPERATIONS["U06"](["wet", "!damp"], [rule])["contradiction"] is True


def test_resume_after_process_exit_and_dedup(station):
    root, token = station
    training = case_file()["training"]
    start = send(root, token, "start025", run="pending", problem=training["problem"])
    with pytest.raises(ValueError):
        send(root, token, "resume025", run="pending", measurement={"feature": "wrong", "source": "x", "value": 1})
    feature = start["decision"]["request"]["feature"]
    request = {"authority": 0, "operation": "resume025", "run": "pending",
               "measurement": {"source": "sensor", "feature": feature, "request_id": start["decision"]["request"]["id"], "value": training["measurements"][feature]}}
    child = subprocess.run([sys.executable, "-c",
        "import json,sys; from muse024 import call; s,r=json.load(sys.stdin); print(json.dumps(call(sys.argv[1],s,r)))",
        str(root)], input=json.dumps([token, request]), text=True, capture_output=True, check=True, cwd=SOURCE)
    result = json.loads(child.stdout)
    assert result["decision"] != start["decision"]
    assert result["activation_count"] == 2
    assert result == call(root, token, request)
    assert any(row.get("function") == "U13" for row in result["trace"])
    end = send(root, token, "expire025", run="pending")
    assert end["decision"]["status"] == "ABSTAIN"


def test_atomic_memory_qualification_and_revocation(station):
    root, token = station
    qualifier = issue_qualifier(root, token)
    training = solve(root, token, case_file()["training"])
    learning = send(root, token, "learn025", run=training["run"])
    method = learning["decision"]["candidate_method"]
    with pytest.raises(PermissionError):
        send(root, token, "qualify025", method=method)
    with pytest.raises(RuntimeError):
        def fail(point):
            if point == "after_big_025":
                raise RuntimeError("fault injection")
        call(root, qualifier, {"authority": 0, "operation": "qualify025", "method": method}, fault=fail)
    assert send(root, token, "occupancy")["big"]["populated"] == 0
    qualified = send(root, qualifier, "qualify025", method=method)
    assert {entry["pool"] for entry in qualified["memory"]} == {"small", "big"}
    assert send(root, qualifier, "qualify025", method=method)["memory"] == []
    reader = send(root, token, "issue", principal="other", scopes=["small:read"], owners=["L04"])["token"]
    pointer = next(item for item in qualified["memory"] if item["pool"] == "small")
    with pytest.raises(PermissionError):
        send(root, reader, "memory_read", pool="small", owner="L15", pointer={
            k: v for k, v in pointer.items() if k not in ("operation", "function_id")})
    output = solve(root, token, case_file()["heldout"][0], arm="D", method=method)
    assert output["decision"]["answer"] == output["expected"]
    assert output["trace"][0]["contributing_blocks"]
    with transaction(root) as db:
        db.execute("UPDATE method_025 SET big=? WHERE id=?", ("{}", method))
    output = solve(root, token, case_file()["heldout"][1], arm="D", method=method)
    assert not output["trace"][0]["contributing_blocks"]
    assert any(r["operation"] == "rejected" for r in output["trace"][0]["memory_accesses"])
    assert send(root, qualifier, "revoke025", method=method)["revoked"] == 1
    output = solve(root, token, case_file()["heldout"][2], arm="D", method=method)
    assert output["memory_reads"] == 0


def test_matched_control_comparisons(tmp_path):
    data = experiment(tmp_path / "matrix")
    arms = data["arms"]
    assert set(arms) == set("ABCDEFGHIJK")
    assert arms["A"]["correct"] == arms["B"]["correct"] == arms["C"]["correct"] == arms["K"]["correct"] == 4
    assert arms["D"]["requests"] == 8
    assert arms["B"]["requests"] == arms["C"]["requests"] == arms["K"]["requests"] == 11
    assert arms["J"]["abstain"] == 4
    assert arms["E"]["requests"] > arms["D"]["requests"]
    assert len(data["function_ablations"]) == 14
    for fid, result in data["function_ablations"].items():
        assert result["changed_outcome"] or result.get("changed_requests"), fid


def test_source_synthesis_avoids_duplicate_references(station):
    row = observe("open")
    result = semantic(station, "duplicate", [row, row, dict(row, source="field-log")])
    sources = result["decision"]["presented_sources"]
    assert sorted(sources) == ["field-log", "sensor"]
    assert all(len(refs) == 1 for refs in sources.values())
    assert any(row.get("function") == "U16" for row in result["trace"])


def test_pending_transaction_retry_after_backup_restore(station, tmp_path):
    root, token = station
    training = case_file()["training"]
    start = send(root, token, "start025", run="recover", problem=training["problem"])
    pending = start["decision"]["request"]
    feature = pending["feature"]
    request = {"authority": 0, "operation": "resume025", "run": "recover",
               "measurement": {"source": "sensor", "feature": feature, "request_id": pending["id"],
                               "value": training["measurements"][feature]}}
    def interrupt(point):
        if point == "before_pathway_025_commit":
            raise RuntimeError("fault injection")
    with pytest.raises(RuntimeError):
        call(root, token, request, fault=interrupt)
    snapshot = tmp_path / "snapshot.sqlite3"
    restored = tmp_path / "restored"
    send(root, token, "backup", destination=str(snapshot))
    send(root, token, "restore", snapshot=str(snapshot), destination=str(restored))
    result = call(restored, token, request)
    assert result["activation_count"] == 2
    assert result == call(root, token, request)
    raw = send(restored, token, "audit", stream="operation", raw=True)
    assert any(item["record"].get("event") == result["event"] for item in raw["records"])


def test_qualified_profile_rejects_invalid_or_missing_discriminator():
    from contract.pathway025 import score_profile
    with pytest.raises(ValueError, match="failed independent"):
        score_profile([1, 3])
    with pytest.raises(ValueError, match="failed independent"):
        score_profile([2, 3])


def test_independent_intake_and_investigation_branches(station):
    root, token = station
    problem = case_file()["training"]["problem"]
    problem["intake"] = {"query": {"subject": "station", "predicate": "state"},
                         "observations": [observe("open"), {"source": "log", "text": "Ambiguous."}]}
    result = send(root, token, "start025", run="independent", problem=problem)
    assert result["intake_decision"]["status"] == "ABSTAIN"
    assert result["decision"]["status"] == "HOLD"
    assert {row.get("disposition") for row in result["trace"]} >= {"PASS", "DROP", "HOLD"}
    assert any(row.get("function") == "L05" for row in result["trace"])
