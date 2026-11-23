# Build 44.1.0 — Pi 5 test package (corrected)

Runs Precision-105 (replay + offline), the HOT/WARM/COOL controller
tests, the Build 043 four-mode checks and the joined Higher-first proof
against the **installed 44.1.0 wheel**, and writes a fresh JSON trace and
raw log.

## Layout (one documented layout)

```
SUIT_AND_BRAINS_44/      the extracted 44.1.0 candidate tree (unchanged)
pi5_test/
  run_pi5_test.sh           the runner (this package)
  modes_43.py               four-mode driver — ships NEXT TO the runner
  proof_43.py               joined Higher-first proof — ships NEXT TO the runner
  README_PI5.md             this file
```

Everything the runner needs that is **not** in the tree lives next to
the runner and is resolved relative to the runner's own location
(`SCRIPT_DIR`). Nothing is resolved relative to the tree's parent
directory. That was the bug in the delivered package: the runner copied
`modes_43.py` from the directory *above* the extracted tree, which the
archive does not contain, so it crashed at the four-mode section after
the Precision-105 and CRAM-PU checks had passed.

## What changed vs the delivered 44.1.0 pi5_test

1. **Missing-file crash fixed.** `modes_43.py` and `proof_43.py` are
   included in this package and resolved relative to the runner.
2. **Startup checks.** Before installing or running anything, the runner
   checks for the wheel, the Precision-105 fixture, the Build 040
   reference trace, the three tree test files and both drivers. A
   missing file prints its exact path and exits with an error.
3. **Truthful report.** The JSON trace records `host_test` (PASS/FAIL
   for whatever machine ran it) and `pi_run` separately. `pi_run` is
   PASS/FAIL only when the hardware model identifies a Raspberry Pi 5
   (read from the device-tree model where available); on any other host
   it is NOT_RUN. CPU architecture alone cannot identify a Pi 5. The
   trace also records `hardware_model`, `cpu_arch`, Python version,
   build version and exit code. The old script wrote `pi_run: NOT_RUN`
   unconditionally, even on a real Pi.
4. **Early-stop handling.** If the driver stops early, the raw log is
   preserved and `failure_summary.json` names the stage that stopped
   (`stage.txt` is updated at every section).
5. **Joined Higher-first proof added (step 7).** `proof_43.py` runs
   against the installed wheel: Higher requests exact information, an
   unrelated reading leaves the case on HOLD, a matching reading after
   a genuine restart reaches Lower, and Higher consumes Lower's actual
   result. The runner checks the producer/consumer references in the
   proof trace (L31 output ref consumed by U16).
6. **Full outputs kept.** `modes_43.log`, `test_build43.log` and
   `proof_43.log` are written in the workdir. The trace records the
   five independent-mode tests individually (both modes, same run and
   after a genuine restart, plus the join guard).

## How to run

From the bundle root (the folder containing `SUIT_AND_BRAINS_44/`):

```
./pi5_test/run_pi5_test.sh
```

Options: `--tree PATH --workdir PATH --wheel PATH`. Defaults: tree
`SUIT_AND_BRAINS_44` next to the script, workdir `./pi5_run`, wheel
`<tree>/release/suit_and_brains-44.1.0-py3-none-any.whl`. Requires
python3 >= 3.11 with venv. No network access is needed (wheel installed
with `--no-index`).

## What it writes (in the workdir)

- `pi5_test_log.txt` — raw log of the whole run.
- `pi5_trace_4410.json` — fresh JSON trace: environment, Precision-105
  results, function calls, memory accesses, WARM staging, bundled
  tests, four modes, independent-mode tests, proof_43, failures, result.
- `response_105.json` — the Precision-105 replay response.
- `modes_43.log`, `test_build43.log`, `proof_43.log` — full outputs.
- `stage.txt` — the section reached (for the failure summary).
- `failure_summary.json` — only if the driver stopped early: the stage,
  exit code and last log lines.

## Reading the verdict

- `host_test: PASS` + `pi_run: NOT_RUN` — the run passed on this host,
  which is not a Pi 5. That is the expected result on a desktop.
- `pi_run: PASS` / `pi_run: FAIL` — the same script ran on a Raspberry
  Pi 5, and the verdict is what actually happened on that device.
- `result: FAIL` with a `failures` list — a check failed; the log and
  trace name it. `failure_summary.json` appears only on an early stop.
