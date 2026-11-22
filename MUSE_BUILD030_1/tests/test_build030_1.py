import json
import sqlite3
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from contract.http025 import Server, check_state
from muse024 import initialize
from qualification_030 import verify


def test_readiness_rejects_missing_and_uninitialized_database(tmp_path):
    path = tmp_path / "observations.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        check_state(tmp_path)
    assert not path.exists()
    path.touch()
    with pytest.raises(sqlite3.OperationalError):
        check_state(tmp_path)


def test_running_http_service_reports_state_loss(tmp_path):
    root = tmp_path / "state"
    initialize(root)
    with Server(root, 0) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        route = f"http://127.0.0.1:{server.server_port}/health"
        try:
            with urlopen(route, timeout=3) as response:
                assert json.load(response) == {"status": "ready", "authority": 0}
            path = root / "observations.sqlite3"
            path.rename(root / "offline.sqlite3")
            with pytest.raises(HTTPError) as error:
                urlopen(route, timeout=3)
            with error.value as response:
                assert response.code == 503
                assert json.load(response) == {"status": "unavailable", "authority": 0}
        finally:
            server.shutdown()
            worker.join()


def test_verifier_preserves_timeout_evidence(tmp_path, monkeypatch):
    def time_out(command, **kwargs):
        raise verify.subprocess.TimeoutExpired(command, 180, output=b"partial", stderr=b"detail")
    monkeypatch.setattr(verify.subprocess, "run", time_out)
    assert verify.verify(tmp_path) == 1
    assert (tmp_path / "phase_d.stdout").read_bytes() == b"partial"
    assert (tmp_path / "phase_d.stderr").read_bytes() == b"detail"
    assert json.loads((tmp_path / "summary.json").read_text())["checks"] == {"phase_d": "FAIL"}
