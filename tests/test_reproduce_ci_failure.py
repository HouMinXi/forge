"""The resolver is the part of the local tool that has to be right.

CI publishes only `public-N`, so a mistake here sends a developer to the wrong
test and wastes exactly the round trip the tool exists to save.
"""

import json

import pytest

from scripts import reproduce_ci_failure as repro


def _write_manifest(tmp_path, nodes):
    path = tmp_path / "identities.json"
    payload = {"modules": [], "nodes": nodes, "schema": "forge-public-test-identities-v1"}
    path.write_text(json.dumps(payload))
    return path


def test_resolves_an_ordinal_to_the_exact_node(tmp_path):
    nodes = [
        "tests/test_a.py::test_one",
        "tests/test_a.py::TestK::test_two[1]",
    ]
    path = _write_manifest(tmp_path, nodes)
    parsed = repro._nodes_from(path.read_text(), str(path))
    assert repro._resolve("public-0", parsed) == "tests/test_a.py::test_one"
    assert repro._resolve("public-1", parsed) == "tests/test_a.py::TestK::test_two[1]"


def test_the_position_is_the_contract_not_a_search(tmp_path):
    """A duplicate node id still resolves by position; the ordinal is an index."""
    nodes = ["tests/test_a.py::test_dup"] * 2
    path = _write_manifest(tmp_path, nodes)
    assert (
        repro._resolve("public-1", repro._nodes_from(path.read_text(), str(path)))
        == "tests/test_a.py::test_dup"
    )


def test_stops_when_the_ordinal_is_past_the_end(tmp_path):
    path = _write_manifest(tmp_path, ["tests/test_a.py::test_one"])
    with pytest.raises(SystemExit):
        repro._resolve("public-9", repro._nodes_from(path.read_text(), str(path)))


@pytest.mark.parametrize("ordinal", ["20349", "public-", "public-01", "public-1x", "public--1", ""])
def test_rejects_a_malformed_ordinal(ordinal):
    with pytest.raises(SystemExit):
        repro._resolve(ordinal, ["tests/test_a.py::test_one"])


def test_manifest_without_a_node_list_is_refused():
    with pytest.raises(SystemExit):
        repro._nodes_from(json.dumps({"modules": []}), "inline")


def test_manifest_that_is_not_json_is_refused():
    with pytest.raises(SystemExit):
        repro._nodes_from("{not json", "inline")


def test_the_shipped_manifest_passes_its_own_validator():
    """The file this tool reads is also what CI validates.

    Anchoring a specific index would punish the correct fix: the manifest is
    sorted, so adding an entry legitimately moves every later ordinal.
    """
    from scripts import ci_private_transport as transport

    document = json.loads(repro.MANIFEST.read_text())
    transport.validate_manifest(document)
    assert document["nodes"] == sorted(document["nodes"])


def test_every_shipped_node_resolves_through_the_guard():
    """The guard must accept the real manifest, not just hostile input."""
    for node in repro._manifest_nodes():
        repro._checked_node(node)


@pytest.mark.parametrize(
    "node",
    [
        "-pevil_plugin",
        "--basetemp=/tmp/x",
        "tests/../../../etc/passwd",
        "tests/a.py; rm -rf /",
        "",
        "tests/",
        "src/code_forge/cli.py",
    ],
)
def test_refuses_a_node_that_could_reach_pytest_as_an_option(node):
    with pytest.raises(SystemExit):
        repro._checked_node(node)


def test_accepts_a_parameterised_node():
    """Parameter ids carry spaces, colons and dots; only the path is guarded."""
    assert repro._checked_node("tests/test_a.py::TestK::test_x[a b: c]")
    assert repro._checked_node("tests/test_a.py::test_x[../outside.py]")


@pytest.mark.parametrize("run", ["--repo attacker/repo", "123abc", "", "../../etc"])
def test_rejects_a_run_id_that_is_not_a_decimal_number(run, tmp_path):
    """A flag-shaped id would otherwise reach gh as a second --repo."""
    with pytest.raises(SystemExit):
        repro._fetch_plan(run, tmp_path)


def test_plan_without_a_sha_is_refused():
    with pytest.raises(SystemExit):
        repro._plan_sha({"phases": {}})


def test_the_run_is_bounded(monkeypatch):
    """Without a timeout a hung pytest would hold the terminal forever."""
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs)

        class Done:
            returncode = 0

        return Done()

    monkeypatch.setattr(repro.subprocess, "run", fake_run)
    assert repro._run_node("tests/test_a.py::test_one") == 0
    assert seen.get("timeout") == repro.PYTEST_TIMEOUT_S


def test_the_node_is_passed_as_one_argument(monkeypatch):
    """The node id goes to pytest whole, never re-split or shell-expanded."""
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command

        class Done:
            returncode = 0

        return Done()

    monkeypatch.setattr(repro.subprocess, "run", fake_run)
    repro._run_node("tests/test_a.py::TestK::test_two[1-2]")
    assert "tests/test_a.py::TestK::test_two[1-2]" in seen["command"]


def test_a_dry_run_does_not_spawn_pytest(monkeypatch, tmp_path, capsys):
    """Resolution alone must stay cheap; only a real run starts a process."""
    spawned = []

    def fake_run(command, **kwargs):
        spawned.append(command)

        class Done:
            returncode = 0
            stdout = '{"sha": "' + "a" * 40 + '"}'
            stderr = ""

        return Done()

    monkeypatch.setattr(repro.subprocess, "run", fake_run)
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / repro.PLAN_FILE).write_text(json.dumps({"sha": "a" * 40}))

    def fake_fetch(run, workdir):
        return plan_dir / repro.PLAN_FILE

    monkeypatch.setattr(repro, "_fetch_plan", fake_fetch)
    monkeypatch.setattr(repro, "_checkout_sha", lambda: "a" * 40)
    monkeypatch.setattr(repro, "_manifest_nodes", lambda sha=None: ["tests/test_a.py::test_one"])
    assert repro.main(["--run", "1", "--id", "public-0", "--dry-run"]) == 0
    assert spawned == []


def test_the_module_list_is_not_a_projection_of_the_node_list():
    """The manifest lists modules that have no node of their own.

    `tests/unit.py` is in `modules` and contributes nothing to `nodes`, so
    rebuilding the module list from the node ids silently drops it.
    """
    import json

    document = json.loads(repro.MANIFEST.read_text())
    modules, nodes = set(document["modules"]), document["nodes"]
    without_nodes = {
        module for module in modules if not any(node.startswith(module + "::") for node in nodes)
    }
    assert "tests/unit.py" in without_nodes


def test_reads_the_manifest_at_the_run_revision(monkeypatch):
    """The ordinal is a position, so a later manifest renumbers it.

    Reading the working tree is how `public-N` ends up naming a different test
    than the one CI published for that run.
    """
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command

        class Done:
            returncode = 0
            stdout = json.dumps({"nodes": ["tests/old.py::test_from_that_commit"]})
            stderr = ""

        return Done()

    monkeypatch.setattr(repro.subprocess, "run", fake_run)
    nodes = repro._manifest_nodes("a" * 40)
    assert nodes == ["tests/old.py::test_from_that_commit"]
    assert any("a" * 40 in str(part) for part in seen["command"])


def test_falls_back_to_the_working_tree_when_git_cannot_answer(monkeypatch):
    """A shallow clone or a missing commit must not stop the tool."""

    def fake_run(command, **kwargs):
        class Done:
            returncode = 128
            stdout = ""
            stderr = "fatal: bad object"

        return Done()

    monkeypatch.setattr(repro.subprocess, "run", fake_run)
    assert repro._manifest_nodes("b" * 40) == repro._nodes_from(
        repro.MANIFEST.read_text(), str(repro.MANIFEST)
    )
