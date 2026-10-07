# SPDX-License-Identifier: Apache-2.0
"""Phase 59-B1: context_sources contract.

Five invariants, each with one test whose failure names the seam:

  1. render_blast_radius reproduces cli.py:3756-3759 byte for byte.
  2. Every row carries its source; an adapter that drops it fails.
  3. A snapshotted source at a sha other than head is skipped, and the
     skip is recorded; allow_unsnapshotted lets it through.
  4. A source that raises lands in result.errors with its name, and the
     other sources still contribute.
  5. Empty rows render "" (no header-only table), so a repo with no graph
     produces the same prompt as before.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from code_forge.advisory import AdvisoryFinding
from code_forge.context_sources import (
    FactRow,
    GatherResult,
    GraphTriageSource,
    _adapt_advisory,
    gather,
    render_blast_radius,
    render_context_sources,
)

# The exact string cli.py built for two rows before this module existed.
EXPECTED_TWO_ROWS = (
    "| Entity | File | Downstream | Top Dependents |\n"
    "|--------|------|------------|----------------|\n"
    "| f | f.py | 3 | g, h |\n"
    "| g | g.py | 1 | h |"
)


def _adv(desc: str, file: str, line=(1, 1)) -> AdvisoryFinding:
    return AdvisoryFinding(
        id="a",
        axis="graph_triage",
        file=file,
        line_range=line,
        description=desc,
        attribution="graph",
    )


@dataclass
class _Src:
    name: str
    snap: str | None = None
    rows: list | None = None
    raises: Exception | None = None

    def snapshot_sha(self):
        return self.snap

    def facts(self, changed_files, diff_text):
        if self.raises is not None:
            raise self.raises
        return list(self.rows or [])


def _row(entity="f", file="f.py", source="graph_triage", **kw) -> FactRow:
    return FactRow(entity=entity, file=file, downstream="3", dependents="g, h", source=source, **kw)


# -- invariant 1 ------------------------------------------------------------


def test_render_matches_cli_verbatim():
    rows = [
        _adapt_advisory(
            _adv("f (impact: 3 downstream) -- top dependents: g, h", "f.py"), "graph_triage"
        ),
        _adapt_advisory(_adv("g (impact: 1 downstream) -- top dependents: h", "g.py"), "graph_triage"),
    ]
    assert render_blast_radius(rows) == EXPECTED_TWO_ROWS


def test_adapt_handles_description_without_dependents():
    r = _adapt_advisory(_adv("f (impact: 0 downstream)", "f.py"), "graph_triage")
    assert (r.entity, r.downstream, r.dependents) == ("f", "0", "")


def test_adapt_handles_description_without_impact():
    r = _adapt_advisory(_adv("plain", "f.py"), "graph_triage")
    assert (r.entity, r.downstream, r.dependents) == ("plain", "0", "")


# -- invariant 2 ------------------------------------------------------------


def test_every_row_carries_source_and_origin():
    r = _adapt_advisory(_adv("f (impact: 3 downstream)", "f.py", line=(42, 50)), "graph_triage")
    assert r.source == "graph_triage"
    assert r.origin_line == 42


def test_origin_line_none_when_range_is_zero():
    r = _adapt_advisory(_adv("f (impact: 3 downstream)", "f.py", line=(0, 0)), "graph_triage")
    assert r.origin_line is None


# -- invariant 3 ------------------------------------------------------------


def test_stale_snapshot_is_skipped_and_recorded():
    src = _Src("g", snap="a" * 40, rows=[_row()])
    res = gather([src], ["f.py"], "diff", head_sha="b" * 40)
    assert res.rows == []
    assert len(res.skipped) == 1 and res.skipped[0].startswith("g: index at aaaaaaaaaaaa")
    assert res.snapshot_shas == {"g": "a" * 40}
    assert res.skipped_sources == ["g"]


def test_matching_snapshot_runs():
    src = _Src("g", snap="a" * 40, rows=[_row()])
    res = gather([src], ["f.py"], "diff", head_sha="a" * 40)
    assert len(res.rows) == 1 and res.skipped == []
    assert res.skipped_sources == []


def test_unknown_head_skips_snapshotted_source():
    src = _Src("g", snap="a" * 40, rows=[_row()])
    res = gather([src], ["f.py"], "diff", head_sha=None)
    assert res.rows == [] and len(res.skipped) == 1
    assert res.skipped_sources == ["g"]


def test_allow_unsnapshotted_overrides_gate():
    src = _Src("g", snap="a" * 40, rows=[_row()])
    res = gather([src], ["f.py"], "diff", head_sha="b" * 40, allow_unsnapshotted=True)
    assert len(res.rows) == 1 and res.skipped == []
    assert res.skipped_sources == []


def test_on_demand_source_ignores_gate():
    src = _Src("sem", snap=None, rows=[_row()])
    res = gather([src], ["f.py"], "diff", head_sha=None)
    assert len(res.rows) == 1 and res.skipped == []


# -- invariant 4 ------------------------------------------------------------


def test_raising_source_is_recorded_not_swallowed():
    seen = []
    bad = _Src("bad", raises=RuntimeError("boom"))
    good = _Src("good", rows=[_row(source="good")])
    res = gather([bad, good], ["f.py"], "diff", head_sha=None, on_error=lambda n, m: seen.append((n, m)))
    assert res.errors == ["bad: RuntimeError: boom"]
    assert seen == [("bad", "bad: RuntimeError: boom")]
    assert [r.source for r in res.rows] == ["good"]


def test_raising_snapshot_sha_is_also_recorded():
    class _S:
        name = "s"

        def snapshot_sha(self):
            raise OSError("db locked")

        def facts(self, changed_files, diff_text):
            return [_row()]

    res = gather([_S()], ["f.py"], "diff", head_sha=None)
    assert res.errors == ["s: OSError: db locked"] and res.rows == []


# -- invariant 5 ------------------------------------------------------------


def test_empty_rows_render_empty_string():
    assert render_blast_radius([]) == ""


def test_context_sources_text_empty_when_only_graph_triage():
    res = GatherResult(rows=[_row(), _row(entity="g")])
    assert render_context_sources(res) == ""


def test_context_sources_text_lists_non_graph_rows():
    res = GatherResult(rows=[_row(), _row(entity="x", file="x.py", source="mcp:kb", origin_line=7)])
    out = render_context_sources(res)
    assert out.startswith("| Source | Entity | File | Line | Note |")
    assert "| mcp:kb | x | x.py | 7 | g, h |" in out
    assert "graph_triage" not in out


# -- GraphTriageSource adapter ---------------------------------------------


def test_graph_source_reads_head_sha_from_graphdb(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / ".code-review-graph" / "graph.db"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    con.execute("create table metadata (key text, value text)")
    con.execute("insert into metadata values ('git_head_sha', ?)", ("c" * 40,))
    con.commit()
    con.close()
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert GraphTriageSource(tmp_path).snapshot_sha() == "c" * 40


def test_graph_source_none_when_no_backend(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.delenv("CRG_DB_PATH", raising=False)
    assert GraphTriageSource(tmp_path).snapshot_sha() is None


def test_graph_source_surfaces_runner_infra_errors(tmp_path, monkeypatch):
    from code_forge import graph_triage as gt

    class _Runner:
        def __init__(self):
            self.infra_errors = []

        def run(self, diff, root):
            self.infra_errors.append("sem timed out")
            return []

    monkeypatch.setattr(gt, "GraphTriageRunner", _Runner)
    src = GraphTriageSource(tmp_path)
    with pytest.raises(RuntimeError, match="sem timed out"):
        src.facts(["f.py"], "diff")


def test_malformed_gate_yaml_is_an_error_not_empty_config(tmp_path, monkeypatch):
    """Review round 1: swallowing ValueError let a broken gate.yaml
    re-enable a backend the operator disabled. It must surface."""
    (tmp_path / ".code-forge").mkdir()
    (tmp_path / ".code-forge" / "gate.yaml").write_text("gate: [unclosed\n")
    monkeypatch.setattr("shutil.which", lambda _: None)
    res = gather([GraphTriageSource(tmp_path)], ["f.py"], "diff", head_sha=None)
    assert res.rows == []
    # Must be the YAML error itself, not the downstream "no backend"
    # RuntimeError a swallowing _gate_cfg would let the runner reach.
    assert len(res.errors) == 1
    assert res.errors[0].startswith("graph_triage: ValueError: Invalid YAML")


def test_findings_cache_is_replaced_not_accumulated(tmp_path, monkeypatch):
    """Review round 0 on b90e799 asked whether findings_cache grows across
    facts() calls. It is assigned, not appended; pin that."""
    from code_forge import graph_triage as gt

    calls = {"n": 0}

    class _Runner:
        def __init__(self):
            self.infra_errors = []

        def run(self, diff, root):
            calls["n"] += 1
            return [_adv("f%d (impact: 1 downstream)" % calls["n"], "f.py")]

    monkeypatch.setattr(gt, "GraphTriageRunner", _Runner)
    src = GraphTriageSource(tmp_path)
    src.facts(["f.py"], "d")
    src.facts(["f.py"], "d")
    assert len(src.findings_cache) == 1
    assert src.findings_cache[0].description.startswith("f2")


def _repo(path, commits):
    """commits is a list of (file, subject). Each writes that one file."""
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    for name, subject in commits:
        (path / name).write_text(subject + "\n")
        subprocess.run(["git", "add", name], cwd=path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", subject], cwd=path, check=True)


def test_git_history_returns_subjects_for_changed_files_only(tmp_path):
    from code_forge.context_sources import GitHistorySource

    _repo(tmp_path, [("a.py", "touch a"), ("b.py", "touch b"), ("c.py", "touch c")])
    rows = GitHistorySource(tmp_path).facts(["a.py", "b.py"], "diff")
    subjects = {r.entity for r in rows}
    assert subjects == {"touch a", "touch b"}
    assert all(r.dependents and r.source == "git-history" for r in rows)


def test_git_history_failure_is_recorded_and_isolated(tmp_path):
    from subprocess import CalledProcessError

    from code_forge.context_sources import GitHistorySource

    def boom(cmd, timeout):
        raise CalledProcessError(128, cmd, "", "fatal: not a repository")

    other = FactRow("e", "f.py", "", "", "other")

    class _Other:
        name = "other"

        def snapshot_sha(self):
            return None

        def facts(self, files, diff):
            return [other]

    res = gather(
        [GitHistorySource(tmp_path, runner=boom), _Other()],
        ["a.py"], "diff", head_sha=None,
    )
    assert any("git-history" in e and "128" in e for e in res.errors)
    assert res.rows == [other]


def test_git_history_timeout_uses_the_given_bound(tmp_path):
    from subprocess import TimeoutExpired

    from code_forge.context_sources import GitHistorySource

    def block(cmd, timeout):
        raise TimeoutExpired(cmd, timeout)

    res = gather(
        [GitHistorySource(tmp_path, runner=block, timeout=0.01)],
        ["a.py"], "diff", head_sha=None,
    )
    assert any("TimeoutExpired" in e for e in res.errors)


def test_git_history_skips_git_when_no_file_gained_a_line(tmp_path):
    from code_forge.context_sources import GitHistorySource

    calls = {"n": 0}

    def runner(cmd, timeout):
        calls["n"] += 1
        return ""

    rows = GitHistorySource(tmp_path, runner=runner).facts([], "diff of a deletion")
    assert rows == []
    assert calls["n"] == 0


def _raw_git_history(path, monkeypatch, subjects):
    """Write real commits without passing their messages through text decoding."""
    import subprocess

    for key in (
        "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_DIR",
        "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("LC_ALL", "C")

    def git(*args, input=None):
        return subprocess.run(
            ["git", *args], cwd=path, input=input, capture_output=True,
            check=True, timeout=10,
        ).stdout

    git("init", "-q")
    parent = None
    short_ids = []
    for index, subject in enumerate(subjects):
        (path / "a.py").write_text(f"value = {index}\n", encoding="utf-8")
        git("add", "a.py")
        tree = git("write-tree").strip()
        body = b"tree " + tree + b"\n"
        if parent is not None:
            body += b"parent " + parent + b"\n"
        body += (
            b"author T <t@example.com> 1000000000 +0000\n"
            b"committer T <t@example.com> 1000000000 +0000\n\n"
            + subject + b"\n"
        )
        parent = git("hash-object", "-t", "commit", "-w", "--stdin", input=body).strip()
        git("update-ref", "HEAD", parent.decode("ascii"))
        short_ids.append(git("rev-parse", "--short", "HEAD").decode("ascii").strip())
    return git, short_ids


def test_git_history_preserves_malformed_subjects_and_other_commits(tmp_path, monkeypatch):
    from code_forge.context_sources import GitHistorySource

    git, short_ids = _raw_git_history(
        tmp_path, monkeypatch, [b"valid older subject", b"keep \xff context", b"keep \xfe context"],
    )
    raw = git("log", "--encoding=utf-8", "--format=%h %s", "--", "a.py")
    assert b"keep \xff context" in raw and b"keep \xfe context" in raw
    expected = [
        FactRow("keep \ufffd context", "", "", short_ids[2], "git-history"),
        FactRow("keep \ufffd context", "", "", short_ids[1], "git-history"),
        FactRow("valid older subject", "", "", short_ids[0], "git-history"),
    ]
    assert len(set(short_ids)) == 3
    source = GitHistorySource(tmp_path)
    assert source.facts(["a.py"], "") == expected
    warnings = []
    result = gather([source], ["a.py"], "", head_sha=None, on_error=lambda *args: warnings.append(args))
    assert result.rows == expected
    assert result.errors == [] and warnings == []
    rendered = render_context_sources(result)
    assert "keep \ufffd context" in rendered
    assert all(short in rendered for short in short_ids)


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("first\u2028not-a-hash second".encode("utf-8"), "first\u2028not-a-hash second"),
        ("first\u2029second".encode("utf-8"), "first\u2029second"),
        (b"first\x0bsecond", "first\x0bsecond"),
        (b"first\x0csecond", "first\x0csecond"),
        (b"first\x1esecond", "first\x1esecond"),
        (b"first\x1fsecond", "first\x1fsecond"),
        (b"first\x1bsecond", "first\x1bsecond"),
        (b"first\tsecond", "first\tsecond"),
        (b"first\rsecond", "first\rsecond"),
        (b"first\nsecond", "first second"),
        (b"first\r\nsecond", "first second"),
        (b"", ""),
        (b"first\r", "first"),
    ],
)
def test_git_history_uses_git_record_boundaries(tmp_path, monkeypatch, subject, expected):
    from code_forge.context_sources import GitHistorySource

    _, short_ids = _raw_git_history(tmp_path, monkeypatch, [b"older subject", subject])
    source = GitHistorySource(tmp_path)
    rows = source.facts(["a.py"], "")
    assert rows == [
        FactRow(expected, "", "", short_ids[1], "git-history"),
        FactRow("older subject", "", "", short_ids[0], "git-history"),
    ]
    result = gather([source], ["a.py"], "", head_sha=None)
    assert result.rows == rows and result.errors == []


def test_git_history_runner_preserves_subject_controls(tmp_path):
    from code_forge.context_sources import GitHistorySource

    def runner(cmd, timeout):
        assert cmd == ["git", "log", "--encoding=utf-8", "-n", "5", "--format=%h %s", "--", "a.py"]
        assert timeout == 2
        return "abc123 first\u2028second\rthird\n\ndef456 \n"

    assert GitHistorySource(tmp_path, runner=runner, timeout=2).facts(["a.py"], "") == [
        FactRow("first\u2028second\rthird", "", "", "abc123", "git-history"),
        FactRow("", "", "", "def456", "git-history"),
    ]


@pytest.mark.parametrize("malformed_config", [False, True])
def test_git_history_native_failure_keeps_text_diagnostics(tmp_path, monkeypatch, malformed_config):
    from subprocess import CalledProcessError

    from code_forge.context_sources import GitHistorySource

    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    if malformed_config:
        _raw_git_history(tmp_path, monkeypatch, [b"valid subject"])
        (tmp_path / ".git" / "config").write_bytes(b"[core]\nrepositoryformatversion = \xff\n")
        diagnostic = "bad numeric config value '\ufffd'"
    else:
        diagnostic = "not a git repository"
    source = GitHistorySource(tmp_path)
    with pytest.raises(CalledProcessError) as failure:
        source.facts(["a.py"], "")
    assert failure.value.returncode == 128
    assert failure.value.stdout == ""
    assert isinstance(failure.value.stderr, str)
    assert diagnostic in failure.value.stderr
    warnings = []
    result = gather([source], ["a.py"], "", head_sha=None, on_error=lambda *args: warnings.append(args))
    assert result.rows == []
    assert len(result.errors) == len(warnings) == 1
    assert "CalledProcessError" in result.errors[0]


_DIFF = "+".join(["\n", "def alpha():\n", "    beta = 12345\n"])


def test_knowledge_is_off_unless_enabled(tmp_path):
    from code_forge.context_sources import KnowledgeSource

    calls = {"n": 0}

    def client(query, timeout):
        calls["n"] += 1
        return {"sources": [{"doc_title": "d"}]}

    assert KnowledgeSource(client=client).facts(["a.py"], _DIFF) == []
    assert calls["n"] == 0
    KnowledgeSource(client=client, enabled=True).facts(["a.py"], _DIFF)
    assert calls["n"] == 1


def test_query_drops_long_tokens_and_caps_identifiers():
    from code_forge.context_sources import query_terms

    long = "k" * 41
    added = "\n".join("+%s" % w for w in ["alpha", "12345", long, "beta"] + ["nn%d" % i for i in range(12)])
    text = query_terms(["src/mod.py"], added)
    assert "mod.py" in text
    assert "12345" not in text
    assert long not in text
    assert text.split().count("alpha") == 1
    assert len(text.split()) == 11


def test_knowledge_keeps_the_first_source_only():
    from code_forge.context_sources import KnowledgeSource

    def client(query, timeout):
        return {
            "answer": "x" * 500,
            "sources": [
                {"doc_title": "first", "chunk_id": "c1", "source_url": "/a"},
                {"doc_title": "second", "chunk_id": "c2", "source_url": "/b"},
            ],
        }

    rows = KnowledgeSource(client=client, enabled=True).facts(["a.py"], _DIFF)
    assert len(rows) == 1
    assert rows[0].entity == "first"
    assert rows[0].dependents.startswith("c1 ")
    assert len(rows[0].dependents) <= 404


def test_knowledge_timeout_is_recorded(tmp_path):
    from subprocess import TimeoutExpired

    from code_forge.context_sources import KnowledgeSource

    def client(query, timeout):
        raise TimeoutExpired("kb", timeout)

    res = gather(
        [KnowledgeSource(client=client, enabled=True, timeout=0.01)],
        ["a.py"], _DIFF, head_sha=None,
    )
    assert res.rows == []
    assert any("TimeoutExpired" in e for e in res.errors)


def test_knowledge_empty_sources_is_not_an_error():
    from code_forge.context_sources import KnowledgeSource

    rows = KnowledgeSource(
        client=lambda q, t: {"answer": "", "sources": []}, enabled=True,
    ).facts(["a.py"], _DIFF)
    assert rows == []


def test_knowledge_enabled_reads_a_config_without_a_test_section(tmp_path):
    from code_forge.context_sources import knowledge_enabled

    gate = tmp_path / ".code-forge"
    gate.mkdir()
    (gate / "gate.yaml").write_text("outlet: subprocess\nknowledge:\n  enabled: true\n")
    assert knowledge_enabled(tmp_path) is True


def test_knowledge_enabled_is_false_without_the_switch(tmp_path):
    from code_forge.context_sources import knowledge_enabled

    gate = tmp_path / ".code-forge"
    gate.mkdir()
    (gate / "gate.yaml").write_text("outlet: subprocess\n")
    assert knowledge_enabled(tmp_path) is False
    assert knowledge_enabled(tmp_path / "missing") is False
