"""No network: exercise Forge through urllib and intercept only physical sends."""

from contextlib import contextmanager
import http.client
import io
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from code_forge.backend import BackendConfig
from code_forge.disposition import Disposition
from code_forge.factories import build_l1_provider
from code_forge.falsify_real import RealFalsifier
from code_forge.lenses import LENS_BLOCK
from code_forge.state import StateFinding
from tests import test_lens_prompts as fixtures
from transport_cap import SendRefused, capped_api


@contextmanager
def fake_backend(payload, *, proxy=False):
    sent = []
    encoded = json.dumps(
        {
            "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        }
    ).encode()

    class FakeSocket:
        def makefile(self, *_args):
            return io.BytesIO(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(encoded)).encode()
                + b"\r\nContent-Type: application/json\r\n\r\n"
                + encoded
            )

        def close(self):
            pass

    def fake_send(connection, method, target, body, headers, **_kwargs):
        assert method == "POST" and target == "/v1/chat/completions"
        assert (connection.host, connection.port) == (
            ("proxy.invalid", 8080) if proxy else ("fixture.invalid", 443)
        )
        if proxy:
            assert (connection._tunnel_host, connection._tunnel_port) == ("fixture.invalid", 443)
        body = json.loads(body)
        sent.append(body["messages"][0]["content"])
        connection.sock = FakeSocket()

    def fake_response(connection):
        response = http.client.HTTPResponse(connection.sock)
        response.begin()
        return response

    with (
        patch.object(http.client.HTTPConnection, "request", fake_send),
        patch.object(http.client.HTTPSConnection, "getresponse", fake_response),
        patch("urllib.request._opener", None),
        patch(
            "urllib.request.getproxies",
            return_value={"https": "http://proxy.invalid:8080"} if proxy else {},
        ),
        patch("urllib.request.proxy_bypass", return_value=False),
    ):
        yield sent


def test_real_factory_transport_and_falsifier_boundary(monkeypatch):
    monkeypatch.setenv("FORGE_L1_SERIAL", "1")
    monkeypatch.setenv("FORGE_AB_FIXTURE_KEY", "mock-not-a-secret")
    backend = BackendConfig(
        name="fixture",
        type="api",
        format="openai",
        model="fixture",
        base_url="https://fixture.invalid/v1",
        api_key_env="FORGE_AB_FIXTURE_KEY",
    )
    findings = [
        {
            "file": "f.py",
            "line": 2,
            "severity": "P2",
            "description": "The arithmetic change alters the return value.",
        }
    ]
    payload = {
        "findings": findings,
        "code_excerpts": [
            {
                "file": "f.py",
                "start_line": 2,
                "end_line": 3,
                "content": "    y = x + 1\n    return y",
            }
        ],
    }
    with fake_backend(payload) as sent, capped_api(backend) as cap:
        for arm in ("A", "B"):
            monkeypatch.setenv("FORGE_EXPERIMENT_537_ARM", arm)
            result = build_l1_provider(
                "auto",
                fixtures._resolved(),
                backend=backend,
                post_image=fixtures.POST_IMAGE,
                review_lenses=True,
                max_attempts=1,
            )()
            assert any("arithmetic change" in f.description for f in result[0])
            finding = StateFinding(
                "example",
                "fixture",
                "L1",
                Disposition.CONFIRMED,
                "f.py",
                [2, 2],
                findings[0]["description"],
            )
            with pytest.raises(SendRefused):
                RealFalsifier(backend, fixtures.DIFF).falsify(finding)
    assert len(sent) == len(cap.used) == 6
    assert cap.blocked == 2
    assert all(LENS_BLOCK in prompt for prompt in sent[:3])
    assert all(LENS_BLOCK not in prompt for prompt in sent[3:])


@pytest.mark.parametrize("proxy", [False, True])
def test_actual_cli_subprocess_outlet_capped(tmp_path, monkeypatch, capsys, proxy):
    from code_forge.cli import main
    from code_forge.trust import record_trust
    import yaml

    empty_git_dir = tmp_path / "empty-git"
    empty_git_dir.mkdir()
    # Share the same Git isolation with fixture setup and CLI subprocesses.
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(empty_git_dir))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(empty_git_dir))

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    git("init")
    git("config", "user.email", "forge@example.invalid")
    git("config", "user.name", "Fixture")
    (repo / "f.py").write_text("def f(x):\n    return x\n")
    git("add", "f.py")
    git("commit", "-m", "fixture base")
    (repo / "f.py").write_text("def f(x):\n    y = x + 1\n    return y\n")
    git("add", "f.py")
    git("commit", "-m", "fixture candidate")

    config = tmp_path / "config"
    (config / "code-forge").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(config / "code-forge"))
    monkeypatch.setenv("FORGE_L1_SERIAL", "1")
    monkeypatch.setenv("FORGE_AB_FIXTURE_KEY", "mock-not-a-secret")
    monkeypatch.chdir(repo)
    raw = {
        "type": "api",
        "format": "openai",
        "model": "fixture",
        "base_url": "https://fixture.invalid/v1",
        "api_key_env": "FORGE_AB_FIXTURE_KEY",
    }
    (config / "code-forge" / "config.yaml").write_text(yaml.safe_dump({"backends": {"fixture": raw}}))
    gate = repo / ".code-forge" / "gate.yaml"
    gate.parent.mkdir()
    gate_data = {"review_lenses": True, "test": "true"}
    gate.write_text(yaml.safe_dump(gate_data))
    (gate.parent / "tools.yaml").write_text(
        "tools:\n  ruff:\n    command: ruff check --output-format=sarif\n"
        "    output_format: sarif\n    file_patterns: ['*.py']\n"
    )
    record_trust(gate, gate_data)
    from code_forge.backend import load_backend_configs

    (backend,) = load_backend_configs({"backends": {"fixture": raw}})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "code-forge",
            "review",
            "--allow-main",
            "--mode",
            "ci",
            "--outlet",
            "subprocess",
            "--backend",
            "fixture",
            "--committed",
        ],
    )
    payload = {
        "findings": [],
        "code_excerpts": [
            {
                "file": "f.py",
                "start_line": 2,
                "end_line": 3,
                "content": "    y = x + 1\n    return y",
            }
        ],
    }
    with (
        fake_backend(payload, proxy=proxy) as sent,
        capped_api(backend, match_system_proxy=proxy) as cap,
    ):
        for arm in ("A", "B"):
            monkeypatch.setenv("FORGE_EXPERIMENT_537_ARM", arm)
            result = main()
            out = capsys.readouterr()
            assert len(sent) == (3 if arm == "A" else 6), (result, out)
            report = json.loads(out.out)["runs"][0]
            assert report["properties"]["tokenCost"]["passes"] == 3
            assert result != 0  # Smoke-only; the review gate did not pass.
    # The ordinary CI path also tries the LLM-backed runtime advisory axis,
    # even on an empty finding response. Neither arm gets that send.
    assert len(cap.used) == 6 and cap.blocked == 2
    assert cap.usage.keys() == cap.used
    assert all(row["cached_input_tokens"] == 0 for row in cap.usage.values())
    assert all(LENS_BLOCK in prompt for prompt in sent[:3])
    assert all(LENS_BLOCK not in prompt for prompt in sent[3:])

    from code_forge.lenses import PASS_LENSES

    header, first = LENS_BLOCK.split("L1 ", 1)
    cards = ("L1 " + first).splitlines(keepends=True)
    for name, before, after in zip(PASS_LENSES, sent[:3], sent[3:], strict=True):
        trimmed = header + "".join(card for card in cards if card[:2] in PASS_LENSES[name])
        assert before.count(LENS_BLOCK) == 1
        assert before.replace(LENS_BLOCK, trimmed, 1) == after


def test_single_use_launcher_requires_credential_before_request(tmp_path, monkeypatch, capsys):
    import run_smoke
    import yaml
    from code_forge.reviewer_json import validate_reviewer_json

    raw = {
        "type": "api",
        "format": "openai",
        "model": "agnes-cn",
        "max_tokens": 131072,
        "base_url": "https://fixture.invalid/v1",
        "api_key_env": "FORGE_AB_FIXTURE_KEY",
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"backends": {run_smoke.PROFILE: raw}}))
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("FORGE_AB_FIXTURE_KEY", raising=False)
    args = ["--expected-base-url", raw["base_url"]]
    with patch.object(http.client.HTTPConnection, "request", side_effect=AssertionError):
        assert run_smoke.main([*args, "--execute"]) == 1
    inactive = json.loads(capsys.readouterr().out)
    assert inactive["status"] == "credential-unavailable-in-process"
    assert inactive["real_sends_admitted"] == 0

    monkeypatch.setenv("FORGE_AB_FIXTURE_KEY", "synthetic-not-a-credential")
    with fake_backend({"findings": []}, proxy=True) as sent:
        assert run_smoke.main(args) == 0
        assert not sent
    assert json.loads(capsys.readouterr().out) == {
        "profile": run_smoke.PROFILE,
        "status": "ready-no-sends",
        "real_sends_admitted": 0,
    }

    payload = {
        "findings": [],
        "code_excerpts": [
            {
                "file": "f.py",
                "start_line": 2,
                "end_line": 3,
                "content": "    y = x + 1\n    return y",
            }
        ],
    }
    capsys.readouterr()
    with fake_backend(payload, proxy=True) as sent:
        assert run_smoke.main([*args, "--execute"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "l1-only-smoke"
    assert report["real_sends_admitted"] == len(sent) == 6
    assert report["arms"].keys() == {"A", "B"}
    assert "synthetic-not-a-credential" not in json.dumps(report)
    assert "fixture.invalid" not in json.dumps(report)

    def reject_in_factory(value, **kwargs):
        if "frozen_diff" in kwargs:
            raise ValueError("synthetic rejected")
        return validate_reviewer_json(value, **kwargs)

    with (
        fake_backend(payload, proxy=True) as sent,
        patch("code_forge.reviewer_json.validate_reviewer_json", side_effect=reject_in_factory),
    ):
        assert run_smoke.main([*args, "--execute"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "l1-smoke-incomplete"
    assert report["real_sends_admitted"] == len(sent) == 3
    assert report["arms"].keys() == {"A"}
    assert all(record is not None for record in report["arms"]["A"]["pass_usage"].values())
