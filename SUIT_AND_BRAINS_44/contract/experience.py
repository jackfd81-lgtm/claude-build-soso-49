"""Integration Contract 001 — one durable logical experience record.

Correction #2 from JD's review: the two retrieval paths must be
TWO PROJECTIONS OF ONE DURABLE OUTCOME, not two independently
written claims. So there is exactly one experience store: RealCog's
Big49. Each logical experience is one Big49 block (immutable,
write-once, hash-chained by the store itself). The record retains
observation, validator result, demonstrated conditions,
qualification state, source identity, and the associated exact
signature — everything either projection needs, in one place.

Qualification is a state machine over immutable records: a state
change appends a NEW block with a higher sequence number for the
same (task, procedure) pair. Readers take the latest block per
pair; older blocks are history, not authority.

Qualification states (the SoSo/MEM-002 distinctions, kept
separate — never silently collapsed):
  OBSERVED     — an outcome was reported or measured.
  VALIDATED    — checked against declared independent criteria.
  CANDIDATE    — a trial success, stored but NOT trusted: it is
                 excluded from both projections (the trusted
                 index) until it passes transfer qualification.
  UNDER_TEST   — undergoing transfer qualification.
  QUALIFIED    — demonstrated within explicit applicability
                 conditions; enters both projections.
  INCONCLUSIVE — evidence insufficient to decide.
  REJECTED     — evidence or criteria contradict the success.

Projections (both derived from the same blocks, rebuilt — never
written independently, so a crash during learning cannot create
conflicting projections):
  exact_index: signature -> {procedure_id: record} for QUALIFIED
    records only. Lower's trusted index.
  transfer_layer: an in-memory SoSo layer (durable_memory chains
    + cognitive_layer) fed with QUALIFIED and UNDER_TEST records
    via learn_attempts, with the validator confirming only records
    whose validator result is PASS. SoSo's retrieval operates on
    this projection, not on a second experience store.
"""
from __future__ import annotations

import json
import sqlite3

from . import task as task_mod

QUALIFICATIONS = ("OBSERVED", "VALIDATED", "CANDIDATE", "UNDER_TEST",
                  "QUALIFIED", "INCONCLUSIVE", "REJECTED",
                  "SUPERSEDED", "WITHDRAWN")
# Records in these states enter the trusted projections.
TRUSTED = ("QUALIFIED", "UNDER_TEST")
# Contract 002 — append-only supersession / withdrawal. A
# SUPERSEDED record means "this pair's qualification is replaced
# by a newer one" (the replacement pair is named in the record's
# "sup" field); a WITHDRAWN record means "new evidence
# contradicts the original qualification — it is revoked". Both
# drop the pair from the trusted projections; the history (all
# blocks) is preserved, so the full qualification story survives.
PRINCIPAL = "L30"
BUCKET = "cognitive"
MAX_CONTENT_CHARS = 480  # Big49 cognitive bucket allows 512; keep margin.


def record_id(task_id: str, native_proc: str, seq: int) -> str:
    return f"exp.{task_id}.{native_proc}.{seq}"


def build_content(*, task_id, signature, family, procedure_id,
                  observed_outcome, validator_result, conditions,
                  qualification, source, tags, seq, why=None, sup=None) -> str:
    """Compact canonical JSON for the Big49 block content. Fail
    closed (ValueError) if it would exceed the bucket's char cap —
    a record that does not fit is an error, never a truncation.
    `why` (a short reason, Contract 002) and `sup` (the task id
    that supersedes this record) are optional and omitted when
    absent, so v1 records are unchanged."""
    rec = {"v": 1, "task": task_id, "sig": signature, "fam": family,
           "proc": procedure_id, "obs": observed_outcome,
           "val": validator_result, "cond": dict(conditions),
           "qual": qualification, "src": source,
           "tags": sorted(set(tags)), "seq": seq}
    if why:
        rec["why"] = why
    if sup:
        rec["sup"] = sup
    content = task_mod.canon(rec)
    if len(content) > MAX_CONTENT_CHARS:
        raise ValueError(
            f"experience record too large ({len(content)} chars > "
            f"{MAX_CONTENT_CHARS}); shorten tags/conditions")
    return content


def parse_content(content: str) -> dict:
    rec = json.loads(content)
    assert rec.get("v") == 1, "unknown experience record version"
    return rec


class ExperienceStore:
    """The single durable experience store, over a Big49. All
    writes go through here; all reads (both projections) derive
    from here."""

    def __init__(self, big):
        self.big = big

    def _existing_seqs(self, task_id: str, native_proc: str) -> list:
        rows = self.big.db.execute(
            "SELECT record_id FROM blocks WHERE pool='big' AND record_id LIKE ?",
            (f"exp.{task_id}.{native_proc}.%",)).fetchall()
        seqs = []
        for (rid,) in rows:
            try:
                seqs.append(int(rid.rsplit(".", 1)[1]))
            except ValueError:
                pass
        return seqs

    def write(self, *, task_id, signature, family, procedure_id,
              observed_outcome, validator_result, conditions,
              qualification, source, tags, why=None, sup=None) -> dict:
        """Append one experience block. Returns the parsed record.
        Raises the store's Rejected on any violation (duplicate,
        capacity, oversize) — the caller must not proceed as if
        the experience had been recorded."""
        from ph6_brains_2208 import Block  # local import: RealCog source
        if qualification not in QUALIFICATIONS:
            raise ValueError(f"unknown qualification: {qualification}")
        if observed_outcome not in ("SUCCESS", "FAILURE"):
            raise ValueError(f"bad observed outcome: {observed_outcome}")
        if validator_result not in ("PASS", "FAIL", "INCONCLUSIVE"):
            raise ValueError(f"bad validator result: {validator_result}")
        from .registry import by_native, get as reg_get
        entry = reg_get(procedure_id)
        native = entry.native_id
        seq = (max(self._existing_seqs(task_id, native), default=0) + 1)
        content = build_content(
            task_id=task_id, signature=signature, family=family,
            procedure_id=procedure_id, observed_outcome=observed_outcome,
            validator_result=validator_result, conditions=conditions,
            qualification=qualification, source=source, tags=tags,
            seq=seq, why=why, sup=sup)
        block = Block(record_id(task_id, native, seq), PRINCIPAL, BUCKET,
                      content, graph="G_S", relation="ASSOCIATED_WITH",
                      source_refs=(f"int:{task_id}",))
        self.big.put(block, principal=PRINCIPAL)
        return parse_content(content)

    def read_all(self) -> list:
        """Every experience block, parsed. Read-only SELECT on the
        store's own table — the same sanctioned pattern RealCog's
        pointer_for uses. Returns records in write order."""
        rows = self.big.db.execute(
            "SELECT payload FROM blocks WHERE pool='big' "
            "AND record_id LIKE 'exp.%' ORDER BY rowid").fetchall()
        out = []
        for (raw,) in rows:
            payload = json.loads(raw)
            out.append(parse_content(payload["content"]))
        return out

    def history(self, task_id: str, procedure_id: str) -> list:
        """The full qualification history for one (task, procedure)
        pair, oldest first. Every block is preserved — supersession
        and withdrawal append; they never erase."""
        return [r for r in self.read_all()
                if r["task"] == task_id and r["proc"] == procedure_id]

    def withdraw(self, *, task_id, procedure_id, observed_outcome,
                 validator_result, conditions, source, why) -> dict:
        """Append-only withdrawal: new evidence contradicts the
        original qualification. Only a trusted standing
        (QUALIFIED or UNDER_TEST) can be withdrawn. The pair drops
        from both projections; the history is preserved."""
        latest = self.latest_per_pair().get((task_id, procedure_id))
        if latest is None or latest["qual"] not in TRUSTED:
            raise ValueError(
                f"cannot withdraw {task_id}/{procedure_id}: "
                f"no trusted standing to withdraw")
        return self.write(
            task_id=task_id, signature=latest["sig"], family=latest["fam"],
            procedure_id=procedure_id, observed_outcome=observed_outcome,
            validator_result=validator_result, conditions=conditions,
            qualification="WITHDRAWN", source=source,
            tags=latest["tags"], why=why)

    def withdraw_procedure(self, *, procedure_id, scope=None,
                           observed_outcome, validator_result, conditions,
                           source, why) -> list:
        """Scope-aware procedure-wide withdrawal (HARDEN-001 fix 3,
        JD's correction to the design). Qualification is by
        procedure, version and applicable conditions — a local
        contradiction must not automatically invalidate unrelated
        valid experience.

        `scope` is a dict of demonstrated conditions (default None
        = global, every scope). A trusted pair is in scope when its
        demonstrated conditions contain every scope condition with
        the same value (the record was demonstrated under the
        withdrawn conditions). A pair whose demonstrated conditions
        do not contain the scope is untouched. Global withdrawal
        (scope None or {}) is reserved for evidence that the
        procedure itself is defective or unsafe across all scopes.

        Each affected pair gets its own WITHDRAWN block with the
        same contradicting evidence and reason; the per-pair
        history is preserved and the records are never deleted.
        Only trusted pairs (QUALIFIED or UNDER_TEST) are affected.
        Returns the list of withdrawn (task_id, procedure_id)
        pairs (empty when no trusted pair is in scope)."""
        scope = dict(scope or {})
        withdrawn = []
        for (t, p), r in sorted(self.latest_per_pair().items()):
            if p != procedure_id or r["qual"] not in TRUSTED:
                continue
            demonstrated = r.get("cond") or {}
            if any(demonstrated.get(k) != v for k, v in scope.items()):
                continue
            self.write(
                task_id=t, signature=r["sig"], family=r["fam"],
                procedure_id=p, observed_outcome=observed_outcome,
                validator_result=validator_result, conditions=conditions,
                qualification="WITHDRAWN", source=source,
                tags=r["tags"], why=why)
            withdrawn.append((t, p))
        return withdrawn

    def supersede(self, *, old_task_id, procedure_id, new_task_id,
                  source, why) -> dict:
        """Append-only supersession: the old pair's qualification
        is replaced by a newer one (named in the record's "sup"
        field). The new pair is qualified separately through the
        normal lifecycle. Only a trusted standing can be
        superseded."""
        latest = self.latest_per_pair().get((old_task_id, procedure_id))
        if latest is None or latest["qual"] not in TRUSTED:
            raise ValueError(
                f"cannot supersede {old_task_id}/{procedure_id}: "
                f"no trusted standing to supersede")
        return self.write(
            task_id=old_task_id, signature=latest["sig"],
            family=latest["fam"], procedure_id=procedure_id,
            observed_outcome=latest["obs"], validator_result=latest["val"],
            conditions=latest["cond"], qualification="SUPERSEDED",
            source=source, tags=latest["tags"], why=why, sup=new_task_id)

    def latest_per_pair(self) -> dict:
        """{(task_id, procedure_id): latest record} — the logical
        experience set. Older blocks are history."""
        latest = {}
        for rec in self.read_all():
            key = (rec["task"], rec["proc"])
            prev = latest.get(key)
            if prev is None or _seq_of(rec) > _seq_of(prev):
                latest[key] = rec
        return latest

    def rebuild_projections(self, soso_modules):
        """Rebuild BOTH projections from the same blocks. Returns
        {"exact": exact_index, "transfer_conn": conn} where
        exact_index maps signature -> {procedure_id: record} for
        QUALIFIED records, and transfer_conn is a fresh in-memory
        SoSo layer fed with QUALIFIED + UNDER_TEST records. The
        projections are always rebuilt together from the same
        read, so they cannot disagree about what was learned."""
        dm, cl = soso_modules["durable_memory"], soso_modules["cognitive_layer"]
        latest = self.latest_per_pair()
        exact = {}
        conn = sqlite3.connect(":memory:")
        dm.ensure_tables(conn)
        for (task_id, proc_id), rec in sorted(latest.items()):
            if rec["qual"] == "QUALIFIED":
                exact.setdefault(rec["sig"], {})[proc_id] = rec
            if rec["qual"] in TRUSTED:
                # Feed the transfer layer only with validated
                # outcomes; the validator confirms exactly the
                # records whose validator result is PASS. The
                # layer is procedure-agnostic: a RealCog fix
                # learned here becomes transferable through its
                # tags exactly like a SoSo procedure.
                cl.learn_attempts(
                    conn, task_id, rec["tags"],
                    [(_native_of(proc_id), rec["obs"])],
                    validator=lambda pid, out, cid, _r=rec: (
                        _r["val"] == "PASS" and out == _r["obs"]))
        return {"exact": exact, "transfer_conn": conn}


def _seq_of(rec: dict) -> int:
    # The sequence is stored in the record itself; the latest
    # write for a (task, procedure) pair has the highest seq.
    return int(rec.get("seq", 0))


def _native_of(procedure_id: str) -> str:
    from .registry import get as reg_get
    return reg_get(procedure_id).native_id
