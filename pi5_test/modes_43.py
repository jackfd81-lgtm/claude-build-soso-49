"""Build 043 — all four modes on the same evidence fixtures.

Run:  python3 modes_43.py [--workdir DIR]
Writes traces/modes_43.json and prints a comparison table.

The same fixtures run in every mode (the handoff's step 3):
- observations: two readings of one state (dog awake / asleep), same event.
- evidence snapshot: med:early tox_screen negative.
- hypotheses: H1 tox_screen negative, H2 tox_screen positive_opioid.
- observed: {} (no tox reading yet).

Documented command for each mode (the Investigation API):
    inv.start_case(case_id, mode="<mode>", goal=..., evidence_snapshot=...,
                   hypotheses=..., observed=...)
    inv.execute_mode(case_id, observations=..., hypotheses=..., observed=...)
    # joined_higher_first only: inv.receive_observation(case_id, observation)
    # after a restart: inv.resume_after_restart(case_id, block_n)

Expected outcomes on these fixtures:
- lower_only: COMPLETED, L31 CONFLICT, Higher never invoked.
- higher_only: UNRESOLVED, U16 ABSTAIN (the tox_screen discriminator is
  missing), Lower never invoked.
- joined_lower_first: COMPLETED, U16 CONFLICT consuming the L31 result.
- joined_higher_first: HOLD on the missing discriminator, then COMPLETED
  after the matching reading (the unrelated reading leaves it on HOLD).
"""
import copy
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

CONTRA = [
    {"source": "s1", "subject": "dog", "predicate": "state", "value": "awake",
     "scope": "default", "event": "2026-01-01T00:00:00Z"},
    {"source": "s2", "subject": "dog", "predicate": "state", "value": "asleep",
     "scope": "default", "event": "2026-01-01T00:00:00Z"},
]
SNAPSHOT = {"med:early": {"discipline": "medical", "key": "tox_screen", "value": "negative"}}
HYPOTHESES = {"H1": {"tox_screen": "negative"}, "H2": {"tox_screen": "positive_opioid"}}


def _investigation(closet, n_blocks=4):
    for i in range(n_blocks):
        closet.suit.adapter.controller.register_block_owner(case_block_id(i), AGENT, ADMIN)
    closet.suit.authorize_agent(
        AGENT, {"memory.read", "memory.write", "task.run"},
        allowed_block_ids={case_block_id(i) for i in range(n_blocks)},
        max_payload_bytes=65536)
    return Investigation(closet, AGENT)


def _trace(case):
    return {
        "mode": case["mode"], "case_id": case["case_id"], "status": case["status"],
        "decision": case["decision"], "revision": case["revision"],
        "resume_point": case["resume_point"],
        "invocations": [(i["slot_id"], i["tier"], i["status"]) for i in case["invocations"]],
        "tiers_invoked": sorted({i["tier"] for i in case["invocations"]}),
        "joins": [(j["join_id"].split(":")[-1], j["direction"], j["status"])
                  for j in case["joins"]],
        "requirements": [(r["request_id"], r["required_feature"], r["disposition"])
                         for r in case["requirements"]],
        "reconciliation": case.get("reconciliation"),
    }


def run_mode(root: Path, mode: str) -> dict:
    with local_runtime(root, ADMIN) as (_ctrl, closet, _loop):
        inv = _investigation(closet)
        case_id = f"modes-{mode}"
        inv.start_case(case_id, mode=mode, goal="Compare the four modes on one fixture",
                       evidence_snapshot=copy.deepcopy(SNAPSHOT), scope=["tox_screen", "dog"],
                       acceptable_sources=["med:late"],
                       hypotheses=copy.deepcopy(HYPOTHESES), observed={})
        case = inv.execute_mode(case_id, observations=copy.deepcopy(CONTRA),
                                hypotheses=copy.deepcopy(HYPOTHESES), observed={})
        if mode == "joined_higher_first":
            # The unrelated reading leaves it on HOLD; the matching reading
            # releases it and Higher consumes the Lower result.
            inv.receive_observation(case_id, {
                "observation_id": "modes-unrelated", "feature": "signed_consent",
                "source_id": "leg:a", "value": "present", "case_revision": 0,
                "conclusive": True})
            out = inv.receive_observation(case_id, {
                "observation_id": "modes-matching", "feature": "tox_screen",
                "source_id": "med:late", "value": "positive_opioid",
                "case_revision": 0, "conclusive": True,
                "event": "2026-01-01T02:00:00Z"})
            case = out["case"]
        return _trace(case)


def main() -> int:
    workdir = Path(sys.argv[sys.argv.index("--workdir") + 1]) if "--workdir" in sys.argv else None
    tmp = tempfile.TemporaryDirectory() if workdir is None else None
    root = Path(workdir) if workdir else Path(tmp.name)
    results = {}
    for mode in ("lower_only", "higher_only", "joined_lower_first", "joined_higher_first"):
        sub = root / mode
        sub.mkdir(parents=True, exist_ok=True)
        results[mode] = run_mode(sub, mode)
    out_path = HERE / "traces" / "modes_43.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps({"candidate": "43.0.1", "modes": results}, indent=2))
    print(f"{'mode':<20} {'status':<11} {'decision':<10} {'tiers invoked':<14} joins")
    for mode, r in results.items():
        dec = (r["decision"] or {}).get("status", "-")
        print(f"{mode:<20} {r['status']:<11} {dec:<10} {','.join(r['tiers_invoked']):<14} "
              f"{[j[2] for j in r['joins']] or '-'}")
    print(f"\ntrace: {out_path}")
    if tmp is not None:
        tmp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
