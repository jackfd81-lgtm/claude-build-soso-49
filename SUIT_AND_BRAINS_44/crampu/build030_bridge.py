"""Merged Build 030.1 <-> CRAM-PU bridge with transactional outbox sync and Suit OD-05 gate."""
from __future__ import annotations

from pathlib import Path
import re
import threading
import unicodedata
from typing import Any

from crampu.adapters import Brain49MemoryAdapter, SuitMemoryAdapter
from crampu.controller import (
    AuthorizationError,
    BIG49,
    CRAMPUMemoryController,
    ConflictError,
    IntegrityError,
    block_type,
)
from realcog.brain_bridge_suit_gate import SUIT_VERSION, suit_gate_for_big_payload
from realcog.gtml_impl import ir as suit_ir
from realcog.gtml_impl.frontend import FrontendError, validate_causal
from realcog.ph6_brains_2208 import BIG, SMALL, Big49, Block, Pointer, Rejected, Small49


def _bucket_char_cap(family: str, bucket: str) -> int:
    """Char cap for a mirror bucket, from the RealCog bucket table.

    FIX-REV1 (defect 1): the bridge previously sliced every payload to 512
    characters regardless of bucket, truncating bridge_macro (cap 1000) to 512.
    FIX-REV1 (defect 2): an unknown bucket now raises here, before any COOL
    commit, instead of failing only at mirror time after COOL revision 1.
    """
    limits = BIG if family == "BIG49" else SMALL if family == "SMALL49" else None
    if limits is None or not isinstance(bucket, str) or bucket not in limits:
        raise Rejected("BUCKET_INVALID")
    return limits[bucket][1]


def _bucket_slot_count(family: str, bucket: str) -> int:
    """Slot count for a mirror bucket, from the RealCog bucket table.

    The table entry is (slots, char_cap); see _bucket_char_cap for the cap.
    """
    limits = BIG if family == "BIG49" else SMALL if family == "SMALL49" else None
    if limits is None or not isinstance(bucket, str) or bucket not in limits:
        raise Rejected("BUCKET_INVALID")
    return limits[bucket][0]


def _preflight_write(
    family: str, index: int, payload: dict[str, Any], bucket: str | None,
    store: Any | None = None,
    controller: Any | None = None,
) -> tuple[str, str]:
    """FIX-REV2: validate family, index, bucket and content BEFORE any HOT/WARM/COOL mutation.

    Runs in write_and_sync after the Suit OD-05 gate. Returns (family, resolved_bucket).
    Bucket None means "absent" and resolves to the family default (cognitive/active);
    an empty string, a non-string, or an unknown bucket is Rejected BUCKET_INVALID.
    Content is checked only when the payload carries content/text (the value the
    mirror will use); a payload with neither (e.g. a learned profile) mirrors as a
    txid reference and has no content to validate. Length is measured after NFC
    normalization, matching MemoryStore.put. Char length over the bucket cap is
    BLOCK_CHAR_LIMIT; UTF-8 byte length over 4096 is BLOCK_BYTE_LIMIT.

    FIX-42.1.1 (pre-commit capacity check): when the caller passes the mirror
    store, a new write is also rejected here with Rejected CAPACITY_EXHAUSTED
    if the target bucket already holds all of its slots. A new revision always
    needs a new mirror slot (the record_id carries the revision), so a full
    bucket can never mirror a new write — rejecting before COOL avoids the
    orphaned revision + BLOCKED row the mirror-time failure used to leave.
    The count is mirrored slots PLUS queued PENDING outbox rows for the bucket
    (via controller.count_pending_mirror, when a controller is passed): a
    queued write still needs a mirror slot when the drain runs, so it holds a
    reservation. This is a check-then-act guard, not a reservation, and it is
    only as strong as the lock around it: write_and_sync and
    sync_pending_outbox both run under the adapter RLock, so the check, the
    COOL commit and the mirror put are atomic with respect to other bridge
    operations in this process. The mirror-time CAPACITY_EXHAUSTED from
    MemoryStore.put stays as the backstop for anything outside that lock
    (direct store writes, another process), and a legacy row that reaches the
    drain that way is still contained as BLOCKED_PERMANENT. Note the stores
    themselves are single-thread-affine (sqlite default), so cross-thread
    bridge use is out of scope; the lock protects the in-process
    sequential/interleaved cases (including a claim made from a fault hook
    while another write is mid-flight).
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be object")
    if family not in ("BIG49", "SMALL49"):
        raise ValueError(f"unknown family: {family!r}")
    max_index = 1600 if family == "BIG49" else 608
    if type(index) is not int or not 0 <= index < max_index:
        raise ValueError(f"index out of range for {family}: {index!r}")
    if bucket is None:
        resolved_bucket = "cognitive" if family == "BIG49" else "active"
    else:
        if not isinstance(bucket, str) or not bucket:
            raise Rejected("BUCKET_INVALID")
        resolved_bucket = bucket
    cap = _bucket_char_cap(family, resolved_bucket)  # raises BUCKET_INVALID if unknown
    if store is not None:
        # Mirrored slots plus queued PENDING rows for this bucket: both will
        # need a mirror slot, so both hold against the bucket's slot count.
        queued = controller.count_pending_mirror(resolved_bucket) if controller is not None else 0
        if store.count(resolved_bucket) + queued >= _bucket_slot_count(family, resolved_bucket):
            raise Rejected("CAPACITY_EXHAUSTED")
    if "content" in payload:
        raw = payload["content"]
    elif "text" in payload:
        raw = payload["text"]
    else:
        raw = None
    if raw is not None:
        if not isinstance(raw, str):
            raise Rejected("CONTENT_INVALID")
        content = unicodedata.normalize("NFC", raw)
        if len(content) > cap:
            raise Rejected("BLOCK_CHAR_LIMIT")
        if len(content.encode("utf-8")) > 4096:
            raise Rejected("BLOCK_BYTE_LIMIT")
    return family, resolved_bucket

_PRINCIPAL_RE = re.compile(r"^L(0[1-9]|[1-4][0-9])$")


def build030_suit_capability_check(
    agent_id: str,
    action: str,
    block_id: int,
    *,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Real SoSo Suit capability & GTML OD-05 gate check for Build 030.1."""
    if not isinstance(agent_id, str) or not _PRINCIPAL_RE.fullmatch(agent_id):
        return False
    if action not in ("memory.read", "memory.write"):
        return False
    if type(block_id) is not int or not 0 <= block_id < 2208:
        return False
    if payload is not None:
        if not isinstance(payload, dict):
            return False
        if "authority" in payload and (type(payload["authority"]) is not int or payload["authority"] != 0):
            return False
        if payload.get("relation") == "PROMOTES_TO_CAUSES" or payload.get("promotes_to") == "CAUSES":
            return False
        if any(k in payload for k in ("relation", "causal_basis", "promotes_to")):
            basis = payload.get("causal_basis")
            if basis in (None, "", "UNDETERMINED"):
                basis = None
            relation = payload.get("relation", "ASSOCIATED_WITH")
            promotes_to = payload.get("promotes_to")
            try:
                stmt = suit_ir.Statement(
                    ident=suit_ir.Ident(sid=suit_ir.Handle("SID", str(payload.get("record_id", f"crampu-{block_id}")))),
                    subject=suit_ir.Handle("OID", "crampu-subject"),
                    object=suit_ir.Handle("OID", "crampu-object"),
                    predicate=relation,
                    causal_basis=basis,
                    promotes_to=promotes_to,
                )
                record = suit_ir.Record(version=SUIT_VERSION, statements=(stmt,))
                validate_causal(record)
            except (FrontendError, suit_ir.IRValidationError, ValueError, TypeError):
                return False
            return suit_gate_for_big_payload(
                {
                    "record_id": str(payload.get("record_id", f"crampu-{block_id}")),
                    "relation": relation,
                    "causal_basis": payload.get("causal_basis"),
                }
            ) is True
    return True


class Merged49BrainsCramAdapter:
    """Connects CRAMPUMemoryController (HOT/WARM/COOL + outbox) with Build 030.1 Big49/Small49."""

    def __init__(self, controller: CRAMPUMemoryController, brains_db_dir: Path) -> None:
        self.controller = controller
        self.brains_db_dir = Path(brains_db_dir)
        self.brains_db_dir.mkdir(parents=True, exist_ok=True)
        self.big = Big49(self.brains_db_dir / "big49.sqlite3")
        self.small = Small49(self.brains_db_dir / "small49.sqlite3")
        # FIX-42.1.1: one RLock serializing bridge writes and drains in this
        # process, so a preflight capacity check, its COOL commit and its
        # mirror put are atomic with respect to other bridge operations —
        # the final slot cannot be claimed twice by two interleaved writes.
        # (The stores themselves are single-thread-affine; cross-thread use
        # of one adapter is out of scope. The mirror-time put stays the
        # backstop for anything outside this lock.)
        self._lock = threading.RLock()

        def _cap(agent_id: str, action: str, block_id: int) -> bool:
            return build030_suit_capability_check(agent_id, action, block_id)

        self.suit_adapter = SuitMemoryAdapter(controller, _cap)
        self.brain_adapter = Brain49MemoryAdapter(self.suit_adapter)

    def _pool_and_index(self, block_id: int) -> tuple[str, int]:
        if block_id < BIG49:
            return "BIG49", block_id
        return "SMALL49", block_id - BIG49

    def _build_brain_block(
        self,
        agent_id: str,
        family: str,
        index: int,
        revision: int,
        txid: str,
        payload: dict[str, Any],
        bucket: str | None = None,
    ) -> Block:
        record_id = f"crampu-{family.lower()}-{index}-r{revision}"
        # FIX-REV2: only None means "absent" (family default). An empty string or
        # non-string bucket must reach _bucket_char_cap and raise BUCKET_INVALID
        # instead of silently falling back to the default.
        target_bucket = (
            ("cognitive" if family == "BIG49" else "active") if bucket is None else bucket
        )
        # FIX-REV1: slice to this bucket's own cap, not a hard-coded 512.
        # FIX-REV2: defence in depth only — preflight has already rejected any
        # over-cap write, so this slice must never truncate a validated write.
        content = str(payload.get("content", payload.get("text", txid)))[:_bucket_char_cap(family, target_bucket)]
        relation = str(payload.get("relation", "ASSOCIATED_WITH"))
        causal_basis = payload.get("causal_basis")
        return Block(
            record_id=record_id,
            owner=agent_id,
            bucket=target_bucket,
            content=content,
            graph="G_S",
            relation=relation,
            causal_basis=causal_basis,
            source_refs=(f"int:crampu:{txid}",),
            authority=0,
        )

    def _apply_mirror(
        self,
        agent_id: str,
        family: str,
        index: int,
        revision: int,
        txid: str,
        payload: dict[str, Any],
        bucket: str | None = None,
    ) -> Pointer:
        store = self.big if family == "BIG49" else self.small
        pool_name = "big" if family == "BIG49" else "small"
        block = self._build_brain_block(agent_id, family, index, revision, txid, payload, bucket=bucket)
        # Check if already mirrored (idempotent recovery after crash before ack)
        existing = store.db.execute(
            "SELECT owner, digest, payload FROM blocks WHERE pool=? AND record_id=?",
            (pool_name, block.record_id),
        ).fetchone()
        if existing is not None:
            ex_owner, ex_digest, _ = existing
            pointer = Pointer(block.record_id, 1, ex_digest, agent_id, pool_name)
            ex_data = store.read(pointer, principal=agent_id)
            if (
                ex_owner == agent_id
                and ex_data["source_refs"] == list(block.source_refs)
                and ex_data["bucket"] == block.bucket
                and ex_data["content"] == unicodedata.normalize("NFC", block.content)
                and ex_data["relation"] == block.relation
                and ex_data["causal_basis"] == block.causal_basis
            ):
                return pointer
            raise ConflictError(f"brain mirror conflict on {block.record_id}")
        return store.put(block, principal=agent_id)

    def write_and_sync(
        self,
        agent_id: str,
        family: str,
        index: int,
        payload: dict[str, Any],
        *,
        bucket: str | None = None,
        fault_hook=None,
    ) -> tuple[dict[str, Any], Pointer]:
        """Validate via Suit OD-05 gate, commit atomically to COOL + outbox, and sync to Big49/Small49."""
        with self._lock:
            return self._write_and_sync_inner(
                agent_id, family, index, payload, bucket=bucket, fault_hook=fault_hook
            )

    def _write_and_sync_inner(
        self,
        agent_id: str,
        family: str,
        index: int,
        payload: dict[str, Any],
        *,
        bucket: str | None = None,
        fault_hook=None,
    ) -> tuple[dict[str, Any], Pointer]:
        block_id = (
            self.brain_adapter.big49_id(index)
            if family == "BIG49"
            else self.brain_adapter.small49_id(index)
            if family == "SMALL49"
            else None
        )
        if block_id is None:
            raise ValueError("unknown family")
        if not build030_suit_capability_check(agent_id, "memory.write", block_id, payload=payload):
            raise AuthorizationError("SoSo Suit OD-05 gate denied write")

        # FIX-REV2: full preflight (family/index/bucket/content) BEFORE any
        # HOT/WARM/COOL state change. This supersedes the Fix Rev 1 bucket-only
        # check: an invalid bucket, a non-string content value, or over-cap
        # content is now rejected here, with no COOL revision and no outbox row.
        # FIX-42.1.1: pass the mirror store and controller so the preflight
        # also rejects a write whose bucket is already full — counting mirrored
        # slots plus queued PENDING rows — (CAPACITY_EXHAUSTED) before COOL.
        _preflight_write(family, index, payload, bucket,
                         store=self.big if family == "BIG49" else self.small,
                         controller=self.controller)

        self.controller.write_hot(agent_id, block_id, payload)
        if fault_hook is not None:
            fault_hook("after_hot_before_warm")
        txid = self.controller.stage_to_warm(agent_id, block_id)
        if fault_hook is not None:
            fault_hook("after_warm_before_cool")
        commit_rec = self.controller.commit_to_cool(
            agent_id, txid, enqueue_mirror=True, mirror_bucket=bucket, fault_hook=fault_hook
        )
        if fault_hook is not None:
            fault_hook("after_cool_before_mirror")
        synced = self.sync_pending_outbox(fault_hook=fault_hook)
        if txid not in synced:
            # FIX-REV2: the drain may legitimately skip or block this row (e.g. a
            # blocked earlier row for the same address, or a permanent mirror
            # failure). Report that honestly instead of a KeyError.
            row = self.controller.outbox_row(txid)
            error = (row[7] if row and row[7] else None) or "MIRROR_DEFERRED_BY_BLOCKED_ROW_FOR_BLOCK"
            raise Rejected(error)
        ptr = synced[txid]
        # FIX-REV2 (brief 2.3): honest references. The COOL reference is the
        # authoritative advisory record; the mirror reference is an optional
        # projection. A 'content' projection carries the payload's content
        # string; a 'txid-reference' projection (e.g. a learned profile, which
        # has no content/text) stores only the txid string and cannot
        # reconstruct the structured payload. The legacy pointer is unchanged.
        has_content = isinstance(payload, dict) and ("content" in payload or "text" in payload)
        projection_kind = "content" if has_content else "txid-reference"
        # FIX-42.1.2: the mirror keeps only ONE text string
        # (_build_brain_block takes payload["content"] over payload["text"]).
        # A payload that supplies both with different values is therefore
        # lossy — the dropped field cannot be rebuilt from the mirror — and
        # must not be labelled reconstructible. (Equal values are the same
        # string either way, so nothing is lost.) "text" alone is an alias
        # for the content string, not a second field.
        two_texts_differ = (
            isinstance(payload, dict)
            and "content" in payload and "text" in payload
            and isinstance(payload["content"], str) and isinstance(payload["text"], str)
            and unicodedata.normalize("NFC", payload["content"]) != unicodedata.normalize("NFC", payload["text"])
        )
        reconstructs = (
            projection_kind == "content"
            and isinstance(payload, dict)
            and set(payload.keys()) <= {"content", "text", "relation", "causal_basis"}
            and not two_texts_differ
        )
        commit_rec = dict(commit_rec)
        commit_rec["cool_reference"] = {
            "block_id": block_id,
            "address": block_id,
            "revision": commit_rec["revision"],
            "txid": commit_rec["txid"],
            "sha256": commit_rec["sha256"],
        }
        commit_rec["mirror_reference"] = {
            "pool": ptr.pool,
            "record_id": ptr.record_id,
            "version": ptr.version,
            "owner": ptr.owner,
            "digest": ptr.digest,
            "projection_kind": projection_kind,
            "reconstructs_structured_payload": reconstructs,
        }
        return commit_rec, ptr

    def sync_pending_outbox(self, *, bucket: str | None = None, fault_hook=None) -> dict[str, Pointer]:
        with self._lock:
            return self._sync_pending_outbox_inner(bucket=bucket, fault_hook=fault_hook)

    def _sync_pending_outbox_inner(self, *, bucket: str | None = None, fault_hook=None) -> dict[str, Pointer]:
        """Apply each entry using its recorded bucket, including after restart.

        FIX-REV2 drain policy: a permanent per-entry validation failure (Rejected
        from the mirror, e.g. BUCKET_INVALID) marks that row BLOCKED_PERMANENT
        and the drain CONTINUES to unrelated rows, so one bad row can no longer
        block later valid writes. Order is preserved for the same address: a row
        whose block already has a blocked row, or failed earlier in this drain,
        is skipped and stays PENDING. A legacy row that fails load_mirror_revision
        with IntegrityError still raises (test_pending_legacy_entry_fails_closed),
        as does any other unexpected exception.
        """
        if bucket is not None:
            raise ValueError("replay bucket comes from each committed transaction")
        results: dict[str, Pointer] = {}
        rows = self.controller.list_outbox_with_disposition()
        blocked_blocks = {row[1] for row in rows if row[8] == "BLOCKED_PERMANENT"}
        failed_blocks: set[int] = set()
        for txid, block_id, revision, agent_id, _sha, status, _brain_id, _err, disposition in rows:
            if disposition == "BLOCKED_PERMANENT":
                continue
            if block_id in blocked_blocks or block_id in failed_blocks:
                continue
            family, index = self._pool_and_index(block_id)
            try:
                payload, recorded_bucket = self.controller.load_mirror_revision(
                    txid, block_id, revision, agent_id, _sha
                )
                ptr = self._apply_mirror(
                    agent_id, family, index, revision, txid, payload, bucket=recorded_bucket
                )
            except IntegrityError as exc:
                # Legacy row without a revision-bound payload/bucket: fail closed.
                self.controller.mark_outbox_error(txid, str(exc))
                raise
            except Rejected as exc:
                # Permanent per-entry validation failure: block this row only.
                self.controller.mark_outbox_blocked(txid, str(exc))
                failed_blocks.add(block_id)
                continue
            except Exception as exc:
                self.controller.mark_outbox_error(txid, str(exc))
                raise
            try:
                self.controller.mark_outbox_applied(txid, ptr.record_id)
                if fault_hook is not None:
                    fault_hook("after_mirror_before_ack")
                self.controller.mark_outbox_acknowledged(txid)
            except Exception as exc:
                self.controller.mark_outbox_error(txid, str(exc))
                raise
            results[txid] = ptr
        return results

    def recover_missing_mirrors(self, *, bucket: str | None = None) -> list[Pointer]:
        with self._lock:
            return self._recover_missing_mirrors_inner(bucket=bucket)

    def _recover_missing_mirrors_inner(self, *, bucket: str | None = None) -> list[Pointer]:
        """Rebuild any missing Big49/Small49 mirrors from authoritative COOL blocks."""
        recovered: list[Pointer] = []
        mirrored = {row[0] for row in self.controller.list_outbox(
            ("PENDING", "APPLIED", "ACKNOWLEDGED")
        )}
        for block_id, revision, agent_id, txid, _sha in self.controller.committed_blocks_summary():
            family, index = self._pool_and_index(block_id)
            record_id = f"crampu-{family.lower()}-{index}-r{revision}"
            store = self.big if family == "BIG49" else self.small
            pool_name = "big" if family == "BIG49" else "small"
            exists = store.db.execute(
                "SELECT 1 FROM blocks WHERE pool=? AND record_id=?",
                (pool_name, record_id),
            ).fetchone()
            if exists is None:
                if txid in mirrored:
                    payload, recorded_bucket = self.controller.load_mirror_revision(
                        txid, block_id, revision, agent_id, _sha
                    )
                else:
                    payload = self.controller.load_from_cool(block_id, agent_id)["payload"]
                    recorded_bucket = bucket
                ptr = self._apply_mirror(
                    agent_id, family, index, revision, txid, payload, bucket=recorded_bucket
                )
                recovered.append(ptr)
        return recovered

    def read(self, agent_id: str, family: str, index: int) -> dict[str, Any]:
        return self.brain_adapter.read(agent_id, family, index)

    def verify_all(self) -> None:
        self.big.verify()
        self.small.verify()

    def close(self) -> None:
        self.big.close()
        self.small.close()
