"""Offline qualification of recovered Suit evidence against Build 027."""
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VENDOR = ROOT / "suit61" / "vendor"


def replay_monitor_suite():
    with TemporaryDirectory(prefix="build027-monitor-") as work:
        target = Path(work)
        for version in ("3_2_2", "3_2_3"):
            filename = f"ph6_soso_suit_monitor_v{version}.py"
            shutil.copyfile(VENDOR / filename, target / filename)
        for source in sorted((HERE / "v323").iterdir()):
            if source.is_file():
                shutil.copyfile(source, target / source.name)
        result = subprocess.run(
            [sys.executable, "test_ph6_soso_suit_monitor_v3_2_3.py"],
            cwd=target, check=True, capture_output=True, text=True,
        )
    match = re.search(r"^(\d+)/(\d+) checks passed$", result.stdout, re.MULTILINE)
    if match is None or match.groups() != ("39", "39"):
        raise ValueError(f"Unexpected monitor qualification output: {result.stdout}")
    return result.stdout.strip().splitlines()[-1]


def load_schemas():
    with (HERE / "frozen-source-manifest.csv").open(newline="", encoding="utf-8") as file:
        entries = list(csv.DictReader(file))
    schemas = {}
    for row in entries:
        if not row["source_path"].startswith("schemas/"):
            continue
        name = Path(row["source_path"]).name
        data = (HERE / "v40" / name).read_bytes()
        if (
            len(data) != int(row["bytes"])
            or hashlib.sha256(data).hexdigest() != row["sha256"]
            or hashlib.blake2b(data, digest_size=32).hexdigest() != row["blake2b256"]
        ):
            raise ValueError(f"Frozen manifest mismatch for {name}")
        schemas[name] = json.loads(data)
    if len(schemas) != 9:
        raise ValueError(f"Expected nine recovered schemas, got {len(schemas)}")
    return schemas


def compare_build027_receipt(schemas):
    from jsonschema import Draft202012Validator
    from eval026.run import run

    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    with TemporaryDirectory(prefix="build027-receipts-") as work:
        audit = run(Path(work))["heldout"]["monitor_audit"]
    if not audit:
        raise ValueError("Build 027 produced no audit receipts")
    validator = Draft202012Validator(schemas["disposition-record-1.0.json"])
    outcomes = []
    for row in audit:
        record = row["disposition"]
        if row["authority"] != "AUTH:0" or row["execution_authorization"] != "NOT_GRANTED":
            raise ValueError("Build 027 receipt crossed its authority boundary")
        if row["source"]["trace_digest"] != record["evidence_ref"]["sha256"]:
            raise ValueError("Build 027 disposition lost its trace digest binding")
        errors = list(validator.iter_errors(record))
        if not errors:
            raise ValueError("Expected a version mismatch for the legacy record")
        outcomes.append((row["source"]["kind"], record, errors))
    first_kind, first_record, first_errors = outcomes[0]
    return {
        "receipts_compared": len(outcomes),
        "legacy_authority_and_trace_checks": "PASS",
        "direct_4_0_disposition_compatibility": "REJECTED",
        "example_receipt_kind": first_kind,
        "example_legacy_keys": sorted(first_record),
        "example_missing_4_0_fields": sorted(
            set(validator.schema["required"]) - set(first_record)
        ),
        "example_schema_error_codes": sorted({error.validator for error in first_errors}),
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-runtime", action="store_true",
                        help="Run the Build 027 experiment and compare receipts using jsonschema")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    schemas = load_schemas()
    result = {
        "vendored_v3_2_3_suite": replay_monitor_suite(),
        "v4_0_manifest_exact_schema_files": len(schemas),
    }
    if args.compare_runtime:
        result["live_receipt_schema_comparison"] = compare_build027_receipt(schemas)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
