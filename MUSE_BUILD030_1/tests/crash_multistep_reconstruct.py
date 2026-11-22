"""Crash helper for Contract 002: SIGKILL after step 3's pipeline
run finishes but before its journal record is written (the
reconstructable case). Usage:
    python3 crash_multistep_reconstruct.py <root>
"""
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.harness import Harness, DEMO_TASK, register_demo


def _kill(point, idx, classification):
    if point == "after_step_run" and idx == 2:
        os.kill(os.getpid(), signal.SIGKILL)


def main():
    root = sys.argv[1]
    h = Harness(root=root)
    h.train_baseline()
    register_demo(h)
    h.runner.run(DEMO_TASK, crash_hook=_kill)
    print("no crash fired")
    h.close()


if __name__ == "__main__":
    main()
