"""Offline candidate tests; not frozen Suit tests or hardware qualification."""
import tempfile
import unittest
from pathlib import Path
from ph6_brains_2208 import (Big49, Small49, BrainBridge, Block,
                             Rejected, BIG, SMALL, REGIONS, GRAPHS)


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.big = Big49(root / 'big.sqlite3')
        self.small = Small49(root / 'small.sqlite3')

    def tearDown(self):
        self.big.close()
        self.small.close()
        self.tmp.cleanup()

    def test_topology_exact(self):
        self.assertEqual(sum(x[0] for x in BIG.values()), 1600)
        self.assertEqual(sum(x[0] for x in SMALL.values()), 608)
        self.assertEqual(sum(x[0] for x in BIG.values()) + sum(x[0] for x in SMALL.values()), 2208)
        self.assertEqual(len(REGIONS), 7)
        self.assertEqual(len(GRAPHS), 5)

    def test_big_512_character_cap(self):
        p = self.big.put(Block('exact', 'L15', 'cognitive', 'x' * 512), principal='L15')
        self.assertEqual(len(self.big.read(p, principal='L15')['content']), 512)
        with self.assertRaisesRegex(Rejected, 'BLOCK_CHAR_LIMIT'):
            self.big.put(Block('too-long', 'L15', 'cognitive', 'x' * 513), principal='L15')
        self.assertEqual(self.big.count(), 1)

    def test_small_strict_caps(self):
        self.small.put(Block('hist', 'L15', 'history', 'x' * 512), principal='L15')
        with self.assertRaisesRegex(Rejected, 'BLOCK_CHAR_LIMIT'):
            self.small.put(Block('hist2', 'L15', 'history', 'x' * 513), principal='L15')
        self.small.put(Block('micro', 'L15', 'workspace_micro', 'x' * 64), principal='L15')
        with self.assertRaisesRegex(Rejected, 'BLOCK_CHAR_LIMIT'):
            self.small.put(Block('micro2', 'L15', 'workspace_micro', 'x' * 65), principal='L15')
        self.assertEqual(self.small.count(), 2)

    def test_ext_mint_denied_without_event(self):
        before = self.big.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        with self.assertRaisesRegex(Rejected, 'EXT_PROVENANCE_MINT_DENIED'):
            self.big.put(Block('mint', 'L15', 'association', 'claim',
                               source_refs=('ext:forged',)), principal='L15')
        after = self.big.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(self.big.count(), 0)

    def test_big_to_small_guarded_bridge(self):
        p = self.big.put(Block('one', 'L15', 'cognitive', 'hello'), principal='L15')
        calls = []
        def gate(record):
            calls.append(record['record_id'])
            return True
        bridge = BrainBridge(self.big, self.small, gate)
        down = bridge.submit(p, principal='L15', target_bucket='active')
        self.assertEqual(calls, ['one'])
        self.assertEqual(down.authority, 0)
        self.assertEqual(self.small.read(down, principal='L15')['content'], 'hello')
        self.assertEqual(down.handle, 'mram-s:bridge-one')

    def test_guard_denial_no_write(self):
        p = self.big.put(Block('two', 'L15', 'cognitive', 'hello'), principal='L15')
        with self.assertRaisesRegex(Rejected, 'SUIT_GATE_DENIED'):
            BrainBridge(self.big, self.small, lambda _: False).submit(p, principal='L15')
        self.assertEqual(self.small.count(), 0)

    def test_owner_and_authority_denied(self):
        p = self.big.put(Block('three', 'L15', 'cognitive', 'hello'), principal='L15')
        with self.assertRaisesRegex(Rejected, 'POINTER_DENIED'):
            self.big.read(p, principal='L16')
        with self.assertRaisesRegex(Rejected, 'AUTHORITY_ESCALATION'):
            self.big.put(Block('four', 'L15', 'cognitive', 'claim', authority=1), principal='L15')

    def test_causal_and_provenance_rules(self):
        with self.assertRaisesRegex(Rejected, 'MISSING_CAUSAL_BASIS'):
            self.big.put(Block('a', 'L15', 'association', 'A causes B', relation='CAUSES'), principal='L15')
        with self.assertRaisesRegex(Rejected, 'PROVENANCE_BASIS_REQUIRED'):
            self.big.put(Block('b', 'L15', 'association', 'supports', graph='G_P'), principal='L15')
        self.big.put(Block('c', 'L15', 'association', 'proposition',
                           relation='CAUSES', causal_basis='explicit source'), principal='L15')

    def test_bucket_capacity_and_no_overwrite(self):
        for i in range(30):
            self.small.put(Block(f'p{i}', 'L15', 'bridge_pointer', 'x'), principal='L15')
        with self.assertRaisesRegex(Rejected, 'CAPACITY_EXHAUSTED'):
            self.small.put(Block('overflow', 'L15', 'bridge_pointer', 'x'), principal='L15')
        self.assertEqual(self.small.count('bridge_pointer'), 30)

    def test_full_big_capacity(self):
        for bucket, (count, _) in BIG.items():
            for i in range(count):
                self.big.put(Block(f'{bucket}-{i}', 'L15', bucket, 'x'), principal='L15')
            with self.assertRaisesRegex(Rejected, 'CAPACITY_EXHAUSTED'):
                self.big.put(Block(f'{bucket}-overflow', 'L15', bucket, 'x'), principal='L15')
        self.assertEqual(self.big.count(), 1600)

    def test_full_small_capacity(self):
        for bucket, (count, _) in SMALL.items():
            for i in range(count):
                self.small.put(Block(f'{bucket}-{i}', 'L15', bucket, 'x'), principal='L15')
            with self.assertRaisesRegex(Rejected, 'CAPACITY_EXHAUSTED'):
                self.small.put(Block(f'{bucket}-overflow', 'L15', bucket, 'x'), principal='L15')
        self.assertEqual(self.small.count(), 608)

    def test_restart_integrity(self):
        p = self.big.put(Block('persist', 'L15', 'cognitive', 'data'), principal='L15')
        filename = self.big.db.execute('PRAGMA database_list').fetchone()[2]
        self.big.close()
        self.big = Big49(filename)
        self.assertEqual(self.big.read(p, principal='L15')['content'], 'data')

    def test_tamper_detection(self):
        self.big.put(Block('tamper', 'L15', 'cognitive', 'data'), principal='L15')
        self.big.db.execute("UPDATE blocks SET payload=? WHERE record_id='tamper'", (b'{"bad":true}',))
        with self.assertRaisesRegex(Rejected, 'RECORD_HASH_MISMATCH'):
            self.big.verify()


if __name__ == '__main__':
    unittest.main(verbosity=2)
