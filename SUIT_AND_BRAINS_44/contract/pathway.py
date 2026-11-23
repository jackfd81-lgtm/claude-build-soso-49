"""PATHWAY-001 — pathway storage and reuse (the pathway-store half).

A stored pathway is the ordered INVESTIGATION for one task, not
its answer: the evidence requests (expectation + observation +
resulting candidate-state change, each with its trace seq), the
attempts and their outcomes, the outcome, and provenance (source
execution id, root, task signature + family, tags).

Storage is one durable Big49 pathway block, ``pathway.<task>.<seq>``,
written by a pathway recorder that reads the trace — never by the
experience store, and never mixed with experience qualification.
Writing a pathway does not qualify, under-test, or withdraw any
procedure. The qualification lifecycle is untouched. This module
imports no pipeline code and performs no execution, admission, or
validation of its own.

Two honest deviations from the PATHWAY-001 brief, recorded here
rather than papered over:

1. DUPLICATE WRITE. The brief says writing the same pathway_id
   twice "overwrites deterministically". Big49 blocks are
   write-once: ``put`` rejects a duplicate record_id with
   ``Rejected('ID_EXISTS')`` (a ``ValueError``), the blocks table
   has ``PRIMARY KEY(pool, record_id)``, and there is no delete or
   update API. Deleting a block by hand (raw SQL) would break the
   hash-chained audit the whole project relies on, so it is not
   done. ``PathwayStore.write`` therefore FAILS CLOSED on a
   duplicate id: the first write wins and the second raises. The
   recorder makes collisions near-impossible in practice — the id
   derives from the source execution's TASK_END trace seq, unique
   per ledger — and ``PathwayRecorder.record`` accepts an
   ``existing_ids`` set so a caller that re-records into a store
   that already holds the id gets ``pathway.<task>.<seq>.2``,
   ``.3``, ... instead of a collision.

2. SIZE CAP. The Big49 cognitive bucket allows 512 chars per
   block (the same cap ``ExperienceStore`` keeps a 480-char margin
   under). A pathway record in its full-key form is ~600 chars
   with a single evidence request — it would NOT fit. The store
   therefore writes a compact encoding of the same record
   (``encode_record``: abbreviated top-level keys, evidence
   requests / attempts / outcome as positional arrays, plus a
   ``"v": 1`` marker) and decodes it back on read
   (``decode_record``); the in-memory ``PathwayRecord`` always
   carries the exact keys the brief specifies, and the round-trip
   is asserted by the tests. A record whose compact form still
   exceeds 512 chars raises ``ValueError`` naming the size
   instead of truncating — a record that does not fit is an error,
   never a truncation.

EVIDENCE_* payload shapes (brief §"FACTS YOU MUST BUILD ON"):
the recorder reads exactly these shapes and nothing else —
  EVIDENCE_EXPECTATION data: {"expectation_id", "condition",
    "hypotheses", "predicted_classes", "implications", "source",
    "measurement"}.
  EVIDENCE_OBSERVED data: {"expectation_id", "condition",
    "value", "provenance"}.
The recorder stores only the condition, the expectation id, the
two trace seqs, the observed value, the provenance, and the
outcome taken from the report's information_requests entry. It
never reads the hypotheses, the predicted classes, the
implications, the validator, the criteria, or the observations:
the pathway records WHICH discriminator was asked about and what
was observed, not the reasoning that chose it and not the answer
key. The replayed requests are fresh requests — the pathway
supplies which discriminator to ask about, not the answer.

PATHWAY-002 — the transferable investigative METHOD (v2).
The recorder now ALSO reads the full EVIDENCE_EXPECTATION data
(hypotheses, predicted classes, implications, measurement,
source), the CORRECTION events, and the branching order, and
records five OPTIONAL fields (additive; v1 records remain
valid, and a v2 record carries all five — see
``validate_record``):
  conditions:   {"known": {cond: value}, "unknown": [cond, ...]}
    from the source task's conditions + unknown_conditions.
  hypotheses:   [{"condition", "hypotheses": [{"procedure": native,
    "requires": value}]}] — one entry per evidence expectation,
    in expectation order; procedure ids in NATIVE short form.
  measurements: [{"condition", "source", "distinguishes": [native],
    "expected_classes": [...], "implications": {class: {"admits",
    "contradicts", "ranks_up", "ranks_down": [native]}}}]
    — implication values are code lists ONLY. The expectation's
    natural-language "next" sentences are NEVER stored.
  branching:    [{"step": "ask:<cond>" | "try:<native>", "decision",
    "reason"}] — the investigation sequence: evidence asks and
    attempts interleaved in trace-seq order (expectation seqs
    for asks, OBSERVATION seqs for attempts). Ask decisions come
    from the report's information_requests outcomes; try
    decisions are the attempt validations.
  corrections:  [{"procedure": native, "previous", "new", "reason",
    "validation"}] — one entry per CORRECTION event, in seq
    order; "validation" is the validation recorded in the
    event's evidence (None when the event carries none).

Two deliberate omissions, recorded here rather than papered over:

1. MEASUREMENT PROSE. The expectation's "measurement" question
   is boilerplate prose generated from the condition and the
   pointed-at procedures; storing it (~120 chars) would break
   the 512-char bucket. The recorder reads it (and rejects a
   non-string value) but does not store it. The method stores
   what identifies the measurement and what it was expected to
   discriminate: condition, source, hypotheses, expected
   classes, implications.

2. INFO-REQUEST MATCHING. The v1 evidence_requests outcome uses
   the report's information_requests entry matched by a FLAT
   expectation_id string or a FLAT missing condition. The real
   pipeline stores expectation_id as a {condition: id} dict and
   missing as a [condition] list, so the v1 match misses and
   real-run evidence_requests outcomes are None. That v1
   behaviour is UNCHANGED (PATHWAY-001 numbers depend on it
   staying as recorded). Branching decisions use a separate,
   extended match (dict-form expectation_id first, then the
   missing fallback), so they carry the report's real outcomes
   ("resumed" / "justified_abstention" / "unanswered").

COMPACT v2 ENCODING. ``encode_record`` emits v1 (``"v": 1``)
exactly as before when a record carries none of the five new
keys, and v2 (``"v": 2``) when it carries all five; a record
carrying only some of the five raises ValueError (v2 is
all-or-none). The v2 block tightens the shared fields losslessly
— provenance stored as the bare source string when it is exactly
{"source", "task_id", "condition"} for this record's task and
condition; pathway/execution/expectation ids stored in their
derivable short forms (end seq, uuid suffix, None for the
standard "ev-<seq:04d>" id) with a full-string fallback whenever
the shape does not match — and stores the method once per
expectation ("met"), from which ``decode_record`` expands BOTH
the hypotheses and the measurements fields. Inside "met", the
expected classes and the implications are stored ONLY when they
deviate from what the hypotheses prove: the pipeline builds
predicted classes as sorted distinct required values + "other",
and admits/contradicts per class purely from the hypotheses
(see ``_derived_classes`` / ``_derived_implications``), so a
matching record stores None there and the decoder re-derives
them exactly; ranks_up/ranks_down (soft implications, always
empty in current pipeline output) are part of the deviation
check, so any non-empty rank is stored explicitly. Implication
code lists are stored as indices into that expectation's
hypotheses list. Branching is stored as the ask decisions plus
an "a"/"t" merge pattern; the decoder rebuilds the ask reasons
from the evidence observations and the try entries from the
attempts. A record whose compact form still exceeds 512 chars
raises ValueError naming the size instead of truncating.
"""
from __future__ import annotations

import copy
import json
import random
import re

from . import task as task_mod
from .registry import procedure_identity

PRINCIPAL = "L30"
BUCKET = "cognitive"
MAX_CONTENT_CHARS = 512  # Big49 cognitive bucket char cap (see module docstring).

PATHWAY_KEYS = ("pathway_id", "source_task_id", "source_execution_id",
                "signature", "family", "tags", "discriminators",
                "evidence_requests", "attempts", "outcome", "root")
PATHWAY_V2_KEYS = ("conditions", "hypotheses", "measurements",
                   "branching", "corrections")
EVIDENCE_REQUEST_KEYS = ("condition", "expectation_id", "expectation_seq",
                         "observation_seq", "observation", "provenance",
                         "outcome")
ATTEMPT_KEYS = ("procedure", "observed", "validation")
OUTCOME_KEYS = ("state", "stage", "selected")
CONDITION_KEYS = ("known", "unknown")
HYPOTHESIS_KEYS = ("condition", "hypotheses")
HYPOTHESIS_ENTRY_KEYS = ("procedure", "requires")
MEASUREMENT_KEYS = ("condition", "source", "distinguishes",
                    "expected_classes", "implications")
IMPLICATION_KEYS = ("admits", "contradicts", "ranks_up", "ranks_down")
BRANCHING_KEYS = ("step", "decision", "reason")
CORRECTION_KEYS = ("procedure", "previous", "new", "reason", "validation")
VALIDATIONS = ("PASS", "FAIL", "INCONCLUSIVE")

_PATHWAY_ID_RE = re.compile(r"^pathway\..+\.\d+(?:\.\d+)?$")


def _field(obj, name, default=None):
    """Read a field from a dict or an object (TaskRecord)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _task_fields(task):
    """(task_id, signature, family, tags) for a dict or TaskRecord."""
    tags = _field(task, "tags") or []
    return (_field(task, "task_id"), _field(task, "signature"),
            _field(task, "family"), list(tags))


def _task_conditions(task):
    """(known, unknown) applicability conditions for a dict or
    TaskRecord: known is the conditions dict, unknown the
    unknown_conditions list (a bare string counts as one)."""
    known = _field(task, "conditions")
    if known is None:
        known = {}
    if not isinstance(known, dict):
        raise ValueError(
            f"task {_field(task, 'task_id')!r} conditions must be a dict, "
            f"got {known!r}")
    unknown = _field(task, "unknown_conditions")
    if unknown is None:
        unknown = []
    elif isinstance(unknown, str):
        unknown = [unknown]
    else:
        unknown = list(unknown)
    return dict(known), unknown


def _native_id(procedure_id):
    """Native short form of a procedure id, or the id unchanged
    when it is unknown (encode/decode never crash on a foreign
    id; validation is the gate that rejects it)."""
    try:
        return procedure_identity(procedure_id)["native"]
    except KeyError:
        return procedure_id


def _registered_id(procedure_id):
    """Registered form of a procedure id, or the id unchanged
    when it is unknown (see _native_id)."""
    try:
        return procedure_identity(procedure_id)["registered"]
    except KeyError:
        return procedure_id


def _derived_classes(hypotheses):
    """The predicted classes the pipeline builds from hypotheses
    alone: sorted distinct required values + "other" (see
    Pipeline._answer_condition)."""
    return sorted({h["requires"] for h in hypotheses}, key=str) + ["other"]


def _derived_implications(hypotheses, classes):
    """The admits/contradicts implications the pipeline builds
    from hypotheses alone: for a named class, the procedures
    requiring it are admitted and the rest contradicted; "other"
    admits none and contradicts all. ranks_up/ranks_down are
    always empty here — soft ranks are never derived, only
    stored when the recorded expectation carries them."""
    procs = [h["procedure"] for h in hypotheses]
    out = {}
    for cls in classes:
        if cls == "other":
            out[cls] = {"admits": [], "contradicts": list(procs),
                        "ranks_up": [], "ranks_down": []}
        else:
            out[cls] = {
                "admits": [p for p, v in
                           ((h["procedure"], h["requires"])
                            for h in hypotheses) if v == cls],
                "contradicts": [p for p, v in
                                ((h["procedure"], h["requires"])
                                 for h in hypotheses) if v != cls],
                "ranks_up": [], "ranks_down": []}
    return out


def _expectation_id_for_seq(seq):
    """The trace event id for an expectation at this seq."""
    return f"ev-{seq:04d}"


# ------------------------------------------------------------------
# Compact block encoding
# ------------------------------------------------------------------

def _encode_pathway_id(pathway_id, task_id):
    """Short form: [end_seq] or [end_seq, counter] when the id is
    "pathway.<task>.<seq>[.<n>]"; the full string otherwise."""
    prefix = f"pathway.{task_id}."
    if isinstance(pathway_id, str) and pathway_id.startswith(prefix):
        rest = pathway_id[len(prefix):]
        parts = rest.split(".")
        if len(parts) in (1, 2) and all(p.isdigit() for p in parts):
            return [int(p) for p in parts]
    return pathway_id


def _decode_pathway_id(value, task_id):
    if isinstance(value, list):
        if len(value) == 1 and isinstance(value[0], int):
            return f"pathway.{task_id}.{value[0]}"
        if len(value) == 2 and all(isinstance(v, int) for v in value):
            return f"pathway.{task_id}.{value[0]}.{value[1]}"
        raise ValueError(f"malformed pathway block: bad pathway id {value!r}")
    if isinstance(value, str):
        return value
    raise ValueError(f"malformed pathway block: bad pathway id {value!r}")


def _encode_execution_id(execution_id, task_id):
    """Short form: the 8-hex suffix when the id is
    "run-<task>.<hex8>"; the full string otherwise."""
    prefix = f"run-{task_id}-"
    if (isinstance(execution_id, str)
            and execution_id.startswith(prefix)
            and len(execution_id) == len(prefix) + 8
            and all(c in "0123456789abcdef" for c in execution_id[len(prefix):])):
        return execution_id[len(prefix):]
    return execution_id


def _decode_execution_id(value, task_id):
    if (isinstance(value, str) and len(value) == 8
            and all(c in "0123456789abcdef" for c in value)):
        return f"run-{task_id}-{value}"
    if isinstance(value, str):
        return value
    raise ValueError(f"malformed pathway block: bad execution id {value!r}")


def _encode_provenance(provenance, task_id, condition):
    """Short form: the bare source string when the provenance is
    exactly {"source", "task_id", "condition"} for this record's
    task and condition (the FrozenEvidenceSource shape); the
    dict (or None) otherwise."""
    if (isinstance(provenance, dict)
            and set(provenance) == {"source", "task_id", "condition"}
            and provenance.get("task_id") == task_id
            and provenance.get("condition") == condition):
        return provenance.get("source")
    return provenance


def _decode_provenance(value, task_id, condition):
    if isinstance(value, str):
        return {"source": value, "task_id": task_id,
                "condition": condition}
    return value


def _encode_implications(implications, classes, hypotheses):
    """The compact implication form, or None when the recorded
    implications are exactly what the hypotheses prove (see
    _derived_implications). A deviation is stored explicitly as
    {class: [[admits-idx], [contradicts-idx], [up-idx],
    [down-idx]]} with indices into the hypotheses list. Raises
    ValueError when the implications name a class outside the
    expected classes or a procedure outside the hypotheses."""
    if implications == _derived_implications(hypotheses, classes):
        return None
    if set(implications) != set(classes):
        raise ValueError(
            "cannot encode pathway record: implication classes "
            f"{sorted(implications)} != expected classes {list(classes)}")
    procs = [h["procedure"] for h in hypotheses]
    out = {}
    for cls in classes:
        entry = implications[cls]
        coded = []
        for key in IMPLICATION_KEYS:
            idxs = []
            for proc in entry.get(key) or []:
                if proc not in procs:
                    raise ValueError(
                        "cannot encode pathway record: implication for "
                        f"class {cls!r} names {proc!r}, which is not one "
                        f"of the hypotheses {procs!r}")
                idxs.append(procs.index(proc))
            coded.append(idxs)
        out[cls] = coded
    return out


def _decode_implications(coded, classes, hypotheses):
    """Expand _encode_implications (or re-derive when None)."""
    if coded is None:
        return _derived_implications(hypotheses, classes)
    procs = [h["procedure"] for h in hypotheses]
    out = {}
    for cls in classes:
        lists = coded[cls]
        out[cls] = {key: [procs[i] for i in lists[j]]
                    for j, key in enumerate(IMPLICATION_KEYS)}
    return out


def _encode_branching(record):
    """The compact branching form: [ask decisions in evidence
    order, "a"/"t" merge pattern]. Verifies the in-memory
    branching is exactly the interleave of the evidence asks
    (in order) and the attempts (in order); raises ValueError
    otherwise, rather than silently reordering."""
    ev_conditions = []
    for req in record["evidence_requests"]:
        if not isinstance(req, dict) or not isinstance(
                req.get("condition"), str):
            raise ValueError(
                "cannot encode pathway record: evidence_requests entry "
                f"has no condition: {req!r}")
        ev_conditions.append(req["condition"])
    att_procs = []
    for att in record["attempts"]:
        if not isinstance(att, dict) or not isinstance(
                att.get("procedure"), str):
            raise ValueError(
                "cannot encode pathway record: attempts entry has no "
                f"procedure: {att!r}")
        att_procs.append(_native_id(att["procedure"]))
    decisions = []
    pattern = []
    ev_i = att_i = 0
    for entry in record["branching"]:
        if not isinstance(entry, dict):
            raise ValueError(
                "cannot encode pathway record: branching entry is not "
                f"a dict: {entry!r}")
        step = entry.get("step")
        if isinstance(step, str) and step.startswith("ask:"):
            if ev_i >= len(ev_conditions) or step[4:] != ev_conditions[ev_i]:
                raise ValueError(
                    "cannot encode pathway record: branching ask step "
                    f"{step!r} does not match the next evidence "
                    f"condition "
                    f"{ev_conditions[ev_i] if ev_i < len(ev_conditions) else None!r}")
            decisions.append(entry.get("decision"))
            pattern.append("a")
            ev_i += 1
        elif isinstance(step, str) and step.startswith("try:"):
            if att_i >= len(att_procs) or step[4:] != att_procs[att_i]:
                raise ValueError(
                    "cannot encode pathway record: branching try step "
                    f"{step!r} does not match the next attempt "
                    f"{att_procs[att_i] if att_i < len(att_procs) else None!r}")
            pattern.append("t")
            att_i += 1
        else:
            raise ValueError(
                "cannot encode pathway record: branching step must be "
                f"'ask:<condition>' or 'try:<procedure>', got {step!r}")
    if ev_i != len(ev_conditions) or att_i != len(att_procs):
        raise ValueError(
            "cannot encode pathway record: branching does not cover "
            f"every evidence request and attempt "
            f"({ev_i}/{len(ev_conditions)} asks, "
            f"{att_i}/{len(att_procs)} tries)")
    return [decisions, "".join(pattern)]


def _decode_branching(coded, evidence_requests, attempts):
    """Expand _encode_branching: ask reasons come from the
    evidence observations, try entries from the attempts."""
    decisions, pattern = coded
    branching = []
    ev_i = att_i = dec_i = 0
    for kind in pattern:
        if kind == "a":
            req = evidence_requests[ev_i]
            obs = req["observation"]
            branching.append({
                "step": f"ask:{req['condition']}",
                "decision": decisions[dec_i],
                "reason": (f"observation {obs}" if obs is not None
                           else "no observation")})
            ev_i += 1
            dec_i += 1
        elif kind == "t":
            att = attempts[att_i]
            branching.append({
                "step": f"try:{_native_id(att['procedure'])}",
                "decision": att["validation"],
                "reason": ("observed" if att["observed"]
                           else "not observed")})
            att_i += 1
        else:
            raise ValueError(
                f"malformed pathway block: bad branching kind {kind!r}")
    if ev_i != len(evidence_requests) or att_i != len(attempts):
        raise ValueError(
            "malformed pathway block: branching pattern does not cover "
            "every evidence request and attempt")
    if dec_i != len(decisions):
        raise ValueError(
            "malformed pathway block: branching decisions do not match "
            "the ask entries")
    return branching


def encode_record(record) -> dict:
    """The block form of a pathway record: the same record with
    abbreviated keys and positional sub-arrays, so a record with
    evidence requests fits the 512-char cognitive bucket. The
    in-memory PathwayRecord always carries the full keys;
    ``decode_record`` restores them exactly.

    v1 (``"v": 1``) when the record carries none of the five
    PATHWAY-002 method keys — byte-identical to the v1 encoding.
    v2 (``"v": 2``) when it carries all five; a record carrying
    only some raises ValueError (v2 is all-or-none). The v2
    block stores the method once per expectation ("met") and
    expands it into both the hypotheses and the measurements
    fields on decode; see the module docstring."""
    present = [k for k in PATHWAY_V2_KEYS if k in record]
    if not present:
        return {"v": 1,
                "pid": record["pathway_id"],
                "tid": record["source_task_id"],
                "eid": record["source_execution_id"],
                "sig": record["signature"],
                "fam": record["family"],
                "tags": list(record["tags"]),
                "disc": list(record["discriminators"]),
                "ev": [[r["condition"], r["expectation_id"],
                        r["expectation_seq"], r["observation_seq"],
                        r["observation"], r["provenance"], r["outcome"]]
                       for r in record["evidence_requests"]],
                "att": [[a["procedure"], a["observed"], a["validation"]]
                        for a in record["attempts"]],
                "out": [record["outcome"]["state"],
                        record["outcome"]["stage"],
                        record["outcome"]["selected"]],
                "root": record["root"]}
    if len(present) != len(PATHWAY_V2_KEYS):
        raise ValueError(
            "cannot encode pathway record: partial v2 record "
            f"(carries {present}, needs all of {list(PATHWAY_V2_KEYS)})")
    hypotheses = record["hypotheses"]
    measurements = record["measurements"]
    if len(hypotheses) != len(measurements) or len(hypotheses) != len(
            record["evidence_requests"]):
        raise ValueError(
            "cannot encode pathway record: hypotheses / measurements / "
            "evidence_requests must have one entry per expectation "
            f"({len(hypotheses)} / {len(measurements)} / "
            f"{len(record['evidence_requests'])})")
    task_id = record["source_task_id"]
    met = []
    for hyp_entry, mea_entry in zip(hypotheses, measurements):
        hyps = hyp_entry["hypotheses"]
        classes = mea_entry["expected_classes"]
        met.append([
            hyp_entry["condition"],
            mea_entry["source"],
            [[h["procedure"], h["requires"]] for h in hyps],
            None if list(classes) == _derived_classes(hyps)
            else list(classes),
            _encode_implications(mea_entry["implications"], classes,
                                 hyps)])
    selected = record["outcome"]["selected"]
    return {"v": 2,
            "pid": _encode_pathway_id(record["pathway_id"], task_id),
            "tid": task_id,
            "eid": _encode_execution_id(record["source_execution_id"],
                                        task_id),
            "sig": record["signature"],
            "fam": record["family"],
            "tags": list(record["tags"]),
            "disc": list(record["discriminators"]),
            "ev": [[r["condition"],
                    (None if r["expectation_id"]
                     == _expectation_id_for_seq(r["expectation_seq"])
                     else r["expectation_id"]),
                    r["expectation_seq"], r["observation_seq"],
                    r["observation"],
                    _encode_provenance(r["provenance"], task_id,
                                       r["condition"]),
                    r["outcome"]]
                   for r in record["evidence_requests"]],
            "att": [[_native_id(a["procedure"]), a["observed"],
                     a["validation"]]
                    for a in record["attempts"]],
            "out": [record["outcome"]["state"],
                    record["outcome"]["stage"],
                    (_native_id(selected) if selected is not None
                     else None)],
            "root": record["root"],
            "con": [dict(record["conditions"]["known"]),
                    list(record["conditions"]["unknown"])],
            "met": met,
            "bra": _encode_branching(record),
            "cor": [[_native_id(c["procedure"]), c["previous"], c["new"],
                     c["reason"], c["validation"]]
                    for c in record["corrections"]]}


def decode_record(content) -> dict:
    """Restore a PathwayRecord from its block form (``encode_record``).
    Accepts a dict or its canonical JSON string. Raises ValueError
    on an unknown version or a malformed block — a stored pathway
    that cannot be decoded is never silently misread. v1 blocks
    decode exactly as before; v2 blocks expand the shared "met"
    array into both the hypotheses and the measurements fields."""
    if isinstance(content, str):
        content = json.loads(content)
    if not isinstance(content, dict):
        raise ValueError(
            f"unknown pathway block version: {content!r}"[:200])
    version = content.get("v")
    if version == 1:
        try:
            return {"pathway_id": content["pid"],
                    "source_task_id": content["tid"],
                    "source_execution_id": content["eid"],
                    "signature": content["sig"],
                    "family": content["fam"],
                    "tags": list(content["tags"]),
                    "discriminators": list(content["disc"]),
                    "evidence_requests": [
                        {"condition": r[0], "expectation_id": r[1],
                         "expectation_seq": r[2], "observation_seq": r[3],
                         "observation": r[4], "provenance": r[5],
                         "outcome": r[6]}
                        for r in content["ev"]],
                    "attempts": [{"procedure": a[0], "observed": a[1],
                                  "validation": a[2]}
                                 for a in content["att"]],
                    "outcome": {"state": content["out"][0],
                                "stage": content["out"][1],
                                "selected": content["out"][2]},
                    "root": content["root"]}
        except (KeyError, IndexError, TypeError) as e:
            raise ValueError(f"malformed pathway block: {e}") from e
    if version != 2:
        raise ValueError(
            f"unknown pathway block version: {content!r}"[:200])
    try:
        task_id = content["tid"]
        evidence_requests = [
            {"condition": r[0],
             "expectation_id": (
                 _expectation_id_for_seq(r[2]) if r[1] is None else r[1]),
             "expectation_seq": r[2], "observation_seq": r[3],
             "observation": r[4],
             "provenance": _decode_provenance(r[5], task_id, r[0]),
             "outcome": r[6]}
            for r in content["ev"]]
        attempts = [{"procedure": _registered_id(a[0]), "observed": a[1],
                     "validation": a[2]}
                    for a in content["att"]]
        selected = content["out"][2]
        hypotheses = []
        measurements = []
        for entry in content["met"]:
            condition, source, hyps, classes, coded_impl = entry
            hyps = [{"procedure": _native_id(h[0]), "requires": h[1]}
                    for h in hyps]
            if classes is None:
                classes = _derived_classes(hyps)
            else:
                classes = list(classes)
            distinguishes = []
            for h in hyps:
                if h["procedure"] not in distinguishes:
                    distinguishes.append(h["procedure"])
            hypotheses.append({"condition": condition,
                               "hypotheses": hyps})
            measurements.append({
                "condition": condition, "source": source,
                "distinguishes": distinguishes,
                "expected_classes": classes,
                "implications": _decode_implications(coded_impl, classes,
                                                     hyps)})
        return {"pathway_id": _decode_pathway_id(content["pid"], task_id),
                "source_task_id": task_id,
                "source_execution_id": _decode_execution_id(
                    content["eid"], task_id),
                "signature": content["sig"],
                "family": content["fam"],
                "tags": list(content["tags"]),
                "discriminators": list(content["disc"]),
                "evidence_requests": evidence_requests,
                "attempts": attempts,
                "outcome": {"state": content["out"][0],
                            "stage": content["out"][1],
                            "selected": (_registered_id(selected)
                                         if selected is not None else None)},
                "root": content["root"],
                "conditions": {"known": dict(content["con"][0]),
                               "unknown": list(content["con"][1])},
                "hypotheses": hypotheses,
                "measurements": measurements,
                "branching": _decode_branching(content["bra"],
                                               evidence_requests, attempts),
                "corrections": [
                    {"procedure": _native_id(c[0]), "previous": c[1],
                     "new": c[2], "reason": c[3], "validation": c[4]}
                    for c in content["cor"]]}
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise ValueError(f"malformed pathway block: {e}") from e


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------

def validate_record(record) -> list:
    """Problems with a pathway record (empty = valid).

    Raises KeyError (from ``registry.procedure_identity``) on an
    unknown procedure id in an attempt, in the outcome's
    ``selected``, or in the v2 method fields — an unknown id is
    never silently accepted. Every other defect is reported as
    a problem string.

    v1 records carry exactly the 11 PATHWAY_KEYS. v2 records
    carry those plus all five PATHWAY_V2_KEYS (all-or-none; a
    partial v2 record is a problem). The v2 method fields carry
    procedure ids in native short form, one hypotheses /
    measurements entry per evidence expectation in order, and
    branching as exactly the interleave of the evidence asks and
    the attempts; v2 attempts and outcome.selected must be in
    registered form (the v2 block stores the native form). See
    ``_validate_method``.
    """
    problems = []
    if not isinstance(record, dict):
        return ["record must be a dict"]
    for key in PATHWAY_KEYS:
        if key not in record:
            problems.append(f"missing key {key!r}")
    for key in record:
        if key not in PATHWAY_KEYS and key not in PATHWAY_V2_KEYS:
            problems.append(f"unexpected key {key!r}")
    # v2 is all-or-none: the five method keys are optional as a
    # group (a v1 record carries none of them), but a record
    # carrying only some of them is neither v1 nor v2.
    present_v2 = [k for k in PATHWAY_V2_KEYS if k in record]
    if present_v2 and len(present_v2) != len(PATHWAY_V2_KEYS):
        problems.append(
            "partial v2 record: carries "
            f"{present_v2} but not all of {list(PATHWAY_V2_KEYS)}")
    if problems:
        return problems

    pid = record["pathway_id"]
    if not isinstance(pid, str) or not _PATHWAY_ID_RE.match(pid):
        problems.append(f"pathway_id {pid!r} is not well-formed "
                        f"(expected 'pathway.<task>.<seq>[.<n>]')")
    elif not pid.startswith(f"pathway.{record['source_task_id']}."):
        problems.append(
            f"pathway_id {pid!r} does not start with the source task id "
            f"{record['source_task_id']!r}")
    for name in ("source_task_id", "source_execution_id", "signature",
                 "family", "root"):
        if not isinstance(record[name], str) or not record[name]:
            problems.append(f"{name} must be a non-empty string")
    tags = record["tags"]
    if not isinstance(tags, list) or not all(
            isinstance(t, str) and t for t in tags):
        problems.append("tags must be a list of non-empty strings")

    discs = record["discriminators"]
    if not isinstance(discs, list) or not all(
            isinstance(d, str) and d for d in discs):
        problems.append("discriminators must be a list of non-empty strings")
        discs = []
    elif len(set(discs)) != len(discs):
        problems.append("discriminators must be deduplicated, in order")
    reqs = record["evidence_requests"]
    if not isinstance(reqs, list):
        problems.append("evidence_requests must be a list")
        reqs = []
    # A pathway with no evidence requests is valid but replays
    # nothing; discriminators are allowed to be empty only then.
    if not discs and reqs:
        problems.append("discriminators is empty but evidence_requests is not")
    if discs and not reqs:
        problems.append("discriminators is non-empty but evidence_requests "
                        "is empty")
    if reqs:
        seen_conditions = []
        for r in reqs:
            if isinstance(r, dict) and r.get("condition") not in seen_conditions:
                seen_conditions.append(r.get("condition"))
        if seen_conditions != discs:
            # Only flag when both sides are well-formed lists; the
            # per-request checks below report the malformed cases.
            if (isinstance(discs, list)
                    and all(isinstance(r, dict) and "condition" in r
                            for r in reqs)):
                problems.append(
                    "discriminators must be the evidence requests' "
                    "conditions, deduplicated preserving order")
    for i, req in enumerate(reqs):
        if not isinstance(req, dict):
            problems.append(f"evidence_requests[{i}] must be a dict")
            continue
        for key in EVIDENCE_REQUEST_KEYS:
            if key not in req:
                problems.append(
                    f"evidence_requests[{i}] missing key {key!r}")
        for key in req:
            if key not in EVIDENCE_REQUEST_KEYS:
                problems.append(
                    f"evidence_requests[{i}] unexpected key {key!r}")
        if not isinstance(req.get("condition"), str) or not req.get("condition"):
            problems.append(f"evidence_requests[{i}] condition must be a "
                            f"non-empty string")
        if not isinstance(req.get("expectation_id"), str) or not req.get(
                "expectation_id"):
            problems.append(f"evidence_requests[{i}] expectation_id must be "
                            f"a non-empty string")
        eseq, oseq = req.get("expectation_seq"), req.get("observation_seq")
        if not isinstance(eseq, int) or isinstance(eseq, bool):
            problems.append(f"evidence_requests[{i}] expectation_seq must "
                            f"be an integer")
        if oseq is not None and (not isinstance(oseq, int)
                                 or isinstance(oseq, bool)):
            problems.append(f"evidence_requests[{i}] observation_seq must "
                            f"be an integer or None")
        if (req.get("observation") is None) != (oseq is None):
            problems.append(
                f"evidence_requests[{i}] observation and observation_seq "
                f"must both be present or both be absent (an unanswered "
                f"request carries observation None and observation_seq None)")
        if (isinstance(eseq, int) and isinstance(oseq, int)
                and not eseq < oseq):
            problems.append(
                f"evidence_requests[{i}] expectation_seq ({eseq}) must be "
                f"< observation_seq ({oseq})")

    attempts = record["attempts"]
    if not isinstance(attempts, list):
        problems.append("attempts must be a list")
        attempts = []
    for i, att in enumerate(attempts):
        if not isinstance(att, dict):
            problems.append(f"attempts[{i}] must be a dict")
            continue
        for key in ATTEMPT_KEYS:
            if key not in att:
                problems.append(f"attempts[{i}] missing key {key!r}")
        for key in att:
            if key not in ATTEMPT_KEYS:
                problems.append(f"attempts[{i}] unexpected key {key!r}")
        # Raises KeyError on an unknown id — never silently accepted.
        if isinstance(att.get("procedure"), str):
            identity = procedure_identity(att["procedure"])
            if present_v2 and identity["registered"] != att["procedure"]:
                problems.append(
                    f"attempts[{i}] procedure must be in registered form "
                    f"in a v2 record (the v2 block stores the native "
                    f"form), got {att['procedure']!r}")
        else:
            problems.append(f"attempts[{i}] procedure must be a string")
        if not isinstance(att.get("observed"), bool):
            problems.append(f"attempts[{i}] observed must be a bool")
        if att.get("validation") not in ("PASS", "FAIL", "INCONCLUSIVE"):
            problems.append(
                f"attempts[{i}] validation must be PASS, FAIL or "
                f"INCONCLUSIVE, got {att.get('validation')!r}")

    outcome = record["outcome"]
    if not isinstance(outcome, dict):
        problems.append("outcome must be a dict")
    else:
        for key in OUTCOME_KEYS:
            if key not in outcome:
                problems.append(f"outcome missing key {key!r}")
        for key in outcome:
            if key not in OUTCOME_KEYS:
                problems.append(f"outcome unexpected key {key!r}")
        if not isinstance(outcome.get("state"), str) or not outcome.get("state"):
            problems.append("outcome state must be a non-empty string")
        if not isinstance(outcome.get("stage"), str) or not outcome.get("stage"):
            problems.append("outcome stage must be a non-empty string")
        selected = outcome.get("selected")
        if selected is not None:
            if not isinstance(selected, str):
                problems.append("outcome selected must be a string or None")
            else:
                # Raises KeyError on an unknown id.
                identity = procedure_identity(selected)
                if present_v2 and identity["registered"] != selected:
                    problems.append(
                        "outcome selected must be in registered form in "
                        "a v2 record (the v2 block stores the native "
                        f"form), got {selected!r}")
    if present_v2:
        problems.extend(_validate_method(record))
    return problems


def _is_native_known(procedure_id):
    """True when procedure_id is a known id in native short form.
    Raises KeyError on an unknown id — never silently accepted."""
    return procedure_identity(procedure_id)["native"] == procedure_id


def _validate_method(record) -> list:
    """Problems with the five PATHWAY-002 method fields (empty =
    valid). Only called when the record carries all five (v2);
    v1 records skip this entirely. Raises KeyError on an unknown
    procedure id in the new fields, as for attempts. The
    in-memory method fields carry procedure ids in NATIVE short
    form; hypotheses / measurements carry one entry per evidence
    expectation, in evidence-request order; branching is exactly
    the interleave of the evidence asks (in order) and the
    attempts (in order)."""
    problems = []
    reqs = record["evidence_requests"]
    attempts = record["attempts"]
    ev_conditions = None
    if isinstance(reqs, list) and all(
            isinstance(r, dict) and isinstance(r.get("condition"), str)
            for r in reqs):
        ev_conditions = [r["condition"] for r in reqs]
    att_natives = None
    if isinstance(attempts, list) and all(
            isinstance(a, dict) and isinstance(a.get("procedure"), str)
            for a in attempts):
        att_natives = [_native_id(a["procedure"]) for a in attempts]

    # -- conditions --
    cond = record["conditions"]
    if not isinstance(cond, dict):
        problems.append("conditions must be a dict")
    else:
        for key in CONDITION_KEYS:
            if key not in cond:
                problems.append(f"conditions missing key {key!r}")
        for key in cond:
            if key not in CONDITION_KEYS:
                problems.append(f"conditions unexpected key {key!r}")
        known = cond.get("known")
        if not isinstance(known, dict) or not all(
                isinstance(k, str) and k and isinstance(v, str)
                for k, v in known.items()):
            problems.append("conditions known must be a dict of "
                            "non-empty string conditions to string values")
        unknown = cond.get("unknown")
        if not isinstance(unknown, list) or not all(
                isinstance(u, str) and u for u in unknown):
            problems.append("conditions unknown must be a list of "
                            "non-empty strings")
        elif len(set(unknown)) != len(unknown):
            problems.append("conditions unknown must be deduplicated")

    # -- hypotheses --
    hyps = record["hypotheses"]
    if not isinstance(hyps, list):
        problems.append("hypotheses must be a list")
        hyps = []
    hyp_conditions = []
    for i, entry in enumerate(hyps):
        if not isinstance(entry, dict):
            problems.append(f"hypotheses[{i}] must be a dict")
            continue
        for key in HYPOTHESIS_KEYS:
            if key not in entry:
                problems.append(f"hypotheses[{i}] missing key {key!r}")
        for key in entry:
            if key not in HYPOTHESIS_KEYS:
                problems.append(
                    f"hypotheses[{i}] unexpected key {key!r}")
        if not isinstance(entry.get("condition"), str) \
                or not entry.get("condition"):
            problems.append(f"hypotheses[{i}] condition must be a "
                            f"non-empty string")
        else:
            hyp_conditions.append(entry["condition"])
        sub = entry.get("hypotheses")
        if not isinstance(sub, list):
            problems.append(f"hypotheses[{i}] hypotheses must be a list")
            sub = []
        for j, h in enumerate(sub):
            if not isinstance(h, dict):
                problems.append(
                    f"hypotheses[{i}].hypotheses[{j}] must be a dict")
                continue
            for key in HYPOTHESIS_ENTRY_KEYS:
                if key not in h:
                    problems.append(
                        f"hypotheses[{i}].hypotheses[{j}] missing key "
                        f"{key!r}")
            for key in h:
                if key not in HYPOTHESIS_ENTRY_KEYS:
                    problems.append(
                        f"hypotheses[{i}].hypotheses[{j}] unexpected key "
                        f"{key!r}")
            if isinstance(h.get("procedure"), str):
                # Raises KeyError on an unknown id; must be native.
                if not _is_native_known(h["procedure"]):
                    problems.append(
                        f"hypotheses[{i}].hypotheses[{j}] procedure must "
                        f"be in native short form, got {h['procedure']!r}")
            else:
                problems.append(
                    f"hypotheses[{i}].hypotheses[{j}] procedure must be "
                    f"a string")
            if not isinstance(h.get("requires"), str) \
                    or not h.get("requires"):
                problems.append(
                    f"hypotheses[{i}].hypotheses[{j}] requires must be "
                    f"a non-empty string")
    if ev_conditions is not None and hyp_conditions != ev_conditions:
        problems.append("hypotheses conditions must be the evidence "
                        "requests' conditions, in order")

    # -- measurements --
    meas = record["measurements"]
    if not isinstance(meas, list):
        problems.append("measurements must be a list")
        meas = []
    mea_conditions = []
    for i, entry in enumerate(meas):
        if not isinstance(entry, dict):
            problems.append(f"measurements[{i}] must be a dict")
            continue
        for key in MEASUREMENT_KEYS:
            if key not in entry:
                problems.append(f"measurements[{i}] missing key {key!r}")
        for key in entry:
            if key not in MEASUREMENT_KEYS:
                problems.append(
                    f"measurements[{i}] unexpected key {key!r}")
        if not isinstance(entry.get("condition"), str) \
                or not entry.get("condition"):
            problems.append(f"measurements[{i}] condition must be a "
                            f"non-empty string")
        else:
            mea_conditions.append(entry["condition"])
        source = entry.get("source")
        if source is not None and (not isinstance(source, str)
                                   or not source):
            problems.append(f"measurements[{i}] source must be a "
                            f"non-empty string or None")
        dist = entry.get("distinguishes")
        dist_ok = isinstance(dist, list)
        if not dist_ok:
            problems.append(
                f"measurements[{i}] distinguishes must be a list")
            dist = []
        for j, proc in enumerate(dist):
            if not isinstance(proc, str):
                problems.append(
                    f"measurements[{i}] distinguishes[{j}] must be a "
                    f"string")
            elif not _is_native_known(proc):
                problems.append(
                    f"measurements[{i}] distinguishes[{j}] procedure "
                    f"must be in native short form, got {proc!r}")
        if i < len(hyps) and isinstance(hyps[i], dict) \
                and isinstance(hyps[i].get("hypotheses"), list):
            expected_dist = []
            for h in hyps[i]["hypotheses"]:
                if isinstance(h, dict) \
                        and isinstance(h.get("procedure"), str) \
                        and h["procedure"] not in expected_dist:
                    expected_dist.append(h["procedure"])
            if dist_ok and list(dist) != expected_dist:
                problems.append(
                    f"measurements[{i}] distinguishes must be the "
                    f"hypotheses procedures, in order")
        classes = entry.get("expected_classes")
        if not isinstance(classes, list) or not all(
                isinstance(c, str) and c for c in classes):
            problems.append(
                f"measurements[{i}] expected_classes must be a list of "
                f"non-empty strings")
            classes = []
        impl = entry.get("implications")
        if not isinstance(impl, dict):
            problems.append(
                f"measurements[{i}] implications must be a dict")
            impl = {}
        elif set(impl) != set(classes):
            problems.append(
                f"measurements[{i}] implication classes must equal the "
                f"expected classes")
        for cls, imp in impl.items():
            if not isinstance(imp, dict):
                problems.append(
                    f"measurements[{i}] implications[{cls!r}] must be "
                    f"a dict")
                continue
            for key in IMPLICATION_KEYS:
                if key not in imp:
                    problems.append(
                        f"measurements[{i}] implications[{cls!r}] "
                        f"missing key {key!r}")
            for key in imp:
                if key not in IMPLICATION_KEYS:
                    problems.append(
                        f"measurements[{i}] implications[{cls!r}] "
                        f"unexpected key {key!r}")
            for key in IMPLICATION_KEYS:
                lst = imp.get(key)
                if not isinstance(lst, list):
                    problems.append(
                        f"measurements[{i}] implications[{cls!r}][{key!r}] "
                        f"must be a list")
                    continue
                for proc in lst:
                    if not isinstance(proc, str):
                        problems.append(
                            f"measurements[{i}] implications[{cls!r}]"
                            f"[{key!r}] must be a string")
                    elif not _is_native_known(proc):
                        problems.append(
                            f"measurements[{i}] implications[{cls!r}]"
                            f"[{key!r}] procedure must be in native "
                            f"short form, got {proc!r}")
                    elif proc not in dist:
                        problems.append(
                            f"measurements[{i}] implications[{cls!r}]"
                            f"[{key!r}] names {proc!r}, which is not one "
                            f"of the distinguishes")
    if ev_conditions is not None and mea_conditions != ev_conditions:
        problems.append("measurements conditions must be the evidence "
                        "requests' conditions, in order")

    # -- branching --
    branching = record["branching"]
    if not isinstance(branching, list):
        problems.append("branching must be a list")
        branching = []
    for i, entry in enumerate(branching):
        if not isinstance(entry, dict):
            problems.append(f"branching[{i}] must be a dict")
            continue
        for key in BRANCHING_KEYS:
            if key not in entry:
                problems.append(f"branching[{i}] missing key {key!r}")
        for key in entry:
            if key not in BRANCHING_KEYS:
                problems.append(f"branching[{i}] unexpected key {key!r}")
        if not isinstance(entry.get("step"), str) \
                or not entry.get("step"):
            problems.append(f"branching[{i}] step must be a non-empty "
                            f"string")
        for key in ("decision", "reason"):
            if not isinstance(entry.get(key), str) \
                    or not entry.get(key):
                problems.append(f"branching[{i}] {key} must be a "
                                f"non-empty string")
    if ev_conditions is not None and att_natives is not None:
        ev_i = att_i = 0
        for i, entry in enumerate(branching):
            if not isinstance(entry, dict):
                continue
            step = entry.get("step")
            if not isinstance(step, str):
                continue
            if step.startswith("ask:"):
                if ev_i >= len(ev_conditions) \
                        or step[4:] != ev_conditions[ev_i]:
                    problems.append(
                        f"branching[{i}] ask step {step!r} does not match "
                        f"the next evidence condition "
                        f"{ev_conditions[ev_i] if ev_i < len(ev_conditions) else None!r}")
                else:
                    ev_i += 1
            elif step.startswith("try:"):
                if att_i >= len(att_natives) \
                        or step[4:] != att_natives[att_i]:
                    problems.append(
                        f"branching[{i}] try step {step!r} does not match "
                        f"the next attempt "
                        f"{att_natives[att_i] if att_i < len(att_natives) else None!r}")
                else:
                    att_i += 1
            else:
                problems.append(
                    f"branching[{i}] step must start with 'ask:' or "
                    f"'try:', got {step!r}")
        if ev_i != len(ev_conditions) or att_i != len(att_natives):
            problems.append(
                "branching must cover every evidence request and attempt "
                "exactly once, in order")

    # -- corrections --
    corr = record["corrections"]
    if not isinstance(corr, list):
        problems.append("corrections must be a list")
    else:
        for i, entry in enumerate(corr):
            if not isinstance(entry, dict):
                problems.append(f"corrections[{i}] must be a dict")
                continue
            for key in CORRECTION_KEYS:
                if key not in entry:
                    problems.append(
                        f"corrections[{i}] missing key {key!r}")
            for key in entry:
                if key not in CORRECTION_KEYS:
                    problems.append(
                        f"corrections[{i}] unexpected key {key!r}")
            if isinstance(entry.get("procedure"), str):
                # Raises KeyError on an unknown id; must be native.
                if not _is_native_known(entry["procedure"]):
                    problems.append(
                        f"corrections[{i}] procedure must be in native "
                        f"short form, got {entry['procedure']!r}")
            else:
                problems.append(
                    f"corrections[{i}] procedure must be a string")
            for key in ("previous", "new"):
                if not isinstance(entry.get(key), str) \
                        or not entry.get(key):
                    problems.append(
                        f"corrections[{i}] {key} must be a non-empty "
                        f"string")
            if not isinstance(entry.get("reason"), str):
                problems.append(f"corrections[{i}] reason must be a "
                                f"string")
            if entry.get("validation") is not None \
                    and entry.get("validation") not in VALIDATIONS:
                problems.append(
                    f"corrections[{i}] validation must be PASS, FAIL, "
                    f"INCONCLUSIVE or None, got "
                    f"{entry.get('validation')!r}")
    return problems


# ------------------------------------------------------------------
# Recorder: trace + report -> PathwayRecord
# ------------------------------------------------------------------

class PathwayRecorder:
    """Build a PathwayRecord from one execution's trace events and
    its pipeline report. Reads the trace only; never the validator,
    the criteria, or the observations. Attempts come from the
    report (the committed record) and are cross-checked against
    the OBSERVATION/VALIDATION trace events — a mismatch raises
    ValueError rather than silently recording a divergent pathway.
    The outcome is likewise cross-checked against the TASK_END
    event. An expectation with no observation is recorded with
    observation None: an unanswered request is honest data, not
    an error.

    PATHWAY-002 (v2): the record also carries the transferable
    method — conditions from the source task, hypotheses /
    measurements from the FULL EVIDENCE_EXPECTATION data (the
    natural-language implication sentences are never stored),
    branching from the spans/attempts order and the report's
    information_requests outcomes, and corrections from the
    CORRECTION events. See the module docstring."""

    @staticmethod
    def _expectation_method(data, seq):
        """(hypotheses, predicted_classes, implications) for one
        EVIDENCE_EXPECTATION data dict, in native short form.
        Raises ValueError on malformed shapes and KeyError
        (from procedure_identity) on an unknown procedure id.
        The "measurement" question prose is read and type-checked
        but NOT stored (see the module docstring)."""
        raw_hyps = data.get("hypotheses")
        if raw_hyps is None:
            raw_hyps = []
        if not isinstance(raw_hyps, list):
            raise ValueError(
                f"EVIDENCE_EXPECTATION at seq {seq} has non-list "
                f"hypotheses: {raw_hyps!r}")
        hypotheses = []
        for h in raw_hyps:
            if not isinstance(h, dict) or "procedure" not in h \
                    or "requires" not in h:
                raise ValueError(
                    f"EVIDENCE_EXPECTATION at seq {seq} has a malformed "
                    f"hypothesis: {h!r}")
            hypotheses.append({
                "procedure": procedure_identity(
                    h["procedure"])["native"],
                "requires": h["requires"]})
        classes = data.get("predicted_classes")
        if classes is None:
            classes = []
        if not isinstance(classes, list) or not all(
                isinstance(c, str) for c in classes):
            raise ValueError(
                f"EVIDENCE_EXPECTATION at seq {seq} has malformed "
                f"predicted_classes: {classes!r}")
        measurement = data.get("measurement")
        if measurement is not None and not isinstance(measurement, str):
            raise ValueError(
                f"EVIDENCE_EXPECTATION at seq {seq} has a non-string "
                f"measurement: {measurement!r}")
        raw_impl = data.get("implications")
        if raw_impl is None:
            raw_impl = {}
        if not isinstance(raw_impl, dict):
            raise ValueError(
                f"EVIDENCE_EXPECTATION at seq {seq} has non-dict "
                f"implications: {raw_impl!r}")
        implications = {}
        for cls in classes:
            entry = raw_impl.get(cls) or {}
            if not isinstance(entry, dict):
                raise ValueError(
                    f"EVIDENCE_EXPECTATION at seq {seq} has a malformed "
                    f"implication for class {cls!r}: {entry!r}")
            coded = {}
            for key in IMPLICATION_KEYS:
                raw = entry.get(key)
                if raw is None:
                    raw = []
                if not isinstance(raw, list):
                    raise ValueError(
                        f"EVIDENCE_EXPECTATION at seq {seq} has a "
                        f"non-list implication {key!r} for class "
                        f"{cls!r}: {raw!r}")
                coded[key] = [procedure_identity(p)["native"]
                              for p in raw]
            implications[cls] = coded
        return hypotheses, list(classes), implications

    @staticmethod
    def _match_info_request(info_reqs, expectation_id, condition):
        """The report's information_requests entry for one
        expectation, or None. Expectation-id match first — the
        pipeline stores it as a {condition: id} dict as well as
        a flat string — then the missing-condition fallback
        (flat string or a list containing the condition). This
        extended match is used for branching decisions only; the
        v1 evidence_requests outcome keeps the flat match (see
        the module docstring)."""
        for req in info_reqs:
            if not isinstance(req, dict):
                continue
            eid = req.get("expectation_id")
            if eid == expectation_id:
                return req
            if isinstance(eid, dict) and expectation_id in eid.values():
                return req
        for req in info_reqs:
            if not isinstance(req, dict):
                continue
            missing = req.get("missing")
            if missing == condition:
                return req
            if isinstance(missing, list) and condition in missing:
                return req
        return None

    def record(self, task_dict, report, trace_events, root,
               existing_ids=()):
        """trace_events is the list of (seq, event) pairs from
        ``contract.trace.trace_events`` for this execution.
        ``existing_ids`` is an optional collection of pathway ids
        already in the destination store: if the derived id
        collides, a ``.2`` / ``.3`` / ... counter is appended
        (documented collision rule; the id derives from the
        TASK_END seq, unique per ledger, so a collision only
        arises from re-recording the same execution)."""
        task_id, signature, family, tags = _task_fields(task_dict)
        if not task_id:
            raise ValueError("task_dict has no task_id")
        if not signature or not family:
            raise ValueError(f"task {task_id!r} has no signature/family")
        known_conditions, unknown_conditions = _task_conditions(task_dict)
        events = list(trace_events)

        # TASK_END carries the outcome and the seq the id derives from.
        ends = [(seq, ev) for seq, ev in events
                if ev.get("event_type") == "TASK_END"]
        if not ends:
            raise ValueError(
                f"cannot record a pathway for {task_id!r}: no TASK_END "
                f"event in the trace (the execution has no outcome)")
        end_seq, end_ev = ends[-1]
        end_data = end_ev.get("data") or {}

        outcome = {"state": report.get("state"),
                   "stage": report.get("stage"),
                   "selected": report.get("selected")}
        # Cross-check the report against the TASK_END event.
        if end_data.get("outcome") is not None and end_data.get(
                "outcome") != outcome["state"]:
            raise ValueError(
                f"report/TASK_END outcome mismatch for {task_id!r}: "
                f"report state {outcome['state']!r} vs TASK_END "
                f"{end_data.get('outcome')!r}")
        if end_data.get("stage") is not None and end_data.get(
                "stage") != outcome["stage"]:
            raise ValueError(
                f"report/TASK_END stage mismatch for {task_id!r}: "
                f"report stage {outcome['stage']!r} vs TASK_END "
                f"{end_data.get('stage')!r}")
        if ("selected" in end_data
                and end_data.get("selected") != outcome["selected"]):
            raise ValueError(
                f"report/TASK_END selected mismatch for {task_id!r}: "
                f"report selected {outcome['selected']!r} vs TASK_END "
                f"{end_data.get('selected')!r}")

        execution_id = (report.get("execution_id")
                        or end_ev.get("execution_id")
                        or (events[0][1].get("execution_id") if events
                            else None))
        if not execution_id:
            raise ValueError(
                f"cannot record a pathway for {task_id!r}: no execution id "
                f"in the report or the trace")

        pathway_id = f"pathway.{task_id}.{end_seq}"
        taken = set(existing_ids or ())
        if pathway_id in taken:
            n = 2
            while f"{pathway_id}.{n}" in taken:
                n += 1
            pathway_id = f"{pathway_id}.{n}"

        # Evidence requests: expectations in seq order, each paired
        # with its observation by expectation_id. The full
        # expectation data is kept for the v2 method fields
        # (hypotheses / predicted classes / implications / source);
        # the measurement question prose is read, not stored.
        # CORRECTION events are collected in seq order.
        expectations = []
        observations = {}
        corrections = []
        for seq, ev in events:
            data = ev.get("data") or {}
            if ev.get("event_type") == "EVIDENCE_EXPECTATION":
                condition = data.get("condition")
                expectation_id = data.get("expectation_id")
                if not condition or not expectation_id:
                    raise ValueError(
                        f"EVIDENCE_EXPECTATION at seq {seq} is missing "
                        f"condition/expectation_id: {data!r}")
                hypotheses, classes, implications = (
                    self._expectation_method(data, seq))
                expectations.append({
                    "seq": seq, "expectation_id": expectation_id,
                    "condition": condition, "hypotheses": hypotheses,
                    "predicted_classes": classes,
                    "implications": implications,
                    "source": data.get("source")})
            elif ev.get("event_type") == "EVIDENCE_OBSERVED":
                eid = data.get("expectation_id")
                if eid is not None and eid not in observations:
                    observations[eid] = (seq, data)
            elif ev.get("event_type") == "CORRECTION":
                procedure = data.get("procedure")
                previous = data.get("previous_qualification")
                new = data.get("new_qualification")
                if not procedure or not previous or not new:
                    raise ValueError(
                        f"CORRECTION at seq {seq} is missing "
                        f"procedure/previous_qualification/"
                        f"new_qualification: {data!r}")
                evidence = data.get("evidence")
                if evidence is None:
                    evidence = {}
                elif not isinstance(evidence, dict):
                    raise ValueError(
                        f"CORRECTION at seq {seq} has non-dict evidence: "
                        f"{evidence!r}")
                corrections.append({
                    "procedure": procedure_identity(
                        procedure)["native"],
                    "previous": previous, "new": new,
                    "reason": data.get("reason") or "",
                    "validation": evidence.get("validation")})
        discriminators = []
        for exp in expectations:
            if exp["condition"] not in discriminators:
                discriminators.append(exp["condition"])

        # The report's information_requests entries carry the
        # outcome of each request once the parallel evidence work
        # extends them (expectation_id / outcome); until then they
        # carry only missing/question/procedures/source, so match
        # by the missing condition as a fallback.
        info_reqs = report.get("information_requests") or []
        evidence_requests = []
        for exp in expectations:
            exp_seq = exp["seq"]
            expectation_id = exp["expectation_id"]
            condition = exp["condition"]
            matched = None
            for req in info_reqs:
                if isinstance(req, dict) and req.get(
                        "expectation_id") == expectation_id:
                    matched = req
                    break
            if matched is None:
                for req in info_reqs:
                    if isinstance(req, dict) and req.get(
                            "missing") == condition:
                        matched = req
                        break
            obs_seq, obs_data = observations.get(expectation_id, (None, None))
            evidence_requests.append({
                "condition": condition,
                "expectation_id": expectation_id,
                "expectation_seq": exp_seq,
                "observation_seq": obs_seq,
                "observation": (obs_data.get("value") if obs_data else None),
                "provenance": (obs_data.get("provenance") if obs_data
                               else None),
                "outcome": (matched.get("outcome") if matched else None),
            })

        # Attempts: the report is the committed record; the trace
        # cross-checks it. Trace OBSERVATION/VALIDATION events are
        # paired in execution order (one of each per attempt, the
        # OBSERVATION first).
        report_attempts = []
        for att in report.get("attempts") or []:
            report_attempts.append({
                "procedure": procedure_identity(
                    att["procedure"])["registered"],
                "observed": att["observed"],
                "validation": att["validation"]})
        trace_obs = [(seq, d.get("procedure"), d.get("observed"))
                     for seq, ev in events
                     if ev.get("event_type") == "OBSERVATION"
                     for d in [ev.get("data") or {}]]
        trace_val = [(d.get("procedure"), d.get("validation"))
                     for _seq, ev in events
                     if ev.get("event_type") == "VALIDATION"
                     for d in [ev.get("data") or {}]]
        if len(trace_obs) != len(trace_val):
            raise ValueError(
                f"report/trace attempt mismatch for {task_id!r}: "
                f"{len(trace_obs)} OBSERVATION events but "
                f"{len(trace_val)} VALIDATION events")
        trace_attempts = []
        attempt_seqs = []
        for (oseq, oproc, observed), (vproc, validation) in zip(
                trace_obs, trace_val):
            if oproc != vproc:
                raise ValueError(
                    f"report/trace attempt mismatch for {task_id!r}: "
                    f"OBSERVATION names {oproc!r} but the paired "
                    f"VALIDATION names {vproc!r}")
            trace_attempts.append({
                "procedure": procedure_identity(oproc)["registered"],
                "observed": observed,
                "validation": validation})
            attempt_seqs.append(oseq)
        if trace_attempts != report_attempts:
            raise ValueError(
                f"report/trace attempt mismatch for {task_id!r}: "
                f"report attempts {report_attempts!r} != trace attempts "
                f"{trace_attempts!r}")

        # PATHWAY-002 method fields: conditions from the source
        # task; hypotheses / measurements from the full
        # expectation data; branching as the investigation
        # sequence (asks at their expectation seqs, attempts at
        # their OBSERVATION seqs); corrections from the
        # CORRECTION events.
        hypotheses_field = []
        measurements_field = []
        for exp in expectations:
            hyps = exp["hypotheses"]
            distinguishes = []
            for h in hyps:
                if h["procedure"] not in distinguishes:
                    distinguishes.append(h["procedure"])
            hypotheses_field.append({
                "condition": exp["condition"],
                "hypotheses": hyps})
            measurements_field.append({
                "condition": exp["condition"],
                "source": exp["source"],
                "distinguishes": distinguishes,
                "expected_classes": exp["predicted_classes"],
                "implications": exp["implications"]})
        ordered = []
        for exp, req in zip(expectations, evidence_requests):
            matched = self._match_info_request(
                info_reqs, exp["expectation_id"], exp["condition"])
            ask_decision = None
            if matched is not None:
                ask_decision = matched.get("outcome")
            if not isinstance(ask_decision, str) or not ask_decision:
                ask_decision = ("resumed" if req["observation"] is not None
                                else "unanswered")
            ordered.append((exp["seq"], "ask", {
                "step": f"ask:{exp['condition']}",
                "decision": ask_decision,
                "reason": (f"observation {req['observation']}"
                           if req["observation"] is not None
                           else "no observation")}))
        for att, oseq in zip(report_attempts, attempt_seqs):
            ordered.append((oseq, "try", {
                "step": f"try:{_native_id(att['procedure'])}",
                "decision": att["validation"],
                "reason": ("observed" if att["observed"]
                           else "not observed")}))
        ordered.sort(key=lambda p: p[0])
        branching = [entry for _seq, _kind, entry in ordered]

        return {"pathway_id": pathway_id,
                "source_task_id": task_id,
                "source_execution_id": execution_id,
                "signature": signature,
                "family": family,
                "tags": sorted(set(tags)),
                "discriminators": discriminators,
                "evidence_requests": evidence_requests,
                "attempts": report_attempts,
                "outcome": outcome,
                "root": str(root),
                "conditions": {"known": known_conditions,
                               "unknown": unknown_conditions},
                "hypotheses": hypotheses_field,
                "measurements": measurements_field,
                "branching": branching,
                "corrections": corrections}


# ------------------------------------------------------------------
# Store: one Big49 block per pathway record
# ------------------------------------------------------------------

class PathwayStore:
    """The durable pathway store, over a Big49 — the same
    construction pattern as ``ExperienceStore`` (``PathwayStore(big)``).
    Holds ONLY pathway records: no experience, no qualification
    writes, no pipeline imports. One ``put`` per write; reads are
    read-only SELECTs on the store's own table, the sanctioned
    pattern ``ExperienceStore.read_all`` uses.

    Duplicate write: Big49 blocks are write-once, so writing the
    same pathway_id twice RAISES (the store's ``Rejected``,
    ``ID_EXISTS``) instead of overwriting — the first write wins.
    See the module docstring; the recorder's counter rule is the
    intended way to avoid the collision.
    """

    def __init__(self, big):
        self.big = big

    def write(self, record) -> str:
        """Write one pathway block. Returns the pathway_id.
        Raises ValueError on an invalid record (see
        ``validate_record``), on a record too large for the
        cognitive bucket, or on a duplicate pathway_id (Big49 is
        write-once; the first write wins)."""
        problems = validate_record(record)
        if problems:
            raise ValueError(
                f"invalid pathway record {record.get('pathway_id')!r}: "
                + "; ".join(problems))
        from ph6_brains_2208 import Block  # local import: RealCog source
        content = task_mod.canon(encode_record(record))
        if len(content) > MAX_CONTENT_CHARS:
            raise ValueError(
                f"pathway record too large ({len(content)} chars > "
                f"{MAX_CONTENT_CHARS} even in compact form); shorten "
                f"tags/root or split the pathway")
        block = Block(record["pathway_id"], PRINCIPAL, BUCKET, content,
                      graph="G_S", relation="ASSOCIATED_WITH",
                      source_refs=(f"int:{record['source_task_id']}",))
        self.big.put(block, principal=PRINCIPAL)
        return record["pathway_id"]

    def _read_all_rows(self) -> list:
        rows = self.big.db.execute(
            "SELECT payload FROM blocks WHERE pool='big' "
            "AND record_id LIKE 'pathway.%' ORDER BY rowid").fetchall()
        out = []
        for (raw,) in rows:
            payload = json.loads(raw)
            out.append(decode_record(payload["content"]))
        return out

    def all(self) -> list:
        """Every pathway record, in pathway_id order."""
        return sorted(self._read_all_rows(),
                      key=lambda r: r["pathway_id"])

    def count(self) -> int:
        return len(self._read_all_rows())

    def find(self, signature, family, tags):
        """The best matching pathway for a new task, or None.

        Exact (signature, family) match AND at least one shared
        tag with the stored record's tags (the HARDEN-001 tag gate,
        applied to pathways). When several match, the one with the
        most shared tags wins; ties are broken by earliest
        pathway_id. Returns a copy of the stored record."""
        wanted = set(tags or [])
        best = None
        best_key = None
        for rec in self._read_all_rows():
            if rec.get("signature") != signature:
                continue
            if rec.get("family") != family:
                continue
            shared = len(wanted & set(rec.get("tags") or []))
            if shared < 1:
                continue
            key = (-shared, rec["pathway_id"])
            if best_key is None or key < best_key:
                best_key = key
                best = rec
        return dict(best) if best is not None else None

    def get(self, pathway_id):
        """One pathway by id, or None. (Used by the shuffled
        provider; the experiment audits associations by id.)"""
        for rec in self._read_all_rows():
            if rec["pathway_id"] == pathway_id:
                return dict(rec)
        return None


# ------------------------------------------------------------------
# Providers: the duck-typed interface the pipeline calls
# ------------------------------------------------------------------

def _discriminators_of(record):
    return list(record.get("discriminators") or []) if record else []


class PathwayReplayProvider:
    """Replay provider: on a new task, return the matched stored
    pathway's discriminators (in stored order) when
    ``find()`` matches, else []. The pathway supplies WHICH
    discriminator to ask about, not the answer — the replayed
    requests are fresh requests for this task."""

    def __init__(self, store):
        self.store = store

    def discriminators_for(self, task) -> list:
        _tid, signature, family, tags = _task_fields(task)
        return _discriminators_of(
            self.store.find(signature, family, tags))

    def method_for(self, task):
        """The FULL matched pathway record (the transferable
        method: conditions, hypotheses, measurements, branching,
        corrections) for the selector, or None when ``find()``
        matches nothing. A deep copy: the selector may annotate
        the method without touching the store. The method never
        supplies values — replayed requests are fresh requests
        for this task."""
        _tid, signature, family, tags = _task_fields(task)
        rec = self.store.find(signature, family, tags)
        return copy.deepcopy(rec) if rec is not None else None


class ShuffledPathwayProvider:
    """Shuffled-association control (arm D): at construction, build
    a mapping from each task_id in ``tasks`` to the discriminators
    of a pathway recorded for a DIFFERENT task with the same
    (signature, family). "Compatible" means same (signature,
    family) — the tag gate is NOT applied here, so the shuffle
    tests the association itself. Candidates per group are
    shuffled with ``random.Random(seed)``; each task takes the
    next pathway in the shuffled order that is not its own. If a
    group has only one pathway, that task gets no replay (the
    mapping records None — it never silently falls back to the
    task's own pathway). ``association`` ({task_id: pathway_id |
    None}) is exposed so the experiment can audit it."""

    def __init__(self, store, tasks, seed=20261118):
        self.store = store
        self.seed = seed
        self.association = {}
        self._by_id = {r["pathway_id"]: r for r in store.all()}
        groups = {}
        for rec in store.all():
            groups.setdefault((rec["signature"], rec["family"]),
                              []).append(rec)
        rng = random.Random(seed)
        task_list = sorted(tasks, key=lambda t: _task_fields(t)[0])
        by_group = {}
        for task in task_list:
            tid, sig, fam, _tags = _task_fields(task)
            by_group.setdefault((sig, fam), []).append(tid)
        for group_key in sorted(groups):
            candidates = list(groups[group_key])
            rng.shuffle(candidates)
            remaining = list(candidates)
            for tid in by_group.get(group_key, []):
                chosen = None
                for rec in list(remaining):
                    if rec["source_task_id"] != tid:
                        chosen = rec
                        remaining.remove(rec)
                        break
                self.association[tid] = (
                    chosen["pathway_id"] if chosen else None)
        for group_key, tids in by_group.items():
            if group_key not in groups:
                for tid in tids:
                    self.association[tid] = None
        for task in task_list:
            tid = _task_fields(task)[0]
            self.association.setdefault(tid, None)

    def discriminators_for(self, task) -> list:
        tid = _task_fields(task)[0]
        pathway_id = self.association.get(tid)
        if pathway_id is None:
            return []
        return _discriminators_of(self._by_id.get(pathway_id))

    def method_for(self, task):
        """The FULL pathway record associated with this task by
        the shuffle (or None when the task has no associated
        pathway). A deep copy, as in PathwayReplayProvider — the
        selector evaluates the method's measurements against the
        NEW task's hypotheses."""
        tid = _task_fields(task)[0]
        pathway_id = self.association.get(tid)
        if pathway_id is None:
            return None
        rec = self._by_id.get(pathway_id)
        return copy.deepcopy(rec) if rec is not None else None
