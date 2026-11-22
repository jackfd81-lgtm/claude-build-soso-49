"""PH6 2,208-block experimental Brains implementation (not recovered canon).

Python 3.11+, stdlib only. This module does not modify Suit 4.0 or Lane 1.
The adapter requires an injected, separately verified Suit gate callback.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Callable
import unicodedata

AUTHORITY = 0
PROFILE = 'ph6-brains-2208/0.1-experimental'
GRAPHS = frozenset(('G_P', 'G_S', 'G_N', 'G_C', 'G_V'))
REGIONS = {
    1: ('L01', 'L07', 'EVIDENCE_INTERFACE'),
    2: ('L08', 'L14', 'TOK_REPRESENTATION'),
    3: ('L15', 'L21', 'RELATIONSHIP_CORTEX'),
    4: ('L22', 'L28', 'TEMPORAL_CONTINUITY'),
    5: ('L29', 'L35', 'INVESTIGATION_NAVIGATION'),
    6: ('L36', 'L42', 'AUDIT_METACOGNITION'),
    7: ('L43', 'L49', 'AI_CLOSET_GATES'),
}
# Big-49 per-block cap is NOT present in recovered source. 512 is a
# conservative EXPERIMENTAL candidate cap, not a historical specification.
BIG = {'cognitive': (1280, 512), 'association': (320, 512)}
# Strict Candidate-A 608; unchanged from the recovered allocation table.
SMALL = {
    'history': (256, 512), 'active': (64, 512),
    'workspace_micro': (24, 64), 'workspace_medium': (40, 250),
    'workspace_large': (64, 500), 'bridge_macro': (10, 1000),
    'bridge_projection': (40, 500), 'bridge_path': (20, 250),
    'bridge_typed': (60, 128), 'bridge_pointer': (30, 64),
}
assert sum(n for n, _ in BIG.values()) == 1600
assert sum(n for n, _ in SMALL.values()) == 608

class Rejected(ValueError):
    """Fail-closed host-level rejection; never a Suit Phase-B ReasonCode."""


def _canonical(obj: object) -> bytes:
    """Candidate host format, NOT soso40-canonical/1.0."""
    def check(value: object) -> object:
        if value is None or type(value) in (bool, int):
            return value
        if isinstance(value, str):
            return unicodedata.normalize('NFC', value)
        if isinstance(value, (list, tuple)):
            return [check(x) for x in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise Rejected('NON_STRING_KEY')
                norm = unicodedata.normalize('NFC', key)
                if norm in result:
                    raise Rejected('DUPLICATE_NORMALIZED_KEY')
                result[norm] = check(item)
            return result
        raise Rejected('UNSUPPORTED_CANONICAL_TYPE')
    return json.dumps(check(obj), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _digest(obj: object) -> str:
    return 'sha256:' + hashlib.sha256(_canonical(obj)).hexdigest()


def _region(owner: str) -> int:
    if not isinstance(owner, str) or not re.fullmatch(r'L(0[1-9]|[1-4][0-9])', owner):
        raise Rejected('OWNER_INVALID')
    return (int(owner[1:]) - 1) // 7 + 1


def _ref(value: str, namespace: str = 'int:') -> str:
    if not isinstance(value, str) or not value.startswith(namespace) or len(value) <= len(namespace):
        raise Rejected('SOURCE_NAMESPACE')
    return value


@dataclass(frozen=True)
class Pointer:
    record_id: str
    version: int
    digest: str
    owner: str
    pool: str
    authority: int = AUTHORITY

    @property
    def handle(self) -> str:
        return 'mram-s:' + self.record_id


@dataclass(frozen=True)
class Block:
    record_id: str
    owner: str
    bucket: str
    content: str
    parents: tuple[str, ...] = ()
    graph: str = 'G_S'
    relation: str = 'ASSOCIATED_WITH'
    causal_basis: str | None = None
    source_refs: tuple[str, ...] = ()
    authority: int = AUTHORITY
    # Independent access identity comes from the trusted runtime principal,
    # not from an unauthenticated wire field.


class MemoryStore:
    """Shared SQLite store with fixed pool quotas, immutable versions, and audit."""

    def __init__(self, path: str | Path | None, pool: str, *, connection=None):
        if pool not in ('big', 'small'):
            raise ValueError('UNKNOWN_POOL')
        self.pool = pool
        self.limits = BIG if pool == 'big' else SMALL
        self._joined = connection is not None
        if self._joined and not connection.in_transaction:
            raise ValueError('OUTER_TRANSACTION_REQUIRED')
        self.db = connection if self._joined else sqlite3.connect(str(path), timeout=10, isolation_level=None)
        try:
            if not self._joined:
                self.db.execute('PRAGMA foreign_keys=ON')
                self.db.execute('PRAGMA journal_mode=WAL')
                self.db.execute('PRAGMA synchronous=FULL')
                self.db.execute('PRAGMA busy_timeout=10000')
            self._init()
        except BaseException:
            self.close()
            raise

    def _begin(self):
        self.db.execute('SAVEPOINT brains_pool' if self._joined else 'BEGIN IMMEDIATE')

    def _commit(self):
        self.db.execute('RELEASE brains_pool' if self._joined else 'COMMIT')

    def _rollback(self):
        if not self.db.in_transaction:
            return
        self.db.execute('ROLLBACK TO brains_pool' if self._joined else 'ROLLBACK')
        if self._joined:
            self.db.execute('RELEASE brains_pool')

    def _init(self) -> None:
        schema = '''
        CREATE TABLE IF NOT EXISTS profile (
            pool TEXT PRIMARY KEY, profile TEXT NOT NULL, config_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS blocks (
            pool TEXT NOT NULL, record_id TEXT NOT NULL,
            owner TEXT NOT NULL, region INTEGER NOT NULL,
            bucket TEXT NOT NULL, slot INTEGER NOT NULL, version INTEGER NOT NULL,
            payload BLOB NOT NULL, digest TEXT NOT NULL,
            PRIMARY KEY(pool,record_id), UNIQUE(pool,bucket,slot)
        );
        CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, pool TEXT NOT NULL,
            record_id TEXT NOT NULL, operation TEXT NOT NULL,
            digest TEXT NOT NULL, prev_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL
        );
        '''
        for statement in schema.split(';'):
            if statement.strip():
                self.db.execute(statement)
        expected = _digest({'profile': PROFILE, 'pool': self.pool, 'limits': self.limits})
        self._begin()
        try:
            row = self.db.execute('SELECT profile,config_hash FROM profile WHERE pool=?', (self.pool,)).fetchone()
            if row is None:
                if self.db.execute('SELECT 1 FROM blocks WHERE pool=? LIMIT 1', (self.pool,)).fetchone() or self.db.execute('SELECT 1 FROM events WHERE pool=? LIMIT 1', (self.pool,)).fetchone():
                    raise Rejected('UNPROFILED_DATABASE')
                self.db.execute('INSERT INTO profile VALUES (?,?,?)', (self.pool, PROFILE, expected))
            elif row != (PROFILE, expected):
                raise Rejected('PROFILE_MISMATCH')
            self._commit()
        except BaseException:
            self._rollback()
            raise
        self.verify()

    def verify(self) -> None:
        rows = self.db.execute('SELECT record_id,payload,digest FROM blocks WHERE pool=?', (self.pool,))
        for record_id, raw, digest in rows:
            try:
                payload = json.loads(raw)
                if _digest(payload) != digest:
                    raise Rejected('RECORD_HASH_MISMATCH')
            except (ValueError, UnicodeError) as exc:
                raise Rejected('RECORD_HASH_MISMATCH') from exc
        prev = 'GENESIS'
        for record_id, operation, digest, previous, event_hash in self.db.execute(
            'SELECT record_id,operation,digest,prev_hash,event_hash FROM events WHERE pool=? ORDER BY seq', (self.pool,)
        ):
            if previous != prev or event_hash != _digest([self.pool, record_id, operation, digest, prev]):
                raise Rejected('EVENT_CHAIN_MISMATCH')
            prev = event_hash

    def count(self, bucket: str | None = None) -> int:
        if bucket is None:
            return self.db.execute('SELECT COUNT(*) FROM blocks WHERE pool=?', (self.pool,)).fetchone()[0]
        return self.db.execute('SELECT COUNT(*) FROM blocks WHERE pool=? AND bucket=?', (self.pool, bucket)).fetchone()[0]

    def put(self, block: Block, *, principal: str) -> Pointer:
        region = _region(principal)
        if type(block.authority) is not int or block.authority != 0:
            raise Rejected('AUTHORITY_ESCALATION')
        if block.owner != principal:
            raise Rejected('OWNER_DENIED')
        if block.bucket not in self.limits:
            raise Rejected('BUCKET_INVALID')
        if not isinstance(block.record_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', block.record_id):
            raise Rejected('ID_INVALID')
        if not isinstance(block.content, str):
            raise Rejected('CONTENT_INVALID')
        content = unicodedata.normalize('NFC', block.content)
        capacity, char_cap = self.limits[block.bucket]
        if len(content) > char_cap:
            raise Rejected('BLOCK_CHAR_LIMIT')
        if len(content.encode('utf-8')) > 4096:
            raise Rejected('BLOCK_BYTE_LIMIT')
        if block.graph not in GRAPHS:
            raise Rejected('GRAPH_INVALID')
        if not isinstance(block.relation, str) or not block.relation:
            raise Rejected('RELATION_INVALID')
        if block.relation == 'CAUSES' and (not block.causal_basis or block.causal_basis == 'UNDETERMINED'): raise Rejected('MISSING_CAUSAL_BASIS')
        if block.graph == 'G_P' and block.causal_basis in (None, '', 'UNDETERMINED'):
            raise Rejected('PROVENANCE_BASIS_REQUIRED')
        if block.relation == 'PROMOTES_TO_CAUSES':
            raise Rejected('CAUSAL_PROMOTION_DENIED')
        if any(not isinstance(p, str) or not p for p in block.parents):
            raise Rejected('PARENT_INVALID')
        if len(block.parents) != len(set(block.parents)):
            raise Rejected('DUPLICATE_PARENT')
        if any(p == block.record_id for p in block.parents):
            raise Rejected('PARENT_CYCLE')
        # Brains can reference ext: evidence supplied by a trusted resolver,
        # but may never MINT ext: references via this untrusted write API.
        if any(not isinstance(ref, str) or not ref.startswith('int:') for ref in block.source_refs):
            raise Rejected('EXT_PROVENANCE_MINT_DENIED')
        if len(block.parents) > 64 or len(block.source_refs) > 32:
            raise Rejected('RESOURCE_EXCEEDED')
        payload = {
            'record_id': block.record_id, 'owner': block.owner,
            'region': region, 'bucket': block.bucket, 'content': content,
            'parents': list(block.parents), 'graph': block.graph,
            'relation': block.relation, 'causal_basis': block.causal_basis,
            'source_refs': list(block.source_refs), 'authority': 0, 'version': 1,
        }
        raw = _canonical(payload)
        digest = _digest(payload)
        self._begin()
        try:
            if self.db.execute('SELECT 1 FROM blocks WHERE pool=? AND record_id=?', (self.pool, block.record_id)).fetchone():
                raise Rejected('ID_EXISTS')
            for parent in block.parents:
                if not self.db.execute('SELECT 1 FROM blocks WHERE pool=? AND record_id=?', (self.pool, parent)).fetchone():
                    raise Rejected('MISSING_PARENT')
            used = {row[0] for row in self.db.execute('SELECT slot FROM blocks WHERE pool=? AND bucket=?', (self.pool, block.bucket))}
            if len(used) >= capacity:
                raise Rejected('CAPACITY_EXHAUSTED')
            slot = next(i for i in range(capacity) if i not in used)
            self.db.execute('INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?,?)',
                            (self.pool, block.record_id, principal, region, block.bucket, slot, 1, raw, digest))
            previous = self.db.execute('SELECT event_hash FROM events WHERE pool=? ORDER BY seq DESC LIMIT 1', (self.pool,)).fetchone()
            prev = previous[0] if previous else 'GENESIS'
            event_hash = _digest([self.pool, block.record_id, 'PUT', digest, prev])
            self.db.execute('INSERT INTO events(pool,record_id,operation,digest,prev_hash,event_hash) VALUES (?,?,?,?,?,?)',
                            (self.pool, block.record_id, 'PUT', digest, prev, event_hash))
            self._commit()
        except BaseException:
            self._rollback()
            raise
        return Pointer(block.record_id, 1, digest, principal, self.pool)

    def read(self, pointer: Pointer, *, principal: str) -> dict:
        _region(principal)
        if type(pointer.authority) is not int or pointer.authority != 0 or pointer.owner != principal or pointer.pool != self.pool:
            raise Rejected('POINTER_DENIED')
        row = self.db.execute('SELECT owner,version,payload,digest FROM blocks WHERE pool=? AND record_id=?',
                              (self.pool, pointer.record_id)).fetchone()
        if row is None:
            raise Rejected('UNRESOLVED')
        owner, version, raw, digest = row
        if owner != principal or version != pointer.version or digest != pointer.digest:
            raise Rejected('POINTER_MISMATCH')
        try:
            data = json.loads(raw)
            if _digest(data) != digest:
                raise Rejected('RECORD_HASH_MISMATCH')
            return data
        except (ValueError, UnicodeError) as exc:
            raise Rejected('RECORD_HASH_MISMATCH') from exc

    def close(self) -> None:
        if not self._joined:
            self.db.close()


class Big49(MemoryStore):
    def __init__(self, path: str | Path | None = None, *, connection=None):
        super().__init__(path, 'big', connection=connection)


class Small49(MemoryStore):
    def __init__(self, path: str | Path | None = None, *, connection=None):
        super().__init__(path, 'small', connection=connection)


class BrainBridge:
    """No implicit Suit acceptance: requires an injected real gate callback.

    The callback must be provided by the deployment's separately verified
    Gate Engine integration. A truthy return means only gate eligibility,
    not Lane-1 validation. This is an in-process DEVELOPMENT adapter,
    not the Rev-4.3 Unix-socket peer-credential security boundary.
    """
    def __init__(self, big: Big49, small: Small49,
                 suit_gate: Callable[[dict], bool]):
        if not callable(suit_gate):
            raise Rejected('SUIT_GATE_REQUIRED')
        self.big = big
        self.small = small
        self.suit_gate = suit_gate

    def submit(self, pointer: Pointer, *, principal: str,
               target_bucket: str = 'active') -> Pointer:
        original = self.big.read(pointer, principal=principal)
        if original['authority'] != 0:
            raise Rejected('AUTHORITY_ESCALATION')
        # Gate always precedes any small-memory mutation.
        # An actual production integration MUST invoke its approved Suit
        # pipeline (not substitute the callback with a lambda).
        if self.suit_gate(dict(original)) is not True:
            raise Rejected('SUIT_GATE_DENIED')
        new_id = 'bridge-' + pointer.record_id
        return self.small.put(Block(
            record_id=new_id, owner=principal, bucket=target_bucket,
            content=original['content'], graph=original['graph'],
            relation=original['relation'], causal_basis=original['causal_basis'],
            source_refs=tuple(original['source_refs']), authority=0,
        ), principal=principal)
