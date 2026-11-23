# Build 040.1 — Independent Learning Qualification Extension

## Provenance
Derived from the user-uploaded `SUIT_AND_BRAINS_BUILD040_GEMINI 5.zip`, SHA-256 `1760a51c699eae410c071446174d0138bc3efe5b3303f592ab711418f8a04636`.
This is **not** the unavailable Gemini-reported `7e98a780...` release and does not contain its claimed `muse_initialize()` restart-recovery fix.

## Change scope
Adds `tests/test_learning_qualification_0401.py`: 30 deterministically varied, renamed three-hypothesis tasks, 30 memory-ablated controls, and six negative-transfer cases. Preserves all upstream source code without modification. Does not add live Gemini, hardware qualification, or new learning algorithms.

## Run
```bash
python3 -m unittest discover -s tests -p 'test_learning_qualification_0401.py' -v
python3 -m unittest discover -s tests -p 'test_learning_loop_u11_u15.py' -v
```

## Limitations
The training history explicitly supplies the preferred `[1,2]` partition. Thus the test measures deterministic strategy reuse and rejection, **not** autonomous discovery, causal learning, or broad generalization. No online API calls are made.
