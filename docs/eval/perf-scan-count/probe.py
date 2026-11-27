"""Manual repeated-traversal check for the Forge parser. No timing assertions."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FIXED = "2324abcd9557705bcc4ec69f5aae903e354aeb3a"
PINNED = {
    "src/code_forge/verify.py": "067e7e91a1273712f338368d028a3228b2be319c",
    "src/code_forge/diff.py": "a64dea02687e3c2b0da82f73e3095abfe6b1c2f9",
    "scripts/perf_regression.py": "ebad4386014cd44c130546c453045d86e6627b7d",
}
PARSER = Path("src/code_forge/verify.py")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(data):
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()


class ObservedLines(list):
    def __init__(self, values, counts):
        super().__init__(values)
        self.counts = counts

    def __iter__(self):
        self.counts["iterations"] += 1
        for value in super().__iter__():
            self.counts["visited_lines"] += 1
            yield value


class ObservedDiff(str):
    def __new__(cls, value):
        instance = super().__new__(cls, value)
        instance.counts = {"splitlines_calls": 0, "iterations": 0, "visited_lines": 0}
        return instance

    def splitlines(self, keepends=False):
        self.counts["splitlines_calls"] += 1
        return ObservedLines(super().splitlines(keepends), self.counts)


def measure(expected_sha):
    import runpy

    from code_forge import verify

    source = Path(verify.parse_diff_files.__code__.co_filename).resolve()
    assert source == PARSER.resolve() and sha(source.read_bytes()) == expected_sha
    diff_source = Path(verify.path_from_plus_header.__code__.co_filename).resolve()
    assert diff_source == Path("src/code_forge/diff.py").resolve()
    assert diff_source.is_file()
    cli = Path("scripts/perf_regression.py")
    assert git_blob(cli.read_bytes()) == PINNED[str(cli)]
    pin = runpy.run_path(str(cli))
    workload = pin["_DIFF"]
    expected = pin["_EXPECTED"]
    second_header = workload.index("diff --git a/f20.py")
    rows = []
    for label, raw, want in (
        (
            "20-file",
            workload[:second_header],
            {f"f{n}.py": expected[f"f{n}.py"] for n in range(20)},
        ),
        ("40-file", workload, expected),
    ):
        plain = verify.parse_diff_files(raw)
        observed = ObservedDiff(raw)
        traced = verify.parse_diff_files(observed)
        assert plain == traced == want
        lines = len(raw.splitlines())
        target = {"splitlines_calls": 1, "iterations": 1, "visited_lines": lines}
        rows.append(
            {
                "fixture": label,
                "input_lines": lines,
                "input_sha256": sha(raw.encode()),
                "output_sha256": sha(json.dumps(plain, sort_keys=True).encode()),
                "counts": observed.counts,
                "within_budget": observed.counts == target,
            }
        )
    print(json.dumps(rows))


def run(tree, data):
    with tempfile.TemporaryDirectory(prefix="forge-scan-import-") as scratch:
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                f"pycache_prefix={scratch}",
                str(Path(__file__).resolve()),
                "--child",
                sha(data),
            ],
            cwd=tree,
            env={
                **os.environ,
                "PYTHONPATH": str(tree / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    return json.loads(result.stdout)


def main(fixed, root):
    for path, expected in PINNED.items():
        assert git_blob((fixed / path).read_bytes()) == expected
    changed = root / "mutant"
    shutil.copytree(
        fixed / "src",
        changed / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (changed / "scripts").mkdir()
    shutil.copyfile(fixed / "scripts/perf_regression.py", changed / "scripts/perf_regression.py")
    original = (fixed / PARSER).read_bytes()
    needle = b"    for line in diff_text.splitlines():\n"
    assert original.count(needle) == 1
    replacement = (
        b"    lines = diff_text.splitlines()\n"
        b"    for _ in lines:\n"
        b"        pass\n"
        b"    for line in lines:\n"
    )
    modified = original.replace(needle, replacement)
    (changed / PARSER).write_bytes(modified)

    clean = run(fixed, original)
    mutant = run(changed, modified)
    same_scan = original.replace(needle, needle + b'        line.startswith("diff --git ")\n', 1)
    (changed / PARSER).write_bytes(same_scan)
    negative = run(changed, same_scan)
    assert negative == clean
    (changed / PARSER).write_bytes(original)
    restored = run(changed, original)
    assert restored == clean
    for good, bad in zip(clean, mutant, strict=True):
        assert good["within_budget"] and not bad["within_budget"]
        assert good["input_sha256"] == bad["input_sha256"]
        assert good["output_sha256"] == bad["output_sha256"]
        assert bad["counts"] == {
            "splitlines_calls": 1,
            "iterations": 2,
            "visited_lines": 2 * good["input_lines"],
        }
    print(
        json.dumps(
            {
                "revision": FIXED,
                "clean": clean,
                "duplicate_scan": mutant,
                "restored": restored,
                "extra_work_same_scan": negative,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    if not __debug__:
        raise SystemExit("Run without -O.")
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        measure(sys.argv[2])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument(
            "--source", required=True, type=Path, help="Forge source tree (pinned for the control run)"
        )
        parser.add_argument(
            "--check", action="store_true", help="Measure the supplied parser without mutation"
        )
        args = parser.parse_args()
        source = args.source.resolve(strict=True)
        if args.check:
            parser_bytes = (source / PARSER).read_bytes()
            try:
                rows = run(source, parser_bytes)
            except subprocess.CalledProcessError as error:
                print(error.stderr, file=sys.stderr, end="")
                raise SystemExit(2) from None
            except subprocess.TimeoutExpired:
                print("Parser measurement exceeded 30 seconds.", file=sys.stderr)
                raise SystemExit(2) from None
            print(json.dumps({"parser_sha256": sha(parser_bytes), "results": rows}, indent=2))
            raise SystemExit(0 if all(row["within_budget"] for row in rows) else 1)
        with tempfile.TemporaryDirectory(prefix="forge-scan-probe-") as scratch:
            main(source, Path(scratch))
