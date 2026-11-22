import json, os, tempfile, unittest
from ph6_brains_2208 import Small49, Block
from iou_reconstruct import *
P = "L30"; n = [0]
def put(s, op, rid, st, **kw):
    n[0] += 1; d = {"operation": op, "record_id": rid, "status": st}; d.update(kw)
    s.put(Block("closet-iou-%d" % n[0], P, "active", json.dumps(d, sort_keys=True)), principal=P)
def chk(s, rid, st, ok=True): put(s, "IOU_INTEGRITY", rid, st, expected_digest="sha256:a", actual_digest="sha256:a" if ok else "sha256:b")
class T(unittest.TestCase):
    def setUp(self): self.p = os.path.join(tempfile.mkdtemp(), "s.db"); self.s = Small49(self.p)
    def test_pending_verified_rejected_survive_restart(self):
        put(self.s, "IOU", "A", "PENDING"); put(self.s, "IOU", "B", "PENDING"); put(self.s, "IOU", "C", "PENDING")
        chk(self.s, "B", "VERIFIED"); chk(self.s, "C", "REJECTED", ok=False)
        self.s.close(); self.s = Small49(self.p); r = reconstruct(self.s, "TERMINAL")
        self.assertEqual({k: v[0] for k, v in r["ious"].items()}, {"A": "PENDING", "B": "VERIFIED", "C": "REJECTED"})
        self.assertTrue(r["complete"]); self.assertTrue(may_use_as_accepted(r, "B")); self.assertFalse(may_use_as_accepted(r, "A")); self.assertFalse(may_use_as_accepted(r, "C"))
    def test_policy_is_required(self):
        with self.assertRaises(ValueError): reconstruct(self.s, None)
    def test_repeated_check_policy_difference(self):
        put(self.s, "IOU", "A", "PENDING"); chk(self.s, "A", "REJECTED", ok=False); chk(self.s, "A", "VERIFIED")
        self.assertEqual(reconstruct(self.s, "TERMINAL")["ious"]["A"][0], INDETERMINATE)
        self.assertEqual(reconstruct(self.s, "REPLACE")["ious"]["A"][0], VERIFIED)
    def test_orphan_integrity_is_indeterminate_not_created(self):
        chk(self.s, "ghost", "VERIFIED"); r = reconstruct(self.s, "REPLACE")
        self.assertEqual(r["ious"]["ghost"][0], INDETERMINATE); self.assertFalse(may_use_as_accepted(r, "ghost"))
    def test_duplicate_creation_indeterminate(self):
        put(self.s, "IOU", "A", "PENDING"); put(self.s, "IOU", "A", "PENDING"); chk(self.s, "A", "VERIFIED")
        self.assertEqual(reconstruct(self.s, "REPLACE")["ious"]["A"][0], INDETERMINATE)
    def test_status_digest_disagreement_is_malformed(self):
        put(self.s, "IOU", "A", "PENDING"); put(self.s, "IOU_INTEGRITY", "A", "VERIFIED", expected_digest="x", actual_digest="y")
        r = reconstruct(self.s, "REPLACE"); self.assertEqual(len(r["malformed"]), 1); self.assertFalse(may_use_as_accepted(r, "A"))
    def test_truncated_record_makes_reconstruction_incomplete_and_blocks_all_accept(self):
        put(self.s, "IOU", "A", "PENDING"); chk(self.s, "A", "VERIFIED")
        self.s.put(Block("closet-iou-trunc", P, "active", json.dumps({"operation": "IOU", "truncated": True, "keys": ["record_id"]})), principal=P)
        r = reconstruct(self.s, "REPLACE"); self.assertFalse(r["complete"]); self.assertFalse(may_use_as_accepted(r, "A"))
    def test_order_is_event_sequence_not_record_id(self):
        put(self.s, "IOU", "Z", "PENDING"); chk(self.s, "Z", "REJECTED", ok=False); chk(self.s, "Z", "VERIFIED")
        self.assertEqual(reconstruct(self.s, "REPLACE")["ious"]["Z"][0], VERIFIED)
    def test_tamper_detected(self):
        put(self.s, "IOU", "A", "PENDING"); self.s.db.execute("UPDATE blocks SET payload=replace(payload,'PENDING','VERIFIED')")
        from ph6_brains_2208 import Rejected
        with self.assertRaises(Rejected): reconstruct(self.s, "REPLACE")
if __name__ == "__main__": unittest.main()
