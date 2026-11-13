"""Malformed SQLite values are unavailable, not escaped advisory exceptions."""

from contextlib import closing
import json
import shutil
import sqlite3

import pytest

from code_forge import cross_repo_impact as impact
from tests import test_cross_repo_immediate_diagnostic as immediate
from tests.test_cross_repo_capture import add_named_target_and_caller

graphs = immediate.graphs


def update(repo, statement, values=()):
    with closing(sqlite3.connect(repo / ".code-review-graph/graph.db")) as conn:
        conn.execute(statement, values)
        conn.commit()


@pytest.mark.parametrize("field", ["name", "qualified_name"])
@pytest.mark.parametrize("value", [None, b"private-invalid"])
def test_primary_invalid_text_is_unavailable(graphs, field, value):
    primary, sibling, _, _ = graphs
    add_named_target_and_caller(primary, sibling)
    update(
        primary,
        {
            "name": "UPDATE nodes SET name=?",
            "qualified_name": "UPDATE nodes SET qualified_name=?",
            "line_start": "UPDATE nodes SET line_start=?",
            "file_path": "UPDATE nodes SET file_path=?",
        }[field],
        (value,),
    )
    runner = impact.CrossRepoImpactRunner()
    assert runner.run(immediate.DIFF, primary) == []
    assert runner.infra_errors == [
        "cross-repo-impact: primary graph.db unreadable: invalid changed-symbol text fields"
    ]
    assert runner._cached is None


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("line_start", "not-a-line", "invalid caller source line"),
        ("line_start", b"private-invalid", "invalid caller source line"),
        ("line_start", 1.5, "invalid caller source line"),
        ("line_start", -1, "invalid caller source line"),
        ("file_path", None, "invalid caller file path"),
        ("file_path", b"private-invalid", "invalid caller file path"),
        ("file_path", "", "invalid caller file path"),
    ],
)
def test_bad_sibling_preserves_healthy_result_and_counts(graphs, field, value, reason):
    primary, sibling, registry, _ = graphs
    add_named_target_and_caller(primary, sibling)
    healthy = primary.parent / "healthy"
    shutil.copytree(sibling, healthy)
    update(healthy, "UPDATE metadata SET value=? WHERE key='repo_root'", (str(healthy),))
    entries = json.loads(registry.read_text())
    entries["repos"].append({"path": str(healthy), "alias": "healthy"})
    registry.write_text(json.dumps(entries))
    update(
        sibling,
        {
            "name": "UPDATE nodes SET name=?",
            "qualified_name": "UPDATE nodes SET qualified_name=?",
            "line_start": "UPDATE nodes SET line_start=?",
            "file_path": "UPDATE nodes SET file_path=?",
        }[field],
        (value,),
    )
    messages = []
    runner = impact.CrossRepoImpactRunner(output_fn=messages.append)
    findings = runner.run(immediate.DIFF, primary)
    assert len(findings) == 1 and findings[0].file == "healthy:api.py"
    assert runner.infra_errors == [
        f"cross-repo-impact: sibling 'consumer' graph.db unreadable: {reason}"
    ]
    assert messages == [immediate.QUERY_SUMMARY % (1, 2, 2, 1)]


@pytest.mark.parametrize("value", [b"private-invalid", ""])
def test_invalid_caller_name_fails_before_node_lookup(graphs, value):
    primary, sibling, _, _ = graphs
    add_named_target_and_caller(primary, sibling)
    update(sibling, "UPDATE edges SET source_qualified=? WHERE kind='CALLS'", (value,))
    # Keep the import selector naturally reachable even for the malformed name.
    update(sibling, "UPDATE edges SET source_qualified='' WHERE kind='IMPORTS_FROM'")
    runner = impact.CrossRepoImpactRunner()
    assert runner.run(immediate.DIFF, primary) == []
    assert runner.infra_errors == [
        "cross-repo-impact: sibling 'consumer' graph.db unreadable: invalid caller qualified name"
    ]


@pytest.mark.parametrize("line", [None, 0, 1, 2**63 - 1])
def test_supported_line_boundaries_preserved(graphs, line):
    primary, sibling, _, _ = graphs
    add_named_target_and_caller(primary, sibling)
    update(sibling, "UPDATE nodes SET line_start=?", (line,))
    runner = impact.CrossRepoImpactRunner()
    findings = runner.run(immediate.DIFF, primary)
    assert len(findings) == 1
    assert findings[0].line_range == (line or 0, line or 0)
    assert runner.infra_errors == []
