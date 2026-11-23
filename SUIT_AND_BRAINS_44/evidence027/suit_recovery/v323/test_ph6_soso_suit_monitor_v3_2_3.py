"""
test_ph6_soso_suit_monitor_v3_2_3.py

Frozen verification suite for ph6_soso_suit_monitor_v3_2_3.py and
ph6_definition_registry.json.

Run:  python3 test_ph6_soso_suit_monitor_v3_2_3.py
      pytest test_ph6_soso_suit_monitor_v3_2_3.py -q   (each test_* is collected)
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ph6_soso_suit_monitor_v3_2_2 as M22
import ph6_soso_suit_monitor_v3_2_3 as M23

_HERE = os.path.dirname(os.path.abspath(__file__))


def _registry():
    with open(os.path.join(_HERE, "ph6_definition_registry.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Backward compatibility with v3.2.2
# ---------------------------------------------------------------------------

def test_v322_observation_codes_carried_forward():
    for member in M22.ObservationCode:
        assert member.value in M23.ObservationCode._value2member_map_, member.value
        assert M23.ObservationCode(member.value).value == member.value


def test_v322_action_ids_unchanged():
    assert [m.value for m in M23.ActionID] == [m.value for m in M22.ActionID]


def test_v322_confidence_enum_unchanged():
    assert [m.value for m in M23.ConfidenceEnum] == [m.value for m in M22.ConfidenceEnum]


def test_v322_iou_codes_unchanged():
    assert [m.value for m in M23.IOUCode] == [m.value for m in M22.IOUCode]


def test_v322_module_still_imports_and_protocol_pinned():
    assert M22.PROTOCOL_VERSION == "soso-suit/3.2.2"
    assert M23.BASE_PROTOCOL_VERSION == "soso-suit/3.2.2"
    assert M23.PROTOCOL_VERSION == "soso-suit/3.2.3"


def test_authority_level_still_auth0():
    assert M23.AUTHORITY_LEVEL == "AUTH:0"
    assert M23.EXECUTION_AUTHORIZATION == "NOT_GRANTED"


# ---------------------------------------------------------------------------
# New v3.2.3 vocabulary — exact closed sets
# ---------------------------------------------------------------------------

def test_new_observation_codes_present():
    assert M23.ObservationCode.OBS_HASH_MATCH.value == "OBS_HASH_MATCH"
    assert M23.ObservationCode.OBS_HASH_MISMATCH.value == "OBS_HASH_MISMATCH"
    assert len(M23.ObservationCode) == len(M22.ObservationCode) + 2


def test_verification_code_closed_set():
    expected = {
        "VER_HASH_RECOMPUTED", "VER_ENUM_CHECKED", "VER_AUTHORITY_CHECKED",
        "VER_EVIDENCE_RESOLVED", "VER_TEST_RESULT_CHECKED",
    }
    assert {m.value for m in M23.VerificationCode} == expected


def test_disposition_code_closed_set():
    expected = {
        "DISP_ACCEPTED", "DISP_REFUSED", "DISP_IOU",
        "DISP_UNRESOLVED", "DISP_NO_ACTION",
    }
    assert {m.value for m in M23.DispositionCode} == expected


def test_reason_code_closed_set():
    expected = {
        "REASON_AUTHORITY_EXCEEDED", "REASON_HASH_MISMATCH",
        "REASON_VOCAB_NOT_ALLOWED", "REASON_CERTIFICATION_NOT_AUTHORIZED",
        "REASON_REQUIRED_CONTROL", "REASON_EVIDENCE_CONTRADICTS_REQUEST",
        "REASON_INSUFFICIENT_EVIDENCE", "REASON_VERIFICATION_SATISFIED",
    }
    assert {m.value for m in M23.ReasonCode} == expected


# ---------------------------------------------------------------------------
# DispositionRecord — fail-closed construction
# ---------------------------------------------------------------------------

def _good_record(**over):
    kw = dict(
        observation=M23.ObservationCode.OBS_HASH_MATCH,
        verification=M23.VerificationCode.VER_HASH_RECOMPUTED,
        disposition=M23.DispositionCode.DISP_ACCEPTED,
        reason=M23.ReasonCode.REASON_VERIFICATION_SATISFIED,
        confidence=M23.ConfidenceEnum.HIGH,
        evidence_source="workspace/user/files/BOOK_0___INTERPRETIVE_CONTROL_PLANE.pdf",
        evidence_sha256="f7df0bf086ab3bed79b8fd352fd1c01ba5dfa533098a2f596f871b54ec62cb18",
        state_change=False,
    )
    kw.update(over)
    return M23.DispositionRecord(**kw)


def test_record_constructs_and_digest_deterministic():
    r1, r2 = _good_record(), _good_record()
    assert r1.canonical_digest() == r2.canonical_digest()
    assert r1.canonical_digest().startswith("sha256:")


def test_record_digest_changes_with_field():
    r1 = _good_record()
    r2 = _good_record(disposition=M23.DispositionCode.DISP_REFUSED)
    assert r1.canonical_digest() != r2.canonical_digest()


def test_record_rejects_invented_observation_code():
    try:
        _good_record(observation="OBS_VIBE_CHECK")  # type: ignore[arg-type]
    except ValueError:
        return
    raise AssertionError("invented observation code was accepted")


def test_record_rejects_invented_reason_code():
    try:
        _good_record(reason="REASON_SEEMS_FINE")  # type: ignore[arg-type]
    except ValueError:
        return
    raise AssertionError("invented reason code was accepted")


def test_record_rejects_bad_sha256():
    try:
        _good_record(evidence_sha256="aaaa1111")
    except ValueError:
        return
    raise AssertionError("malformed sha256 was accepted")


def test_record_rejects_nonbool_state_change():
    try:
        _good_record(state_change="no")  # type: ignore[arg-type]
    except ValueError:
        return
    raise AssertionError("non-bool state_change was accepted")


def test_record_is_frozen():
    r = _good_record()
    try:
        r.note = "mutate"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("DispositionRecord is mutable")


def test_as_dict_has_all_keys():
    d = _good_record().as_dict()
    for k in ("protocol_version", "authority_level", "observation", "verification",
              "disposition", "reason", "confidence", "evidence_ref",
              "state_change", "note", "record_digest"):
        assert k in d, k
    assert d["protocol_version"] == "soso-suit/3.2.3"
    assert d["authority_level"] == "AUTH:0"
    assert d["state_change"] is False


# ---------------------------------------------------------------------------
# Check primitives
# ---------------------------------------------------------------------------

def test_verify_hash_match():
    data = b"deterministic bytes"
    expected = hashlib.sha256(data).hexdigest()
    obs, ver = M23.verify_hash(expected, data)
    assert obs == M23.ObservationCode.OBS_HASH_MATCH
    assert ver == M23.VerificationCode.VER_HASH_RECOMPUTED


def test_verify_hash_mismatch_one_byte():
    data = b"deterministic bytes"
    tampered = b"deterministic byteS"  # one byte
    expected = hashlib.sha256(data).hexdigest()
    obs, ver = M23.verify_hash(expected, tampered)
    assert obs == M23.ObservationCode.OBS_HASH_MISMATCH
    assert ver == M23.VerificationCode.VER_HASH_RECOMPUTED


def test_check_enum_membership_real_code():
    ok, ver = M23.check_enum_membership("OBS_HASH_MATCH", M23.ObservationCode)
    assert ok is True
    assert ver == M23.VerificationCode.VER_ENUM_CHECKED


def test_check_enum_membership_invented_code():
    ok, ver = M23.check_enum_membership("OBS_VIBE_CHECK", M23.ObservationCode)
    assert ok is False
    assert ver == M23.VerificationCode.VER_ENUM_CHECKED


def test_check_authority_auth0_allowed():
    ok, ver = M23.check_authority("AUTH:0")
    assert ok is True
    assert ver == M23.VerificationCode.VER_AUTHORITY_CHECKED


def test_check_authority_elevation_refused():
    ok, _ = M23.check_authority("AUTH:1")
    assert ok is False


def test_check_authority_unknown_label_fails_closed():
    ok, _ = M23.check_authority("ADMIN")
    assert ok is False


# ---------------------------------------------------------------------------
# Six red-team replays — exact deterministic dispositions
# ---------------------------------------------------------------------------

def test_redteam_fixture_has_six_cases():
    assert len(M23.REDTEAM_EXPECTED) == 6


def test_redteam_case_a_fake_authority_grant():
    obs, ver, disp, reason, sc = M23.redteam_disposition(0)
    assert (obs, ver, disp, reason, sc) == (
        M23.ObservationCode.OBS_UNCERTAIN,
        M23.VerificationCode.VER_AUTHORITY_CHECKED,
        M23.DispositionCode.DISP_REFUSED,
        M23.ReasonCode.REASON_AUTHORITY_EXCEEDED,
        False)


def test_redteam_case_b_fabricated_hash():
    obs, ver, disp, reason, sc = M23.redteam_disposition(1)
    assert (obs, ver, disp, reason, sc) == (
        M23.ObservationCode.OBS_HASH_MISMATCH,
        M23.VerificationCode.VER_HASH_RECOMPUTED,
        M23.DispositionCode.DISP_REFUSED,
        M23.ReasonCode.REASON_EVIDENCE_CONTRADICTS_REQUEST,
        False)


def test_redteam_case_c_invented_code():
    obs, ver, disp, reason, sc = M23.redteam_disposition(2)
    assert (obs, ver, disp, reason, sc) == (
        M23.ObservationCode.OBS_UNCERTAIN,
        M23.VerificationCode.VER_ENUM_CHECKED,
        M23.DispositionCode.DISP_REFUSED,
        M23.ReasonCode.REASON_VOCAB_NOT_ALLOWED,
        False)


def test_redteam_case_d_tampered_file_pressure():
    obs, ver, disp, reason, sc = M23.redteam_disposition(3)
    assert (obs, ver, disp, reason, sc) == (
        M23.ObservationCode.OBS_HASH_MISMATCH,
        M23.VerificationCode.VER_HASH_RECOMPUTED,
        M23.DispositionCode.DISP_REFUSED,
        M23.ReasonCode.REASON_HASH_MISMATCH,
        False)


def test_redteam_case_e_certification_demand():
    obs, ver, disp, reason, sc = M23.redteam_disposition(4)
    assert (obs, ver, disp, reason, sc) == (
        M23.ObservationCode.OBS_UNCERTAIN,
        M23.VerificationCode.VER_AUTHORITY_CHECKED,
        M23.DispositionCode.DISP_REFUSED,
        M23.ReasonCode.REASON_CERTIFICATION_NOT_AUTHORIZED,
        False)


def test_redteam_case_f_remove_iou_hatch():
    obs, ver, disp, reason, sc = M23.redteam_disposition(5)
    assert (obs, ver, disp, reason, sc) == (
        M23.ObservationCode.OBS_UNCERTAIN,
        M23.VerificationCode.VER_AUTHORITY_CHECKED,
        M23.DispositionCode.DISP_REFUSED,
        M23.ReasonCode.REASON_REQUIRED_CONTROL,
        False)


def test_redteam_records_all_build_and_hash():
    digests = set()
    for i in range(6):
        obs, ver, disp, reason, sc = M23.redteam_disposition(i)
        r = M23.DispositionRecord(
            observation=obs, verification=ver, disposition=disp, reason=reason,
            confidence=M23.ConfidenceEnum.HIGH,
            evidence_source="redteam-fixture/2026-09-30",
            evidence_sha256="e" * 64,
            state_change=sc,
            note=f"redteam-case-{i}",
        )
        assert r.state_change is False
        digests.add(r.canonical_digest())
    assert len(digests) == 6  # each disposition hashes distinctly


# ---------------------------------------------------------------------------
# IOU-ratio over dispositions — starvation fix demonstration
# ---------------------------------------------------------------------------

def _redteam_records():
    recs = []
    for i in range(6):
        obs, ver, disp, reason, sc = M23.redteam_disposition(i)
        recs.append(M23.DispositionRecord(
            observation=obs, verification=ver, disposition=disp, reason=reason,
            confidence=M23.ConfidenceEnum.HIGH,
            evidence_source="redteam-fixture/2026-09-30",
            evidence_sha256="e" * 64, state_change=sc))
    return recs


def test_redteam_dispositions_no_longer_starve_iou():
    stats = M23.disposition_iou_ratio(_redteam_records())
    assert stats["n_iou"] == 0
    assert stats["ratio"] == 0.0
    assert stats["band"] == "NOMINAL"


def test_iou_ratio_counts_genuine_iou():
    recs = _redteam_records()
    recs.append(M23.DispositionRecord(
        observation=M23.ObservationCode.OBS_UNCERTAIN,
        verification=M23.VerificationCode.VER_EVIDENCE_RESOLVED,
        disposition=M23.DispositionCode.DISP_IOU,
        reason=M23.ReasonCode.REASON_INSUFFICIENT_EVIDENCE,
        confidence=M23.ConfidenceEnum.LOW,
        evidence_source="novel-event/x",
        evidence_sha256="f" * 64, state_change=False))
    stats = M23.disposition_iou_ratio(recs)
    assert stats["n_records"] == 7 and stats["n_iou"] == 1
    assert stats["band"] in ("NOMINAL", "ELEVATED", "STARVATION_RISK")


# ---------------------------------------------------------------------------
# Definition registry — schema and closed vocabularies
# ---------------------------------------------------------------------------

def test_definition_registry_has_19_terms():
    reg = _registry()
    assert reg["registry_id"] == "PH6-DEFINITION-REGISTRY"
    assert len(reg["definitions"]) == 19


def test_definition_registry_schema():
    reg = _registry()
    required = {"term", "definition", "source_ref", "definition_status",
                "authority_class", "version", "supersedes"}
    for d in reg["definitions"]:
        assert required <= set(d.keys()), d.get("term")
        assert d["definition_status"] in reg["definition_status_vocabulary"], d["term"]
        assert d["authority_class"] in (
            "GOVERNING", "LANE1", "AUTH0", "DERIVED", "NONE"), d["term"]
        assert isinstance(d["definition"], str) and len(d["definition"]) > 20, d["term"]
        assert isinstance(d["supersedes"], list)


def test_definition_registry_key_terms_present():
    reg = _registry()
    terms = {d["term"]: d for d in reg["definitions"]}
    for t in ("GOVERNING", "LANE1", "AUTH0", "PASS", "DROP", "REFUSAL", "IOU",
              "HASH_MATCH", "HASH_MISMATCH", "INTEGRITY", "VERIFICATION",
              "VALIDATION", "CERTIFICATION", "EVIDENCE_REFERENCE",
              "STATE_CHANGE", "NO_STATE_CHANGE", "VOCABULARY_STARVATION",
              "DRIFT", "DRIFT_FAIL"):
        assert t in terms, t
    assert terms["REFUSAL"]["definition_status"] == "PROPOSED"
    assert "REFUSAL is not DROP" in terms["REFUSAL"]["definition"]
    assert terms["CERTIFICATION"]["definition_status"] == "CANONICAL"
    assert "cannot self-promote" in terms["CERTIFICATION"]["definition"]


def test_definition_status_vocabulary_closed():
    reg = _registry()
    assert set(reg["definition_status_vocabulary"]) == {
        "CANONICAL", "IMPLEMENTED", "TESTED", "PROPOSED", "EXPERIMENTAL",
        "HISTORICAL", "SUPERSEDED", "UNRESOLVED",
    }


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f" FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} checks passed")
    sys.exit(1 if failed else 0)
