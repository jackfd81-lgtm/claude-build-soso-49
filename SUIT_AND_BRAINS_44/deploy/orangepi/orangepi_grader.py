"""Orange Pi Zero 2W Independent Tester & Grader Harness (local simulation & dual-node HTTP)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import statistics
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from crampu.build030_bridge import Merged49BrainsCramAdapter
from crampu.controller import AuthorizationError, CRAMPUMemoryController, ConflictError


def run_remote_http_grading(base_url: str, grader_token: str, session_id: str = "opi-remote-1") -> dict:
    """Submit bounded workload & negative auth check over HTTP to Pi 5 BoundedGraderServer."""
    t0 = time.perf_counter()
    req = Request(
        f"{base_url.rstrip('/')}/grader/submit",
        data=json.dumps({
            "session_id": session_id,
            "goal": "remote-grading",
            "agent_id": "L01",
            "family": "BIG49",
            "index": 0,
            "payload": {"content": "opi-remote-probe", "relation": "ASSOCIATED_WITH"},
        }).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Grader-Token": grader_token},
        method="POST",
    )
    with urlopen(req, timeout=5) as resp:
        ok_body = json.loads(resp.read().decode("utf-8"))
    lat_ms = (time.perf_counter() - t0) * 1000.0

    # Verify bad token is rejected with 401
    bad_rejected = False
    bad_req = Request(
        f"{base_url.rstrip('/')}/grader/submit",
        data=b"{}",
        headers={"Content-Type": "application/json", "X-Grader-Token": "wrong-token"},
        method="POST",
    )
    try:
        urlopen(bad_req, timeout=5)
    except HTTPError as err:
        bad_rejected = err.code == 401

    return {
        "mode": "remote_http",
        "authorized_submit": ok_body.get("status") == "OK",
        "invalid_token_rejected": bad_rejected,
        "latency_ms": round(lat_ms, 3),
        "status": "PASS" if (ok_body.get("status") == "OK" and bad_rejected) else "FAIL",
    }


def run_independent_grading(work_dir: Path, admin_token: str, iterations: int = 20) -> dict:
    warm = work_dir / "warm"
    cool = work_dir / "cool"
    brains = work_dir / "brains"

    host_ctrl = CRAMPUMemoryController(warm, cool, admin_token)
    host_ctrl.register_block_owner(0, "L01", admin_token)
    host_ctrl.register_block_owner(1, "L02", admin_token)
    host_ctrl.register_block_owner(1600, "L01", admin_token)
    host_ctrl.close()

    grader_ctrl = CRAMPUMemoryController(warm, cool, "grader-dummy-non-matching-token-0000")
    adapter = Merged49BrainsCramAdapter(grader_ctrl, brains)

    latencies_ms: list[float] = []
    ru_start = resource.getrusage(resource.RUSAGE_SELF)
    t_start = time.perf_counter()

    for i in range(iterations):
        t0 = time.perf_counter()
        adapter.write_and_sync("L01", "BIG49", 0, {"content": f"grader-step-{i}", "relation": "ASSOCIATED_WITH"})
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    isolation_ok = False
    try:
        adapter.read("L02", "BIG49", 0)
    except AuthorizationError:
        try:
            adapter.write_and_sync("L01", "BIG49", 1, {"content": "cross-agent-tamper"})
        except AuthorizationError:
            isolation_ok = True

    revision_consistency_ok = False
    try:
        grader_ctrl.write_hot("L01", 1600, {"content": "rev-check-1"})
        tx_stale = grader_ctrl.stage_to_warm("L01", 1600)
        adapter.write_and_sync("L01", "SMALL49", 0, {"content": "rev-check-2"})
        grader_ctrl.commit_to_cool("L01", tx_stale)
    except ConflictError:
        revision_consistency_ok = True

    adapter.verify_all()
    adapter.close()
    grader_ctrl.close()

    grader_ctrl_2 = CRAMPUMemoryController(warm, cool, "grader-dummy-non-matching-token-0000")
    adapter_2 = Merged49BrainsCramAdapter(grader_ctrl_2, brains)
    adapter_2.verify_all()
    recovered_payload = adapter_2.read("L01", "BIG49", 0)
    restart_recovery_ok = recovered_payload["payload"]["content"] == f"grader-step-{iterations - 1}"
    adapter_2.close()
    grader_ctrl_2.close()

    ru_end = resource.getrusage(resource.RUSAGE_SELF)
    wall_s = time.perf_counter() - t_start

    return {
        "harness": "Orange Pi Zero 2W Independent Grader (local simulation)",
        "hardware_verified": False,
        "iterations": iterations,
        "latency_p50_ms": round(statistics.median(latencies_ms), 3),
        "latency_max_ms": round(max(latencies_ms), 3),
        "wall_seconds": round(wall_s, 4),
        "cpu_user_seconds": round(ru_end.ru_utime - ru_start.ru_utime, 4),
        "cpu_sys_seconds": round(ru_end.ru_stime - ru_start.ru_stime, 4),
        "max_rss_kb": ru_end.ru_maxrss,
        "checks": {
            "authorized_workload_and_mirror": len(latencies_ms) == iterations,
            "agent_isolation": isolation_ok,
            "revision_consistency": revision_consistency_ok,
            "restart_recovery": restart_recovery_ok,
        },
        "status": "PASS" if (isolation_ok and revision_consistency_ok and restart_recovery_ok) else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Orange Pi independent CRAM-PU grader")
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--remote-url", type=str, default=None)
    parser.add_argument("--token", type=str, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    if args.remote_url:
        report = run_remote_http_grading(args.remote_url, args.token or "")
    else:
        with tempfile.TemporaryDirectory() as td:
            report = run_independent_grading(Path(td), "pi5-host-provision-secret-0001", iterations=args.iterations)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
