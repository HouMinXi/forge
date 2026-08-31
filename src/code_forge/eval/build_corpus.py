"""Rebuild tests/eval/swebench from SWE-bench_Verified.

The corpus is committed, so this is not part of an evaluation run and not
part of the test suite. It exists so the provenance record means
something: a seed and a cap describe a reproducible selection only if the
thing that consumed them can be run again.

    python3 -m code_forge.eval.build_corpus [--out DIR] [--cap N] [--seed N]

Needs the eval-corpus extra:

    pip install -e '.[eval-corpus]'

Regenerating over an existing directory refuses rather than overwrites --
a corpus rebuilt with different thresholds is a different corpus, and
silently swapping it under committed metric numbers is the failure this
whole milestone is about. Delete the directory deliberately.
"""

import argparse
import pathlib
import sys

from .swebench import build_corpus, qualifies, select_instances

_DATASET = "princeton-nlp/SWE-bench_Verified"
_SPLIT = "test"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="code_forge.eval.build_corpus")
    ap.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path("tests/eval/swebench"),
        help="output directory (default: tests/eval/swebench)",
    )
    ap.add_argument(
        "--cap", type=int, default=8,
        help="max entries per repository (default: 8)",
    )
    ap.add_argument(
        "--seed", type=int, default=20260830,
        help="selection seed (default: 20260830)",
    )
    args = ap.parse_args(argv)

    try:
        from datasets import load_dataset
    except ImportError:
        print(
            "datasets is not installed. This is the corpus generator, not "
            "the evaluator: pip install -e '.[eval-corpus]'",
            file=sys.stderr,
        )
        return 2

    if args.out.exists():
        print(
            "%s already exists. Delete it first -- a corpus rebuilt with "
            "different thresholds is a different corpus, and overwriting it "
            "in place would leave committed metrics describing a corpus that "
            "no longer exists." % args.out,
            file=sys.stderr,
        )
        return 2

    ds = load_dataset(_DATASET, split=_SPLIT)

    rejections: list[str] = []
    eligible = []
    for row in ds:
        inst = {
            "instance_id": row["instance_id"],
            "repo": row["repo"],
            "patch": row["patch"],
            "problem_statement": row["problem_statement"],
        }
        why = qualifies(inst)
        if why is None:
            eligible.append(inst)
        else:
            rejections.append(why.value)

    selected = select_instances(eligible, cap=args.cap)
    args.out.mkdir(parents=True)
    build_corpus(
        selected, args.out,
        rejections=rejections, cap=args.cap, seed=args.seed,
    )

    print(
        "%d of %d instances qualified; selected %d; wrote %d entries to %s"
        % (len(eligible), len(ds), len(selected), len(selected) * 2, args.out)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
