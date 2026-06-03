import json
import os
import subprocess
import time
import urllib.error
from unittest.mock import patch, MagicMock, Mock

import pytest

from code_forge.llm_invoke import llm_invoke, LLMInvokeError, LLMResult, Usage
from code_forge.backend import BackendConfig


def _make_mock_proc(returncode=0, stdout="", stderr=""):
    """Build a mock Popen process object."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    proc.pid = 12345
    return proc


class TestLLMInvoke:
    def test_returns_llm_result_on_success(self):
        mock_proc = _make_mock_proc(stdout=json.dumps({"findings": []}))

        backend = BackendConfig(
            name="test", type="cli", model="claude-sonnet-4-6", command=""
        )
        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            result = llm_invoke("review this code", backend=backend)

        assert isinstance(result, LLMResult)
        assert result.content == {"findings": []}
        assert isinstance(result.usage, Usage)
        assert result.duration_s >= 0.0

    def test_raises_on_timeout(self):
        mock_proc = _make_mock_proc()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(
            cmd=["claude"], timeout=120
        )

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc), \
             patch("code_forge.llm_invoke._kill_tree"):
            with pytest.raises(LLMInvokeError, match="timed out"):
                llm_invoke("prompt", timeout_s=120)

    def test_raises_on_nonzero_exit(self):
        mock_proc = _make_mock_proc(returncode=1, stderr="error: rate limited")

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(LLMInvokeError, match="exited with code 1"):
                llm_invoke("prompt")

    def test_raises_on_invalid_json(self):
        mock_proc = _make_mock_proc(stdout="not json at all")

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(LLMInvokeError, match="non-JSON"):
                llm_invoke("prompt")

    def test_raises_when_claude_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(LLMInvokeError, match="not found"):
                llm_invoke("prompt")

    def test_respects_forge_llm_model_env(self):
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch.dict(os.environ, {"FORGE_LLM_MODEL": "opus-4-7"}):
            llm_invoke("prompt")
            cmd = mock_popen.call_args[0][0]
            assert "opus-4-7" in cmd

    def test_large_prompt_uses_shell_command(self):
        large_prompt = "x" * 1_100_000
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen:
            result = llm_invoke(large_prompt)
        assert result.content == {"ok": True}
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "sh"
        assert cmd[1] == "-c"

    def test_strips_markdown_fences(self):
        mock_proc = _make_mock_proc(stdout='```json\n{"key": "value"}\n```')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            result = llm_invoke("prompt")
        assert result.content == {"key": "value"}

    def test_raises_on_oserror(self):
        with patch(
            "code_forge.llm_invoke.subprocess.Popen",
            side_effect=OSError("No such file or directory"),
        ):
            with pytest.raises(LLMInvokeError, match="subprocess failed"):
                llm_invoke("prompt")

    def test_cli_dispatch_default_backend(self):
        """backend=None uses DEFAULT_BACKEND cli path."""
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen:
            result = llm_invoke("prompt")
        assert result.content == {"ok": True}
        cmd = mock_popen.call_args[0][0]
        assert "claude" in cmd[0]

    def test_cli_dispatch_custom_command(self):
        """cli backend with custom command uses specified binary."""
        backend = BackendConfig(
            name="custom", type="cli", model="", command="aicc"
        )
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch("shutil.which", return_value="/usr/bin/aicc"):
            result = llm_invoke("prompt", backend=backend)
        assert result.content == {"ok": True}
        cmd = mock_popen.call_args[0][0]
        assert "aicc" in cmd[0] or "/usr/bin/aicc" in cmd[0]

    def test_cli_dispatch_custom_model(self):
        """cli backend with custom model passes --model."""
        backend = BackendConfig(
            name="test", type="cli", model="opus", command=""
        )
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen:
            llm_invoke("prompt", backend=backend)
        cmd = mock_popen.call_args[0][0]
        assert "opus" in cmd

    def test_cli_uses_start_new_session(self):
        """cli backend passes start_new_session=True for process group isolation."""
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen:
            llm_invoke("prompt")
        kwargs = mock_popen.call_args[1]
        assert kwargs.get("start_new_session") is True

    def test_cli_usage_zero_when_no_envelope(self):
        """Direct JSON response (no Claude CLI envelope) returns Usage(0, 0)."""
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            result = llm_invoke("prompt")
        assert result.usage.input_tokens == 0
        assert result.usage.output_tokens == 0
        assert result.content == {"ok": True}

    def test_cli_usage_extracted_from_envelope(self):
        """Claude CLI JSON envelope format: extracts usage + unwraps inner result."""
        envelope = json.dumps({
            "type": "result",
            "subtype": "success",
            "result": json.dumps({"findings": []}),
            "usage": {"input_tokens": 350, "output_tokens": 120,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 50},
        })
        mock_proc = _make_mock_proc(stdout=envelope)

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            result = llm_invoke("prompt")
        assert result.usage.input_tokens == 350
        assert result.usage.output_tokens == 120
        assert result.content == {"findings": []}

    def test_api_dispatch_openai(self):
        """api backend with openai format makes HTTP call and returns LLMResult."""
        backend = BackendConfig(
            name="deepseek",
            type="api",
            model="deepseek-chat",
            format="openai",
            base_url="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
        )
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"result": "pass"}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }).encode("utf-8")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            result = llm_invoke("prompt", backend=backend)

        assert isinstance(result, LLMResult)
        assert result.content == {"result": "pass"}
        assert result.usage.input_tokens == 100
        assert result.usage.output_tokens == 50
        req = mock_urlopen.call_args[0][0]
        assert "Bearer sk-test" in req.headers["Authorization"]
        assert req.full_url == "https://api.deepseek.com/v1/chat/completions"

    def test_api_dispatch_anthropic(self):
        """api backend with anthropic format makes HTTP call and returns LLMResult."""
        backend = BackendConfig(
            name="claude-api",
            type="api",
            model="claude-sonnet-4-20250514",
            format="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
        )
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "content": [{"text": '{"result": "pass"}'}],
            "usage": {"input_tokens": 200, "output_tokens": 75},
        }).encode("utf-8")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
             patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            result = llm_invoke("prompt", backend=backend)

        assert isinstance(result, LLMResult)
        assert result.content == {"result": "pass"}
        assert result.usage.input_tokens == 200
        assert result.usage.output_tokens == 75
        req = mock_urlopen.call_args[0][0]
        assert req.headers.get("X-api-key") == "sk-ant-test"
        assert req.full_url == "https://api.anthropic.com/v1/messages"

    def test_api_dispatch_missing_key(self):
        """api backend with unset api_key_env raises LLMInvokeError."""
        backend = BackendConfig(
            name="test",
            type="api",
            model="model",
            format="openai",
            base_url="https://example.com",
            api_key_env="MISSING_KEY",
        )
        with pytest.raises(LLMInvokeError, match="MISSING_KEY.*not set"):
            llm_invoke("prompt", backend=backend)

    def test_api_dispatch_http_error(self):
        """api backend HTTP error raises LLMInvokeError with status code."""
        backend = BackendConfig(
            name="test",
            type="api",
            model="model",
            format="openai",
            base_url="https://example.com",
            api_key_env="TEST_KEY",
        )
        http_error = urllib.error.HTTPError(
            "https://example.com", 429, "Rate limited", {}, None
        )
        http_error.read = Mock(return_value=b"rate limit exceeded")

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(LLMInvokeError, match="HTTP 429"):
                llm_invoke("prompt", backend=backend)

    def test_api_dispatch_timeout(self):
        """api backend URLError raises LLMInvokeError."""
        backend = BackendConfig(
            name="test",
            type="api",
            model="model",
            format="openai",
            base_url="https://example.com",
            api_key_env="TEST_KEY",
        )
        url_error = urllib.error.URLError("timeout")

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=url_error):
            with pytest.raises(LLMInvokeError, match="URLError"):
                llm_invoke("prompt", backend=backend)

    def test_unsupported_backend_type(self):
        """Unsupported backend type raises LLMInvokeError."""
        backend = BackendConfig(
            name="test", type="grpc", model="model"
        )
        with pytest.raises(LLMInvokeError, match="unsupported backend type"):
            llm_invoke("prompt", backend=backend)


    def test_cli_omits_model_flag_when_empty(self):
        """When backend.model='' and FORGE_LLM_MODEL unset, --model must NOT appear in cmd."""
        backend = BackendConfig(name="test", type="cli", model="", command="")
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        env_without_forge_model = {
            k: v for k, v in os.environ.items() if k != "FORGE_LLM_MODEL"
        }
        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch.dict(os.environ, env_without_forge_model, clear=True):
            llm_invoke("prompt", backend=backend)
        cmd = mock_popen.call_args[0][0]
        # Check both list elements and any shell string (large-prompt path embeds in cmd[2])
        shell_str = cmd[2] if len(cmd) > 2 else ""
        assert "--model" not in cmd, "cmd list must not contain --model when effective_model is empty"
        assert "--model" not in shell_str, "shell string must not contain --model when effective_model is empty"

    def test_cli_passes_model_flag_when_backend_has_model(self):
        """When backend.model='opus', --model opus must appear in cmd."""
        backend = BackendConfig(name="test", type="cli", model="opus", command="")
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen:
            llm_invoke("prompt", backend=backend)
        cmd = mock_popen.call_args[0][0]
        assert "--model" in cmd, "cmd list must contain --model when backend.model is set"
        assert "opus" in cmd, "cmd list must contain the model value"

    def test_cli_passes_model_flag_when_forge_env_set(self):
        """When FORGE_LLM_MODEL='haiku' and backend.model='', --model haiku must appear."""
        backend = BackendConfig(name="test", type="cli", model="", command="")
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch.dict(os.environ, {"FORGE_LLM_MODEL": "haiku"}):
            llm_invoke("prompt", backend=backend)
        cmd = mock_popen.call_args[0][0]
        assert "--model" in cmd, "cmd list must contain --model when FORGE_LLM_MODEL is set"
        assert "haiku" in cmd, "cmd list must contain the env-specified model value"

    def test_large_prompt_omits_model_when_empty(self):
        """Large-prompt shell path must omit --model when effective_model is empty."""
        backend = BackendConfig(name="test", type="cli", model="", command="")
        prompt = "x" * 1_100_000
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        env_without_forge_model = {
            k: v for k, v in os.environ.items() if k != "FORGE_LLM_MODEL"
        }
        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch.dict(os.environ, env_without_forge_model, clear=True):
            llm_invoke(prompt, backend=backend)
        cmd = mock_popen.call_args[0][0]
        # Large-prompt path produces ["sh", "-c", shell_str]
        shell_str = cmd[2] if len(cmd) > 2 else ""
        assert "--model" not in cmd[:2], "sh -c prefix must not contain --model"
        assert "--model" not in shell_str, "shell string must not contain --model when effective_model is empty"


class TestLLMResult:
    def test_llm_result_structure(self):
        """Verify LLMResult has content, usage, duration_s fields."""
        usage = Usage(input_tokens=100, output_tokens=50)
        result = LLMResult(content={"test": "data"}, usage=usage, duration_s=1.5)
        assert result.content == {"test": "data"}
        assert result.usage.input_tokens == 100
        assert result.usage.output_tokens == 50
        assert result.duration_s == 1.5

    def test_llm_result_is_frozen(self):
        """LLMResult is immutable (frozen=True)."""
        result = LLMResult(content={})
        with pytest.raises((AttributeError, TypeError)):
            result.content = "new"  # type: ignore[misc]

    def test_usage_is_frozen(self):
        """Usage is immutable (frozen=True)."""
        usage = Usage(input_tokens=10, output_tokens=5)
        with pytest.raises((AttributeError, TypeError)):
            usage.input_tokens = 99  # type: ignore[misc]

    def test_usage_default_zero(self):
        """Usage defaults to zero tokens."""
        usage = Usage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_llm_result_default_usage(self):
        """LLMResult default usage is Usage() with zero tokens."""
        result = LLMResult(content={})
        assert result.usage == Usage()
        assert result.usage.input_tokens == 0
        assert result.usage.output_tokens == 0

    def test_llm_result_default_duration(self):
        """LLMResult default duration_s is 0.0."""
        result = LLMResult(content={})
        assert result.duration_s == 0.0


class TestSubprocessCleanup:
    def test_subprocess_cleanup_on_timeout(self, tmp_path):
        """Verify _kill_tree is called on timeout to prevent orphan processes."""
        mock_proc = _make_mock_proc()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(
            cmd=["claude"], timeout=1
        )

        kill_called = []

        def mock_kill_tree(proc):
            kill_called.append(proc)

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc), \
             patch("code_forge.llm_invoke._kill_tree", side_effect=mock_kill_tree):
            with pytest.raises(LLMInvokeError, match="timed out"):
                llm_invoke("test", timeout_s=1)

        assert len(kill_called) == 1, "_kill_tree must be called exactly once on timeout"
        assert kill_called[0] is mock_proc

    def test_active_proc_cleared_after_success(self):
        """_active_proc is cleared after successful invocation."""
        import code_forge.llm_invoke as m
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            llm_invoke("prompt")

        assert m._active_proc is None

    def test_active_proc_cleared_after_error(self):
        """_active_proc is cleared even when invocation raises."""
        import code_forge.llm_invoke as m
        mock_proc = _make_mock_proc()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(
            cmd=["claude"], timeout=1
        )

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc), \
             patch("code_forge.llm_invoke._kill_tree"):
            with pytest.raises(LLMInvokeError):
                llm_invoke("prompt", timeout_s=1)

        assert m._active_proc is None
