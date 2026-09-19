# SPDX-License-Identifier: Apache-2.0
"""The falsifier sees the diff it is judging.

Phase 59-A2 measured 8/20 agreement on a frozen calibration set and the
probe that followed showed why: RealFalsifier's prompt carried only
File/Lines/Description. Seven of the ten clean-side misses were the judge
confirming a finding that described the upstream FIX as a defect --
"removing .lower() makes registration case-sensitive" is a true sentence,
and a true sentence was all it had.

These tests pin: with diff_text, the prompt carries the annotated hunks
for the finding's file and nothing from other files; without diff_text the
prompt is byte-identical to before (so the calibration A/B is clean).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from code_forge.disposition import Disposition
from code_forge.falsify_real import RealFalsifier
from code_forge.state import StateFinding

DIFF = """\
diff --git a/sphinx/domains/std.py b/sphinx/domains/std.py
--- a/sphinx/domains/std.py
+++ b/sphinx/domains/std.py
@@ -305,3 +305,3 @@ def make_glossary_term(env, textnodes, index_key, source, lineno, node_id, document):
     term['ids'].append(node_id)
-    std.note_object('term', termtext.lower(), node_id, location=term)
+    std.note_object('term', termtext, node_id, location=term)
     return term
diff --git a/other/file.py b/other/file.py
--- a/other/file.py
+++ b/other/file.py
@@ -1,2 +1,2 @@
-SECRET_OTHER_FILE_LINE = 1
+SECRET_OTHER_FILE_LINE = 2
"""


def _finding(file="sphinx/domains/std.py", lr=(308, 308)):
    return StateFinding(
        id="x", fingerprint="x", source="L1", disposition=Disposition.CONFIRMED,
        file=file, line_range=list(lr),
        description="Removal of '.lower()' makes term registration case-sensitive.",
    )


def _capture(falsifier, finding):
    caught = {}

    def fake(prompt, **kw):
        caught["prompt"] = prompt
        return SimpleNamespace(content={"verdict": "DISMISSED", "reasoning": "r"})
    with patch("code_forge.falsify_real.llm_invoke", fake):
        falsifier.falsify(finding)
    return caught["prompt"]


def test_prompt_carries_annotated_hunks_for_the_findings_file():
    p = _capture(RealFalsifier(backend=None, diff_text=DIFF), _finding())
    assert "termtext.lower()" in p
    assert "[----] -    std.note_object('term', termtext.lower()" in p
    assert "[+ 306] +    std.note_object('term', termtext, node_id" in p


def test_prompt_excludes_other_files_hunks():
    p = _capture(RealFalsifier(backend=None, diff_text=DIFF), _finding())
    assert "SECRET_OTHER_FILE_LINE" not in p


def test_prompt_tells_the_judge_which_direction_the_change_went():
    """The rubric line that turns 'the behaviour changed' into a question
    about THIS diff's direction. Without it the judge confirms a correct
    description of a fix."""
    p = _capture(RealFalsifier(backend=None, diff_text=DIFF), _finding())
    assert "[+" in p and "[----]" in p
    assert "removed lines" in p.lower() and "added lines" in p.lower()


def test_prompt_without_diff_is_unchanged():
    """No diff_text: byte-identical to the pre-A4-0 prompt, so the 8/20
    baseline can be re-run against the same code path."""
    before = _capture(RealFalsifier(backend=None), _finding())
    after = _capture(RealFalsifier(backend=None, diff_text=None), _finding())
    assert before == after
    assert "## Diff" not in before


def test_file_absent_from_diff_falls_back_to_no_diff_section():
    p = _capture(RealFalsifier(backend=None, diff_text=DIFF), _finding(file="not/in/diff.py"))
    assert "## Diff" not in p
    assert "SECRET_OTHER_FILE_LINE" not in p


def test_diff_section_is_capped(monkeypatch):
    big = DIFF.replace("+    std.note_object('term', termtext, node_id, location=term)",
                       "+    std.note_object('term', termtext, node_id, location=term)  # " + "x" * 20000)
    p = _capture(RealFalsifier(backend=None, diff_text=big), _finding())
    assert len(p) < 12000
    assert "truncated" in p.lower()


# ---- wiring: every build_falsifier call site passes the diff ------------

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "code_forge"


def _build_falsifier_calls(path: Path):
    tree = ast.parse(path.read_text())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name == "build_falsifier":
                out.append({kw.arg for kw in node.keywords})
    return out


def test_every_review_path_passes_diff_text_to_build_falsifier():
    """cli (outlet A), outlet_c (subagent), cross_repo (sibling repos).
    mcp_server's sampling path uses the stub on purpose and is excluded."""
    for rel in ("cli.py", "outlet_c.py", "cross_repo.py"):
        calls = _build_falsifier_calls(_SRC / rel)
        assert calls, rel
        for kws in calls:
            assert "diff_text" in kws, "%s: build_falsifier without diff_text" % rel


def test_factory_threads_diff_text_to_real_falsifier():
    from code_forge.factories import build_falsifier
    f = build_falsifier("real", backend=None, diff_text=DIFF)
    assert f._diff_text == DIFF
    f = build_falsifier("auto", backend=None, diff_text=DIFF)
    assert getattr(f, "_diff_text", None) == DIFF


def test_cross_repo_falsifier_uses_the_threads_own_diff():
    """The first wiring referenced resolved_for_l1, which only the primary
    thread defines; sibling threads raised NameError inside _thread_fn and
    their receipts vanished (test_cross_repo.py caught it). The behavioural
    proof is test_l0_runs_on_each_repo; this pins the shape so the
    primary-only local cannot creep back into the shared call."""
    src = (_SRC / "cross_repo.py").read_text()
    # The call must not depend on the primary-only local.
    import ast
    calls = [node for node in ast.walk(ast.parse(src))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == 'build_falsifier']
    assert len(calls) == 1
    value = next(k.value for k in calls[0].keywords if k.arg == 'diff_text')
    assert ast.unparse(value) == "e['diff']"
    assert 'resolved_for_l1' not in ast.unparse(calls[0])


# ---- review R4: subagent outlet is a production falsifier path too ---------

def test_run_outlet_c_forwards_context_rows_to_build_falsifier(monkeypatch):
    """cli.py's main line gathers RemovedSymbolReaders and hands the rows
    to build_falsifier. Outlet C returns before that gather and built
    its own falsifier without them -- the one production path where the
    A4 readers were silently absent."""
    import code_forge.factories as fac
    import code_forge.outlet_c as oc
    seen = {}

    def fake_build(engine, backend=None, diff_text=None, context_rows=None, **kw):
        seen["rows"] = context_rows
        seen["diff"] = diff_text

        class _F:
            def falsify(self, f):
                return f.disposition
        return _F()
    monkeypatch.setattr(fac, "build_falsifier", fake_build)
    # run_outlet_c imports build_falsifier lazily from .factories
    rows = [object(), object()]
    try:
        oc.run_outlet_c(
            resolved_review=type("R", (), {"git_diff": "diff --git a/x b/x\n",
                                          "source_files": [], "baseline_content": None,
                                          "mode_hint": "git"})(),
            source_hash="h", cwd=".", spawn_fn=lambda *a, **k: None,
            clean_round_threshold=1, backend=None, engine="auto",
            context_rows=rows,
        )
    except Exception:
        pass  # the machine may not run to completion on this stub; the
              # assertion is about what reached build_falsifier
    assert seen.get("rows") is rows
    assert seen.get("diff") == "diff --git a/x b/x\n"


def test_dispatch_subagent_does_not_reference_an_undefined_args():
    """R4 added a context gather to _dispatch_subagent that read
    `args`, a name the function never received. pyflakes catches it;
    so does calling the function with outlet='subagent'."""
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "pyflakes",
                        "src/code_forge/cli.py"], capture_output=True, text=True)
    assert "undefined name 'args'" not in r.stdout, r.stdout
