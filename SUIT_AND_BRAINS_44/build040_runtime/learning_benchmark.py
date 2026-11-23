"""Synthetic fixed-seed qualification of outcome-driven profile transfer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import secrets
import tempfile

from contract.functions025 import OPERATIONS

from .standalone import local_runtime

SEEDS = (31, 79, 173, 257, 677)
TASK_VERSION = "42.0-task/1"


def freeze_cases(seed: int, split: str, count: int = 8):
    offsets = {"training": 0, "holdout": 10_000, "novel": 20_000}
    rng = random.Random(seed + offsets[split])
    size = 6 if split == "novel" else 4
    for case in range(count):
        rows = list(range(size))
        rng.shuffle(rows)
        weak = f"a_{split}_{case}"
        balanced = f"z_{split}_{case}"
        predictions = {
            f"row-{i}": {weak: i == rows[0], balanced: i in rows[:size // 2]}
            for i in range(size)
        }
        yield predictions, f"row-{rng.randrange(size)}"


def measure(candidates, truth, feature):
    observed = {feature: candidates[truth][feature]}
    return len(OPERATIONS["U13"](candidates, observed)["remaining"])


def run_benchmark():
    report = {"design": "synthetic-binary-partition/1", "provider": "offline",
              "metric": "mean candidates remaining after one observation (lower is better)",
              "cases_per_seed_per_split": 8, "seeds": []}
    for seed in SEEDS:
        training = tuple(freeze_cases(seed, "training"))
        suites = {name: tuple(freeze_cases(seed, name)) for name in ("holdout", "novel")}
        admin = secrets.token_hex(24)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with local_runtime(root, admin) as (ctrl, closet, learning):
                for address in (0, 1):
                    ctrl.register_block_owner(address, "L01", admin)
                closet.suit.authorize_agent("L01", {"task.run", "memory.read", "memory.write"},
                                          allowed_block_ids={0, 1}, max_steps=256)
                history = []
                closet.submit_task("training-feedback", "Collect simulated training observations")
                for i, (predictions, truth) in enumerate(training):
                    for feature, profile in OPERATIONS["U11"](predictions, {}).items():
                        outcome = {"before": len(predictions), "after": measure(predictions, truth, feature),
                                   "profile": profile}
                        history.append(outcome)
                        closet.continuity.record_event(
                            "training-feedback", "OBSERVATION", "synthetic_oracle",
                            {"case": i, "feature": feature, **outcome},
                        )
                closet.continuity.complete_session("training-feedback")
                example, _ = training[0]
                learned = learning.assist_and_learn("training-update", "L01", example, {}, history)
                closet.suit.execute_memory_write("L01", "BIG49", 1, {"learned_profile": [1, 3]})

                def evaluate(active_closet, split, label, block):
                    total = 0
                    for i, (predictions, truth) in enumerate(suites[split]):
                        request = {"version": TASK_VERSION, "operation": "select_discriminator",
                                   "candidates": predictions, "observed": {}}
                        if block is not None:
                            request["memory_block"] = ["BIG49", block]
                        result = active_closet.run_task(f"{split}-{label}-{i}", "L01", request)
                        total += measure(predictions, truth, result["selected_feature"])
                    return total / len(suites[split])

                result = {"seed": seed, "learned_profile": learned["learned_profile"],
                          "training_feedback": history,
                          "holdout": {label: evaluate(closet, "holdout", label, block)
                                      for label, block in (("cold", None), ("learned", 0), ("wrong", 1))}}
            with local_runtime(root, admin) as (_, closet, _):
                closet.suit.authorize_agent("L01", {"task.run", "memory.read"},
                                          allowed_block_ids={0}, max_steps=128)
                result["holdout"]["reopened"] = evaluate(closet, "holdout", "reopened", 0)
                result["novel"] = {label: evaluate(closet, "novel", label, block)
                                   for label, block in (("cold", None), ("learned", 0))}
        report["seeds"].append(result)
    report["interpretation"] = (
        "Generated training feedback and held-out outcome scoring use independent fixed seeds. "
        "Novel cases change cardinality from four to six while retaining the binary-partition mechanism. "
        "The profile is exact-match by partition shape. Generalization beyond this mechanism is unqualified."
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(run_benchmark(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
