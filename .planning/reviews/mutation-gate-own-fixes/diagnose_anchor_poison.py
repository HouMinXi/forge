#!/usr/bin/env python3
"""Is the synthetic <llm-invoke> anchor what blocks attestation?

Control  = receipts copied verbatim. Must reproduce the CLI's exact failure,
           otherwise the copy is not a faithful stand-in and nothing below
           this line means anything.
Treatment= same copy with the synthetic anchors dropped from the three
           receipts that carry them. Nothing else differs.

Both runs get the same diff_sha256 / diff_files the CLI computes, built the
same way cli.py builds them (git diff HEAD -> compute_source_hash ->
parse_diff_files), so the only variable is the anchor list.

Read-only against the real receipts: they are copied, never edited in place.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WORKTREE = Path("/home/houminxi/code/forge/.worktrees/mutation-gate")
sys.path.insert(0, str(WORKTREE / "src"))

from code_forge.source import compute_source_hash          # noqa: E402
from code_forge.verify import run_verify, parse_diff_files  # noqa: E402

diff_text = subprocess.run(
    ["git", "diff", "HEAD"], capture_output=True, text=True,
    encoding="utf-8", errors="replace", cwd=WORKTREE,
).stdout
diff_sha = compute_source_hash(git_diff=diff_text)
diff_f = parse_diff_files(diff_text)
print("diff_sha256 = %s" % diff_sha)
print("diff files   = %s\n" % sorted(diff_f))

src = WORKTREE / ".code-forge" / "receipts"


def run(strip: bool) -> None:
    with tempfile.TemporaryDirectory() as td:
        dst = Path(td) / ".code-forge" / "receipts"
        shutil.copytree(src, dst)
        touched = 0
        if strip:
            for f in sorted(dst.glob("receipt-*.json")):
                r = json.loads(f.read_text())
                keep = [a for a in r["anchors"]
                        if not a.get("file", "").startswith("<")]
                if len(keep) != len(r["anchors"]):
                    r["anchors"] = keep
                    f.write_text(json.dumps(r, indent=2))
                    touched += 1
        vr = run_verify(Path(td), diff_sha, diff_f, diff_text=diff_text)
        label = "TREATMENT (synthetic anchors dropped from %d receipts)" % touched \
            if strip else "CONTROL   (receipts verbatim)"
        print("%s\n    passed=%s  check=%s  checks_passed=%s\n    reason: %s\n"
              % (label, vr.passed, vr.checks_run, vr.checks_passed, vr.reason))


run(strip=False)
run(strip=True)
