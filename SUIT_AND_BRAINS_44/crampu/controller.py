"""MUSE 030.1 CRAM-PU cognitive tier controller (repaired & integrated candidate).

Trusted caller must authenticate agent identity before invoking this library. The
administrator token is a local process secret, not a network authentication scheme.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

BIG49 = 1600
SMALL49 = 608
TOTAL = BIG49 + SMALL49
AUTHORITY = 'COGNITIVE_ADVISORY'


class CRAMError(Exception):
    pass


class AuthorizationError(CRAMError):
    pass


class ConflictError(CRAMError):
    pass


class IntegrityError(CRAMError):
    pass


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def block_type(block_id):
    if type(block_id) is not int or not 0 <= block_id < TOTAL:
        raise ValueError('block_id must be integer 0..2207')
    return 'BIG49' if block_id < BIG49 else 'SMALL49'


def durable_write(path: Path, data: bytes):
    """Single-filesystem atomic publication. Assumes honest POSIX filesystem/device."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        dirfd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if tmp.exists():
            tmp.unlink()


class CRAMPUMemoryController:
    def __init__(
        self,
        warm_dir,
        cool_dir,
        admin_token,
        *,
        max_hot_entries: int = 256,
        max_hot_bytes: int = 1048576,
    ):
        if not isinstance(admin_token, str) or len(admin_token) < 16:
            raise ValueError('admin_token must be a strong caller-held secret (>=16 chars)')
        if max_hot_entries <= 0 or max_hot_bytes <= 0:
            raise ValueError('HOT bounds must be positive')
        self._admin = admin_token
        self.max_hot_entries = max_hot_entries
        self.max_hot_bytes = max_hot_bytes
        self.warm = Path(warm_dir).resolve()
        self.cool = Path(cool_dir).resolve()
        if self.warm == self.cool or self.warm in self.cool.parents or self.cool in self.warm.parents:
            raise ValueError('warm and cool must be separate directories')
        self.warm.mkdir(parents=True, exist_ok=True)
        self.cool.mkdir(parents=True, exist_ok=True)
        os.chmod(self.warm, 0o700)
        os.chmod(self.cool, 0o700)
        self._hot: OrderedDict[int, dict] = OrderedDict()
        self._hot_sizes: dict[int, int] = {}
        self._hot_bytes = 0
        self._hot_evictions = 0
        self._staged: dict[str, tuple[int, str, Path]] = {}
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.cool / 'registry.sqlite3', timeout=30, check_same_thread=False, isolation_level=None)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA synchronous=FULL')
        self._db.execute('PRAGMA foreign_keys=ON')
        self._db.executescript('''
        CREATE TABLE IF NOT EXISTS owners(
            block_id INTEGER PRIMARY KEY CHECK(block_id BETWEEN 0 AND 2207),
            agent_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS blocks(
            block_id INTEGER PRIMARY KEY,
            revision INTEGER NOT NULL,
            record BLOB NOT NULL,
            FOREIGN KEY(block_id) REFERENCES owners(block_id)
        );
        CREATE TABLE IF NOT EXISTS transitions(
            txid TEXT PRIMARY KEY,
            block_id INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            agent_id TEXT NOT NULL,
            action TEXT NOT NULL,
            record_sha256 TEXT NOT NULL,
            committed_ns INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outbox(
            txid TEXT PRIMARY KEY,
            block_id INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            agent_id TEXT NOT NULL,
            record_sha256 TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PENDING','APPLIED','ACKNOWLEDGED')),
            brain_record_id TEXT,
            last_error TEXT,
            updated_ns INTEGER NOT NULL,
            FOREIGN KEY(txid) REFERENCES transitions(txid)
        );
        CREATE TABLE IF NOT EXISTS mirror_revisions(
            txid TEXT PRIMARY KEY REFERENCES outbox(txid),
            record BLOB NOT NULL,
            bucket TEXT,
            binding_sha256 TEXT NOT NULL
        );
        ''')
        # FIX-REV2: durable per-entry disposition for permanent outbox failures.
        # Added by migration, not by changing the CREATE TABLE above, so RC1- and
        # Fix-Rev-1-created databases open cleanly. The existing status CHECK
        # constraint is untouched, so status values stay PENDING/APPLIED/
        # ACKNOWLEDGED; a permanently failed row keeps status PENDING and is
        # distinguished by disposition='BLOCKED_PERMANENT' plus last_error.
        cols = [row[1] for row in self._db.execute("PRAGMA table_info(outbox)").fetchall()]
        if "disposition" not in cols:
            self._db.execute("ALTER TABLE outbox ADD COLUMN disposition TEXT")

    def close(self):
        with self._lock:
            self._db.close()

    def _admin_check(self, token):
        import hmac
        if not isinstance(token, str) or not hmac.compare_digest(token, self._admin):
            raise AuthorizationError('administrator authorization required')

    def _put_hot_bounded(self, block_id: int, item: dict) -> None:
        item_bytes = len(canonical(item))
        if item_bytes > self.max_hot_bytes:
            raise ValueError(f'HOT entry ({item_bytes} B) exceeds max_hot_bytes ({self.max_hot_bytes} B)')
        if block_id in self._hot:
            self._hot_bytes -= self._hot_sizes.pop(block_id, 0)
            self._hot.pop(block_id)
        while self._hot and (len(self._hot) >= self.max_hot_entries or self._hot_bytes + item_bytes > self.max_hot_bytes):
            evicted_id, _ = self._hot.popitem(last=False)
            self._hot_bytes -= self._hot_sizes.pop(evicted_id, 0)
            self._hot_evictions += 1
        self._hot[block_id] = item
        self._hot_sizes[block_id] = item_bytes
        self._hot_bytes += item_bytes

    def register_block_owner(self, block_id, agent_id, admin_token):
        block_type(block_id)
        self._admin_check(admin_token)
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError('nonempty agent ID required')
        with self._lock:
            row = self._db.execute('SELECT agent_id FROM owners WHERE block_id=?', (block_id,)).fetchone()
            if row and row[0] != agent_id:
                raise ConflictError('ownership transfer requires separate audited migration')
            self._db.execute('INSERT OR IGNORE INTO owners VALUES (?,?)', (block_id, agent_id))

    def _authorize(self, block_id, agent_id):
        block_type(block_id)
        row = self._db.execute('SELECT agent_id FROM owners WHERE block_id=?', (block_id,)).fetchone()
        if not row or row[0] != agent_id:
            raise AuthorizationError('block not assigned to requesting agent')

    def _revision(self, block_id):
        row = self._db.execute('SELECT revision FROM blocks WHERE block_id=?', (block_id,)).fetchone()
        return row[0] if row else 0

    def write_hot(self, agent_id, block_id, payload):
        with self._lock:
            self._authorize(block_id, agent_id)
            if not isinstance(payload, dict):
                raise ValueError('payload must be object')
            canonical(payload)
            item = {
                'block_id': block_id,
                'block_type': block_type(block_id),
                'owner_agent_id': agent_id,
                'authority_level': AUTHORITY,
                'schema_version': '030.1',
                'payload': copy.deepcopy(payload),
                'base_revision': self._revision(block_id),
            }
            self._put_hot_bounded(block_id, item)
            return copy.deepcopy(item)

    def stage_to_warm(self, agent_id, block_id):
        with self._lock:
            self._authorize(block_id, agent_id)
            item = self._hot.get(block_id)
            if not item or item['owner_agent_id'] != agent_id:
                raise KeyError('no authorized HOT state')
            self._hot.move_to_end(block_id)
            txid = uuid.uuid4().hex
            record = copy.deepcopy(item)
            record['txid'] = txid
            record['checksum'] = digest({k: v for k, v in record.items() if k != 'checksum'})
            path = self.warm / (txid + '.json')
            durable_write(path, canonical(record))
            self._staged[txid] = (block_id, agent_id, path)
            return txid

    def commit_to_cool(self, agent_id, txid, *, enqueue_mirror: bool = False, mirror_bucket: str | None = None, fault_hook=None):
        with self._lock:
            # Idempotent replay check: if txid already committed in COOL transitions
            existing_tx = self._db.execute(
                'SELECT block_id, revision, agent_id, record_sha256 FROM transitions WHERE txid=? AND action=?',
                (txid, 'COMMIT'),
            ).fetchone()
            if existing_tx is not None:
                ebid, erev, eagent, esha = existing_tx
                if eagent != agent_id:
                    raise AuthorizationError('transaction owner mismatch on replay')
                self._authorize(ebid, agent_id)
                return {'block_id': ebid, 'revision': erev, 'txid': txid, 'sha256': esha, 'replayed': True}

            staged = self._staged.get(txid)
            if not staged:
                raise KeyError('unknown staged transaction')
            block_id, owner, path = staged
            self._authorize(block_id, agent_id)
            if owner != agent_id:
                raise AuthorizationError('transaction owner mismatch')
            data = json.loads(path.read_bytes())
            checksum = data.pop('checksum')
            if digest(data) != checksum:
                raise IntegrityError('staged record checksum mismatch')
            if (
                data['block_id'] != block_id
                or data['block_type'] != block_type(block_id)
                or data['owner_agent_id'] != owner
                or data['authority_level'] != AUTHORITY
                or data['schema_version'] != '030.1'
                or data['txid'] != txid
            ):
                raise IntegrityError('staged metadata mismatch')
            if not isinstance(data['payload'], dict):
                raise IntegrityError('invalid payload')
            expected = data['base_revision']
            current = self._revision(block_id)
            if current != expected:
                raise ConflictError(f'stale revision {expected}, current {current}')
            revision = current + 1
            stored = {k: v for k, v in data.items() if k not in ('txid', 'base_revision')}
            stored['revision'] = revision
            stored['checksum'] = digest(stored)
            blob = canonical(stored)
            blob_sha = hashlib.sha256(blob).hexdigest()
            now_ns = time.time_ns()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                if self._revision(block_id) != expected:
                    raise ConflictError('concurrent revision conflict')
                self._db.execute(
                    'INSERT INTO blocks(block_id,revision,record) VALUES(?,?,?) '
                    'ON CONFLICT(block_id) DO UPDATE SET revision=excluded.revision,record=excluded.record',
                    (block_id, revision, blob),
                )
                self._db.execute(
                    'INSERT INTO transitions VALUES(?,?,?,?,?,?,?)',
                    (txid, block_id, revision, agent_id, 'COMMIT', blob_sha, now_ns),
                )
                if enqueue_mirror:
                    self._db.execute(
                        'INSERT INTO outbox(txid,block_id,revision,agent_id,record_sha256,status,brain_record_id,last_error,updated_ns) '
                        'VALUES(?,?,?,?,?,?,?,?,?)',
                        (txid, block_id, revision, agent_id, blob_sha, 'PENDING', None, None, now_ns),
                    )
                    self._db.execute(
                        'INSERT INTO mirror_revisions VALUES(?,?,?,?)',
                        (txid, blob, mirror_bucket, digest([txid, blob_sha, mirror_bucket])),
                    )
                if fault_hook is not None:
                    fault_hook('before_cool_commit')
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise
            self._staged.pop(txid, None)
            try:
                path.unlink()
            except OSError:
                pass
            return {'block_id': block_id, 'revision': revision, 'txid': txid, 'sha256': blob_sha, 'replayed': False}

    def load_from_cool(self, block_id, agent_id):
        data, _ref = self._load_from_cool_bound(block_id, agent_id)
        return data

    def load_from_cool_with_reference(self, block_id, agent_id):
        """FIX-42.1.2: payload and COOL reference from ONE revision-bound
        read under the controller lock. Previously execute_memory_read read
        the payload (via load_from_cool) and then fetched the reference in a
        second, separate read, so a write landing between the two could make
        the reference describe a different revision than the payload. The
        reference here is built from the same revision, txid and digest the
        payload was verified against, so they cannot disagree."""
        return self._load_from_cool_bound(block_id, agent_id)

    def _load_from_cool_bound(self, block_id, agent_id):
        with self._lock:
            self._authorize(block_id, agent_id)
            row = self._db.execute('SELECT record,revision FROM blocks WHERE block_id=?', (block_id,)).fetchone()
            if not row:
                raise KeyError('no committed COOL block')
            blob, rev = row
            blob_sha = hashlib.sha256(blob).hexdigest()
            tx_row = self._db.execute(
                'SELECT agent_id, record_sha256, txid FROM transitions WHERE block_id=? AND revision=? AND action=? ORDER BY rowid DESC LIMIT 1',
                (block_id, rev, 'COMMIT'),
            ).fetchone()
            if tx_row is None or tx_row[0] != agent_id or tx_row[1] != blob_sha:
                raise IntegrityError('COOL ledger/ownership cross-check mismatch')
            data = json.loads(blob)
            checksum = data.pop('checksum')
            if digest(data) != checksum:
                raise IntegrityError('COOL checksum mismatch')
            if (
                data['block_id'] != block_id
                or data['block_type'] != block_type(block_id)
                or data['owner_agent_id'] != agent_id
                or data['authority_level'] != AUTHORITY
                or data['schema_version'] != '030.1'
                or data['revision'] != rev
            ):
                raise IntegrityError('COOL metadata mismatch')
            hot_item = {k: copy.deepcopy(v) for k, v in data.items() if k != 'revision'}
            hot_item['base_revision'] = data['revision']
            self._put_hot_bounded(block_id, hot_item)
            reference = {
                'block_id': block_id,
                'address': block_id,
                'revision': rev,
                'txid': tx_row[2],
                'sha256': blob_sha,
            }
            return copy.deepcopy(data), reference

    def purge_hot_state(self, block_id, agent_id):
        with self._lock:
            self._authorize(block_id, agent_id)
            if block_id in self._hot:
                self._hot.pop(block_id, None)
                self._hot_bytes -= self._hot_sizes.pop(block_id, 0)
                return True
            return False

    # ------------------------------------------------------------------
    # Transactional Outbox Helpers
    # ------------------------------------------------------------------
    def load_mirror_revision(self, txid, block_id, revision, agent_id, expected_sha):
        with self._lock:
            self._authorize(block_id, agent_id)
            row = self._db.execute(
                'SELECT record,bucket,binding_sha256 FROM mirror_revisions WHERE txid=?', (txid,)
            ).fetchone()
            if row is None:
                raise IntegrityError('legacy outbox entry lacks revision-bound payload and bucket')
            blob, bucket, binding = row
            transition = self._db.execute(
                'SELECT block_id,revision,agent_id,record_sha256 FROM transitions WHERE txid=? AND action=?',
                (txid, 'COMMIT'),
            ).fetchone()
            if (
                transition != (block_id, revision, agent_id, expected_sha)
                or hashlib.sha256(blob).hexdigest() != expected_sha
                or binding != digest([txid, expected_sha, bucket])
            ):
                raise IntegrityError('outbox revision binding mismatch')
            data = json.loads(blob)
            if (data['block_id'], data['revision'], data['owner_agent_id']) != (block_id, revision, agent_id):
                raise IntegrityError('outbox revision metadata mismatch')
            return data['payload'], bucket

    def list_outbox(self, statuses=('PENDING', 'APPLIED')):
        with self._lock:
            placeholders = ','.join('?' for _ in statuses)
            return self._db.execute(
                f'SELECT txid,block_id,revision,agent_id,record_sha256,status,brain_record_id,last_error '
                f'FROM outbox WHERE status IN ({placeholders}) ORDER BY rowid',
                tuple(statuses),
            ).fetchall()

    def list_outbox_with_disposition(self, statuses=('PENDING', 'APPLIED')):
        """FIX-REV2: same rows as list_outbox plus the durable disposition (9th field)."""
        with self._lock:
            placeholders = ','.join('?' for _ in statuses)
            return self._db.execute(
                f'SELECT txid,block_id,revision,agent_id,record_sha256,status,brain_record_id,last_error,disposition '
                f'FROM outbox WHERE status IN ({placeholders}) ORDER BY rowid',
                tuple(statuses),
            ).fetchall()

    def count_pending_mirror(self, bucket: str) -> int:
        """FIX-42.1.1: PENDING outbox rows whose recorded mirror bucket is
        `bucket` — i.e. queued writes that still need a mirror slot when the
        drain runs. APPLIED rows are not counted: their mirror put already
        happened, so the store count includes them. A PENDING row whose mirror
        put already happened (crash between put and APPLIED) is counted again
        here; that double-count is conservative — it can only reject a write
        that a later drain would have let through, never the reverse. Rows
        with no recorded bucket (legacy, mirror_revisions deleted) count for
        no bucket; they fail closed at drain time instead."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM outbox o JOIN mirror_revisions m "
                "ON m.txid = o.txid "
                "WHERE o.status = 'PENDING' AND m.bucket = ?",
                (bucket,),
            ).fetchone()
            return row[0] if row else 0

    def list_blocked_outbox(self):
        """FIX-REV2: rows permanently blocked by a mirror validation failure."""
        with self._lock:
            return self._db.execute(
                "SELECT txid,block_id,revision,agent_id,record_sha256,status,brain_record_id,last_error "
                "FROM outbox WHERE disposition='BLOCKED_PERMANENT' ORDER BY rowid"
            ).fetchall()

    def outbox_row(self, txid):
        """FIX-REV2: one outbox row by txid (8-field tuple, as list_outbox), or None."""
        with self._lock:
            return self._db.execute(
                'SELECT txid,block_id,revision,agent_id,record_sha256,status,brain_record_id,last_error '
                'FROM outbox WHERE txid=?',
                (txid,),
            ).fetchone()

    def cool_reference(self, block_id, agent_id):
        """FIX-REV2 (brief 2.3): authoritative COOL reference for a block.

        Identifies address, revision, txid and digest of the latest COMMIT
        transition. Ownership is checked, as in load_from_cool.
        """
        with self._lock:
            self._authorize(block_id, agent_id)
            row = self._db.execute(
                'SELECT txid,revision,record_sha256 FROM transitions '
                'WHERE block_id=? AND action=? ORDER BY rowid DESC LIMIT 1',
                (block_id, 'COMMIT'),
            ).fetchone()
            if row is None:
                raise KeyError('no committed COOL block')
            txid, revision, sha = row
            return {
                'block_id': block_id,
                'address': block_id,
                'revision': revision,
                'txid': txid,
                'sha256': sha,
            }

    def mark_outbox_applied(self, txid: str, brain_record_id: str) -> None:
        with self._lock:
            self._db.execute(
                'UPDATE outbox SET status=?, brain_record_id=?, last_error=NULL, updated_ns=? WHERE txid=?',
                ('APPLIED', brain_record_id, time.time_ns(), txid),
            )

    def mark_outbox_acknowledged(self, txid: str) -> None:
        with self._lock:
            self._db.execute(
                'UPDATE outbox SET status=?, last_error=NULL, updated_ns=? WHERE txid=?',
                ('ACKNOWLEDGED', time.time_ns(), txid),
            )

    def mark_outbox_error(self, txid: str, error: str) -> None:
        with self._lock:
            self._db.execute(
                'UPDATE outbox SET last_error=?, updated_ns=? WHERE txid=?',
                (error[:500], time.time_ns(), txid),
            )

    def mark_outbox_blocked(self, txid: str, error: str) -> None:
        """FIX-REV2: permanently block one outbox row (durable disposition + diagnostic).

        Status stays PENDING (the CHECK constraint is untouched); the row is
        distinguished by disposition='BLOCKED_PERMANENT' and is skipped by
        later drains instead of aborting them.
        """
        with self._lock:
            self._db.execute(
                "UPDATE outbox SET disposition=?, last_error=?, updated_ns=? WHERE txid=?",
                ('BLOCKED_PERMANENT', error[:500], time.time_ns(), txid),
            )

    def committed_blocks_summary(self):
        with self._lock:
            return self._db.execute(
                'SELECT b.block_id, b.revision, o.agent_id, t.txid, t.record_sha256 '
                'FROM blocks b '
                'JOIN owners o ON b.block_id = o.block_id '
                'JOIN transitions t ON t.block_id = b.block_id AND t.revision = b.revision AND t.action = "COMMIT" '
                'ORDER BY b.block_id'
            ).fetchall()

    def transitions(self, admin_token):
        self._admin_check(admin_token)
        return self._db.execute(
            'SELECT txid,block_id,revision,agent_id,action,record_sha256,committed_ns FROM transitions ORDER BY rowid'
        ).fetchall()

    def inspect(self, admin_token):
        self._admin_check(admin_token)
        return {
            'owners': self._db.execute('SELECT COUNT(*) FROM owners').fetchone()[0],
            'committed': self._db.execute('SELECT COUNT(*) FROM blocks').fetchone()[0],
            'transitions': self._db.execute('SELECT COUNT(*) FROM transitions').fetchone()[0],
            'outbox_pending': self._db.execute("SELECT COUNT(*) FROM outbox WHERE status IN ('PENDING','APPLIED')").fetchone()[0],
            'outbox_blocked': self._db.execute("SELECT COUNT(*) FROM outbox WHERE disposition='BLOCKED_PERMANENT'").fetchone()[0],
            'hot': len(self._hot),
            'hot_bytes': self._hot_bytes,
            'hot_evictions': self._hot_evictions,
            'staged': len(self._staged),
        }
