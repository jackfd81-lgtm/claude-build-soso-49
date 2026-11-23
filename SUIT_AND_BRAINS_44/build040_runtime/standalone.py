"""Local Suit and Brains 42 candidate entry point and offline wiring check."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import json
from pathlib import Path
import secrets
import tempfile
import uuid

from contract.brains_wiring import region_for_index
from contract.cross_specialty import CROSS_OPERATIONS
from contract.functions025 import OPERATIONS
from crampu.build030_bridge import Merged49BrainsCramAdapter
from crampu.controller import CRAMError, CRAMPUMemoryController

from .closet import SoSoCloset
from .learning_loop import AssistedLearningLoop
from .soso_continuity import SoSoContinuity
from .suit_runtime import SoSoSuitRuntime


def function_registry():
    implemented = OPERATIONS.keys() | CROSS_OPERATIONS.keys() | {"U09"}
    return [
        {
            "id": fid, "layer": layer, "region": region,
            "status": "IMPLEMENTED" if fid in implemented else "PASSTHROUGH",
            "implementation": (
                "contract.functions025" if fid in OPERATIONS else
                "contract.cross_specialty" if fid in CROSS_OPERATIONS else
                "contract.disposition_engine" if fid == "U09" else
                "contract.brains_wiring"
            ),
        }
        for fid, layer, region in (
            (f"{prefix}{index:02}", layer, region_for_index(index)[1])
            for prefix, layer in (("L", "LOWER_49"), ("U", "HIGHER_49"))
            for index in range(1, 50)
        )
    ]


@contextmanager
def local_runtime(root: Path, admin: str):
    with ExitStack() as stack:
        controller = CRAMPUMemoryController(root / "warm", root / "cool", admin)
        stack.callback(controller.close)
        adapter = Merged49BrainsCramAdapter(controller, root / "brains")
        stack.callback(adapter.close)
        continuity = SoSoContinuity(root / "continuity.sqlite3")
        stack.callback(continuity.close)
        suit = SoSoSuitRuntime(adapter)
        yield controller, SoSoCloset(continuity, suit), AssistedLearningLoop(continuity, suit)


def demo():
    admin = secrets.token_hex(24)
    cases = {"H1": {"a_noise": 1, "z_signal": "hit"},
             "H2": {"a_noise": 2, "z_signal": "miss"},
             "H3": {"a_noise": 3, "z_signal": "miss"}}
    request = {"version": "42.0-task/1", "operation": "select_discriminator",
               "candidates": cases, "observed": {}}
    with tempfile.TemporaryDirectory(prefix="suit-brains-") as directory:
        root = Path(directory)
        with local_runtime(root, admin) as (ctrl, closet, learning):
            for address in (0, 1600):
                ctrl.register_block_owner(address, "L01", admin)
            closet.suit.authorize_agent("L01", {"task.run", "memory.read", "memory.write"},
                                        allowed_block_ids={0, 1600})
            cold = closet.run_task("cold", "L01", request)
            trained = learning.assist_and_learn(
                "train", "L01", cases, {}, [{"before": 3, "after": 1, "profile": [1, 2]}]
            )
            closet.submit_task("small-write", "Exercise Small49 wiring")
            closet.invoke_agent("small-write", "L01", "SMALL49", 0, {"content": "small"})
        with local_runtime(root, admin) as (ctrl, closet, _):
            closet.suit.authorize_agent("L01", {"task.run", "memory.read"}, allowed_block_ids={0, 1600})
            learned = closet.run_task("reopened", "L01", {**request, "memory_block": ["BIG49", 0]})
            small = closet.request_memory("small-write", "L01", "SMALL49", 0)
            closet.suit.adapter.verify_all()
            return {"authority": 0, "cold": cold, "trained": trained, "reopened": learned,
                    "small49": small["payload"], "pending_mirrors": ctrl.inspect(admin)["outbox_pending"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="exercise Suit, Brains, Big49 and Small49")
    sub.add_parser("registry", help="print the 98-slot source registry")
    run = sub.add_parser("run", help="run one local request in disposable state")
    run.add_argument("request", type=Path)
    args = parser.parse_args()
    if args.command == "demo":
        result = demo()
    elif args.command == "registry":
        result = function_registry()
    else:
        try:
            with args.request.open("rb") as request_file:
                raw = request_file.read(16_385)
            if len(raw) > 16_384:
                raise ValueError("Request file exceeds 16384 bytes")
            request = json.loads(raw)
            with tempfile.TemporaryDirectory(prefix="suit-brains-") as directory:
                with local_runtime(Path(directory), secrets.token_hex(24)) as (_, closet, _):
                    closet.suit.authorize_agent("L01", {"task.run"}, max_steps=1)
                    result = closet.run_task(uuid.uuid4().hex, "L01", request)
        except (ValueError, OSError, RecursionError, CRAMError) as exc:
            parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))



if __name__ == "__main__":
    main()
