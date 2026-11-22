"""SoSo Suit 6.1: procedural memory, cross-domain transfer, continuity, modes, trace. Real authenticated muse024.call."""
import copy, json, os, signal, subprocess, sys, sqlite3
from pathlib import Path
import pytest

from eval061 import run as E
from muse024 import call, initialize, issue
from suit61 import compare as C
from suit61.agent import Agent

ROOT = Path(__file__).resolve().parents[1]
TASKS = {t["id"]: t for t in E.TASKS}
SKILLS = {s["skill_id"]: s for s in E.SKILLS}


@pytest.fixture
def st(tmp_path):
    root, tok = E.station(tmp_path)
    return root, tok


@pytest.fixture
def trained(st):
    root, tok = st
    log = E.train(root, tok, transfers=True)
    return root, tok, log


def suit(root, tok, role, op, **kw):
    if op in ("suit_practice", "suit_attempt"):
        return E.record_trial(root, tok[role], op, **kw)
    return call(root, tok[role], {"operation": op, **kw})


# ------------------------------------------------------------------ structural comparison (pure)
def comp(skill_id, task_id, ordering=None):
    return C.compare(C.normalize_skill(SKILLS[skill_id]), C.normalize_target(TASKS[task_id]["target"]), ordering)


def test_names_and_labels_never_influence_the_comparison():
    base = comp("fastener-release-v1", "N1")
    s, t = copy.deepcopy(SKILLS["fastener-release-v1"]), copy.deepcopy(TASKS["N1"]["target"])
    s["label"], s["skill_id"], t["label"] = "dentist: totally different words", "other-id", "surgeon task"
    again = C.compare(C.normalize_skill(s), C.normalize_target(t), None)
    assert again["elements"] == base["elements"] and again["disposition"] == base["disposition"]


def test_high_similarity_does_not_override_an_incompatible_element():
    c = comp("fastener-release-v1", "N1")
    assert c["similarity_ppm"] > 800000 and c["disposition"] == "DO_NOT_TRANSFER"
    assert any(e["kind"] == "authorization" and e["state"] == "INCOMPATIBLE" for e in c["elements"])


@pytest.mark.parametrize("task,kind", [("N2", "consequence"), ("N3", "constraint"), ("N4", "constraint"), ("N5", "constraint"), ("N6", "constraint")])
def test_each_misleading_similarity_is_an_incompatible_element(task, kind):
    skill = "component-seat-v1" if task == "N6" else "fastener-release-v1"
    assert any(e["kind"] == kind and e["state"] == "INCOMPATIBLE" for e in comp(skill, task)["elements"])


def test_states_are_kept_apart_reusable_adaptation_unsupported():
    c = comp("fastener-release-v1", "P5")
    assert c["disposition"] == "TRANSFER_PROPOSED_ADAPTATION" and c["counts"]["INCOMPATIBLE"] == 0
    ordering_unreviewed = comp("component-seat-v1", "P3")
    assert ordering_unreviewed["disposition"] == "TRANSFER_PROPOSED_NEEDS_EVIDENCE"
    viol = comp("component-seat-v1", "P3", "VIOLATED")
    el = [e for e in viol["elements"] if e["kind"] == "ordering"][0]
    assert el["state"] == "ADAPTATION_REQUIRED" and el["reordered"].index("VERIFY") < el["reordered"].index("INSERT")


def test_registered_primitives_only_and_integer_only():
    bad = copy.deepcopy(SKILLS["fastener-release-v1"]); bad["steps"][0]["verb"] = "TELEPORT"
    with pytest.raises(ValueError):
        C.normalize_skill(bad)
    bad = copy.deepcopy(SKILLS["fastener-release-v1"]); bad["context"]["numeric"]["force"]["value"] = 1.5
    with pytest.raises(ValueError):
        C.normalize_skill(bad)


# ------------------------------------------------------------------ knowledge states and independent qualification
def test_recorded_skill_is_not_a_qualified_skill(st):
    root, tok = st
    suit(root, tok, "author", "suit_learn", skill=SKILLS["fastener-release-v1"])
    got = suit(root, tok, "agent", "suit_retrieve", target=TASKS["F1"]["target"])
    assert got["mode"] != "REUSE_QUALIFIED_SKILL" and got["qualification"] == "NONE"
    assert suit(root, tok, "agent", "suit_show", kind="skill", item="fastener-release-v1")["state"] == "KNOWN"


def test_practice_makes_practiced_not_qualified_and_qualifier_must_be_independent(st):
    root, tok = st
    tok["author_q"] = issue(root, {"principal": "author_q", "scopes": ["suit:write", "suit:qualify"], "owners": []})["token"]
    suit(root, tok, "author_q", "suit_learn", skill=SKILLS["fastener-release-v1"])
    for i, who in enumerate(("obs1", "obs2")):
        r = suit(root, tok, who, "suit_practice", skill_id="fastener-release-v1", outcome="SUCCESS", evidence_ref=f"fixture-obs:{i}")
    assert r["state"] == "PRACTICED"
    own = suit(root, tok, "author_q", "suit_qualify_skill", skill_id="fastener-release-v1")
    assert own["status"] == "QUALIFICATION_REFUSED" and "qualifier must be independent of author and observers" in own["reasons"]
    assert suit(root, tok, "qual", "suit_qualify_skill", skill_id="fastener-release-v1")["state"] == "QUALIFIED"


def test_scopes_separate_learning_qualification_and_use(trained):
    root, tok, _ = trained
    for role, op, extra in (("agent", "suit_qualify_skill", {"skill_id": "fastener-release-v1"}),
                            ("author", "suit_qualify_transfer", {"transfer_id": "x"}),
                            ("author", "suit_retrieve", {"target": TASKS["F1"]["target"]}),
                            ("agent", "suit_learn", {"skill": SKILLS["connection-inspect-v1"]})):
        with pytest.raises(PermissionError):
            suit(root, tok, role, op, **extra)
    with pytest.raises(PermissionError):
        call(root, "x" * 43, {"operation": "suit_show", "kind": "trace"})


def test_caller_cannot_assert_qualification_by_extra_fields(st):
    root, tok = st
    suit(root, tok, "author", "suit_learn", skill=SKILLS["fastener-release-v1"], qualified=True, state="QUALIFIED")
    assert suit(root, tok, "agent", "suit_show", kind="skill", item="fastener-release-v1")["state"] == "KNOWN"
    r = suit(root, tok, "qual", "suit_qualify_skill", skill_id="fastener-release-v1")
    assert r["status"] == "QUALIFICATION_REFUSED" and "insufficient successful practice" in r["reasons"]


def test_skill_records_are_immutable_and_damage_fails_closed(st):
    root, tok = st
    suit(root, tok, "author", "suit_learn", skill=SKILLS["fastener-release-v1"])
    with pytest.raises(ValueError):
        suit(root, tok, "author", "suit_learn", skill=SKILLS["fastener-release-v1"])
    with sqlite3.connect(root / "observations.sqlite3") as db:
        db.execute("UPDATE suit61_skills SET record=replace(record,'ALIGN','GRIP')")
    with pytest.raises(ValueError, match="digest mismatch"):
        suit(root, tok, "agent", "suit_show", kind="skill", item="fastener-release-v1")


def test_practice_evidence_needs_acceptable_provenance(st):
    root, tok = st
    suit(root, tok, "author", "suit_learn", skill=SKILLS["fastener-release-v1"])
    for bad in ("", "fake:x", "unverified:thing"):
        with pytest.raises(ValueError):
            suit(root, tok, "obs1", "suit_practice", skill_id="fastener-release-v1", outcome="SUCCESS", evidence_ref=bad)


# ------------------------------------------------------------------ transfer lifecycle
def test_transfer_states_proposed_tested_qualified_and_reuse_after_restart(trained):
    root, tok, log = trained
    assert log["transfers"]["P1"]["state"] == "TRANSFER_QUALIFIED"
    child = subprocess.run([sys.executable, "-I", "-c",
        "import sys,json;sys.path.insert(0,sys.argv[1]);from muse024 import call\n"
        "s,r,t=json.loads(sys.stdin.read());print(json.dumps(call(r,s,{'operation':'suit_retrieve','target':t})))",
        str(ROOT), ], input=json.dumps([tok["agent"], str(root), TASKS["P1B"]["target"]]), capture_output=True, text=True, check=True)
    got = json.loads(child.stdout)
    assert got["mode"] == "REUSE_QUALIFIED_TRANSFER" and got["plan"]["limits"]["force"] == 25
    assert got["execution_authorization"] == "NOT_GRANTED" and got["authority"] == "AUTH:0"


def test_qualified_transfer_covers_only_the_tested_envelope(trained):
    root, tok, _ = trained
    assert suit(root, tok, "agent", "suit_retrieve", target=TASKS["P1B"]["target"])["mode"] == "REUSE_QUALIFIED_TRANSFER"   # looser limit
    stricter = suit(root, tok, "agent", "suit_retrieve", target=TASKS["P1C"]["target"])
    assert stricter["mode"] == "PROPOSE_TRANSFER"                                                                           # stricter limit
    assert suit(root, tok, "agent", "suit_retrieve", target=TASKS["P5"]["target"])["mode"] == "PROPOSE_TRANSFER"            # different consequences


def test_contradicted_success_counts_as_failure_and_is_retained(trained):
    root, tok, log = trained
    assert log["transfers"]["X2"]["status"] == "QUALIFICATION_REFUSED"
    tid = suit(root, tok, "agent", "suit_retrieve", target=TASKS["X2"]["target"])
    assert tid["mode"] == "DO_NOT_TRANSFER" and "failed transfer retained" in tid["reason"]
    shown = suit(root, tok, "agent", "suit_show", kind="transfer", item=tid["transfer_id"])
    assert [a["effective"] for a in shown["attempts"]] == ["SUCCESS", "FAILURE", "SUCCESS"]
    assert shown["attempts"][1]["reported"] == "SUCCESS" and "measurement outside limit tolerance_um" in shown["attempts"][1]["problems"]


def test_attempt_with_wrong_order_or_missing_measurement_is_a_failure(st):
    root, tok = st
    E.train(root, tok, transfers=False)
    prop = E.propose(root, tok["agent"], "fastener-release-v1", TASKS["P5"]["target"])
    bad = suit(root, tok, "obs1", "suit_attempt", transfer_id=prop["transfer_id"], outcome="SUCCESS", evidence_ref="fixture-obs:a",
               executed_order=["APPLY_FORCE", "ALIGN", "GRIP", "ROTATE", "DETECT_RESISTANCE", "VERIFY", "SELECT_TOOL"], measured={"force": 10, "tolerance_um": 100})
    assert bad["effective_outcome"] == "FAILURE" and "executed order violates precedence" in bad["problems"]
    missing = suit(root, tok, "obs1", "suit_attempt", transfer_id=prop["transfer_id"], outcome="SUCCESS", evidence_ref="fixture-obs:b",
                   executed_order=E.TRAIN["transfers"]["P1"]["attempts"][0]["executed_order"], measured={})
    assert missing["effective_outcome"] == "FAILURE"


def test_incompatible_transfer_cannot_even_be_tested_or_qualified(st):
    root, tok = st
    E.train(root, tok, transfers=False)
    prop = E.propose(root, tok["agent"], "fastener-release-v1", TASKS["N1"]["target"])
    assert prop["disposition"] == "DO_NOT_TRANSFER"
    with pytest.raises(PermissionError):
        suit(root, tok, "obs1", "suit_attempt", transfer_id=prop["transfer_id"], outcome="SUCCESS", evidence_ref="fixture-obs:z",
             executed_order=["ALIGN"], measured={})
    r = suit(root, tok, "qual", "suit_qualify_transfer", transfer_id=prop["transfer_id"])
    assert r["status"] == "QUALIFICATION_REFUSED"


def test_transfer_qualification_needs_independent_qualifier_and_three_clean_attempts(st):
    root, tok = st
    E.train(root, tok, transfers=False)
    tr = E.TRAIN["transfers"]["P1"]
    tok["proposer_q"] = issue(root, {"principal": "proposer_q", "scopes": ["suit:execute", "suit:qualify"], "owners": []})["token"]
    prop = E.propose(root, tok["proposer_q"], tr["skill_id"], tr["target"])
    for i, a in enumerate(tr["attempts"][:2]):
        suit(root, tok, "obs1", "suit_attempt", transfer_id=prop["transfer_id"], **a)
    assert "fewer than 3" in " ".join(suit(root, tok, "qual", "suit_qualify_transfer", transfer_id=prop["transfer_id"])["reasons"])
    suit(root, tok, "obs2", "suit_attempt", transfer_id=prop["transfer_id"], **tr["attempts"][2])
    own = suit(root, tok, "proposer_q", "suit_qualify_transfer", transfer_id=prop["transfer_id"])
    assert own["status"] == "QUALIFICATION_REFUSED" and "qualifier must be independent of proposer and observers" in own["reasons"]
    ok = suit(root, tok, "qual", "suit_qualify_transfer", transfer_id=prop["transfer_id"])
    assert ok["state"] == "TRANSFER_QUALIFIED"
    assert suit(root, tok, "qual", "suit_revoke", kind="transfer", item=prop["transfer_id"], reason="test")["state"] == "REVOKED"
    after = suit(root, tok, "agent", "suit_retrieve", target=TASKS["P1B"]["target"])
    assert after["mode"] != "REUSE_QUALIFIED_TRANSFER"


def test_unfamiliar_is_not_prohibited_and_permission_is_separate(trained):
    root, tok, _ = trained
    ok = suit(root, tok, "agent", "suit_retrieve", target=TASKS["U1"]["target"], permitted=True)
    no = suit(root, tok, "agent", "suit_retrieve", target=TASKS["U1"]["target"], permitted=False)
    assert ok["mode"] == no["mode"] == "UNFAMILIAR_REDISCOVERY_REQUIRED" and ok["familiarity"] == "UNFAMILIAR"
    assert ok["operational_permission"] == "PERMITTED_BY_CALLER" and no["operational_permission"] == "NOT_PERMITTED_BY_CALLER"
    assert ok["execution_authorization"] == "NOT_GRANTED"
    assert "PROHIBITED" not in json.dumps(ok).upper() and "reason" in ok


# ------------------------------------------------------------------ continuity and recovery
def admit(root, tok, tid, target, role="agent"):
    got = suit(root, tok, role, "suit_retrieve", task_id=tid, target=target)
    return suit(root, tok, role, "suit_begin", task_id=tid, target=target, skill_id=got["skill_id"],
                transfer_id=got.get("transfer_id"), retrieval_receipt=got["receipt"])


def observe_step(root, tok, tid, idx, verb, ref):
    return suit(root, tok, "obs1", "suit_observe", evidence_ref=ref, observation={
        "kind": "step", "task": tid, "principal": "agent", "index": idx, "verb": verb, "outcome": "SUCCESS"})


def test_task_continuity_duplicate_ack_and_ordering(trained):
    root, tok, _ = trained
    first = admit(root, tok, "t1", TASKS["F1"]["target"])
    verify = lambda **kw: suit(root, tok, "agent", "suit_ack", task_id="t1", **kw)
    with pytest.raises(ValueError, match="registered observation"):
        verify(index=0, evidence_ref="fixture-obs:test-1")
    observe_step(root, tok, "t1", 0, first["steps"][0], "fixture-obs:test-1")
    assert verify(index=0, evidence_ref="fixture-obs:test-1")["step"] == 1
    assert verify(index=0, evidence_ref="fixture-obs:test-1")["duplicate"]
    with pytest.raises(ValueError):
        verify(index=0, evidence_ref="fixture-obs:other")
    with pytest.raises(ValueError):
        verify(index=2, evidence_ref="fixture-obs:other")
    assert suit(root, tok, "agent", "suit_next", task_id="t1")["step"] == first["steps"][1]


def test_caller_plan_and_missing_capability_are_rejected(trained):
    root, tok, _ = trained
    with pytest.raises(ValueError, match="overrides"):
        suit(root, tok, "agent", "suit_begin", task_id="t2", plan={"steps": [{"verb": "VERIFY"}]})
    tok["runner"] = issue(root, {"principal": "runner", "scopes": ["suit:execute"], "owners": []})["token"]
    with pytest.raises(PermissionError):
        admit(root, tok, "t2", TASKS["F1"]["target"], "runner")


def test_interrupted_ack_rolls_back_and_sigkill_restart_resumes_without_repeating(trained):
    root, tok, _ = trained
    first = admit(root, tok, "t3", TASKS["F1"]["target"])
    for i in range(2):
        observe_step(root, tok, "t3", i, first["steps"][i], f"fixture-obs:k{i}")
    def boom(point):
        raise RuntimeError(point)
    with pytest.raises(RuntimeError):
        call(root, tok["agent"], {"operation": "suit_ack", "task_id": "t3", "index": 0,
                                 "evidence_ref": "fixture-obs:k0"}, fault=boom)
    assert suit(root, tok, "agent", "suit_resume", task_id="t3")["step"] == 0
    code = ("import sys,json,os,signal;sys.path.insert(0,sys.argv[1]);from muse024 import call\n"
            "r,t=json.loads(sys.stdin.read())\n"
            "for i in range(2): call(r,t,{'operation':'suit_ack','task_id':'t3','index':i,'evidence_ref':'fixture-obs:k%d'%i})\n"
            "os.kill(os.getpid(),signal.SIGKILL)")
    p = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], input=json.dumps([str(root), tok["agent"]]), text=True)
    assert p.returncode == -signal.SIGKILL
    resumed = suit(root, tok, "agent", "suit_resume", task_id="t3")
    assert resumed["step"] == 2 and [c["index"] for c in resumed["completed"]] == [0, 1]


def test_joint_admission_qualified_transfer_then_complete_and_reuse(trained):
    root, tok, _ = trained
    target = TASKS["P1B"]["target"]
    agent = Agent(root, suit_token=tok["agent"], brain_token=tok["brain"])
    admitted = agent.admit("joint", target)
    assert admitted["status"] == "READY" and agent.brain.invocations >= 2
    assert admitted["task"]["transfer_id"]
    for i, verb in enumerate(admitted["task"]["steps"]):
        assert suit(root, tok, "agent", "suit_next", task_id="joint")["step"] == verb
        ref = f"fixture-obs:joint-{i}"
        observe_step(root, tok, "joint", i, verb, ref)
        outcome = suit(root, tok, "agent", "suit_ack", task_id="joint", index=i, evidence_ref=ref)
    assert outcome["status"] == "COMPLETE"
    restored = Agent(root, suit_token=tok["agent"], brain_token=tok["brain"])
    assert restored.admit("joint-reuse", target)["status"] == "READY"


# ------------------------------------------------------------------ trace integrity
def test_trace_chain_verifies_detects_tamper_and_survives_concurrent_processes(trained):
    root, tok, _ = trained
    before = suit(root, tok, "qual", "suit_show", kind="trace")
    assert before["intact"] and before["events"] > 20
    code = ("import sys;sys.path.insert(0,sys.argv[1]);from muse024 import call\n"
            "for i in range(10): call(sys.argv[2],sys.argv[3],{'operation':'suit_retrieve','target':__import__('json').loads(sys.argv[4])})")
    procs = [subprocess.Popen([sys.executable, "-I", "-c", code, str(ROOT), str(root), tok["agent"], json.dumps(TASKS["F1"]["target"])]) for _ in range(5)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    after = suit(root, tok, "qual", "suit_show", kind="trace")
    assert after["intact"] and after["events"] == before["events"] + 50
    with sqlite3.connect(root / "observations.sqlite3") as db:
        db.execute("UPDATE suit61_trace SET kind='TAMPERED' WHERE seq=3")
    assert suit(root, tok, "qual", "suit_show", kind="trace")["intact"] is False


# ------------------------------------------------------------------ four modes
def run_agent(root, tok, suit_on, mask_on, transfer=True):
    a = Agent(root, suit_token=tok["agent"], brain_token=tok["brain"], suit=suit_on, mask=mask_on, transfer=transfer)
    outs = {i: a.handle(t["target"]) for i, t in TASKS.items()}
    return a, outs


def test_four_modes_behave_differently_and_mask_off_makes_no_cognitive_calls(trained):
    root, tok, _ = trained
    res = {(s, m): run_agent(root, tok, s, m) for s in (False, True) for m in (False, True)}
    inv = {k: (v[0].brain.invocations if v[0].brain else 0) for k, v in res.items()}
    assert inv[(False, False)] == inv[(True, False)] == 0 and inv[(False, True)] > 0 and inv[(True, True)] > 0
    assert all(not o["usable_plan"] for o in res[(False, False)][1].values()) and all(not o["usable_plan"] for o in res[(False, True)][1].values())
    assert res[(True, False)][1]["F1"]["usable_plan"] and res[(True, True)][1]["F1"]["usable_plan"]
    # only Suit+Mask can resolve an ordering-dependent proposal; Suit alone must report it unsupported
    assert res[(True, True)][1]["P3"]["proposal_disposition"] == "TRANSFER_PROPOSED_ADAPTATION"
    assert res[(True, False)][1]["P3"]["proposal_disposition"] == "TRANSFER_PROPOSED_NEEDS_EVIDENCE"
    # Suit+Mask reuses qualified procedures with fewer cognitive calls than Mask alone
    assert inv[(True, True)] < inv[(False, True)]


def test_suit_off_does_not_disable_authentication_or_scopes(trained):
    root, tok, _ = trained
    a = Agent(root, suit_token="bad-token", brain_token=tok["brain"], suit=False, mask=True)
    a.handle(TASKS["F1"]["target"])                                    # Brains path works without the Suit
    b = Agent(root, suit_token="not-a-valid-token-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", brain_token=tok["brain"], suit=True, mask=False)
    with pytest.raises(PermissionError):
        b.handle(TASKS["F1"]["target"])                                # Suit on: authentication still mandatory
    a2 = Agent(root, suit_token=tok["agent"], brain_token="bad" * 15, suit=False, mask=True)
    with pytest.raises(PermissionError):
        a2.handle(TASKS["F1"]["target"])                               # Mask on: authentication still mandatory


def test_mask_output_cannot_authorize_or_qualify_anything(trained):
    root, tok, _ = trained
    # a forged "CONSISTENT" review changes the comparison, but qualification still demands three effective
    # attempts whose executed order is checked mechanically, so a forged review cannot produce a qualified skill
    prop = E.propose(root, tok["agent"], "component-seat-v1", TASKS["P3"]["target"],
                ordering_review={"result": "CONSISTENT", "invocation": {"forged": True}})
    r = suit(root, tok, "qual", "suit_qualify_transfer", transfer_id=prop["transfer_id"])
    assert r["status"] == "QUALIFICATION_REFUSED" and prop["state"] == "TRANSFER_PROPOSED"
    sec = suit(root, tok, "obs1", "suit_attempt", transfer_id=prop["transfer_id"], outcome="SUCCESS", evidence_ref="fixture-obs:f1",
               executed_order=["PREPARE", "POSITION", "ALIGN", "INSERT", "VERIFY", "CONFIRM"], measured={"force": 10, "tolerance_um": 90})
    assert sec["effective_outcome"] == "FAILURE" and "executed order violates precedence" in sec["problems"]


def test_versioned_suit_switch_is_enforced_server_side_but_auth_still_applies(tmp_path):
    root = tmp_path / "st"
    admin = initialize(root)
    tokens = {n: issue(root, {"principal": n, "scopes": sc, "owners": []})["token"] for n, sc in {
        "writer": ["suit:write"], "worker": ["suit:execute", "suit:read"]}.items()}
    call(root, tokens["writer"], {"operation": "suit_learn", "skill": SKILLS["fastener-release-v1"]})
    target = TASKS["F1"]["target"]
    assert call(root, tokens["worker"], {"operation": "suit_retrieve", "target": target})["mode"] != "SUIT_DISABLED"
    cfg = {"suit_enabled": False, "mask_enabled": True, "policy": {}}
    call(root, admin, {"operation": "agent_configure", "agent": "worker", "expected_version": 0, "configuration": cfg})
    off = call(root, tokens["worker"], {"operation": "suit_retrieve", "target": target})
    assert off["status"] == "SUIT_DISABLED" and off["agent_configuration_version"] == 1 and off["authority"] == "AUTH:0"
    with pytest.raises(PermissionError):
        call(root, "z" * 43, {"operation": "suit_retrieve", "target": target})          # OFF never relaxes authentication
    cfg["suit_enabled"] = True
    call(root, admin, {"operation": "agent_configure", "agent": "worker", "expected_version": 1, "configuration": cfg})
    on = call(root, tokens["worker"], {"operation": "suit_retrieve", "target": target})
    assert on["agent_configuration_version"] == 2 and on["mode"] != "SUIT_DISABLED"
