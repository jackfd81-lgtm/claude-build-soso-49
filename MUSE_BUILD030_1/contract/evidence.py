"""PATHWAY-001 — the evidence-source interface + frozen source.

An evidence source answers ONE named discriminator for ONE task,
from that task's own evidence. It never returns the correct
procedure, never consults the validator's criteria (the EVAL-001
E5 independence rule — the criteria are the answer key, and an
evidence source that reads them is a leak), and never returns
another task's record. It answers only through answer(); the
pipeline cannot read anything else from it.

The synthetic implementation, FrozenEvidenceSource, holds a
per-task ground-truth table of the values of that task's unknown
conditions, built by the test harness alongside the frozen
observations and criteria and kept in a separate object. An
absent entry raises KeyError: unanswerable means the run
escalates, it does not guess.

E5 INDEPENDENCE: this module imports nothing from the pipeline,
the registry, or the admission/validator modules, and the source
holds no reference to criteria, a validator, or a correct
procedure. A source built without criteria cannot leak the
correct procedure — the answer for a task is the same regardless
of which procedure is correct for that task (see
tests/test_evidence001.py).
"""
from __future__ import annotations

import copy
from types import MappingProxyType


class EvidenceSource:
    """The evidence-source interface. `source_id` names the
    source in the expectation record; an answer from any other
    source is refused and the run escalates. answer() returns
    (value, provenance) or raises KeyError when the task /
    condition is absent (unanswerable). provenance is a dict
    {"source": source_id, "task_id": task_id,
     "condition": condition_name}."""

    source_id: str = ""

    def answer(self, task_id, condition_name):
        """Return (value, provenance) for this task's named
        condition, or raise KeyError when unanswerable."""
        raise NotImplementedError


class FrozenEvidenceSource(EvidenceSource):
    """A frozen per-task evidence table: {(task_id,
    condition_name): value}. answer() returns the table value
    with its provenance, or raises KeyError when the task /
    condition is absent. The table is the source's only state —
    it holds no criteria, no validator, and no procedure id."""

    def __init__(self, table, source_id="frozen-evidence"):
        # Deep-copy mapping AND values: the caller's table (and any
        # mutable value inside it) must never be shared with the
        # frozen source. Keys are re-strung as before.
        self._table = {(str(t), str(c)): copy.deepcopy(v)
                       for (t, c), v in dict(table).items()}
        self.source_id = source_id

    @property
    def table(self):
        """Read-only view of the frozen table. Item assignment /
        deletion raise TypeError (MappingProxyType); rebinding the
        attribute raises AttributeError (property without setter).
        Read access still works."""
        return MappingProxyType(self._table)

    def answer(self, task_id, condition_name):
        value = copy.deepcopy(self._table[(task_id, condition_name)])
        provenance = {"source": self.source_id,
                      "task_id": task_id,
                      "condition": condition_name}
        return value, provenance

    def available(self, task_id, condition):
        """True iff the (task_id, condition) key is present in the
        frozen table — key presence only, never the stored value
        (falsy values are still available). Same str() key
        normalisation the constructor applies."""
        return (str(task_id), str(condition)) in self._table
