"""Replay the archived reciprocal exchange on the Build 030 runtime."""
import argparse
import json
from pathlib import Path
import tempfile

from contract.context_envelope import ContextEnvelope
from contract.cross_specialty import run_consultation, verify_evidence_records
from muse024 import call, initialize
from qualification_030.referee import score_response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixtures = Path(__file__).with_name("fixtures")
    # 1. Score ChatGPT's live responses on M2C-01..05
    cgpt_resps = json.loads((fixtures / "adv003_archived.json").read_text())["chatgpt_on_m2c"]["details"]
    m2c_scores = []
    for row in cgpt_resps:
        sc = score_response(row["challenge_id"], row["verdict"], row.get("requested_measurements", []))
        m2c_scores.append({**row, "referee_score": sc})

    # 2. Run ChatGPT's 5 challenges (C2M-01..05) through actual MUSE Build 029 runtime
    c2m_challenges = json.loads((fixtures / "c2m.json").read_text())
    c2m_results = []

    with tempfile.TemporaryDirectory(prefix="adv003-c2m-") as td:
        rt_dir = Path(td)
        tok = initialize(rt_dir)

        for ch in c2m_challenges:
            cid = ch["challenge_id"]
            vis = ch["visible_evidence"]
            # Build evidence_store from visible_evidence (keeping UNVERIFIED/mismatched status intact)
            store = {
                ev["source_id"]: {"discipline": ch["domain"], "key": ev["key"], "value": ev["value"],
                                  "uncertain": ev["verification_status"] != "VERIFIED"}
                for ev in vis
            }
            # Test 2a: V4.2 Evidence Verification + ContextEnvelope
            env = ContextEnvelope(
                investigation_id=cid,
                originating_question=ch["question_for_muse"],
                originating_specialty="Legal" if ch["domain"] == "cross_specialty" else ch["domain"].capitalize(),
                current_specialty=ch["domain"].capitalize(),
                consultation_purpose=f"evaluate_{ch['domain']}",
                evidence_store=store,
                evidence_refs=vis,
                authority=0,
            )
            ev_check = verify_evidence_records(env)

            # Test 2b: Real Build 029 pathway C investigation (HOLD -> discriminating measurement request)
            avail = ch["available_measurements_to_request"]
            if len(avail) >= 2:
                # Formulate competing hypotheses over the available measurements (no answer leakage)
                prob_c = {
                    "pathway": "C", "budget": 2, "premises": ["observable"], "rules": [],
                    "hypotheses": {
                        "chatgpt_claim_supported": {"requires": ["observable"], "predicts": {avail[0]: 1, avail[1]: 1}},
                        "alternative_fault_or_artifact": {"requires": ["observable"], "predicts": {avail[0]: 0, avail[1]: 1}},
                        "unresolved_or_contaminated": {"requires": ["observable"], "predicts": {avail[0]: 0, avail[1]: 0}},
                    },
                }
                p_res = call(rt_dir, tok, {"authority": 0, "operation": "start025", "run": f"{cid}-inv", "problem": prob_c, "arm": "A"})
                p_dec = p_res["decision"]
                p_funcs = [e["function"] for e in p_res.get("trace", []) if e.get("function")]
            else:
                p_dec = {"status": "SKIPPED"}
                p_funcs = []

            # Test 2c: Cross-specialty consultation if C2M-05 (Engineering -> Legal)
            if cid == "C2M-05":
                env_cs = ContextEnvelope(
                    investigation_id="C2M-05-cs",
                    originating_question=ch["question_for_muse"],
                    originating_specialty="Medical",  # Using available Medical->Legal or Engineering check
                    current_specialty="Engineering",
                    consultation_purpose="assess_legal_duty_from_malfunction",
                    evidence_store=store,
                    evidence_refs=vis,
                    authority=0,
                )
                cs_out = run_consultation(env_cs, "Legal")
                cs_status = cs_out.coherence_status
                cs_funcs = [t["function_id"] for t in cs_out.execution_trace]
            else:
                cs_status = None
                cs_funcs = []

            # Independent post-hoc assessment of MUSE's behavior
            # Note: C2M-04 has stored_digest != recovered_digest; check if MUSE has a digest-equality rule or only HOLDs
            digest_vals = {r["value"] for r in ev_check["verified"] if "digest" in r["key"]}
            c2m_results.append({
                "challenge_id": cid,
                "domain": ch["domain"],
                "chatgpt_claim": ch["chatgpt_generated_claim"],
                "verify_evidence_records": ev_check,
                "pathway_c_decision": p_dec,
                "pathway_c_functions": p_funcs,
                "cross_specialty_status": cs_status,
                "cross_specialty_functions": cs_funcs,
                "digest_mismatch_observed_in_verified": len(digest_vals) > 1,
            })

    summary = {
        "experiment_id": "MUSE-BUILD030-ADV003-REPLAY",
        "chatgpt_on_m2c": {
            "total": len(m2c_scores),
            "correct_detections": sum(1 for r in m2c_scores if r["referee_score"]["correct_detection"]),
            "false_positives": sum(1 for r in m2c_scores if r["referee_score"]["false_positive"]),
            "false_negatives": sum(1 for r in m2c_scores if r["referee_score"]["false_negative"]),
            "discriminating_requests_matched": sum(1 for r in m2c_scores if r["referee_score"]["requested_discriminating_measurement"]),
            "details": m2c_scores,
        },
        "muse_on_c2m": c2m_results,
        "response_origin": "archived ChatGPT responses",
        "problem_origin": "harness-authored hypotheses over structured fixture evidence",
    }

    with args.output.open("x") as output:
        json.dump(summary, output, indent=2, default=str)


if __name__ == "__main__":
    main()
