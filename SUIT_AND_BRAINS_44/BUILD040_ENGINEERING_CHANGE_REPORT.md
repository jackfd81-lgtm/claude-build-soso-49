# Engineering Change Report — Suit and Brains Build 040

**Baseline:** `MUSE Build 030.1 (30.0.1)` CRAM-PU Qualified Candidate  
**Build Version:** `40.0.0` (`VERSION.txt`)  
**Python / OS:** Python `3.11.2`, `Linux-6.12.105+-x86_64-with-glibc2.36`, SQLite `3.40.1`

## 1. Subsystem Status Matrix
| Subsystem | Existed in Build 030.1 | Changed in Build 040 | Executed & Result |
|---|---|---|---|
| **CRAM-PU HOT/WARM/COOL + Outbox** | `crampu/controller.py`, `crampu/build030_bridge.py` | Preserved intact (bounded HOT, outbox `PENDING->APPLIED->ACKNOWLEDGED`) | `VERIFIED_EXECUTED` (`33/33` PASS) |
| **49 Brains Registry & Ops** | `98` slots (`16` `functions025`, `4` `cross_specialty`, `1` `U09`, `77` region-routed) | Inventoried in `BUILD040_49BRAINS_INVENTORY.json`; wired to `Brains49Runtime` | `VERIFIED_EXECUTED` |
| **SoSo Continuity** | Scattered `trace` / `observation_memory` | Added `build040_runtime/soso_continuity.py` (sessions, provenance kinds, routing) | `VERIFIED_EXECUTED` |
| **SoSo Suit 4.x** | `brain_bridge_suit_gate.py` (OD-05 gate) | Added `build040_runtime/suit_runtime.py` (grants, child attenuation, revocation, budgets) | `VERIFIED_EXECUTED` |
| **SoSo Closet** | Not present | Added `build040_runtime/closet.py` (Main AI bounded interface) | `VERIFIED_EXECUTED` |
| **Experience & Ablation** | `realcog/memory025.py` (method pointers) | Added `ExperienceEngine` + 4-arm ablation (`with`, `without`, `irrelevant`, `corrupted`) | `VERIFIED_EXECUTED` |
| **Online AI Gateway** | Not present | Added `build040_runtime/online_ai.py` (Gemini/OpenAI/Replay/Offline, `AUTH:0`) | `VERIFIED_EXECUTED` (offline/replay/failure; live API keys not tested) |
| **Pi 5 & Orange Pi Dual-Node** | `deploy/pi5/*`, local `orangepi_grader.py` | Added `BoundedGraderServer` + remote HTTP mode in `orangepi_grader.py` | `VERIFIED_EXECUTED` (local & loopback HTTP; physical boards unverified) |

## 2. Known Limitations & Unverified Scope
- **Physical Hardware:** Raspberry Pi 5 and Orange Pi Zero 2W physical deployment, SD/NVMe power-loss, and thermal contention remain `IMPLEMENTED_UNVERIFIED` until run on target hardware.
- **Live Online AI:** `GoogleGeminiProvider` and `OpenAIProvider` require `GEMINI_API_KEY` / `OPENAI_API_KEY` and outbound network access (disabled in this sandbox).
- **Broader `pytest` Suite:** `pytest` is not installed in this sandbox (`unittest` + `qualification_030.verify` were executed).
