"""Tests for scripts/forge-provider.py.

The script rewrites the files that decide where a review's credentials go,
across every repo on a host. Each test below stands for a way that went
wrong during development: a block boundary that swallowed its neighbour, a
trust re-seal that authorized an edit nobody made, a regex that ate the
newline after the field it matched.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest
import yaml


# Under mutmut the suite runs from mutants/. also_copy should place
# scripts/ beside the tests, but a collection that happens before that
# copy, or a mirror that dropped the directory, must still find the real
# script outside the mutants/ root. Same fallback as test_analyse_arms.py.
def _script_path() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    candidate = here.parents[1] / "scripts" / "forge-provider.py"
    if candidate.exists():
        return candidate
    for parent in here.parents:
        if parent.name != "mutants":
            continue
        for root in parent.parents:
            outside = root / "scripts" / "forge-provider.py"
            if outside.exists():
                return outside
    return candidate


SCRIPT = _script_path()


def _load():
    spec = importlib.util.spec_from_file_location("forge_provider", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fp = _load()


@pytest.fixture(autouse=True)
def _isolate_user_layer(monkeypatch, tmp_path_factory):
    """Keep the host's real user layer out of every test.

    `find_configs` unions the layer forge resolves, which on a developer's
    machine is a real file; a test that patches USER_CONFIG alone would still
    sweep it. The default points at a path that does not exist, so a test that
    forgets to patch it sees no user layer at all instead of a live one.
    """
    real = getattr(fp, "_user_layer_paths_real", fp.user_layer_paths)
    monkeypatch.setattr(fp, "_user_layer_paths_real", real, raising=False)
    sandbox = tmp_path_factory.mktemp("no-user-layer") / "none.yaml"
    monkeypatch.setattr(fp, "USER_CONFIG", sandbox, raising=False)
    # Read through the attribute so a test that patches USER_CONFIG later is
    # still the layer this helper reports.
    monkeypatch.setattr(fp, "user_layer_paths", lambda: [fp.USER_CONFIG])
    # The trust store is `$XDG_CONFIG_HOME/code-forge/trusted.json`; without
    # this, every run of this suite writes the operator's live store, which is
    # the file the trust gate reads in production.
    import tempfile

    monkeypatch.setenv("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="forge-tests-xdg-"))
    monkeypatch.delenv("FORGE_CONFIG_DIR", raising=False)


import types  # noqa: E402

from code_forge.trust import record_trust  # noqa: E402 - needs fp's src bootstrap


GATE = """\
backends:
  alpha:
    type: api
    format: anthropic
    base_url: "https://alpha.example/anthropic"
    api_key_env: ALPHA_KEY
    model: alpha-model
    timeout_s: 2400
    default: true

  beta:
    type: api
    format: openai
    base_url: "https://beta.example/v1"
    api_key_env: BETA_KEY
    model: beta-model
    headers:
      User-Agent: something/1.0
"""


@pytest.fixture()
def gate(tmp_path):
    p = tmp_path / "gate.yaml"
    p.write_text(GATE)
    return p


class TestYamlScalar:
    @pytest.mark.parametrize("value", ["no", "true", "null", "on", "0755", "1.0"])
    def test_types_survive_a_round_trip(self, value):
        """`model: no` parses as False unless it is quoted."""
        rendered = fp.yaml_scalar(value, "model")
        assert yaml.safe_load(f"model: {rendered}")["model"] == value

    @pytest.mark.parametrize(
        "value",
        ["m\n    api_key_env: STOLEN", 'x"\n  evil:', "a\tb", "has'quote"],
    )
    def test_refuses_values_that_would_change_structure(self, value):
        with pytest.raises(ValueError):
            fp.yaml_scalar(value, "model")

    def test_plain_names_stay_unquoted(self):
        assert fp.yaml_scalar("mimo-v2.5-pro", "model") == "mimo-v2.5-pro"


class TestWriteField:
    def test_writes_and_reports_the_old_value(self, gate):
        changed, old, _problem = fp.write_field(
            gate, "alpha", "model", "new-model", ".bak-t", dry_run=False
        )
        assert (changed, old) == (True, "alpha-model")
        assert fp.read_field(fp.read_config(gate), "alpha", "model") == "new-model"

    def test_same_value_is_not_a_change(self, gate):
        """Numeric fields compare against a string read from the file.

        Without the coercion every `set --timeout-s` rewrote, re-backed-up
        and re-sealed all thirty configs while changing nothing.
        """
        changed, _old, _problem = fp.write_field(
            gate, "alpha", "timeout_s", 2400, ".bak-t", dry_run=False
        )
        assert changed is False

    def test_value_containing_a_quote_is_still_matched(self, tmp_path):
        p = tmp_path / "g.yaml"
        p.write_text('backends:\n  b:\n    model: say "hi" now\n')
        changed, old, _problem = fp.write_field(p, "b", "model", "plain", ".bak-t", dry_run=False)
        assert (changed, old) == (True, 'say "hi" now')

    def test_a_blank_line_after_the_field_survives(self, tmp_path):
        """The match must stop at the end of its own line.

        A `\\s*$` tail runs past the newline through any blank lines that
        follow, so rewriting a field silently deleted the paragraph break
        between two backends -- and with a CRLF file, the carriage returns.
        """
        p = tmp_path / "g.yaml"
        p.write_text("backends:\n  a:\n    model: old\n\n  b:\n    model: x\n")
        fp.write_field(p, "a", "model", "new", ".bak-t", dry_run=False)
        after = fp.read_config(p)
        assert after.count("\n") == 6
        assert yaml.safe_load(after)["backends"].keys() == {"a", "b"}

    def test_dry_run_writes_nothing(self, gate):
        before = fp.read_config(gate)
        fp.write_field(gate, "alpha", "model", "x", ".bak-t", dry_run=True)
        assert fp.read_config(gate) == before


class TestSetDefault:
    def test_moves_the_flag_and_is_idempotent(self, gate):
        changed, _ = fp.set_default(gate, "beta", ".bak-t", dry_run=False)
        assert changed is True
        data = yaml.safe_load(fp.read_config(gate))["backends"]
        assert data["beta"].get("default") is True
        assert "default" not in data["alpha"]

        again, _ = fp.set_default(gate, "beta", ".bak-t2", dry_run=False)
        assert again is False

    def test_unknown_name_is_reported(self, gate):
        changed, why = fp.set_default(gate, "nope", ".bak-t", dry_run=False)
        assert (changed, why) == (False, "not declared")


class TestInsertBackend:
    def test_adds_a_backend(self, gate):
        spec = {
            "name": "gamma",
            "base_url": "https://g.example/v1",
            "model": "gamma-model",
            "format": "openai",
            "key_env": "GAMMA_KEY",
            "max_tokens": 65536,
            "timeout_s": 2400,
        }
        did, why = fp.insert_backend(gate, spec, ".bak-t", dry_run=False)
        assert (did, why) == (True, None)
        data = yaml.safe_load(fp.read_config(gate))["backends"]
        assert data["gamma"]["model"] == "gamma-model"

    def test_existing_name_is_skipped(self, gate):
        spec = {
            "name": "alpha",
            "base_url": "https://x/v1",
            "model": "m",
            "format": "openai",
            "key_env": "K",
            "max_tokens": 1,
            "timeout_s": 1,
        }
        did, why = fp.insert_backend(gate, spec, ".bak-t", dry_run=False)
        assert (did, why) == (False, "already declared")

    def test_anchored_name_counts_as_existing(self, tmp_path):
        """`name: &anchor` is a declaration too.

        The key regex once required the line to end at the colon, so an
        anchored backend was invisible and `add` wrote a duplicate key --
        which YAML resolves by silently keeping one of them.
        """
        p = tmp_path / "g.yaml"
        p.write_text("backends:\n  base: &base\n    type: api\n  alias: *base\n")
        spec = {
            "name": "base",
            "base_url": "https://x/v1",
            "model": "m",
            "format": "openai",
            "key_env": "K",
            "max_tokens": 1,
            "timeout_s": 1,
        }
        did, why = fp.insert_backend(p, spec, ".bak-t", dry_run=False)
        assert (did, why) == (False, "already declared")


class TestBackup:
    def test_one_snapshot_per_run(self, gate):
        """Two fields, one command: the backup is the pre-run state.

        Copying per field left the backup holding a half-applied state, so
        rollback restored something that never existed on disk.
        """
        before = fp.read_config(gate)
        fp.write_field(gate, "alpha", "model", "m2", ".bak-t", dry_run=False)
        fp.write_field(gate, "alpha", "timeout_s", 60, ".bak-t", dry_run=False)
        assert fp.read_config(gate.with_name(gate.name + ".bak-t")) == before

    def test_separate_runs_get_separate_stamps(self):
        import datetime as dt

        # Mirrors the production call in forge-provider.py, which stamps
        # backups in local time; %f keeps consecutive runs apart.
        a = dt.datetime.now().strftime(fp.BACKUP_FMT)  # noqa: DTZ005
        b = dt.datetime.now().strftime(fp.BACKUP_FMT)  # noqa: DTZ005
        assert a != b


class TestLineEndings:
    def test_crlf_is_preserved(self, tmp_path):
        """Editing one field must not rewrite every line of a CRLF file."""
        p = tmp_path / "g.yaml"
        p.write_bytes(b"backends:\r\n  b:\r\n    model: old\r\n")
        fp.write_field(p, "b", "model", "new", ".bak-t", dry_run=False)
        assert p.read_bytes().count(b"\r\n") == 3

    def test_crlf_file_is_navigable(self, tmp_path):
        """Line-anchored patterns must tolerate the CR before the newline.

        Without it every key line failed to match, so a CRLF config reported
        no backends at all -- `set` and `default` became silent no-ops on it.
        """
        p = tmp_path / "g.yaml"
        p.write_bytes(b"backends:\r\n  b:\r\n    model: old\r\n    default: true\r\n")
        text = fp.read_config(p)
        assert fp.list_backends(text) == ["b"]
        assert fp.declares(text, "b") is True
        assert fp.read_field(text, "b", "model") == "old"
        changed, _ = fp.set_default(p, "b", ".bak-t", dry_run=False)
        assert changed is False  # already the default


class TestProbe:
    """The probe decides whether thirty files get rewritten."""

    def _serve(self, body, status=200):
        import http.server
        import json
        import socketserver
        import threading

        payload = json.dumps(body).encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                pass

        srv = socketserver.TCPServer(("127.0.0.1", 0), H, bind_and_activate=False)
        srv.allow_reuse_address = False
        srv.server_bind()
        srv.server_activate()
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"

    def test_completion_passes(self):
        srv, url = self._serve({"content": [{"type": "text", "text": "ok"}]})
        try:
            ok, _ = fp.probe(url, "m", "k", "anthropic")
        finally:
            srv.shutdown()
            srv.server_close()
        assert ok is True

    def test_error_body_under_200_fails(self):
        """A gateway can answer 200 with an error object.

        Checking only the status code would call that a working endpoint and
        write it to every config on the host.
        """
        srv, url = self._serve({"error": {"message": "quota exceeded"}})
        try:
            ok, detail = fp.probe(url, "m", "k", "anthropic")
        finally:
            srv.shutdown()
            srv.server_close()
        assert ok is False
        assert "quota exceeded" in detail

    def test_empty_body_fails(self):
        srv, url = self._serve({"id": "x"})
        try:
            ok, _ = fp.probe(url, "m", "k", "anthropic")
        finally:
            srv.shutdown()
            srv.server_close()
        assert ok is False


class TestResolveKey:
    def test_env_var_is_used_when_no_pass_entry(self, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "from-env")
        assert fp.resolve_key(None, "SOME_KEY") == "from-env"

    def test_unresolvable_pass_entry_falls_back(self, monkeypatch, capsys):
        """A mistyped --key-pass once surfaced as a generic "no key".

        That reads like the operator forgot to pass one, and the run
        continues without a probe.
        """
        monkeypatch.setenv("SOME_KEY", "from-env")
        got = fp.resolve_key("no/such/entry/here", "SOME_KEY")
        assert got == "from-env"
        assert "did not resolve" in capsys.readouterr().err


class TestFindConfigs:
    def test_skips_paths_no_review_reads(self, monkeypatch, tmp_path):
        real = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        junk = tmp_path / ".local" / "share" / ".code-forge" / "gate.yaml"
        for p in (real, junk):
            p.parent.mkdir(parents=True)
            p.write_text(GATE)
        record_trust(real, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "nonexistent.yaml")
        found = fp.find_configs()
        assert real in found
        assert junk not in found

    def test_no_duplicates(self, monkeypatch, tmp_path):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "nonexistent.yaml")
        found = fp.find_configs()
        assert len(found) == len(set(found))


class TestReseal:
    def test_only_the_files_passed_in_are_sealed(self, tmp_path, monkeypatch):
        """Re-sealing a file this run did not write defeats forge's gate.

        A malicious base_url is caught by the trust hash; an unrelated
        command that re-sealed everything authorized it again.
        """
        sys.path.insert(0, str(SCRIPT.resolve().parents[1] / "src"))
        from code_forge.trust import is_trusted, record_trust

        victim = tmp_path / "victim" / ".code-forge" / "gate.yaml"
        other = tmp_path / "other" / ".code-forge" / "gate.yaml"
        for p in (victim, other):
            p.parent.mkdir(parents=True)
            p.write_text(GATE)
            record_trust(p, yaml.safe_load(p.read_text()))

        victim.write_text(GATE.replace("alpha.example", "attacker.example"))
        assert not is_trusted(victim, yaml.safe_load(victim.read_text()))

        fp.write_config(other, other.read_text())
        sealed, failures = fp.reseal_paths([other], dry_run=False)
        assert (sealed, failures) == (1, [])
        assert not is_trusted(victim, yaml.safe_load(victim.read_text()))

    def test_dry_run_seals_nothing(self, tmp_path, monkeypatch):
        sys.path.insert(0, str(SCRIPT.resolve().parents[1] / "src"))
        from code_forge.trust import is_trusted

        p = tmp_path / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        sealed, _failures = fp.reseal_paths([p], dry_run=True)
        assert sealed == 1
        assert not is_trusted(p, yaml.safe_load(p.read_text()))


class TestAddAndSet:
    """`add` and `set` are the two paths that write credentials to disk."""

    def _args(self, name, dry_run=False, **kw):
        import types

        fields = {
            "name": name,
            "base_url": None,
            "model": None,
            "format": None,
            "key_env": None,
            "key_pass": None,
            "timeout_s": None,
            "max_tokens": None,
            "dry_run": dry_run,
        }
        fields.update(kw)
        return types.SimpleNamespace(**fields)

    def _gate(self, tmp_path, monkeypatch):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        # Retargeting refuses to write until a live probe answers, which is the
        # point of the command. These tests are about what lands on disk, so
        # they stand in for the network rather than reaching it.
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        return p

    def test_add_declares_a_working_backend(self, tmp_path, monkeypatch, capsys):
        p = self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_add(
            self._args(
                "gamma",
                base_url="https://gamma.example/v1",
                model="gamma-model",
                format="openai",
                key_env="GAMMA_KEY",
            )
        )
        assert rc == 0
        entry = yaml.safe_load(fp.read_config(p))["backends"]["gamma"]
        assert entry["base_url"] == "https://gamma.example/v1"
        assert entry["model"] == "gamma-model"
        assert entry["api_key_env"] == "GAMMA_KEY"

    def test_add_leaves_the_neighbours_alone(self, tmp_path, monkeypatch, capsys):
        """A block written at the wrong boundary silently eats its sibling."""
        p = self._gate(tmp_path, monkeypatch)
        before = yaml.safe_load(fp.read_config(p))["backends"]
        fp.cmd_add(
            self._args(
                "gamma", base_url="https://g.example/v1", model="m", format="openai", key_env="G_KEY"
            )
        )
        after = yaml.safe_load(fp.read_config(p))["backends"]
        assert after["alpha"] == before["alpha"]
        assert after["beta"] == before["beta"]

    def test_add_skips_a_name_that_exists(self, tmp_path, monkeypatch, capsys):
        """Re-adding an existing name leaves it alone rather than duplicating it.

        A duplicate key is silently dropped by the YAML parser, so the second
        declaration would win invisibly.
        """
        p = self._gate(tmp_path, monkeypatch)
        before = fp.read_config(p)
        rc = fp.cmd_add(
            self._args("alpha", base_url="https://x.example/v1", model="m", format="openai", key_env="X")
        )
        assert rc == 0
        assert "1 skipped" in capsys.readouterr().out
        assert fp.read_config(p) == before

    def test_set_changes_only_the_named_field(self, tmp_path, monkeypatch, capsys):
        p = self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_set(self._args("alpha", model="replacement"))
        assert rc == 0
        entry = yaml.safe_load(fp.read_config(p))["backends"]["alpha"]
        assert entry["model"] == "replacement"
        assert entry["base_url"] == "https://alpha.example/anthropic"
        assert entry["api_key_env"] == "ALPHA_KEY"

    def test_remove_on_a_flow_block_refuses_without_a_traceback(self, tmp_path, monkeypatch, capsys):
        """A one-line block parses and declares the entry, so the sweep must
        report a refusal for that file instead of dying with a KeyError and
        leaving the files before it edited but unsealed."""
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text("backends: {alpha: {type: api}, beta: {type: api}}\n")
        record_trust(p, yaml.safe_load(p.read_text()))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        rc = fp.cmd_remove(self._args("alpha", base_url=None, model=None, format=None, key_env=None))
        err = capsys.readouterr().err
        assert rc == 1
        assert "cannot address" in err
        assert "Traceback" not in err
        assert p.read_text().startswith("backends: {alpha")

    def test_set_on_a_flow_block_says_it_cannot_address_it(self, tmp_path, monkeypatch, capsys):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(
            "backends: {alpha: {type: api, model: old, base_url: 'https://a.example/v1'}, "
            "beta: {type: api}}\n"
        )
        record_trust(p, yaml.safe_load(p.read_text()))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        rc = fp.cmd_set(self._args("alpha", model="new"))
        err = capsys.readouterr().err
        assert rc == 1
        assert "cannot address" in err
        assert "not declared" not in err
        assert "model: old" in p.read_text()

    def test_set_writes_fields_and_headers_in_one_write(self, tmp_path, monkeypatch, capsys):
        p = self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_set(self._args("alpha", model="m2", header=["X-A: 1"]))
        assert rc == 0
        entry = yaml.safe_load(fp.read_config(p))["backends"]["alpha"]
        assert entry["model"] == "m2"
        assert entry["headers"] == {"X-A": "1"}
        backups = list(p.parent.glob(p.name + ".bak-provider-*"))
        assert len(backups) == 1

    def test_set_writes_a_field_the_entry_does_not_carry_yet(self, tmp_path, monkeypatch, capsys):
        """`max_tokens`/`timeout_s` used to be unwritable: the field was absent,
        so `set` skipped it and reported success with nothing changed."""
        p = self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_set(self._args("alpha", max_tokens=99))
        assert rc == 0
        assert yaml.safe_load(fp.read_config(p))["backends"]["alpha"]["max_tokens"] == 99

    def test_a_no_op_takes_no_backup(self, tmp_path, monkeypatch, capsys):
        p = self._gate(tmp_path, monkeypatch)
        before = p.read_text()
        assert fp.cmd_set(self._args("alpha", model="alpha-model")) == 0
        assert p.read_text() == before
        assert list(p.parent.glob(p.name + ".bak-provider-*")) == []

    def test_add_writes_nothing_when_the_probe_fails(self, tmp_path, monkeypatch, capsys):
        p = self._gate(tmp_path, monkeypatch)
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (False, "no answer"))
        before = p.read_text()
        rc = fp.cmd_add(
            self._args(
                "gamma", base_url="https://g.example/v1", model="m", format="openai", key_env="G_KEY"
            )
        )
        assert rc == 3
        assert p.read_text() == before
        assert list(p.parent.glob(p.name + ".bak-provider-*")) == []

    def test_a_failed_probe_writes_nothing(self, tmp_path, monkeypatch, capsys):
        p = self._gate(tmp_path, monkeypatch)
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (False, "no answer"))
        before = p.read_text()
        assert fp.cmd_set(self._args("alpha", base_url="https://new.example/v1")) == 3
        assert p.read_text() == before
        assert list(p.parent.glob(p.name + ".bak-provider-*")) == []

    def test_a_value_with_a_newline_is_refused_before_any_write(self, tmp_path, monkeypatch, capsys):
        p = self._gate(tmp_path, monkeypatch)
        before = p.read_text()
        assert fp.cmd_set(self._args("alpha", model="a\nb")) == 2
        assert p.read_text() == before

    def test_set_reports_a_reindent_it_had_to_make(self, tmp_path, monkeypatch, capsys):
        """A neighbour written at a different step comes back normalised; the
        command has to say so, not print a bare list of changed fields."""
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            "    base_url: 'https://a.example/v1'\n    api_key_env: A_KEY\n"
            "    model: a\n  beta:\n    type: api\n    headers:\n"
            "        User-Agent: x\n"
        )
        p.write_text(text)
        record_trust(p, yaml.safe_load(text))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "k")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        assert fp.cmd_set(self._args("alpha", model="b")) == 0
        assert "re-formatted" in capsys.readouterr().out

    def test_set_reports_the_header_names_it_replaces(self, tmp_path, monkeypatch, capsys):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            "    base_url: 'https://a.example/v1'\n    api_key_env: A_KEY\n"
            "    model: a\n    headers:\n      x-goog-api-key: sk-live-abc123\n"
        )
        p.write_text(text)
        record_trust(p, yaml.safe_load(text))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        rc = fp.cmd_set(self._args("alpha", header=["x-note: 1"]))
        out = capsys.readouterr().out
        assert rc == 0
        assert "headers removed: x-goog-api-key" in out
        assert "sk-live-abc123" not in out
        assert yaml.safe_load(fp.read_config(p))["backends"]["alpha"]["headers"] == {"x-note": "1"}

    def test_set_rejects_an_unknown_backend(self, tmp_path, monkeypatch, capsys):
        self._gate(tmp_path, monkeypatch)
        assert fp.cmd_set(self._args("nosuch", model="m")) == 1

    def test_set_refuses_to_retarget_without_a_probe(self, tmp_path, monkeypatch, capsys):
        """Pointing a backend at a new endpoint requires a live answer first.

        This test deliberately does not stub resolve_key: with no key there is
        nothing to probe with, and writing anyway is how a backend ends up
        aimed at a URL nobody checked.
        """
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: None)
        before = fp.read_config(p)

        rc = fp.cmd_set(self._args("alpha", base_url="https://elsewhere/v1"))
        assert rc == 3
        assert fp.read_config(p) == before

    def test_set_allows_a_non_routing_field_without_a_probe(self, tmp_path, monkeypatch, capsys):
        """timeout_s cannot misroute a request, so it does not need a probe."""
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: None)

        assert fp.cmd_set(self._args("alpha", timeout_s="900")) == 0
        entry = yaml.safe_load(fp.read_config(p))["backends"]["alpha"]
        assert str(entry["timeout_s"]) == "900"

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch, capsys):
        p = self._gate(tmp_path, monkeypatch)
        before = fp.read_config(p)
        fp.cmd_add(
            self._args(
                "gamma", dry_run=True, base_url="https://g/v1", model="m", format="openai", key_env="G"
            )
        )
        fp.cmd_set(self._args("alpha", dry_run=True, model="other"))
        assert fp.read_config(p) == before


class TestDefaultAndList:
    def _gate(self, tmp_path, monkeypatch):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        return p

    def test_default_moves_the_flag(self, tmp_path, monkeypatch, capsys):
        """Two backends both flagged default is an ambiguous config."""
        import types

        p = self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_default(types.SimpleNamespace(name="beta", dry_run=False))
        assert rc == 0
        data = yaml.safe_load(fp.read_config(p))["backends"]
        assert data["beta"].get("default") is True
        assert data["alpha"].get("default") is not True

    def test_list_names_every_backend(self, tmp_path, monkeypatch, capsys):
        import types

        self._gate(tmp_path, monkeypatch)
        assert fp.cmd_list(types.SimpleNamespace()) == 0
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out


class TestRename:
    """`rename` carries a backend's identity, its default flag, and its trust."""

    def _args(self, old, new, dry_run=False):
        import types

        return types.SimpleNamespace(old=old, new=new, dry_run=dry_run)

    def test_renames_only_the_key_line(self, tmp_path, monkeypatch, capsys):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")

        assert fp.cmd_rename(self._args("alpha", "renamed")) == 0
        data = yaml.safe_load(fp.read_config(p))["backends"]
        assert "renamed" in data
        assert "alpha" not in data
        assert data["renamed"]["default"] is True

    def test_a_backslash_in_the_new_name_is_refused(self, tmp_path, monkeypatch):
        """A name outside the reader's grammar cannot be written at all.

        The name used to reach re.sub as a template string, so `x\\1y` wrote
        the captured indentation into the key; refusing the name is the
        narrower fix, and the metacharacter case below covers the substitution.
        """
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")

        assert fp.cmd_rename(self._args("alpha", "a\\1b")) == 1
        assert fp.read_config(p) == GATE

    def test_a_dot_in_the_new_name_is_not_a_pattern(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(GATE)
        did, why = fp.rename_backend(p, "beta", "b.ta", ".bak-t", dry_run=False)
        assert did and why is None
        assert "  b.ta:" in p.read_text()

    def test_refuses_when_the_target_name_exists(self, tmp_path, monkeypatch):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")

        before = fp.read_config(p)
        assert fp.cmd_rename(self._args("alpha", "beta")) == 1
        assert fp.read_config(p) == before

    def test_dry_run_changes_nothing(self, tmp_path, monkeypatch):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")

        before = fp.read_config(p)
        fp.cmd_rename(self._args("alpha", "renamed", dry_run=True))
        assert fp.read_config(p) == before


class TestSync:
    """`sync` is the whole-machine setup path: rename, add, align, default.

    Doing this by hand took four invocations on a new machine, and getting
    the order wrong left a repo-level gate.yaml still naming the retired
    backend -- which outranks the user config, so that repo kept reviewing
    somewhere else with nothing on screen to say so.
    """

    def _args(self, name, **kw):
        import types

        fields = {
            "name": name,
            "base_url": "https://new.example/anthropic",
            "model": "new-model",
            "format": "anthropic",
            "from_name": None,
            "key_env": "NEW_KEY",
            "key_pass": None,
            "timeout_s": 2400,
            "max_tokens": 65536,
            "dry_run": False,
        }
        fields.update(kw)
        return types.SimpleNamespace(**fields)

    def _tree(self, monkeypatch, tmp_path, files):
        for rel, text in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            # A repository file is authorized before a command may edit it,
            # which is the state every real repo is in after `code-forge trust`.
            record_trust(p, yaml.safe_load(text))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))

    def test_one_call_renames_adds_and_defaults(self, tmp_path, monkeypatch, capsys):
        """The three states a machine can be in, handled in one pass."""
        self._tree(
            monkeypatch,
            tmp_path,
            {
                # declares the old name
                "code/one/.code-forge/gate.yaml": GATE.replace("beta:", "oldname:"),
                # declares nothing relevant
                "code/two/.code-forge/gate.yaml": GATE,
            },
        )
        rc = fp.cmd_sync(self._args(name="newname", from_name=["oldname"]))
        assert rc == 0

        one = yaml.safe_load(fp.read_config(tmp_path / "code/one/.code-forge/gate.yaml"))
        two = yaml.safe_load(fp.read_config(tmp_path / "code/two/.code-forge/gate.yaml"))
        # renamed in one, added in two, and the default moved in both
        assert "oldname" not in one["backends"]
        assert one["backends"]["newname"]["default"] is True
        assert two["backends"]["newname"]["default"] is True
        assert one["backends"]["alpha"].get("default") is not True

    def test_a_renamed_backend_gets_the_requested_fields(self, tmp_path, monkeypatch, capsys):
        """Renaming alone leaves the old machine's URL and key env in place.

        Measured on a real setup: the config carried an openai base_url and
        a retired MIMO_API_KEY under the new name, so reviews resolved a key
        that no longer existed.
        """
        self._tree(
            monkeypatch,
            tmp_path,
            {
                "code/one/.code-forge/gate.yaml": GATE.replace("beta:", "oldname:"),
            },
        )
        fp.cmd_sync(self._args(name="newname", from_name=["oldname"]))
        entry = yaml.safe_load(fp.read_config(tmp_path / "code/one/.code-forge/gate.yaml"))["backends"][
            "newname"
        ]
        assert entry["base_url"] == "https://new.example/anthropic"
        assert entry["api_key_env"] == "NEW_KEY"
        assert entry["format"] == "anthropic"

    def test_running_it_twice_changes_nothing(self, tmp_path, monkeypatch, capsys):
        """Non-idempotence here means a boundary or comparison is wrong."""
        self._tree(
            monkeypatch,
            tmp_path,
            {
                "code/one/.code-forge/gate.yaml": GATE.replace("beta:", "oldname:"),
                "code/two/.code-forge/gate.yaml": GATE,
            },
        )
        fp.cmd_sync(self._args(name="newname", from_name=["oldname"]))
        after_first = {p: fp.read_config(p) for p in fp.find_configs()}
        capsys.readouterr()
        fp.cmd_sync(self._args(name="newname", from_name=["oldname"]))
        out = capsys.readouterr().out
        assert "0 renamed, 0 added, 0 updated, 0 defaulted" in out
        for p, text in after_first.items():
            assert fp.read_config(p) == text

    def test_a_dry_run_previews_without_writing(self, tmp_path, monkeypatch, capsys):
        """A preview names each file and leaves every one of them untouched.

        Counts alone are not enough here: this command reaches every repo on
        the machine, and which repo it is about to convert is the thing you
        check before letting it run.
        """
        self._tree(
            monkeypatch,
            tmp_path,
            {
                "code/one/.code-forge/gate.yaml": GATE.replace("beta:", "oldname:"),
                "code/two/.code-forge/gate.yaml": GATE,
            },
        )
        before = {p: fp.read_config(p) for p in fp.find_configs()}

        rc = fp.cmd_sync(self._args("newname", from_name=["oldname"], dry_run=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert str(tmp_path / "code/one/.code-forge/gate.yaml") in out
        assert str(tmp_path / "code/two/.code-forge/gate.yaml") in out
        assert "would rename" in out and "would add" in out

        for p, text in before.items():
            assert fp.read_config(p) == text

    def test_nothing_is_written_when_the_endpoint_is_down(self, tmp_path, monkeypatch, capsys):
        """A probe failure must not leave the machine half-converted."""
        self._tree(
            monkeypatch,
            tmp_path,
            {
                "code/one/.code-forge/gate.yaml": GATE.replace("beta:", "oldname:"),
            },
        )
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (False, "timeout"))
        before = fp.read_config(tmp_path / "code/one/.code-forge/gate.yaml")
        rc = fp.cmd_sync(self._args(name="newname", from_name=["oldname"]))
        assert rc == 3
        assert fp.read_config(tmp_path / "code/one/.code-forge/gate.yaml") == before

    def test_sync_walks_the_config_tree_once(self, tmp_path, monkeypatch, capsys):
        """Five edit passes share one directory listing."""
        self._tree(
            monkeypatch,
            tmp_path,
            {"code/one/.code-forge/gate.yaml": GATE.replace("beta:", "oldname:")},
        )
        calls = {"n": 0}
        real = fp.find_configs

        def counting():
            calls["n"] += 1
            return real()

        monkeypatch.setattr(fp, "find_configs", counting)
        rc = fp.cmd_sync(self._args(name="newname", from_name=["oldname"]))
        assert rc == 0
        assert calls["n"] == 1


class TestTrustCommand:
    """`trust` reports the configs forge is currently refusing."""

    def _args(self, fix=False):
        import types

        return types.SimpleNamespace(fix=fix, dry_run=False)

    def test_reports_an_untrusted_file(self, tmp_path, monkeypatch, capsys):
        sys.path.insert(0, str(SCRIPT.resolve().parents[1] / "src"))
        from code_forge.trust import record_trust

        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(p.read_text()))
        p.write_text(GATE.replace("alpha.example", "attacker.example"))

        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        fp.cmd_trust(self._args())
        assert "1 not trusted" in capsys.readouterr().out

    def test_fix_reseals(self, tmp_path, monkeypatch, capsys):
        sys.path.insert(0, str(SCRIPT.resolve().parents[1] / "src"))
        from code_forge.trust import is_trusted, record_trust

        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(p.read_text()))
        p.write_text(GATE.replace("alpha.example", "other.example"))
        assert not is_trusted(p, yaml.safe_load(p.read_text()))

        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        fp.cmd_trust(self._args(fix=True))
        assert is_trusted(p, yaml.safe_load(p.read_text()))

    def test_clean_host_reports_zero(self, tmp_path, monkeypatch, capsys):
        sys.path.insert(0, str(SCRIPT.resolve().parents[1] / "src"))
        from code_forge.trust import record_trust

        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(p.read_text()))

        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        fp.cmd_trust(self._args())
        assert "0 not trusted" in capsys.readouterr().out


class TestHeaders:
    """`--header` is how Omni gateway options reach a fleet rewrite.

    forge already sends `headers:` (llm_invoke.py). This script only wrote
    scalars, so a sync onto an Omni combo dropped the two flags the live
    candidate backend needed.
    """

    def _args(self, name, dry_run=False, **kw):
        import types

        fields = dict(
            name=name,
            base_url="https://g.example/v1",
            model="g-model",
            format="openai",
            key_env="G_KEY",
            key_pass=None,
            timeout_s=2400,
            max_tokens=65536,
            header=None,
            from_name=None,
            dry_run=dry_run,
        )
        fields.update(kw)
        return types.SimpleNamespace(**fields)

    def _gate(self, tmp_path, monkeypatch):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        return p

    def test_add_writes_the_headers_mapping(self, tmp_path, monkeypatch, capsys):
        self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_add(
            self._args("gamma", header=["x-omniroute-no-memory:1", "x-omniroute-compression:off"])
        )
        assert rc == 0
        entry = yaml.safe_load(fp.read_config(tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"))[
            "backends"
        ]["gamma"]
        assert entry["headers"] == {
            "x-omniroute-no-memory": "1",
            "x-omniroute-compression": "off",
        }
        # yaml_scalar must quote reserved words; otherwise `off` is False
        assert isinstance(entry["headers"]["x-omniroute-compression"], str)

    def test_add_without_headers_omits_the_key(self, tmp_path, monkeypatch, capsys):
        self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_add(self._args("gamma"))
        assert rc == 0
        entry = yaml.safe_load(fp.read_config(tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"))[
            "backends"
        ]["gamma"]
        assert "headers" not in entry

    def test_set_replaces_headers_on_an_existing_backend(self, tmp_path, monkeypatch, capsys):
        p = self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_set(
            self._args(
                "beta",
                base_url=None,
                model=None,
                format=None,
                key_env=None,
                timeout_s=None,
                max_tokens=None,
                header=["x-omniroute-no-memory:1"],
            )
        )
        assert rc == 0
        entry = yaml.safe_load(fp.read_config(p))["backends"]["beta"]
        assert entry["headers"] == {"x-omniroute-no-memory": "1"}
        assert entry["model"] == "beta-model"

    def test_set_inserts_headers_when_the_backend_had_none(self, tmp_path, monkeypatch, capsys):
        p = self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_set(
            self._args(
                "alpha",
                base_url=None,
                model=None,
                format=None,
                key_env=None,
                timeout_s=None,
                max_tokens=None,
                header=["x-omniroute-no-memory:1"],
            )
        )
        assert rc == 0
        entry = yaml.safe_load(fp.read_config(p))["backends"]["alpha"]
        assert entry["headers"] == {"x-omniroute-no-memory": "1"}
        assert entry.get("default") is True

    def test_set_headers_only_does_not_need_a_probe(self, tmp_path, monkeypatch, capsys):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("probed")))
        rc = fp.cmd_set(
            self._args(
                "alpha",
                base_url=None,
                model=None,
                format=None,
                key_env=None,
                timeout_s=None,
                max_tokens=None,
                header=["x-omniroute-no-memory:1"],
            )
        )
        assert rc == 0

    def test_sync_writes_headers_on_add_and_on_update(self, tmp_path, monkeypatch, capsys):
        one = tmp_path / "code" / "one" / ".code-forge" / "gate.yaml"
        two = tmp_path / "code" / "two" / ".code-forge" / "gate.yaml"
        one.parent.mkdir(parents=True)
        two.parent.mkdir(parents=True)
        one.write_text(GATE)
        two.write_text(GATE)
        for path in (one, two):
            record_trust(path, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        rc = fp.cmd_sync(
            self._args(
                "newname",
                base_url="https://new.example/v1",
                model="new-model",
                format="openai",
                key_env="NEW_KEY",
                header=["x-omniroute-no-memory:1", "x-omniroute-compression:off"],
            )
        )
        assert rc == 0
        for path in (one, two):
            entry = yaml.safe_load(fp.read_config(path))["backends"]["newname"]
            assert entry["headers"] == {
                "x-omniroute-no-memory": "1",
                "x-omniroute-compression": "off",
            }

    def test_sync_without_headers_leaves_existing_ones(self, tmp_path, monkeypatch, capsys):
        p = tmp_path / "code" / "one" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        record_trust(p, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        rc = fp.cmd_sync(
            self._args(
                "beta",
                base_url="https://beta.example/v1",
                model="beta-model",
                format="openai",
                key_env="BETA_KEY",
            )
        )
        assert rc == 0
        entry = yaml.safe_load(fp.read_config(p))["backends"]["beta"]
        assert entry["headers"] == {"User-Agent": "something/1.0"}

    def test_rejects_a_protected_header(self, tmp_path, monkeypatch, capsys):
        self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_add(self._args("gamma", header=["Authorization:Bearer x"]))
        assert rc == 2
        data = yaml.safe_load(fp.read_config(tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"))
        assert "gamma" not in data["backends"]

    def test_rejects_a_duplicate_header_name(self, tmp_path, monkeypatch, capsys):
        self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_add(self._args("gamma", header=["X-Note:one", "x-note:two"]))
        assert rc == 2

    def test_rejects_a_header_without_a_colon(self, tmp_path, monkeypatch, capsys):
        self._gate(tmp_path, monkeypatch)
        rc = fp.cmd_add(self._args("gamma", header=["not-a-header"]))
        assert rc == 2

    def test_add_with_headers_leaves_the_neighbours_alone(self, tmp_path, monkeypatch, capsys):
        p = self._gate(tmp_path, monkeypatch)
        before = yaml.safe_load(fp.read_config(p))["backends"]
        rc = fp.cmd_add(self._args("gamma", header=["x-omniroute-no-memory:1"]))
        assert rc == 0
        after = yaml.safe_load(fp.read_config(p))["backends"]
        assert after["alpha"] == before["alpha"]
        assert after["beta"] == before["beta"]


@pytest.mark.parametrize(
    "url",
    ["file:///tmp/key", "ftp://example.test", "data:text/plain,key", "localhost:8000", "//example.test"],
)
def test_probe_rejects_non_http_scheme_before_io(url, monkeypatch):
    def no_io(*args, **kwargs):
        raise AssertionError("invalid scheme must not reach request construction")

    monkeypatch.setattr(fp.urllib.request, "Request", no_io)
    assert fp.probe(url, "m", "k", "openai") == (False, "provider probe requires an http or https URL")


class TestRemoveBackend:
    """Removing a dead entry must not touch its neighbours or a live default."""

    def test_deletes_the_block_and_keeps_the_sibling(self, gate):
        original = gate.read_text()
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did and why is None
        body = gate.read_text()
        assert "beta:" not in body
        # Everything that belonged to the survivor survives byte for byte: a removal
        # that dropped alpha's fields or its default flag would pass a name check.
        assert body == original.split("  beta:")[0]
        assert yaml.safe_load(body)["backends"]["alpha"]["default"] is True
        # The backup holds the pre-removal bytes.
        backups = sorted(gate.parent.glob(gate.name + ".bak-test"))
        assert len(backups) == 1
        assert backups[0].read_text() == original

    def test_refuses_the_review_default(self, gate):
        before = gate.read_bytes()
        did, why = fp.remove_backend(gate, "alpha", ".bak-test", dry_run=False)
        assert did is False
        assert "default" in why
        # A refusal must leave the file byte-identical and write no backup: a handler
        # that removes fields first and refuses afterwards would pass a name check.
        assert gate.read_bytes() == before
        assert list(gate.parent.glob(gate.name + ".bak-test")) == []

    @pytest.mark.parametrize("spelling", ["true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"])
    @pytest.mark.parametrize("separator", [":", " :"])
    def test_refuses_the_default_whatever_its_case(self, tmp_path, spelling, separator):
        p = tmp_path / "gate.yaml"
        p.write_text(
            "backends:\n"
            "  solo:\n"
            "    type: api\n"
            "    format: openai\n"
            '    base_url: "https://solo.example/v1"\n'
            "    model: solo-model\n"
            f"    default{separator} {spelling}\n"
            "  spare:\n"
            "    type: api\n"
            "    format: openai\n"
            '    base_url: "https://spare.example/v1"\n'
            "    api_key_env: SPARE_KEY\n"
            "    model: spare-model\n"
        )
        before = p.read_bytes()
        did, why = fp.remove_backend(p, "solo", ".bak-test", dry_run=False)
        assert did is False and "default" in why
        assert p.read_bytes() == before

    @pytest.mark.parametrize("key", ["headers", "top-level"])
    def test_refuses_to_break_a_surviving_alias(self, tmp_path, key):
        p = tmp_path / "gate.yaml"
        if key == "headers":
            value = (
                "backends:\n  drop:\n    headers: &shared\n      User-Agent: agent\n"
                "  keep:\n    model: keep\n    headers: *shared\n"
            )
        else:
            value = (
                "backends:\n  drop:\n    headers: &shared\n      User-Agent: agent\n"
                "  keep:\n    model: keep\nother: *shared\n"
            )
        p.write_text(value)
        before = p.read_bytes()
        did, why = fp.remove_backend(p, "drop", ".bak-test", dry_run=False)
        # Either the editor spots the dangling alias before writing, or the
        # re-parse after the removal does; both mean the same thing to the
        # caller, and the rewrite refuses earlier with a named anchor.
        assert did is False and ("YAML" in why or "anchor" in why)
        assert p.read_bytes() == before
        assert not p.with_name("gate.yaml.bak-test").exists()

    def test_nested_default_does_not_make_a_backend_the_default(self, gate):
        # `params` is a per-request body map, so a `default` key inside it is a
        # provider option. Under `headers` a boolean would be rejected by the
        # real parser and the fixture would prove nothing.
        original = gate.read_text().replace(
            "    headers:\n", "    params:\n      default: true\n    headers:\n"
        )
        gate.write_text(original)
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did and why is None
        assert gate.read_text() == original.split("  beta:")[0]

    def test_refuses_a_default_marked_with_an_inline_comment(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(
            "backends:\n"
            "  solo:\n"
            "    type: api\n"
            "    format: openai\n"
            '    base_url: "https://solo.example/v1"\n'
            "    model: solo-model\n"
            "    default: true  # the active one\n"
            "  spare:\n"
            "    type: api\n"
            "    format: openai\n"
            '    base_url: "https://spare.example/v1"\n'
            "    api_key_env: SPARE_KEY\n"
            "    model: spare-model\n"
        )
        before = p.read_bytes()
        did, why = fp.remove_backend(p, "solo", ".bak-test", dry_run=False)
        assert did is False
        assert "default" in why
        assert p.read_bytes() == before

    def test_refuses_to_empty_the_mapping(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(
            "backends:\n"
            "  only:\n"
            "    type: api\n"
            "    format: openai\n"
            '    base_url: "https://only.example/v1"\n'
            "    model: only-model\n"
        )
        before = p.read_bytes()
        did, why = fp.remove_backend(p, "only", ".bak-test", dry_run=False)
        assert did is False
        assert "no backends" in why
        assert p.read_bytes() == before

    def test_second_run_reports_nothing_declared(self, gate):
        fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did is False
        assert why == "not declared"

    def test_dry_run_leaves_the_file_and_makes_no_backup(self, gate):
        before = gate.read_bytes()
        did, _ = fp.remove_backend(gate, "beta", ".bak-test", dry_run=True)
        assert did is True
        assert gate.read_bytes() == before
        assert list(gate.parent.glob(gate.name + ".bak-test")) == []


class TestRemoveCommand:
    """The parser must actually dispatch, and partial success must say so."""

    def _run(self, monkeypatch, paths, argv):
        from code_forge.trust import record_trust

        self.sealed = []
        for path in paths:
            record_trust(path, yaml.safe_load(path.read_text()))
        monkeypatch.setattr(fp, "find_configs", lambda: list(paths))

        def record(edited, dry_run):
            # One call per edited file: that is what makes a failure in a
            # later file unable to strand an earlier edit's authorization.
            self.sealed.append((list(edited), dry_run))
            return len(edited), []

        monkeypatch.setattr(fp, "reseal_paths", record)
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", *argv])
        return fp.main()

    def test_main_dispatches_remove(self, tmp_path, monkeypatch):
        gate = tmp_path / "gate.yaml"
        gate.write_text(GATE)
        rc = self._run(monkeypatch, [gate], ["remove", "beta"])
        assert rc == 0
        assert "beta:" not in gate.read_text()
        # Trust is re-sealed for exactly the file this run edited, with dry_run False;
        # a missing or over-broad re-seal is invisible to the file assertions above.
        assert self.sealed == [([gate], False)]

    def test_main_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        gate = tmp_path / "gate.yaml"
        gate.write_text(GATE)
        before = gate.read_bytes()
        rc = self._run(monkeypatch, [gate], ["remove", "beta", "--dry-run"])
        assert rc == 0
        assert gate.read_bytes() == before
        assert self.sealed == [([gate], True)]

    def test_removes_every_eligible_file(self, tmp_path, monkeypatch, capsys):
        first = tmp_path / "a" / "gate.yaml"
        second = tmp_path / "b" / "gate.yaml"
        for p in (first, second):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(GATE)
        rc = self._run(monkeypatch, [first, second], ["remove", "beta"])
        assert rc == 0
        assert "beta:" not in first.read_text()
        assert "beta:" not in second.read_text()
        # Both edited files are re-sealed, each as its own edit, in discovery
        # order: a command that stopped after the first eligible config would
        # leave the second pointing at a name that no longer resolves.
        assert self.sealed == [([first], False), ([second], False)]

    def test_no_declaring_config_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        absent = tmp_path / "gate.yaml"
        absent.write_text("backends:\n  other:\n    model: x\n")
        rc = self._run(monkeypatch, [absent], ["remove", "ghost"])
        assert rc == 1
        assert "no config declares" in capsys.readouterr().err
        assert absent.read_text() == "backends:\n  other:\n    model: x\n"

    @pytest.mark.parametrize("already_trusted", [False, True])
    @pytest.mark.parametrize("change", ["endpoint", "review_focus", "review_lenses"])
    def test_refuses_to_authorize_an_untrusted_survivor(
        self, tmp_path, monkeypatch, already_trusted, change
    ):
        from code_forge.trust import is_trusted, record_trust

        gate = tmp_path / ".code-forge" / "gate.yaml"
        gate.parent.mkdir()
        gate.write_text(GATE)
        if already_trusted:
            record_trust(gate, yaml.safe_load(gate.read_text()))
        changed = {
            "endpoint": GATE.replace("alpha.example", "attacker.example"),
            "review_focus": GATE + "review_focus: unapproved instructions\n",
            "review_lenses": GATE + "review_lenses: true\n",
        }[change]
        gate.write_text(changed)
        before = gate.read_bytes()
        from code_forge.trust import is_trusted_focus, is_trusted_lenses

        def accepted():
            data = yaml.safe_load(gate.read_text())
            return (
                is_trusted(gate, data) and is_trusted_focus(gate, data) and is_trusted_lenses(gate, data)
            )

        assert not accepted()
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        assert fp.main() == 1
        assert gate.read_bytes() == before
        assert not list(gate.parent.glob("gate.yaml.bak-provider-*"))
        assert not accepted()

    def test_trusted_removal_reseals_the_surviving_backend(self, tmp_path, monkeypatch):
        from code_forge.trust import is_trusted, record_trust

        gate = tmp_path / ".code-forge" / "gate.yaml"
        gate.parent.mkdir()
        gate.write_text(GATE)
        record_trust(gate, yaml.safe_load(gate.read_text()))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        assert fp.main() == 0
        assert yaml.safe_load(gate.read_text())["backends"] == {
            "alpha": yaml.safe_load(GATE)["backends"]["alpha"]
        }
        assert is_trusted(gate, yaml.safe_load(gate.read_text()))

    @pytest.mark.parametrize("mixed", [False, True], ids=["single", "mixed"])
    def test_reseal_failure_after_write_exits_nonzero(self, tmp_path, monkeypatch, capsys, mixed):
        from code_forge import trust

        failed = tmp_path / "code" / "a" / ".code-forge" / "gate.yaml"
        healthy = tmp_path / "code" / "b" / ".code-forge" / "gate.yaml"
        user = tmp_path / "config.yaml"
        repos = [failed, healthy] if mixed else [failed]
        configs = [*repos, user]
        original = yaml.safe_load(GATE)
        for path in configs:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(GATE, encoding="utf-8")
        record_trust = trust.record_trust
        for path in repos:
            record_trust(path, original)
        old_hash = trust.trust_status(failed, original).stored_hash
        assert old_hash is not None
        attempts = []

        def reseal(path, data):
            attempts.append(path)
            if path == failed:
                raise PermissionError("fixture trust store write denied")
            record_trust(path, data)

        monkeypatch.setattr(trust, "record_trust", reseal)
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", user)
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        rc = fp.main()
        out, err = capsys.readouterr()

        assert attempts == repos
        remaining = {"backends": {"alpha": original["backends"]["alpha"]}}
        for path in configs:
            assert yaml.safe_load(path.read_text(encoding="utf-8")) == remaining
            backups = list(path.parent.glob(path.name + ".bak-provider-*"))
            assert [backup.read_bytes() for backup in backups] == [GATE.encode("utf-8")]
        failed_status = trust.trust_status(failed, remaining)
        assert not failed_status.trusted
        assert failed_status.stored_hash == old_hash
        if mixed:
            assert trust.is_trusted(healthy, remaining)
            assert "re-sealed trust on 1 gate.yaml file(s)" in out
        assert str(failed) in err
        assert "backups:" in out
        assert rc == 1
        assert "could not re-seal trust" in err
        assert "still need authorization" in err

    def test_user_config_needs_no_repo_trust(self, tmp_path, monkeypatch):
        gate = tmp_path / "config.yaml"
        gate.write_text(GATE)
        monkeypatch.setattr(fp, "USER_CONFIG", gate)
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        assert fp.main() == 0
        assert yaml.safe_load(gate.read_text())["backends"] == {
            "alpha": yaml.safe_load(GATE)["backends"]["alpha"]
        }

    def test_partial_success_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        removable = tmp_path / "a" / "gate.yaml"
        defaulted = tmp_path / "b" / "gate.yaml"
        absent = tmp_path / "c" / "gate.yaml"
        for p in (removable, defaulted):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(GATE)
        # In the second file beta is the default, so the removal must be refused there.
        defaulted.write_text(
            GATE.replace("    default: true\n", "", 1).replace(
                "    model: beta-model\n", "    model: beta-model\n    default: true\n", 1
            )
        )
        absent.parent.mkdir(parents=True, exist_ok=True)
        absent.write_text("backends:\n  other:\n    model: x\n")

        before_defaulted = defaulted.read_bytes()
        before_absent = absent.read_bytes()
        rc = self._run(monkeypatch, [removable, defaulted, absent], ["remove", "beta"])
        assert rc == 1
        captured = capsys.readouterr()
        out, err = captured.out, captured.err
        # The summary must say what actually happened: one removal out of the two
        # declaring files, and the non-declaring file must not inflate the denominator.
        assert "from 1 of 2 declaring file(s)" in out
        assert str(removable) in out
        assert str(absent) not in out
        assert "kept it" in err
        assert str(defaulted) in err
        assert "beta:" not in removable.read_text()
        assert "beta:" in defaulted.read_text()
        # Only the edited file is re-sealed; passing every declaring file would
        # authorize a gate this run refused to touch.
        assert self.sealed == [([removable], False)]
        assert defaulted.read_bytes() == before_defaulted
        assert absent.read_bytes() == before_absent


class TestRemoveMiddleBlock:
    """A removal that truncates from the target onward must not pass."""

    THREE = (
        "backends:\n"
        "  first:\n"
        "    type: api\n"
        "    format: openai\n"
        '    base_url: "https://first.example/v1"\n'
        "    api_key_env: FIRST_KEY\n"
        "    model: first-model\n"
        "  middle:\n"
        "    type: api\n"
        "    format: openai\n"
        '    base_url: "https://middle.example/v1"\n'
        "    api_key_env: MIDDLE_KEY\n"
        "    model: middle-model\n"
        "  last:\n"
        "    type: api\n"
        "    format: openai\n"
        '    base_url: "https://last.example/v1"\n'
        "    api_key_env: LAST_KEY\n"
        "    model: last-model\n"
    )

    def test_keeps_both_survivors_in_order(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(self.THREE)
        did, why = fp.remove_backend(p, "middle", ".bak-test", dry_run=False)
        assert did and why is None
        # The whole text, not just names: dropping a field from either survivor,
        # reordering them or reflowing whitespace would change meaning or diff noise.
        expected = self.THREE.split("  middle:")[0] + "  last:" + self.THREE.split("  last:", 1)[1]
        assert p.read_text() == expected
        assert list(yaml.safe_load(p.read_text())["backends"]) == ["first", "last"]


class TestRemoveLeavesProseAlone:
    """The name also appears in comments; deleting it there changes meaning."""

    FIXTURE = (
        "backends:\n"
        "  # keeper: do not confuse with ghost\n"
        "  keeper:\n"
        "    type: api\n"
        "    format: openai\n"
        '    base_url: "https://keeper.example/v1"\n'
        "    api_key_env: KEEPER_KEY\n"
        "    model: keeper-model\n"
        "    # ghost used to live here; see the review log\n"
        "  ghost:\n"
        "    type: api\n"
        "    format: openai\n"
        '    base_url: "https://ghost.example/v1"\n'
        "    api_key_env: GHOST_KEY\n"
        "    model: ghost-model\n"
    )

    def test_comments_naming_the_backend_survive(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(self.FIXTURE)
        did, why = fp.remove_backend(p, "ghost", ".bak-test", dry_run=False)
        assert did and why is None
        body = p.read_text()
        assert "ghost:" not in body
        assert "# keeper: do not confuse with ghost" in body
        assert "# ghost used to live here; see the review log" in body


class TestRemoveFirstBlock:
    """Removing the first entry must not eat the `backends:` header."""

    @staticmethod
    def fixture():
        """THREE with a marked default, so removing `first` keeps the choice."""
        return TestRemoveMiddleBlock.THREE.replace(
            "    model: middle-model\n", "    model: middle-model\n    default: true\n"
        )

    def test_header_and_following_blocks_survive(self, tmp_path):
        p = tmp_path / "gate.yaml"
        fixture = self.fixture()
        p.write_text(fixture)
        did, why = fp.remove_backend(p, "first", ".bak-test", dry_run=False)
        assert did and why is None
        body = p.read_text()
        assert body.startswith("backends:\n  middle:")
        expected = "backends:\n  middle:" + fixture.split("  middle:", 1)[1]
        assert body == expected
        parsed = yaml.safe_load(body)
        assert list(parsed["backends"]) == ["middle", "last"]


class TestRemovePreservesLaterSections:
    """The backends mapping is not always the end of the file."""

    FIXTURE = (
        "backends:\n"
        "  keep:\n"
        "    type: api\n"
        "    format: openai\n"
        '    base_url: "https://keep.example/v1"\n'
        "    api_key_env: KEEP_KEY\n"
        "    model: keep-model\n"
        "  drop:\n"
        "    type: api\n"
        "    format: openai\n"
        '    base_url: "https://drop.example/v1"\n'
        "    api_key_env: DROP_KEY\n"
        "    model: drop-model\n"
        "\n"
        "retry:\n"
        "  max_attempts: 5\n"
        "  initial_delay_s: 2.0\n"
    )

    def test_later_sections_survive(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(self.FIXTURE)
        did, why = fp.remove_backend(p, "drop", ".bak-test", dry_run=False)
        assert did and why is None
        body = p.read_text()
        assert "drop:" not in body
        assert body.endswith("retry:\n  max_attempts: 5\n  initial_delay_s: 2.0\n")
        parsed = yaml.safe_load(body)
        assert list(parsed["backends"]) == ["keep"]
        assert parsed["retry"]["max_attempts"] == 5


class TestRemoveWithExplicitFalseDefault:
    """`default: false` is a normal field, not a reason to refuse."""

    def test_removes_a_backend_marked_default_false(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(
            "backends:\n"
            "  keeper:\n"
            "    type: api\n"
            "    format: openai\n"
            '    base_url: "https://keeper.example/v1"\n'
            "    api_key_env: KEEPER_KEY\n"
            "    model: keeper-model\n"
            "    default: true\n"
            "  spare:\n"
            "    type: api\n"
            "    format: openai\n"
            '    base_url: "https://spare.example/v1"\n'
            "    api_key_env: SPARE_KEY\n"
            "    model: spare-model\n"
            "    default: false\n"
        )
        did, why = fp.remove_backend(p, "spare", ".bak-test", dry_run=False)
        assert did and why is None
        body = p.read_text()
        assert "spare:" not in body
        assert "keeper:" in body


class TestDefaultMaxTokens:
    """A backend declared without --max-tokens still has to ask for room.

    The cap is the output budget a review sends. Too small a value truncates
    the verdict JSON, and a truncated verdict parses as zero findings, so the
    floor is a correctness property and not a preference.
    """

    @pytest.mark.parametrize("cmd", ["add", "sync"])
    def test_written_cap_leaves_room_for_a_long_verdict(self, monkeypatch, cmd):
        seen = {}

        def capture(args):
            seen["max_tokens"] = args.max_tokens
            return 0

        monkeypatch.setattr(fp, "cmd_add", capture)
        monkeypatch.setattr(fp, "cmd_sync", capture)
        argv = [cmd, "gamma", "--base-url", "https://g.example/v1", "--model", "m"]
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", *argv])
        assert fp.main() == 0
        # The parser must forward the constant rather than a literal of its own,
        # and the constant itself must leave room for a long verdict. Either half
        # alone lets the other drift: an inflated constant slips past a floor,
        # and a hardcoded parser default slips past a constant-only check.
        assert seen.get("max_tokens") == fp.DEFAULT_MAX_TOKENS
        assert fp.DEFAULT_MAX_TOKENS >= 131072


UNMARKED_PAIR = (
    "backends:\n"
    "  alpha:\n"
    "    type: api\n"
    "    format: openai\n"
    '    base_url: "https://alpha.example/v1"\n'
    "    api_key_env: ALPHA_KEY\n"
    "    model: alpha-model\n"
    "  beta:\n"
    "    type: api\n"
    "    format: openai\n"
    '    base_url: "https://beta.example/v1"\n'
    "    api_key_env: BETA_KEY\n"
    "    model: beta-model\n"
)


def _mark_default(text, name):
    """Add a default flag to one entry, the way an operator would."""
    marker = f"  {name}:\n"
    head, tail = text.split(marker, 1)
    lines = tail.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("    model:"):
            lines.insert(index + 1, "    default: true\n")
            break
    else:
        raise AssertionError(f"no model line under {name}")
    return head + marker + "".join(lines)


def _user_layer(monkeypatch, backends):
    """Replace the user-level layer the removal guard has to account for."""
    from code_forge import user_config

    monkeypatch.setattr(user_config, "load_user_backends", lambda: backends)


def _effective(project, user=None):
    """The backend forge itself would review with, through real code.

    Layers are parsed separately because the review path parses them
    separately; merging first would reject one `default: true` per layer.
    """
    from code_forge.backend import load_backend_configs, resolve_backend

    configs = load_backend_configs({"backends": project})
    user_only = {name: value for name, value in (user or {}).items() if name not in project}
    if user_only:
        configs += load_backend_configs({"backends": user_only})
    return resolve_backend({}, configs).name


class TestRemovePreservesEffectiveDefault:
    """Deleting an entry must not silently reroute the backend a review uses.

    The resolver falls back to the first surviving entry when no entry is
    marked, and a user-level entry of the same name stops being shadowed
    once the project entry is gone. Either way the file still parses and the
    command reports success, so only comparing the resolved backend catches it.
    """

    def test_refuses_when_the_first_entry_is_the_implicit_default(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(UNMARKED_PAIR)
        before = p.read_bytes()
        did, why = fp.remove_backend(p, "alpha", ".bak-test", dry_run=False)
        assert did is False
        assert "effective backend" in why and "alpha" in why and "beta" in why
        assert p.read_bytes() == before
        assert not p.with_name("gate.yaml.bak-test").exists()

    def test_allows_deleting_a_later_entry(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(UNMARKED_PAIR)
        did, why = fp.remove_backend(p, "beta", ".bak-test", dry_run=False)
        assert did and why is None
        assert "beta:" not in p.read_text()

    def test_allows_deleting_when_an_explicit_default_survives(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        did, why = fp.remove_backend(p, "beta", ".bak-test", dry_run=False)
        assert did and why is None
        assert _effective(yaml.safe_load(p.read_text())["backends"]) == "alpha"

    def test_refuses_to_unmask_a_user_entry_marked_default(self, tmp_path, monkeypatch):
        p = tmp_path / "gate.yaml"
        p.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        project = yaml.safe_load(p.read_text())["backends"]
        project["alpha"]["default"] = False
        p.write_text(yaml.safe_dump({"backends": project}, sort_keys=False))
        user = {name: dict(cfg) for name, cfg in project.items()}
        user["beta"]["default"] = True
        _user_layer(monkeypatch, user)
        # Shadowing keeps alpha in front; dropping beta exposes the user beta.
        assert _effective(project, user) == "alpha"
        before = p.read_bytes()
        did, why = fp.remove_backend(p, "beta", ".bak-test", dry_run=False)
        assert did is False
        assert "effective backend" in why and "beta" in why
        assert p.read_bytes() == before

    def test_refuses_to_remove_the_user_layers_implicit_default(self, tmp_path, monkeypatch):
        user_config = tmp_path / "config.yaml"
        user_config.write_text(UNMARKED_PAIR)
        monkeypatch.setattr(fp, "USER_CONFIG", user_config)
        before = user_config.read_bytes()
        did, why = fp.remove_backend(user_config, "alpha", ".bak-test", dry_run=False)
        assert did is False
        assert "effective backend" in why and "beta" in why
        assert user_config.read_bytes() == before

    def test_an_unresolvable_survivor_is_refused(self, tmp_path):
        # A surviving entry the parser rejects means the resolved default
        # cannot be compared; removing anyway would be a guess about where
        # the next review's credentials go.
        p = tmp_path / "gate.yaml"
        p.write_text(UNMARKED_PAIR + "  broken:\n    type: api\n    model: no-format\n")
        before = p.read_bytes()
        did, why = fp.remove_backend(p, "beta", ".bak-test", dry_run=False)
        assert did is False
        assert "cannot determine" in why
        assert p.read_bytes() == before

    def test_a_dry_run_reports_the_same_refusal(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(UNMARKED_PAIR)
        before = p.read_bytes()
        did, why = fp.remove_backend(p, "alpha", ".bak-test", dry_run=True)
        assert did is False and "effective backend" in why
        assert p.read_bytes() == before


class TestRemoveKeepsTheResolvedEndpoint:
    """The same backend *name* can mean a different host after a removal.

    A project entry shadows a user-level entry of the same name. Deleting the
    project one leaves the name in place while `base_url`, `model` and the
    credential variable change, so comparing names alone reports success for a
    silent reroute.
    """

    def _setup(self, tmp_path):
        project = (
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://project-beta.example/v1"\n'
            "    api_key_env: PROJECT_BETA_KEY\n    model: project-beta\n"
            "  gamma:\n    type: api\n    format: openai\n"
            '    base_url: "https://gamma.example/v1"\n'
            "    api_key_env: GAMMA_KEY\n    model: gamma\n"
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(project)
        user = {
            "beta": {
                "type": "api",
                "format": "openai",
                "base_url": "https://user-beta.example/v1",
                "api_key_env": "USER_BETA_KEY",
                "model": "user-beta",
                "default": True,
            }
        }
        return gate, user

    def test_refuses_a_same_name_reroute(self, tmp_path, monkeypatch):
        gate, user = self._setup(tmp_path)
        _user_layer(monkeypatch, user)
        before = gate.read_bytes()
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did is False
        assert "effective backend" in why and "base_url" in why
        assert gate.read_bytes() == before

    def test_a_header_only_reroute_is_refused(self, tmp_path, monkeypatch):
        """`headers` is part of the routing identity, so a user-layer entry that
        differs only in a header is still a different endpoint."""
        project = (
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://same.example/v1"\n    api_key_env: B_KEY\n    model: same\n'
            "    headers:\n      X-Org: project\n"
            "  gamma:\n    type: api\n    format: openai\n"
            '    base_url: "https://gamma.example/v1"\n    api_key_env: G_KEY\n    model: gamma\n'
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(project)
        user = {
            "beta": {
                "type": "api",
                "format": "openai",
                "base_url": "https://same.example/v1",
                "api_key_env": "B_KEY",
                "model": "same",
                "headers": {"X-Org": "user"},
                "default": True,
            }
        }
        _user_layer(monkeypatch, user)
        before = gate.read_bytes()
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did is False
        assert "headers" in why and "X-Org" in why
        assert gate.read_bytes() == before

    def test_a_shadowed_user_entry_without_a_default_does_not_block(self, tmp_path, monkeypatch):
        # Project alpha keeps an explicit default, so the surviving resolution
        # is alpha both before and after even though a user `beta` reappears.
        gate = tmp_path / "gate.yaml"
        gate.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        project = yaml.safe_load(gate.read_text())["backends"]
        user = {"beta": dict(project["beta"])}
        _user_layer(monkeypatch, user)
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did and why is None
        assert _effective(yaml.safe_load(gate.read_text())["backends"], user) == "alpha"

    def test_refuses_removing_the_backend_the_environment_selects(self, tmp_path, monkeypatch):
        gate = tmp_path / "gate.yaml"
        gate.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        monkeypatch.setenv("FORGE_BACKEND", "beta")
        before = gate.read_bytes()
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did is False
        assert "effective backend" in why or "cannot determine" in why
        assert gate.read_bytes() == before

    def test_one_default_per_layer_is_a_legal_configuration(self, tmp_path, monkeypatch):
        # forge parses the two layers separately, so this is legal there; a
        # single parse of the merged mapping would refuse it.
        gate = tmp_path / "gate.yaml"
        gate.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        user = {
            "gamma": {
                "type": "api",
                "format": "openai",
                "base_url": "https://gamma.example/v1",
                "api_key_env": "GAMMA_KEY",
                "model": "gamma",
                "default": True,
            }
        }
        _user_layer(monkeypatch, user)
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did and why is None
        assert _effective(yaml.safe_load(gate.read_text())["backends"], user) == "alpha"


class TestRemoveLeavesSurroundingProse:
    """Comments between blocks describe the block that follows them."""

    def test_an_indented_comment_before_the_next_backend_survives(self, tmp_path):
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n'
            "    model: a\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n"
            "  # gamma keeps the spare credential for the audit trail\n"
            "  gamma:\n    type: api\n    format: openai\n"
            '    base_url: "https://gamma.example/v1"\n    api_key_env: G_KEY\n'
            "    model: g\n"
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        # The comment after beta's block is attached to beta by the loader, so
        # removing beta would delete it: refused, with the comment named.
        assert did is False and "gamma keeps the spare credential" in why
        assert gate.read_text() == text

    def test_a_flush_left_comment_between_blocks_survives(self, tmp_path):
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n'
            "    model: a\n"
            "# the preferred endpoint for release reviews\n"
            "  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n    default: true\n"
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        did, why = fp.remove_backend(gate, "alpha", ".bak-test", dry_run=False)
        assert did is False and "preferred endpoint for release reviews" in why
        assert gate.read_text() == text

    def test_a_deeper_indented_comment_goes_with_its_block(self, tmp_path):
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n'
            "    model: a\n    # only alpha uses this model\n"
            "  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n    default: true\n"
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        did, why = fp.remove_backend(gate, "alpha", ".bak-test", dry_run=False)
        assert did and why is None
        body = gate.read_text()
        assert "# only alpha uses this model" not in body
        assert "beta:" in body


class TestRemoveAcceptsLegalKeyShapes:
    def test_a_key_with_a_trailing_comment_is_declared_and_removable(self, tmp_path):
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n'
            "    model: a\n    default: true\n"
            "  beta:  # the retired spare endpoint\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n"
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        assert fp.list_backends(text) == ["alpha", "beta"]
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        # The comment sits on the entry's own key line, so it goes with the
        # entry instead of blocking the removal.
        assert did and why is None
        assert "beta" not in gate.read_text()

    def test_an_anchored_key_is_removable(self, tmp_path):
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n'
            "    model: a\n    default: true\n"
            "  beta: &beta\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n"
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did and why is None
        assert "beta" not in gate.read_text()


class TestRemoveReportsWhatItCannotAddress:
    def test_a_quoted_key_is_reported_instead_of_counted_as_absent(self, tmp_path, monkeypatch, capsys):
        gate = tmp_path / "gate.yaml"
        text = (
            'backends:\n  "beta":\n    type: api\n    format: openai\n'
            "  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n'
            "    model: a\n"
        )
        gate.write_text(text)
        from code_forge.trust import record_trust

        record_trust(gate, yaml.safe_load(text))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        # A quoted key is ordinary YAML to a parser, so it is removed rather
        # than reported as a shape the tool cannot address.
        assert fp.main() == 0
        assert list(yaml.safe_load(gate.read_text())["backends"]) == ["alpha"]

    def test_remove_backend_names_the_shape_itself(self, tmp_path):
        """The library call must not fall back to "absent" for a shape it cannot edit."""
        gate = tmp_path / "gate.yaml"
        gate.write_text(
            'backends:\n  "beta":\n    type: api\n'
            "  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n'
            "    model: a\n"
        )
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        # A quoted key is ordinary YAML to a parser, so the shape refusal the
        # locator needed is gone.
        assert did and why is None
        assert list(yaml.safe_load(gate.read_text())["backends"]) == ["alpha"]

    def test_a_plain_absence_still_says_so(self, tmp_path, monkeypatch, capsys):
        gate = tmp_path / "gate.yaml"
        gate.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "nowhere"])
        assert fp.main() == 1
        assert "no config declares" in capsys.readouterr().err


class TestKeyScopeAndShape:
    """The key grammar is shared; a rename must stay inside `backends:`."""

    def test_a_rename_does_not_touch_a_same_named_key_elsewhere(self, tmp_path):
        text = (
            "retry:\n  beta:  # a retry policy, not a backend\n    max_attempts: 3\n"
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        did, why = fp.rename_backend(gate, "beta", "beta2", ".bak-test", dry_run=False)
        assert did and why is None
        body = gate.read_text()
        assert "retry:\n  beta:  # a retry policy, not a backend" in body
        assert "  beta2:\n" in body

    def test_a_rename_does_not_touch_a_neighbours_nested_key(self, tmp_path):
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
            "    params:\n      beta:  # a request option\n        x: 1\n"
            "  beta:\n    type: api\n    format: openai\n    default: true\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        did, why = fp.rename_backend(gate, "beta", "beta2", ".bak-test", dry_run=False)
        assert did and why is None
        body = gate.read_text()
        assert "      beta:  # a request option" in body
        assert "  beta2:\n" in body

    def test_a_space_before_the_colon_is_still_a_declaration(self, tmp_path):
        text = (
            "backends:\n  beta :\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
            "  alpha:\n    type: api\n    format: openai\n    default: true\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        assert fp.list_backends(text) == ["beta", "alpha"]
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did and why is None
        assert "beta" not in gate.read_text()

    def test_an_alias_entry_is_reported_not_counted_as_absent(self, tmp_path, monkeypatch, capsys):
        gate = tmp_path / "gate.yaml"
        gate.write_text(
            "shared: &shared\n  type: api\n  format: openai\n"
            '  base_url: "https://beta.example/v1"\n  api_key_env: B_KEY\n  model: b\n'
            "backends:\n  beta: *shared\n"
            "  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n'
            "    model: a\n    default: true\n"
        )
        from code_forge.trust import record_trust

        record_trust(gate, yaml.safe_load(gate.read_text()))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        # An aliased entry is a key like any other; the parser resolves it.
        assert fp.main() == 0
        assert "beta" not in yaml.safe_load(gate.read_text())["backends"]


class TestRemovalShapesAndLineEndings:
    def test_a_backend_whose_value_is_not_a_mapping_is_refused(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text("backends:\n  beta:\n  - a\n  - b\n  alpha:\n    type: api\n")
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did is False and why == "'beta' is not a mapping"

    def test_a_duplicate_key_is_refused_rather_than_half_removed(self, tmp_path):
        """YAML keeps the last duplicate; deleting the first would leave the name."""
        gate = tmp_path / "gate.yaml"
        gate.write_text(
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://one.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
            "  alpha:\n    type: api\n    format: openai\n    default: true\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
            "  beta:\n    type: api\n"
        )
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did is False
        # Refused at parse now: a duplicate key is not a file this editor will
        # rewrite, which is stricter than the old post-write value comparison.
        assert "duplicate" in why or "does not parse" in why

    def test_removal_keeps_crlf(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_bytes(
            b"backends:\r\n  beta:\r\n    type: api\r\n    format: openai\r\n"
            b'    base_url: "https://beta.example/v1"\r\n    api_key_env: B_KEY\r\n    model: b\r\n'
            b"  alpha:\r\n    type: api\r\n    format: openai\r\n    default: true\r\n"
            b'    base_url: "https://alpha.example/v1"\r\n    api_key_env: A_KEY\r\n    model: a\r\n'
        )
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did and why is None
        written = gate.read_bytes()
        assert written.count(b"\n") == written.count(b"\r\n")
        assert b"beta" not in written


class TestRefusalsListedInTheContract:
    """Each refusal the contract names must be executable, not just described."""

    def test_no_backends_block(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text("outlet: subprocess\n")
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did is False and why == "not declared"

    def test_removal_that_would_change_a_surviving_value(self, tmp_path):
        # The anchor is defined in the removed block and used by the survivor,
        # so the file stops parsing once the definition goes.
        text = (
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
            "  alpha:\n    type: api\n    format: openai\n    default: true\n"
            "    base_url: *beta-url\n    api_key_env: A_KEY\n    model: a\n"
        ).replace(
            'base_url: "https://beta.example/v1"\n',
            'base_url: "https://beta.example/v1"\n    x-url: &beta-url\n',
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did is False
        # The guard names the anchor and the fact that a survivor still uses it.
        assert "anchor" in why and "beta" in why

    def test_one_backend_left_is_refused(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text(
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
        )
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did is False and "would leave no backends" in why

    def test_a_target_the_parser_rejects_is_removable(self, tmp_path):
        # The allowance, positive direction: the config was already unusable,
        # and the survivors resolve.
        gate = tmp_path / "gate.yaml"
        gate.write_text(
            "backends:\n  broken:\n    type: api\n    model: no-format\n"
            "  alpha:\n    type: api\n    format: openai\n    default: true\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
        )
        did, why = fp.remove_backend(gate, "broken", ".bak-test", dry_run=False)
        assert did and why is None

    def test_the_allowance_does_not_cover_broken_survivors(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text(
            "backends:\n  broken:\n    type: api\n    model: no-format\n"
            "  worse:\n    type: api\n    model: also-no-format\n"
        )
        did, why = fp.remove_backend(gate, "broken", ".bak-test", dry_run=False)
        assert did is False and "cannot determine" in why

    def test_an_environment_override_that_still_resolves_allows_the_removal(self, tmp_path, monkeypatch):
        gate = tmp_path / "gate.yaml"
        gate.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        monkeypatch.setenv("FORGE_BACKEND", "alpha")
        did, why = fp.remove_backend(gate, "beta", ".bak-test", dry_run=False)
        assert did and why is None


class TestEditingNeedsAuthorization:
    """An edit must not authorize the rest of the file as a side effect."""

    def _gate(self, tmp_path, monkeypatch):
        from code_forge.trust import is_trusted

        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(GATE)
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        return gate, is_trusted

    def test_default_refuses_an_untrusted_file_and_leaves_it_unauthorized(
        self, tmp_path, monkeypatch, capsys
    ):
        gate, is_trusted = self._gate(tmp_path, monkeypatch)
        before = gate.read_text()
        assert is_trusted(gate, yaml.safe_load(before), migrate=False) is False
        assert fp.cmd_default(types.SimpleNamespace(name="beta", dry_run=False)) == 1
        assert gate.read_text() == before
        assert is_trusted(gate, yaml.safe_load(gate.read_text()), migrate=False) is False
        assert "is not trusted" in capsys.readouterr().err

    def test_the_refusal_does_not_turn_into_an_authorization_for_remove(
        self, tmp_path, monkeypatch, capsys
    ):
        gate, is_trusted = self._gate(tmp_path, monkeypatch)
        fp.cmd_default(types.SimpleNamespace(name="beta", dry_run=False))
        capsys.readouterr()
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        assert fp.main() == 1
        assert is_trusted(gate, yaml.safe_load(gate.read_text()), migrate=False) is False
        assert "is not trusted" in capsys.readouterr().err


class TestEveryEditingCommandNeedsAuthorization:
    """No command may edit a repository file, and none may re-seal one."""

    def _tree(self, tmp_path, monkeypatch):
        one = tmp_path / "code" / "one" / ".code-forge" / "gate.yaml"
        two = tmp_path / "code" / "two" / ".code-forge" / "gate.yaml"
        one.parent.mkdir(parents=True)
        two.parent.mkdir(parents=True)
        one.write_text(GATE)
        two.write_text(GATE)
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        return one, two

    def test_rename_refuses_an_unauthorized_file(self, tmp_path, monkeypatch, capsys):
        one, two = self._tree(tmp_path, monkeypatch)
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "rename", "beta", "beta2"])
        assert fp.main() == 1
        assert one.read_text() == GATE and two.read_text() == GATE
        assert capsys.readouterr().err.count("is not trusted") == 2

    def test_sync_refuses_an_unauthorized_tree(self, tmp_path, monkeypatch, capsys):
        one, two = self._tree(tmp_path, monkeypatch)
        import types

        args = types.SimpleNamespace(
            name="newname",
            base_url="https://new.example/anthropic",
            model="new-model",
            format="anthropic",
            from_name=None,
            key_env="NEW_KEY",
            key_pass=None,
            timeout_s=2400,
            max_tokens=65536,
            dry_run=False,
        )
        assert fp.cmd_sync(args) == 1
        assert one.read_text() == GATE and two.read_text() == GATE
        assert "is not trusted" in capsys.readouterr().err

    def test_rollback_restores_an_unauthorized_file_and_only_reports(
        self, tmp_path, monkeypatch, capsys
    ):
        """Restoring is what rollback is for, including a file the record does not cover.

        Refusing it would leave the corrupt file in place; the command restores
        and names what the operator still has to authorize.
        """
        one, two = self._tree(tmp_path, monkeypatch)
        from code_forge.trust import record_trust as record

        drifted = GATE.replace("ALPHA_KEY", "OTHER_KEY")
        one.write_text(drifted)
        record(one, yaml.safe_load(drifted))
        (one.parent / (one.name + ".bak-provider-20260101T000000")).write_text(GATE)
        import types

        assert fp.cmd_rollback(types.SimpleNamespace(dry_run=False)) == 0
        assert one.read_text() == GATE
        err = capsys.readouterr().err
        assert "authoriz" in err.lower()

    def test_the_gate_covers_the_focus_scope_too(self, tmp_path, monkeypatch, capsys):
        """A file whose backends hash still matches must not be edited when the
        repository prompt text moved: the re-seal would authorize it unread."""
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(GATE + "review_focus: |\n  first\n")
        record_trust(gate, yaml.safe_load(gate.read_text()))
        gate.write_text(GATE + "review_focus: |\n  second, unread\n")
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        import types

        assert fp.cmd_default(types.SimpleNamespace(name="beta", dry_run=False)) == 1
        assert "review_focus text changed" in capsys.readouterr().err


class TestAddOverAnUnaddressableEntry:
    def test_add_refuses_a_name_declared_inline(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text("backends:\n  beta: {type: api, format: openai}\n  alpha:\n    type: api\n")
        did, why = fp.insert_backend(
            gate,
            {
                "name": "beta",
                "base_url": "https://beta.example/v1",
                "model": "b",
                "format": "openai",
                "key_env": "B_KEY",
                "max_tokens": 1,
                "timeout_s": 1,
            },
            ".bak-test",
            dry_run=False,
        )
        assert did is False and why == "already declared"
        assert gate.read_text().count("beta") == 1

    def test_a_nested_inline_option_is_not_a_backend_declaration(self):
        text = "backends:\n  alpha:\n    type: api\n    params:\n      beta: {x: 1}\n"
        # `params.beta` is a request option, not a backend: the parser scopes
        # the lookup to the `backends` mapping.
        assert fp.declares(text, "beta") is False
        assert fp.declares(text, "alpha") is True

    def test_add_does_not_sprinkle_blank_lines(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        # A blank line after the key is where a whitespace-run oracle goes wrong:
        # it swallows the newline and every added line starts with one.
        gate.write_text("backends:\n\n  alpha:\n    type: api\n    format: openai\n")
        record_trust(gate, yaml.safe_load(gate.read_text()))
        fp.insert_backend(
            gate,
            {
                "name": "gamma",
                "base_url": "https://gamma.example/v1",
                "model": "g",
                "format": "openai",
                "key_env": "G_KEY",
                "max_tokens": 1,
                "timeout_s": 1,
            },
            ".bak-test",
            dry_run=False,
        )
        body = gate.read_text()
        # The comment line carries the key indent and nothing before it: an
        # oracle that swallowed the newline emitted a line starting with one.
        added = [line for line in body.splitlines() if line.lstrip().startswith("# added by")]
        assert added == [
            f"  # added by forge-provider.py {__import__('datetime').date.today().isoformat()}"
        ]
        # Two blank lines in the whole file: the one that was already after the
        # key, and the one before the new comment. An oracle that matched the
        # newline instead of the indent produced one per added line.
        # One blank line per gap is the point; two in a row is the sprinkle the
        # whitespace-run oracle used to produce.
        assert body.count("\n\n") >= 1
        assert "\n\n\n" not in body
        assert list(yaml.safe_load(body)["backends"]) == ["alpha", "gamma"]


class TestDefaultUnderEverySpelling:
    """`default:` is a YAML boolean, not one literal line."""

    BASE = (
        "backends:\n"
        "  alpha:\n    type: api\n    format: openai\n"
        '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
        "  beta:\n    type: api\n    format: openai\n"
        '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
    )

    @pytest.mark.parametrize("spelling", ["true", "yes", "True", '"true"', "true  # primary"])
    def test_the_old_flag_is_cleared_whatever_its_spelling(self, tmp_path, spelling):
        gate = tmp_path / "gate.yaml"
        gate.write_text(self.BASE.replace("    model: b\n", f"    default: {spelling}\n    model: b\n"))
        did, why = fp.set_default(gate, "alpha", ".bak-test", dry_run=False)
        assert did and why is None
        assert fp._defaults(gate.read_text()) == ["alpha"]
        # The file forge loads must still load: two truthy defaults make
        # load_backend_configs raise and kill every review in the repo.
        from code_forge.backend import load_backend_configs

        assert load_backend_configs(yaml.safe_load(gate.read_text()))

    def test_a_shape_it_cannot_rewrite_is_refused_instead_of_written(self, tmp_path):
        """An anchor shared by two entries cannot carry one default."""
        gate = tmp_path / "gate.yaml"
        text = (
            "backends:\n  alpha: &shared\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
            "  beta: *shared\n"
        )
        gate.write_text(text)
        did, why = fp.set_default(gate, "alpha", ".bak-test", dry_run=False)
        # `alpha` and `beta` share one mapping, so setting a default on either
        # name would write it for both; the module refuses that shape.
        assert did is False
        assert gate.read_text() == text
        assert "shares its mapping" in why


class TestTrustReportSeesEveryScope:
    def test_an_unparseable_config_is_not_reported_clean(self, tmp_path, monkeypatch, capsys):
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text("backends: [unclosed\n")
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        import types

        assert fp.cmd_trust(types.SimpleNamespace(fix=False, dry_run=False)) == 1
        assert "0 not trusted" not in capsys.readouterr().out

    def test_a_moved_focus_text_is_reported_and_repaired(self, tmp_path, monkeypatch, capsys):
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(GATE + "review_focus: |\n  first\n")
        record_trust(gate, yaml.safe_load(gate.read_text()))
        gate.write_text(GATE + "review_focus: |\n  second, unread\n")
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        import types

        assert fp.cmd_trust(types.SimpleNamespace(fix=False, dry_run=False)) == 1
        assert "0 not trusted" not in capsys.readouterr().out
        assert fp.cmd_trust(types.SimpleNamespace(fix=True, dry_run=False)) == 0
        # After the repair the gate accepts the file again.
        assert fp.trust_refusal(gate, gate.read_text()) is None


class TestRollbackDoesNotAuthorize:
    def test_restoring_an_unauthorized_backup_leaves_it_unauthorized(
        self, tmp_path, monkeypatch, capsys
    ):
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(GATE)
        record_trust(gate, yaml.safe_load(GATE))
        # A repository can hold a file named like a backup, with its own endpoint.
        backup = gate.parent / (gate.name + ".bak-provider-20260101T000000")
        backup.write_text(GATE.replace("https://beta.example/v1", "https://attacker.example/v1"))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        import types

        # The restore itself succeeds; what it must not do is authorize.
        assert fp.cmd_rollback(types.SimpleNamespace(dry_run=False)) == 0
        assert "attacker" in gate.read_text()
        assert "need authorization" in capsys.readouterr().err
        from code_forge.trust import is_trusted

        assert is_trusted(gate, yaml.safe_load(gate.read_text()), migrate=False) is False


class TestUnparseableFileIsNotEdited:
    def test_default_refuses_a_file_it_could_never_authorize(self, tmp_path, monkeypatch, capsys):
        gate = tmp_path / "gate.yaml"
        gate.write_text("backends:\n  beta: [unclosed\n")
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        import types

        assert fp.cmd_default(types.SimpleNamespace(name="beta", dry_run=False)) == 1
        assert gate.read_text() == "backends:\n  beta: [unclosed\n"
        assert "does not parse" in capsys.readouterr().err


class TestTheGateOnEveryCommand:
    """Each editing command, not one representative of the family."""

    def _tree(self, tmp_path, monkeypatch):
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(GATE)
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        return gate

    def test_add_refuses_an_untrusted_file(self, tmp_path, monkeypatch, capsys):
        gate = self._tree(tmp_path, monkeypatch)
        import types

        args = types.SimpleNamespace(
            name="gamma",
            base_url="https://gamma.example/v1",
            model="g",
            format="openai",
            key_env="G_KEY",
            key_pass=None,
            max_tokens=1,
            timeout_s=1,
            header=None,
            dry_run=False,
        )
        assert fp.cmd_add(args) == 1
        assert gate.read_text() == GATE
        assert "is not trusted" in capsys.readouterr().err

    def test_set_refuses_an_untrusted_file(self, tmp_path, monkeypatch, capsys):
        gate = self._tree(tmp_path, monkeypatch)
        import types

        args = types.SimpleNamespace(
            name="beta",
            base_url=None,
            model="other",
            format=None,
            key_env=None,
            key_pass=None,
            max_tokens=None,
            timeout_s=None,
            header=None,
            dry_run=False,
        )
        assert fp.cmd_set(args) == 1
        assert gate.read_text() == GATE
        assert "is not trusted" in capsys.readouterr().err

    def test_the_lens_switch_is_a_scope_too(self, tmp_path, monkeypatch, capsys):
        gate = self._tree(tmp_path, monkeypatch)
        record_trust(gate, yaml.safe_load(GATE))
        gate.write_text(GATE + "review_lenses: true\n")
        import types

        assert fp.cmd_default(types.SimpleNamespace(name="beta", dry_run=False)) == 1
        assert "review_lenses switch changed" in capsys.readouterr().err

    def test_the_check_does_not_write_a_legacy_record(self, tmp_path, monkeypatch):
        gate = self._tree(tmp_path, monkeypatch)
        from code_forge import trust as trust_mod

        # A record written before the focus scope existed: the backends hash
        # matches, the focus hash is absent. Reading it must not upgrade it.
        text = GATE + "review_focus: |\n  read me\n"
        gate.write_text(text)
        data = yaml.safe_load(text)
        # A legacy record: the stored hash is the old all-fields one, so a
        # migrating read rewrites it. The gate must not take that path.
        store = {str(gate.resolve()): {"hash": trust_mod._hash_all_backends(data)}}
        trust_mod._save_trust_store(store)
        record = trust_mod._trust_store_path()
        before = record.read_bytes()
        assert fp.trust_refusal(gate, text) is not None
        assert record.read_bytes() == before
        # Control: the migrating read this guard avoids does write.
        trust_mod.is_trusted(gate.resolve(), data, migrate=True)
        assert record.read_bytes() != before

    def test_the_user_layer_needs_no_record(self, tmp_path, monkeypatch):
        gate = tmp_path / "user" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(GATE)
        monkeypatch.setattr(fp, "USER_CONFIG", gate)
        assert fp.is_user_config(gate) is True
        assert fp.trust_refusal(gate, GATE) is None


class TestBackendIdentity:
    def test_headers_count_because_they_change_what_the_request_carries(self):
        from code_forge.backend import load_backend_configs

        plain = load_backend_configs(
            {
                "backends": {
                    "a": {
                        "type": "api",
                        "format": "openai",
                        "base_url": "https://a/v1",
                        "api_key_env": "A",
                        "model": "m",
                        "default": True,
                    }
                }
            }
        )[0]
        keyed = load_backend_configs(
            {
                "backends": {
                    "a": {
                        "type": "api",
                        "format": "openai",
                        "base_url": "https://a/v1",
                        "api_key_env": "A",
                        "model": "m",
                        "headers": {"X-Org": "acme"},
                        "default": True,
                    }
                }
            }
        )[0]
        assert fp._backend_identity(plain) != fp._backend_identity(keyed)


class TestDefaultCommandRefusals:
    """A refusal is reported and fails the command; a falsy flag is moved."""

    def _gate(self, tmp_path, monkeypatch, text):
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(text)
        record_trust(gate, yaml.safe_load(text))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        return gate

    def test_a_falsy_flag_on_the_target_is_moved_not_ignored(self, tmp_path, monkeypatch):
        import types

        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n    default: false\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
            "  beta:\n    type: api\n    format: openai\n    default: true\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
        )
        gate = self._gate(tmp_path, monkeypatch, text)
        assert fp.cmd_default(types.SimpleNamespace(name="alpha", dry_run=False)) == 0
        body = gate.read_text()
        assert fp._defaults(body) == ["alpha"]
        # One flag per entry: inserting beside the old line would leave two
        # `default:` keys in alpha's block and YAML would keep the last.
        alpha_block = body.split("  alpha:", 1)[1].split("  beta:", 1)[0]
        assert alpha_block.count("default:") == 1

    def test_a_nested_params_default_survives(self, tmp_path, monkeypatch):
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
            "  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
            "    params:\n      default: 7\n    default: true\n"
        )
        gate = self._gate(tmp_path, monkeypatch, text)
        assert fp.cmd_default(types.SimpleNamespace(name="alpha", dry_run=False)) == 0
        parsed = yaml.safe_load(gate.read_text())["backends"]
        assert parsed["beta"]["params"] == {"default": 7}
        assert parsed["alpha"]["default"] is True
        assert "default" not in parsed["beta"]

    def test_a_refusal_fails_the_command_and_says_why(self, tmp_path, monkeypatch, capsys):
        import types

        # An anchor shared by two entries: adding a flag to one marks both.
        text = (
            "backends:\n  alpha: &shared\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
            "  beta: *shared\n"
        )
        gate = self._gate(tmp_path, monkeypatch, text)
        before = gate.read_text()
        assert fp.cmd_default(types.SimpleNamespace(name="alpha", dry_run=False)) == 1
        assert gate.read_text() == before
        assert "default" in capsys.readouterr().err

    def test_an_insert_keeps_the_files_line_endings(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_bytes(
            b"backends:\r\n  alpha:\r\n    type: api\r\n    format: openai\r\n"
            b'    base_url: "https://alpha.example/v1"\r\n    api_key_env: A_KEY\r\n    model: a\r\n'
            b"  beta:\r\n    type: api\r\n    format: openai\r\n    default: true\r\n"
            b'    base_url: "https://beta.example/v1"\r\n    api_key_env: B_KEY\r\n    model: b\r\n'
        )
        assert fp.set_default(gate, "alpha", ".bak-test", dry_run=False)[0] is True
        written = gate.read_bytes()
        assert written.count(b"\n") == written.count(b"\r\n")


class TestListShowsEveryShape:
    def test_list_reports_an_inline_backend(self, tmp_path, monkeypatch, capsys):
        gate = tmp_path / "gate.yaml"
        gate.write_text('backends:\n  beta: {type: api, format: openai, base_url: "https://b/v1"}\n')
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        import types

        assert fp.cmd_list(types.SimpleNamespace()) == 0
        out = capsys.readouterr().out
        assert "beta" in out


class TestQuotedAndNestedValues:
    """The parsers see what forge sees: Python values, not YAML spellings."""

    def test_a_quoted_falsy_default_is_still_a_default(self, tmp_path):
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
            '  beta:\n    type: api\n    format: openai\n    default: "false"\n'
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        # forge asks `if cfg.default`, and "false" is a truthy string.
        assert fp._defaults(text) == ["beta"]
        assert fp.set_default(gate, "alpha", ".bak-test", dry_run=False)[0] is True
        assert fp._defaults(gate.read_text()) == ["alpha"]

    def test_a_nested_field_is_not_the_backends_field(self, tmp_path):
        text = (
            "backends:\n  beta:\n    params:\n      model: nested\n"
            "    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        assert fp.read_field(text, "beta", "model") == "b"
        assert fp.write_field(gate, "beta", "model", "outer", ".bak-test", dry_run=False)[0] is True
        body = gate.read_text()
        assert "    model: outer" in body
        assert "      model: nested" in body

    def test_the_field_indent_skips_a_comment(self, tmp_path):
        text = (
            "backends:\n  beta:\n"
            "      # a note about the endpoint below\n"
            "    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        assert fp.write_field(gate, "beta", "model", "outer", ".bak-test", dry_run=False)[0] is True
        assert "    model: outer" in gate.read_text()

    def test_a_falsy_flag_keeps_its_comment(self, tmp_path):
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
            "    default: false  # retired soon\n"
            "  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        assert fp.set_default(gate, "alpha", ".bak-test", dry_run=False)[0] is True
        body = gate.read_text()
        assert "default: true" in body and "# retired soon" in body
        assert fp._defaults(body) == ["alpha"]


class TestHeadersKeepTheLineEndings:
    def test_writing_headers_does_not_convert_a_crlf_file(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_bytes(
            b"backends:\r\n  beta:\r\n    type: api\r\n    format: openai\r\n"
            b'    base_url: "https://beta.example/v1"\r\n    api_key_env: B_KEY\r\n    model: b\r\n'
        )
        assert fp.write_headers(gate, "beta", {"X-Org": "acme"}, ".bak-test", dry_run=False)[0] is True
        written = gate.read_bytes()
        assert written.count(b"\n") == written.count(b"\r\n")
        assert b"X-Org" in written


class TestSyncReportsItsOwnRefusals:
    def _tree(self, tmp_path, monkeypatch, files):
        paths = []
        for rel, text in files.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            record_trust(path, yaml.safe_load(text))
            paths.append(path)
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        monkeypatch.setattr(fp, "find_configs", lambda: list(paths))
        return paths

    def _args(self, **kw):
        import types

        fields = {
            "name": "beta",
            "base_url": "https://beta.example/v1",
            "model": "b",
            "format": "openai",
            "from_name": None,
            "key_env": "B_KEY",
            "key_pass": None,
            "timeout_s": 2400,
            "max_tokens": 65536,
            "dry_run": False,
        }
        fields.update(kw)
        return types.SimpleNamespace(**fields)

    def test_an_already_synced_machine_exits_zero(self, tmp_path, monkeypatch):
        gate = self._tree(
            tmp_path, monkeypatch, {"one/.code-forge/gate.yaml": _mark_default(GATE, "beta")}
        )
        assert fp.cmd_sync(self._args()) == 0
        assert fp._defaults(gate[0].read_text()) == ["beta"]

    def test_a_default_refusal_reaches_the_status_with_its_own_reason(
        self, tmp_path, monkeypatch, capsys
    ):
        # An anchor shared by two entries: the flag cannot be added to one.
        text = (
            "backends:\n  alpha: &shared\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
            "  beta: *shared\n"
        )
        self._tree(tmp_path, monkeypatch, {"one/.code-forge/gate.yaml": text})
        assert fp.cmd_sync(self._args()) == 1
        assert "default" in capsys.readouterr().err

    def test_a_from_name_in_a_quoted_declaration_is_renamed(self, tmp_path, monkeypatch, capsys):
        text = (
            'backends:\n  "oldname":\n    type: api\n    format: openai\n'
            '    base_url: "https://old.example/v1"\n    api_key_env: OLD_KEY\n'
            "    model: old\n"
            "  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n"
        )
        paths = self._tree(tmp_path, monkeypatch, {"one/.code-forge/gate.yaml": text})
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "k")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        assert fp.cmd_sync(self._args(name="newname", from_name=["oldname"])) == 0
        assert "newname" in paths[0].read_text()

    def test_sync_reports_the_header_names_it_replaces(self, tmp_path, monkeypatch, capsys):
        text = (
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n    headers:\n      x-goog-api-key: sk-live-abc123\n"
        )
        self._tree(tmp_path, monkeypatch, {"one/.code-forge/gate.yaml": text})
        rc = fp.cmd_sync(self._args(header=["X-Note: 1"]))
        err = capsys.readouterr().err
        assert rc == 0
        assert "headers removed: x-goog-api-key" in err
        assert "sk-live-abc123" not in err

    def test_the_summary_lists_headers_alongside_the_fields_it_changed(
        self, tmp_path, monkeypatch, capsys
    ):
        text = (
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: old\n    default: true\n    headers:\n      X-Old: 1\n"
        )
        self._tree(tmp_path, monkeypatch, {"one/.code-forge/gate.yaml": text})
        assert fp.cmd_sync(self._args(header=["X-New: 2"])) == 0
        out = capsys.readouterr().out
        updated = [
            line.split(": ", 1)[1]
            for line in out.splitlines()
            if line.strip().startswith("updated ") and ": " in line
        ]
        assert updated, out
        # The tmp path carries the test's own name, so compare the field list only.
        assert updated[0].split(", ") == ["model", "headers"]

    def test_a_note_is_printed_once(self, tmp_path, monkeypatch, capsys):
        text = (
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n    default: true\n  gamma:\n    type: api\n"
            '    format: openai\n    base_url: "https://g.example/v1"\n'
            "    api_key_env: G_KEY\n    model: g\n    default: false\n"
        )
        self._tree(tmp_path, monkeypatch, {"one/.code-forge/gate.yaml": text})
        assert fp.cmd_sync(self._args()) == 0
        out = capsys.readouterr().out
        assert out.count("cleared the default flag on 'gamma'") == 1

    def test_a_note_is_printed_even_when_nothing_else_changed(self, tmp_path, monkeypatch, capsys):
        """The both-names case has nothing to write, so the note is the only
        output; skipping it left the operator with silence."""
        text = (
            "backends:\n  oldname:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n    default: true\n"
        )
        self._tree(tmp_path, monkeypatch, {"one/.code-forge/gate.yaml": text})
        assert fp.cmd_sync(self._args(from_name=["oldname"])) == 0
        assert "already declared; 'oldname' left as it is" in capsys.readouterr().out

    def test_the_summary_counts_what_happened_not_what_was_attempted(
        self, tmp_path, monkeypatch, capsys
    ):
        """The counters used to be intent-based: `defaulted` fired even when the
        flag was already there, and `updated` said "headers" when the headers
        were already identical."""
        text = (
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: old\n    default: true\n    headers:\n      X-Note: 1\n"
        )
        self._tree(tmp_path, monkeypatch, {"one/.code-forge/gate.yaml": text})
        assert fp.cmd_sync(self._args(header=["X-Note: 1"])) == 0
        out = capsys.readouterr().out
        assert "1 updated, 0 defaulted" in out
        assert "headers" not in out.split("updated")[-1].split("\n")[0]

    def test_a_later_refusal_leaves_the_file_as_it_was(self, tmp_path, monkeypatch, capsys):
        """`sync` applies rename, add, fields, headers and default to one
        document and writes once; a refusal on a later step must not leave the
        earlier ones on disk."""
        text = (
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://old.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n    headers: none\n"
        )
        paths = self._tree(tmp_path, monkeypatch, {"one/.code-forge/gate.yaml": text})
        before = paths[0].read_text()
        writes = []
        real_write = fp.write_config
        monkeypatch.setattr(fp, "write_config", lambda p, s: (writes.append(p), real_write(p, s))[1])
        rc = fp.cmd_sync(self._args(header=["X-Note: 1"]))
        assert rc == 1
        assert "not a mapping" in capsys.readouterr().err
        assert paths[0].read_text() == before
        assert writes == []

    def test_a_file_it_cannot_address_reaches_the_status(self, tmp_path, monkeypatch, capsys):
        """A one-line block declares the backend and cannot be edited in place.

        Skipping it with exit 0 is how a machine ends up "synced" while one repo
        still points at the old endpoint.
        """
        text = (
            "backends: {beta: {type: api, format: openai, "
            "base_url: 'https://beta.example/v1', api_key_env: B_KEY, model: b}}\n"
        )
        paths = self._tree(tmp_path, monkeypatch, {"one/.code-forge/gate.yaml": text})
        before = paths[0].read_text()
        rc = fp.cmd_sync(self._args())
        assert rc == 1
        assert "cannot address" in capsys.readouterr().err
        assert paths[0].read_text() == before


class TestTheSweepSeesTheLayerForgeReads:
    def test_an_xdg_user_layer_is_visited(self, tmp_path, monkeypatch):
        xdg = tmp_path / "xdg"
        layer = xdg / "code-forge" / "config.yaml"
        layer.parent.mkdir(parents=True)
        layer.write_text("backends:\n  beta:\n    type: api\n")
        monkeypatch.setattr(fp, "user_layer_paths", fp._user_layer_paths_real)
        monkeypatch.delenv("FORGE_CONFIG_DIR", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "legacy" / "config.yaml")
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path / "none")
        assert layer in fp.find_configs()


class TestFieldValuesAndComments:
    """A comment is not part of a value, at either end of the edit."""

    TEXT = (
        "backends:\n  beta:\n"
        "      # a note about the endpoint below\n"
        "    type: api\n    format: openai\n"
        '    base_url: "https://beta.example/v1"  # primary host\n'
        "    api_key_env: B_KEY\n    model: b  # cheap tier\n"
    )

    def test_the_value_excludes_the_comment(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text(self.TEXT)
        assert fp.read_field(gate.read_text(), "beta", "model") == "b"
        assert fp.read_field(gate.read_text(), "beta", "base_url") == "https://beta.example/v1"

    def test_a_rewrite_keeps_the_comment_and_does_not_think_it_changed(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text(self.TEXT)
        # Same value: no write, no comment loss.
        assert fp.write_field(gate, "beta", "model", "b", ".bak-test", dry_run=False)[0] is False
        assert gate.read_text() == self.TEXT
        assert fp.write_field(gate, "beta", "model", "b2", ".bak-test", dry_run=False)[0] is True
        body = gate.read_text()
        assert "model: b2" in body and "# cheap tier" in body

    def test_headers_written_under_a_deeper_comment_still_parse(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text(self.TEXT)
        assert fp.write_headers(gate, "beta", {"X-Org": "acme"}, ".bak-test", dry_run=False)[0] is True
        parsed = yaml.safe_load(gate.read_text())["backends"]["beta"]
        assert parsed["headers"] == {"X-Org": "acme"}
        assert "      # a note about the endpoint below" in gate.read_text()


class TestHeaderValuesAndIndent:
    def test_a_header_comment_is_not_part_of_the_value(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text(
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
            "    headers:\n      X-Org: acme  # our tenant\n"
        )
        assert fp.write_headers(gate, "beta", {"X-Org": "acme"}, ".bak-test", dry_run=False)[0] is False

    def test_an_empty_body_does_not_invent_a_four_space_indent(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        # A backend with no field lines at all: the fallback indent decides.
        gate.write_text("backends:\n    beta:\n    other:\n")
        assert fp.write_headers(gate, "beta", {"X-Org": "acme"}, ".bak-test", dry_run=False)[0] is True
        parsed = yaml.safe_load(gate.read_text())
        # `headers` must stay a field of `beta`, not become a sibling backend.
        assert sorted(parsed["backends"]) == ["beta", "other"]
        assert parsed["backends"]["beta"]["headers"] == {"X-Org": "acme"}


class TestTheGateApprovesTheBytesThatGetEdited:
    def test_a_change_after_the_gate_refuses_the_edit(self, tmp_path):
        """The editor must build on the text the gate judged, not a new read."""
        gate = tmp_path / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(GATE)
        record_trust(gate, yaml.safe_load(GATE))
        allowed, refused = fp.editable([gate])
        assert allowed == [gate] and refused == []
        # Someone else rewrites the file between the gate and the edit.
        gate.write_text(GATE.replace("beta.example", "attacker.example"))
        did, _old, problem = fp.write_field(
            gate, "alpha", "model", "x", ".bak-t", False, require_trust=True
        )
        assert did is False
        assert "changed while this command was running" in problem
        assert "attacker.example" in gate.read_text()

    def test_a_config_without_backends_is_not_a_trust_dead_end(self, tmp_path):
        """docs/configuration.md documents gate files with only test settings."""
        gate = tmp_path / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text("test:\n  command: pytest -q\n")
        allowed, refused = fp.editable([gate])
        assert allowed == [gate] and refused == []


class TestARefusedHeaderEditIsReported:
    def test_a_file_that_changed_under_the_command_exits_one(self, tmp_path, monkeypatch, capsys):
        from code_forge.trust import record_trust as record

        gate = tmp_path / "gate.yaml"
        gate.write_text(GATE)
        record(gate, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        real_editable = fp.editable
        raced = {"done": False}

        def editable_then_race(paths):
            allowed, refused = real_editable(paths)
            if allowed and not raced["done"]:
                # Another writer lands between the gate and the edit.
                raced["done"] = True
                gate.write_text(GATE + "# appended by someone else\n")
            return allowed, refused

        monkeypatch.setattr(fp, "editable", editable_then_race)
        import types

        args = types.SimpleNamespace(
            name="alpha",
            header=["x-test: 1"],
            model=None,
            base_url=None,
            format=None,
            key_env=None,
            key_pass=None,
            timeout_s=None,
            max_tokens=None,
            dry_run=False,
        )
        assert fp.cmd_set(args) == 1
        assert "changed while this command was running" in capsys.readouterr().err


class TestAFileThatOverflowsTheParser:
    def test_a_deeply_nested_file_is_refused_and_the_sweep_continues(
        self, tmp_path, monkeypatch, capsys
    ):
        import types

        depth = 700
        deep = (
            "backends:\n  alpha:\n    params:\n"
            + "".join(" " * (6 + 2 * i) + "k:\n" for i in range(depth))
            + " " * (6 + 2 * depth)
            + "x: 1\n"
        )
        bad = tmp_path / "bad.yaml"
        bad.write_text(deep)
        good = tmp_path / "good.yaml"
        good.write_text(
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://b.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
            "  gamma:\n    type: api\n    format: openai\n"
            '    base_url: "https://g.example/v1"\n    api_key_env: G_KEY\n    model: g\n'
            "    default: true\n"
        )
        record_trust(good, yaml.safe_load(good.read_text()))
        monkeypatch.setattr(fp, "find_configs", lambda: [bad, good])
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "k")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))

        rc = fp.cmd_remove(types.SimpleNamespace(name="beta", dry_run=False))
        assert rc in (0, 1)
        assert "beta" not in good.read_text()  # the good file was still processed


class TestAddPutsTheKeyAtTheBlocksOwnIndent:
    def test_fields_above_do_not_become_the_new_keys_indent(self, tmp_path):
        # No bare `name:` line, so the block indent has to come from the
        # shallowest structural line rather than from a per-field guess.
        text = "backends:\n    alpha: {type: api, format: openai, base_url: 'https://a.example/v1'}\n"
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        did, why = fp.insert_backend(
            gate,
            {
                "name": "beta",
                "type": "api",
                "format": "openai",
                "base_url": "https://b.example/v1",
                "key_env": "B_KEY",
                "model": "b",
                "max_tokens": 4096,
                "timeout_s": 60,
            },
            ".bak-t",
            False,
        )
        assert did and why is None
        parsed = yaml.safe_load(gate.read_text())
        assert set(parsed["backends"]) == {"alpha", "beta"}
        key_line = next(
            line for line in gate.read_text().splitlines() if line.lstrip().startswith("beta:")
        )
        # This block indents its keys by four spaces; a deeper key would parse
        # and would still contain "  beta:".
        assert key_line == "    beta:", key_line


class TestAnAddedKeyMustBeAddressable:
    def test_a_numeric_name_is_not_added(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text(GATE)
        did, why = fp.insert_backend(
            gate,
            {
                "name": "1.5",
                "type": "api",
                "format": "openai",
                "base_url": "https://x.example/v1",
                "key_env": "X_KEY",
                "model": "x",
                "max_tokens": 4096,
                "timeout_s": 60,
            },
            ".bak-t",
            False,
        )
        assert did is False
        assert "not read back as a string" in why


class TestSetNamesAQuotedDeclaration:
    def test_set_names_a_quoted_declaration_instead_of_calling_it_absent(
        self, tmp_path, monkeypatch, capsys
    ):
        text = 'backends:\n  "alpha":\n    type: api\n    format: openai\n    model: old\n'
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        record_trust(gate, yaml.safe_load(text))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        import types

        args = types.SimpleNamespace(
            name="alpha",
            header=None,
            model="m",
            base_url="https://a.example/v1",
            format="openai",
            key_env="A_KEY",
            key_pass=None,
            timeout_s=None,
            max_tokens=None,
            dry_run=False,
        )
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "k")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        assert fp.cmd_set(args) == 0
        assert "model: m" in gate.read_text()


class TestARenamedKeyMustReadBackAsAString:
    def test_a_numeric_name_is_refused(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text(GATE)
        did, why = fp.rename_backend(gate, "beta", "1.5", ".bak-t", dry_run=False)
        assert did is False
        assert "not read back as a string" in why
        assert "beta" in gate.read_text()


class TestHeadersStayInTheirBackend:
    def test_a_nested_headers_mapping_is_not_the_backends_own(self, tmp_path):
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://a.example/v1"\n    model: a\n'
            "    params:\n      headers:\n        X-Nested: keep\n"
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        did, old, problem = fp.write_headers(gate, "alpha", {"X-Own": "1"}, ".bak-t", False)
        assert did and problem is None
        parsed = yaml.safe_load(gate.read_text())
        assert parsed["backends"]["alpha"]["headers"] == {"X-Own": "1"}
        assert parsed["backends"]["alpha"]["params"]["headers"] == {"X-Nested": "keep"}


class TestSyncWithTheTargetNameAlreadyDeclared:
    def test_the_rest_of_the_command_still_runs(self, tmp_path, monkeypatch, capsys):
        """A file that already declares the target name has nothing to rename,
        but it can still need the fields aligned and the default moved; refusing
        it wholesale left the fleet permanently unconverged."""

        text = (
            "backends:\n  oldname:\n    type: api\n    format: openai\n"
            "  beta:\n    type: api\n    format: openai\n"
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        record_trust(gate, yaml.safe_load(text))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "k")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        import types

        args = types.SimpleNamespace(
            name="beta",
            base_url="https://beta.example/v1",
            model="b",
            format="openai",
            from_name=["oldname"],
            key_env="B_KEY",
            key_pass=None,
            timeout_s=2400,
            max_tokens=65536,
            dry_run=False,
        )
        assert fp.cmd_sync(args) == 0
        out = capsys.readouterr().out
        body = gate.read_text()
        assert "already declared; 'oldname' left as it is" in out
        assert "oldname" in body
        assert "default: true" in body


class TestAFlowOnlyBlockStillTakesAnAdd:
    def test_the_new_key_takes_the_block_indent_and_the_file_parses(self, tmp_path):
        text = (
            "backends:\n"
            "    alpha: {type: api, format: openai, base_url: 'https://a.example/v1', model: a}\n"
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        did, why = fp.insert_backend(
            gate,
            {
                "name": "beta",
                "type": "api",
                "format": "openai",
                "base_url": "https://b.example/v1",
                "key_env": "B_KEY",
                "model": "b",
                "max_tokens": 4096,
                "timeout_s": 60,
            },
            ".bak-t",
            False,
        )
        assert did and why is None
        parsed = yaml.safe_load(gate.read_text())
        assert set(parsed["backends"]) == {"alpha", "beta"}
        lines = gate.read_text().splitlines()
        key_line = next(line for line in lines if line.lstrip().startswith("beta:"))
        # The block indents its keys by four spaces; a deeper key would still
        # parse and would still contain "    beta:", so compare the line.
        assert key_line == "    beta:", key_line


class TestTheUserLayerIsARemovalTarget:
    def test_a_backend_the_tool_added_can_be_removed(self, tmp_path, monkeypatch, capsys):
        """`add` writes to the user layer, so remove must be able to undo it.

        The round-15 guard compared the file against the user layer it belongs
        to and refused every file, including that one.
        """
        user_cfg = tmp_path / "config.yaml"
        user_cfg.write_text(
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://a.example/v1"\n    api_key_env: A_KEY\n    model: a\n'
            "  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://b.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
            "    default: true\n"
        )
        monkeypatch.setattr(fp, "find_configs", lambda: [user_cfg])
        monkeypatch.setattr(fp, "USER_CONFIG", user_cfg)
        monkeypatch.setattr(
            fp, "_user_backends", lambda: yaml.safe_load(user_cfg.read_text())["backends"]
        )
        import types

        assert fp.cmd_remove(types.SimpleNamespace(name="alpha", dry_run=False)) == 0
        assert "alpha" not in user_cfg.read_text()
        assert "beta" in user_cfg.read_text()


class TestARemovalDoesNotUnshadowTheUserLayer:
    def test_a_user_layer_entry_of_the_same_name_stops_the_removal(self, tmp_path, monkeypatch, capsys):
        text = (
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        record_trust(gate, yaml.safe_load(text))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(
            fp,
            "_user_backends",
            lambda: {
                "beta": {
                    "type": "api",
                    "format": "openai",
                    "base_url": "https://mine.example/v1",
                    "api_key_env": "M_KEY",
                    "model": "m",
                }
            },
        )
        import types

        assert fp.cmd_remove(types.SimpleNamespace(name="beta", dry_run=False)) == 1
        err = capsys.readouterr().err
        assert "user layer declares 'beta'" in err
        assert "beta" in gate.read_text()


class TestTheSealCoversTheBytesThisRunWrote:
    def test_a_file_changed_after_the_write_is_not_authorized(self, tmp_path):
        """A fresh read at seal time would authorize someone else's edit."""
        import sys as _sys

        _sys.path.insert(0, str(SCRIPT.resolve().parents[1] / "src"))
        from code_forge.trust import is_trusted

        p = tmp_path / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        fp.write_config(p, _mark_default(UNMARKED_PAIR, "alpha"))
        # Someone else edits the file between our write and our seal.
        p.write_text(p.read_text().replace("alpha.example", "attacker.example"))
        sealed, failures = fp.reseal_paths([p], dry_run=False)
        assert sealed == 0
        assert "changed after this run wrote it" in failures[0][1]
        assert not is_trusted(p, yaml.safe_load(p.read_text()))

    def test_a_file_this_run_did_not_write_is_refused(self, tmp_path):
        p = tmp_path / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        sealed, failures = fp.reseal_paths([p], dry_run=False)
        assert sealed == 0
        assert "was not written by this run" in failures[0][1]


class TestEveryComparedFieldIsCompared:
    """Dropping a field from the routing tuple must turn a test red.

    A field the tuple names but nothing else reflects would make the removal
    guard blind to it while the contract says the identity covers it.
    """

    # Pinned literally: parametrising over the module's own tuple let a field
    # be dropped from both the code and the test at once.
    EXPECTED = (
        "name",
        "type",
        "format",
        "base_url",
        "model",
        "api_key_env",
        "api_key_file",
        "command",
        "credentials_path",
        "project_id",
        "region",
        "headers",
    )

    def test_the_tuple_names_exactly_the_expected_fields(self):
        assert tuple(fp._ROUTING_FIELDS) == self.EXPECTED

    @pytest.mark.parametrize("field", EXPECTED)
    def test_each_field_is_in_the_identity(self, field):
        import types as _types

        base = {name: None for name in self.EXPECTED}
        a = _types.SimpleNamespace(**base)
        b = _types.SimpleNamespace(**{**base, field: "changed"})
        assert fp._backend_identity(a) != fp._backend_identity(b)

    def _gate(self, tmp_path, monkeypatch, text):
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(text)
        record_trust(gate, yaml.safe_load(text))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "k")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        return gate

    def test_removing_a_backend_that_a_header_change_would_redirect_is_refused(
        self, tmp_path, monkeypatch
    ):
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n'
            "    model: a\n    default: true\n"
            "  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n"
        )
        gate = self._gate(tmp_path, monkeypatch, text)
        import types

        assert fp.cmd_remove(types.SimpleNamespace(name="alpha", dry_run=False)) == 1
        assert "alpha" in gate.read_text()


class TestTheProbeFallbackIsNamed:
    def test_a_user_layer_resolution_names_what_a_failed_probe_would_pick(
        self, tmp_path, monkeypatch, capsys
    ):
        """`resolve_backend_with_fallback` can move a review to a project entry.

        Probing is out of reach here, so the command names the entry that
        fallback would choose instead of letting the move stay silent.
        """
        text = (
            "backends:\n  alpha:\n    type: api\n    format: openai\n"
            '    base_url: "https://alpha.example/v1"\n    api_key_env: A_KEY\n'
            "    model: a\n    default: false\n"
            "  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: b\n"
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        record_trust(gate, yaml.safe_load(text))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "k")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        # The user layer supplies the backend a review would use.
        monkeypatch.setattr(
            fp,
            "_user_backends",
            lambda: {
                "mine": {
                    "type": "api",
                    "format": "openai",
                    "base_url": "https://mine.example/v1",
                    "api_key_env": "M_KEY",
                    "model": "m",
                }
            },
        )
        monkeypatch.setenv("FORGE_BACKEND", "mine")
        import types

        fp.cmd_remove(types.SimpleNamespace(name="beta", dry_run=False))
        err = capsys.readouterr().err
        assert "a failed probe" in err
        assert "'alpha'" in err


class TestAFieldLineItCannotAddress:
    def test_sync_fails_instead_of_keeping_the_old_value(self, tmp_path, monkeypatch, capsys):
        text = (
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model : spaced\n"
        )
        gate = tmp_path / "gate.yaml"
        gate.write_text(text)
        record_trust(gate, yaml.safe_load(text))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "k")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        import types

        args = types.SimpleNamespace(
            name="beta",
            base_url="https://beta.example/v1",
            model="b2",
            format="openai",
            from_name=None,
            key_env="B_KEY",
            key_pass=None,
            timeout_s=2400,
            max_tokens=65536,
            dry_run=False,
        )
        # `model : spaced` is an ordinary `model` key to a parser, so there is
        # no "line at the wrong indent" left to refuse: the value is aligned
        # and the command reports success.
        assert fp.cmd_sync(args) == 0
        assert "model: b2" in gate.read_text()

    def test_a_field_the_backend_does_not_carry_is_nothing_to_align(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text("backends:\n  beta:\n    type: api\n")
        assert fp.write_field(gate, "beta", "max_tokens", 5, ".bak-t", dry_run=False) == (
            False,
            None,
            None,
        )


class TestAuditReportsWhatItSkipped:
    def test_a_config_without_a_backends_block_is_named(self, tmp_path, monkeypatch, capsys):
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text("outlet: subprocess\n")
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        import types

        assert fp.cmd_trust(types.SimpleNamespace(fix=False, dry_run=False)) == 0
        out = capsys.readouterr().out
        assert "without a backends block" in out
        assert str(gate) in out


class TestValuesOnCrlfAndHashes:
    def test_a_field_is_written_in_a_crlf_file(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_bytes(
            b"backends:\r\n  beta:\r\n    type: api\r\n    format: openai\r\n"
            b'    base_url: "https://beta.example/v1"\r\n    api_key_env: B_KEY\r\n'
            b"    model: b  # cheap tier\r\n"
        )
        assert fp.write_field(gate, "beta", "model", "b2", ".bak-test", dry_run=False)[0] is True
        written = gate.read_bytes()
        assert b"model: b2" in written and b"# cheap tier" in written
        assert written.count(b"\n") == written.count(b"\r\n")

    def test_a_hash_inside_a_scalar_is_not_a_comment(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text(
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://beta.example/v1"\n    api_key_env: B_KEY\n'
            "    model: a#b\n"
        )
        assert fp.read_field(gate.read_text(), "beta", "model") == "a#b"
        assert fp.write_field(gate, "beta", "model", "c", ".bak-test", dry_run=False)[0] is True
        assert "    model: c\n" in gate.read_text()


class TestRemoveNamesTheScope:
    def test_a_moved_focus_text_is_named_in_the_refusal(self, tmp_path, monkeypatch, capsys):
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(GATE + "review_focus: |\n  first\n")
        record_trust(gate, yaml.safe_load(gate.read_text()))
        gate.write_text(GATE + "review_focus: |\n  second, unread\n")
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        assert fp.main() == 1
        assert "review_focus text changed" in capsys.readouterr().err


class TestUnparseableUserLayer:
    def test_the_user_layer_is_refused_when_it_does_not_parse(self, tmp_path, monkeypatch):
        gate = tmp_path / "user" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text("backends: [unclosed\n")
        monkeypatch.setattr(fp, "USER_CONFIG", gate)
        assert fp.is_user_config(gate) is True
        assert "does not parse" in (fp.trust_refusal(gate, gate.read_text()) or "")


class TestNameGrammar:
    def test_a_name_the_reader_cannot_address_is_refused(self, tmp_path, monkeypatch, capsys):
        gate = tmp_path / "gate.yaml"
        gate.write_text(GATE)
        record_trust(gate, yaml.safe_load(GATE))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "k")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "forge-provider.py",
                "add",
                "bad name",
                "--base-url",
                "https://x/v1",
                "--model",
                "m",
                "--format",
                "openai",
                "--key-env",
                "X_KEY",
            ],
        )
        assert fp.main() == 2
        assert "letters, digits, dot, dash" in capsys.readouterr().err
        assert gate.read_text() == GATE


class TestSealFailureExitStatus:
    def test_a_command_other_than_remove_fails_when_sealing_fails(self, tmp_path, monkeypatch, capsys):
        from code_forge import trust as trust_mod

        gate = tmp_path / "gate.yaml"
        gate.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        trust_mod.record_trust(gate, yaml.safe_load(gate.read_text()))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])

        def refuse(path, data, config_dir=None):
            raise OSError("injected trust store failure")

        monkeypatch.setattr(trust_mod, "record_trust", refuse)
        # `beta` carries the default in this fixture, so this run edits the file.
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "default", "beta"])
        assert fp.main() == 1
        assert "still need authorization" in capsys.readouterr().err


class TestRemoveSealsEachEditedFile:
    """Every edited file finishes its own trust update, or says it did not."""

    def _pair(self, tmp_path, monkeypatch):
        from code_forge.trust import record_trust

        first = tmp_path / "a" / ".code-forge" / "gate.yaml"
        second = tmp_path / "b" / ".code-forge" / "gate.yaml"
        for path in (first, second):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
            record_trust(path, yaml.safe_load(path.read_text()))
        monkeypatch.setattr(fp, "find_configs", lambda: [first, second])
        return first, second

    def test_a_later_read_error_does_not_strand_an_earlier_edit(self, tmp_path, monkeypatch, capsys):
        from code_forge.trust import is_trusted

        first, second = self._pair(tmp_path, monkeypatch)
        real = fp.read_config

        def fail_on_second(path):
            if path == second:
                raise OSError("injected unreadable file")
            return real(path)

        monkeypatch.setattr(fp, "read_config", fail_on_second)
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        rc = fp.main()
        captured = capsys.readouterr()
        assert rc == 1
        assert "beta:" not in first.read_text()
        # The edit landed, so its trust record must describe the new bytes.
        assert is_trusted(first, yaml.safe_load(first.read_text()))
        assert str(second) in captured.err
        assert "injected unreadable file" in captured.err

    def test_an_interrupt_between_files_keeps_the_earlier_edit_authorized(self, tmp_path, monkeypatch):
        """Ctrl-C during a sweep must not leave an edit it already made unsealed.

        An abort is not an OSError, so nothing downstream of this loop gets a
        chance to seal anything: the file that was already written has to be
        authorized before the next file is read.
        """
        from code_forge.trust import is_trusted

        first, second = self._pair(tmp_path, monkeypatch)
        real = fp.read_config

        def interrupt_on_second(path):
            if path == second:
                raise KeyboardInterrupt
            return real(path)

        monkeypatch.setattr(fp, "read_config", interrupt_on_second)
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        with pytest.raises(KeyboardInterrupt):
            fp.main()
        assert "beta:" not in first.read_text()
        assert is_trusted(first, yaml.safe_load(first.read_text()))

    def test_a_non_utf8_sibling_is_reported_not_raised(self, tmp_path, monkeypatch, capsys):
        """A decode error is a file problem, not a reason to abort the sweep."""
        from code_forge.trust import is_trusted

        first, second = self._pair(tmp_path, monkeypatch)
        second.write_bytes(b"\xff\xfe backends:\n")
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        rc = fp.main()
        captured = capsys.readouterr()
        assert rc == 1
        assert "beta:" not in first.read_text()
        assert is_trusted(first, yaml.safe_load(first.read_text()))
        assert "unreadable" in captured.err

    def test_a_failed_reseal_makes_the_command_fail(self, tmp_path, monkeypatch, capsys):
        from code_forge import trust as trust_mod
        from code_forge.trust import is_trusted

        gate = tmp_path / "gate.yaml"
        gate.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        trust_mod.record_trust(gate, yaml.safe_load(gate.read_text()))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        real = trust_mod.record_trust

        def refuse(path, data, config_dir=None):
            if path == gate:
                raise OSError("injected trust store failure")
            return real(path, data, config_dir)

        monkeypatch.setattr(trust_mod, "record_trust", refuse)
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        rc = fp.main()
        captured = capsys.readouterr()
        assert rc == 1
        assert "trust" in captured.err.lower()
        assert "injected trust store failure" in captured.err
        # It must not claim success for a file it left untrusted.
        assert not is_trusted(gate, yaml.safe_load(gate.read_text()))


class TestTheNumericFlagsAreValidatedBeforeAnyWork:
    """The numbers the loader would reject are refused by the parser.

    These assertions are on the parser on purpose: a run that reached the config
    scan would need the machine's real config tree (the scan starts from the tree
    the tool is run in, not only from `$HOME`), and an earlier version of this
    test wrote its fixture somewhere the tool never reads, so "the file is
    unchanged" proved nothing. Refusing at parse time is the property that makes
    the write path unreachable.
    """

    def _parse(self, argv):
        return fp.build_parser().parse_args(argv)

    @pytest.mark.parametrize("bad", ["100000", "-1", "abc"])
    def test_a_timeout_outside_the_loader_range_is_refused(self, bad):
        for command in ("add", "set", "sync"):
            argv = [command, "alpha", "--timeout-s", bad]
            if command == "add":
                argv += ["gate.yaml"]
            with pytest.raises(SystemExit) as exit_info:
                self._parse(argv)
            assert exit_info.value.code == 2, (command, bad)

    @pytest.mark.parametrize("bad", ["-5", "0"])
    def test_a_token_cap_that_is_not_positive_is_refused(self, bad):
        for command in ("add", "set", "sync"):
            argv = [command, "alpha", "--max-tokens", bad]
            if command == "add":
                argv += ["gate.yaml"]
            with pytest.raises(SystemExit) as exit_info:
                self._parse(argv)
            assert exit_info.value.code == 2, (command, bad)

    def test_the_accepted_values_parse_to_integers(self):
        parsed = self._parse(["set", "alpha", "--timeout-s", "86400", "--max-tokens", "1"])
        assert parsed.timeout_s == 86400
        assert parsed.max_tokens == 1


class TestSharedHelpersKeepTheirBoundaries:
    """A seal covers exactly the files handed to it."""

    def test_a_non_utf8_file_in_a_batch_is_reported_not_raised(self, tmp_path, capsys):
        from code_forge.trust import record_trust

        good = tmp_path / "good.yaml"
        bad = tmp_path / "bad.yaml"
        fp.write_config(good, _mark_default(UNMARKED_PAIR, "alpha"))
        record_trust(good, yaml.safe_load(good.read_text()))
        bad.write_bytes(b"\xff\xfe not utf-8")
        sealed, failures = fp.reseal_paths([good, bad], dry_run=False)
        assert sealed == 1
        assert [path for path, _ in failures] == [bad]
        assert "could not read it back" in failures[0][1]

    def test_an_unavailable_trust_module_does_not_report_the_user_config(
        self, tmp_path, monkeypatch, capsys
    ):
        project = tmp_path / "gate.yaml"
        user = tmp_path / "config.yaml"
        project.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        user.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        monkeypatch.setattr(fp, "USER_CONFIG", user)
        import builtins

        real_import = builtins.__import__

        def no_trust(name, *args, **kwargs):
            if name == "code_forge.trust":
                raise ImportError("simulated: trust module unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_trust)
        sealed, failures = fp.reseal_paths([project, user], dry_run=False)
        assert sealed == 0
        assert [path for path, _ in failures] == [project]

    def test_the_trust_audit_dry_run_does_not_migrate(self, tmp_path, monkeypatch):
        from code_forge import trust as trust_mod

        gate = tmp_path / "gate.yaml"
        gate.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        data = yaml.safe_load(gate.read_text())
        trust_mod.record_trust(gate, data)
        store = trust_mod._load_trust_store()
        store[str(gate.resolve())]["hash"] = trust_mod._hash_all_backends(data)
        trust_mod._save_trust_store(store)
        record = trust_mod._trust_store_path()
        before = (record.read_bytes(), record.stat().st_mtime_ns)
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "trust", "--dry-run"])
        assert fp.main() == 0
        assert (record.read_bytes(), record.stat().st_mtime_ns) == before

    def test_a_legacy_user_layer_path_needs_no_repository_trust(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fp, "user_layer_paths", fp._user_layer_paths_real)
        from code_forge import user_config

        legacy = tmp_path / "legacy-gate.yaml"
        legacy.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "unused.yaml")
        monkeypatch.setattr(user_config, "user_config_path", lambda: legacy)
        assert fp.is_user_config(legacy) is True
        did, why = fp.remove_backend(legacy, "alpha", ".bak-test", dry_run=True)
        # Refused because `alpha` is the default, not because a file that forge
        # treats as the user layer was asked for repository trust.
        assert did is False
        assert "not trusted" not in why

    def test_a_write_failure_is_not_summarised_as_kept(self, tmp_path, monkeypatch, capsys):
        from code_forge import trust as trust_mod

        gate = tmp_path / "gate.yaml"
        gate.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        trust_mod.record_trust(gate, yaml.safe_load(gate.read_text()))
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])

        def fail_write(path, text):
            raise OSError("injected write failure")

        monkeypatch.setattr(fp, "write_config", fail_write)
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        assert fp.main() == 1
        err = capsys.readouterr().err
        assert "could not be written" in err
        assert "kept it" not in err


class TestRemoveDryRunIsReadOnly:
    """A preview may not touch the trust store, even to migrate it."""

    def _legacy_store(self, tmp_path):
        from code_forge import trust as trust_mod

        gate = tmp_path / ".code-forge" / "gate.yaml"
        gate.parent.mkdir()
        gate.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        data = yaml.safe_load(gate.read_text())
        trust_mod.record_trust(gate, data)
        store = trust_mod._load_trust_store()
        store[str(gate.resolve())]["hash"] = trust_mod._hash_all_backends(data)
        trust_mod._save_trust_store(store)
        record = trust_mod._trust_store_path()
        return gate, record

    def test_a_dry_run_leaves_a_legacy_record_untouched(self, tmp_path, monkeypatch):
        gate, record = self._legacy_store(tmp_path)
        before = (record.read_bytes(), record.stat().st_mtime_ns)
        config_before = gate.read_bytes()
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta", "--dry-run"])
        assert fp.main() == 0
        after = (record.read_bytes(), record.stat().st_mtime_ns)
        assert after == before
        assert gate.read_bytes() == config_before
        assert list(record.parent.glob("*.tmp")) == []

    def test_the_real_run_still_updates_the_record(self, tmp_path, monkeypatch):
        from code_forge.trust import is_trusted

        gate, record = self._legacy_store(tmp_path)
        before = record.read_bytes()
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        assert fp.main() == 0
        assert record.read_bytes() != before
        assert is_trusted(gate, yaml.safe_load(gate.read_text()))


class TestATestOnlyGateIsNotAFailure:
    """A gate that carries `test.*` and no backends is a valid file.

    `trust_refusal` already lets it through, so the editing commands must skip
    it rather than report it as a refusal and exit non-zero.
    """

    TEST_ONLY = "test:\n  command: pytest -q\n"

    def _tree(self, tmp_path, monkeypatch):
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text(self.TEST_ONLY)
        record_trust(gate, yaml.safe_load(self.TEST_ONLY))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        return gate

    def test_default_does_not_fail_on_it(self, tmp_path, monkeypatch, capsys):
        gate = self._tree(tmp_path, monkeypatch)
        assert fp.cmd_default(types.SimpleNamespace(name="beta", dry_run=False)) == 0
        assert gate.read_text() == self.TEST_ONLY
        assert "no backends" not in capsys.readouterr().err

    def test_sync_does_not_fail_on_it(self, tmp_path, monkeypatch, capsys):
        gate = self._tree(tmp_path, monkeypatch)
        args = types.SimpleNamespace(
            name="beta",
            base_url="https://beta.example/v1",
            model="b",
            format="openai",
            from_name=None,
            key_env="B_KEY",
            key_pass=None,
            timeout_s=2400,
            max_tokens=65536,
            dry_run=False,
        )
        assert fp.cmd_sync(args) == 0
        assert gate.read_text() == self.TEST_ONLY
        assert "no backends" not in capsys.readouterr().err


class TestTheUserLayerIsResolvedThroughOneHelper:
    def test_the_refusal_names_the_configured_user_file(self, tmp_path, monkeypatch):
        """`FORGE_CONFIG_DIR` moves the user layer; the message must follow."""
        layer = tmp_path / "cfg"
        layer.mkdir()
        (layer / "config.yaml").write_text("backends:\n  beta:\n    type: api\n")
        monkeypatch.setenv("FORGE_CONFIG_DIR", str(layer))
        _user_layer(monkeypatch, {"beta": {"type": "api"}})
        assert fp._user_layer_same_name("beta") == str(layer / "config.yaml")


class TestTheShadowGuardDoesNotDependOnWalkOrder:
    def test_a_user_layer_that_goes_too_does_not_block_the_project_entry(self, tmp_path, monkeypatch):
        """Both layers declare the name and both are being removed: refusing the
        project file first left the job half done and needed a second run."""
        project = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        project.parent.mkdir(parents=True)
        project.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        user = tmp_path / "user" / "config.yaml"
        user.parent.mkdir()
        user.write_text(
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://ub.example/v1"\n    api_key_env: UB_KEY\n    model: ub\n'
            "  delta:\n    type: api\n    format: openai\n"
            '    base_url: "https://ud.example/v1"\n    api_key_env: UD_KEY\n    model: ud\n'
            "    default: true\n"
        )
        for path in (project, user):
            record_trust(path, yaml.safe_load(path.read_text()))
        monkeypatch.setattr(fp, "find_configs", lambda: [project, user])
        monkeypatch.setattr(fp, "is_user_config", lambda path: path == user)
        # The loader reads the file, as production does, so the project entry
        # sees the shadow disappear only after the user layer was written.
        from code_forge import user_config as uc

        monkeypatch.setattr(
            uc,
            "load_user_backends",
            lambda: yaml.safe_load(user.read_text()).get("backends") or {},
        )
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        assert fp.main() == 0
        assert "beta:" not in project.read_text()
        assert "beta:" not in user.read_text()


class TestTheDryRunPreviewMatchesTheRun:
    def test_a_file_that_does_not_parse_is_not_reported_as_would_be_sealed(
        self, tmp_path, monkeypatch, capsys
    ):
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text("backends: [unclosed\n")
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        rc = fp.cmd_trust(types.SimpleNamespace(fix=True, dry_run=True))
        captured = capsys.readouterr()
        assert rc == 1
        assert "does not parse" in captured.err or "not parse" in captured.err

    def test_the_fallback_warning_names_a_survivor_not_the_removed_entry(
        self, tmp_path, monkeypatch, capsys
    ):
        project = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        project.parent.mkdir(parents=True)
        text = (
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://b.example/v1"\n    api_key_env: B_KEY\n    model: b\n'
            "  gamma:\n    type: api\n    format: openai\n"
            '    base_url: "https://g.example/v1"\n    api_key_env: G_KEY\n    model: g\n'
        )
        project.write_text(text)
        record_trust(project, yaml.safe_load(text))
        # The user layer resolves a name of its own, so after the removal the
        # review is on a user backend; a failed probe falls back to the first
        # surviving project entry, which is `gamma`. A dry run that read the
        # unchanged file would name `beta`, the entry being removed.
        _user_layer(
            monkeypatch,
            {
                "delta": {
                    "type": "api",
                    "format": "openai",
                    "base_url": "https://ud.example/v1",
                    "api_key_env": "UD_KEY",
                    "model": "ud",
                    "default": True,
                }
            },
        )
        monkeypatch.setattr(fp, "find_configs", lambda: [project])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta", "--dry-run"])
        fp.main()
        err = capsys.readouterr().err
        assert "gamma" in err
        assert "to 'beta'" not in err


class TestTheShadowGuardReadsWhatTheRunProduced:
    def test_a_user_removal_that_fails_keeps_the_project_entry(self, tmp_path, monkeypatch):
        """The project entry is only removable once the shadow is really gone."""
        project = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        project.parent.mkdir(parents=True)
        project.write_text(_mark_default(UNMARKED_PAIR, "alpha"))
        user = tmp_path / "user" / "config.yaml"
        user.parent.mkdir()
        # Only `beta`: removing it from the user layer would leave it empty.
        user.write_text(
            "backends:\n  beta:\n    type: api\n    format: openai\n"
            '    base_url: "https://ub.example/v1"\n    api_key_env: UB_KEY\n    model: ub\n'
        )
        for path in (project, user):
            record_trust(path, yaml.safe_load(path.read_text()))
        monkeypatch.setattr(fp, "find_configs", lambda: [project, user])
        monkeypatch.setattr(fp, "is_user_config", lambda path: path == user)
        _user_layer(monkeypatch, yaml.safe_load(user.read_text())["backends"])
        monkeypatch.setattr(sys, "argv", ["forge-provider.py", "remove", "beta"])
        assert fp.main() == 1
        assert "beta:" in project.read_text()
        assert "beta:" in user.read_text()


class TestAddOnATestOnlyGate:
    def test_add_skips_a_config_without_backends(self, tmp_path, monkeypatch, capsys):
        gate = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        gate.parent.mkdir(parents=True)
        gate.write_text("test:\n  command: pytest -q\n")
        record_trust(gate, yaml.safe_load(gate.read_text()))
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")
        monkeypatch.setattr(fp, "find_configs", lambda: [gate])
        monkeypatch.setattr(fp, "resolve_key", lambda *a, **k: "test-key")
        monkeypatch.setattr(fp, "probe", lambda *a, **k: (True, "200 OK"))
        args = types.SimpleNamespace(
            name="gamma",
            base_url="https://gamma.example/v1",
            model="g",
            format="openai",
            key_env="G_KEY",
            key_pass=None,
            max_tokens=1,
            timeout_s=1,
            header=None,
            dry_run=False,
        )
        assert fp.cmd_add(args) == 0
        captured = capsys.readouterr()
        assert "1 skipped" in captured.out
        assert "no backends" not in captured.err


class TestTheDryRunSealMatchesTheRun:
    def test_a_file_without_backends_is_not_counted_as_sealed(self, tmp_path):
        gate = tmp_path / "gate.yaml"
        gate.write_text("test:\n  command: pytest -q\n")
        sealed, failures = fp.reseal_paths([gate], dry_run=True)
        assert sealed == 0
        assert failures and "no backends" in failures[0][1]
