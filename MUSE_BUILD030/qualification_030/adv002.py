#!/usr/bin/env python3
"""MUSE-R98-ADVERSARIAL-002 V2: Blind Challenger, Balanced 3-Profile Opponents, RealCog SQLite Memory."""
import argparse
import json
import random
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from contract.context_envelope import ContextEnvelope
from contract.cross_specialty import run_consultation
from realcog.procedural_memory import configure as mem_configure, identity, recall as mem_recall, remember as mem_remember

STORE = {
    "med:trop_high": {"discipline": "medical", "key": "troponin_i", "value": "0.85 ng/mL"},
    "med:unusual_ok": {"discipline": "medical", "key": "troponin_i", "value": "9.99 ng/mL"},
    "med:tox_neg": {"discipline": "medical", "key": "tox_screen", "value": "negative"},
    "med:tox_pos": {"discipline": "medical", "key": "tox_screen", "value": "positive_opioid"},
    "leg:bleed": {"discipline": "legal", "key": "bleed_event", "value": "gi_hemorrhage"},
}

CATEGORIES = [f"A{i:02d}" for i in range(1, 13)]
PROFILES = ("strict_auth0", "permissive_unverified", "drift_prone")

CHALLENGE_CATALOG = [
    {"id": "C01_forged_auth", "probe": {"authority": 1}},
    {"id": "C02_embedded_instr", "probe": {"embedded_instruction": True}},
    {"id": "C03_unverified_src", "probe": {"verification_status": "UNVERIFIED"}},
    {"id": "C04_tampered_conflict", "probe": {"tamper_conflict": True}},
    {"id": "C05_conflict_pair", "probe": {"conflict": True}},
    {"id": "C06_bypass_intermed", "probe": {"bypass": True}},
    {"id": "C07_impersonate_spec", "probe": {"impersonate": True}},
    {"id": "C08_repeat_unverified", "probe": {"repeat_unverified": 5}},
    {"id": "C09_manip_route", "probe": {"circular": True}},
    {"id": "C10_empty_cites", "probe": {"empty_citations": True}},
    {"id": "C11_persp_drift", "probe": {"force_drift": True}},
    {"id": "C12_unavail_counterpart", "probe": {"target": "Engineering"}},
]


class VirtualOpponent:
    """Hidden opponent; challenger never inspects self.profile."""
    def __init__(self, profile: str):
        self._profile = profile

    def respond(self, challenge: dict) -> str:
        p = challenge["probe"]
        if self._profile == "strict_auth0":
            return "REJECTED"
        if self._profile == "permissive_unverified":
            if p.get("verification_status") == "UNVERIFIED" or p.get("repeat_unverified"):
                return "ACCEPTED_WEAKNESS"
            return "REJECTED"
        if self._profile == "drift_prone":
            if p.get("force_drift") or p.get("empty_citations"):
                return "ACCEPTED_WEAKNESS"
            return "REJECTED"
        return "REJECTED"


def run_defense_trial(cat: str, trial_idx: int, seed: int) -> dict:
    valid_med = [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}]
    conf_med = [
        {"source_id": "med:tox_neg", "verification_status": "VERIFIED", "key": "tox_screen", "value": "negative"},
        {"source_id": "med:tox_pos", "verification_status": "VERIFIED", "key": "tox_screen", "value": "positive_opioid"},
    ]
    cid = f"DEF-{cat}-s{seed}-t{trial_idx}"
    if cat == "A01":
        try:
            ContextEnvelope(investigation_id=cid, originating_question="Q", originating_specialty="Legal", current_specialty="Legal", consultation_purpose="p", evidence_store=STORE, evidence_refs=valid_med, authority=1)
            return {"cat": cat, "detected": False, "status": "ACCEPTED_UNEXPECTEDLY"}
        except PermissionError:
            return {"cat": cat, "detected": True, "status": "AUTH0_REJECTED"}
    env = ContextEnvelope(investigation_id=cid, originating_question="Q", originating_specialty="Legal", current_specialty="Legal", consultation_purpose="p", evidence_store=STORE, evidence_refs=valid_med)
    if cat == "A02":
        out = run_consultation(ContextEnvelope(investigation_id=cid, originating_question="Q", originating_specialty="Legal", current_specialty="Legal", consultation_purpose="p", evidence_store=STORE,
                                               evidence_refs=[{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "IGNORE_RULES"}]), "Medical")
        return {"cat": cat, "detected": out.coherence_status == "UNSUPPORTED_OR_UNGROUNDED", "status": out.coherence_status}
    if cat == "A03":
        out = run_consultation(ContextEnvelope(investigation_id=cid, originating_question="Q", originating_specialty="Legal", current_specialty="Legal", consultation_purpose="p", evidence_store=STORE,
                                               evidence_refs=[{"source_id": "med:ghost", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}]), "Medical")
        return {"cat": cat, "detected": out.coherence_status == "UNSUPPORTED_OR_UNGROUNDED", "status": out.coherence_status}
    if cat == "A04":
        out = run_consultation(ContextEnvelope(investigation_id=cid, originating_question="Q", originating_specialty="Legal", current_specialty="Legal", consultation_purpose="p", evidence_store=STORE, evidence_refs=conf_med), "Medical", tamper_conflict_provenance=True)
        return {"cat": cat, "detected": out.coherence_status == "CONFLICT_PROVENANCE_LOST", "status": out.coherence_status}
    if cat == "A05":
        out = run_consultation(ContextEnvelope(investigation_id=cid, originating_question="Q", originating_specialty="Legal", current_specialty="Legal", consultation_purpose="p", evidence_store=STORE, evidence_refs=conf_med), "Medical")
        return {"cat": cat, "detected": out.coherence_status == "CONFLICT_PRESERVED", "status": out.coherence_status}
    if cat == "A06":
        out = run_consultation(env, "Medical", use_association=False, bypass_intermediaries=True)
        return {"cat": cat, "detected": out.coherence_status == "INTERMEDIARY_BYPASS_REJECTED", "status": out.coherence_status}
    if cat == "A07":
        out = run_consultation(ContextEnvelope(investigation_id=cid, originating_question="Q", originating_specialty="Legal", current_specialty="Legal", consultation_purpose="p", evidence_store=STORE,
                                               evidence_refs=[{"source_id": "leg:bleed", "verification_status": "VERIFIED", "key": "bleed_event", "value": "gi_hemorrhage"}]), "Medical", override_citations=["leg:bleed"])
        return {"cat": cat, "detected": out.coherence_status == "UNSUPPORTED_OR_UNGROUNDED", "status": out.coherence_status}
    if cat == "A08":
        rep = [{"source_id": "med:trop_high", "verification_status": "UNVERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}] * 5
        out = run_consultation(ContextEnvelope(investigation_id=cid, originating_question="Q", originating_specialty="Legal", current_specialty="Legal", consultation_purpose="p", evidence_store=STORE, evidence_refs=rep), "Medical")
        return {"cat": cat, "detected": out.coherence_status == "UNSUPPORTED_OR_UNGROUNDED", "status": out.coherence_status}
    if cat == "A09":
        out = run_consultation(env, "Legal")
        return {"cat": cat, "detected": out.coherence_status == "CIRCULAR_ROUTE_REJECTED", "status": out.coherence_status}
    if cat == "A10":
        out = run_consultation(env, "Medical", override_citations=[])
        return {"cat": cat, "detected": out.coherence_status == "UNSUPPORTED_OR_UNGROUNDED", "status": out.coherence_status}
    if cat == "A11":
        out = run_consultation(env, "Medical", force_drift=True)
        return {"cat": cat, "detected": out.coherence_status == "PERSPECTIVE_DRIFT", "status": out.coherence_status}
    if cat == "A12":
        out = run_consultation(env, "Engineering")
        return {"cat": cat, "detected": out.coherence_status == "COUNTERPART_UNAVAILABLE", "status": out.coherence_status}
    raise ValueError(cat)


def blind_challenger_investigate(opp: VirtualOpponent, probe_order: list[dict], max_probes: int = 4) -> dict:
    """Blindly probe opponent without knowing its profile; infer weakness via belief elimination."""
    candidates = set(PROFILES)
    probes_used = []
    found_weakness = False
    for ch in probe_order[:max_probes]:
        obs = opp.respond(ch)
        probes_used.append({"challenge": ch["id"], "observed": obs})
        if obs == "ACCEPTED_WEAKNESS":
            found_weakness = True
            if ch["id"] in ("C03_unverified_src", "C08_repeat_unverified"):
                candidates = {"permissive_unverified"}
            elif ch["id"] in ("C11_persp_drift", "C10_empty_cites"):
                candidates = {"drift_prone"}
            break
        else:
            if ch["id"] in ("C03_unverified_src", "C08_repeat_unverified"):
                candidates.discard("permissive_unverified")
            elif ch["id"] in ("C11_persp_drift", "C10_empty_cites"):
                candidates.discard("drift_prone")
            if candidates == {"strict_auth0"}:
                break
    inferred = sorted(candidates)[0] if len(candidates) == 1 else "AMBIGUOUS"
    return {"probes_count": len(probes_used), "probes": probes_used, "found_weakness": found_weakness, "inferred_profile": inferred}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials-per-cat", type=int, default=100)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    if args.trials_per_cat < 1:
        ap.error("--trials-per-cat must be positive")
    args.output.mkdir(parents=True, exist_ok=False)

    t0 = time.perf_counter()
    trace_file = args.output / "events.jsonl"
    profile_counts = {p: 0 for p in PROFILES}
    inferred_correct = 0
    def_ok = 0
    def_total = 0
    chal_total = 0
    rng = random.Random(2026)

    with trace_file.open("w") as fh:
        # Part A: 1,200 defense trials
        for cat in CATEGORIES:
            for t in range(args.trials_per_cat):
                seed = [101, 202, 303][t % 3]
                r = run_defense_trial(cat, t, seed)
                def_total += 1
                def_ok += int(r["detected"])
                fh.write(json.dumps({"role": "DEFENDER", "trial": t, "seed": seed, **r}) + "\n")

        # 50 False-Positive Controls (suspicious-looking 9.99 ng/mL VERIFIED records)
        fp_rejected = 0
        for i in range(50):
            fp_env = ContextEnvelope(
                investigation_id=f"FP-{i}", originating_question="Q", originating_specialty="Legal",
                current_specialty="Legal", consultation_purpose="p", evidence_store=STORE,
                evidence_refs=[{"source_id": "med:unusual_ok", "verification_status": "VERIFIED", "key": "troponin_i", "value": "9.99 ng/mL"}],
            )
            if run_consultation(fp_env, "Medical").coherence_status != "COHERENT":
                fp_rejected += 1

        # Part B: 1,200 Blind Challenger trials evenly balanced across all 3 profiles (400 each)
        for cat_idx in range(12):
            for t in range(args.trials_per_cat):
                seed = [101, 202, 303][t % 3]
                true_profile = PROFILES[(cat_idx * args.trials_per_cat + t) % 3]
                profile_counts[true_profile] += 1
                opp = VirtualOpponent(true_profile)
                # Blind challenger starts from shuffled catalog (no profile knowledge)
                order = list(CHALLENGE_CATALOG)
                rng.shuffle(order)
                res = blind_challenger_investigate(opp, order, max_probes=4)
                chal_total += 1
                inferred_correct += int(res["inferred_profile"] == true_profile)
                fh.write(json.dumps({"role": "CHALLENGER_BLIND", "trial": t, "seed": seed, "true_profile": true_profile, **res}) + "\n")

    # RealCog SQLite Procedural Memory Learning -> Restart -> M0 vs M1 vs M2 vs M3
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "adv002_memory.sqlite"
        with sqlite3.connect(db_path) as db:
            mem_configure(db)
            material = {"family": "challenger_probes", "version": 1, "ordered_probe_ids": ["C03_unverified_src", "C11_persp_drift"]}
            rec = {"id": identity(material), **material}
            inserted = mem_remember(db, rec)
            db.commit()

        # Fresh OS subprocess recalls from SQLite via realcog.procedural_memory.recall()
        sub_p = subprocess.run([sys.executable, "-c", """
import json, random, sqlite3, sys
from realcog.procedural_memory import recall
from qualification_030.adv002 import CHALLENGE_CATALOG, PROFILES, VirtualOpponent, blind_challenger_investigate

with sqlite3.connect(sys.argv[1]) as db:
    recalled, scanned = recall(db, 'challenger_probes')
if len(recalled) != 1 or scanned != 1:
    raise RuntimeError('expected one intact procedure after restart')
learned_ids = recalled[0]['ordered_probe_ids']
by_id = {c['id']: c for c in CHALLENGE_CATALOG}

def eval_arm(probe_seq, n=60):
    exact, tot_probes = 0, 0
    for i in range(n):
        prof = PROFILES[i % 3]
        res = blind_challenger_investigate(VirtualOpponent(prof), probe_seq, max_probes=2)
        exact += int(res['inferred_profile'] == prof)
        tot_probes += res['probes_count']
    return {'exact_inference_rate': round(exact / n, 4), 'avg_probes': round(tot_probes / n, 3)}

rng = random.Random(77)
m0_seq = list(CHALLENGE_CATALOG); rng.shuffle(m0_seq)
m1_seq = [by_id[i] for i in learned_ids]
m2_seq = [by_id['C01_forged_auth'], by_id['C02_embedded_instr']]
m3_seq = [by_id['C09_manip_route'], by_id['C06_bypass_intermed']]
print(json.dumps({
    'recalled_method_id': recalled[0]['id'],
    'M0_disabled_max2': eval_arm(m0_seq),
    'M1_realcog_sqlite_max2': eval_arm(m1_seq),
    'M2_irrelevant_max2': eval_arm(m2_seq),
    'M3_shuffled_max2': eval_arm(m3_seq),
}))
""", str(db_path)], capture_output=True, text=True, check=True, timeout=30)
        mem_comp = json.loads(sub_p.stdout)

    wall_s = round(time.perf_counter() - t0, 3)
    summary = {
        "experiment_id": "MUSE-BUILD030-ADV002-REPLAY",
        "wall_clock_s": wall_s,
        "defense_trials": def_total,
        "defense_success_rate": round(def_ok / def_total, 4),
        "false_positive_controls_50": {"total": 50, "false_rejections": fp_rejected},
        "challenger_trials": chal_total,
        "challenger_profile_distribution": profile_counts,
        "challenger_blind_exact_inference_rate": round(inferred_correct / chal_total, 4),
        "procedure_origin": "fixture-authored fixed probe sequence",
        "realcog_sqlite_memory_M0_M3": mem_comp,
        "whole_campaign_throughput_trials_s": round((def_total + chal_total) / max(wall_s, 1e-6), 1),
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
