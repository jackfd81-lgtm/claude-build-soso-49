# Suit and Brains — Build 040 Architecture

```
USER / OPERATOR
       |
       v
MAIN AI INTERFACE  -->  build040_runtime/online_ai.py (Gemini / OpenAI / Replay / Offline; ADVISORY AUTH:0)
       |
       v
SOSO CLOSET        -->  build040_runtime/closet.py (Bounded Main AI request boundary)
       |
       v
SOSO               -->  build040_runtime/soso_continuity.py (Session continuity, provenance separation, routing)
       |
       v
49 BRAINS          -->  build040_runtime/brains_49_runtime.py (Lower49 / Higher49 dispatch + ExperienceEngine)
       |
       v
SOSO SUIT 4.x      -->  build040_runtime/suit_runtime.py (Grants, block scope, child attenuation, OD-05, revocation)
       |
       v
CRAM-PU MEMORY     -->  crampu/controller.py + crampu/build030_bridge.py (HOT LRU / WARM fsync / COOL SQLite + Outbox)
       |
       v
BIG49 / SMALL49    -->  realcog/ph6_brains_2208.py (2,208 logical blocks: 1,600 Big49 + 608 Small49)
```

- **Authority Separation:** `OnlineAIGateway`, `SoSoCloset`, `SoSoSuitRuntime`, `Brains49Runtime`, and `CRAMPUMemoryController` operate at `AUTH:0` (`ADVISORY_AUTH_0` / `COGNITIVE_ADVISORY`). Cognitive persistence never mints `ext:` PH6 Lane-1 evidence authority.
- **Dual-Node Boundary:** `deploy/orangepi/orangepi_grader.py` interacts with `build040_runtime/grader_endpoint.py` (`BoundedGraderServer`) via scoped `X-Grader-Token` HTTP requests without `admin_token` or direct SQLite access.
