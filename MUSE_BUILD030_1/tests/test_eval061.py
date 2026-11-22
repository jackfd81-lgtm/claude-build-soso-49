"""Pins the frozen Suit 6.1 experiment (identical fixtures per arm; evaluation in a fresh process after training)."""
import hashlib
import json
from pathlib import Path

from eval061 import run as E

FIX = Path(E.FIX)


def test_fixtures_match_their_recorded_hashes():
    for line in (FIX / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split()
        assert hashlib.sha256((FIX / name).read_bytes()).hexdigest() == digest


def test_controlled_arms(tmp_path):
    arms = {"A": dict(suit=False, mask=True, transfer=False), "B": dict(suit=True, mask=False, transfer=True),
            "C": dict(suit=True, mask=True, transfer=False), "D": dict(suit=True, mask=True, transfer=True)}
    s = {k: E.run_arm(k, **v) for k, v in arms.items()}
    sm = {k: v["summary"] for k, v in s.items()}
    assert [sm[k]["correct_disposition"] for k in "ABCD"] == [0, 16, 6, 17]
    assert all(sm[k]["negative_transfers"] == 0 for k in "ABCD")
    assert sm["D"]["critical_difference_recognised"] == sm["B"]["critical_difference_recognised"] == "7/7"
    assert sm["C"]["critical_difference_recognised"] == "0/7"          # without transfer memory, similar-looking tasks are not recognised as such
    assert sm["D"]["evidence_requests"] < sm["C"]["evidence_requests"] < sm["A"]["evidence_requests"]
    assert sm["D"]["cognitive_invocations"] < sm["A"]["cognitive_invocations"]
    assert sm["B"]["cognitive_invocations"] == 0 and sm["D"]["usable_plans_supplied"] == sm["B"]["usable_plans_supplied"] == 5
    assert all(v["trace"]["intact"] for k, v in s.items() if k != "A")
    # the only task where Suit+Mask beats Suit alone is the one whose resolution requires the ordering review
    diff = [r["id"] for rb, rd in zip(s["B"]["rows"], s["D"]["rows"]) for r in (rb,) if rb["correct"] != rd["correct"]]
    assert diff == ["P3"]


def test_naive_similarity_control_commits_negative_transfers():
    rows = E.naive()
    assert sum(r["negative_transfer"] for r in rows) == 7
