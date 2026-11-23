"""Claude 010 improvements adopted into Build 025: scale-gated qualification and U09 selection,
per-procedure quarantine with explicit no-memory fallback, and typed derived evidence for the
025 investigation. Real CLI-level calls (``muse024.call``) and an authenticated HTTP service."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from eval025.run import send
from muse024 import call, initialize, issue, transaction
from realcog import memory024
from realcog.ph6_brains_2208 import Rejected

ROOT = Path(__file__).resolve().parents[1]
WORK = json.loads((ROOT / "eval025/workload.json").read_text())

TEMPORAL_SMALL = {"domain": "temporal", "events": {"s": "2025-01-01T00:00:00+00:00", "t": "2025-01-01T00:10:00+00:00"},
                  "query": ["s", "t"]}
SEMANTIC_SMALL = {"domain": "semantic", "statements": ["zed owns hat", "yan visits spa"], "query": "zed possesses hat"}


def big_temporal(n=14, gap=60):
    names = [f"e{i:02d}" for i in range(n)]
    ev = {x: (None if i % 5 == 3 else f"2025-03-01T00:{(i * gap) // 60:02d}:{(i * gap) % 60:02d}+00:00") for i, x in enumerate(names)}
    return {"domain": "temporal", "events": ev, "before": [[names[i], names[i + 1]] for i in range(n - 1)],
            "query": [names[0], names[-1]]}


def big_semantic(n=20):
    rows = [f"{a} {r} {o}" for a, r, o in zip(["alice", "bob", "carol", "dave"] * 5, ["owns", "visits", "backs", "goes to"] * 5,
                                              ["widget", "shop", "gate", "dock", "mill"] * 4)][: n - 1] + ["zed owns hat"]
    return {"domain": "semantic", "statements": rows, "query": "zed possesses hat"}


@pytest.fixture
def station(tmp_path):
    root = tmp_path / "station"
    admin = initialize(root)
    tok = {}
    for name, scopes in {"trainer": ["procedure:write", "procedure:execute"],
                         "qualifier": ["procedure:qualify", "procedure:read", "big:write", "small:write"],
                         "client": ["procedure:execute", "procedure:read", "observation:write", "big:read", "small:read"],
                         "client_nodrive": ["observation:write"],
                         "client_derive": ["procedure:execute", "observation:write", "big:read", "small:read",
                                           "procedure:read"],
                         "experimenter": ["procedure:execute", "observation:write", "big:read", "small:read",
                                          "procedure:read", "experiment"],
                         "reader": ["big:read", "small:read"]}.items():
        tok[name] = issue(root, {"principal": name, "scopes": scopes, "owners": ["L15"] if name == "qualifier" else []})["token"]
    return root, admin, tok


def teach(station, spec, expected, qualify=True):
    root, admin, tok = station
    out = call(root, tok["trainer"], {"operation": "train", "task_id": "t" + str(abs(hash(json.dumps(spec))) % 10**8),
                                      "expected_answer": expected, **spec})
    method = out["method_learned"]
    if qualify:
        call(root, tok["qualifier"], {"operation": "qualify", "method": method})
    return method


def evaluate(station, spec, name, role="client", **extra):
    root, _, tok = station
    return call(root, tok[role], {"operation": "evaluate", "task_id": name, **spec, **extra})


# ---------------------------------------------------------------- U09 / scale
def test_small_trained_temporal_method_is_refused_at_qualification_when_dominated_at_scale(station):
    method = teach(station, TEMPORAL_SMALL, "BEFORE", qualify=False)
    root, _, tok = station
    with pytest.raises(ValueError, match="dominated at scale"):
        call(root, tok["qualifier"], {"operation": "qualify", "method": method})
    assert call(root, tok["reader"], {"operation": "occupancy"})["big"]["populated"] == 0     # nothing written


def test_scale_trained_temporal_method_qualifies_and_cuts_work(station):
    base = evaluate(station, big_temporal(), "no-mem")["operation_cost"]
    method = teach(station, big_temporal(20, 40), "BEFORE")
    out = evaluate(station, big_temporal(), "with-mem")
    assert out["method_applied"] == method and out["chosen_strategy"] == "temporal_path"
    assert out["operation_cost"] < base


def test_semantic_small_trained_method_still_qualifies_it_is_not_dominated(station):
    method = teach(station, SEMANTIC_SMALL, "SUPPORTED")
    out = evaluate(station, big_semantic(), "sem")
    assert out["method_applied"] == method and out["chosen_strategy"] == "semantic_index"


def test_u09_compares_methods_by_common_scale_cost_not_raw_training_cost(station):
    """Two qualified methods in one context: the selector must follow qualification-scale cost."""
    root, _, tok = station
    good = teach(station, big_temporal(20, 40), "BEFORE")
    with transaction(root) as db:
        row = db.execute("SELECT evidence FROM qualifications_024 WHERE method=?", (good,)).fetchone()
    scale = json.loads(row[0])["scale"]
    assert scale and all(r["cost"] <= r["best"] * 5 / 4 for r in scale)
    out = evaluate(station, big_temporal(), "pick")
    assert out["method_applied"] == good


# ---------------------------------------------------------------- corrupt procedure
def tamper_procedure(root, method):
    with sqlite3.connect(root / "observations.sqlite3") as db:
        rec, = db.execute("SELECT record FROM procedures_023 WHERE id=?", (method,)).fetchone()
        d = json.loads(rec)
        d["training_cost"] += 9
        db.execute("UPDATE procedures_023 SET record=? WHERE id=?", (json.dumps(d), method))
    return rec, json.dumps(d)


def test_tampered_procedure_is_quarantined_with_evidence_and_trusted_no_memory_runs(station):
    root, _, tok = station
    sem = teach(station, SEMANTIC_SMALL, "SUPPORTED")
    healthy = teach(station, {"domain": "mathematics", "text": "x=17"}, "17")
    clean = evaluate(station, big_semantic(), "clean")
    tamper_procedure(root, sem)
    out = evaluate(station, big_semantic(), "after")
    assert out["answer"] == clean["answer"] == "SUPPORTED"                 # trusted algorithms still answer
    assert out["method_applied"] is None and out["operation_cost"] > clean["operation_cost"]
    q = out["memory_integrity"][0]
    assert q["method"] == sem and q["status"] == "QUARANTINED" and q["first_seen"] is True
    # the unrelated qualified method is untouched
    assert evaluate(station, {"domain": "mathematics", "text": "x=99"}, "m")["method_applied"] == healthy
    # forensic record kept; damaged row preserved byte-for-byte; second run reports it again
    listed = call(root, tok["reader"], {"operation": "quarantine"})["quarantined"]
    assert [x["method"] for x in listed] == [sem]
    again = evaluate(station, big_semantic(), "again")
    assert again["memory_integrity"][0]["first_seen"] is False and again["method_applied"] is None


def test_storage_block_corruption_still_fails_closed(station):
    root, _, _ = station
    teach(station, SEMANTIC_SMALL, "SUPPORTED")
    with sqlite3.connect(root / "observations.sqlite3") as db:
        db.execute("UPDATE blocks SET digest='corrupt' WHERE pool='big'")
    with pytest.raises(Rejected, match="RECORD_HASH_MISMATCH"):
        evaluate(station, big_semantic(), "bad")


# ---------------------------------------------------------------- typed derived evidence
def start_case(station, name="r1", role="client_derive"):
    root, _, tok = station
    t = WORK["training"]
    started = send(root, tok[role], "start025", run=name, problem=t["problem"])
    return started["decision"]["request"], t["measurements"]


def resume(station, request, run, measurement, role="client_derive"):
    root, _, tok = station
    return call(root, tok[role], {"authority": 0, "operation": "resume025", "run": run,
                                  "measurement": {"source": "sensor", "feature": request["feature"],
                                                  "request_id": request["id"], **measurement}})


def literal_to_derive(value):
    assert type(value) is int
    return {"domain": "mathematics", "text": f"3x+4={3 * value + 4}", "project": "answer"}


def test_derived_evidence_matches_literal_evidence_decision(station, tmp_path):
    req, truth = start_case(station)
    feature = req["feature"]
    if type(truth[feature]) is not int:
        pytest.skip("training workload feature is not an integer")
    derived = resume(station, req, "r1", {"derive": literal_to_derive(truth[feature])})
    other = (tmp_path / "other", initialize(tmp_path / "other"))
    request2, _ = start_case((other[0], other[1], {"client": other[1], "client_derive": other[1]}))
    literal = call(other[0], other[1], {"authority": 0, "operation": "resume025", "run": "r1", "measurement": {
        "source": "sensor", "feature": feature, "request_id": request2["id"], "value": truth[feature]}})
    strip = lambda d: {k: ({**v, "id": None} if k == "request" else v) for k, v in d.items()}   # ids bind the principal
    assert strip(derived["decision"]) == strip(literal["decision"]) and derived["activation_count"] == literal["activation_count"]
    d = derived["derivation"]
    assert d["interface"] == "derive/1" and d["projection_status"] == "PROJECTED" and d["cognitive_status"] == "ANSWERED"
    assert [f[0] for f in d["functions"]][0] == "L01" and "U10" in [f[0] for f in d["functions"]]


def test_inconclusive_derivation_keeps_the_request_pending_and_is_not_zero(station):
    req, _ = start_case(station)
    bad = {"domain": "temporal", "events": {}, "query": ["a", "b"], "project": "status"}
    out = resume(station, req, "r1", {"derive": bad})
    assert out["decision"]["status"] == "HOLD" and out["activation_count"] == 1
    assert out["derivation"]["projection"] is None and out["derivation"]["cognitive_status"] == "NEED_INFORMATION"
    assert out["derivation"]["projection_status"].startswith("INCONCLUSIVE")
    shown = send(station[0], station[2]["client_derive"], "show025", run="r1")
    assert shown["decision"]["status"] == "HOLD"


def test_semantic_result_is_preserved_not_flattened_to_a_number(station):
    out = evaluate(station, {**SEMANTIC_SMALL}, "s")
    from contract import derivation025
    value, why = derivation025.project({"domain": "semantic", "project": "answer"}, out)
    assert value == "SUPPORTED" and why == "OK"
    assert derivation025.project({"domain": "semantic", "project": "status"}, dict(out, status="NEED_INFORMATION", answer=None))[0] is None


def test_budget_exceeded_derivation_holds(station):
    req, truth = start_case(station)
    spec = {"domain": "temporal", "events": big_temporal()["events"], "before": big_temporal()["before"],
            "query": big_temporal()["query"], "project": "answer", "max_work": 1}
    out = resume(station, req, "r1", {"derive": spec})
    assert out["decision"]["status"] == "HOLD" and out["derivation"]["projection_status"].startswith("BUDGET_EXCEEDED")


@pytest.mark.parametrize("bad", [
    {"domain": "mathematics", "text": "x=1"},                                   # no projection named
    {"domain": "mathematics", "text": "x=1", "project": "answer", "authority": 1},
    {"domain": "mathematics", "text": "x=1", "project": "answer", "expected_answer": "1"},
    {"domain": "shell", "text": "x=1", "project": "answer"},
])
def test_derive_spec_boundaries(station, bad):
    req, _ = start_case(station)
    with pytest.raises(ValueError):
        resume(station, req, "r1", {"derive": bad})


def test_derive_needs_evaluate_scopes_and_ablation_needs_experiment(station):
    req, _ = start_case(station)
    good = {"domain": "mathematics", "text": "x=5", "project": "answer"}
    with pytest.raises(PermissionError):
        resume(station, req, "r1", {"derive": good}, role="client_nodrive")      # lacks evaluate scopes
    with pytest.raises(PermissionError):
        resume(station, req, "r1", {"derive": dict(good, disabled_functions=["U08"])}, role="client_derive")


def test_value_and_derive_together_rejected(station):
    req, _ = start_case(station)
    with pytest.raises(ValueError):
        resume(station, req, "r1", {"value": 1, "derive": {"domain": "mathematics", "text": "x=1", "project": "answer"}})


def test_duplicate_delivery_returns_same_receipt_and_computes_once(station):
    root, _, tok = station
    req, truth = start_case(station)
    feature = req["feature"]
    if type(truth[feature]) is not int:
        pytest.skip("not integer")
    m = {"derive": literal_to_derive(truth[feature])}
    first = resume(station, req, "r1", m)
    second = resume(station, req, "r1", m)
    assert first == second
    with sqlite3.connect(root / "observations.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM executions_023 WHERE id LIKE '%d025-%'").fetchone()[0] == 1


def test_derivation_ablation_changes_the_outcome_only_under_experiment_scope(station):
    req, truth = start_case(station, role="experimenter")
    feature = req["feature"]
    if type(truth[feature]) is not int:
        pytest.skip("not integer")
    off = resume(station, req, "r1", {"derive": dict(literal_to_derive(truth[feature]), disabled_functions=["U10"])},
                 role="experimenter")
    assert off["decision"]["status"] == "HOLD" and off["derivation"]["projection"] is None
    assert off["derivation"]["cognitive_status"] == "NEED_INFORMATION"


# ---------------------------------------------------------------- authenticated HTTP path
def test_http_evidence_with_derive_round_trip(tmp_path):
    root = tmp_path / "http"
    admin = initialize(root)
    client = issue(root, {"principal": "web", "scopes": ["procedure:execute", "observation:write", "big:read",
                                                         "small:read", "procedure:read"], "owners": []})["token"]
    proc = subprocess.Popen([sys.executable, "muse024.py", "--state", str(root), "serve", "--port", "0"], cwd=ROOT,
                            stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
                            env=dict(os.environ, MUSE024_TOKEN=admin))
    try:
        url = "http://127.0.0.1:" + str(json.loads(proc.stdout.readline())["listening"])

        def post(path, body):
            r = Request(url + path, json.dumps(body).encode(), {"Content-Type": "application/json",
                                                                 "Authorization": f"Bearer {client}"})
            with urlopen(r, timeout=20) as resp:
                return json.load(resp)
        t = WORK["training"]
        start = post("/v1/investigations", {"authority": 0, "run": "h1", "problem": t["problem"]})
        req = start["decision"]["request"]
        v = t["measurements"][req["feature"]]
        if type(v) is not int:
            pytest.skip("not integer")
        out = post("/v1/investigations/h1/evidence", {"authority": 0, "measurement": {
            "source": "sensor", "feature": req["feature"], "request_id": req["id"], "derive": literal_to_derive(v)}})
        assert out["derivation"]["projection_status"] == "PROJECTED" and out["activation_count"] == 2
    finally:
        proc.terminate(); proc.wait(timeout=15); proc.stdout.close()
