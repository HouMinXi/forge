#!/usr/bin/env python3
"""Corrected: truly-separate non-git directory (sibling tempdir, not
nested inside a git repo) to isolate the non-git case cleanly."""
import tempfile
from pathlib import Path

from code_forge.baseline import resolve_baseline, EmptyBaseline
from code_forge.git import is_git_repo

with tempfile.TemporaryDirectory() as td_git, tempfile.TemporaryDirectory() as td_nogit:
    nogit = Path(td_nogit)
    print("is_git_repo(nogit) =", is_git_repo(nogit))
    (nogit / "x").write_text("a")
    r = resolve_baseline(EmptyBaseline(), None, [nogit / "x"], nogit)
    print("EmptyBaseline, head_spec=None, TRULY non-git dir:")
    print("  base_sha=%r head_sha=%r mode_hint=%r" % (r.base_sha, r.head_sha, r.mode_hint))
