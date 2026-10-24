# SPDX-License-Identifier: Apache-2.0
"""Phase 59.1 Task 5: `code-forge lenses --pass <p>` and the SKILL.md text that calls it."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from code_forge.lenses import LENS_BLOCK, selection_line
from code_forge.trust import record_trust

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "src" / "code_forge" / "skills" / "code-forge" / "SKILL.md"
PASSES = ("qodo", "expert", "adversarial")

FALLBACK = (
    "If the command exits non-zero, tell the user its stderr and continue without lenses "
    "(do not STOP)."
)
SKEW = (
    "If this exits 2 with 'unrecognized arguments', the installed CLI predates lenses; "
    "continue without them. A newer CLI with an older installed skill never appends lenses "
    "until `code-forge install-skill` is rerun."
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    e = dict(os.environ)
    e["XDG_CONFIG_HOME"] = str(cfg)
    e["HOME"] = str(tmp_path / "home")
    e["PYTHONPATH"] = str(ROOT / "src")
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    return e


def _repo(tmp_path: Path, data: dict | str | None, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir(exist_ok=True)
    if data is not None:
        gate = repo / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(exist_ok=True)
        gate.write_text(data if isinstance(data, str) else yaml.safe_dump(data), encoding="utf-8")
    return repo


def _trust(repo: Path) -> None:
    gate = repo / ".code-forge" / "gate.yaml"
    record_trust(gate, yaml.safe_load(gate.read_text(encoding="utf-8")))


def _lenses(repo: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "code_forge", "lenses", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("p", PASSES)
def test_effective_switch_prints_exactly_block_and_selection_line(tmp_path, env, p):
    repo = _repo(tmp_path, {"review_lenses": True})
    _trust(repo)
    r = _lenses(repo, env, "--pass", p)
    assert r.returncode == 0, r.stderr
    assert r.stdout == LENS_BLOCK + selection_line(p)
    assert r.stderr == ""


def test_each_pass_gets_its_own_selection_line(tmp_path, env):
    repo = _repo(tmp_path, {"review_lenses": True})
    _trust(repo)
    outs = {p: _lenses(repo, env, "--pass", p).stdout for p in PASSES}
    assert len(set(outs.values())) == 3


@pytest.mark.parametrize(
    "data",
    [{"review_lenses": False}, {"other": 1}, None, {}],
    ids=["false", "absent-key", "missing-gate", "empty"],
)
def test_off_prints_nothing_and_exits_zero(tmp_path, env, data):
    repo = _repo(tmp_path, data)
    r = _lenses(repo, env, "--pass", "qodo")
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_untrusted_true_prints_nothing_and_warns_on_stderr(tmp_path, env):
    repo = _repo(tmp_path, {"review_lenses": True})
    r = _lenses(repo, env, "--pass", "qodo")
    assert r.returncode == 0
    assert r.stdout == ""
    assert "not trusted" in r.stderr


@pytest.mark.parametrize("value", ['"true"', "1", '"yes"'])
def test_non_bool_prints_nothing_and_warns(tmp_path, env, value):
    repo = _repo(tmp_path, "review_lenses: %s\n" % value)
    _trust(repo)
    r = _lenses(repo, env, "--pass", "qodo")
    assert r.returncode == 0
    assert r.stdout == ""
    assert "not a bool" in r.stderr


def test_bad_or_missing_pass_exits_2(tmp_path, env):
    repo = _repo(tmp_path, {"review_lenses": True})
    _trust(repo)
    for args in ((), ("--pass",), ("--pass", "nope"), ("--pass", "L1")):
        r = _lenses(repo, env, *args)
        assert r.returncode == 2, args
        assert r.stdout == ""


def test_gate_yaml_is_read_from_cwd_only(tmp_path, env):
    repo = _repo(tmp_path, {"review_lenses": True})
    _trust(repo)
    sub = repo / "pkg"
    sub.mkdir()
    r = _lenses(sub, env, "--pass", "qodo")
    assert (r.returncode, r.stdout) == (0, "")


def test_malformed_gate_yaml_exits_nonzero_with_stderr_and_no_stdout(tmp_path, env):
    repo = _repo(tmp_path, "review_lenses: [unclosed\n")
    r = _lenses(repo, env, "--pass", "qodo")
    assert r.returncode != 0
    assert r.stdout == ""
    assert "gate.yaml" in r.stderr


def _store_state(cfg: Path) -> dict:
    return {
        str(p.relative_to(cfg)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(cfg.rglob("*"))
        if p.is_file()
    }


def test_read_only_trust_store_is_untouched(tmp_path, env):
    repo = _repo(tmp_path, {"review_lenses": True})
    _trust(repo)
    cfg = Path(env["XDG_CONFIG_HOME"])
    before = _store_state(cfg)
    assert before
    for d in [cfg, *[p for p in cfg.rglob("*") if p.is_dir()]]:
        d.chmod(stat.S_IRUSR | stat.S_IXUSR)
    for f in [p for p in cfg.rglob("*") if p.is_file()]:
        f.chmod(stat.S_IRUSR)
    try:
        r = _lenses(repo, env, "--pass", "expert")
        assert r.returncode == 0, r.stderr
        assert r.stdout == LENS_BLOCK + selection_line("expert")
    finally:
        for d in [cfg, *[p for p in cfg.rglob("*") if p.is_dir()]]:
            d.chmod(stat.S_IRWXU)
        for f in [p for p in cfg.rglob("*") if p.is_file()]:
            f.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert _store_state(cfg) == before


def test_lenses_never_calls_the_backend_trust_functions():
    import ast

    from code_forge import cli

    src = Path(cli.__file__).read_text(encoding="utf-8")
    fn = next(
        n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "_run_lenses"
    )
    names = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names.update(a.name.split(".")[-1] for a in n.names)
    called = names
    assert not called & {
        "_load_gate_backends",
        "is_trusted",
        "trust_status",
        "record_trust",
        "resolve_workspace",
    }
    assert "_load_trusted_review_lenses" in called


# -- SKILL.md ---------------------------------------------------------------


def _skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_skill_names_the_command_for_every_pass():
    text = _skill()
    for p in PASSES:
        assert "`code-forge lenses --pass %s`" % p in text, p
    assert "`code-forge lenses --pass <pass>`" in text


def test_skill_fallback_and_version_skew_sentences_appear_in_all_four_places():
    text = _skill()
    assert text.count(FALLBACK) == 4
    assert text.count(SKEW) == 4


def test_skill_tells_the_host_to_append_verbatim_and_nothing_when_empty():
    text = _skill()
    assert text.count("Append that text verbatim at the end of this pass's instructions") == 4
    assert text.count("if it is empty, append nothing") == 4


def test_skill_does_not_stop_on_a_lens_failure():
    # The outlet-resolution failure STOPs; lens failure must not.
    for line in _skill().splitlines():
        if "code-forge lenses" in line:
            assert "STOP" not in line.replace("(do not STOP)", "")


def test_cli_known_subcommand_and_subparser_registered():
    from code_forge.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["lenses", "--pass", "qodo"])
    assert args.subcommand == "lenses"
    assert args.lens_pass == "qodo"
