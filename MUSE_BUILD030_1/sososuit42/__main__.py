"""Local CLI. Context files are controlled by the trusted host."""
import argparse
import sys
from pathlib import Path

from . import Invalid, Journal, canonical, govern_context, parse, run_pipeline, verify_replay


def read(path):
    return parse(Path(path).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True, dest="command")
    for name in ("commit", "evaluate", "replay", "submit", "pending", "complete"):
        command = commands.add_parser(name)
        if name in ("commit", "evaluate", "replay", "submit"):
            command.add_argument("context")
        if name in ("evaluate", "replay", "submit"):
            command.add_argument("candidate")
        if name == "replay":
            command.add_argument("receipt")
        if name in ("submit", "pending", "complete"):
            command.add_argument("--db", required=True)
        if name in ("pending", "complete"):
            command.add_argument("principal")
        if name == "complete":
            command.add_argument("decision_id")
            command.add_argument("iou_id")
            command.add_argument("status", choices=("EVIDENCE_RECEIVED", "ABANDONED"))
            command.add_argument("note")
    args = parser.parse_args()
    try:
        if args.command == "commit":
            output = govern_context(read(args.context))
        elif args.command == "evaluate":
            output = run_pipeline(read(args.context), read(args.candidate))
        elif args.command == "replay":
            verified = verify_replay(read(args.context), read(args.candidate), read(args.receipt))
            print(canonical({"verified": verified}).decode())
            return 0 if verified else 1
        elif args.command == "submit":
            output = Journal(args.db).submit(read(args.context), read(args.candidate))
        elif args.command == "pending":
            output = Journal(args.db).pending(args.principal)
        else:
            output = Journal(args.db).complete_iou(
                args.principal, args.decision_id, args.iou_id, args.status, args.note)
        print(canonical(output).decode())
        return 0
    except (Invalid, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
