"""Build 043 first proof — Higher first, with restart recovery.

Higher starts -> requests exact information -> Lower receives an unrelated
reading and stays on HOLD -> a matching reading arrives after restart ->
Higher uses the Lower result and shows what changed.

Run:  python3 proof_43.py [--workdir DIR]
Writes traces/proof_43_higher_first.json (the case trace) and prints the
story. Uses disposable state; the "restart" is a genuine one: the first
phase closes its runtime (controller, adapter, continuity, Suit) and the
second phase opens a fresh runtime on the same durable directories and
restores the case from COOL. Suit authorization is session-scoped, so the
second phase re-authorizes the agent; block ownership and the case state
itself are durable.
"""
import json
import secrets
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREE = HERE / "SUIT_AND_BRAINS_43_0"
sys.path.insert(0, str(TREE))

from build040_runtime.investigation import Investigation, case_block_id  # noqa: E402
from build040_runtime.standalone import local_runtime  # noqa: E402

ADMIN = secrets.token_hex(24)
AGENT = "L01"
CASE_ID = "proof-higher-first-1"

# The case: two candidate explanations of a tox screen. Higher must find
# which one holds, but the discriminating reading is missing.
SNAPSHOT = {
    "med:early": {"discipline": "medical", "key": "tox_screen", "value": "negative",
                  "event": "2026-01-01T01:00:00Z"},
}
HYPOTHESES = {
    "H1": {"tox_screen": "negative"},
    "H2": {"tox_screen": "positive_opioid"},
}
OBSERVED: dict = {}
SOURCES = ["med:late"]


def authorize(closet, n_blocks=2):
    # The case-state document grows with the investigation (one transition
    # event per slot execution), so the grant's payload bound must cover it;
    # 64KB covers roughly 130 executions.
    closet.suit.authorize_agent(
        AGENT, {"memory.read", "memory.write", "task.run"},
        allowed_block_ids={case_block_id(i) for i in range(n_blocks)},
        max_payload_bytes=65536)


def phase_one(root: Path) -> dict:
    print("== Phase 1: Higher starts, requests exact information ==")
    with local_runtime(root, ADMIN) as (ctrl, closet, _loop):
        ctrl.register_block_owner(case_block_id(0), AGENT, ADMIN)
        authorize(closet)
        inv = Investigation(closet, AGENT)
        case = inv.start_case(
            CASE_ID, mode="joined_higher_first",
            goal="Decide which tox-screen explanation holds",
            evidence_snapshot=SNAPSHOT, scope=["tox_screen"],
            origin_specialty="Medical", target_specialty="Medical",
            acceptable_sources=SOURCES, hypotheses=HYPOTHESES, observed=dict(OBSERVED))
        print(f"  case started: mode={case['mode']} revision={case['revision']} "
              f"cool_revision={case['durable']['cool_revision']}")
        case = inv.execute_mode(CASE_ID, hypotheses=HYPOTHESES, observed=dict(OBSERVED))
        req = case["requirements"][0]
        print(f"  Higher gap review: discriminator={req['required_feature']} "
              f"(U11 gaps -> U12 select)")
        print(f"  requirement: {req['request_id']} feature={req['required_feature']} "
              f"source in {req['supporting_evidence']} release={req['release_condition']}")
        print(f"  case: status={case['status']} resume_point={case['resume_point']} "
              f"revision={case['revision']} cool_revision={case['durable']['cool_revision']}")
        assert case["status"] == "HOLD" and case["revision"] == 0

        print("== Phase 1b: Lower receives an unrelated reading ==")
        unrelated = {"observation_id": "obs-unrelated-1", "feature": "signed_consent",
                     "source_id": "leg:a", "value": "present",
                     "case_revision": 0, "conclusive": True}
        out = inv.receive_observation(CASE_ID, unrelated)
        print(f"  matcher outcome: {out['outcome']} (expected WRONG_FEATURE)")
        print(f"  case: status={case['status']} revision={case['revision']} "
              f"(unchanged) cool_revision={case['durable']['cool_revision']}")
        assert out["outcome"] == "WRONG_FEATURE"
        assert case["status"] == "HOLD" and case["revision"] == 0
        return {"case_id": CASE_ID, "block_n": 0,
                "cool_revision_after_hold": case["durable"]["cool_revision"]}


def phase_two(root: Path, info: dict) -> dict:
    print("== Phase 2: restart, then the matching reading arrives ==")
    with local_runtime(root, ADMIN) as (ctrl, closet, _loop):
        authorize(closet)  # session-scoped: re-authorize after the restart
        inv = Investigation(closet, AGENT)
        case = inv.resume_after_restart(info["case_id"], info["block_n"])
        print(f"  restored from COOL: mode={case['mode']} status={case['status']} "
              f"revision={case['revision']} resume_point={case['resume_point']} "
              f"cool_revision={case['durable']['cool_revision']}")
        assert case["mode"] == "joined_higher_first" and case["status"] == "HOLD"
        assert case["revision"] == 0

        matching = {"observation_id": "obs-matching-1", "feature": "tox_screen",
                    "source_id": "med:late", "value": "positive_opioid",
                    "case_revision": 0, "conclusive": True,
                    "event": "2026-01-01T02:00:00Z"}
        before = dict(case["decision"]) if case.get("decision") else None
        out = inv.receive_observation(info["case_id"], matching)
        case = out["case"]
        print(f"  matcher outcome: {out['outcome']} (expected RELEASED)")
        print(f"  case: status={case['status']} revision={case['revision']} "
              f"resumptions={case['resumptions']} cool_revision={case['durable']['cool_revision']}")
        assert out["outcome"] == "RELEASED"
        lower = out["lower"]
        higher = out["higher"]
        print(f"  Lower result: L31 decision={lower['decision']['status']} "
              f"conflicts={len(lower['conflicts'])}")
        print(f"  Higher result: decision={higher['decision']['status']} "
              f"remaining={sorted(higher['remaining'])}")
        print(f"  join: {out['join']['join_id']} status={out['join']['status']} "
              f"(Lower L31 -> Higher U16, consumed={out['join']['status'] == 'CONSUMED'})")
        rec = case["reconciliation"]
        print("  what changed:")
        print(f"    decision before: {rec['decision_before']}")
        print(f"    decision after:  {rec['decision_after']}")
        print(f"    changed fields:  {rec['changed_fields']}")
        print(f"    resolved unknowns: {rec['resolved_unknowns']}")
        print(f"    new evidence: {rec['new_evidence']}")
        print(f"    disposition change: {rec['disposition_change']}")
        assert case["status"] == "COMPLETED"
        assert rec["decision_changed"] is True
        return {"case": case, "before_decision": before,
                "cool_revision_after_hold": info["cool_revision_after_hold"]}


def main() -> int:
    workdir = Path(sys.argv[sys.argv.index("--workdir") + 1]) if "--workdir" in sys.argv else None
    tmp = tempfile.TemporaryDirectory() if workdir is None else None
    root = Path(workdir) if workdir else Path(tmp.name)
    root.mkdir(parents=True, exist_ok=True)
    info = phase_one(root)
    result = phase_two(root, info)
    case = result["case"]
    trace = {
        "candidate": "43.0.1",
        "proof": "higher-first with restart",
        "case_id": CASE_ID,
        "mode": case["mode"],
        "status": case["status"],
        "case_revision": case["revision"],
        "cool_revision_after_hold": result["cool_revision_after_hold"],
        "cool_revision_final": case["durable"]["cool_revision"],
        "decision_before": result["before_decision"],
        "decision_after": case["decision"],
        "reconciliation": case["reconciliation"],
        "joins": case["joins"],
        "requirements": case["requirements"],
        "observations": case["observations"],
        "invocations": case["invocations"],
        "events": case["events"],
        "decision_history": case["decision_history"],
    }
    out_path = HERE / "traces" / "proof_43_higher_first.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(trace, indent=2))
    print(f"\nPROOF PASS: case {CASE_ID} COMPLETED after restart")
    print(f"trace: {out_path}")
    if tmp is not None:
        tmp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
