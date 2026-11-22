"""Run Build 030 qualification against disposable state, retaining machine-readable evidence."""
import argparse
import json
from pathlib import Path
import platform
import re
import sqlite3
import subprocess
import sys

from qualification_030.continuity import require

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def read(path):
    return json.loads(path.read_text())


def without_timing(value):
    if isinstance(value, dict):
        return {k: without_timing(v) for k, v in value.items() if k != "elapsed_ns"}
    if isinstance(value, list):
        return [without_timing(v) for v in value]
    return value


def verify(output):
    checks = {}

    def run(name, args, expected_pattern=None):
        result = subprocess.run([PY, *map(str, args)], cwd=ROOT, text=True, capture_output=True, timeout=180)
        (output / f"{name}.stdout").write_text(result.stdout)
        (output / f"{name}.stderr").write_text(result.stderr)
        require(result.returncode == 0, f"{name} failed with exit {result.returncode}")
        if expected_pattern:
            require(re.search(expected_pattern, result.stdout + result.stderr) is not None,
                    f"{name} did not report the expected count")
        checks[name] = "PASS"
        return result.stdout

    def check(name, passed):
        require(passed, name)
        checks[name] = "PASS"

    try:
        recovery = ROOT / "evidence027/suit_recovery"
        run("phase_d", [recovery / "phase_d_candidate/phase_d_candidate.py", "self-test"], r"Ran 44 tests\b")
        run("suit42", ["-m", "unittest", "discover", "-s", "qualification_029/suit42/tests"], r"Ran 8 tests\b")
        run("legacy_monitor", [recovery / "verify.py"], r"39/39 checks passed")
        run("continuity", ["-m", "qualification_030.continuity"])
        t12 = output / "t12.json"
        run("t12", ["-m", "qualification_030.t12_oracle", t12])
        report = read(t12)
        check("t12_all_cases", report["assertion_status"] == "PASS" and len(report["cases"]) == 5)

        state = output / ".memory-work"
        state.mkdir(mode=0o700)
        import shutil
        try:
            module = ["-m", "qualification_030.persist_test_two_process"]
            run("memory_write", [*module, "write", state / "store"])
            result = json.loads(run("memory_read", [*module, "read", state / "store"]))
            check("memory_restart_and_isolation", all(
                result[f"{pool}_restart_read"] == "alice-secret" and all(
                    result[f"{pool}_{key}"] == "PermissionError"
                    for key in ("cross_principal", "auth1", "authfalse", "badtoken")
                ) for pool in ("big", "small")))
        finally:
            shutil.rmtree(state)

        path025 = output / "eval025.json"
        run("eval025", ["-m", "eval025.run", path025])
        arms = read(path025)["arms"]
        expected = {**dict.fromkeys("ADG", (4, 8)), **dict.fromkeys("BCEFHK", (4, 11)), "I": (0, 0), "J": (0, 4)}
        check("eval025_arm_parity", {k: (v["correct"], v["requests"]) for k, v in arms.items()} == expected)

        modes = {}
        for name, flags in (("baseline", []), ("shadow", ["--shadow029"])):
            paths = [output / f"eval026_{name}_{i}.json" for i in (1, 2)]
            for path in paths:
                run(path.stem, ["-m", "eval026.run", path, *flags])
            values = [without_timing(read(path)) for path in paths]
            check(f"eval026_{name}_repeatability", values[0] == values[1])
            modes[name] = values[0]
        off, on = modes["baseline"], modes["shadow"]
        heldout = on["heldout"]
        rows = heldout.pop("shadow_receipts")
        pending = heldout.pop("shadow_pending_ious")
        delivered = heldout.pop("shadow_delivery")
        ids = {row["receipt"]["seal"]["digest"]["sha256"] for row in rows}
        check("shadow_advisory_parity", off == on and len(rows) == len(pending) == len(delivered) == len(ids) == 12
              and all(row["status"] == "ACKED" for row in rows)
              and ids == {row["decision_id"] for row in pending})

        adv002 = output / "adv002"
        run("adv002", ["-m", "qualification_030.adv002", "--output", adv002])
        report = read(adv002 / "summary.json")
        check("adv002_fixture_and_restart", report["defense_trials"] == report["challenger_trials"] == 1200
              and set(report["challenger_profile_distribution"].values()) == {400}
              and bool(report["realcog_sqlite_memory_M0_M3"]["recalled_method_id"]))
        adv003 = output / "adv003.json"
        run("adv003", ["-m", "qualification_030.adv003", "--output", adv003])
        report = read(adv003)
        archived = read(ROOT / "qualification_030/fixtures/adv003_archived.json")
        check("adv003_archived_fixture_parity",
              all(report[key] == archived[key] for key in ("chatgpt_on_m2c", "muse_on_c2m")))
    except Exception as exc:
        status, error = "FAIL", str(exc)
    else:
        status, error = "PASS", None
    summary = {"scope": "Build 030 source-tree qualification", "status": status,
               "python": platform.python_version(), "machine": platform.machine(),
               "sqlite": sqlite3.sqlite_version, "checks": checks, "error": error}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if status == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    return verify(output)


if __name__ == "__main__":
    raise SystemExit(main())
