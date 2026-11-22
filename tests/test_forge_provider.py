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


class TestBlockBoundaries:
    def test_lists_only_top_level_backends(self, gate):
        assert fp.list_backends(fp.read_config(gate)) == ["alpha", "beta"]

    def test_nested_key_is_not_a_backend(self, gate):
        """`headers:` has a backend key's shape and must not be treated as one.

        Renaming it would rewrite a sub-block belonging to beta and leave any
        real backend of that name untouched.
        """
        assert fp.find_backend(fp.read_config(gate), "headers") is None

    def test_block_ends_at_the_next_sibling(self, gate):
        """A blank line between backends must not merge them.

        alpha's body once ran to the end of the file, so beta's fields --
        including its default flag -- read as alpha's.
        """
        text = fp.read_config(gate)
        lo, hi = fp.find_backend(text, "alpha")
        body = text[lo:hi]
        assert "alpha-model" in body
        assert "beta-model" not in body

    def test_read_field_of_the_named_backend(self, gate):
        text = fp.read_config(gate)
        assert fp.read_field(text, "beta", "model") == "beta-model"
        assert fp.read_field(text, "alpha", "model") == "alpha-model"

    def test_tab_indented_file_agrees_with_itself(self, tmp_path):
        """list_backends and find_backend must read the same file the same way.

        One matched literal spaces and the other any whitespace, so a
        tab-indented config reported no backends while still being editable
        -- `list` showed nothing and `set` changed it anyway.
        """
        p = tmp_path / "g.yaml"
        p.write_text("backends:\n\tb:\n\t\tmodel: old\n")
        text = fp.read_config(p)
        assert fp.list_backends(text) == ["b"]
        assert fp.find_backend(text, "b") is not None

    def test_a_flush_left_comment_does_not_end_the_block(self, tmp_path):
        """Grouping comments are common in a hand-maintained gate.yaml.

        The block once ended at the first unindented character, so a comment
        written flush left between two backends hid every backend below it --
        `list` showed a subset and `add` wrote a duplicate of what it could
        not see.
        """
        p = tmp_path / "g.yaml"
        p.write_text(
            "backends:\n  a:\n    model: m1\n# --- slower models below ---\n  b:\n    model: m2\n"
        )
        text = fp.read_config(p)
        assert fp.list_backends(text) == ["a", "b"]
        assert fp.find_backend(text, "b") is not None


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
        changed, old = fp.write_field(gate, "alpha", "model", "new-model", ".bak-t", dry_run=False)
        assert (changed, old) == (True, "alpha-model")
        assert fp.read_field(fp.read_config(gate), "alpha", "model") == "new-model"

    def test_same_value_is_not_a_change(self, gate):
        """Numeric fields compare against a string read from the file.

        Without the coercion every `set --timeout-s` rewrote, re-backed-up
        and re-sealed all thirty configs while changing nothing.
        """
        changed, _ = fp.write_field(gate, "alpha", "timeout_s", 2400, ".bak-t", dry_run=False)
        assert changed is False

    def test_value_containing_a_quote_is_still_matched(self, tmp_path):
        p = tmp_path / "g.yaml"
        p.write_text('backends:\n  b:\n    model: say "hi" now\n')
        changed, old = fp.write_field(p, "b", "model", "plain", ".bak-t", dry_run=False)
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
        assert fp.find_backend(text, "b") is not None
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
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "nonexistent.yaml")
        found = fp.find_configs()
        assert real in found
        assert junk not in found

    def test_no_duplicates(self, monkeypatch, tmp_path):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
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

        fp.reseal_trust([other], dry_run=False)
        assert not is_trusted(victim, yaml.safe_load(victim.read_text()))

    def test_dry_run_seals_nothing(self, tmp_path, monkeypatch):
        sys.path.insert(0, str(SCRIPT.resolve().parents[1] / "src"))
        from code_forge.trust import is_trusted

        p = tmp_path / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        fp.reseal_trust([p], dry_run=True)
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
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")

        assert fp.cmd_rename(self._args("alpha", "renamed")) == 0
        data = yaml.safe_load(fp.read_config(p))["backends"]
        assert "renamed" in data
        assert "alpha" not in data
        assert data["renamed"]["default"] is True

    def test_a_backslash_in_the_new_name_is_not_a_group_reference(self, tmp_path, monkeypatch):
        """The replacement went through re.sub as a template string.

        A name containing a backslash was read as a group reference, so
        `rename a 'x\\1y'` wrote the captured indentation into the key.
        """
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")

        fp.cmd_rename(self._args("alpha", "a\\1b"))
        assert "a\\1b:" in fp.read_config(p)

    def test_refuses_when_the_target_name_exists(self, tmp_path, monkeypatch):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
        monkeypatch.setattr(fp, "SEARCH_ROOT", tmp_path)
        monkeypatch.setattr(fp, "USER_CONFIG", tmp_path / "none.yaml")

        before = fp.read_config(p)
        assert fp.cmd_rename(self._args("alpha", "beta")) == 1
        assert fp.read_config(p) == before

    def test_dry_run_changes_nothing(self, tmp_path, monkeypatch):
        p = tmp_path / "code" / "repo" / ".code-forge" / "gate.yaml"
        p.parent.mkdir(parents=True)
        p.write_text(GATE)
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
        rc = fp.cmd_sync(self._args("newname", from_name=["oldname"]))
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
        fp.cmd_sync(self._args("newname", from_name=["oldname"]))
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
        fp.cmd_sync(self._args("newname", from_name=["oldname"]))
        after_first = {p: fp.read_config(p) for p in fp.find_configs()}
        capsys.readouterr()
        fp.cmd_sync(self._args("newname", from_name=["oldname"]))
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
        rc = fp.cmd_sync(self._args("newname", from_name=["oldname"]))
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
        rc = fp.cmd_sync(self._args("newname", from_name=["oldname"]))
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
        assert did is False and "YAML" in why
        assert p.read_bytes() == before
        assert not p.with_name("gate.yaml.bak-test").exists()

    def test_nested_default_does_not_make_a_backend_the_default(self, gate):
        original = gate.read_text().replace(
            "      User-Agent:", "      default: true\n      User-Agent:"
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
            self.sealed.append((list(edited), dry_run))
            return len(edited)

        monkeypatch.setattr(fp, "reseal_trust", record)
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
        # Both edited files are re-sealed, in discovery order: a command that stopped
        # after the first eligible config would leave the second pointing at a name
        # that no longer resolves, with nothing on screen to say so.
        assert self.sealed == [([first, second], False)]

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
        assert "trust was not updated for 1 repository config file(s) changed by remove" in err

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
        "    model: first-model\n"
        "  middle:\n"
        "    type: api\n"
        "    format: openai\n"
        '    base_url: "https://middle.example/v1"\n'
        "    model: middle-model\n"
        "  last:\n"
        "    type: api\n"
        "    format: openai\n"
        '    base_url: "https://last.example/v1"\n'
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
        "    model: keeper-model\n"
        "    # ghost used to live here; see the review log\n"
        "  ghost:\n"
        "    type: api\n"
        "    format: openai\n"
        '    base_url: "https://ghost.example/v1"\n'
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

    FIXTURE = TestRemoveMiddleBlock.THREE

    def test_header_and_following_blocks_survive(self, tmp_path):
        p = tmp_path / "gate.yaml"
        p.write_text(self.FIXTURE)
        did, why = fp.remove_backend(p, "first", ".bak-test", dry_run=False)
        assert did and why is None
        body = p.read_text()
        assert body.startswith("backends:\n  middle:")
        expected = "backends:\n  middle:" + self.FIXTURE.split("  middle:", 1)[1]
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
        "    model: keep-model\n"
        "  drop:\n"
        "    type: api\n"
        "    format: openai\n"
        '    base_url: "https://drop.example/v1"\n'
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
            "    model: keeper-model\n"
            "    default: true\n"
            "  spare:\n"
            "    type: api\n"
            "    format: openai\n"
            '    base_url: "https://spare.example/v1"\n'
            "    model: spare-model\n"
            "    default: false\n"
        )
        did, why = fp.remove_backend(p, "spare", ".bak-test", dry_run=False)
        assert did and why is None
        body = p.read_text()
        assert "spare:" not in body
        assert "keeper:" in body
