#!/bin/bash
# Run first on the Pi. Writes a host inspection report.
set -u
if [ "$#" -gt 1 ]; then
    echo "Usage: $0 [REPORT_PATH]" >&2
    exit 2
fi
PYTHON_BIN="${PYTHON_BIN:-python3}"
OUT="${1:-$(mktemp /tmp/muse030-pi-report.XXXXXX)}"
if [ "$#" -eq 1 ]; then
    (set -C; : > "$OUT") || exit 1
fi
{
echo "== date"; date -u
echo "== uname -a"; uname -a
echo "== arch"; uname -m
echo "== os-release"; cat /etc/os-release
echo "== python3"; "$PYTHON_BIN" --version; command -v python3.11 python3.12 python3.13 2>/dev/null
echo "== sqlite3 (python)"; "$PYTHON_BIN" -c "import sqlite3;print(sqlite3.sqlite_version)"
echo "== fcntl"; "$PYTHON_BIN" -c "import fcntl;print('ok')"
echo "== memory"; free -h
echo "== disk"; df -h /
echo "== storage device"; lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,ROTA 2>/dev/null
echo "== temp"; vcgencmd measure_temp 2>/dev/null || echo "vcgencmd n/a"
echo "== throttled (0x0 = clean)"; vcgencmd get_throttled 2>/dev/null || echo "vcgencmd n/a"
echo "== cpu"; lscpu | head -15
echo "== listening sockets"; ss -ltn 2>/dev/null | head -20
} > "$OUT" 2>&1 || exit 1
echo "Wrote $OUT"
"$PYTHON_BIN" - <<'PY'
import sys
import fcntl, sqlite3
ok = sys.version_info >= (3,11)
print("PYTHON >=3.11:", "OK" if ok else "FAIL -> install python3.11+ before continuing")
raise SystemExit(0 if ok else 1)
PY
