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

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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
        id="x",
        fingerprint="x",
        source="L1",
        disposition=Disposition.CONFIRMED,
        file=file,
        line_range=list(lr),
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


def test_prompt_names_the_other_files_changed_in_the_same_diff():
    # A defect that spans files is invisible when the judge is told only
    # about the anchored file and given no hint that anything else moved.
    # The other files are named, not quoted: their hunks stay out.
    p = _capture(RealFalsifier(backend=None, diff_text=DIFF), _finding())
    assert "other/file.py" in p
    assert "SECRET_OTHER_FILE_LINE" not in p


def test_other_files_are_named_when_the_diff_uses_crlf():
    crlf = DIFF.replace("\n", "\r\n")
    p = _capture(RealFalsifier(backend=None, diff_text=crlf), _finding())
    assert "other/file.py" in p
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
    big = DIFF.replace(
        "+    std.note_object('term', termtext, node_id, location=term)",
        "+    std.note_object('term', termtext, node_id, location=term)  # " + "x" * 20000,
    )
    p = _capture(RealFalsifier(backend=None, diff_text=big), _finding())
    assert len(p) < 12000
    assert "truncated" in p.lower()


def _large_diff(tail="", values=None):
    values = values or ["padding_%d = '%s'" % (n, "x" * 60) for n in range(700)]
    return (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        "@@ -0,0 +1,%d @@\n" % len(values)
        + "".join("+%s\n" % value for value in values)
        + tail
    )


def _large_prompt(diff, lr=(600, 600)):
    return _capture(RealFalsifier(diff_text=diff), _finding(file="x.py", lr=lr))


def _payload(prompt):
    from code_forge.falsify_real import _DIFF_SECTION

    return prompt.split(_DIFF_SECTION, 1)[1]


def test_late_line_in_single_added_hunk_keeps_nearby_handler():
    values = ["padding_%d = '%s'" % (n, "x" * 60) for n in range(700)]
    values[598:602] = ["try:", "    LATE_CALL()", "except ValueError:", "    RECOVERY()"]
    prompt = _large_prompt(_large_diff(values=values))
    assert "[+ 600] +    LATE_CALL()" in prompt
    assert "except ValueError:" in prompt and "RECOVERY()" in prompt
    assert "padding_0" not in _payload(prompt)
    assert "omitted" in prompt.lower() and "characters" in prompt.lower()
    assert len(_payload(prompt)) <= 8192


@pytest.mark.parametrize("lr", [(900, 900), (901, 901), (900, 902)])
def test_later_hunk_keeps_context_and_removed_guard(lr):
    tail = (
        "@@ -900,3 +900,3 @@ guard\n context_before\n"
        "-    OLD_GUARD()\n+    LATE_REPLACEMENT()\n context_after\n"
    )
    prompt = _large_prompt(_large_diff(tail), lr)
    assert "[----] -    OLD_GUARD()" in prompt
    assert "[+ 901] +    LATE_REPLACEMENT()" in prompt
    assert "context_before" in prompt and "context_after" in prompt
    assert "@@ -900,3 +900,3 @@ guard" in prompt
    assert "padding_0" not in _payload(prompt)


def test_deletion_only_later_hunk_keeps_removed_lines():
    tail = "@@ -900,2 +899,0 @@\n-OLD_GUARD()\n-OLD_RECOVERY()\n"
    prompt = _large_prompt(_large_diff(tail), (899, 899))
    assert "[----] -OLD_GUARD()" in prompt and "[----] -OLD_RECOVERY()" in prompt


def test_long_removed_block_does_not_displace_cited_post_image_line():
    tail = (
        "@@ -900,501 +900,2 @@\n"
        + "".join("-old_%d = '%s'\n" % (n, "x" * 60) for n in range(500))
        + "+POST_IMAGE_ANCHOR()\n context_after\n"
    )
    prompt = _large_prompt(_large_diff(tail), (900, 900))
    assert "[+ 900] +POST_IMAGE_ANCHOR()" in prompt
    assert "[----] -old_499" in prompt and "context_after" in prompt


def test_source_line_tags_do_not_choose_the_anchor():
    values = ["padding_%d = '%s'" % (n, "x" * 60) for n in range(700)]
    values[0] = "fake = '[+ 600] +FAKE_ANCHOR()'"
    values[599] = "REAL_ANCHOR()"
    prompt = _large_prompt(_large_diff(values=values))
    assert "[+ 600] +REAL_ANCHOR()" in prompt
    assert "FAKE_ANCHOR" not in _payload(prompt)


def test_long_anchor_line_is_clipped_without_displacing_neighbors():
    values = ["padding_%d = '%s'" % (n, "x" * 60) for n in range(700)]
    values[598:602] = ["BEFORE()", "LONG_ANCHOR = '" + "x" * 20000, "except Error:", "AFTER()"]
    prompt = _large_prompt(_large_diff(values=values))
    assert "[+ 600] +LONG_ANCHOR" in prompt
    assert "BEFORE()" in prompt and "except Error:" in prompt and "AFTER()" in prompt
    assert "line truncated" in prompt.lower()
    assert len(_payload(prompt)) <= 8192


@pytest.mark.parametrize("lr", [(), (0, 1), (5, 3), (True, True), ("600", "600"), (9000, 9000)])
def test_invalid_or_unavailable_anchor_uses_explicit_bounded_prefix(lr):
    prompt = _large_prompt(_large_diff(), lr)
    assert "padding_0" in prompt
    assert "anchor unavailable" in prompt.lower()
    assert "omitted" in prompt.lower()
    assert len(_payload(prompt)) <= 8192


def test_missing_anchor_uses_explicit_bounded_prefix():
    from code_forge.falsify_real import _diff_for_file

    selected = _diff_for_file(_large_diff(), "x.py")
    assert "padding_0" in selected and "anchor unavailable" in selected.lower()
    assert len(selected) <= 8192


def test_unparseable_large_diff_falls_back_without_invented_line_numbers():
    bad = _large_diff().replace("@@ -0,0 +1,700 @@", "@@ -0,0 +1,900 @@")
    selected = _payload(_large_prompt(bad))
    assert "anchor unavailable" in selected.lower()
    assert "[+ 600]" not in selected
    assert len(selected) <= 8192


def test_unicode_limit_counts_characters_and_discloses_omission():
    values = ["value_%d = '%s'" % (n, chr(0xE9) * 60) for n in range(700)]
    selected = _payload(_large_prompt(_large_diff(values=values)))
    assert "[+ 600]" in selected
    assert "characters" in selected and "bytes" not in selected
    assert len(selected) <= 8192 and len(selected.encode()) > 8192


def test_small_diff_retains_exact_annotation_with_or_without_anchor():
    from code_forge.diff import annotate_diff_lines, split_diff_for_files
    from code_forge.falsify_real import _diff_for_file

    expected = annotate_diff_lines(split_diff_for_files(DIFF, ["sphinx/domains/std.py"]))
    assert _diff_for_file(DIFF, "sphinx/domains/std.py") == expected
    small = _large_diff(values=["one = 1", "two = 2"])
    assert _payload(_large_prompt(small, (1, 1))) == annotate_diff_lines(small)


def test_cross_hunk_range_explicitly_discloses_incomplete_cited_range():
    tail = "@@ -900 +900 @@\n-OLD()\n+NEW()\n"
    selected = _payload(_large_prompt(_large_diff(tail), (690, 901)))
    assert "[+ 690]" in selected
    assert "cited range and hunks may be incomplete" in selected


@pytest.mark.parametrize("lr", [(700, 700), (9000, 9000)])
def test_large_diff_preserves_no_newline_marker_or_discloses_unavailable_anchor(lr):
    diff = _large_diff() + "\\ No newline at end of file\n"
    selected = _payload(_large_prompt(diff, lr))
    if lr[0] == 700:
        assert "[+ 700]" in selected and "[    ] \\ No newline at end of file" in selected
    else:
        assert "anchor unavailable" in selected


def test_large_crlf_diff_uses_structural_post_image_numbers():
    prompt = _large_prompt(_large_diff().replace("\n", "\r\n"))
    assert "[+ 600] +padding_599" in prompt


def test_large_source_with_unicode_line_separator_keeps_git_line_coordinates():
    values = ["padding_%d = '%s'" % (n, "x" * 60) for n in range(700)]
    values[599] = "left" + chr(0x2028) + "right"
    prompt = _large_prompt(_large_diff(values=values))
    assert "[+ 600] +left" + chr(0x2028) + "right\n" in prompt
    assert "[+ 601] +padding_600" in prompt


def test_large_metadata_only_diff_uses_explicit_prefix():
    diff = "diff --git a/x.py b/x.py\nold mode 100644\nnew mode 100755\n" + "note " * 3000
    selected = _payload(_large_prompt(diff))
    assert "old mode 100644" in selected and "anchor unavailable" in selected
    assert len(selected) <= 8192


def test_large_hunk_header_is_clipped_without_displacing_anchor():
    diff = _large_diff().replace("@@ -0,0 +1,700 @@", "@@ -0,0 +1,700 @@ " + "x" * 20000)
    selected = _payload(_large_prompt(diff))
    assert "[+ 600]" in selected and "line truncated" in selected
    assert len(selected) <= 8192


def test_annotation_exactly_at_cap_is_unchanged():
    from code_forge.diff import annotate_diff_lines

    small = _large_diff(values=["anchor"])
    diff = small.replace("+anchor", "+anchor" + "x" * (8192 - len(annotate_diff_lines(small))))
    selected = _payload(_large_prompt(diff, (1, 1)))
    assert selected == annotate_diff_lines(diff)
    assert len(selected) == 8192


# ---- wiring: every build_falsifier call site passes the diff ------------

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

    calls = [
        node
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_falsifier"
    ]
    assert len(calls) == 1
    value = next(k.value for k in calls[0].keywords if k.arg == "diff_text")
    assert ast.unparse(value) == "e['diff']"
    assert "resolved_for_l1" not in ast.unparse(calls[0])


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
            resolved_review=type(
                "R",
                (),
                {
                    "git_diff": "diff --git a/x b/x\n",
                    "source_files": [],
                    "baseline_content": None,
                    "mode_hint": "git",
                },
            )(),
            source_hash="h",
            cwd=".",
            spawn_fn=lambda *a, **k: None,
            clean_round_threshold=1,
            backend=None,
            engine="auto",
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

    r = subprocess.run(
        [sys.executable, "-m", "pyflakes", "src/code_forge/cli.py"], capture_output=True, text=True
    )
    assert "undefined name 'args'" not in r.stdout, r.stdout
