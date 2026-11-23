"""MUSE Build 040 Cognitive Learning Qualification Runner (B040-LEARN-TRANSFER-105-LIVE-001)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from threading import Thread
import time
from typing import Any

from build040_runtime import (
    Brains49Runtime,
    ExperienceEngine,
    OfflineAdvisoryProvider,
    OnlineAIGateway,
    ReplayProvider,
    SoSoCloset,
    SoSoContinuity,
    SoSoSuitRuntime,
)
from build040_runtime.grader_endpoint import BoundedGraderServer
from build040_runtime.learning_loop import AssistedLearningLoop
from build040_runtime.online_ai import GoogleGeminiProvider
from build040_runtime.precision_105 import BlockedGenerationError, Precision105Constructor
from crampu.build030_bridge import Merged49BrainsCramAdapter
from crampu.controller import AuthorizationError, CRAMPUMemoryController
from deploy.orangepi.orangepi_grader import run_remote_http_grading

ADMIN = "b040-qual-admin-secret-040000"


def run_qualification(v2_challenge_path: Path | None = None) -> dict[str, Any]:
    t_start = time.perf_counter()
    report: dict[str, Any] = {
        "run_id": "B040-LEARN-TRANSFER-105-LIVE-001",
        "environment": {
            "python": sys.version,
            "os": platform.platform(),
            "gemini_api_key_present": bool(os.environ.get("GEMINI_API_KEY")),
            "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
        },
        "phases": {},
    }

    # Tasks with different structures/features where 'z_opt' has optimal [1, 2] split
    tasks = {
        "Task_A": {
            "H1": {"a_flat": 1, "z_opt": "hit"},
            "H2": {"a_flat": 2, "z_opt": "miss"},
            "H3": {"a_flat": 3, "z_opt": "miss"},
        },
        "Task_B": {
            "P1": {"b_noise": "x", "y_opt": 10},
            "P2": {"b_noise": "y", "y_opt": 20},
            "P3": {"b_noise": "z", "y_opt": 20},
        },
        "Task_C": {
            "M1": {"c_uniq": "a", "x_opt": True},
            "M2": {"c_uniq": "b", "x_opt": False},
            "M3": {"c_uniq": "c", "x_opt": False},
        },
    }
    expected_opt = {"Task_A": "z_opt", "Task_B": "y_opt", "Task_C": "x_opt"}

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ctrl = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
        for idx in (0, 1, 1600):
            ctrl.register_block_owner(idx, "L01", ADMIN)
        adapter = Merged49BrainsCramAdapter(ctrl, root / "brains")
        cont = SoSoContinuity(root / "soso.sqlite3")
        suit = SoSoSuitRuntime(adapter)
        suit.authorize_agent("L01", {"memory.read", "memory.write"}, allowed_block_ids={0, 1, 1600})
        closet = SoSoCloset(cont, suit)
        brains = Brains49Runtime(cont, suit, root / "muse_state")
        saved_muse_token = brains._muse_token
        exp = ExperienceEngine(cont, suit, brains)

        # Phase 2: Cold 49 Brains Baseline (no Gemini, fresh memory)
        cold_loop = AssistedLearningLoop(cont, suit, gateway=None)
        cold_results = {}
        cold_hits = 0
        for name, cands in tasks.items():
            r = cold_loop.run_unassisted(f"cold-{name}", "L01", cands, {}, memory_block=None)
            hit = r["selected_feature"] == expected_opt[name]
            cold_hits += int(hit)
            cold_results[name] = {**r, "optimal_hit": hit}
        report["phases"]["phase_2_cold_baseline"] = {
            "status": "VERIFIED_EXECUTED",
            "optimal_hits": cold_hits,
            "total": len(tasks),
            "results": cold_results,
        }

        # Phase 3: Limited Teaching (Live Gemini check + explicitly labeled Replay teaching simulation)
        live_gemini_status = "BLOCKED_LIVE_API"
        try:
            GoogleGeminiProvider().generate("ping", timeout_s=1.0)
            live_gemini_status = "VERIFIED_EXECUTED"
        except Exception as exc:
            live_gemini_error = str(exc)

        rp = ReplayProvider()
        rp.record(
            "Provide a diagnostic hint for discriminating partitions: {'a_flat': [1, 1, 1], 'z_opt': [1, 2]}",
            "Prefer binary [1, 2] partition to isolate one hypothesis per probe.",
        )
        sim_gw = OnlineAIGateway(primary=rp, fallback=OfflineAdvisoryProvider())
        teach_loop = AssistedLearningLoop(cont, suit, gateway=sim_gw)
        history = [
            {"before": 3, "after": 3, "profile": [1, 1, 1]},
            {"before": 3, "after": 1, "profile": [1, 2]},
        ]
        learned = teach_loop.assist_and_learn("teach-1", "L01", tasks["Task_A"], {}, history, target_block=("BIG49", 0))
        report["phases"]["phase_3_teaching"] = {
            "live_gemini_status": live_gemini_status,
            "live_gemini_error": live_gemini_error if live_gemini_status == "BLOCKED_LIVE_API" else None,
            "simulated_teaching_status": "VERIFIED_EXECUTED",
            "teaching_source": "replay_cache (explicitly NOT live_gemini)",
            "learned": learned,
        }

        # Close and restart to prove persistence across process/controller restart
        adapter.close(); ctrl.close(); cont.close()

        ctrl2 = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
        adapter2 = Merged49BrainsCramAdapter(ctrl2, root / "brains")
        cont2 = SoSoContinuity(root / "soso.sqlite3")
        suit2 = SoSoSuitRuntime(adapter2)
        suit2.authorize_agent("L01", {"memory.read", "memory.write"}, allowed_block_ids={0, 1, 1600})
        closet2 = SoSoCloset(cont2, suit2)
        brains2 = Brains49Runtime(cont2, suit2, root / "muse_state", muse_token=saved_muse_token)
        exp2 = ExperienceEngine(cont2, suit2, brains2)

        # Phase 4: Learned Memory (Gemini Off) vs Memory Disabled Control (Gemini Off)
        retry_loop = AssistedLearningLoop(cont2, suit2, gateway=None)
        learned_hits = 0
        ablation_hits = 0
        retry_results = {}
        ablation_results = {}
        for name, cands in tasks.items():
            r_mem = retry_loop.run_unassisted(f"retry-{name}", "L01", cands, {}, memory_block=("BIG49", 0))
            hit_mem = r_mem["selected_feature"] == expected_opt[name]
            learned_hits += int(hit_mem)
            retry_results[name] = {**r_mem, "optimal_hit": hit_mem}

            r_nomem = retry_loop.run_unassisted(f"ablate-{name}", "L01", cands, {}, memory_block=None)
            hit_nomem = r_nomem["selected_feature"] == expected_opt[name]
            ablation_hits += int(hit_nomem)
            ablation_results[name] = {**r_nomem, "optimal_hit": hit_nomem}

        report["phases"]["phase_4_retention_and_ablation"] = {
            "status": "VERIFIED_EXECUTED",
            "learned_memory_gemini_off_hits": f"{learned_hits}/{len(tasks)}",
            "memory_disabled_gemini_off_hits": f"{ablation_hits}/{len(tasks)}",
            "retry_results": retry_results,
            "ablation_results": ablation_results,
        }

        # Phase 5: Unseen Transfer + Inappropriate-Method Rejection
        unseen_tasks = {
            "Unseen_1": ({"F1": {"aa_drift": 1, "zz_pressure": "hi"}, "F2": {"aa_drift": 2, "zz_pressure": "lo"}, "F3": {"aa_drift": 3, "zz_pressure": "lo"}}, "zz_pressure"),
            "Unseen_2": ({"G1": {"aa_temp": 10, "zz_valve": "open"}, "G2": {"aa_temp": 20, "zz_valve": "shut"}, "G3": {"aa_temp": 30, "zz_valve": "shut"}}, "zz_valve"),
        }
        unseen_mem_hits = 0
        unseen_nomem_hits = 0
        for uname, (ucands, uopt) in unseen_tasks.items():
            rm = retry_loop.run_unassisted(f"unseen-mem-{uname}", "L01", ucands, {}, memory_block=("BIG49", 0))
            rn = retry_loop.run_unassisted(f"unseen-nomem-{uname}", "L01", ucands, {}, memory_block=None)
            unseen_mem_hits += int(rm["selected_feature"] == uopt)
            unseen_nomem_hits += int(rn["selected_feature"] == uopt)

        # Inappropriate-method rejection: 4 hypotheses with [2, 2] and [1, 1, 1, 1] -> [1, 2] absent!
        inappropriate = {
            "Q1": {"f1": "a", "f2": 1},
            "Q2": {"f1": "a", "f2": 2},
            "Q3": {"f1": "b", "f2": 3},
            "Q4": {"f1": "b", "f2": 4},
        }
        rej = retry_loop.run_unassisted("unseen-reject", "L01", inappropriate, {}, memory_block=("BIG49", 0))
        report["phases"]["phase_5_unseen_transfer"] = {
            "status": "VERIFIED_EXECUTED",
            "unseen_with_memory_hits": f"{unseen_mem_hits}/{len(unseen_tasks)}",
            "unseen_without_memory_hits": f"{unseen_nomem_hits}/{len(unseen_tasks)}",
            "inappropriate_method_rejected": (not rej["transferred_from_memory"] and not rej["used_learned_method"]),
        }

        # Phase 6: Blind HIST-GEO-CULTURE-105-v2
        p6_status = "NOT_RUN"
        p6_error = None
        if v2_challenge_path and v2_challenge_path.exists():
            for idx in range(2, 15):
                ctrl2.register_block_owner(idx, "L01", ADMIN)
            suit2.authorize_agent("L01", {"memory.read", "memory.write"}, allowed_block_ids=set(range(15)) | {1600})
            live_gw = OnlineAIGateway(primary=GoogleGeminiProvider(), fallback=OfflineAdvisoryProvider(), max_retries=0)
            ctor = Precision105Constructor(cont2, suit2, brains2, gateway=live_gw)
            try:
                ctor.construct(v2_challenge_path, session_id="b040-v2-blind", agent_id="L01")
                p6_status = "VERIFIED_EXECUTED"
            except BlockedGenerationError as exc:
                p6_status = "BLOCKED_LIVE_API"
                p6_error = str(exc)
        report["phases"]["phase_6_blind_hist_geo_culture_105_v2"] = {
            "status": p6_status,
            "error": p6_error,
        }

        # Phase 7: Full Subsystem Coherence (success + failure injection)
        closet2.submit_task("p7-coh", "full coherence")
        exp2.record_experience("p7-coh", "L01", "coh", "sig", "probe_pressure", "SUCCESS", family="SMALL49", index=0)
        srv = BoundedGraderServer(("127.0.0.1", 0), closet2, "p7-opi-tok")
        holder: dict[str, Any] = {}
        t = Thread(target=lambda: holder.update({"res": run_remote_http_grading(f"http://127.0.0.1:{srv.server_port}", "p7-opi-tok")}), daemon=True)
        t.start()
        srv.handle_request(); srv.handle_request()
        t.join(timeout=5)
        srv.server_close()

        unauthorized_denied = False
        try:
            suit2.execute_memory_write("L02", "BIG49", 0, {"content": "tamper"})
        except AuthorizationError:
            unauthorized_denied = True

        report["phases"]["phase_7_coherence"] = {
            "status": "VERIFIED_EXECUTED",
            "loopback_http_grader": holder["res"]["status"],
            "unauthorized_write_denied": unauthorized_denied,
            "physical_orangepi_hardware": "IMPLEMENTED_UNVERIFIED",
        }

        adapter2.close(); ctrl2.close(); cont2.close()

    report["elapsed_s"] = round(time.perf_counter() - t_start, 4)
    return report


if __name__ == "__main__":
    v2p = Path("/working_dir/c_44127b7ce41252ee/challenge_v2_isolated/challenge_public.json")
    print(json.dumps(run_qualification(v2p if v2p.exists() else None), indent=2))
