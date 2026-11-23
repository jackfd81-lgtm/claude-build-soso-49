"""PyRIT-compatible HTTP Target Adapter for Build 040 BoundedGraderServer.

Use on a networked host or Raspberry Pi 5 where `pyrit` is installed:
    target = Build040GraderHttpTarget("http://127.0.0.1:8030", os.environ["MUSE_GRADER_TOKEN"])
"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class Build040GraderHttpTarget:
    """Sends scoped agent payloads to POST /grader/submit with X-Grader-Token."""

    def __init__(self, base_url: str, grader_token: str, *, agent_id: str = "L01", family: str = "BIG49", index: int = 0) -> None:
        self.base_url = base_url.rstrip("/")
        self.grader_token = grader_token
        self.agent_id = agent_id
        self.family = family
        self.index = index

    def send_probe(self, session_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        body = json.dumps({
            "session_id": session_id,
            "goal": "pyrit-interrogation",
            "agent_id": self.agent_id,
            "family": self.family,
            "index": self.index,
            "payload": payload,
        }).encode("utf-8")
        req = Request(
            f"{self.base_url}/grader/submit",
            data=body,
            headers={"Content-Type": "application/json", "X-Grader-Token": self.grader_token},
            method="POST",
        )
        try:
            with urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8"))
