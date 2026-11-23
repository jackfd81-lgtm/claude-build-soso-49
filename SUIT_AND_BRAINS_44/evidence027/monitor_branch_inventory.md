# What the uploads provide, what was used, what is still missing (reviewed 2026-11-20)

| Item | Where found | Hash / evidence | Used |
|---|---|---|---|
| Closet router reference v0.1 (`ph6_closet_router.py`, 2 identical uploads) | upload | sha256 `efa216da...`; 10/10 run here | YES (vendored, unmodified) |
| SoSo Suit monitor v3.2.3 | upload `e729f67e` | `2a5d95ee...`; equals the copy embedded in the unabridged text | YES (vendored) |
| SoSo Suit monitor v3.2.2 (v3.2.3's required base) | embedded in `PH6_UNABRIDGED_CONSOLIDATED_TEXT_2026-11-03.txt` (SOURCE 0051 and 0099, identical) | recomputed sha256 = recorded `EXTRACTED_SHA256` `150f1d12...` | YES (vendored) |
| v3.2.3 test file | upload `8f2db22c` | 35 of 39 pass against the recovered v3.2.2; the 4 failures are only `ph6_definition_registry.json` not found | run here |
| Closet router, later 1,509-line version (`closet_router_reference.py`) | `MUSE_CLAUDE_MISSING_SOURCE_RECOVERY` zip | sha256 `16fd1ad8...`; differs from the build report's 1,401-line `089eff0c...` | NOT used: it imports `bridge1_adapter`, not found |
| `ph6_realbridge.py`, `brain_bridge_suit_gate.py` | recovery zip, Build 024/025 trees | present in the Build 025 base | already part of the base |
| Suit 4.0 Consolidated (2 identical PDFs) | upload | description, freeze records, manifests; no `soso40` source block found in any upload | NOT used |
| `soso_suit_v323.py` adapter (2 identical) + `.pyc` | upload | imports `ph6_language.SemanticObject`; no `ph6_language.py` source found | NOT used |
| **Not found anywhere in the uploads:** `bridge1_adapter.py` / `ph6_bridge_adapter.py`, `ph6_language.py`, `ph6_definition_registry.json`, `soso40/*.py` source, the 1,401-line `closet_router.py` | searched all ZIP members and every embedded source block of the unabridged text | - | - |
