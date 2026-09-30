# SPDX-License-Identifier: Apache-2.0
"""Semgrep taint rule validation and annotation tests.

This file serves two purposes:
1. Annotated Python snippets for `semgrep --test` (ruleid/ok annotations).
2. Pytest wrappers that validate YAML structure and optionally run semgrep.

Annotation functions use example_* prefix (NOT test_*) to prevent pytest
from collecting and executing them -- they contain undefined variables
like `f` and would crash if run as tests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

# Semgrep annotation placeholder: `f` is a file-like object used in
# example_* snippets below. semgrep --test parses but never executes these
# functions; defining f = None here satisfies ruff without affecting semgrep.
f = None  # type: ignore[assignment]


# -------------------------------------------------------------------
# Annotated snippets for semgrep --test (positive: ruleid, negative: ok)
# -------------------------------------------------------------------


def example_env_to_subprocess():
    val = os.environ["SECRET"]
    # ruleid: forge-taint-config-to-subprocess
    subprocess.run(val, shell=True)


def example_yaml_to_system():
    data = yaml.safe_load(f)
    # ruleid: forge-taint-config-to-subprocess
    os.system(data["cmd"])


def example_json_to_popen():
    cfg = json.load(f)
    # ruleid: forge-taint-config-to-subprocess
    os.popen(cfg["script"])


def example_getenv_to_call():
    cmd = os.getenv("CMD")
    # ruleid: forge-taint-config-to-subprocess
    subprocess.call(cmd, shell=True)


def example_env_to_urlopen():
    url = os.environ["URL"]
    # ruleid: forge-taint-config-to-network
    urllib.request.urlopen(url)


def example_yaml_to_requests_get():
    cfg = yaml.safe_load(f)
    # ruleid: forge-taint-config-to-network
    requests.get(cfg["endpoint"])


def example_hardcoded_safe():
    # ok: forge-taint-config-to-subprocess
    subprocess.run(["ls", "-la"])


def example_literal_system():
    # ok: forge-taint-config-to-subprocess
    os.system("echo hello")


def example_hardcoded_url():
    # ok: forge-taint-config-to-network
    requests.get("https://example.com")


# -------------------------------------------------------------------
# Pytest wrappers: validate YAML structure (always) + semgrep (if present)
# -------------------------------------------------------------------

_RULES_PATH = (
    Path(__file__).resolve().parent.parent / "src" / "code_forge" / "rules" / "forge-taint.yaml"
)


def _load_rules():
    """Load and return the rules list from forge-taint.yaml."""
    import yaml as _yaml

    with open(_RULES_PATH) as fh:
        data = _yaml.safe_load(fh)
    return data


def test_forge_taint_yaml_valid_syntax():
    """YAML parses without error and has a 'rules' key."""
    data = _load_rules()
    assert "rules" in data
    assert len(data["rules"]) >= 2


def test_forge_taint_rule_ids():
    """Rule IDs match expected values."""
    data = _load_rules()
    ids = {r["id"] for r in data["rules"]}
    assert "forge-taint-config-to-subprocess" in ids
    assert "forge-taint-config-to-network" in ids


def test_forge_taint_yaml_mode():
    """Each rule uses mode: taint."""
    data = _load_rules()
    for rule in data["rules"]:
        assert rule["mode"] == "taint", "Rule %s has mode=%s, expected taint" % (
            rule["id"],
            rule["mode"],
        )


def test_forge_taint_yaml_severity():
    """Each rule has severity WARNING."""
    data = _load_rules()
    for rule in data["rules"]:
        assert rule["severity"] == "WARNING"


def test_forge_taint_open_not_sink():
    """open() appears only as source, never as sink.

    open() as both source and sink creates a self-loop (false positives);
    open-as-sink is deferred. Uses regex word-boundary to avoid false-matching
    subprocess.Popen.
    """
    import re

    data = _load_rules()
    for rule in data["rules"]:
        for sink in rule.get("pattern-sinks", []):
            pat = sink.get("pattern", "")
            # Match standalone open( but not Popen( or other *open(
            assert not re.search(r"(?<![A-Za-z])open\(", pat), (
                "Rule %s has open() as sink (self-loop risk)" % rule["id"]
            )


def test_forge_taint_focus_metavariable():
    """All sinks use focus-metavariable for precise argument matching."""
    data = _load_rules()
    for rule in data["rules"]:
        for sink in rule.get("pattern-sinks", []):
            assert "focus-metavariable" in sink, "Sink '%s' in rule %s missing focus-metavariable" % (
                sink.get("pattern", "?"),
                rule["id"],
            )


@pytest.mark.skipif(
    shutil.which("semgrep") is None,
    reason="semgrep not installed",
)
def test_semgrep_validate(tmp_path):
    """The local rule runs on a clean file without contacting a registry.

    ``semgrep --validate`` fetches ``p/semgrep-rule-lints`` from semgrep.dev
    before it reads the file, so a reset connection fails the run even
    though the rule never changed. ``scan --error`` reads only the config
    it was given and returns non-zero when the rule matches, so a clean
    file must come back zero. The outage test below keeps the
    ``--validate`` failure pinned.
    """
    target = tmp_path / "clean.py"
    target.write_text("x = 1\n")
    result = subprocess.run(
        ["semgrep", "scan", "--metrics=off", "--error", "--config", str(_RULES_PATH), "--quiet", str(target)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, "semgrep scan failed: %s" % result.stderr


@pytest.mark.skipif(
    shutil.which("semgrep") is None,
    reason="semgrep not installed",
)
def test_semgrep_validate_fails_when_the_registry_is_unreachable():
    """``--validate`` fetches a rule pack from semgrep.dev before it reads
    the local file. A proxy that refuses every tunnel must make the
    command fail, so a later change cannot swallow the outage.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class _Refuse(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_CONNECT(self):
            host, _sep, _port = self.path.partition(":")
            self.close_connection = True
            if host == "semgrep.dev":
                self.wfile.write(b"HTTP/1.1 502 semgrep.dev refused\r\n\r\n")
                return
            self.wfile.write(b"HTTP/1.1 502 not forwarded\r\n\r\n")

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Refuse)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = dict(os.environ)
    env["https_proxy"] = "http://127.0.0.1:%d" % port
    env["http_proxy"] = env["https_proxy"]
    env["HTTPS_PROXY"] = env["https_proxy"]
    env["HTTP_PROXY"] = env["https_proxy"]
    try:
        result = subprocess.run(
            ["semgrep", "--validate", "--config", str(_RULES_PATH)],
            capture_output=True,
            text=True,
            timeout=150,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode != 0


@pytest.mark.skipif(
    shutil.which("semgrep") is None,
    reason="semgrep not installed",
)
def test_semgrep_test_annotations():
    """semgrep --test passes with ruleid/ok annotations in this file."""
    result = subprocess.run(
        [
            "semgrep",
            "--test",
            "--config",
            str(_RULES_PATH),
            str(Path(__file__).resolve()),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, "semgrep --test failed: %s\n%s" % (result.stderr, result.stdout)
