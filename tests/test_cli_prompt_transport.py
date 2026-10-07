"""Custom CLI transports preserve the complete prompt beyond Linux argv limits."""

import hashlib
import sys

import pytest

from code_forge.backend import CliError, load_backend_configs
from code_forge.llm_invoke import _invoke_cli, effective_invoke_timeout_s


def _backend(**fields):
    return load_backend_configs({"backends": {"custom": {"type": "cli", **fields}}})[0]


@pytest.mark.parametrize("size", [180_000, 1_100_000])
def test_stdin_roundtrips_large_unicode_prompt_in_real_subprocess(tmp_path, size):
    executable = tmp_path / "prompt-reader"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import hashlib, json, sys\n"
        "assert sys.argv[1:3] == ['-p', '-']\n"
        "assert sys.argv[3:] == ['--model', 'native', '--output-format', 'json']\n"
        "data = sys.stdin.buffer.read()\n"
        "print(json.dumps({'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    prompt = "quoted ' \" $() 雪\n" * (size // 16) + "\n\n"
    encoded = prompt.encode("utf-8")
    assert len(encoded) > 128 * 1024
    result = _invoke_cli(
        prompt,
        _backend(command=str(executable), model="native", prompt_transport="stdin"),
        timeout_s=10,
    )
    assert result.content == {"sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded)}


def test_default_argv_compatibility_real_subprocess(tmp_path):
    executable = tmp_path / "argv-reader"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\nprint(json.dumps({'prompt': sys.argv[2]}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    prompt = "literal - $() ' \" 雪\n\n"
    backend = _backend(command=str(executable))
    assert backend.prompt_transport == "argv"
    assert _invoke_cli(prompt, backend, timeout_s=10).content == {"prompt": prompt}


@pytest.mark.parametrize("value", ["file", "", True, 1, [], {}])
def test_invalid_cli_transport_rejected(value):
    with pytest.raises(CliError, match="prompt_transport"):
        _backend(prompt_transport=value)


@pytest.mark.parametrize("value", [-1, True, 1.5, "1800", [], {}])
def test_invalid_cli_timeout_rejected(value):
    with pytest.raises(CliError, match="timeout_s"):
        _backend(timeout_s=value)


def test_explicit_cli_timeout_survives_parser_and_bypasses_implicit_cap(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_TIMEOUT_S", "1800")
    assert effective_invoke_timeout_s(_backend()) == 300
    assert effective_invoke_timeout_s(_backend(timeout_s=0)) == 300
    assert effective_invoke_timeout_s(_backend(timeout_s=1800)) == 1800


def test_prompt_transport_is_cli_only():
    with pytest.raises(CliError, match="prompt_transport.*only valid on cli"):
        load_backend_configs({"backends": {"api": {"type": "api", "prompt_transport": "stdin"}}})


def test_null_cli_fields_use_defaults():
    backend = _backend(prompt_transport=None, timeout_s=None)
    assert backend.prompt_transport == "argv"
    assert backend.timeout_s == 0


def test_stdin_large_prompt_timeout_reaps_real_child(tmp_path, monkeypatch):
    import subprocess

    import code_forge.llm_invoke as invoke

    executable = tmp_path / "nonreading-cli"
    executable.write_text(
        f"#!{sys.executable}\nimport time\ntime.sleep(30)\n", encoding="utf-8"
    )
    executable.chmod(0o700)
    backend = _backend(command=str(executable), prompt_transport="stdin")
    children = []
    real_popen = subprocess.Popen

    def spawn(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(invoke.subprocess, "Popen", spawn)
    try:
        with pytest.raises(invoke.LLMInvokeError) as raised:
            _invoke_cli("雪" * 180_000, backend, timeout_s=1)
        assert raised.value.is_timeout
        assert len(children) == 1
        assert children[0].poll() is not None
        assert invoke._active_proc is None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()


def test_prompt_transport_schema_accepts_stdin_and_rejects_unknown():
    import json
    from pathlib import Path

    import jsonschema

    import code_forge.backend as backend_module

    schema = json.loads(Path(backend_module.__file__).with_name("gate.schema.json").read_text())
    # Validate the field independently of unrelated required gate settings.
    transport_schema = schema["$defs"]["backendEntry"]["properties"]["prompt_transport"]
    jsonschema.validate("stdin", transport_schema)
    jsonschema.validate("argv", transport_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate("file", transport_schema)
