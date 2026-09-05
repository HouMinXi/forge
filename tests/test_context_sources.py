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
from pathlib import Path

import pytest

from code_forge.advisory import AdvisoryFinding
from code_forge.context_sources import (
    FactRow, GatherResult, GraphTriageSource, _adapt_advisory, gather,
    render_blast_radius, render_context_sources,
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
        id="a", axis="graph_triage", file=file, line_range=line,
        description=desc, attribution="graph",
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
    return FactRow(entity=entity, file=file, downstream="3",
                   dependents="g, h", source=source, **kw)


# -- invariant 1 ------------------------------------------------------------

def test_render_matches_cli_verbatim():
    rows = [
        _adapt_advisory(_adv("f (impact: 3 downstream) -- top dependents: g, h", "f.py"), "graph_triage"),
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


def test_matching_snapshot_runs():
    src = _Src("g", snap="a" * 40, rows=[_row()])
    res = gather([src], ["f.py"], "diff", head_sha="a" * 40)
    assert len(res.rows) == 1 and res.skipped == []


def test_unknown_head_skips_snapshotted_source():
    src = _Src("g", snap="a" * 40, rows=[_row()])
    res = gather([src], ["f.py"], "diff", head_sha=None)
    assert res.rows == [] and len(res.skipped) == 1


def test_allow_unsnapshotted_overrides_gate():
    src = _Src("g", snap="a" * 40, rows=[_row()])
    res = gather([src], ["f.py"], "diff", head_sha="b" * 40, allow_unsnapshotted=True)
    assert len(res.rows) == 1 and res.skipped == []


def test_on_demand_source_ignores_gate():
    src = _Src("sem", snap=None, rows=[_row()])
    res = gather([src], ["f.py"], "diff", head_sha=None)
    assert len(res.rows) == 1 and res.skipped == []


# -- invariant 4 ------------------------------------------------------------

def test_raising_source_is_recorded_not_swallowed():
    seen = []
    bad = _Src("bad", raises=RuntimeError("boom"))
    good = _Src("good", rows=[_row(source="good")])
    res = gather([bad, good], ["f.py"], "diff", head_sha=None,
                 on_error=lambda n, m: seen.append((n, m)))
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
    con.commit(); con.close()
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
