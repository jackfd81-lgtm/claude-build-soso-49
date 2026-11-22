# Build 030.1 review and verification

Source baseline: attached Build 030 ZIP, SHA-256
`61a905a99fa426f77c016ebe7c4aab878c80332b5438c0a8b680a3cf94ab3961`.
The source archive's manifest verified before execution. Its 22 built-in
checks passed. The 91 supported regression cases selected for direct comparison
passed. A separate plain pytest run stopped on 33 collection errors. Details
are in release_evidence/build030_1/legacy.log.

## Fixes and regression coverage

| Area | Build 030.1 change |
|---|---|
| HTTP readiness | The service checks that the state database can be read and initialized tables are present. Health returns 503 when state is unavailable. The probe opens the database read-only and does not fetch secrets. |
| Verifier evidence | Child timeouts and launch failures preserve stdout, stderr, and failure metadata. Failed assertions and commands are named in the summary. Qualification output is private by default and kept outside the extracted release. |
| Pi inspection | A fresh output path prevents report overwrite and keeps the release tree clean. PYTHON_BIN is shared with verify.sh. Missing or incompatible Python fails preflight, as does inability to load SQLite or fcntl. Diagnostic host probes are best-effort. |
| Regression entry point | Pytest and CI now select the 94 supported cases. Historical files are retained, and those tests still require their historical dependencies. |
| Release package | Version 30.0.1, rebuilt offline-installable wheel, regenerated source manifest. The existing muse030 console command remains the entry point. |

Host qualification used x86_64 Python 3.12. The suite passed 94 tests with
one dependency deprecation warning. The standalone verifier passed all 22
checks. The installed 30.0.1 wheel ran its CLI and the shadow investigation
outside the source directory in a fresh virtual environment. Host preflight
passed, and the missing-interpreter and invalid-arguments cases failed as
expected. The verifier refused an output path inside the release.

The legacy suite's missing modules and fixtures remain a recovery task.
Build 030.1 does not inherit Build 029's regression count. Installed wheel
qualification covers the runtime. The full source verifier stays in the ZIP
and uses accompanying fixtures and recovered evidence.

Pi 5 deployment, physical reboot and power-loss behavior, and coordinated
restore and rollback on the target are unverified. The release is local and
advisory. No repository merge or Pi deployment is part of this maintenance
release.
