#!/usr/bin/env bash
set -euo pipefail

# Raspberry Pi 5 CRAM-PU + Build 030.1 preflight, storage setup, and config validation
PYTHON_BIN="${PYTHON_BIN:-python3}"
MUSE_ROOT="${MUSE_ROOT:-/opt/muse030_1}"
STATE_ROOT="${STATE_ROOT:-/var/lib/muse030_1}"
WARM_DIR="${WARM_DIR:-/var/lib/muse030_1/warm}"
COOL_DIR="${COOL_DIR:-/var/lib/muse030_1/cool}"
BRAINS_DIR="${BRAINS_DIR:-/var/lib/muse030_1/brains}"
LOG_DIR="${LOG_DIR:-/var/log/muse030_1}"
MIN_FREE_MB="${MIN_FREE_MB:-512}"

command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "ERROR: Python not found: $PYTHON_BIN" >&2; exit 1; }
"$PYTHON_BIN" -c "import sys, sqlite3, fcntl; assert sys.version_info >= (3, 11)" || {
  echo "ERROR: Python >= 3.11 with sqlite3 and fcntl required" >&2; exit 1;
}

mkdir -p "$WARM_DIR" "$COOL_DIR" "$BRAINS_DIR" "$LOG_DIR"
chmod 700 "$WARM_DIR" "$COOL_DIR" "$BRAINS_DIR"

# Disk space check (do not assume /tmp is tmpfs)
avail_mb=$(df -Pm "$STATE_ROOT" | awk 'NR==2 {print $4}')
if [ "${avail_mb:-0}" -lt "$MIN_FREE_MB" ]; then
  echo "ERROR: Insufficient disk space under $STATE_ROOT (${avail_mb}MB < ${MIN_FREE_MB}MB)" >&2
  exit 1
fi

# Validate filesystem type & basic write/fsync test
fs_type=$(df -PT "$STATE_ROOT" | awk 'NR==2 {print $2}')
echo "INFO: STATE_ROOT=$STATE_ROOT fs=$fs_type avail_mb=$avail_mb"

"$PYTHON_BIN" - <<'PY'
import os, tempfile
from pathlib import Path
for d in ("WARM_DIR", "COOL_DIR", "BRAINS_DIR"):
    p = Path(os.environ.get(d, "/tmp"))
    testf = p / ".cram_preflight.tmp"
    fd = os.open(testf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, b"ok")
    os.fsync(fd)
    os.close(fd)
    testf.unlink()
print("INFO: Storage write+fsync preflight OK")
PY
