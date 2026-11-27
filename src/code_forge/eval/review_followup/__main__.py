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
    for command in ("validate", "replay", "preview-context", "dry-run-context"):
        sub = commands.add_parser(command)
        sub.add_argument("root", type=Path)
        if command != "validate":
            sub.add_argument("--output", type=Path, required=True)
        if command in {"preview-context", "dry-run-context"}:
            sub.add_argument("--case", required=True)
        if command == "dry-run-context":
            sub.add_argument("--max-prompt-bytes", type=int, required=True)
    capture = commands.add_parser("capture-revision")
    capture.add_argument("repository", type=Path)
    capture.add_argument("request", type=Path)
    capture.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("validate-captured-proposal")
    check.add_argument("repository", type=Path)
    check.add_argument("request", type=Path)
    check.add_argument("proposal", type=Path)
    check.add_argument("--validation-id", required=True)
    check.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            print(f"validated {len(validate(args.root))} review input(s)")
        elif args.command == "replay":
            publish_local(args.output, build_report(replay(args.root)))
        elif args.command == "preview-context":
            publish_local(args.output, preview(args.root, args.case))
        elif args.command == "capture-revision":
            from .revision_capture import capture_revision_evidence

            publish_local(args.output, capture_revision_evidence(args.repository, args.request))
        elif args.command == "validate-captured-proposal":
            from .revision_capture import validate_captured_proposal

            publish_local(
                args.output,
                validate_captured_proposal(
                    args.repository, args.request, args.proposal, args.validation_id
                ),
            )
        else:
            from .experiment import dry_run

            publish_local(args.output, dry_run(args.root, args.case, args.max_prompt_bytes))
    except InputError as exc:
        print(f"input rejected: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print(f"{args.command} failed: unable to publish report", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
