"""37 individual unittest methods: 21 bounded Cross-Specialty V4.1 cases + 16 adversarial checks."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract.context_envelope import ContextEnvelope
from contract.cross_specialty import run_consultation

STORE = {
    "med:trop_high": {"discipline": "medical", "key": "troponin_i", "value": "0.85 ng/mL"},
    "med:ecg_st": {"discipline": "medical", "key": "ecg", "value": "ST_depression"},
    "med:trop_norm": {"discipline": "medical", "key": "troponin_i", "value": "normal"},
    "med:ecg_norm": {"discipline": "medical", "key": "ecg", "value": "normal"},
    "med:tox_neg": {"discipline": "medical", "key": "tox_screen", "value": "negative"},
    "med:tox_pos": {"discipline": "medical", "key": "tox_screen", "value": "positive_opioid"},
    "med:missing_note": {"discipline": "medical", "key": "op_note", "value": "absent", "missing": True},
    "med:disputed_lab": {"discipline": "medical", "key": "lactate", "value": "4.2", "disputed": True},
    "med:uncertain_obs": {"discipline": "medical", "key": "fever", "value": "38.5C", "uncertain": True},
    "leg:bleed": {"discipline": "legal", "key": "bleed_event", "value": "gi_hemorrhage"},
    "leg:consent_ok": {"discipline": "legal", "key": "signed_consent", "value": "present"},
    "leg:consent_miss": {"discipline": "legal", "key": "signed_consent", "value": "absent", "missing": True},
}

BOUNDED_21 = [
    ("B01", "Legal", "Medical", [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}], "COHERENT"),
    ("B02", "Legal", "Medical", [{"source_id": "med:trop_norm", "verification_status": "VERIFIED", "key": "troponin_i", "value": "normal"}, {"source_id": "med:ecg_norm", "verification_status": "VERIFIED", "key": "ecg", "value": "normal"}], "COHERENT"),
    ("B03", "Medical", "Legal", [{"source_id": "leg:bleed", "verification_status": "VERIFIED", "key": "bleed_event", "value": "gi_hemorrhage"}], "COHERENT"),
    ("B04", "Medical", "Legal", [{"source_id": "leg:bleed", "verification_status": "VERIFIED", "key": "bleed_event", "value": "gi_hemorrhage"}, {"source_id": "leg:consent_ok", "verification_status": "VERIFIED", "key": "signed_consent", "value": "present"}], "COHERENT"),
    ("B05", "Legal", "Medical", [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}, {"source_id": "med:ecg_st", "verification_status": "VERIFIED", "key": "ecg", "value": "ST_depression"}], "COHERENT"),
    ("B06", "Legal", "Medical", [{"source_id": "", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}], "UNSUPPORTED_OR_UNGROUNDED"),
    ("B07", "Legal", "Medical", [{"source_id": "med:trop_high", "key": "troponin_i", "value": "0.85 ng/mL"}], "UNSUPPORTED_OR_UNGROUNDED"),
    ("B08", "Legal", "Medical", [{"source_id": "med:trop_high", "verification_status": "UNVERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}], "UNSUPPORTED_OR_UNGROUNDED"),
    ("B09", "Legal", "Medical", [{"source_id": "med:nonexistent", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}], "UNSUPPORTED_OR_UNGROUNDED"),
    ("B10", "Legal", "Medical", [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "WRONG_VALUE"}], "UNSUPPORTED_OR_UNGROUNDED"),
    ("B11", "Legal", "Medical", [{"source_id": "med:missing_note", "verification_status": "VERIFIED", "key": "op_note", "value": "absent"}], "UNSUPPORTED_OR_UNGROUNDED"),
    ("B12", "Legal", "Medical", [{"source_id": "med:disputed_lab", "verification_status": "VERIFIED", "key": "lactate", "value": "4.2"}], "UNSUPPORTED_OR_UNGROUNDED"),
    ("B13", "Legal", "Medical", [{"source_id": "med:uncertain_obs", "verification_status": "VERIFIED", "key": "fever", "value": "38.5C"}], "UNSUPPORTED_OR_UNGROUNDED"),
    ("B14", "Medical", "Legal", [], "UNSUPPORTED_OR_UNGROUNDED"),
    ("B15", "Legal", "Medical", [{"source_id": "med:tox_neg", "verification_status": "VERIFIED", "key": "tox_screen", "value": "negative"}, {"source_id": "med:tox_pos", "verification_status": "VERIFIED", "key": "tox_screen", "value": "positive_opioid"}], "CONFLICT_PRESERVED"),
    ("B16", "Legal", "Medical", [{"source_id": "med:tox_neg", "verification_status": "VERIFIED", "key": "tox_screen", "value": "negative"}, {"source_id": "med:tox_pos", "verification_status": "VERIFIED", "key": "tox_screen", "value": "positive_opioid"}, {"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}], "CONFLICT_PRESERVED"),
    ("B17", "Legal", "Medical", [{"source_id": "med:missing_note", "verification_status": "VERIFIED", "key": "op_note", "value": "absent"}, {"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}], "COHERENT"),
    ("B18", "Medical", "Legal", [{"source_id": "leg:consent_miss", "verification_status": "VERIFIED", "key": "signed_consent", "value": "absent"}], "UNSUPPORTED_OR_UNGROUNDED"),
    ("B19", "Medical", "Legal", [{"source_id": "leg:bleed", "verification_status": "VERIFIED", "key": "bleed_event", "value": "gi_hemorrhage"}, {"source_id": "leg:consent_miss", "verification_status": "VERIFIED", "key": "signed_consent", "value": "absent"}], "COHERENT"),
    ("B20", "Legal", "Legal", [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}], "CIRCULAR_ROUTE_REJECTED"),
    ("B21", "Legal", "Astrology", [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}], "COUNTERPART_UNAVAILABLE"),
]


class CrossSpecialtyV4Tests(unittest.TestCase):
    def _env(self, cid, orig, targ, refs, *, authority=0):
        return ContextEnvelope(
            investigation_id=cid,
            originating_question=f"{orig}->{targ} question for {cid}",
            originating_specialty=orig,
            current_specialty=orig,
            consultation_purpose=f"consult_{targ.lower()}",
            required_counterpart=(f"{orig}->{targ}",),
            evidence_store=STORE,
            evidence_refs=refs,
            authority=authority,
        )

    def test_n01_empty_source_id(self):
        self.assertEqual(run_consultation(self._env("N01", "Legal", "Medical", [{"source_id": "", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}]), "Medical").coherence_status, "UNSUPPORTED_OR_UNGROUNDED")

    def test_n02_missing_verification_status(self):
        self.assertEqual(run_consultation(self._env("N02", "Legal", "Medical", [{"source_id": "med:trop_high", "key": "troponin_i", "value": "0.85 ng/mL"}]), "Medical").coherence_status, "UNSUPPORTED_OR_UNGROUNDED")

    def test_n03_nonexistent_source(self):
        self.assertEqual(run_consultation(self._env("N03", "Legal", "Medical", [{"source_id": "med:ghost", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}]), "Medical").coherence_status, "UNSUPPORTED_OR_UNGROUNDED")

    def test_n04_nonsupporting_source(self):
        self.assertEqual(run_consultation(self._env("N04", "Legal", "Medical", [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "normal"}]), "Medical").coherence_status, "UNSUPPORTED_OR_UNGROUNDED")

    def test_n05_empty_citations(self):
        valid_med = [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}]
        self.assertEqual(run_consultation(self._env("N05", "Legal", "Medical", valid_med), "Medical", override_citations=[]).coherence_status, "UNSUPPORTED_OR_UNGROUNDED")

    def test_n06_fabricated_citation(self):
        valid_med = [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}]
        self.assertEqual(run_consultation(self._env("N06", "Legal", "Medical", valid_med), "Medical", override_citations=["med:fabricated"]).coherence_status, "UNSUPPORTED_OR_UNGROUNDED")

    def test_n07_wrong_discipline_citation(self):
        wrong_disc = [{"source_id": "leg:bleed", "verification_status": "VERIFIED", "key": "bleed_event", "value": "gi_hemorrhage"}]
        self.assertEqual(run_consultation(self._env("N07", "Legal", "Medical", wrong_disc), "Medical", override_citations=["leg:bleed"]).coherence_status, "UNSUPPORTED_OR_UNGROUNDED")

    def test_n08_l2m_perspective_drift(self):
        valid_med = [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}]
        self.assertEqual(run_consultation(self._env("N08", "Legal", "Medical", valid_med), "Medical", force_drift=True).coherence_status, "PERSPECTIVE_DRIFT")

    def test_n09_m2l_perspective_drift(self):
        valid_leg = [{"source_id": "leg:bleed", "verification_status": "VERIFIED", "key": "bleed_event", "value": "gi_hemorrhage"}]
        self.assertEqual(run_consultation(self._env("N09", "Medical", "Legal", valid_leg), "Legal", force_drift=True).coherence_status, "PERSPECTIVE_DRIFT")

    def test_n10_conflicting_evidence_preserved(self):
        conf = [{"source_id": "med:tox_neg", "verification_status": "VERIFIED", "key": "tox_screen", "value": "negative"}, {"source_id": "med:tox_pos", "verification_status": "VERIFIED", "key": "tox_screen", "value": "positive_opioid"}]
        self.assertEqual(run_consultation(self._env("N10", "Legal", "Medical", conf), "Medical").coherence_status, "CONFLICT_PRESERVED")

    def test_n11_conflict_provenance_lost(self):
        conf = [{"source_id": "med:tox_neg", "verification_status": "VERIFIED", "key": "tox_screen", "value": "negative"}, {"source_id": "med:tox_pos", "verification_status": "VERIFIED", "key": "tox_screen", "value": "positive_opioid"}]
        self.assertEqual(run_consultation(self._env("N11", "Legal", "Medical", conf), "Medical", drop_conflict_provenance=True).coherence_status, "CONFLICT_PROVENANCE_LOST")

    def test_n11b_tampered_conflict_provenance_rejected(self):
        conf = [{"source_id": "med:tox_neg", "verification_status": "VERIFIED", "key": "tox_screen", "value": "negative"}, {"source_id": "med:tox_pos", "verification_status": "VERIFIED", "key": "tox_screen", "value": "positive_opioid"}]
        self.assertEqual(run_consultation(self._env("N11B", "Legal", "Medical", conf), "Medical", tamper_conflict_provenance=True).coherence_status, "CONFLICT_PROVENANCE_LOST")

    def test_n12_counterpart_unavailable(self):
        valid_med = [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}]
        self.assertEqual(run_consultation(self._env("N12", "Legal", "Engineering", valid_med), "Engineering").coherence_status, "COUNTERPART_UNAVAILABLE")

    def test_n13_intermediary_bypass_rejected(self):
        valid_med = [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}]
        self.assertEqual(run_consultation(self._env("N13", "Legal", "Medical", valid_med), "Medical", use_association=False, bypass_intermediaries=True).coherence_status, "INTERMEDIARY_BYPASS_REJECTED")

    def test_n14_circular_routing_rejected(self):
        valid_med = [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}]
        self.assertEqual(run_consultation(self._env("N14", "Legal", "Legal", valid_med), "Legal").coherence_status, "CIRCULAR_ROUTE_REJECTED")

    def test_n15_forged_authority_rejected(self):
        valid_med = [{"source_id": "med:trop_high", "verification_status": "VERIFIED", "key": "troponin_i", "value": "0.85 ng/mL"}]
        with self.assertRaises(PermissionError):
            self._env("N15", "Legal", "Medical", valid_med, authority=1)


for _cid, _orig, _targ, _refs, _exp in BOUNDED_21:
    def _make_test(cid=_cid, orig=_orig, targ=_targ, refs=_refs, exp=_exp):
        def _t(self):
            self.assertEqual(run_consultation(self._env(cid, orig, targ, refs), targ).coherence_status, exp)
        return _t
    setattr(CrossSpecialtyV4Tests, f"test_bounded_{_cid.lower()}", _make_test())


if __name__ == "__main__":
    unittest.main(verbosity=2)
