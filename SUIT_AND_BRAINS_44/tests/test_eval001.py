"""EVAL-001 — cognitive capability evaluation tests.

Runs the frozen Contract 002 baseline through six experiments
plus the controls, the exact-signature-vs-contradiction sweep
and the restart-retention check. Every task uses frozen
deterministic observations; the three configurations run the
SAME tasks, so the comparison is fair.

Controls:
  UNTRAINED — no training, no qualification loop (trial only).
  TRAINED   — baseline training, description transfer OFF.
  FULL      — baseline training, description transfer ON.
"""
import random

import pytest

from contract.harness import Harness
from contract.eval001 import (FAMILIES, check_projections, classify,
                              make_config_harness, make_task,
                              qualify_learned, registered_id, retention_rate,
                              run_sequence, seed_legacy_experience,
                              summarize)
from contract.registry import by_native

SEED = 20261118
FAMS6 = ["valve", "sensor", "pump", "blocked", "narrow", "slope"]


def e1_tasks():
    """24 tasks: 4 rounds x 6 families. The slope task is a
    synonym (reworded) in rounds 2 and 4 — the hard case where
    the vocabulary does not overlap the procedure's description."""
    rng = random.Random(SEED)
    tasks = []
    for r in range(4):
        for f in FAMS6:
            tasks.append(make_task(rng, f, f"e1-r{r}-{f}",
                                   synonym=(f == "slope" and r in (1, 3))))
    return tasks


def e1_rounds():
    return [[r * 6 + i for i in range(6)] for r in range(4)]


class TestE1LearningCurve:
    """Does the system improve as experience accumulates? The
    trial solves everything solvable, so the resolution rate is
    1.00 for every configuration — the learning signal is the
    TRUSTED first-attempt rate (exact/transfer reuse) and the
    investigation cost, not raw solvability."""

    def _run(self, name, qualify):
        tasks = e1_tasks()
        h = make_config_harness(name, tasks)
        try:
            outs = run_sequence(h, tasks, e1_rounds(),
                                random.Random(99), qualify=qualify)
            per_round = [summarize([o for o in outs if o["round"] == ri])
                         for ri in range(4)]
            return summarize(outs), per_round, outs
        finally:
            h.close()

    def test_untrained_never_reuses(self):
        s, per_round, _ = self._run("UNTRAINED", False)
        assert s["resolved"] == 24 and s["false_resolved"] == 0
        # It never learns: every task is a fresh trial.
        assert s["first_attempt"] == 0
        assert all(pr["first_attempt"] == 0 for pr in per_round)

    def test_trained_learns_within_family(self):
        s, per_round, _ = self._run("TRAINED", True)
        assert s["resolved"] == 24 and s["false_resolved"] == 0
        # Round 0: only the trained families reuse (4/6).
        assert per_round[0]["first_attempt"] == 4
        # After the qualification loop, the novel families reuse too.
        assert per_round[3]["first_attempt"] == 6
        assert s["first_attempt"] >= 20

    def test_full_learns_cross_family(self):
        s, per_round, _ = self._run("FULL", True)
        assert s["resolved"] == 24 and s["false_resolved"] == 0
        # The clean slope task transfers cross-family in round 0
        # (P7 learned for blocked applies to slope by description).
        assert per_round[0]["first_attempt"] == 5
        assert per_round[3]["first_attempt"] == 6
        assert s["first_attempt"] >= 22
        # And the full system is cheaper than the untrained control.
        _, _, untrained = self._run("UNTRAINED", False)
        assert s["attempts_per_task"] <= summarize(untrained)["attempts_per_task"]

    def test_legacy_procedures_must_be_learned(self):
        """P8/P9 are REQUIRES_AUTHORIZATION, so the trial never
        tries them blind. The only way to solve a P8/P9 task is to
        have learned it from a prior authorized execution (seeded
        here as setup). The untrained control cannot solve them."""
        tasks = e1_tasks()
        legacy = []
        rng = random.Random(SEED + 1)
        for i, proc in enumerate(("P8", "P9", "P8", "P9")):
            fam = "sensor" if proc == "P8" else "pump"
            t = make_task(rng, fam, f"e1-legacy-{i}")
            # The legacy task's signature is new, so the exact
            # index only has the seeded record.
            t["signature"] = ("svc_hung_legacy" if proc == "P8"
                              else "cfg_bad_legacy")
            t["correct"] = proc
            legacy.append(t)
        # Seed the prior authorized qualification (setup, not a
        # pipeline execution — the pipeline cannot execute P8/P9
        # without a prior record).
        seed_tasks = [dict(legacy[0], task_id="eval-seed-p8"),
                      dict(legacy[1], task_id="eval-seed-p9")]
        for name, qual in (("UNTRAINED", False), ("TRAINED", True),
                           ("FULL", True)):
            h = make_config_harness(name, tasks + legacy)
            try:
                if name != "UNTRAINED":
                    for st in seed_tasks:
                        seed_legacy_experience(h, st["correct"], st)
                outs = []
                for t in legacy:
                    rep = h.pipe.run({k: v for k, v in t.items()
                                      if k != "correct"})
                    outs.append({"task": t, "report": rep,
                                 "failure": classify(t, rep, h.policy)})
                s = summarize(outs)
                if name == "UNTRAINED":
                    assert s["resolved"] == 0
                    assert all(o["failure"] == "correct_escalation"
                               for o in outs)
                else:
                    assert s["resolved"] == 4 and s["false_resolved"] == 0
                    assert s["first_attempt"] == 4
            finally:
                h.close()


class TestE2LongHistory:
    """100 mixed-family tasks in 10 rounds. The projections must
    stay consistent as the history grows, and the investigation
    cost must not rise."""

    def test_long_history_consistent_and_cheap(self):
        rng = random.Random(SEED + 2)
        fams = ["valve", "sensor", "pump", "blocked", "narrow", "slope",
                "flood"]
        tasks = [make_task(rng, fams[i % len(fams)], f"e2-{i}")
                 for i in range(100)]
        rounds = [[i for i in range(b * 10, (b + 1) * 10)]
                  for b in range(10)]
        for name in ("TRAINED", "FULL"):
            h = make_config_harness(name, tasks)
            try:
                outs = run_sequence(h, tasks, rounds,
                                    random.Random(99), qualify=True)
                s = summarize(outs)
                assert s["resolved"] == 100 and s["false_resolved"] == 0
                # The projections agree after every block.
                assert check_projections(h) == []
                # Cost does not rise with history length: the last
                # block is no more expensive than the first.
                first = summarize([o for o in outs if o["round"] == 0])
                last = summarize([o for o in outs if o["round"] == 9])
                assert last["attempts_per_task"] <= first["attempts_per_task"]
                assert last["first_attempt_rate"] >= first["first_attempt_rate"]
            finally:
                h.close()


class TestE3MisleadingPriors:
    """12 tasks where prior qualified experience points at the
    wrong procedure. The system must not false-resolve; the cost
    of being misled is measured in attempts."""

    def _tasks(self):
        rng = random.Random(SEED + 3)
        tasks = []
        # 4 tasks: the transfer stage proposes the wrong qualified
        # procedure first (blocked-looking, correct P5).
        for i in range(4):
            t = make_task(rng, "blocked", f"e3-transfer-{i}")
            t["correct"] = "P5"
            tasks.append(t)
        # 4 tasks: the exact index serves a procedure whose
        # demonstrated conditions contradict the task (firmware).
        for i in range(4):
            t = make_task(rng, "valve", f"e3-exact-{i}")
            t["conditions"] = {"firmware": "v1"}
            t["correct"] = "P1"
            tasks.append(t)
        # 4 tasks: the trial ranks the wrong procedure first
        # (flood, no description overlap with P7).
        for i in range(4):
            tasks.append({"task_id": f"e3-trial-{i}",
                          "signature": "flood_1", "family": "flood",
                          "tags": ["flood", "water", "rising", "deep"],
                          "conditions": {},
                          "requested_operation": "repair",
                          "provenance": ("int:eval-001",),
                          "correct": "P7", "synonym": False})
        return tasks

    def test_misleading_priors_no_false_resolution(self):
        tasks = self._tasks()
        h = make_config_harness("FULL", tasks)
        try:
            outs = []
            for t in tasks:
                rep = h.pipe.run({k: v for k, v in t.items()
                                  if k != "correct"})
                outs.append({"task": t, "report": rep,
                             "failure": classify(t, rep, h.policy)})
            s = summarize(outs)
            assert s["resolved"] == 12 and s["false_resolved"] == 0
            assert s["failures"] == {}
            # The cost of being misled, by group.
            groups = {"transfer": outs[0:4], "exact": outs[4:8],
                      "trial": outs[8:12]}
            assert summarize(groups["transfer"])["attempts_per_task"] == 2
            assert summarize(groups["exact"])["attempts_per_task"] == 1
            # The flood tasks cost the full eligible trial list:
            # 8 attempts (P1/P2 are excluded — their hard firmware
            # precondition is unknown for a task with no firmware
            # condition).
            assert summarize(groups["trial"])["attempts_per_task"] == 8
            # The exact group never executed the contradicted P2.
            for o in groups["exact"]:
                assert all(a["procedure"] != "SOSO-P2"
                           for a in o["report"]["attempts"])
        finally:
            h.close()


class TestExactSignatureVsContradiction:
    """JD's hardening item #3: exact-signature retrieval must not
    outrank contradictory tags or conditions. Two groups:
    (A) contradicted CONDITIONS with a demonstrated condition
        recorded — the gate must refuse the procedure.
    (B) contradicted TAGS with no demonstrated condition recorded
        — the exact index serves the procedure (the gap), but the
        observation fails and the validator does not learn it, so
        the outcome is still safe. The gap is reported, not hidden."""

    def test_contradicted_conditions_refused(self):
        tasks = []
        rng = random.Random(SEED + 4)
        t = make_task(rng, "valve", "e3c-disk-v1")
        t["conditions"] = {"firmware": "v1"}
        t["correct"] = "P1"
        tasks.append(t)
        t = make_task(rng, "blocked", "e3c-cache-traverse")
        t["conditions"] = {"kind": "traverse"}
        t["correct"] = "P6"
        tasks.append(t)
        h = make_config_harness("FULL", tasks)
        try:
            for t in tasks:
                rep = h.pipe.run({k: v for k, v in t.items()
                                  if k != "correct"})
                assert rep["state"] == "RESOLVED"
                assert rep["selected"] == registered_id(t["correct"])
                wrong = ("SOSO-P2" if t["correct"] == "P1" else "SOSO-P7")
                # Lower exact refused the contradicted procedure.
                assert any(r["procedure"] == wrong
                           for r in rep["rejected"])
                if t["correct"] == "P1":
                    # Firmware is a HARD gate: P2 is never attempted.
                    assert all(a["procedure"] != wrong
                               for a in rep["attempts"])
                else:
                    # Kind is SOFT: the transfer stage may attempt
                    # P7 and fail safely, but Lower exact never
                    # serves it — the task did not resolve via
                    # lower-exact with the contradicted procedure.
                    assert rep["stage"] != "lower-exact" or \
                        rep["selected"] != wrong
        finally:
            h.close()

    def test_contradicted_tags_refused_not_served(self):
        """P3/P4 were qualified with EMPTY demonstrated conditions
        (the training sensor/pump tasks carry no conditions), so
        the exact index served them on signature alone even when
        the task's tags contradict. HARDEN-001 fix 1: the
        exact-index tag gate now refuses them (the task shares no
        tag with the demonstration), the tasks still resolve
        correctly via the right procedure, and 0 false resolutions
        occur."""
        tasks = [
            {"task_id": "e3c-svc-pump", "signature": "svc_hung",
             "family": "sensor",
             "tags": ["pump", "intermittent", "vibration", "bearing"],
             "conditions": {}, "requested_operation": "repair",
             "provenance": ("int:eval-001",), "correct": "P4",
             "synonym": False},
            {"task_id": "e3c-cfg-sensor", "signature": "cfg_bad",
             "family": "pump",
             "tags": ["sensor", "temperature", "drift", "readings"],
             "conditions": {}, "requested_operation": "repair",
             "provenance": ("int:eval-001",), "correct": "P3",
             "synonym": False},
        ]
        h = make_config_harness("FULL", tasks)
        try:
            for t in tasks:
                rep = h.pipe.run({k: v for k, v in t.items()
                                  if k != "correct"})
                assert rep["state"] == "RESOLVED"
                assert rep["selected"] == registered_id(t["correct"])
                # The exact index refused the wrong procedure: it
                # was never attempted (the tag gate), and it is in
                # rejected with the tag reason.
                wrong = ("SOSO-P3" if t["correct"] == "P4" else "SOSO-P4")
                assert all(a["procedure"] != wrong
                           for a in rep["attempts"])
                assert any(r["procedure"] == wrong and "tags" in r["reason"]
                           for r in rep["rejected"])
                # And no record was created for the wrong procedure
                # on this task at all.
                latest = h.pipe.experiences.latest_per_pair()
                assert (t["task_id"], wrong) not in latest
                assert check_projections(h) == []
        finally:
            h.close()


class TestE4ChangingConditions:
    """Firmware flips v1 -> v2 mid-sequence. The system must not
    reuse the stale v1 qualification, must withdraw it, and must
    re-qualify under the new conditions."""

    def test_firmware_flip(self):
        h = make_config_harness("FULL", [])
        try:
            rng = random.Random(SEED + 5)
            # Phase 1: learn P1 under firmware v1.
            t1 = make_task(rng, "valve", "e4-v1-learn")
            t1["tags"] = ["valve", "firmware_v1", "stuck", "pressure"]
            t1["conditions"] = {"firmware": "v1"}
            t1["correct"] = "P1"
            h.add_task(t1)
            rep = h.pipe.run({k: v for k, v in t1.items()
                              if k != "correct"})
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P1"
            q = qualify_learned(h, t1, "P1", rng)
            assert q is not None and q["stage"] == "soso-transfer"
            # Phase 2: the condition flips to v2. Six valve tasks.
            v2 = []
            for i in range(6):
                t = make_task(rng, "valve", f"e4-v2-{i}")
                v2.append(t)
            for t in v2:
                h.add_task(t)
                rep = h.pipe.run({k: v for k, v in t.items()
                                  if k != "correct"})
                assert rep["state"] == "RESOLVED"
                assert rep["selected"] == "SOSO-P2"
                # Stale reuse: P1 never executes on a v2 task.
                assert all(a["procedure"] != "SOSO-P1"
                           for a in rep["attempts"])
            # Phase 3: withdraw the stale v1 qualification.
            h.pipe.experiences.withdraw(
                task_id="e4-v1-learn", procedure_id="SOSO-P1",
                observed_outcome="FAILURE", validator_result="FAIL",
                conditions={"firmware": "v1"},
                source="eval-001", why="firmware changed to v2")
            assert check_projections(h) == []
            latest = h.pipe.experiences.latest_per_pair()
            assert latest[("e4-v1-learn", "SOSO-P1")]["qual"] == "WITHDRAWN"
            # Phase 4: a v1 task again — the withdrawn pair is
            # out of the exact index, but the transfer task's
            # UNDER_TEST record still proposes P1 (withdrawal is
            # per (task, procedure), not per procedure). Withdraw
            # that record too, then a fresh v1 task re-learns by
            # trial and re-qualifies.
            h.pipe.experiences.withdraw(
                task_id="e4-v1-learn-xfer", procedure_id="SOSO-P1",
                observed_outcome="FAILURE", validator_result="FAIL",
                conditions={"firmware": "v1"},
                source="eval-001", why="firmware changed to v2")
            assert check_projections(h) == []
            t4 = make_task(rng, "valve", "e4-v1-again")
            t4["tags"] = ["valve", "firmware_v1", "stuck", "pressure"]
            t4["conditions"] = {"firmware": "v1"}
            t4["correct"] = "P1"
            h.add_task(t4)
            rep = h.pipe.run({k: v for k, v in t4.items()
                              if k != "correct"})
            assert rep["state"] == "RESOLVED"
            assert rep["stage"] == "higher-trial"
            q = qualify_learned(h, t4, "P1", rng)
            assert q is not None and q["stage"] == "soso-transfer"
        finally:
            h.close()


class TestE5EvaluatorIndependence:
    """Inject the reference answer through four channels. The
    selection must be identical with and without the injection
    (channels a-c); the lie (d) must be caught by the validator."""

    def _base(self):
        rng = random.Random(SEED + 6)
        t = make_task(rng, "slope", "e5-slope")
        return t

    def _run_clean(self, h, t):
        return h.pipe.run({k: v for k, v in t.items()
                           if k != "correct"})

    def _sig(self, rep):
        # The stage is excluded: the first run learns, so the
        # second run resolves via lower-exact instead of transfer.
        # The selection (what was chosen and tried) is what a
        # leak would change.
        return (rep["selected"],
                tuple(a["procedure"] for a in rep["attempts"]))

    def test_trace_leak_does_not_change_selection(self):
        t = self._base()
        h = make_config_harness("FULL", [t])
        try:
            clean = self._run_clean(h, t)
            h.pipe.trace.append(kind="LEAK", task=t["task_id"],
                                data={"answer": t["correct"],
                                      "expected": "SOSO-P7"})
            leaked = self._run_clean(h, t)
            assert self._sig(leaked) == self._sig(clean)
        finally:
            h.close()

    def test_tag_leak_does_not_change_selection(self):
        t = self._base()
        h = make_config_harness("FULL", [t])
        try:
            clean = self._run_clean(h, t)
            t2 = dict(t, tags=list(t["tags"]) + ["P7"],
                      task_id="e5-slope-tag")
            h.add_task(t2)
            leaked = h.pipe.run({k: v for k, v in t2.items()
                                 if k not in ("correct", "synonym")})
            assert self._sig(leaked) == self._sig(clean)
        finally:
            h.close()

    def test_source_leak_does_not_change_selection(self):
        t = self._base()
        h = make_config_harness("FULL", [t])
        try:
            clean = self._run_clean(h, t)
            h.pipe.experiences.write(
                task_id="e5-dummy", signature="dummy",
                family="slope", procedure_id="SOSO-P7",
                observed_outcome="SUCCESS", validator_result="PASS",
                conditions={}, qualification="CANDIDATE",
                source="leak:answer-is-P7", tags=["slope", "ground"])
            leaked = self._run_clean(h, t)
            assert self._sig(leaked) == self._sig(clean)
        finally:
            h.close()

    def test_lying_observation_is_caught(self):
        # A blocked-looking task whose correct procedure is P5.
        # The transfer stage proposes P7 first (analogy Jaccard
        # 1.0 with a training success set) and P7's observation
        # LIES (claims success). The validator must catch it
        # (FAIL / REJECTED) and the task must still resolve
        # correctly via P5. (A legacy procedure such as P11 cannot
        # be the liar: the no-trial rule escalates without
        # trialling when the only pointed-at procedure is blocked
        # by trial safety.)
        t = {"task_id": "e5-lie", "signature": "cache_big",
             "family": "blocked",
             "tags": ["route", "blocked", "debris", "rubble"],
             "conditions": {"kind": "detour"},
             "requested_operation": "repair",
             "provenance": ("int:eval-001",), "correct": "P5",
             "synonym": False}
        h = make_config_harness("FULL", [t])
        try:
            h.pipe.observations[(t["task_id"], "P7")] = True
            rep = h.pipe.run({k: v for k, v in t.items()
                              if k != "correct"})
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P5"
            assert any(a["procedure"] == "SOSO-P7"
                       for a in rep["attempts"])
            latest = h.pipe.experiences.latest_per_pair()
            rec = latest.get((t["task_id"], "SOSO-P7"))
            # HARDEN-001 fix 2: the lie is recorded as a failed
            # validation (obs SUCCESS, val FAIL) with qualification
            # REJECTED — the same label the trial stage uses. The
            # observation, validator decision and evidence are
            # preserved in the record; only the qualification
            # changes. It is caught, not learned, and never enters
            # the trusted projections.
            assert rec is not None
            assert rec["obs"] == "SUCCESS" and rec["val"] == "FAIL"
            assert rec["qual"] == "REJECTED"
            assert check_projections(h) == []
        finally:
            h.close()


class TestE6QualificationDecay:
    """Qualify P7 for blocked (kind detour); change the kind to
    traverse; the same signature must not serve the stale
    qualification. Withdrawal is the remedy; re-qualification
    under the new conditions is the recovery."""

    def test_kind_change_refuses_stale_qualification(self):
        h = make_config_harness("FULL", [])
        try:
            rng = random.Random(SEED + 7)
            # P7 is already QUALIFIED for cache_big (kind detour)
            # from the baseline training.
            t = make_task(rng, "blocked", "e6-traverse")
            t["conditions"] = {"kind": "traverse"}
            t["correct"] = "P6"
            h.add_task(t)
            rep = h.pipe.run({k: v for k, v in t.items()
                              if k != "correct"})
            assert rep["state"] == "RESOLVED"
            assert rep["selected"] == "SOSO-P6"
            # Lower exact refused P7 (it is in rejected); the
            # transfer stage may have attempted it and failed
            # safely, because kind is a soft precondition — the
            # task did not resolve via lower-exact with P7.
            assert any(r["procedure"] == "SOSO-P7"
                       for r in rep["rejected"])
            assert rep["stage"] != "lower-exact" or \
                rep["selected"] != "SOSO-P7"
            # Withdraw the detour qualification; it leaves both
            # projections. (The training variants b/c keep P7
            # qualified for cache_big via other tasks — the
            # withdrawal is per (task, procedure), so the exact
            # index still serves P7 for cache_big from them.)
            h.pipe.experiences.withdraw(
                task_id="ct-train-blocked-a", procedure_id="SOSO-P7",
                observed_outcome="FAILURE", validator_result="FAIL",
                conditions={"kind": "detour"},
                source="eval-001", why="kind changed to traverse")
            assert check_projections(h) == []
            latest = h.pipe.experiences.latest_per_pair()
            assert latest[("ct-train-blocked-a", "SOSO-P7")]["qual"] \
                == "WITHDRAWN"
        finally:
            h.close()


class TestRestartRetention:
    """The E1 sequence, then a restart, then the same tasks again.
    The improvement must survive the restart."""

    def test_retention_after_restart(self):
        import tempfile
        tasks = e1_tasks()
        root = tempfile.mkdtemp(prefix="eval001-retention-")
        h = make_config_harness("FULL", tasks, root=root)
        try:
            outs = run_sequence(h, tasks, e1_rounds(),
                                random.Random(99), qualify=True)
            pre = summarize(outs)
        finally:
            h.close()
        # Reopen on the same root: no re-training (the Big49
        # records persist); the projections rebuild from disk.
        h2 = Harness(root=root, tasks=tasks)
        try:
            post = retention_rate(h2, tasks)
        finally:
            h2.close()
        assert post["resolution_rate"] >= pre["resolution_rate"]
        assert post["first_attempt_rate"] >= pre["first_attempt_rate"]
        assert post["false_resolved"] == 0
