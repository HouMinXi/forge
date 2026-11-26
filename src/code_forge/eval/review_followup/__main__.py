"""Validate or replay offline fixtures; no model, network, or candidate execution."""

import argparse
import sys
from pathlib import Path

from .context_plan import preview
from .fixtures import InputError
from .replay import replay, validate
from .report import build_report, publish_local


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "replay", "preview-context"):
        sub = commands.add_parser(command)
        sub.add_argument("root", type=Path)
        if command != "validate":
            sub.add_argument("--output", type=Path, required=True)
        if command == "preview-context":
            sub.add_argument("--case", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            print(f"validated {len(validate(args.root))} review input(s)")
        elif args.command == "replay":
            publish_local(args.output, build_report(replay(args.root)))
        else:
            publish_local(args.output, preview(args.root, args.case))
    except InputError as exc:
        print(f"input rejected: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print(f"{args.command} failed: unable to publish report", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
