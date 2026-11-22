from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from eval025.run import issue_qualifier, send
from muse024 import initialize
from tests.test_claude_integrated025 import WORK, measurement
from tests.test_claude_integrated025 import app as app

ROOT = Path(__file__).resolve().parents[1]
CAPS = ["procedure:execute", "procedure:read", "procedure:write", "observation:write",
        "big:read", "small:read", "big:write", "small:write", "audit:raw", "experiment"]


def make_agent(root, admin, name="agent"):
    return send(root, admin, "issue", principal=name, scopes=CAPS, owners=["L30"])["token"]


def settings(root, admin, suit, mask, previous=0, agent="agent", **policy):
    return send(root, admin, "agent_configure", agent=agent, expected_version=previous,
                configuration={"suit_enabled": suit, "mask_enabled": mask, "policy": policy})["configuration"]


def frame(seq, level, source="fixture:video", stream="movie"):
    pixels = bytes([level]) * 768
    return {"stream": stream, "seq": seq, "time_ms": seq * 40, "source": source,
            "gray": base64.b64encode(pixels).decode(), "frame_sha256": hashlib.sha256(pixels).hexdigest()}


@pytest.mark.parametrize("suit,mask", [(True, True), (True, False), (False, True), (False, False)])
def test_four_modes_survive_process_exit(tmp_path, suit, mask):
    root = tmp_path / "state"
    admin = initialize(root)
    token = make_agent(root, admin)
    settings(root, admin, suit, mask)
    case = WORK["training"]
    result = send(root, token, "agent_start", run="logic", problem=case["problem"])
    assert result["suit"]["decision"] == ("ADMIT" if suit else "BYPASSED")
    assert result["decision"]["status"] == ("HOLD" if mask else "ABSTAIN")
    assert bool(result["trace"]) is mask
    child = subprocess.run([sys.executable, "muse024.py", "--state", str(root), "call", "--json",
                           '{"operation":"agent_show","authority":0,"run":"logic"}'],
                           cwd=ROOT, capture_output=True, text=True, check=True,
                           env=dict(os.environ, MUSE024_TOKEN=token))
    import json
    assert json.loads(child.stdout)["decision"] == result["decision"]
    send(root, token, "agent_stream", stream="movie", source="fixture:video", continuous=True)
    frames = [send(root, token, "agent_frame", **frame(i, v)) for i, v in enumerate((10, 150, 151, 10, 11))]
    assert bool(sum(bool(r["trace"]) for r in frames)) is mask
    if mask:
        assert frames[1]["decision"]["status"] == "HOLD"
        assert frames[2]["decision"]["answer"] == "change"
        assert [r["operation"] for r in frames[4]["memory_accesses"]] == ["read", "read", "write", "write"]
    assert len(send(root, token, "agent_inventory")["blocks"]) == (4 if mask else 0)


def test_http_switch_keeps_hold_and_exactly_once_release(app):
    token = make_agent(app.root, app.token)
    case = WORK["training"]
    config = {"authority": 0, "agent": "agent", "expected_version": 0,
              "configuration": {"suit_enabled": True, "mask_enabled": True}}
    assert app.request("/v1/agent/configuration", config, token)[0] == 403
    assert app.request("/v1/agent/configuration", config)[0] == 200
    request = {"authority": 0, "run": "held", "problem": case["problem"]}
    code, start = app.request("/v1/agent/runs", request, token)
    assert code == 200 and start["decision"]["status"] == "HOLD"
    config["expected_version"] = 1
    config["configuration"] = {"suit_enabled": False, "mask_enabled": False}
    assert app.request("/v1/agent/configuration", config)[0] == 200
    assert app.request("/v1/agent/configuration", config)[0] == 400
    assert app.request("/v1/investigations", request, token)[0] == 403
    app.stop(hard=True)
    app.start(recover=True)
    body = measurement(start["decision"], case)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: app.request("/v1/agent/runs/held/evidence", body, token), range(4)))
    assert all(row == rows[0] for row in rows)
    reply = rows[0][1]
    assert reply["configuration_version"] == 1 and len(reply["trace"]) > 0
    logs = send(app.root, app.token, "audit", stream="operation", raw=True)
    assert sum(r["record"].get("request_id") == reply["request_id"] for r in logs["records"]) == 1
    _, off = app.request("/v1/agent/runs", dict(request, run="new"), token)
    assert off["configuration_version"] == 2 and off["trace"] == []


def test_policy_source_and_mandatory_security(tmp_path):
    root = tmp_path / "state"
    admin = initialize(root)
    actor = make_agent(root, admin)
    settings(root, admin, True, True, source_prefixes=["allowed:"])
    refused = send(root, actor, "agent_stream", stream="movie", source="fixture:video", continuous=True)
    assert refused["decision"]["status"] == "REFUSED"
    settings(root, admin, False, True, previous=1)
    with pytest.raises(ValueError):
        send(root, actor, "agent_stream", stream="movie", source="unverified:fake", continuous=True)
    assert send(root, actor, "agent_stream", stream="movie", source="fixture:video", continuous=True)["decision"]["status"] == "OPEN"
    with pytest.raises(ValueError):
        send(root, actor, "agent_frame", **frame(1, 10))
    with pytest.raises(ValueError):
        send(root, actor, "agent_frame", **dict(frame(0, 10), frame_sha256="fake"))
    with pytest.raises(PermissionError):
        send(root, actor, "agent_start", run="x", problem=dict(WORK["training"]["problem"], authority=1))


def test_unmanaged_pending_requires_resolution_before_enrollment(tmp_path):
    root = tmp_path / "state"
    admin = initialize(root)
    actor = make_agent(root, admin)
    send(root, actor, "start025", run="legacy", problem=WORK["training"]["problem"])
    with pytest.raises(ValueError, match="legacy HOLD"):
        settings(root, admin, True, True)
    send(root, actor, "expire025", run="legacy")
    settings(root, admin, True, True)


def test_stream_mode_and_order_survive_restart_and_competing_writes(app):
    token = make_agent(app.root, app.token)
    settings(app.root, app.token, True, True)
    def post(row):
        body = {"authority": 0, **{k: v for k, v in row.items() if k != "stream"}}
        return app.request("/v1/agent/streams/movie/frame", body, token)
    code, opening = app.request("/v1/agent/streams", {
        "authority": 0, "stream": "movie", "source": "fixture:video", "continuous": True}, token)
    assert code == 200 and opening["configuration_version"] == 1
    assert post(frame(0, 10))[1]["decision"]["answer"] == "initial"
    assert post(frame(1, 150))[1]["decision"]["status"] == "HOLD"
    settings(app.root, app.token, False, False, previous=1)
    app.stop(hard=True)
    app.start(recover=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(post, (frame(2, 151), frame(2, 151), frame(2, 151), frame(2, 152))))
    assert len([x for x in results if x[0] == 400]) in (1, 3)
    accepted = [value for status, value in results if status == 200]
    assert accepted and all(r == accepted[0] for r in accepted)
    assert accepted[0]["configuration_version"] == 1
    assert len(send(app.root, token, "agent_inventory")["blocks"]) == 2


def test_mask_off_has_no_learning_writes_and_raw_memory_is_closed(tmp_path):
    root = tmp_path / "state"
    admin = initialize(root)
    actor = make_agent(root, admin)
    settings(root, admin, False, False)
    send(root, actor, "agent_start", run="off", problem=WORK["training"]["problem"])
    assert send(root, actor, "agent_learn", run="off")["decision"]["reason"] == "mask_disabled"
    with pytest.raises(PermissionError):
        send(root, actor, "memory_write", pool="big", owner="L30")
    assert not send(root, actor, "agent_inventory")["blocks"]
    assert send(root, admin, "occupancy")["big"]["populated"] == 0


def test_suit_frame_budget_and_qualifier_owner(tmp_path):
    root = tmp_path / "state"
    admin = initialize(root)
    actor = make_agent(root, admin)
    settings(root, admin, True, False, max_frames=1)
    send(root, actor, "agent_stream", stream="movie", source="fixture:video", continuous=True)
    assert send(root, actor, "agent_frame", **frame(0, 1))["decision"]["status"] == "OBSERVED"
    assert send(root, actor, "agent_frame", **frame(1, 1))["decision"]["reason"] == "suit_frame_budget_exhausted"
    bad = send(root, admin, "issue", principal="wrong-owner",
               scopes=["procedure:qualify", "procedure:read", "big:write", "small:write"], owners=["L30"])["token"]
    with pytest.raises(PermissionError, match="qualification owner"):
        send(root, bad, "qualify025", method="0123")
