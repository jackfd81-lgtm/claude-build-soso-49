"""Crash helper for Contract 002: SIGKILL during step 3's first
trial attempt (after the ACTION_INTENT trace, before the result
is recorded). Usage: python3 crash_multistep_ambiguous.py <root>
"""
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contract.harness import Harness, DEMO_TASK, register_demo


def _kill(task, entry):
    if task.task_id.endswith(".s3-synonym"):
        os.kill(os.getpid(), signal.SIGKILL)


def main():
    root = sys.argv[1]
    h = Harness(root=root, generations_threshold=0.05,
                post_intent_hook=_kill)
    h.train_baseline()
    register_demo(h)
    h.runner.run(DEMO_TASK)
    # Unreachable when the crash fires (step 3 always attempts P7).
    print("no crash fired")
    h.close()


if __name__ == "__main__":
    main()
