#!/bin/bash
# Run FIRST on the Pi. Read-only. Writes pi_report.txt next to this script.
OUT="$(dirname "$0")/pi_report.txt"
{
echo "== date"; date -u
echo "== uname -a"; uname -a
echo "== arch"; uname -m
echo "== os-release"; cat /etc/os-release
echo "== python3"; python3 --version; command -v python3.11 python3.12 python3.13 2>/dev/null
echo "== sqlite3 (python)"; python3 -c "import sqlite3;print(sqlite3.sqlite_version)"
echo "== fcntl"; python3 -c "import fcntl;print('ok')"
echo "== memory"; free -h
echo "== disk"; df -h /
echo "== storage device"; lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,ROTA 2>/dev/null
echo "== temp"; vcgencmd measure_temp 2>/dev/null || echo "vcgencmd n/a"
echo "== throttled (0x0 = clean)"; vcgencmd get_throttled 2>/dev/null || echo "vcgencmd n/a"
echo "== cpu"; lscpu | head -15
echo "== listening sockets"; ss -ltn 2>/dev/null | head -20
} > "$OUT" 2>&1
echo "Wrote $OUT"
python3 - <<'PY'
import sys
ok = sys.version_info >= (3,11)
print("PYTHON >=3.11:", "OK" if ok else "FAIL -> install python3.11+ before continuing")
PY
