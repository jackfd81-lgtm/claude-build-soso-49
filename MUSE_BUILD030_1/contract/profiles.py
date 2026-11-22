"""TRACE-001 — domain-specific cognitive profiles.

Experimental profiles for the cross-domain investigation. A
profile is an ADVISORY cognitive strategy — preferred functions,
investigation strategy, evidence requirements, escalation
conditions — not a professional credential or permission to
perform regulated work. Nothing here authorizes real mechanical,
legal or medical work; every task remains synthetic and AUTH:0.

A profile changes the pipeline's behaviour in three measurable
ways (so the difference can be PROVEN from the trace, not
assumed from the name):
  trial_allowed     — whether the Higher trial stage runs. The
                      medical and legal profiles forbid trial:
                      an unproven procedure is escalated, not
                      tested. The mechanical and general profiles
                      allow it (trial is the investigation path).
  descriptions      — "default" (description transfer on) or None
                      (off). The medical profile turns it off: a
                      procedure must not transfer across domains
                      on vocabulary alone.
  evidence_required — "under_test" (an UNDER_TEST procedure may
                      be proposed from transfer) or "qualified"
                      (only QUALIFIED procedures may be served
                      from transfer). The legal and medical
                      profiles require qualified evidence.
  preferred_functions — the declared order of functions for the
                      domain. Recorded in the trace; the
                      experiment compares the declared order
                      against the functions that actually ran.
"""
from __future__ import annotations

PROFILES = {
    "doctor": {
        "id": "doctor",
        "name": "Doctor — symptoms, hypotheses, diagnostic testing",
        "domain": "medical",
        "strategy": "Form hypotheses, retrieve missing evidence, disambiguate, then compare; never trial an unproven procedure.",
        "trial_allowed": False,
        "descriptions": None,
        "evidence_required": "qualified",
        "preferred_functions": ["L01", "U10", "U08", "U09", "U37"],
    },
    "lawyer": {
        "id": "lawyer",
        "name": "Lawyer — claims, evidence, contradictions, reasoning",
        "domain": "legal",
        "strategy": "Retrieve claims and evidence, compare contradictions, and escalate when proof is insufficient.",
        "trial_allowed": False,
        "descriptions": "default",
        "evidence_required": "qualified",
        "preferred_functions": ["L01", "U08", "U09", "U10", "U37"],
    },
    "mechanic": {
        "id": "mechanic",
        "name": "Mechanic — fault isolation, components, causal testing",
        "domain": "mechanical",
        "strategy": "Use prior experience, isolate components, compare candidates, then run only bounded tests.",
        "trial_allowed": True,
        "descriptions": "default",
        "evidence_required": "under_test",
        "preferred_functions": ["L01", "L30", "U09", "U37", "L15", "L21", "U10"],
    },
    "cat": {
        "id": "cat",
        "name": "Cat — attention, novelty, motion, environmental changes",
        "domain": "environmental",
        "strategy": "Attend to novelty and motion first, retrieve relevant context, then compare environmental changes.",
        "trial_allowed": False,
        "descriptions": "default",
        "evidence_required": "under_test",
        "preferred_functions": ["L01", "U08", "L24", "U09", "U10"],
    },
    "crocodile": {
        "id": "crocodile",
        "name": "Crocodile — tracking, timing, distance, persistent observation",
        "domain": "temporal-tracking",
        "strategy": "Retrieve context, compare trajectories over time, disambiguate distance/timing, and persist observation.",
        "trial_allowed": False,
        "descriptions": "default",
        "evidence_required": "qualified",
        "preferred_functions": ["L01", "U08", "U09", "U10", "U37"],
    },
    "mechanical": {
        "id": "mechanical",
        "name": "Mechanical diagnosis and repair",
        "domain": "mechanical",
        "strategy": ("Try known fixes first; investigate unfamiliar "
                     "work by trial; escalate only when no safe "
                     "trial exists."),
        "trial_allowed": True,
        "descriptions": "default",
        "evidence_required": "under_test",
        "preferred_functions": ["L01", "L30", "L24", "U09", "U37",
                                "L15", "L21", "U08", "U10"],
    },
    "legal": {
        "id": "legal",
        "name": "Legal investigation and analysis",
        "domain": "legal",
        "strategy": ("Compare the evidence, request the missing "
                     "information, escalate rather than test an "
                     "unproven procedure."),
        "trial_allowed": False,
        "descriptions": "default",
        "evidence_required": "qualified",
        "preferred_functions": ["L01", "U08", "U09", "U37", "U10",
                                "L30", "L15", "L21"],
    },
    "medical": {
        "id": "medical",
        "name": "Medical diagnostic reasoning",
        "domain": "medical",
        "strategy": ("Gather evidence, disambiguate the missing "
                     "condition, escalate rather than trial an "
                     "unproven procedure; never transfer a "
                     "procedure on vocabulary alone."),
        "trial_allowed": False,
        "descriptions": None,
        "evidence_required": "qualified",
        "preferred_functions": ["L01", "U08", "U10", "U09", "U37",
                                "L30", "L15", "L21"],
    },
    "general": {
        "id": "general",
        "name": "General technical investigation",
        "domain": "general",
        "strategy": "The default Lower-first pipeline.",
        "trial_allowed": True,
        "descriptions": "default",
        "evidence_required": "under_test",
        "preferred_functions": ["L01", "L30", "U08", "U09", "U37",
                                "L15", "L21", "L24", "U10"],
    },
}


def get(profile_id: str) -> dict:
    if profile_id not in PROFILES:
        raise ValueError(f"unknown cognitive profile {profile_id!r}; "
                         f"known: {sorted(PROFILES)}")
    return dict(PROFILES[profile_id])
