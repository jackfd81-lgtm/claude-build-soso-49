"""Generate the example candidate bound to the current build and fixture."""
import sys
from pathlib import Path
from sososuit42 import canonical, digest, govern_context, parse

context = parse(Path(__file__).with_name("context.json").read_bytes())
candidate = {
    "producer": "AI",
    "candidate_id": "example",
    "governed_state_digest": digest(govern_context(context)),
    "actions": ["REQUEST_READING"],
    "claims": [{"id": "int:review", "text": "Request a repeat reading.",
                "refs": ["ext:initial"]}],
    "ious": [{"id": "repeat", "type": "READING",
              "code": "IOU_AMBIGUOUS_READING", "description": "Repeat fixture reading."}],
}
sys.stdout.buffer.write(canonical(candidate) + b"\n")
