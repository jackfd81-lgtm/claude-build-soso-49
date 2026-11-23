"""All 20 required integration, cognitive, experience, ablation, and dual-node tests for Build 040."""
from __future__ import annotations

from pathlib import Path
import tempfile
from threading import Thread
import unittest

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
from crampu.build030_bridge import Merged49BrainsCramAdapter
from crampu.controller import AuthorizationError, CRAMPUMemoryController, ConflictError
from deploy.orangepi.orangepi_grader import run_independent_grading, run_remote_http_grading

ADMIN = "build040-admin-secret-040000"


class _FailingProvider:
    name = "failing_provider"
    def generate(self, prompt: str, *, timeout_s: float = 10.0):
        raise TimeoutError("simulated upstream timeout")


class TestBuild040Integration20(unittest.TestCase):
    def _env(self, td: str, *, max_hot_entries: int = 256):
        root = Path(td)
        ctrl = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN, max_hot_entries=max_hot_entries)
        ctrl.register_block_owner(0, "L01", ADMIN)
        ctrl.register_block_owner(1, "L02", ADMIN)
        ctrl.register_block_owner(1600, "L01", ADMIN)
        adapter = Merged49BrainsCramAdapter(ctrl, root / "brains")
        cont = SoSoContinuity(root / "soso_continuity.sqlite3")
        suit = SoSoSuitRuntime(adapter)
        suit.authorize_agent("L01", {"memory.read", "memory.write", "spawn_child"}, allowed_block_ids={0, 1600})
        suit.authorize_agent("L02", {"memory.read", "memory.write"}, allowed_block_ids={1})
        closet = SoSoCloset(cont, suit)
        brains = Brains49Runtime(cont, suit, root / "muse_state")
        exp = ExperienceEngine(cont, suit, brains)
        return root, ctrl, adapter, cont, suit, closet, brains, exp

    def _close_all(self, ctrl, adapter, cont):
        adapter.close()
        ctrl.close()
        cont.close()

    # 1. Complete SoSo-to-Brains cognitive execution
    def test_01_soso_to_brains_cognitive_execution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, _, brains, _ = self._env(td)
            cont.start_session("s01", "temporal check")
            tier, fn = cont.route_cognitive_function("temporal_chronology")
            self.assertEqual((tier, fn), ("LOWER_49", "L22"))
            res = brains.dispatch_lower49("s01", fn, {
                "pathway": "B",
                "query": {"subject": "msg", "predicate": "instant"},
                "observations": [{"source": "s", "subject": "msg", "predicate": "instant", "value": "ok", "event": "2026-11-22T01:00:00Z"}],
            })
            self.assertEqual(res["decision"]["status"], "SUPPORTED")
            self._close_all(ctrl, adapter, cont)

    # 2. Lower49 and Higher49 function dispatch
    def test_02_lower49_and_higher49_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, _, brains, _ = self._env(td)
            cont.start_session("s02", "both tiers")
            r_low = brains.dispatch_lower49("s02", "L22", {"pathway": "B", "query": {"subject": "x", "predicate": "y"}, "observations": [{"source": "s", "subject": "x", "predicate": "y", "value": "ok", "event": "2026-11-22T01:00:00Z"}]})
            r_high = brains.dispatch_higher49("s02", "U09", {"obs_id": "o1", "facts": {"current": "high", "rotation": 0}})
            self.assertEqual(r_low["decision"]["status"], "SUPPORTED")
            self.assertGreaterEqual(len(r_high["events"]), 1)
            self._close_all(ctrl, adapter, cont)

    # 3. Big49 memory retrieval and utilization
    def test_03_big49_retrieval_and_utilization(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, closet, brains, _ = self._env(td)
            closet.submit_task("s03", "big49 use")
            closet.invoke_agent("s03", "L01", "BIG49", 0, {"content": "big49-fact", "relation": "ASSOCIATED_WITH"})
            brains.mark_exercised("BIG49", 0)
            got = closet.request_memory("s03", "L01", "BIG49", 0)
            self.assertEqual(got["payload"]["content"], "big49-fact")
            self.assertEqual(brains.utilization_metrics(ADMIN)["big49_mirrored"], 1)
            self._close_all(ctrl, adapter, cont)

    # 4. Small49 memory retrieval and utilization
    def test_04_small49_retrieval_and_utilization(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, closet, brains, _ = self._env(td)
            closet.submit_task("s04", "small49 use")
            closet.invoke_agent("s04", "L01", "SMALL49", 0, {"content": "small49-active", "relation": "ASSOCIATED_WITH"})
            brains.mark_exercised("SMALL49", 0)
            got = closet.request_memory("s04", "L01", "SMALL49", 0)
            self.assertEqual(got["payload"]["content"], "small49-active")
            self.assertEqual(brains.utilization_metrics(ADMIN)["small49_mirrored"], 1)
            self._close_all(ctrl, adapter, cont)

    # 5. Suit authorization and denial (plus revocation & child attenuation)
    def test_05_suit_authorization_and_denial(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, suit, _, _, _ = self._env(td)
            # Child escalation denied
            with self.assertRaises(AuthorizationError):
                suit.request_child_agent("L01", "L03", {"memory.read", "memory.write"}, allowed_block_ids={99})
            # Revocation denies subsequent writes
            suit.revoke_agent("L01")
            with self.assertRaises(AuthorizationError):
                suit.execute_memory_write("L01", "BIG49", 0, {"content": "after-revoke"})
            self._close_all(ctrl, adapter, cont)

    # 6. Closet-to-agent invocation
    def test_06_closet_to_agent_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, closet, _, _ = self._env(td)
            closet.submit_task("s06", "closet call")
            out = closet.invoke_agent("s06", "L01", "BIG49", 0, {"content": "via-closet"})
            self.assertEqual(out["status"], "COMMITTED")
            self.assertEqual(len(closet.audit_log()), 3)  # 2 initial grants + 1 write
            self._close_all(ctrl, adapter, cont)

    # 7. CRAM-PU HOT/WARM/COOL transitions
    def test_07_cram_pu_hot_warm_cool_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, _, _, _ = self._env(td)
            ctrl.write_hot("L01", 0, {"content": "t1"})
            tx = ctrl.stage_to_warm("L01", 0)
            rec = ctrl.commit_to_cool("L01", tx, enqueue_mirror=True)
            self.assertEqual(rec["revision"], 1)
            adapter.sync_pending_outbox()
            self.assertEqual(ctrl.list_outbox(), [])
            self._close_all(ctrl, adapter, cont)

    # 8. COOL-to-Brain outbox replay
    def test_08_cool_to_brain_outbox_replay(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, _, _, _ = self._env(td)
            ctrl.write_hot("L01", 0, {"content": "outbox-replay"})
            tx = ctrl.stage_to_warm("L01", 0)
            ctrl.commit_to_cool("L01", tx, enqueue_mirror=True)
            self.assertEqual(len(ctrl.list_outbox(("PENDING",))), 1)
            adapter.sync_pending_outbox()
            self.assertEqual(adapter.big.count(), 1)
            self.assertEqual(ctrl.list_outbox(), [])
            self._close_all(ctrl, adapter, cont)

    # 9. Restart recovery
    def test_09_restart_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, ctrl, adapter, cont, _, closet, _, _ = self._env(td)
            closet.submit_task("s09", "restart")
            closet.invoke_agent("s09", "L01", "BIG49", 0, {"content": "survive"})
            cont.mark_interrupted("s09")
            self._close_all(ctrl, adapter, cont)

            cont2 = SoSoContinuity(root / "soso_continuity.sqlite3")
            resumed = cont2.resume_session("s09")
            self.assertEqual(resumed["status"], "ACTIVE")
            self.assertGreaterEqual(resumed["event_count"], 2)
            cont2.close()

    # 10. Memory conflict handling
    def test_10_memory_conflict_handling(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, _, _, _ = self._env(td)
            ctrl.write_hot("L01", 0, {"content": "v1"})
            tx_stale = ctrl.stage_to_warm("L01", 0)
            adapter.write_and_sync("L01", "BIG49", 0, {"content": "v2"})
            with self.assertRaises(ConflictError):
                ctrl.commit_to_cool("L01", tx_stale)
            self._close_all(ctrl, adapter, cont)

    # 11. Multi-agent memory isolation
    def test_11_multi_agent_memory_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, closet, _, _ = self._env(td)
            closet.submit_task("s11", "isolation")
            closet.invoke_agent("s11", "L01", "BIG49", 0, {"content": "l01-only"})
            with self.assertRaises(AuthorizationError):
                closet.request_memory("s11", "L02", "BIG49", 0)
            self._close_all(ctrl, adapter, cont)

    # 12. Context continuity
    def test_12_context_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, _, _, _ = self._env(td)
            cont.start_session("s12", "provenance separation")
            cont.record_event("s12", "OBSERVATION", "sensor", {"v": 1})
            cont.record_event("s12", "MODEL_OUTPUT", "gemini", {"v": "advisory"})
            cont.record_event("s12", "VERIFIED_RESULT", "L22", {"v": "ok"})
            rec = cont.reconstruct_session("s12")
            self.assertEqual(rec["counts_by_kind"], {"OBSERVATION": 1, "MODEL_OUTPUT": 1, "VERIFIED_RESULT": 1})
            self._close_all(ctrl, adapter, cont)

    # 13. Experience reuse
    def test_13_experience_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, _, _, exp = self._env(td)
            cont.start_session("s13-train", "learn voltage fault")
            exp.record_experience("s13-train", "L01", "diag_fault", "sig_A", "probe_voltage", "SUCCESS", family="BIG49", index=0)
            res = exp.solve_diagnostic_case("s13-eval", "L01", "diag_fault", "sig_A", "probe_voltage", mode="with_memory")
            self.assertEqual(res["memory_status"], "APPLIED")
            self.assertTrue(res["first_attempt_hit"])
            self.assertEqual(res["probes_needed"], 1)
            self._close_all(ctrl, adapter, cont)

    # 14. Memory ablation (without, with, irrelevant, corrupted)
    def test_14_memory_ablation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, _, _, exp = self._env(td)
            cont.start_session("s14-train", "store experience")
            exp.record_experience("s14-train", "L01", "diag_fault", "sig_B", "probe_voltage", "SUCCESS", family="BIG49", index=0)

            r_without = exp.solve_diagnostic_case("s14-no", "L01", "diag_fault", "sig_B", "probe_voltage", mode="without_memory")
            r_with = exp.solve_diagnostic_case("s14-with", "L01", "diag_fault", "sig_B", "probe_voltage", mode="with_memory")
            r_irrel = exp.solve_diagnostic_case("s14-irrel", "L01", "diag_fault", "sig_B", "probe_voltage", mode="irrelevant_memory")

            # Corrupt SQLite payload for corrupted_memory ablation
            ctrl._db.execute("UPDATE blocks SET record = ? WHERE block_id = 0", (b'{"bad":1}',))
            r_corrupt = exp.solve_diagnostic_case("s14-corrupt", "L01", "diag_fault", "sig_B", "probe_voltage", mode="corrupted_memory")

            self.assertTrue(r_with["first_attempt_hit"])
            self.assertEqual(r_with["probes_needed"], 1)
            self.assertFalse(r_without["first_attempt_hit"])
            self.assertEqual(r_without["probes_needed"], 3)
            self.assertFalse(r_irrel["first_attempt_hit"])
            self.assertEqual(r_corrupt["memory_status"], "CORRUPTED_FALLBACK")
            self._close_all(ctrl, adapter, cont)

    # 15. Online AI provider failure (falls back cleanly, advisory only)
    def test_15_online_ai_provider_failure(self) -> None:
        gw = OnlineAIGateway(primary=_FailingProvider(), fallback=OfflineAdvisoryProvider(), max_retries=1, backoff_s=0.001)
        resp = gw.request_advisory("analyze sensor drift")
        self.assertTrue(resp.fallback_used)
        self.assertTrue(resp.advisory_only)
        self.assertEqual(resp.authority, 0)

    # 16. Offline operation
    def test_16_offline_operation(self) -> None:
        gw = OnlineAIGateway(primary=OfflineAdvisoryProvider())
        resp = gw.request_advisory("offline check")
        self.assertEqual(resp.provider, "offline_advisory")
        self.assertEqual(resp.authority, 0)

    # 17. Deterministic replay (Online AI ReplayProvider + Memory)
    def test_17_deterministic_replay(self) -> None:
        rp = ReplayProvider()
        rp.record("summarize", "replayed advisory text")
        gw = OnlineAIGateway(primary=rp)
        r1 = gw.request_advisory("summarize")
        r2 = gw.request_advisory("summarize")
        self.assertTrue(r1.from_replay)
        self.assertEqual(r1.text, r2.text)

    # 18. Orange Pi grading protocol (local sim + bounded HTTP)
    def test_18_orangepi_grading_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, closet, _, _ = self._env(td)
            srv = BoundedGraderServer(("127.0.0.1", 0), closet, "opi-scoped-token-01")
            holder = {}
            def _client():
                holder["res"] = run_remote_http_grading(f"http://127.0.0.1:{srv.server_port}", "opi-scoped-token-01")
            t = Thread(target=_client, daemon=True)
            t.start()
            try:
                srv.handle_request()
                srv.handle_request()
                t.join(timeout=5)
                self.assertEqual(holder["res"]["status"], "PASS")
            finally:
                srv.server_close()
            self._close_all(ctrl, adapter, cont)

        with tempfile.TemporaryDirectory() as td2:
            local_rep = run_independent_grading(Path(td2), ADMIN, iterations=5)
            self.assertEqual(local_rep["status"], "PASS")

    # 19. Resource exhaustion (HOT bounded LRU eviction + step budget exhaustion)
    def test_19_resource_exhaustion(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, suit, _, _, _ = self._env(td, max_hot_entries=1)
            ctrl.write_hot("L01", 0, {"content": "a"})
            ctrl.write_hot("L01", 1600, {"content": "b"})
            self.assertEqual(ctrl.inspect(ADMIN)["hot"], 1)
            self.assertEqual(ctrl.inspect(ADMIN)["hot_evictions"], 1)

            suit.authorize_agent("L05", {"memory.write"}, allowed_block_ids={0}, max_steps=1)
            ctrl.register_block_owner(2, "L05", ADMIN)
            suit.authorize_agent("L05", {"memory.write"}, allowed_block_ids={2}, max_steps=1)
            suit.execute_memory_write("L05", "BIG49", 2, {"content": "step1"})
            with self.assertRaises(AuthorizationError):
                suit.execute_memory_write("L05", "BIG49", 2, {"content": "step2-over-budget"})
            self._close_all(ctrl, adapter, cont)

    # 20. Complete end-to-end task execution
    def test_20_complete_e2e_task_execution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, ctrl, adapter, cont, _, closet, brains, exp = self._env(td)
            closet.submit_task("s20", "full e2e cognitive lifecycle")
            gw = OnlineAIGateway(primary=OfflineAdvisoryProvider())
            adv = gw.request_advisory("suggest diagnostic probe")
            cont.record_event("s20", "MODEL_OUTPUT", adv.provider, {"text": adv.text, "authority": adv.authority})
            exp.record_experience("s20", "L01", "e2e_diag", "sig_E2E", "probe_pressure", "SUCCESS", family="SMALL49", index=0)
            solved = exp.solve_diagnostic_case("s20-followup", "L01", "e2e_diag", "sig_E2E", "probe_pressure", mode="with_memory")
            self.assertTrue(solved["first_attempt_hit"])
            cont.complete_session("s20")
            self.assertEqual(cont.get_session("s20")["status"], "COMPLETED")
            self._close_all(ctrl, adapter, cont)


if __name__ == "__main__":
    unittest.main()
