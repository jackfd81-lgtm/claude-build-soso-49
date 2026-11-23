# Engineering Change Report — MUSE Build 030.1 CRAM-PU Qualified Candidate

**Baseline:** `MUSE_BUILD030_1` (`30.0.1`) + `MUSE_BUILD030_1_CRAMPU_REENGINEERED.zip` (`179e22f3c8c5da103fa947a85498026cc8975c2653890ee9aff33cbb4211013e`)  
**Python / OS:** Python `3.11.2`, `Linux-6.12.105+-x86_64-with-glibc2.36`, SQLite `3.40.1`

## 1. Key Repairs & Integrations Implemented
1. **CRAM-PU Controller (`crampu/controller.py`):**
   - Bounded HOT tier via `OrderedDict` LRU (`max_hot_entries=256`, `max_hot_bytes=1048576`) so volatile HOT memory never grows unbounded.
   - `load_from_cool()` cross-checks `blocks.record` SHA-256 and `owners.agent_id` against `transitions.record_sha256` and `transitions.agent_id` to detect SQLite payload corruption and ownership metadata tampering.
   - `commit_to_cool()` supports idempotent `txid` replay (`replayed=True`) and atomic transactional `outbox` insertion (`PENDING`) inside the same `BEGIN IMMEDIATE ... COMMIT`.
2. **COOL-to-Brain Transactional Outbox (`crampu/build030_bridge.py`):**
   - `Merged49BrainsCramAdapter.write_and_sync()` commits atomically to COOL + `outbox(PENDING)` and applies to `Big49`/`Small49` (`PENDING` -> `APPLIED` -> `ACKNOWLEDGED`).
   - `sync_pending_outbox()` recovers interrupted `PENDING` and `APPLIED` mirror entries idempotently on restart without duplicate writes.
   - `recover_missing_mirrors()` rebuilds missing `Big49`/`Small49` mirrors from authoritative COOL state.
3. **SoSo Suit OD-05 Gate Hardening (`crampu/build030_bridge.py`):**
   - `build030_suit_capability_check()` enforces `L01`–`L49` principals, `memory.read`/`memory.write`, `0..2207` block bounds, `authority == 0`, `promotes_to != "CAUSES"`, `relation != "PROMOTES_TO_CAUSES"`, and `validate_causal()` / `IRValidationError` fail-closed handling.
4. **Raspberry Pi 5 & Orange Pi Zero 2W Kits (`deploy/pi5/`, `deploy/orangepi/`):**
   - Added `cram-pu-setup.sh`, `muse030-cram.service`, `CRAM_PU_DEPLOYMENT.md`, `orangepi_grader.py`, and `deploy/orangepi/README.md`.

## 2. Acceptance Classification
| Requirement | Classification | Evidence |
|---|---|---|
| Standalone CRAM-PU controller & schemas | `VERIFIED_EXECUTED` | `10/10` PASS (`tests/test_crampu_controller.py`) |
| Merged Suit OD-05 + Big49/Small49 + Outbox | `VERIFIED_EXECUTED` | `3/3` PASS (`tests/test_merged_crampu_build030_1.py`) |
| All 20 failure-injection & E2E pathway tests | `VERIFIED_EXECUTED` | `20/20` PASS (`tests/test_failure_injection_20.py`) |
| Existing Build 030.1 cognitive qualification | `VERIFIED_EXECUTED` | `22/22` PASS (`qualification_030.verify`) |
| Orange Pi independent grader (local simulation) | `VERIFIED_EXECUTED` | `PASS` (`0.47 ms` p50, `1.17 ms` max, `21,736 KB` RSS) |
| Physical Pi 5 / Orange Pi Zero 2W hardware & power-loss | `IMPLEMENTED_UNVERIFIED` | Deployment scripts & systemd unit included; requires target hardware |
| Build 030.1 `pytest` 94-case suite | `IMPLEMENTED_UNVERIFIED` | `pytest` package not installed in sandbox (`unittest` + `verify.py` executed) |
