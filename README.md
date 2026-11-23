# claude-build-soso-49

# Suit and Brains Build 44 — Raspberry Pi 5 candidate

Version `44.1.0`. Unzip and run from the bundle directory with Python 3.11+
and the Python venv module.

    bash pi5_test/run_pi5_test.sh --workdir ./pi5_run

The runner installs the bundled wheel offline and writes logs and
`pi5_trace_4410.json`. The result distinguishes a desktop test from a Pi 5
run. Use a fresh work directory for each qualification run.

## Native Precision-315 with a configured provider

Set OPENAI_API_KEY or GEMINI_API_KEY in the environment for the provider you
choose. This command sends the public fixture to that provider.

    ./pi5_run/venv/bin/python -m build040_runtime.precision_315 SUIT_AND_BRAINS_44/fixtures/PRECISION315_BLIND_V2.json --provider openai --output-dir ./live315
    python3 pi5_test/validate_315.py ./live315/response.json --fixture SUIT_AND_BRAINS_44/fixtures/PRECISION315_BLIND_V2.json

The output directory must be new. The constructor makes 15 segment requests,
validates all 315 lines, and saves each position via Suit into Big49. The trace
identifies provider responses and storage receipts. This command currently
covers generation and storage. Investigation routing is qualified separately
by the four-mode runner. Historical review and a live provider run remain
separate from the synthetic provider test.
