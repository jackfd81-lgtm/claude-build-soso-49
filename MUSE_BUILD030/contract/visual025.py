"""Deterministic ordered grayscale observations and a Mask scene pathway."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json

from contract.agent025 import save_binding, source_ok
from contract.pathway025 import Trace, semantic
from realcog.memory025 import receipt
from realcog.ph6_brains_2208 import Big49, Block, Pointer, Rejected, Small49
from realcog.procedural_memory import canonical, identity

SIZE = 32 * 24
THRESHOLD = 22
OWNER = "L30"
TRANSITIONS = 4


def decode(text):
    if not isinstance(text, str) or len(text) != SIZE * 4 // 3:
        raise ValueError("a frame must be 32 x 24 grayscale bytes")
    pixels = base64.b64decode(text, validate=True)
    if len(pixels) != SIZE:
        raise ValueError("incorrect frame size")
    return pixels


def inventory(db, principal):
    db.execute("""CREATE TABLE IF NOT EXISTS visual_blocks_025 (
                principal TEXT, stream TEXT, sequence INTEGER, pointer TEXT,
                PRIMARY KEY (principal,stream,sequence,pointer))""")
    return [{"stream": stream, "seq": seq, **json.loads(row)} for stream, seq, row in db.execute(
        "SELECT stream,sequence,pointer FROM visual_blocks_025 WHERE principal=? ORDER BY stream,sequence,pointer",
        (principal,))]


def checkpoint(db, principal, key, payload, previous):
    inventory(db, principal)
    digest = identity([principal, key, payload])
    big = Big49(connection=db)
    small = Small49(connection=db)
    db.execute("SAVEPOINT scene_025")
    try:
        bp = big.put(Block("vb025-" + digest, OWNER, "cognitive", canonical(payload),
                           source_refs=("int:frame:" + payload["frame_sha256"],)), principal=OWNER)
        sp = small.put(Block("vs025-" + digest, OWNER, "active",
                             canonical({"big": bp.digest, "seq": payload["seq"]}),
                             source_refs=("int:" + bp.handle,)), principal=OWNER)
        for pointer in (bp, sp):
            db.execute("INSERT INTO visual_blocks_025 VALUES (?,?,?,?)",
                       (principal, key, payload["seq"], canonical(vars(pointer))))
    except Rejected as error:
        db.execute("ROLLBACK TO scene_025")
        if str(error) != "CAPACITY_EXHAUSTED":
            raise
        return previous, [{"operation": "skipped", "reason": "pool_capacity", "authority": 0}]
    finally:
        db.execute("RELEASE scene_025")
    return [vars(sp), vars(bp)], [dict(receipt(p, "write"), content_digest=p.digest) for p in (sp, bp)]


def prior(db, pointers):
    if not pointers:
        return None, []
    sp, bp = map(lambda row: Pointer(**row), pointers)
    link = Small49(connection=db).read(sp, principal=OWNER)
    data = Big49(connection=db).read(bp, principal=OWNER)
    if json.loads(link["content"])["big"] != bp.digest:
        raise ValueError("scene memory reference mismatch")
    return json.loads(data["content"])["frame_sha256"], [
        dict(receipt(p, "read"), function_id=None, content_digest=p.digest) for p in (sp, bp)]


def difference(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) // SIZE


def observation(source, time_ms, value):
    when = datetime(2000, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=time_ms)
    return {"source": source, "subject": "scene", "predicate": "state",
            "value": value, "event": when.isoformat()}


def analyze(db, principal, key, state, pixels, digest, seq, task, conf, trace_id):
    previous = decode(state["previous"]) if state.get("previous") else pixels
    motion = difference(previous, pixels)
    mean = sum(pixels) // SIZE
    measure = {"seq": seq, "time_ms": task["time_ms"], "mean_luma": mean, "adjacent_mad": motion,
               "frame_sha256": digest, "source": task["source"]}
    result = {"measurement": measure, "trace": [], "memory_accesses": [],
              "decision": {"status": "OBSERVED"}}
    if not conf["mask_enabled"]:
        return result
    if not state["continuous"]:
        signal = "bright" if mean >= 128 else "dim"
    elif not state.get("anchor"):
        state["anchor"] = task["gray"]
        signal = "initial"
    else:
        distance = difference(decode(state["anchor"]), pixels)
        pending = state.get("pending")
        if distance >= THRESHOLD and (pending is None or motion >= THRESHOLD):
            request = {"kind": "next_frame_confirmation", "after": seq, "frame_sha256": digest}
            state["pending"] = request
            result["decision"] = {"status": "HOLD", "request": request}
            trace = Trace(trace_id)
            trace.route("VISUAL_CONTINUITY", "next_frame", "HOLD", request)
            result["trace"] = trace.events
            return result
        signal = "change" if distance >= THRESHOLD else "stable"
        state.pop("pending", None)
        if signal == "change":
            state["anchor"] = task["gray"]
    problem = {"pathway": "B", "query": {"subject": "scene", "predicate": "state"}}
    trace = Trace(trace_id)
    if state.get("scene"):
        trace.route("VISUAL_CONTINUITY", "L03", "PASS", {"previous_measurement_sha256": state["scene_digest"]})
    rows = ([state["scene"]] if state.get("scene") else []) + [observation(task["source"], task["time_ms"], signal)]
    result["decision"] = semantic(problem, rows, trace, "A")
    result["trace"] = trace.events
    state["scene"] = rows[-1]
    state["scene_digest"] = identity(measure)
    if signal == "change" and state["stored"] < TRANSITIONS:
        link, receipts = prior(db, state.get("memory"))
        payload = {"seq": seq, "frame_sha256": digest, "previous_episode": link,
                   "policy_version": conf["version"], "mean_luma": mean}
        state["memory"], written = checkpoint(db, principal, key, payload, state.get("memory"))
        result["memory_accesses"] = receipts + written
        state["stored"] += int(any(row["operation"] == "write" for row in written))
    return result


def handle_media(db, principal, task, action, key, conf, state, trace_id):
    if action == "stream":
        source = task.get("source")
        if not source_ok(source) or type(task.get("continuous")) is not bool:
            raise ValueError("stream source and continuous flag required")
        state = {"source": source, "continuous": task["continuous"], "next": 0, "last_time": -1, "stored": 0}
        result = {"decision": {"status": "OPEN"}, "trace": []}
    elif state.get("closed"):
        raise ValueError("stream is closed")
    elif action == "close":
        state["closed"] = True
        pending = state.pop("pending", None)
        result = {"decision": {"status": "CLOSED", "unresolved_hold": pending}, "trace": []}
    else:
        time = task.get("time_ms")
        if task["seq"] != state["next"] or type(time) is not int or not state["last_time"] < time < 86400000:
            raise ValueError("out of order frame sequence or time")
        if task["source"] != state["source"]:
            raise ValueError("frame source does not match stream")
        pixels = decode(task.get("gray"))
        digest = hashlib.sha256(pixels).hexdigest()
        if digest != task.get("frame_sha256"):
            raise ValueError("frame digest mismatch")
        result = analyze(db, principal, key, state, pixels, digest, task["seq"], task, conf, trace_id)
        state.update(previous=task["gray"], next=state["next"] + 1, last_time=time)
    save_binding(db, principal, "stream", key, conf, state)
    return result
