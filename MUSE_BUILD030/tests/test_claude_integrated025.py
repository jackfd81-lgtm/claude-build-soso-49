"""Exercise the integrated application from independent OS processes."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from eval025.run import issue_qualifier, send
from muse024 import call, initialize

ROOT = Path(__file__).resolve().parents[1]
WORK = json.loads((ROOT / "eval025/workload.json").read_text())


class App:
    def __init__(self, root):
        self.root = root
        self.token = initialize(root)
        self.process = None

    def start(self, recover=False):
        args = [sys.executable, "muse024.py", "--state", str(self.root), "serve", "--port", "0"]
        if recover:
            args.append("--recover")
        self.process = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                        text=True, env=dict(os.environ, MUSE024_TOKEN=self.token))
        self.url = "http://127.0.0.1:" + str(json.loads(self.process.stdout.readline())["listening"])
        assert self.request("/health", token="")[1]["status"] == "ready"

    def stop(self, hard=False):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill() if hard else self.process.terminate()
            assert self.process.wait(timeout=15) == (-signal.SIGKILL if hard else 0)
            self.process.stdout.close()
            self.process = None

    def request(self, path, data=None, token=None):
        headers = {"Content-Type": "application/json"}
        token = self.token if token is None else token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = Request(self.url + path, json.dumps(data).encode() if data is not None else None, headers)
        try:
            with urlopen(req, timeout=15) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def cli(self, task):
        proc = subprocess.run([sys.executable, "muse024.py", "--state", str(self.root), "call", "--json", "-"],
                              input=json.dumps(task), capture_output=True, text=True, check=True, cwd=ROOT,
                              env=dict(os.environ, MUSE024_TOKEN=self.token))
        return json.loads(proc.stdout)


@pytest.fixture
def app(tmp_path):
    service = App(tmp_path / "application")
    service.start()
    try:
        yield service
    finally:
        service.stop()


def measurement(decision, case):
    req = decision["request"]
    return {"authority": 0, "measurement": {"source": "fixture:local", "feature": req["feature"],
            "request_id": req["id"], "value": case["measurements"][req["feature"]]}}


def answer(app, run, result, case):
    while result["decision"]["status"] == "HOLD":
        code, result = app.request(f"/v1/investigations/{run}/evidence", measurement(result["decision"], case))
        assert code == 200
    return result


def test_restart_and_transfer_blends_cli_and_http(app):
    training = WORK["training"]
    held = WORK["heldout"][0]
    root = app.root
    start = app.cli({"authority": 0, "operation": "start025", "run": "training", "problem": training["problem"]})
    assert start["decision"]["status"] == "HOLD"
    app.stop(hard=True)
    app.start(recover=True)
    assert app.request("/v1/investigations/training")[1]["decision"] == start["decision"]
    final = answer(app, "training", start, training)
    assert final["decision"]["answer"] == training["answer"]
    assert any(event.get("function") == "U13" for event in final["trace"])
    code, candidate = app.request("/v1/investigations/training/learn", {"authority": 0})
    assert code == 200
    method = candidate["decision"]["candidate_method"]
    assert app.request(f"/v1/methods/{method}/qualify", {"authority": 0})[0] == 403
    qualifier = issue_qualifier(root, app.token)
    assert app.request(f"/v1/methods/{method}/qualify", {"authority": 0}, qualifier)[0] == 200
    app.stop()
    app.start()
    code, start = app.request("/v1/investigations", {"authority": 0, "run": "transfer", "problem": held["problem"]})
    assert code == 200 and len(start["contributing_blocks"]) == 2
    assert answer(app, "transfer", start, held)["decision"]["answer"] == held["answer"]


def test_concurrent_http_and_cli_release_exactly_once(app):
    case = WORK["training"]
    _, start = app.request("/v1/investigations", {"authority": 0, "run": "race", "problem": case["problem"]})
    data = measurement(start["decision"], case)
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(app.request, "/v1/investigations/race/evidence", data) for _ in range(4)]
        cli = pool.submit(app.cli, dict(data, run="race", operation="resume025"))
        receipt = cli.result()
        assert all(future.result() == (200, receipt) for future in futures)
    assert receipt["activation_count"] == 2
    raw = send(app.root, app.token, "audit", stream="operation", raw=True)
    assert sum(r["record"].get("event") == receipt["event"] for r in raw["records"]) == 1
    assert len({e["id"] for e in receipt["trace"]}) == len(receipt["trace"])


@pytest.mark.parametrize("point,committed", [("before_pathway_025_commit", False), ("after_pathway_025_commit", True)])
def test_kill_during_release_and_retry(app, point, committed):
    case = WORK["training"]
    _, start = app.request("/v1/investigations", {"authority": 0, "run": "kill", "problem": case["problem"]})
    body = measurement(start["decision"], case)
    task = dict(body, run="kill", operation="resume025")
    child = """
import json, os, signal, sys
from muse024 import call
def fault(point):
    if point == sys.argv[2]:
        os.kill(os.getpid(), signal.SIGKILL)
call(sys.argv[1], os.environ['MUSE024_TOKEN'], json.load(sys.stdin), fault=fault)
"""
    result = subprocess.run([sys.executable, "-c", child, str(app.root), point], input=json.dumps(task),
                            text=True, cwd=ROOT, env=dict(os.environ, MUSE024_TOKEN=app.token))
    assert result.returncode == -signal.SIGKILL
    recovery = send(app.root, app.token, "recover_audit")
    assert bool(recovery["recovered_attempts"]) is not committed
    _, current = app.request("/v1/investigations/kill")
    assert current["activation_count"] == (2 if committed else 1)
    assert app.request("/v1/investigations/kill/evidence", body)[1]["activation_count"] == 2


def test_authentication_authority_and_evidence_gate(app):
    case = WORK["training"]
    path = "/v1/investigations"
    data = {"authority": 0, "run": "own", "problem": case["problem"]}
    assert app.request(path, data, "")[0] == 401
    assert app.request(path, dict(data, authority=False))[0] == 403
    assert app.request(path, dict(data, problem=dict(case["problem"], authority=1)))[0] == 403
    assert app.request("/v1/methods", data)[0] == 404
    code, start = app.request(path, data)
    assert code == 200
    other = send(app.root, app.token, "issue", principal="other", scopes=["procedure:execute"], owners=[])["token"]
    assert app.request(path + "/own", token=other)[0] == 404
    payload = measurement(start["decision"], case)
    payload["measurement"]["source"] = "unverified:fixture"
    assert app.request(path + "/own/evidence", payload)[0] == 400
    assert app.request(path + "/own")[1]["activation_count"] == 1


def test_application_endpoint_preserves_causal_ablation(app):
    case = WORK["heldout"][0]
    _, start = app.request("/v1/investigations", {"authority": 0, "run": "full", "problem": case["problem"]})
    assert answer(app, "full", start, case)["decision"]["answer"] == case["answer"]
    code, ablated = app.request("/v1/investigations", {"authority": 0, "run": "ablated",
                                "problem": case["problem"], "disabled_functions": ["U13"]})
    assert code == 200 and answer(app, "ablated", ablated, case)["decision"]["status"] == "ABSTAIN"
