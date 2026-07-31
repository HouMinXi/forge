#!/usr/bin/env python3
"""EXPERIMENT: does the real resolve_baseline() ever return None SHAs,
and under which real CLI-selectable configurations?"""
from pathlib import Path
import tempfile, subprocess

from code_forge.baseline import (
    resolve_baseline, GitRefBaseline, SnapshotBaseline, EmptyBaseline,
)

with tempfile.TemporaryDirectory() as td:
    repo = Path(td)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "x").write_text("a")
    subprocess.run(["git", "-C", str(repo), "add", "x"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

    print("--- default review invocation: GitRefBaseline(HEAD) + head=WORKING, in a real git repo ---")
    r = resolve_baseline(GitRefBaseline("HEAD"), GitRefBaseline("WORKING"), [repo / "x"], repo)
    print("base_sha=%r head_sha=%r" % (r.base_sha, r.head_sha))

    print()
    print("--- --whole-file mode: EmptyBaseline + head=WORKING, in a real git repo ---")
    r = resolve_baseline(EmptyBaseline(), GitRefBaseline("WORKING"), [repo / "x"], repo)
    print("base_sha=%r head_sha=%r" % (r.base_sha, r.head_sha))

    print()
    print("--- SnapshotBaseline pointing at a MISSING snapshot file, in a real git repo ---")
    r = resolve_baseline(SnapshotBaseline(path=repo / ".code-forge" / "snapshots" / "nonexistent.json"), None, [repo / "x"], repo)
    print("base_sha=%r head_sha=%r mode_hint=%r" % (r.base_sha, r.head_sha, r.mode_hint))

    print()
    print("--- EmptyBaseline with NO head_spec, in a NON-git directory ---")
    nogit = Path(td) / "nogit"
    nogit.mkdir()
    r = resolve_baseline(EmptyBaseline(), None, [nogit / "x"], nogit)
    print("base_sha=%r head_sha=%r mode_hint=%r" % (r.base_sha, r.head_sha, r.mode_hint))
