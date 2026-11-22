"""PATHWAY-002 — measurement selection (design SS2).

MeasurementSelector: pure, deterministic, no ground-truth access.

Inputs (only these):
- hypotheses: [(procedure_id, {condition: (required_value, is_hard)})]
  derived from registry preconditions, HARD and SOFT.
- task: an object with .task_id, .conditions, .unknown_conditions.
- sources: evidence-source objects. The selector calls only
  .source_id and .available(task_id, condition). It never asks a
  source for a value and never touches a source's table.
- costs: {(source_id, condition): number}, default 1.
- allowed_sources: set of admissible source_ids, or None (every
  source admissible).
- known: {condition: value} already known (from task.conditions
  plus this run's answered observations).
- method: optional pathway record dict; its "measurements"
  ([{"condition": ...}] entries) are CANDIDATES, not orders.

Evaluation per candidate measurement (all six, recorded):
1. which competing hypotheses it distinguishes (>=2 distinct
   required values among hypotheses requiring that condition);
2. expected observation classes (the distinct required values +
   "other");
3. expected implications for candidate ranking — HARD: admits /
   contradicts (admission); SOFT: ranks_up / ranks_down (ranking
   only; a soft mismatch is NEVER recorded as a contradiction);
4. whether the source is available and admissible (available()
   plus the source allow-list);
5. measurement cost (from costs);
6. whether the observation is already known (known).

Selection rule: a measurement is USEFUL iff it distinguishes
>=2 competing hypotheses AND the source is available and
admissible AND the observation is not already known. The
selection holds one entry per condition (cheapest admissible
available source wins), ranked by (distinguishing power desc,
cost asc, condition name asc) — deterministic. No hard-coded
ground-truth values anywhere in the selector.

The selector reads the registry (public decision inputs) and
the sources' availability only. It never reads observations,
and it never imports the pipeline, the admission checks, or
the evaluation key.
"""


def _next_text(condition, cls, impl, has_hard, has_soft):
    """The "next" sentence for one expected class: what the
    observation would mean for the ranking. Hard wording is
    admission (admits / contradicts); soft wording is ranking
    only (ranks up / down) — a soft mismatch is never worded
    as a contradiction."""
    parts = []
    if has_hard:
        if cls == "other":
            parts.append("justified abstention: escalate with "
                         "the observation recorded")
        else:
            parts.append(
                f"apply {condition}={cls}: "
                f"{', '.join(impl['admits']) or 'no procedure'} admitted; "
                f"{', '.join(impl['contradicts']) or 'no procedure'} "
                f"contradicted")
    if has_soft:
        if cls == "other":
            parts.append("no procedure ranks up: the observed value "
                         "matches no required value")
        else:
            parts.append(
                f"apply {condition}={cls}: "
                f"{', '.join(impl['ranks_up']) or 'no procedure'} ranks up; "
                f"{', '.join(impl['ranks_down']) or 'no procedure'} "
                f"ranks down")
    return "; ".join(parts)


class MeasurementSelector:
    """Pure measurement selection over registry hypotheses.

    Constructed with the sources and the cost table; select()
    takes the hypotheses for this task and returns the ranked
    selection plus the full per-measurement evaluation. The
    selector makes zero value-returning calls during selection:
    availability is the only source call.
    """

    def __init__(self, sources, costs=None, allowed_sources=None):
        self.sources = list(sources)
        self.costs = dict(costs or {})
        self.allowed_sources = (None if allowed_sources is None
                                else set(allowed_sources))

    def select(self, hypotheses, task, known=None, method=None):
        """Rank the measurements for this task's hypotheses.

        Returns {"task_id", "evaluations", "selected"} where each
        evaluation records all six dimensions and "selected" is
        the ranked useful subset, one entry per condition."""
        task_id = getattr(task, "task_id", None)
        known_values = dict(getattr(task, "conditions", None) or {})
        if known:
            known_values.update(known)
        method_conditions = set()
        if method:
            for entry in method.get("measurements") or []:
                if isinstance(entry, dict) and entry.get("condition"):
                    method_conditions.add(entry["condition"])
        # Group the requiring hypotheses per condition, in
        # hypothesis order (deterministic; procedure lists are
        # sorted below).
        by_condition = {}
        for procedure_id, conditions in hypotheses:
            for name, (value, is_hard) in (conditions or {}).items():
                by_condition.setdefault(name, []).append(
                    (procedure_id, value, bool(is_hard)))
        evaluations = []
        for condition in sorted(by_condition):
            reqs = by_condition[condition]
            procedures = sorted({p for p, _v, _h in reqs})
            values = sorted({v for _p, v, _h in reqs})
            hard_reqs = [(p, v) for p, v, h in reqs if h]
            soft_reqs = [(p, v) for p, v, h in reqs if not h]
            implications = self._implications(condition, values,
                                              hard_reqs, soft_reqs)
            for source in self.sources:
                source_id = source.source_id
                available = bool(source.available(task_id, condition))
                admissible = (self.allowed_sources is None
                              or source_id in self.allowed_sources)
                cost = self.costs.get((source_id, condition), 1)
                already_known = condition in known_values
                useful = (len(values) >= 2 and available and admissible
                          and not already_known)
                evaluations.append({
                    "condition": condition,
                    "source_id": source_id,
                    "distinguishes": procedures,
                    "distinguishing_power": len(values),
                    "expected_classes": values + ["other"],
                    "implications": implications,
                    "available": available,
                    "admissible": admissible,
                    "cost": cost,
                    "already_known": already_known,
                    "method_candidate": condition in method_conditions,
                    "useful": useful,
                })
        # One entry per condition: the cheapest admissible
        # available source wins (ties: lowest source_id, so the
        # choice is deterministic).
        best = {}
        for ev in evaluations:
            if not ev["useful"]:
                continue
            cur = best.get(ev["condition"])
            if cur is None or (ev["cost"], ev["source_id"]) < (
                    cur["cost"], cur["source_id"]):
                best[ev["condition"]] = ev
        ranked = sorted(
            best.values(),
            key=lambda ev: (-ev["distinguishing_power"], ev["cost"],
                            ev["condition"]))
        selected = [{"condition": ev["condition"],
                     "source_id": ev["source_id"],
                     "cost": ev["cost"],
                     "distinguishing_power": ev["distinguishing_power"]}
                    for ev in ranked]
        return {"task_id": task_id, "evaluations": evaluations,
                "selected": selected}

    @staticmethod
    def _implications(condition, values, hard_reqs, soft_reqs):
        """Expected implications per observation class.

        A class's implication carries the admission keys
        (admits / contradicts) exactly when a hard hypothesis
        requires the condition, and the ranking keys
        (ranks_up / ranks_down) exactly when a soft hypothesis
        does. A condition every hypothesis marks hard never
        carries ranking keys; a condition every hypothesis
        marks soft never carries admission keys."""
        implications = {}
        for cls in values + ["other"]:
            impl = {}
            if hard_reqs:
                if cls == "other":
                    admits = []
                    contradicts = sorted(p for p, _v in hard_reqs)
                else:
                    admits = sorted(p for p, v in hard_reqs if v == cls)
                    contradicts = sorted(
                        p for p, v in hard_reqs if v != cls)
                impl["admits"] = admits
                impl["contradicts"] = contradicts
            if soft_reqs:
                if cls == "other":
                    up = []
                    down = sorted(p for p, _v in soft_reqs)
                else:
                    up = sorted(p for p, v in soft_reqs if v == cls)
                    down = sorted(p for p, v in soft_reqs if v != cls)
                impl["ranks_up"] = up
                impl["ranks_down"] = down
            impl["next"] = _next_text(condition, cls, impl,
                                      bool(hard_reqs), bool(soft_reqs))
            implications[cls] = impl
        return implications
