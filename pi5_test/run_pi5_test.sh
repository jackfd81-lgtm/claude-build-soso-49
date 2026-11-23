#!/usr/bin/env bash
# ============================================================================
# Build 44.1.0 — Raspberry Pi 5 test (Precision-105 + wiring + memory + WARM
# + four modes + joined Higher-first proof)
#
# One runnable script. It:
#   1. checks, at startup, that the wheel, fixtures, every required test
#      file and both mode drivers are present; a missing file prints its
#      exact path and exits before installing or running anything,
#   2. creates a fresh virtual environment,
#   3. installs the BUNDLED wheel from the verified candidate ZIP (no network),
#   4. runs the bundled Precision-105 replay + offline tests and the bundled
#      HOT/WARM/COOL controller tests against the INSTALLED WHEEL (the test
#      files are run by path from the workdir, so imports resolve to the
#      wheel, while fixtures resolve via the test files' own location),
#   5. runs the Precision-105 constructor against the wheel with a replay
#      provider and captures the full function trace, memory map and a
#      staged bridge write,
#   6. runs the Build 043 four-mode checks (modes_43.py + tests/test_build43.py)
#      by path from the workdir, so they import the installed wheel,
#   7. runs the joined Higher-first proof (proof_43.py) by path from the
#      workdir, so it imports the installed wheel, and checks the
#      producer/consumer references in its trace,
#   8. writes pi5_trace_4410.json (fresh JSON trace) and pi5_test_log.txt
#      (raw log) into the workdir. If the driver stops early, the raw log is
#      preserved and failure_summary.json names the stage that stopped.
#
# Layout (one documented layout). The candidate tree is the extracted
# archive (SUIT_AND_BRAINS_44). Everything the runner needs that is NOT
# in the tree — modes_43.py and proof_43.py — lives NEXT TO this script in
# pi5_test/, and the runner resolves them relative to its own location.
# Nothing is resolved relative to the tree's parent directory.
#
# Usage:
#   ./run_pi5_test.sh [--tree PATH] [--workdir PATH] [--wheel PATH]
#
#   --tree     extracted candidate tree (default: SUIT_AND_BRAINS_44
#              next to this script, else ./SUIT_AND_BRAINS_44)
#   --workdir  disposable state + outputs (default: ./pi5_run)
#   --wheel    wheel to install (default: <tree>/release/suit_and_brains-44.1.0-py3-none-any.whl)
#
# Requirements: python3 >= 3.11 with the venv module (Raspberry Pi OS
# Bookworm ships 3.11). No network access is needed.
#
# Reporting truthfully: host_test is PASS/FAIL for whatever machine ran
# this script. pi_run is PASS/FAIL only when the hardware model identifies
# a Raspberry Pi 5 (read from the device-tree model where available); on
# any other host pi_run is NOT_RUN and the trace says so. CPU architecture
# alone cannot identify a Pi 5.
# ============================================================================
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TREE=""
WORKDIR=""
WHEEL=""

while [ $# -gt 0 ]; do
  case "$1" in
    --tree)    TREE="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    --wheel)   WHEEL="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; echo "usage: $0 [--tree PATH] [--workdir PATH] [--wheel PATH]" >&2; exit 2 ;;
  esac
done

if [ -z "$TREE" ]; then
  if [ -d "$SCRIPT_DIR/../SUIT_AND_BRAINS_44" ]; then
    TREE="$SCRIPT_DIR/../SUIT_AND_BRAINS_44"
  elif [ -d "$PWD/SUIT_AND_BRAINS_44" ]; then
    TREE="$PWD/SUIT_AND_BRAINS_44"
  else
    echo "ERROR: candidate tree not found; pass --tree PATH" >&2
    exit 2
  fi
fi
TREE="$(cd "$TREE" && pwd)"

if [ -z "$WORKDIR" ]; then
  WORKDIR="$PWD/pi5_run"
fi
mkdir -p "$WORKDIR"
WORKDIR="$(cd "$WORKDIR" && pwd)"

if [ -z "$WHEEL" ]; then
  WHEEL="$TREE/release/suit_and_brains-44.1.0-py3-none-any.whl"
fi
if [ ! -f "$WHEEL" ]; then
  echo "ERROR: wheel not found: $WHEEL" >&2
  exit 2
fi
if [ ! -f "$TREE/fixtures/hist_geo_time_105_v1_public.json" ]; then
  echo "ERROR: Precision-105 fixture missing in tree: $TREE" >&2
  exit 2
fi
if [ ! -f "$TREE/BUILD040_P105_FUNCTION_TRACE.json" ]; then
  echo "ERROR: Build 040 reference trace missing in tree: $TREE" >&2
  exit 2
fi
# Every other file the run needs, checked before anything is installed or
# run. A missing file prints its exact path and stops the run.
for _f in "$TREE/tests/test_precision_105_build040.py" \
         "$TREE/tests/test_crampu_controller.py" \
         "$TREE/tests/test_build43.py" \
         "$TREE/tests/test_build44.py" \
         "$TREE/fixtures/PRECISION315_BLIND_V2.json" \
         "$SCRIPT_DIR/modes_43.py" "$SCRIPT_DIR/proof_43.py"; do
  if [ ! -f "$_f" ]; then
    echo "ERROR: required file missing: $_f" >&2
    echo "The package is incomplete; nothing was installed or run." >&2
    exit 2
  fi
done

# --- python >= 3.11 -------------------------------------------------------
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
case "$PYVER" in
  3.11|3.12|3.13|3.14) : ;;
  *) echo "ERROR: python3 $PYVER is too old; the wheel needs >= 3.11" >&2; exit 2 ;;
esac

VENV="$WORKDIR/venv"
echo "== Build 44.1.0 Pi 5 test =="
echo "tree:    $TREE"
echo "wheel:   $WHEEL"
echo "workdir: $WORKDIR"
echo "python:  $(python3 --version) ($PYVER)"

if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV" || {
    echo "ERROR: could not create the virtual environment." >&2
    echo "On Raspberry Pi OS: sudo apt install python3-venv python3-full" >&2
    exit 2
  }
fi
"$VENV/bin/python" -m ensurepip --default-pip >/dev/null 2>&1 || true
"$VENV/bin/pip" install --no-index --force-reinstall "$WHEEL" > "$WORKDIR/wheel_install.log" 2>&1
echo "wheel installed: $("$VENV/bin/pip" show suit-and-brains | grep -E '^(Name|Version|Location):' | tr '\n' ' ')"

# --- driver (embedded) ------------------------------------------------------
# Runs from $WORKDIR (NOT from the tree), so every import resolves to the
# installed wheel. Fixtures, the Build 040 reference trace and the bundled
# test files are located by absolute path under $TREE.
cd "$WORKDIR"
set +e
"$VENV/bin/python" - "$TREE" "$WORKDIR" "$WHEEL" "$SCRIPT_DIR" > "$WORKDIR/pi5_test_log.txt" 2>&1 <<'PYEOF'
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

TREE = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
WHEEL = Path(sys.argv[3]).resolve()
SCRIPT_DIR = Path(sys.argv[4]).resolve()
MODES_SRC = SCRIPT_DIR / "modes_43.py"
PROOF_SRC = SCRIPT_DIR / "proof_43.py"
FIXTURE = TREE / "fixtures" / "hist_geo_time_105_v1_public.json"
HIST_TRACE = TREE / "BUILD040_P105_FUNCTION_TRACE.json"
TEST_P105 = TREE / "tests" / "test_precision_105_build040.py"
TEST_CRAMPU = TREE / "tests" / "test_crampu_controller.py"

from build040_runtime import (
    Brains49Runtime, OfflineAdvisoryProvider, OnlineAIGateway,
    ReplayProvider, SoSoContinuity, SoSoSuitRuntime,
)
from build040_runtime.precision_105 import BlockedGenerationError, Precision105Constructor
from build040_runtime.precision_105_grader import grade_105_response
from contract.brains_wiring import BrainsWiringRegistry
from crampu.build030_bridge import Merged49BrainsCramAdapter
from crampu.controller import CRAMPUMemoryController

ADMIN = secrets.token_hex(24)
AGENT = "L01"
FAILURES: list[str] = []
NOTES: list[str] = []
func_calls: list[dict] = []
registry_calls: list[dict] = []
mem_accesses: list[dict] = []
STAGE = {"name": "setup"}
SECTION = {"name": "setup"}


def set_stage(name: str) -> None:
    """Record the section the driver is in, for the failure summary."""
    SECTION["name"] = name
    try:
        (WORK / "stage.txt").write_text(name)
    except Exception:
        pass


def hardware_model() -> str:
    """Hardware model string. On a Pi this is the device-tree model
    (e.g. "Raspberry Pi 5 Model B Rev 1.0"); CPU architecture alone
    cannot identify a Pi 5, so it is recorded separately."""
    dt = Path("/proc/device-tree/model")
    if dt.exists():
        try:
            return dt.read_bytes().rstrip(b"\0").decode("utf-8", "replace").strip()
        except Exception:
            pass
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        try:
            for line in cpuinfo.read_text(errors="replace").splitlines():
                if line.startswith("Model"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
    return platform.processor() or platform.machine()


HW_MODEL = hardware_model()
HOST_TYPE = "raspberry_pi_5" if "Raspberry Pi 5" in HW_MODEL else "other"


def log(msg: str) -> None:
    print(msg, flush=True)


def check(ok: bool, label: str, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    log(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def trunc(v, limit: int = 400):
    s = v if isinstance(v, str) else json.dumps(v, sort_keys=True, default=str)
    return s if len(s) <= limit else s[:limit] + f"...<truncated {len(s) - limit} chars>"


# ---------------------------------------------------------------------------
# Instrumentation: top-level dispatch, inner registry execution, memory I/O.
# The wrappers call the original implementation and only record what ran.
# ---------------------------------------------------------------------------
_orig_dispatch_lower = Brains49Runtime.dispatch_lower49
_orig_dispatch_higher = Brains49Runtime.dispatch_higher49
_orig_execute_operation = BrainsWiringRegistry.execute_operation
_orig_mem_write = SoSoSuitRuntime.execute_memory_write
_orig_mem_read = SoSoSuitRuntime.execute_memory_read


def _dispatch_lower(self, session_id, function_id, problem):
    res = _orig_dispatch_lower(self, session_id, function_id, problem)
    func_calls.append({
        "seq": len(func_calls) + 1, "level": "top-level dispatch",
        "function_id": function_id, "session_id": session_id,
        "input": trunc(problem), "output": res.get("decision", res),
        "consumer": "Precision105Constructor.construct",
        "inner_trace_events": len(res.get("trace", [])),
    })
    for ev in res.get("trace", []):
        if "function" in ev:
            func_calls.append({
                "seq": len(func_calls) + 1, "level": "inner execution",
                "function_id": ev["function"], "session_id": session_id,
                "input": ev.get("input"), "output": ev.get("output"),
                "input_digest": ev.get("input_digest"),
                "dependencies": ev.get("dependencies"),
                "possible_consumers": ev.get("possible_consumers"),
                "consumer": f"pathway025 semantic(), called from {function_id} dispatch",
            })
        elif "connection" in ev:
            func_calls.append({
                "seq": len(func_calls) + 1, "level": "inner route",
                "connection": ev.get("connection"), "disposition": ev.get("disposition"),
                "session_id": session_id,
            })
    return res


def _dispatch_higher(self, session_id, function_id, payload):
    res = _orig_dispatch_higher(self, session_id, function_id, payload)
    func_calls.append({
        "seq": len(func_calls) + 1, "level": "top-level dispatch",
        "function_id": function_id, "session_id": session_id,
        "input": trunc(payload), "output": res,
        "consumer": "Precision105Constructor.construct",
    })
    return res


def _execute_operation(self, function_id, payload, *args):
    out = _orig_execute_operation(self, function_id, payload, *args)
    registry_calls.append({
        "function_id": function_id,
        "args": trunc(args), "output": trunc(out),
    })
    return out


def _mem_write(self, agent_id, family, index, payload, *a, **kw):
    commit_rec, ptr = _orig_mem_write(self, agent_id, family, index, payload, *a, **kw)
    ctrl = self.adapter.controller
    insp = ctrl.inspect(ADMIN)
    mirror = dict(commit_rec.get("mirror_reference") or {})
    store = self.adapter.big if family == "BIG49" else self.adapter.small
    row = store.db.execute(
        "SELECT 1 FROM blocks WHERE pool=? AND record_id=?",
        (mirror.get("pool"), mirror.get("record_id")),
    ).fetchone()
    mem_accesses.append({
        "seq": len(mem_accesses) + 1, "stage": STAGE["name"],
        "function": "SoSoSuitRuntime.execute_memory_write",
        "pool": family, "address_global": int(commit_rec["block_id"]),
        "address_local": index, "operation": "write",
        "revision": int(commit_rec["revision"]), "txid": commit_rec.get("txid"),
        "sha256": commit_rec.get("sha256"),
        "source": "COOL (committed) + mirror",
        "cool_reference": commit_rec.get("cool_reference"),
        "mirror": {**mirror, "present": row is not None},
        "outbox_pending": insp["outbox_pending"],
        "outbox_blocked": insp["outbox_blocked"],
    })
    return commit_rec, ptr


def _mem_read(self, agent_id, family, index, *a, **kw):
    data = _orig_mem_read(self, agent_id, family, index, *a, **kw)
    mem_accesses.append({
        "seq": len(mem_accesses) + 1, "stage": STAGE["name"],
        "function": "SoSoSuitRuntime.execute_memory_read",
        "pool": family, "address_global": int(data["block_id"]),
        "address_local": index, "operation": "read",
        "revision": int(data["revision"]),
        "source": "COOL (load_from_cool, revision-bound read)",
        "cool_reference": data.get("cool_reference"),
    })
    return data


Brains49Runtime.dispatch_lower49 = _dispatch_lower
Brains49Runtime.dispatch_higher49 = _dispatch_higher
BrainsWiringRegistry.execute_operation = _execute_operation
SoSoSuitRuntime.execute_memory_write = _mem_write
SoSoSuitRuntime.execute_memory_read = _mem_read


def make_runtime(root: Path):
    ctrl = CRAMPUMemoryController(root / "warm", root / "cool", ADMIN)
    for idx in range(14):
        ctrl.register_block_owner(idx, AGENT, ADMIN)
    ctrl.register_block_owner(1600, AGENT, ADMIN)
    ctrl.register_block_owner(1601, AGENT, ADMIN)
    adapter = Merged49BrainsCramAdapter(ctrl, root / "brains")
    cont = SoSoContinuity(root / "soso.sqlite3")
    suit = SoSoSuitRuntime(adapter)
    suit.authorize_agent(AGENT, {"memory.read", "memory.write"},
                         allowed_block_ids=set(range(14)) | {1600, 1601})
    brains = Brains49Runtime(cont, suit, root / "muse_state")
    return ctrl, adapter, cont, suit, brains


def close_all(*objs):
    for obj in objs:
        try:
            obj.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 0. Environment
# ---------------------------------------------------------------------------
log("== Build 44.1.0 Pi 5 test ==")
log(f"host: {platform.machine()} {platform.system()} {platform.release()} | "
    f"python {platform.python_version()} | {Path(sys.executable)}")
log(f"tree: {TREE}")
log(f"workdir: {WORK}")
import build040_runtime
log(f"build040_runtime imported from: {Path(build040_runtime.__file__).resolve()}")
assert "site-packages" in str(build040_runtime.__file__) or "dist-packages" in str(build040_runtime.__file__), \
    "code under test must come from the installed wheel, not the tree"
challenge = json.loads(FIXTURE.read_text(encoding="utf-8"))
anchors = [p for p in challenge["positions"] if p["kind"] == "ANCHOR"]
open_specs = [p for p in challenge["positions"] if p["kind"] != "ANCHOR"]
log(f"challenge {challenge['id']}: {len(challenge['positions'])} positions, "
    f"{len(anchors)} anchors, {len(open_specs)} open, words {challenge['word_min']}..{challenge['word_max']}")
hist = json.loads(HIST_TRACE.read_text(encoding="utf-8"))

STATE = WORK / "state"
if STATE.exists():
    shutil.rmtree(STATE)
STATE.mkdir(parents=True)

# ---------------------------------------------------------------------------
# 1a. Offline case: generation must be blocked, not hardcoded.
# ---------------------------------------------------------------------------
set_stage("1a-offline")
log("\n-- 1a. Offline gateway (expect BlockedGenerationError) --")
off_root = STATE / "offline"
off_root.mkdir(parents=True)
ctrl_o, ad_o, cont_o, suit_o, brains_o = make_runtime(off_root)
gw_off = OnlineAIGateway(primary=OfflineAdvisoryProvider())
ctor_o = Precision105Constructor(cont_o, suit_o, brains_o, gateway=gw_off)
STAGE["name"] = "offline-anchor-write/read"
mark = len(mem_accesses)
try:
    ctor_o.construct(FIXTURE, session_id="p105-offline", agent_id=AGENT)
    check(False, "offline case raises BlockedGenerationError", "construct returned")
    off_blocked = False
except BlockedGenerationError as exc:
    off_blocked = True
    log(f"  blocked as required: {exc}")
    check(True, "offline case raises BlockedGenerationError")
off_accesses = mem_accesses[mark:]
off_writes = [a for a in off_accesses if a["operation"] == "write"]
off_reads = [a for a in off_accesses if a["operation"] == "read"]
log(f"  before the block: {len(off_writes)} anchor writes, {len(off_reads)} anchor reads "
    f"(anchors + chronology + recall all ran; generation never started)")
check(len(off_writes) == 14 and len(off_reads) == 14,
      "offline run still wrote/read the 14 anchors before blocking",
      f"writes={len(off_writes)} reads={len(off_reads)}")
close_all(ad_o, ctrl_o, cont_o)

# ---------------------------------------------------------------------------
# 1b. Replay case: 105 positions, anchors preserved, 91 open from replay.
# ---------------------------------------------------------------------------
set_stage("1b-replay")
log("\n-- 1b. Replay gateway (105 positions) --")
prompt = json.dumps({
    "challenge_id": challenge["id"],
    "word_min": challenge["word_min"], "word_max": challenge["word_max"],
    "anchors": [{"position": a["position"], "text": a["text"]} for a in anchors],
    "open_positions": [o["position"] for o in open_specs],
}, sort_keys=True)
fake_generated = {str(o["position"]): f"Gateway generated line {o['position']} words" for o in open_specs}
rp = ReplayProvider()
rp.record(prompt, json.dumps(fake_generated))

rep_root = STATE / "replay"
rep_root.mkdir(parents=True)
ctrl, adapter, cont, suit, brains = make_runtime(rep_root)
gw = OnlineAIGateway(primary=rp)
ctor = Precision105Constructor(cont, suit, brains, gateway=gw)
STAGE["name"] = "replay-anchor-write"
# The offline run above already recorded 13 L22 + 1 U09 calls in the global
# logs; the function map in section 2 must be scoped to THIS run only.
FUNC_MARK = len(func_calls)
REG_MARK = len(registry_calls)
t0 = time.monotonic()
out = ctor.construct(FIXTURE, session_id="p105-replay", agent_id=AGENT)
elapsed = time.monotonic() - t0
REPLAY_CALLS = func_calls[FUNC_MARK:]
REPLAY_REGISTRY = registry_calls[REG_MARK:]
OFFLINE_CALLS = func_calls[:FUNC_MARK]
positions = out["response"]["positions"]
trace_exec = out["execution_trace"]
log(f"  construct took {elapsed:.2f}s")
check(len(positions) == 105, "replay returns all 105 positions", f"got {len(positions)}")
anchor_ok = all(
    next(p["text"] for p in positions if p["position"] == a["position"]) == a["text"]
    for a in anchors)
check(anchor_ok, "14 locked anchors preserved verbatim")
open_ok = all(
    next(p["text"] for p in positions if p["position"] == o["position"]) == fake_generated[str(o["position"])]
    for o in open_specs)
check(open_ok, "91 open positions filled from the replay provider",
      "every open text equals the recorded replay payload")
check(trace_exec["generation_source"] == "replay_cache",
      "generation source is replay_cache", trace_exec["generation_source"])
check(trace_exec["external_ai_used"] is True, "external_ai_used is True (replay, not fallback)")
check(trace_exec["memory_writes"] == 15, "execution trace reports 15 memory writes",
      f"got {trace_exec['memory_writes']}")
check(trace_exec["memory_reads"] == 14, "execution trace reports 14 memory reads",
      f"got {trace_exec['memory_reads']}")
check(trace_exec["l22_chronology_checks"] == 13, "execution trace reports 13 L22 chronology checks")
check(trace_exec["functions_executed"] == ["L22", "U09"],
      "functions_executed is exactly [L22, U09]", str(trace_exec["functions_executed"]))

# Grade the replay response: structure must pass; segment keywords are
# expected to FAIL because the replay payload is synthetic wiring text,
# not historical content. That is the documented limit of a replay run.
resp_path = WORK / "response_105.json"
resp_path.write_text(json.dumps(out["response"], indent=2))
grade = grade_105_response(resp_path, FIXTURE)
log(f"  grader: structural {grade['structural_passes']}/{grade['total_positions']} pass, "
    f"segments {grade['segment_historical_passes']}/{grade['segment_historical_total']} pass")
check(grade["structural_failures"] == 0, "grader structural check passes (105/105)",
      f"failures={grade['structural_failures']}")
if grade["segment_historical_passes"] < grade["segment_historical_total"]:
    NOTES.append(
        f"grader segment keywords {grade['segment_historical_passes']}/{grade['segment_historical_total']}: "
        "expected for replay — the synthetic payload ('Gateway generated line N words') carries no "
        "historical content. A live provider run is needed to grade historical grounding.")
    log("  NOTE: segment keyword failures are expected for synthetic replay text (see trace notes)")

# ---------------------------------------------------------------------------
# 2. Function map vs the Build 040 trace.
# ---------------------------------------------------------------------------
set_stage("2-function-map")
log("\n-- 2. Function map vs Build 040 trace --")
log(f"  (scoped to the replay run; the offline run's 13 L22 + 1 U09 calls are "
    f"recorded separately as offline_function_calls)")
top = [c for c in REPLAY_CALLS if c["level"] == "top-level dispatch"]
inner = [c for c in REPLAY_CALLS if c["level"] == "inner execution"]
routes = [c for c in REPLAY_CALLS if c["level"] == "inner route"]
l22_top = [c for c in top if c["function_id"] == "L22"]
u09_top = [c for c in top if c["function_id"] == "U09"]
log(f"  top-level dispatches: {len(l22_top)} x L22, {len(u09_top)} x U09")
check(len(l22_top) == 13, "13 top-level L22 dispatches", f"got {len(l22_top)}")
check(len(u09_top) == 1, "1 top-level U09 dispatch", f"got {len(u09_top)}")
u09_events = u09_top[0]["output"].get("events", []) if u09_top else []
log(f"  U09 structural check: {len(u09_events)} events; first: "
    f"{trunc(u09_events[0], 300) if u09_events else 'none'}")

from collections import Counter
observed_counts = dict(Counter(c["function_id"] for c in inner))
expected_counts = dict(hist["per_function_invocations"])
log(f"  inner execution counts (observed): {observed_counts}")
log(f"  inner execution counts (Build 040): {expected_counts}")
missing = {f: expected_counts[f] for f in expected_counts if observed_counts.get(f, 0) != expected_counts[f]}
extra = {f: observed_counts[f] for f in observed_counts if f not in expected_counts}
check(observed_counts == expected_counts,
      "inner function counts match Build 040 exactly (8 functions x 13)",
      f"missing/different={missing or 'none'} extra={extra or 'none'}")
check(len(REPLAY_REGISTRY) == len(inner),
      "every inner execution went through the bound registry (execute_operation)",
      f"registry={len(REPLAY_REGISTRY)} inner={len(inner)}")

# Per-check order: the 13 L22 calls each run the same inner sequence.
EXPECTED_ORDER = ["L03", "L04", "L16", "L22", "L17", "L18", "L31", "U16"]
seqs = []
cur = []
for c in inner:
    cur.append(c["function_id"])
    if c["function_id"] == "U16":
        seqs.append(cur)
        cur = []
order_ok = len(seqs) == 13 and all(s == EXPECTED_ORDER for s in seqs)
check(order_ok, "inner order per L22 check is L03 L04 L16 L22 L17 L18 L31 U16",
      f"checks={len(seqs)}" + ("" if order_ok else f" first={seqs[0] if seqs else 'none'}"))
if cur:
    NOTES.append(f"inner events after the last U16 (incomplete tail): {cur}")

# Sample I/O: first check vs the Build 040 sample. The raw anchor
# observations carry no digest; L03's OUTPUT (the accepted rows) does, and
# those digests flow into L22's input and U16's support. Compare those.
sample = hist["sample_io_by_function"]
first_l03 = next((c for c in inner if c["function_id"] == "L03"), None)
first_u16 = next((c for c in inner if c["function_id"] == "U16"), None)
digests_ok = False
if first_l03 and first_u16:
    obs_digests = [row.get("digest") for row in first_l03["output"].get("accepted", [])]
    exp_digests = [row.get("digest") for row in sample["L03"]["output"].get("accepted", [])]
    obs_u16_out = first_u16["output"]
    exp_u16_out = sample["U16"]["output"]
    digests_ok = obs_digests == exp_digests and obs_u16_out.get("answer") == exp_u16_out.get("answer")
    log(f"  first-check L03 accepted digests: {obs_digests}")
    log(f"  Build 040 sample digests:         {exp_digests}")
    log(f"  first-check U16 answer: {obs_u16_out.get('answer')} (Build 040: {exp_u16_out.get('answer')})")
check(digests_ok, "first-check sample I/O digests match the Build 040 trace")
# L22 decision answers: the top-level dispatch decision (not the inner L22
# function's output, which is a grouping, not a decision) must name the
# later anchor of each consecutive pair.
l22_answers = [c["output"].get("answer") for c in l22_top]
expected_answers = [f"pos_{anchors[i + 1]['position']}" for i in range(len(anchors) - 1)]
check(l22_answers == expected_answers, "all 13 L22 decisions answer with the later anchor",
      f"answers={l22_answers}")

# Generic registry probe (NOT part of the P105 run): what invoke() returns.
log("  generic registry probe (invoke, not dispatch):")
try:
    probe_problem = {
        "pathway": "B",
        "query": {"subject": "era_order", "predicate": "latest_anchor"},
        "observations": [
            {"source": "anchor", "subject": "era_order", "predicate": "latest_anchor",
             "value": "pos_1", "event": "1492-01-01T00:00:00+00:00"},
            {"source": "anchor", "subject": "era_order", "predicate": "latest_anchor",
             "value": "pos_9", "event": "1956-01-01T00:00:00+00:00"},
        ],
    }
    generic = BrainsWiringRegistry().invoke("L22", {"authority": 0, "problem": probe_problem})
    generic_probe = {"function_id": "L22", "via": "BrainsWiringRegistry.invoke",
                     "result": trunc(generic)}
except Exception as exc:  # noqa: BLE001 - the probe result itself is the evidence
    generic_probe = {"function_id": "L22", "via": "BrainsWiringRegistry.invoke",
                     "error": f"{type(exc).__name__}: {exc}"}
log(f"    {trunc(generic_probe, 300)}")
check("error" not in generic_probe, "generic invoke probe returned a response (recorded for contrast)")

# ---------------------------------------------------------------------------
# 3. Memory map: 15 writes, 14 reads, with revisions and mirror status.
# ---------------------------------------------------------------------------
set_stage("3-memory-map")
log("\n-- 3. Memory map (replay run) --")
rep_accesses = [a for a in mem_accesses if a["stage"] == "replay-anchor-write"]
rep_writes = [a for a in rep_accesses if a["operation"] == "write"]
rep_reads = [a for a in rep_accesses if a["operation"] == "read"]
log(f"  observed: {len(rep_writes)} writes, {len(rep_reads)} reads")
check(len(rep_writes) == 15 and len(rep_reads) == 14,
      "memory totals are 15 writes / 14 reads",
      f"writes={len(rep_writes)} reads={len(rep_reads)}")
anchor_writes = [a for a in rep_writes if a["pool"] == "BIG49"]
summary_writes = [a for a in rep_writes if a["pool"] == "SMALL49"]
check([a["address_global"] for a in anchor_writes] == list(range(14)),
      "anchor writes hit Big49 addresses 0-13 in order")
check([a["address_global"] for a in summary_writes] == [1600],
      "completion summary written to Small49 local 0 (global 1600)")
check(all(a["revision"] == 1 for a in rep_writes),
      "every write committed at COOL revision 1", 
      f"revisions={[a['revision'] for a in rep_writes]}")
check(all(a["mirror"].get("present") for a in rep_writes),
      "every written record is mirrored (mirror record present in the store)")
check(all(a["revision"] == 1 for a in rep_reads),
      "every anchor read came from COOL at revision 1")
check(all(a["outbox_pending"] == 0 and a["outbox_blocked"] == 0 for a in rep_writes),
      "outbox empty after every write (pending 0, blocked 0)")
insp = ctrl.inspect(ADMIN)
log(f"  final state: big49_mirrored={adapter.big.count()} small49_mirrored={adapter.small.count()} "
    f"cool_committed={insp['committed']} pending={insp['outbox_pending']} blocked={insp['outbox_blocked']}")
close_all(adapter, ctrl, cont)

# ---------------------------------------------------------------------------
# 4. WARM staging: one real bridge write, captured at each stage.
# ---------------------------------------------------------------------------
set_stage("4-warm-staging")
log("\n-- 4. WARM staging (one bridge write, Small49 local 1 / global 1601) --")
st_root = STATE / "staging"
st_root.mkdir(parents=True)
s_ctrl, s_adapter, s_cont, s_suit, s_brains = make_runtime(st_root)
PAYLOAD = {"content": "staging-demo", "relation": "ASSOCIATED_WITH"}


class _Stop(Exception):
    pass


def _hook_at(stage_name):
    def hook(name):
        if name == stage_name:
            raise _Stop(name)
    return hook


warm_stage: dict = {}
try:
    STAGE["name"] = "staging-hot"
    s_adapter.write_and_sync(AGENT, "SMALL49", 1, PAYLOAD, fault_hook=_hook_at("after_hot_before_warm"))
    check(False, "staging attempt 1 stops after HOT", "write completed")
except _Stop:
    warm_stage["hot"] = {
        "address_global": 1601, "address_local": 1, "pool": "SMALL49",
        "hot_count": s_ctrl.inspect(ADMIN)["hot"],
        "cool_revision": s_ctrl._revision(1601),
        "outbox_pending": s_ctrl.inspect(ADMIN)["outbox_pending"],
    }
    try:
        s_ctrl.load_from_cool(1601, AGENT)
        check(False, "nothing is in COOL after the HOT stage", "load_from_cool returned")
    except KeyError:
        check(True, "HOT stage: record is hot only, nothing in COOL")
    log(f"  HOT: address 1601, hot_count={warm_stage['hot']['hot_count']}, "
        f"cool_revision={warm_stage['hot']['cool_revision']}")

try:
    STAGE["name"] = "staging-warm"
    s_adapter.write_and_sync(AGENT, "SMALL49", 1, PAYLOAD, fault_hook=_hook_at("after_warm_before_cool"))
    check(False, "staging attempt 2 stops after WARM", "write completed")
except _Stop:
    staged = sorted((st_root / "warm").glob("*.json"), key=lambda p: p.stat().st_mtime)
    latest = staged[-1]
    record = json.loads(latest.read_text())
    warm_stage["warm"] = {
        "staging_file": str(latest), "txid": latest.stem,
        "record_txid": record.get("txid"), "block_id": record.get("block_id"),
        "payload": record.get("payload"),
        "staging_files_present": [p.name for p in staged],
        "cool_revision": s_ctrl._revision(1601),
    }
    check(latest.stem == record.get("txid"), "WARM stage: staging file name is the transaction id")
    check(record.get("block_id") == 1601, "WARM stage: staged record is block 1601")
    log(f"  WARM: file {latest.name}, txid {latest.stem}, block {record.get('block_id')}")

try:
    STAGE["name"] = "staging-cool"
    s_adapter.write_and_sync(AGENT, "SMALL49", 1, PAYLOAD, fault_hook=_hook_at("after_cool_before_mirror"))
    check(False, "staging attempt 3 stops after COOL", "write completed")
except _Stop:
    insp3 = s_ctrl.inspect(ADMIN)
    data3 = s_ctrl.load_from_cool(1601, AGENT)
    warm_stage["cool"] = {
        "address_global": 1601, "revision": int(data3["revision"]),
        "txid": data3.get("txid"), "sha256": data3.get("sha256"),
        "payload": data3["payload"],
        "outbox_pending": insp3["outbox_pending"], "outbox_blocked": insp3["outbox_blocked"],
    }
    check(int(data3["revision"]) == 1, "COOL stage: committed at revision 1")
    check(insp3["outbox_pending"] == 1, "COOL stage: one PENDING outbox row (mirror not yet run)",
          f"pending={insp3['outbox_pending']}")
    log(f"  COOL: address 1601, revision 1, outbox_pending={insp3['outbox_pending']}")

STAGE["name"] = "staging-mirror"
drained = s_adapter.sync_pending_outbox()
insp4 = s_ctrl.inspect(ADMIN)
mirror_ptr = next(iter(drained.values()), None)
warm_stage["mirror"] = {
    "pointer": mirror_ptr, "drained": drained,
    "outbox_pending": insp4["outbox_pending"], "outbox_blocked": insp4["outbox_blocked"],
}
check(insp4["outbox_pending"] == 0 and insp4["outbox_blocked"] == 0,
      "mirror stage: outbox drained (pending 0, blocked 0)",
      f"pending={insp4['outbox_pending']} blocked={insp4['outbox_blocked']}")
check(mirror_ptr is not None, "mirror stage: a mirror record was produced", str(mirror_ptr))
log(f"  MIRROR: {mirror_ptr}")

# Reopen the database and verify the committed record.
close_all(s_adapter, s_ctrl, s_cont)
r_ctrl = CRAMPUMemoryController(st_root / "warm", st_root / "cool", ADMIN)
r_adapter = Merged49BrainsCramAdapter(r_ctrl, st_root / "brains")
reopened = r_ctrl.load_from_cool(1601, AGENT)
insp_r = r_ctrl.inspect(ADMIN)
warm_stage["reopen"] = {
    "payload": reopened["payload"], "revision": int(reopened["revision"]),
    "outbox_pending": insp_r["outbox_pending"], "outbox_blocked": insp_r["outbox_blocked"],
    "small49_mirrored": r_adapter.small.count(),
}
check(reopened["payload"] == PAYLOAD, "reopen: committed record reads back with the same payload")
check(int(reopened["revision"]) == 1, "reopen: revision still 1")
log(f"  REOPEN: payload={reopened['payload']}, revision 1, "
    f"pending={insp_r['outbox_pending']} blocked={insp_r['outbox_blocked']}")
close_all(r_adapter, r_ctrl)

# ---------------------------------------------------------------------------
# 5. Bundled tests (run by path from the workdir, so they import the wheel).
# ---------------------------------------------------------------------------
set_stage("5-bundled-tests")
log("\n-- 5. Bundled tests (installed wheel) --")
bundled: dict = {}
for name, test_file in (("precision_105", TEST_P105), ("crampu_controller", TEST_CRAMPU)):
    proc = subprocess.run(
        [sys.executable, str(test_file), "-v"],
        cwd=str(WORK), capture_output=True, text=True, timeout=600)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    bundled[name] = {
        "returncode": proc.returncode,
        "last_lines": tail[-6:],
        "ran_ok": proc.returncode == 0,
    }
    log(f"  {name}: exit {proc.returncode}")
    for line in tail[-6:]:
        log(f"    {line}")
    check(proc.returncode == 0, f"bundled test file {test_file.name} passes")

# ---------------------------------------------------------------------------
# 6. Build 043 four-mode tests (run by path from the workdir, so they
#    import the installed wheel, not the tree). modes_43.py inserts its
#    own tree path, which does not exist under the workdir copy, so the
#    insert is inert and the wheel wins. test_build43.py inserts its
#    parent's parent (the workdir), which holds no build040_runtime, so
#    it too imports the wheel; the two test files it shells out to are
#    copied alongside it. modes_43.py ships next to the runner (see the
#    header layout note); it is never resolved from the tree's parent.
# ---------------------------------------------------------------------------
set_stage("6-four-modes")
log("\n-- 6. Build 043 four modes (installed wheel) --")
modes_dir = WORK / "modes_check"
modes_dir.mkdir(exist_ok=True)
for _src in (MODES_SRC, PROOF_SRC):
    if not _src.is_file():
        raise SystemExit(f"ERROR: required driver missing: {_src} "
                         "(modes_43.py and proof_43.py must ship next to "
                         "run_pi5_test.sh)")
shutil.copy(MODES_SRC, modes_dir / "modes_43.py")
tests_dir = WORK / "tests"
tests_dir.mkdir(exist_ok=True)
for _name in ("test_build43.py", "test_precision_105_build040.py",
              "test_crampu_controller.py"):
    shutil.copy(TREE / "tests" / _name, tests_dir / _name)
# test_precision_105_build040.py resolves its fixture relative to its own
# location (parents[1] / "fixtures" / ...), so link the tree's fixtures in.
_fixtures = WORK / "fixtures"
if _fixtures.is_symlink() or _fixtures.exists():
    _fixtures.unlink() if _fixtures.is_symlink() else shutil.rmtree(_fixtures)
_fixtures.symlink_to(TREE / "fixtures", target_is_directory=True)
four_modes: dict = {}
proc = subprocess.run(
    [sys.executable, str(modes_dir / "modes_43.py")],
    cwd=str(WORK), capture_output=True, text=True, timeout=600)
four_modes["returncode"] = proc.returncode
four_modes["table"] = proc.stdout.strip().splitlines()
(WORK / "modes_43.log").write_text(proc.stdout + proc.stderr)
_modes_trace = modes_dir / "traces" / "modes_43.json"
if _modes_trace.is_file():
    four_modes["trace"] = json.loads(_modes_trace.read_text())
log(f"  modes_43.py: exit {proc.returncode}")
for line in proc.stdout.strip().splitlines():
    log(f"    {line}")
_expected_rows = {
    "lower_only": ("COMPLETED", "CONFLICT"),
    "higher_only": ("UNRESOLVED", "ABSTAIN"),
    "joined_lower_first": ("COMPLETED", "CONFLICT"),
    "joined_higher_first": ("COMPLETED", "CONFLICT"),
}
for _mode, (_status, _decision) in _expected_rows.items():
    _row = next((l for l in proc.stdout.splitlines() if l.startswith(_mode)), "")
    check(proc.returncode == 0 and _status in _row and _decision in _row,
          f"four-mode {_mode}: {_status} / {_decision}", _row.strip())
proc = subprocess.run(
    [sys.executable, "-m", "unittest", "test_build43", "-v"],
    cwd=str(tests_dir), capture_output=True, text=True, timeout=600)
_full = proc.stdout + proc.stderr
(WORK / "test_build43.log").write_text(_full)
_tail = _full.strip().splitlines()
four_modes["build43_tests"] = {
    "returncode": proc.returncode, "last_lines": _tail[-6:],
    "ran_ok": proc.returncode == 0, "total_tests": 26,
}
# The independent-mode fix (44.1.0): five tests must each pass — both
# modes, same run and after a genuine restart, plus the join guard.
_expected_follow = [
    "test_higher_only_follow_up_stays_terminal",
    "test_higher_only_follow_up_stays_terminal_after_restart",
    "test_lower_only_follow_up_stays_terminal",
    "test_lower_only_follow_up_stays_terminal_after_restart",
    "test_join_against_independent_case_fails_mode_mismatch",
]
four_modes["independent_follow_up_tests"] = {}
for _t in _expected_follow:
    _line = next((l for l in _full.splitlines() if l.strip().startswith(_t)), "")
    four_modes["independent_follow_up_tests"][_t] = " ... ok" in _line
    check(" ... ok" in _line, f"independent-mode {_t}", _line.strip())
log(f"  test_build43.py: exit {proc.returncode}")
for line in _tail[-6:]:
    log(f"    {line}")
check(proc.returncode == 0, "bundled test file test_build43.py passes (26 tests)")

# ---------------------------------------------------------------------------
# Run Build 44 regressions against the same installed wheel.
_build44 = WORK / "build44_check"
(_build44 / "tests").mkdir(parents=True, exist_ok=True)
(_build44 / "fixtures").mkdir(exist_ok=True)
for _test in ("test_build43.py", "test_build44.py"):
    shutil.copyfile(TREE / "tests" / _test, _build44 / "tests" / _test)
shutil.copyfile(TREE / "fixtures" / "PRECISION315_BLIND_V2.json",
                _build44 / "fixtures" / "PRECISION315_BLIND_V2.json")
_proc44 = subprocess.run([sys.executable, "-m", "unittest", "test_build44", "-v"],
                         cwd=_build44 / "tests", capture_output=True, text=True,
                         env={**os.environ, "BUILD441_HIGHER_FIRST_TRACE": str(WORK / "higher-first-15.json")})
(WORK / "test_build44.log").write_text(_proc44.stdout + _proc44.stderr)
check(_proc44.returncode == 0, "Build 44 join and native Precision-315 tests pass")

# 7. Joined Higher-first control (proof_43.py): Higher requests exact
#    information, an unrelated reading leaves the case on HOLD, a matching
#    reading after a genuine restart reaches Lower, and Higher consumes
#    Lower's actual result. The proof runs by path from the workdir, so it
#    imports the installed wheel; its trace lands next to the copied file.
# ---------------------------------------------------------------------------
set_stage("7-joined-higher-first-proof")
log("\n-- 7. Joined Higher-first control (installed wheel) --")
proof_dir = modes_dir
shutil.copy(PROOF_SRC, proof_dir / "proof_43.py")
proof_work = WORK / "proof_check"
proof_work.mkdir(exist_ok=True)
proc = subprocess.run(
    [sys.executable, str(proof_dir / "proof_43.py"), "--workdir", str(proof_work)],
    cwd=str(WORK), capture_output=True, text=True, timeout=600)
(WORK / "proof_43.log").write_text(proc.stdout + proc.stderr)
_proof_trace_path = proof_dir / "traces" / "proof_43_higher_first.json"
_proof_trace = (json.loads(_proof_trace_path.read_text())
                if _proof_trace_path.is_file() else {})
log(f"  proof_43.py: exit {proc.returncode}")
for line in proc.stdout.strip().splitlines()[-8:]:
    log(f"    {line}")
check(proc.returncode == 0, "joined Higher-first proof_43.py passes")
_proof_joins = _proof_trace.get("joins", [])
_proof_consumed = [j for j in _proof_joins if j.get("status") == "CONSUMED" and j.get("direction") == "LOWER_TO_HIGHER"]
check(_proof_trace.get("status") == "COMPLETED",
      "proof case COMPLETED after restart", str(_proof_trace.get("status")))
check(_proof_trace.get("decision_before") != _proof_trace.get("decision_after"),
      "proof decision changed after Higher consumed Lower",
      f"{_proof_trace.get('decision_before')} -> {_proof_trace.get('decision_after')}")
check(len(_proof_consumed) >= 1 and all(
    j.get("consumer", {}).get("slot_id") == "U16"
    and j.get("producer", {}).get("slot_id") == "L31"
    and j.get("required_inputs", [{}])[0].get("output_ref")
        == j.get("producer", {}).get("output_ref")
    for j in _proof_consumed),
    "proof join CONSUMED: L31 output ref consumed by U16",
    str([(j.get("producer", {}).get("output_ref"),
          j.get("consumer", {}).get("slot_id")) for j in _proof_consumed]))
_proof_obs = _proof_trace.get("observations", [])
check(any(o.get("outcome") == "WRONG_FEATURE" and not o.get("consumed")
          for o in _proof_obs)
      and any(o.get("outcome") == "RELEASED" and o.get("consumed")
              for o in _proof_obs),
      "proof: unrelated reading held (WRONG_FEATURE), matching reading released after restart")
proof_summary = {
    "returncode": proc.returncode,
    "case_id": _proof_trace.get("case_id"),
    "mode": _proof_trace.get("mode"),
    "status": _proof_trace.get("status"),
    "case_revision": _proof_trace.get("case_revision"),
    "cool_revision_after_hold": _proof_trace.get("cool_revision_after_hold"),
    "cool_revision_final": _proof_trace.get("cool_revision_final"),
    "decision_before": _proof_trace.get("decision_before"),
    "decision_after": _proof_trace.get("decision_after"),
    "joins": _proof_joins,
    "trace_path": str(_proof_trace_path),
    "full_trace": _proof_trace,
}

# ---------------------------------------------------------------------------
# Trace + summary
# ---------------------------------------------------------------------------
set_stage("trace-write")
fresh_trace = {
    "candidate": "44.1.0",
    "label": "replay",
    "run": "pi5-precision-105",
    "environment": {
        "machine": platform.machine(), "system": platform.system(),
        "release": platform.release(), "python": platform.python_version(),
        "executable": sys.executable,
        "code_under_test": str(Path(build040_runtime.__file__).resolve()),
        "wheel": str(WHEEL),
        "hardware_model": HW_MODEL,
        "cpu_arch": platform.machine(),
        "build": "44.1.0",
        "exit_code": 0 if not FAILURES else 1,
    },
    "host_type": HOST_TYPE,
    "host_test": "PASS" if not FAILURES else "FAIL",
    "pi_run": ("PASS" if not FAILURES else "FAIL")
        if HOST_TYPE == "raspberry_pi_5" else "NOT_RUN",
    "note": ("Host test outcome (host_test) and Pi status (pi_run) are separate "
             "fields. host_test is PASS/FAIL for whatever machine ran this script. "
             "pi_run is PASS/FAIL only when the hardware model identifies a "
             "Raspberry Pi 5 (device-tree model); on any other host it is NOT_RUN. "
             "CPU architecture alone cannot identify a Pi 5."),
    "precision_105": {
        "challenge_id": challenge["id"], "positions_total": len(positions),
        "anchors": len(anchors), "open_positions": len(open_specs),
        "anchors_preserved": anchor_ok, "open_filled_from_replay": len(open_specs) if open_ok else 0,
        "generation_source": trace_exec["generation_source"],
        "external_ai_used": trace_exec["external_ai_used"],
        "offline_blocked": off_blocked,
        "offline_block_stage": "generation (after 14 anchor writes + 13 L22 checks + 1 U09 check + 14 anchor reads)",
        "memory_writes": trace_exec["memory_writes"],
        "memory_reads": trace_exec["memory_reads"],
        "l22_chronology_checks": trace_exec["l22_chronology_checks"],
        "u09_events": len(u09_events),
        "elapsed_seconds": round(elapsed, 3),
        "grader": grade,
        "response_path": "response_105.json",
    },
    "function_calls": REPLAY_CALLS,
    "offline_function_calls": OFFLINE_CALLS,
    "registry_calls_count": len(REPLAY_REGISTRY),
    "generic_registry_probe": generic_probe,
    "build040_comparison": {
        "reference_run": hist["run"],
        "expected_per_function_invocations": expected_counts,
        "observed_per_function_invocations": observed_counts,
        "missing": missing, "extra": extra,
        "expected_inner_order_per_check": EXPECTED_ORDER,
        "observed_inner_orders_match": order_ok,
        "first_check_l03_input_digests_match": digests_ok,
        "l22_decisions_match_expected_answers": l22_answers == expected_answers,
        "l22_pathway_total_ms_build040": hist["l22_pathway_total_ms"],
    },
    "memory_accesses": mem_accesses,
    "warm_staging": warm_stage,
    "bundled_tests": bundled,
    "four_modes": four_modes,
    "proof_43": proof_summary,
    "failures": FAILURES,
    "notes": NOTES,
    "result": "PASS" if not FAILURES else "FAIL",
}
def _json_default(o):
    if hasattr(o, "_asdict"):  # e.g. crampu Pointer namedtuples
        return o._asdict()
    return str(o)


trace_path = WORK / "pi5_trace_4410.json"
set_stage("done")
trace_path.write_text(json.dumps(fresh_trace, indent=2, default=_json_default))
log(f"\n== result: {fresh_trace['result']} ==")
log(f"failures: {FAILURES or 'none'}")
log(f"notes: {len(NOTES)} (see trace)")
log(f"fresh trace: {trace_path}")
sys.exit(0 if not FAILURES else 1)
PYEOF
RC=$?
set -e
echo "----"
echo "driver exit code: $RC"
echo "raw log: $WORKDIR/pi5_test_log.txt"
if [ "$RC" -ne 0 ]; then
  # The driver stopped early: preserve the raw log and write a failure
  # summary naming the stage that stopped (recorded in stage.txt).
  STAGE_NAME="$(cat "$WORKDIR/stage.txt" 2>/dev/null || echo unknown)"
  python3 - "$WORKDIR" "$RC" "$STAGE_NAME" <<'PYEOF2'
import json, sys
from pathlib import Path
work = Path(sys.argv[1]); rc = int(sys.argv[2]); stage = sys.argv[3]
log = work / "pi5_test_log.txt"
tail = log.read_text(errors="replace").strip().splitlines()[-15:] if log.exists() else []
summary = {"result": "FAIL", "stage_stopped": stage, "driver_exit_code": rc,
           "raw_log": str(log), "last_log_lines": tail}
(work / "failure_summary.json").write_text(json.dumps(summary, indent=2))
print(f"FAILURE SUMMARY: stopped at stage {stage} (driver exit {rc}); raw log preserved at {log}")
PYEOF2
  echo "failure summary: $WORKDIR/failure_summary.json"
  exit $RC
fi
echo "fresh trace: $WORKDIR/pi5_trace_4410.json"
echo "response: $WORKDIR/response_105.json"
exit $RC
