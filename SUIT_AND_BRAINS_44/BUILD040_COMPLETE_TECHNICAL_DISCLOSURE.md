# Suit and Brains Build 040 — Complete Technical Disclosure

**Version:** `40.0.0` | **Environment:** Python `3.11.2`, `Linux-6.12.105+-x86_64-with-glibc2.36`, SQLite `3.40.1`  
**Machine-Readable Companions:**
- `BUILD040_FILE_INVENTORY_SHA256.json` (`319` files with SHA-256 hashes)
- `BUILD040_98_SLOT_DISCLOSURE.json` (all `98` registered slots `L01–L49`, `U01–U49`)
- `BUILD040_P105_FUNCTION_TRACE.json` (exact `p105-official-run` `muse024.call` per-function trace)
- `BUILD040_QUALIFICATION_EVIDENCE.json`

## 1. Cognitive Architecture Summary (`VERIFIED_SOURCE` / `VERIFIED_EXECUTED`)
- **98 Registered Slots (`contract/brains_wiring.py`):** `49` Lower (`L01–L49`) + `49` Higher (`U01–U49`).
  - **21 Implemented Executable Functions:** `16` in `contract/functions025.py` (`L03, L04, L05, L16, L17, L18, L22, L31, U06, U07, U11–U16`), `4` in `contract/cross_specialty.py` (`L33, L34, U32, U33`), and `1` standalone in `contract/disposition_engine.py` (`U09`: `evaluate_motor_branches`).
  - **77 Default-Routed Slots:** Return `status="OK"` region passthroughs via `BrainsWiringRegistry._default_region_handler`.
- **`p105-official-run` Execution (`VERIFIED_EXECUTED`):** `13` consecutive anchor chronology checks (`31.994 ms` total) executed `L03, L04, L16, L22, L17, L18, L31, U16` (`13` times each = `104` trace steps) plus `U09` (`1` dispatch, `3` disposition events). **None** of the 9 functions generated historical prose; they verified anchor chronology and structural disposition.

## 2. Memory & CRAM-PU Architecture (`VERIFIED_SOURCE` / `VERIFIED_EXECUTED`)
- **2,208 Logical Positions (`realcog/ph6_brains_2208.py`):**
  - `Big49` (`0–1599`, `1,600` blocks): `cognitive` (`1280 × 512` chars), `association` (`320 × 512` chars).
  - `Small49` (`1600–2207`, `608` blocks): `history(256,512)`, `active(64,512)`, `workspace_micro(24,64)`, `workspace_medium(40,250)`, `workspace_large(64,500)`, `bridge_macro(10,1000)`, `bridge_projection(40,500)`, `bridge_path(20,250)`, `bridge_typed(60,128)`, `bridge_pointer(30,64)`.
- **CRAM-PU HOT / WARM / COOL (`crampu/controller.py`, `crampu/build030_bridge.py`):**
  - `HOT`: Bounded `OrderedDict` LRU (`max_hot_entries=256`, `max_hot_bytes=1048576`).
  - `WARM`: Atomic `durable_write` (`O_EXCL` + `fsync` + `os.replace`) JSON snapshots.
  - `COOL`: SQLite (`WAL`, `synchronous=FULL`, `foreign_keys=ON`) storing `owners`, `blocks`, `transitions`, and `outbox` (`PENDING → APPLIED → ACKNOWLEDGED`), cross-checked against `transitions.record_sha256` on `load_from_cool()`.
- **Experience & Cognitive Use (`build040_runtime/brains_49_runtime.py`):** `ExperienceEngine` stores task outcomes in `Big49`/`Small49` via Suit and retrieves them on later tasks (ablation-verified: `1` probe `with_memory` vs. `3` probes `without_memory`, `irrelevant_memory`, or `corrupted_memory`).

## 3. Historical Construction Capability & Gap Analysis
| Capability | Status in Build 040 |
|---|---|
| Retrieve stored anchor/experience facts | `VERIFIED_EXECUTED` (`SoSoSuitRuntime` + `Big49`/`Small49`) |
| Reason about anchor chronology | `VERIFIED_EXECUTED` (`L22` / `start025` Pathway B) |
| Validate structure, word counts, & anchors | `VERIFIED_EXECUTED` (`validate.py`, `validate_public.py`, `precision_105_grader.py`) |
| Learn from prior task outcomes | `VERIFIED_EXECUTED` (`ExperienceEngine` 4-arm ablation) |
| Construct original historical prose (`HIST-GEO-CULTURE-105-v2`) | `BLOCKED` offline (no local historical LLM; requires live `OnlineAIGateway` or external model) |

## 4. `OnlineAIGateway` Technical Report (`build040_runtime/online_ai.py`)
- **Providers:** `GoogleGeminiProvider` (`GEMINI_API_KEY` env var, default `gemini-2.5-flash`), `OpenAIProvider` (`OPENAI_API_KEY` env var, default `gpt-4o-mini`), `ReplayProvider` (SHA-256 prompt-keyed deterministic cache), and `OfflineAdvisoryProvider`.
- **Governance:** All responses are tagged `advisory_only=True, authority=0`. Retries (`max_retries=2`) with linear backoff fall back cleanly to `OfflineAdvisoryProvider`, logging `request_digest` and `fallback` status in `provenance_log`.

## 5. Blocker-Resolution Plan for `HIST-GEO-CULTURE-105-v2`
1. **Run in a networked environment** with `GEMINI_API_KEY` or `OPENAI_API_KEY` set (never committed).
2. **Dynamic Per-Segment Generation:** Update `Precision105Constructor` to call `OnlineAIGateway.request_advisory()` per inter-anchor segment (and positions `100..105`), enforcing `5–10` words per open position and exact preservation of all `15` locked anchors without touching any private answer bank.
3. **Cognitive Verification & Provenance:** Run `L22` chronology checks across all `15` anchor eras, store anchor/segment provenance in `Big49`/`Small49` + `SoSoContinuity`, emit `generation_provenance.json` + `memory_operations.json`, freeze `response.json` with SHA-256, and validate via `validate_public.py`.
