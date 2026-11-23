"""Crash helper for Contract 001 test 7.

Opens a pipeline on a fresh root, runs one task by trial (which
writes a CANDIDATE experience block to Big49), then SIGKILLs
itself before any projection rebuild. The parent test then opens
a fresh pipeline on the same root and verifies the two
projections agree about what was learned.
"""
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.admission import Policy
from contract.harness import SOSO_MODULES
from contract.pipeline import Pipeline

ROOT = sys.argv[1]

TASK = {"task_id": "ct-crash-1", "signature": "disk_full",
        "family": "valve", "tags": ["valve", "firmware_v2", "stuck"],
        "conditions": {"firmware": "v2"},
        "requested_operation": "repair",
        "provenance": ("int:contract-test",)}

criteria = {"ct-crash-1": "P2"}
observations = {(("ct-crash-1", n)): (n == "P2") for n in
                ("P1", "P2", "P3", "P4", "P5", "P6", "P7",
                 "P8", "P9", "P10", "P11", "clear_cache",
                 "restart_svc", "rotate_logs", "wipe_disk", "reset_cfg")}

pipe = Pipeline(ROOT, Policy("contract-001-v1",
                             forbidden=("RC-FIX-WIPE-DISK",)),
                criteria=criteria, observations=observations,
                soso_modules=SOSO_MODULES)
rep = pipe.run(TASK)
assert rep["state"] == "RESOLVED" and rep["stage"] == "higher-trial", rep
# The experience block is durable in Big49. Die now, before any
# projection rebuild, so the parent can verify the rebuild.
sys.stdout.flush()
os.kill(os.getpid(), signal.SIGKILL)
