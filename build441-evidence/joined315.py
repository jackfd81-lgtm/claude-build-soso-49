import collections
import json
from pathlib import Path
import secrets
import sys
import tempfile
from unittest.mock import patch

from build040_runtime.investigation import Investigation, case_block_id
from build040_runtime.standalone import local_runtime
from contract.slot_specs import describe

fixture_path, response_path, out = map(Path, sys.argv[1:])
fixture = json.loads(fixture_path.read_text())
response = json.loads(response_path.read_text())
admin = secrets.token_hex(24)
trace = []
cases = []

with tempfile.TemporaryDirectory() as tmp, local_runtime(Path(tmp), admin) as (ctrl, closet, _):
    addresses = {case_block_id(i) for i in range(15)}
    for address in sorted(addresses):
        ctrl.register_block_owner(address, "L01", admin)
    suit = closet.suit
    suit.authorize_agent("L01", {"memory.read", "memory.write", "task.run"},
                        allowed_block_ids=addresses, max_steps=1024, max_payload_bytes=65536)
    inv = Investigation(closet, "L01")
    original_write = suit.execute_memory_write
    original_read = suit.execute_memory_read

    def write(agent, family, index, payload, **kwargs):
        commit, pointer = original_write(agent, family, index, payload, **kwargs)
        trace.append({"case": name, "operation":"write", "family":family, "local":index,
                      "cool":commit["cool_reference"], "mirror":commit["mirror_reference"]})
        return commit, pointer

    def read(agent, family, index):
        row = original_read(agent, family, index)
        trace.append({"case":name, "operation":"read", "family":family, "local":index,
                      "cool":row["cool_reference"]})
        return row

    with patch.object(suit, "execute_memory_write", side_effect=write), patch.object(
            suit, "execute_memory_read", side_effect=read):
        for segment in fixture["segments"]:
            name = f"replayed-segment-{segment['segment']:02}"
            rows = [{"source":"recorded-draft", "subject":f"position-{row['position']}",
                     "predicate":"state", "value":row["text"], "scope":segment["language"],
                     "event":"2026-01-01T00:00:00Z"}
                    for row, spec in zip(response["positions"], fixture["positions"])
                    if spec["segment"] == segment["segment"] and spec["kind"] == "OPEN"]
            inv.start_case(name, mode="joined_lower_first", goal=segment["objective"],
                           evidence_snapshot={"segment":segment["segment"]}, scope=["conclusion"])
            case = inv.execute_mode(name, observations=rows,
                                    hypotheses={"H1":{"conclusion":"supported"},
                                                "H2":{"conclusion":"open"}}, observed={})
            assert case["status"] == "COMPLETED" and case["joins"][0]["status"] == "CONSUMED"
            low = next(i for i in case["invocations"] if i["slot_id"] == "L31")
            high = next(i for i in case["invocations"] if i["slot_id"] == "U16")
            assert low["output_ref"] == high["input_ref"]
            cases.append(case)
    allocation = ctrl.inspect(admin)
slots = [describe(f"{tier}{index:02}").to_dict()
         for tier in ("L", "U") for index in range(1, 50)]
(out / "joined315-trace.json").write_text(json.dumps({
    "cases":cases,"memory":trace,"allocation":allocation,"slot_map":slots},
    ensure_ascii=False, indent=2))
print(collections.Counter(i["slot_id"] for c in cases for i in c["invocations"]))
print(collections.Counter(x["operation"] for x in trace), allocation)
