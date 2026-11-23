import secrets
ADMIN = secrets.token_hex(24)
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from build040_runtime.online_ai import OnlineAIGateway, ReplayProvider
from build040_runtime.precision_315 import Precision315Constructor
from build040_runtime.standalone import local_runtime
from realcog.ph6_brains_2208 import Block, Rejected

fixture, prior, out = map(Path, sys.argv[1:])
draft = {str(r["position"]): r["text"] for r in json.loads(prior.read_text())["positions"]}

class AuditReplay(ReplayProvider):
    def generate(self, prompt, **kwargs):
        positions = json.loads(prompt)["open_positions"]
        self.record(prompt, json.dumps({p: draft[p] for p in positions}, ensure_ascii=False))
        return super().generate(prompt, **kwargs)

def save(name, value):
    (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    stage = {}
    with local_runtime(root, ADMIN) as (ctrl, closet, _):
        addresses = set(range(315))
        for address in sorted(addresses):
            ctrl.register_block_owner(address, "L01", ADMIN)
        closet.suit.authorize_agent("L01", {"memory.write"}, allowed_block_ids=addresses, max_steps=315)
        adapter = closet.suit.adapter
        original = adapter.write_and_sync
        def write(agent, family, address, payload, **kw):
            def observe(when):
                if when == "after_hot_before_warm":
                    stage["hot"] = ctrl.inspect(ADMIN)
                elif when == "after_warm_before_cool":
                    staged = next(ctrl.warm.glob("*.json"))
                    stage["warm"] = json.loads(staged.read_text())
                elif when == "after_cool_before_mirror":
                    _, stage["cool"] = ctrl.load_from_cool_with_reference(address, agent)
                    stage["before_mirror"] = ctrl.inspect(ADMIN)
            if address == 0:
                kw["fault_hook"] = observe
            return original(agent, family, address, payload, **kw)
        with patch.object(adapter, "write_and_sync", side_effect=write):
            result = Precision315Constructor(closet.suit, OnlineAIGateway(AuditReplay())).construct(fixture)
        stage["mirror"] = result["writes"][0]["mirror_reference"]
        stage["after_mirror"] = ctrl.inspect(ADMIN)
    with local_runtime(root, ADMIN) as (ctrl, closet, _):
        closet.suit.authorize_agent("L01", {"memory.read"}, allowed_block_ids=addresses, max_steps=315)
        reads = []
        for index, row in enumerate(result["positions"]):
            got = closet.suit.execute_memory_read("L01", "BIG49", index)
            assert got["payload"]["text"] == row["text"]
            assert got["cool_reference"] == result["writes"][index]["cool_reference"]
            reads.append(got["cool_reference"])
        stage["reopen"] = reads[0]
    warm = stage["warm"].copy()
    checksum = warm.pop("checksum")
    assert hashlib.sha256(json.dumps(warm, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest() == checksum
    assert warm["txid"] == stage["cool"]["txid"] == stage["reopen"]["txid"]
    save("native315-response.json", {k: result[k] for k in ("id", "positions")})
    save("native315-trace.json", {"provider":result["provider_trace"], "writes": result["writes"],
                                 "reopen_reads": reads, "sample_stages":stage})

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    samples = []
    with local_runtime(root, ADMIN) as (ctrl, closet, _):
        ad = closet.suit.adapter
        for family, offset, store in (("BIG49", 0, ad.big), ("SMALL49", 1600, ad.small)):
            index = 0
            for bucket, (size, cap) in store.limits.items():
                first = index
                for _ in range(size):
                    ctrl.register_block_owner(offset + index, "L01", ADMIN)
                    ad.write_and_sync("L01", family, index, {"content":"x" * cap}, bucket=bucket)
                    index += 1
                assert store.count(bucket) == size
                try:
                    store.put(Block("overflow-" + bucket, "L01", bucket, "x"), principal="L01")
                except Rejected as error:
                    assert str(error) == "CAPACITY_EXHAUSTED"
                else:
                    raise AssertionError(bucket)
                before = ctrl.inspect(ADMIN)
                try:
                    ad.write_and_sync("L01", family, first, {"content":"y"}, bucket=bucket)
                except Rejected as error:
                    assert str(error) == "CAPACITY_EXHAUSTED"
                else:
                    raise AssertionError(bucket)
                assert before == ctrl.inspect(ADMIN)
                samples.append(dict(family=family,bucket=bucket,capacity=size,character_cap=cap,
                                    start=offset+first,end=offset+index-1,overflow="CAPACITY_EXHAUSTED"))
        assert ctrl.inspect(ADMIN)["committed"] == 2208
    with local_runtime(root, ADMIN) as (ctrl, closet, _):
        ad = closet.suit.adapter
        for sample in samples:
            store = ad.big if sample["family"] == "BIG49" else ad.small
            assert store.count(sample["bucket"]) == sample["capacity"]
            row, ref = ctrl.load_from_cool_with_reference(sample["start"], "L01")
            assert ref["revision"] == 1 and len(row["payload"]["content"]) == sample["character_cap"]
        final = ctrl.inspect(ADMIN)
        assert final["committed"] == 2208 and final["outbox_pending"] == 0
    save("saturation.json", {"buckets":samples,"reopen":final})
