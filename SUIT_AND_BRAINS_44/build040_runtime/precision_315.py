"""Native multilingual Precision-315 construction through the advisory gateway."""

import json
from pathlib import Path

from .precision_105 import BlockedGenerationError


def _valid_line(text):
    return (isinstance(text, str) and len(text.splitlines()) == 1
            and 7 <= sum(any(c.isalpha() or c.isdecimal() for c in part)
                         for part in text.split()) <= 15)


def _fixture(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data["positions"]
    segments = data["segments"]
    if (not isinstance(data["id"], str) or not data["id"]
            or len(rows) != 315 or len(segments) != 15):
        raise ValueError("Invalid Precision-315 fixture shape")
    for index, segment in enumerate(segments):
        start = index * 21 + 1
        locked = [start, start + 9, start + 20]
        if (segment["segment"] != index + 1 or segment["start"] != start
                or segment["end"] != start + 20 or segment["locked_positions"] != locked
                or segment["language"] not in {"en", "es", "fr", "ru"}):
            raise ValueError(f"Invalid segment {index + 1}")
        for position in range(start, start + 21):
            row = rows[position - 1]
            anchor = position in locked
            if (row["position"] != position or row["segment"] != index + 1
                    or row["language"] != segment["language"]
                    or row["kind"] != ("ANCHOR" if anchor else "OPEN")
                    or (anchor and not _valid_line(row.get("text")))
                    or (not anchor and row.get("text") not in (None, ""))):
                raise ValueError(f"Invalid fixture position {position}")
    return data


class Precision315Constructor:
    """Validate all provider output before persisting positions through Suit."""

    def __init__(self, suit, gateway=None):
        self.suit = suit
        self.gateway = gateway

    def construct(self, challenge_path, *, agent_id="L01"):
        guide = _fixture(challenge_path)
        if self.gateway is None:
            raise BlockedGenerationError("Precision-315 advisory provider is not configured")
        positions = []
        provenance = []
        for segment in guide["segments"]:
            specs = guide["positions"][segment["start"] - 1:segment["end"]]
            anchors = {str(row["position"]): row["text"]
                       for row in specs if row["kind"] == "ANCHOR"}
            requested = [str(row["position"]) for row in specs if row["kind"] == "OPEN"]
            prompt = json.dumps({
                "task": "Return only a JSON object mapping the requested position IDs to distinct "
                        "historically grounded lines in the segment language, 7–15 words each. "
                        "Use the locked context and mark uncertain claims appropriately.",
                "challenge_id": guide["id"], "segment": segment,
                "anchors": anchors, "open_positions": requested,
            }, ensure_ascii=False, sort_keys=True)
            reply = self.gateway.request_advisory(prompt, timeout_s=60.0)
            if reply.fallback_used:
                raise BlockedGenerationError(f"Provider unavailable at segment {segment['segment']}")
            generated = json.loads(reply.text)
            if (not isinstance(generated, dict) or set(generated) != set(requested)
                    or any(not _valid_line(line) for line in generated.values())):
                raise ValueError(f"Provider returned invalid lines for segment {segment['segment']}")
            lines = {**anchors, **generated}
            positions.extend({"position": row["position"], "language": row["language"],
                              "text": lines[str(row["position"])]} for row in specs)
            provenance.append({
                "segment": segment["segment"], "positions": list(map(int, requested)),
                "provider": reply.provider, "model": reply.model,
                "request_digest": reply.request_digest, "from_replay": reply.from_replay,
                "chronology": segment.get("period") or "UNKNOWN",
            })
        writes = []
        for row in positions:
            index = row["position"] - 1
            payload = {**row, "content": row["text"]}
            result, _ = self.suit.execute_memory_write(agent_id, "BIG49", index, payload)
            writes.append({"pool": "BIG49", "address": index,
                           "cool_reference": result.get("cool_reference"),
                           "mirror_reference": result.get("mirror_reference")})
        return {"id": guide["id"], "positions": positions,
                "provider_trace": provenance, "writes": writes}


def main():
    import argparse
    import secrets

    from .online_ai import GoogleGeminiProvider, OnlineAIGateway, OpenAIProvider
    from .standalone import local_runtime

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="New directory for isolated state, response and trace")
    parser.add_argument("--provider", required=True, choices=("openai", "gemini"))
    parser.add_argument("--model")
    args = parser.parse_args()
    _fixture(args.fixture)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    admin = secrets.token_hex(24)
    factory = OpenAIProvider if args.provider == "openai" else GoogleGeminiProvider
    provider = factory(model=args.model) if args.model else factory()
    with local_runtime(args.output_dir, admin) as (controller, closet, _):
        addresses = set(range(315))
        for address in sorted(addresses):
            controller.register_block_owner(address, "L01", admin)
        closet.suit.authorize_agent("L01", {"memory.write"}, allowed_block_ids=addresses,
                                    max_steps=315)
        try:
            result = Precision315Constructor(closet.suit, OnlineAIGateway(provider)).construct(
                args.fixture)
        except BlockedGenerationError as exc:
            parser.exit(2, f"BLOCKED: {exc}\n")
    trace = {key: result.pop(key) for key in ("provider_trace", "writes")}
    for name, data in (("response.json", result), ("trace.json", trace)):
        (args.output_dir / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
