# SPDX-License-Identifier: Apache-2.0
"""Root-bound identity regressions using real SQLite, liveness and runner output.

Only discovery's database locator and registry snapshot are faked.
No network, Git, subprocesses, SQL stubs or optional dependency skips are used.
"""

from contextlib import closing
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import types

import pytest

from code_forge import cross_repo_impact as impact
from code_forge import dead_code


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network/subprocess use is forbidden in root identity tests")

    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)


def diff(*paths):
    return "".join(f"--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n" for p in paths)


def database(path, root, metadata="valid"):
    path.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            "CREATE TABLE nodes(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, "
            "qualified_name TEXT, file_path TEXT, line_start INTEGER, line_end INTEGER);"
            "CREATE TABLE edges(kind TEXT, source_qualified TEXT, target_qualified TEXT);"
        )
        if metadata != "absent-table":
            conn.execute("CREATE TABLE metadata(key TEXT, value TEXT)")
            values = {
                "valid": [str(root)],
                "absent-row": [],
                "empty": [""],
                "null": [None],
                "duplicate": [str(root), str(root)],
                "relative": [root.name],
                "nul": [str(root) + "\0"],
            }.get(metadata, [metadata])
            conn.executemany("INSERT INTO metadata VALUES ('repo_root', ?)", [(v,) for v in values])
        conn.commit()
    return path


def node(db, path, name="process", qualified=None, line=2):
    qualified = qualified or f"{Path(path).stem}::{name}"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "INSERT INTO nodes(kind,name,qualified_name,file_path,line_start,line_end) "
            "VALUES ('function',?,?,?,?,?)",
            (name, qualified, str(path), line, line + 1),
        )
        conn.commit()
    return qualified


def source(root, relative="src/use.c", *, dead=False):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        ("#if 0\n" if dead else "")
        + "int use(void) {\n  return process();\n}\n"
        + ("#endif\n" if dead else ""),
        encoding="utf-8",
    )
    return path


def caller(db, path, module="api", qualified="use::caller", *, dead=False, with_node=True):
    if with_node:
        node(db, path, "caller", qualified, 3 if dead else 2)
    with closing(sqlite3.connect(db)) as conn:
        conn.executemany(
            "INSERT INTO edges VALUES (?,?,?)",
            [
                ("CALLS", qualified, "process"),
                ("CALLS", qualified, "process"),
                ("IMPORTS_FROM", qualified, module),
            ],
        )
        conn.commit()


@pytest.fixture
def world(tmp_path, monkeypatch):
    primary = tmp_path / "primary"
    sibling = tmp_path / "sibling"
    p_db = database(tmp_path / "data" / "primary.db", primary)
    s_db = database(tmp_path / "data" / "sibling.db", sibling)
    mapping = {str(primary): p_db, str(sibling): s_db}
    repos = [{"path": str(primary), "alias": "main"}, {"path": str(sibling), "alias": "consumer"}]
    package = types.ModuleType("code_review_graph")
    package.__path__ = []
    registry = types.ModuleType("code_review_graph.registry")
    registry.default_registry_path = lambda: tmp_path / "registry.json"
    monkeypatch.delenv("CRG_REGISTRY_PATH", raising=False)
    monkeypatch.setattr(impact, "_read_registry", lambda path: repos)
    monkeypatch.setattr(impact, "_readonly_db_path", lambda root, entries: mapping[str(root)])
    for module in (package, registry):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return primary, sibling, p_db, s_db, mapping, repos


def seed(world, *, absolute=True, filename="src/api.c"):
    primary, sibling, p_db, s_db, _, _ = world
    node(p_db, primary / filename if absolute else filename)
    caller(s_db, source(sibling))


def run(world, text=None, root=None):
    runner = impact.CrossRepoImpactRunner()
    findings = runner.run(diff("src/api.c") if text is None else text, root or world[0])
    return findings, runner.infra_errors


def assert_hit(findings):
    assert len(findings) == 1
    finding = findings[0]
    assert finding.file == "consumer:src/use.c"
    assert finding.line_range == (2, 2)
    assert finding.axis == "CROSS-REPO-IMPACT"
    assert finding.description == "process used by consumer at src/use.c:2"
    assert finding.attribution == "cross-repo graph.db"


@pytest.mark.parametrize("absolute", [False, True])
def test_real_runner_relative_and_absolute_primary_external_data_dir(world, absolute):
    seed(world, absolute=absolute)
    findings, errors = run(world)
    assert_hit(findings)
    assert errors == []
    assert world[2].parent != world[0] and world[3].parent != world[1]


@pytest.mark.parametrize("reader", ["primary", "sibling"])
def test_nonempty_read_requires_explicit_root(world, reader):
    seed(world)
    with pytest.raises(impact.GraphIdentityError, match="root"):
        if reader == "primary":
            impact.resolve_changed_symbols(diff("src/api.c"), str(world[2]))
        else:
            impact.find_cross_repo_callers(str(world[3]), [{"name": "process", "module": "api"}])


@pytest.mark.parametrize("side", [0, 1], ids=["primary", "sibling"])
@pytest.mark.parametrize(
    "metadata", ["absent-table", "absent-row", "empty", "null", "duplicate", "relative", "nul", "wrong"]
)
def test_invalid_metadata_is_infrastructure_not_partial_success(world, side, metadata, tmp_path):
    seed(world)
    db = world[2 + side]
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("DROP TABLE metadata")
        if metadata != "absent-table":
            conn.execute("CREATE TABLE metadata(key TEXT, value TEXT)")
            wrong = tmp_path / "another-checkout"
            wrong.mkdir(exist_ok=True)
            values = {
                "absent-row": [],
                "empty": [""],
                "null": [None],
                "duplicate": [str(world[side])] * 2,
                "relative": [world[side].name],
                "nul": [str(world[side]) + "\0"],
                "wrong": [str(wrong)],
            }[metadata]
            conn.executemany("INSERT INTO metadata VALUES ('repo_root',?)", [(v,) for v in values])
        conn.commit()
    findings, errors = run(world)
    assert findings == []
    assert len(errors) == 1 and "graph.db unreadable" in errors[0]


def test_same_basename_is_not_same_repository(world, tmp_path):
    seed(world)
    other = tmp_path / "elsewhere" / world[0].name
    other.mkdir(parents=True)
    world[4][str(other)] = world[2]
    findings, errors = run(world, root=other)
    assert findings == []
    assert "different repository root" in errors[0]


def test_one_database_cannot_be_bound_to_two_roots(world):
    seed(world)
    world[4][str(world[1])] = world[2]
    findings, errors = run(world)
    assert findings == []
    assert "different repository root" in errors[0]


@pytest.mark.parametrize("side", [0, 1], ids=["primary", "sibling"])
@pytest.mark.parametrize("metadata_lexical", [False, True])
@pytest.mark.parametrize("node_lexical", [False, True])
def test_symlink_repository_root_lexical_and_canonical(
    world, tmp_path, side, metadata_lexical, node_lexical
):
    root = world[side]
    alias = tmp_path / ("primary-link" if side == 0 else "sibling-link")
    alias.symlink_to(root, target_is_directory=True)
    world[4][str(alias)] = world[2 + side]
    if metadata_lexical:
        with closing(sqlite3.connect(world[2 + side])) as conn:
            conn.execute("UPDATE metadata SET value=?", (str(alias),))
            conn.commit()
    p_root = alias if side == 0 and node_lexical else world[0]
    s_root = alias if side == 1 and node_lexical else world[1]
    node(world[2], p_root / "src/api.c")
    source(world[1])
    caller(world[3], s_root / "src/use.c")
    if side == 1:
        world[5][1]["path"] = str(alias)
    findings, errors = run(world, root=alias if side == 0 else world[0])
    assert_hit(findings)
    assert errors == []


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/api.c",
        "../api.c",
        "src/../../api.c",
        "src/./api.c",
        "src//api.c",
        "src/a\0pi.c",
        "src\\api.c",
        "C:/api.c",
        "C:api.c",
    ],
)
def test_invalid_later_diff_path_never_leaks_earlier_hit(world, path):
    seed(world)
    findings, errors = run(world, diff("src/api.c", path))
    assert findings == []
    assert len(errors) == 1 and "primary" in errors[0]


@pytest.mark.parametrize(
    "filename,decoy",
    [
        ("foo_bar.c", "fooXbar.c"),
        ("foo%bar.c", "fooXYZbar.c"),
        ("foo!bar.c", "foobar.c"),
        ("Foo.c", "foo.c"),
    ],
)
def test_literal_path_matching_excludes_wildcards_case_and_vendor(world, filename, decoy):
    primary, sibling, p_db, s_db, _, _ = world
    target = "src/" + filename
    node(p_db, target, "intended")
    node(p_db, "src/" + decoy, "wildcard_decoy")
    node(p_db, "vendor/" + target, "vendor_decoy")
    node(p_db, primary.parent / "foreign" / target, "foreign_decoy")
    rows = impact.resolve_changed_symbols(diff(target), str(p_db), primary)
    assert [row["name"] for row in rows] == ["intended"]
    assert rows[0]["file_path"] == target


def test_mixed_relative_absolute_identity_refused(world):
    seed(world)
    node(world[2], "src/api.c", "another")
    findings, errors = run(world)
    assert findings == []
    assert "ambiguous" in errors[0]


@pytest.mark.parametrize("side", [0, 1], ids=["primary", "sibling"])
@pytest.mark.parametrize("kind", ["internal", "external", "cycle"])
def test_file_symlink_containment(world, tmp_path, monkeypatch, side, kind):
    if kind != "internal":

        def forbidden_read(*args):
            pytest.fail("escaping or cyclic source must not reach the liveness reader")

        monkeypatch.setattr(dead_code, "_is_dead_call_site", forbidden_read)
    primary, sibling, p_db, s_db, _, _ = world
    root = world[side]
    relative = "src/api.c" if side == 0 else "src/use.c"
    link = root / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    if kind == "cycle":
        link.symlink_to(link.name)
    else:
        target_root = root if kind == "internal" else tmp_path / "outside"
        target = source(target_root, "real.c")
        link.symlink_to(target)
    node(p_db, primary / "src/api.c")
    if side == 0:
        source(sibling)
    caller(s_db, sibling / "src/use.c")
    findings, errors = run(world)
    if kind == "internal":
        assert_hit(findings)
        assert errors == []
    else:
        assert findings == []
        assert len(errors) == 1


@pytest.mark.parametrize("invalid", ["missing-node", "null", "external", "traversal"])
def test_bad_sibling_path_is_rejected_before_liveness_reads(world, monkeypatch, invalid):
    seed(world)
    path = {
        "missing-node": "unused",
        "null": None,
        "external": str(world[0] / "secret.c"),
        "traversal": "../secret.c",
    }[invalid]
    caller(world[3], path or "placeholder", qualified="bad::caller", with_node=invalid != "missing-node")
    if invalid == "null":
        with closing(sqlite3.connect(world[3])) as conn:
            conn.execute("UPDATE nodes SET file_path=NULL WHERE qualified_name='bad::caller'")
            conn.commit()
    reads = []
    real_read = dead_code._is_dead_call_site

    def guarded_read(path, line):
        reads.append(path)
        assert path == str(world[1] / "src/use.c"), "invalid source reached liveness reader"
        return real_read(path, line)

    monkeypatch.setattr(dead_code, "_is_dead_call_site", guarded_read)
    findings, errors = run(world)
    assert findings == []
    assert len(errors) == 1 and "sibling" in errors[0]
    assert all(path == str(world[1] / "src/use.c") for path in reads)


def test_missing_sibling_node_cannot_leak_valid_earlier_result(world):
    seed(world)
    caller(world[3], "unused", qualified="bad::caller", with_node=False)
    findings, errors = run(world)
    assert findings == []
    assert len(errors) == 1 and "sibling" in errors[0]


def test_bad_sibling_does_not_erase_healthy_sibling(world, tmp_path):
    seed(world)
    bad_root = tmp_path / "bad-sibling"
    bad_db = database(tmp_path / "bad.db", bad_root, "absent-row")
    world[4][str(bad_root)] = bad_db
    world[5].append({"path": str(bad_root), "alias": "broken"})
    findings, errors = run(world)
    assert_hit(findings)
    assert len(errors) == 1 and "broken" in errors[0]


def test_real_dead_c_caller_is_not_emitted(world):
    node(world[2], "src/api.c")
    caller(world[3], source(world[1], dead=True), dead=True)
    findings, errors = run(world)
    assert findings == []
    assert errors == []


def test_same_named_symbols_and_underscore_import_do_not_multiply_hits(world):
    node(world[2], "src/foo_bar.c", "process", "foo_bar::process")
    node(world[2], "src/fooXbar.c", "process", "fooXbar::process")
    caller(world[3], source(world[1]), module="foo_bar")
    findings, errors = run(world, diff("src/foo_bar.c"))
    assert_hit(findings)
    assert errors == []


@pytest.mark.parametrize("reverse", [False, True])
def test_same_named_changed_files_keep_only_the_matching_module(world, reverse):
    paths = ["src/alpha.c", "src/beta.c"]
    for path in paths:
        node(world[2], path, "process", f"{Path(path).stem}::process")
    caller(world[3], source(world[1]), module="alpha")
    if reverse:
        paths.reverse()
    findings, errors = run(world, diff(*paths))
    assert_hit(findings)
    assert errors == []


@pytest.mark.parametrize("reverse", [False, True])
def test_same_named_changed_files_keep_caller_ranking_bound_to_target(world, reverse):
    paths = ["near/alpha.c", "far/beta.c"]
    for path in paths:
        node(world[2], path, "process", f"{Path(path).stem}::process")
    caller(world[3], source(world[1], "far/alpha_user.c"), "alpha", "alpha_user::caller")
    caller(world[3], source(world[1], "far/beta_user.c"), "beta", "beta_user::caller")
    if reverse:
        paths.reverse()
    findings, errors = run(world, diff(*paths))
    assert [f.file for f in findings] == [
        "consumer:far/beta_user.c",
        "consumer:far/alpha_user.c",
    ]
    assert [f.id for f in findings] == ["cross-repo-impact-1", "cross-repo-impact-2"]
    assert errors == []


def test_same_named_changed_files_do_not_crowd_out_top_n_callers(world):
    # Only alpha has callers. beta has the same name and directory, but no
    # import edge. The six close callers must not be counted twice ahead of
    # the four farther callers in the existing ten-finding budget.
    paths = ["near/alpha.c", "near/beta.c"]
    for path in paths:
        node(world[2], path, "process", f"{Path(path).stem}::process")
    expected = []
    for index in range(12):
        relative = f"{'near' if index < 6 else 'far'}/use{index}.c"
        caller(world[3], source(world[1], relative), "alpha", f"use{index}::caller")
        expected.append(f"consumer:{relative}")
    findings, errors = run(world, diff(*paths))
    assert len(findings) == impact._TOP_N == 10
    assert len({f.file for f in findings}) == 10
    files = [f.file for f in findings]
    assert set(files[:6]) == set(expected[:6])
    assert set(files[6:]).issubset(expected[6:])
    assert errors == []


def test_same_named_changed_files_keep_legacy_caller_rows(world):
    paths = ["src/alpha.c", "src/beta.c"]
    for path in paths:
        node(world[2], path, "process", f"{Path(path).stem}::process")
    caller(world[3], source(world[1]), module="alpha")
    changed = impact.resolve_changed_symbols(diff(*paths), str(world[2]), world[0])
    assert impact.find_cross_repo_callers(str(world[3]), changed, world[1]) == [
        {
            "symbol": "process",
            "caller_qualified": "use::caller",
            "caller_file": str(world[1] / "src/use.c"),
            "caller_line": 2,
        }
    ]


@pytest.mark.parametrize("api", ["runner", "helper"])
def test_later_changed_symbol_failure_rejects_the_whole_sibling(world, api):
    paths = ["src/alpha.c", "src/beta.c"]
    for path in paths:
        node(world[2], path, "process", f"{Path(path).stem}::process")
    caller(world[3], source(world[1]), module="alpha")
    caller(world[3], "missing.c", "beta", "missing::caller", with_node=False)
    if api == "runner":
        findings, errors = run(world, diff(*paths))
        assert findings == []
        assert len(errors) == 1 and "caller graph path unavailable" in errors[0]
    else:
        changed = impact.resolve_changed_symbols(diff(*paths), str(world[2]), world[0])
        with pytest.raises(impact.GraphIdentityError, match="caller graph path unavailable"):
            impact.find_cross_repo_callers(str(world[3]), changed, world[1])


def test_same_caller_matched_to_distinct_targets_is_not_deduplicated(world):
    paths = ["src/alpha.c", "src/beta.c"]
    for path in paths:
        node(world[2], path, "process", f"{Path(path).stem}::process")
    caller(world[3], source(world[1]), module="alpha")
    with closing(sqlite3.connect(world[3])) as conn:
        conn.execute("INSERT INTO edges VALUES ('IMPORTS_FROM', 'use::caller', 'beta')")
        conn.commit()
    findings, errors = run(world, diff(*paths))
    assert len(findings) == 2
    assert findings[0].file == findings[1].file == "consumer:src/use.c"
    assert errors == []


@pytest.mark.parametrize("text", ["", " \n", "no diff paths", "--- a/src/api.c\n+++ /dev/null\n"])
def test_empty_work_retains_no_result_behavior(world, text):
    findings, errors = run(world, text)
    assert findings == []
    assert errors == []
    assert impact.resolve_changed_symbols(text, str(world[2])) == []
    assert impact.find_cross_repo_callers(str(world[3]), []) == []


@pytest.mark.parametrize(
    "sibling_dead", [False, True], ids=["live-sibling-dead-cwd", "dead-sibling-live-cwd"]
)
def test_relative_sibling_liveness_uses_bound_root_not_cwd(world, tmp_path, monkeypatch, sibling_dead):
    node(world[2], "src/api.c")
    source(world[1], dead=sibling_dead)
    caller(world[3], "src/use.c", dead=sibling_dead)
    decoy_root = tmp_path / "working-directory"
    source(decoy_root, dead=not sibling_dead)
    monkeypatch.chdir(decoy_root)
    findings, errors = run(world)
    assert errors == []
    if sibling_dead:
        assert findings == []
    else:
        assert_hit(findings)
        changed = impact.resolve_changed_symbols(diff("src/api.c"), str(world[2]), world[0])
        assert changed == [
            {
                "name": "process",
                "qualified_name": "api::process",
                "file_path": "src/api.c",
                "module": "api",
            }
        ]
        rows = impact.find_cross_repo_callers(str(world[3]), changed, world[1])
        assert rows == [
            {
                "symbol": "process",
                "caller_qualified": "use::caller",
                "caller_file": "src/use.c",
                "caller_line": 2,
            }
        ]


def test_shared_live_callers_default_keeps_original_path(world, monkeypatch):
    caller(world[3], "src/use.c")
    reads = []
    monkeypatch.setattr(
        dead_code, "_is_dead_call_site", lambda path, line: reads.append((path, line)) or False
    )
    with closing(sqlite3.connect(world[3])) as conn:
        rows = dead_code._live_callers(conn.cursor(), "process", "api")
    assert reads == [("src/use.c", 2)]
    assert [(row.qualified, row.file, row.line) for row in rows] == [("use::caller", "src/use.c", 2)]


def test_shared_live_callers_resolves_before_read_but_retains_graph_spelling(world, monkeypatch):
    caller(world[3], "src/use.c")
    events = []
    resolved = str(world[1] / "src/use.c")

    def resolve(path):
        events.append(("resolve", path))
        return resolved

    def read(path, line):
        events.append(("read", path, line))
        return False

    monkeypatch.setattr(dead_code, "_is_dead_call_site", read)
    with closing(sqlite3.connect(world[3])) as conn:
        rows = dead_code._live_callers(conn.cursor(), "process", "api", resolve_file=resolve)
    assert events == [("resolve", "src/use.c"), ("read", resolved, 2)]
    assert [(row.file, row.line) for row in rows] == [("src/use.c", 2)]


def test_shared_live_callers_resolver_failure_prevents_read(world, monkeypatch):
    caller(world[3], "../secret.c")
    events = []

    def resolve(path):
        events.append(("resolve", path))
        raise impact.GraphIdentityError("refuse invalid path")

    def forbidden_read(*args):
        pytest.fail("resolver failure must prevent source read")

    monkeypatch.setattr(dead_code, "_is_dead_call_site", forbidden_read)
    with closing(sqlite3.connect(world[3])) as conn:
        with pytest.raises(impact.GraphIdentityError, match="refuse invalid path"):
            dead_code._live_callers(conn.cursor(), "process", "api", resolve_file=resolve)
    assert events == [("resolve", "../secret.c")]


def test_mixed_relative_absolute_sibling_identity_refused(world):
    seed(world)
    node(world[3], "src/use.c", "another", "another::caller")
    findings, errors = run(world)
    assert findings == []
    assert len(errors) == 1 and "ambiguous" in errors[0]


def nocase_paths(db):
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(
            "ALTER TABLE nodes RENAME TO old_nodes;"
            "CREATE TABLE nodes(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, "
            "qualified_name TEXT, file_path TEXT COLLATE NOCASE, line_start INTEGER, line_end INTEGER);"
            "INSERT INTO nodes SELECT * FROM old_nodes;"
            "DROP TABLE old_nodes;"
        )


def test_exact_primary_identity_overrides_nocase_schema(world):
    nocase_paths(world[2])
    node(world[2], "src/Api.c", "intended")
    node(world[2], "src/api.c", "decoy")
    rows = impact.resolve_changed_symbols(diff("src/Api.c"), str(world[2]), world[0])
    assert [row["name"] for row in rows] == ["intended"]


def test_nocase_distinct_cannot_hide_case_distinct_bound_root_aliases(world, tmp_path):
    seed(world)
    alias = tmp_path / "SIBLING"
    if alias.exists():
        pytest.skip("case-distinct root aliases require a case-sensitive filesystem")
    alias.symlink_to(world[1], target_is_directory=True)
    nocase_paths(world[3])
    node(world[3], alias / "src/use.c", "another", "another::caller")
    with closing(sqlite3.connect(world[3])) as conn:
        conn.execute("UPDATE metadata SET value=? WHERE key='repo_root'", (str(alias),))
        conn.commit()
    findings, errors = run(world)
    assert findings == []
    assert len(errors) == 1 and "ambiguous" in errors[0]


def test_discovery_missing_provider_helper_never_invokes_mutating_api(tmp_path, monkeypatch):
    package = types.ModuleType("code_review_graph")
    package.__path__ = []
    registry = types.ModuleType("code_review_graph.registry")
    incremental = types.ModuleType("code_review_graph.incremental")

    def forbidden(*args, **kwargs):
        pytest.fail("provider mutating APIs must never be invoked")

    registry.Registry = forbidden
    incremental.get_db_path = forbidden
    incremental.get_data_dir = forbidden
    for module in (package, registry, incremental):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    runner = impact.CrossRepoImpactRunner()
    assert runner.run(diff("api.py"), tmp_path) == []
    assert runner.infra_errors == [
        "cross-repo-impact: registry location unavailable; code-review-graph >=2.3.8 required"
    ]


def test_discovery_pure_provider_helper_is_sufficient(tmp_path, monkeypatch):
    root = tmp_path / "primary"
    db = database(root / ".code-review-graph" / "graph.db", root)
    node(db, "api.py")
    package = types.ModuleType("code_review_graph")
    package.__path__ = []
    registry = types.ModuleType("code_review_graph.registry")
    registry.default_registry_path = lambda: tmp_path / "absent" / "registry.json"
    for module in (package, registry):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.delenv("CRG_DATA_DIR", raising=False)
    monkeypatch.delenv("CRG_REGISTRY_PATH", raising=False)
    runner = impact.CrossRepoImpactRunner()
    assert runner.run(diff("api.py"), root) == []
    assert runner.infra_errors == [
        "cross-repo-impact: no sibling repos registered; use code-review-graph register"
    ]
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"repos": None},
        {"repos": {}},
        {"repos": [None]},
        {"repos": [{}]},
        {"repos": [{"path": ""}]},
        {"repos": [{"path": 1}]},
        {"repos": [{"path": "bad\0path"}]},
        {"repos": [{"path": "/repo", "data_dir": None}]},
        {"repos": [{"path": "/repo", "data_dir": "bad\0path"}]},
        {"repos": [{"path": "/repo", "alias": []}]},
    ],
    ids=[
        "null",
        "array",
        "no-repos",
        "null-repos",
        "object-repos",
        "null-entry",
        "no-path",
        "empty-path",
        "numeric-path",
        "nul-path",
        "null-data-dir",
        "nul-data-dir",
        "array-alias",
    ],
)
def test_discovery_rejects_unsupported_registry_shapes(tmp_path, data):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(impact.GraphDiscoveryError, match="unsupported registry"):
        impact._read_registry(path)


@pytest.mark.parametrize("payload", [b"{", b"\xff"], ids=["invalid-json", "invalid-utf8"])
def test_discovery_rejects_unreadable_registry_bytes(tmp_path, payload):
    path = tmp_path / "registry.json"
    path.write_bytes(payload)
    with pytest.raises(impact.GraphDiscoveryError, match="registry unreadable"):
        impact._read_registry(path)


def test_discovery_missing_registry_is_empty_without_creation(tmp_path):
    path = tmp_path / "absent" / "registry.json"
    assert impact._read_registry(path) == []
    assert not path.parent.exists()


def test_discovery_permission_error_is_not_an_empty_registry(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"

    def denied(*args, **kwargs):
        raise PermissionError("fixture")

    monkeypatch.setattr(impact, "_source_opener", denied)
    with pytest.raises(impact.GraphDiscoveryError, match="registry unreadable"):
        impact._read_registry(path)


@pytest.mark.parametrize("first_data", [None, ""], ids=["absent-dir", "empty-dir"])
def test_discovery_first_matching_registry_entry_wins(tmp_path, monkeypatch, first_data):
    root = tmp_path / "repo"
    root.mkdir()
    first = {"path": str(root)}
    if first_data is not None:
        first["data_dir"] = first_data
    repos = [first, {"path": str(root), "data_dir": str(tmp_path / "later")}]
    monkeypatch.setenv("CRG_DATA_DIR", str(tmp_path / "env"))
    assert impact._readonly_db_path(root, repos) == tmp_path / "env" / "graph.db"
    assert not (tmp_path / "env").exists()


def test_discovery_registry_dir_precedes_environment_without_tilde_expansion(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CRG_DATA_DIR", str(tmp_path / "env"))
    repos = [{"path": str(root), "data_dir": "~/literal"}]
    assert impact._readonly_db_path(root, repos) == tmp_path / "~" / "literal" / "graph.db"
    assert not (tmp_path / "~").exists()


def test_discovery_environment_expands_tilde_and_trims_whitespace(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("CRG_DATA_DIR", "  ~/external  ")
    assert impact._readonly_db_path(root, []) == tmp_path / "home" / "external" / "graph.db"
    assert not (tmp_path / "home").exists()


def test_discovery_repo_lookup_canonicalizes_query_not_stored_entry(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    alias = tmp_path / "link"
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.delenv("CRG_DATA_DIR", raising=False)
    registered = [{"path": str(root), "data_dir": str(tmp_path / "external")}]
    assert impact._readonly_db_path(alias, registered) == tmp_path / "external" / "graph.db"
    stored_alias = [{"path": str(alias), "data_dir": str(tmp_path / "ignored")}]
    assert impact._readonly_db_path(root, stored_alias) == root / ".code-review-graph" / "graph.db"
