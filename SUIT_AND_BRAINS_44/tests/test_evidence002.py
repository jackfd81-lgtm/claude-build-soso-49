"""PATHWAY-002 (evidence immutability, design section 1) — reproduction
tests for the FrozenEvidenceSource defect.

Verified defect (pre-fix): the constructor shallow-copies the outer
mapping only (dict(table) + re-strung keys). Values are shared by
reference, answer() returns the stored value object directly, and
self.table is a plain writable attribute. Current values are all
strings, so the defect is latent — these tests use mutable values
to make it observable, and require the fix per design section 1:

- deep-copy the table on construction (mapping AND values);
- store it behind a read-only mapping (MappingProxyType over the
  deep copy / private attribute + read-only property);
- answer() returns a deep copy of the stored value;
- available(task_id, condition) -> bool by key presence only.

These tests were written BEFORE the fix and must FAIL on the
unfixed code, then PASS after it. No existing test file is modified.
"""
import pytest

from contract.evidence import EvidenceSource, FrozenEvidenceSource


def test_caller_table_mutation_after_construction_does_not_change_answers():
    """(a) Mutating the caller's table after construction — both the
    outer mapping and a mutable (incl. nested) value — must not
    change answers."""
    nested = {"version": "v1", "tags": ["a"]}
    caller_table = {("t1", "firmware"): ["v1"],
                    ("t2", "firmware"): nested}
    src = FrozenEvidenceSource(caller_table)
    assert isinstance(src, EvidenceSource)

    # Outer-mapping mutation: replace / add entries in the caller's dict.
    caller_table[("t1", "firmware")] = ["TAMPERED"]
    caller_table[("t3", "firmware")] = ["v9"]
    del caller_table[("t2", "firmware")]

    # In-place mutation of the caller's (formerly shared) values,
    # including a nested object inside a value.
    caller_table[("t1", "firmware")].append("TAMPERED")
    nested["version"] = "TAMPERED"
    nested["tags"].append("TAMPERED")

    value1, prov1 = src.answer("t1", "firmware")
    assert value1 == ["v1"], value1
    assert prov1 == {"source": "frozen-evidence", "task_id": "t1",
                     "condition": "firmware"}
    value2, _ = src.answer("t2", "firmware")
    assert value2 == {"version": "v1", "tags": ["a"]}, value2
    # The deleted key is still answerable from the frozen copy.
    with pytest.raises(KeyError):
        src.answer("t3", "firmware")


def test_returned_value_mutation_does_not_change_subsequent_answers():
    """(b) Mutating a value returned by answer() must not change
    subsequent answers — answer() must return a deep copy."""
    src = FrozenEvidenceSource(
        {("t1", "firmware"): ["v1"],
         ("t2", "firmware"): {"version": "v1", "tags": ["a"]}})

    value1, _ = src.answer("t1", "firmware")
    assert value1 == ["v1"]
    value1.append("TAMPERED")
    value1b, _ = src.answer("t1", "firmware")
    assert value1b == ["v1"], value1b

    value2, _ = src.answer("t2", "firmware")
    value2["version"] = "TAMPERED"
    value2["tags"].append("TAMPERED")
    value2b, _ = src.answer("t2", "firmware")
    assert value2b == {"version": "v1", "tags": ["a"]}, value2b


def test_rewriting_source_mapping_through_public_attribute_raises():
    """(c) Rewriting the source's mapping through the public
    attribute must raise, and answers must be unchanged afterwards."""
    src = FrozenEvidenceSource({("t1", "firmware"): "v1"})

    # Item assignment through the public mapping must raise.
    with pytest.raises((TypeError, AttributeError)):
        src.table[("t1", "firmware")] = "v2"
    # Item deletion through the public mapping must raise.
    with pytest.raises((TypeError, AttributeError)):
        del src.table[("t1", "firmware")]
    # Rebinding the public attribute must raise.
    with pytest.raises(AttributeError):
        src.table = {("t1", "firmware"): "v2"}

    # Answers unchanged after every rejected rewrite attempt.
    assert src.answer("t1", "firmware") == (
        "v1", {"source": "frozen-evidence", "task_id": "t1",
               "condition": "firmware"})
    # Read access through the public attribute still works.
    assert src.table[("t1", "firmware")] == "v1"


def test_available_returns_bool_by_key_presence_only():
    """(d) available(task_id, condition) returns True/False by key
    presence only, without leaking values: the result is a bool,
    never the stored value — including for falsy stored values."""
    src = FrozenEvidenceSource(
        {("t1", "firmware"): "v1",
         ("t2", "kind"): [],
         ("t3", "kind"): "",
         ("t4", "kind"): 0})

    assert src.available("t1", "firmware") is True
    assert src.available("t1", "kind") is False
    assert src.available("other", "firmware") is False
    # Present keys with falsy values are still available.
    assert src.available("t2", "kind") is True
    assert src.available("t3", "kind") is True
    assert src.available("t4", "kind") is True
    # Results are bools, not the stored values.
    for result in (src.available("t1", "firmware"),
                   src.available("t2", "kind"),
                   src.available("t1", "kind")):
        assert type(result) is bool
