# Suit and Brains 42.0 RC1 on Build 040.1

Python 3.11 or newer. This package is a local engineering candidate against the
Revision 2 implementation contract. The supplied merged 040.1 source is the baseline.
All candidate paths and hashes are in `audit/BUILD42_SOURCE_CHANGELOG.json`.

## Run from the extracted source root

```bash
python3 -m build040_runtime.standalone demo
python3 -m build040_runtime.standalone run fixtures/build42_task.json
python3 run_build42_tests.py
python3 -m build040_runtime.learning_benchmark
```

The demo uses disposable state. The one-shot `run` command accepts the request
in `fixtures/build42_task.json` and an operator-created local `task.run` grant.
It reads JSON and prints one AUTH:0 result. Trusted Python callers can use
`local_runtime` and `SoSoCloset.run_task` with an explicit Suit grant and
optional read-scoped memory block. The single supported task operation is
`select_discriminator`. Session IDs should be unique per invocation. Existing
`SoSoCloset.invoke_agent` remains the legacy memory-write entry point.

## Install the candidate wheel

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --no-index release/suit_and_brains-42.0.0rc1-py3-none-any.whl
suit-brains demo
suit-brains run fixtures/build42_task.json
```

The wheel in `dist/` is the historical Build 030.1 artifact included in the
baseline. The 42.0 RC1 wheel is in `release/`. The bundled deployment configs
are historical examples. Local loopback grading ran against the existing
memory-write endpoint. A hardened 42.0 network grader, device installation,
live provider qualification, and physical durability testing remain open.

## Muse handoff

Start with `audit/BUILD42_EXECUTION_EVIDENCE.json` and
`audit/BUILD42_READOUT.pdf`. The original Revision 2 handoff in `audit/`
contains the full implementation contract, diagrams, registries and lineage.
Run the focused suite before editing. Use fresh or backed-up state for this
candidate. A legacy pending outbox row without immutable revision and bucket
data stays pending with an explicit integrity error. It requires reconciliation
against original evidence before recovery. Use a single trusted runtime process
per state directory during candidate testing.

The learning harness measures candidates remaining after one observation, on
synthetic partition tasks. Its transfer limitation is a concrete next experiment.
