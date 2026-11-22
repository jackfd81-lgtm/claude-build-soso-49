# MUSE Build 030

Build 030 is a standalone local runtime for structured evidence requests, investigation resumption, scoped memory, and advisory Suit delivery. It starts from the Build 29.2 standalone archive with SHA-256 `bb85c71ecf76dfe0d17be20babe5aca15ad853321a686766871a85f0e57a2641`. The original member manifest is retained under `evidence027/`.

## Run and verify

Use Linux/POSIX and Python 3.11 or newer with SQLite and fcntl. The runtime requires no third-party Python packages. Build and regression tooling have separate dependencies.

From the extracted source root:

```sh
sha256sum -c SHA256SUMS.txt
python3 -m qualification_030.verify --output ../muse030-checks
```

The output directory must be new. The verifier uses disposable state and writes a summary, raw evaluator artifacts, and step logs. It fails on unsuccessful commands, missing or invalid outputs, or a required assertion failure. Phase D, Suit recovery and Suit 4.2 are checked separately from the retained historical pytest tree.

Initialize real local state when needed:

```sh
umask 077
python3 -m muse024 --state "$PWD/muse-state" init > muse-local-credential.json
MUSE024_TOKEN="$(python3 -c 'import json; print(json.load(open("muse-local-credential.json"))["token"])')" \
  python3 -m muse024 --state "$PWD/muse-state" call --json '{"authority":0,"operation":"occupancy"}'
python3 -m muse024 --state "$PWD/muse-state" serve --port 8000
```

Keep the credential file private. The HTTP endpoint binds only to loopback. The installed console command is `muse030` with the same arguments. The optional shadow worker remains under `python3 -m suit61.shadow029 STATE enable|drain|disable`. Disabling shadow delivery leaves its records available. Suit's EVIDENCE_RECEIVED is an advisory event. The continuity test reconciles pending IOUs and leaves them pending until host action, even after the separate cognitive investigation reaches a terminal result.

## What Build 030 adopts

| Component | Integration |
|---|---|
| SoSo-49 and Pathway C | The stable 29.2 investigation, evidence, trace, memory and operator paths form the core. eval025 and eval026 retain their existing workloads and matched controls. |
| Big49, Small49, RealCog | Preserves both logical block pools and the procedural and observation stores. Adds authenticated verification of restart reads and negative cases to the release check. |
| Suit and IOU | Preserves the optional Suit 4.2 shadow bridge. A new continuity test interrupts the actual worker before journal submit and after journal commit, recovers from a fresh process, feeds observations from a separate fixture principal, confirms terminal state in a later process and reconciles advisory receipts, unique pending IOUs and the trace chain. |
| Legacy and blind checks | Retains Phase D and the 39-check monitor suite. Adopts the five-case terminal oracle and requires rejection of both wrong-input and post-terminal stale/conflicting controls. Early exact replay retains its historical decision. |
| ADV002 and ADV003 | Repair subprocess arguments and procedural recall unpacking. Replay both against the 030 tree. ADV002 reuses a fixed fixture-authored probe sequence. ADV003 scores archived responses and runs harness-authored hypotheses. |
| Packaging and Pi | Installable runtime wheel, source-tree verifier, POSIX CI, Raspberry Pi inspection, disposable pilot command and explicit-state loopback service example. |

## Current limits

Historical Candidate 001/002 manifests, original six-stage continuity runner, PATHWAY-001/002 adapters and parity files, GTML's TML dependency, and the legacy brain49_path_explain helper were unavailable. Historical source and tests remain in the source tree for recovery. Importable runtime packaging and the new qualification suite cover the supported Build 030 entry points. The integrated 029 archive's 602-test result does not carry over to this build. Exact legacy 4.0 receipt compatibility remains outside the adopted wire contract; on a host with jsonschema, use `python3 evidence027/suit_recovery/verify.py --compare-runtime` for the compatibility comparison.

The retained regional routing and synthetic fixtures establish bounded behavior. Autonomous method discovery, external evidence authenticity, broad natural-language reasoning, all-block memory saturation, physical power-loss durability, and production readiness remain open. Build 030 uses the existing advisory boundaries and does not grant Lane 1 authority.

## GitHub and Pi 5

Import the source root into a private engineering branch and run the included CI check. Review publication rights before changing repository visibility. For the Pi, see [deploy/pi5/README.md](deploy/pi5/README.md). The source release was qualified on x86_64. Pi hardware, service reboot, coordinated restore and rollback are separate acceptance steps.

## Packaged wheel

The ZIP includes a built wheel in `dist/`. To install it offline in an existing virtual environment, run `python -m pip install --no-index --no-deps dist/*.whl` from the extracted source directory. Source-tree qualification commands use the accompanying source and evidence files. Host verification records are under `release_evidence/`.
