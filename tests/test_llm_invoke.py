import json
import os
import subprocess
import urllib.error
from unittest.mock import patch, MagicMock, Mock

import pytest

from code_forge.llm_invoke import llm_invoke, LLMInvokeError
from code_forge.backend import BackendConfig


class TestLLMInvoke:
    def test_returns_parsed_json_on_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"findings": []})
        mock_result.stderr = ""

        backend = BackendConfig(
            name="test", type="cli", model="claude-sonnet-4-6", command=""
        )
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = llm_invoke("review this code", backend=backend)

        assert result == {"findings": []}
        args = mock_run.call_args
        cmd = args[0][0]
        assert "claude" in cmd[0]
        assert "-p" in cmd
        assert "--output-format" in cmd

    def test_raises_on_timeout(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=120),
        ):
            with pytest.raises(LLMInvokeError, match="timed out"):
                llm_invoke("prompt", timeout_s=120)

    def test_raises_on_nonzero_exit(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error: rate limited"
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(LLMInvokeError, match="exited with code 1"):
                llm_invoke("prompt")

    def test_raises_on_invalid_json(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json at all"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(LLMInvokeError, match="non-JSON"):
                llm_invoke("prompt")

    def test_raises_when_claude_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(LLMInvokeError, match="not found"):
                llm_invoke("prompt")

    def test_respects_forge_llm_model_env(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"ok": true}'
        mock_result.stderr = ""
        with (
            patch("subprocess.run", return_value=mock_result) as mock_run,
            patch.dict(os.environ, {"FORGE_LLM_MODEL": "opus-4-7"}),
        ):
            llm_invoke("prompt")
            cmd = mock_run.call_args[0][0]
            assert "opus-4-7" in cmd

    def test_large_prompt_uses_shell_command(self):
        large_prompt = "x" * 1_100_000
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"ok": true}'
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = llm_invoke(large_prompt)
        assert result == {"ok": True}
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "sh"
        assert cmd[1] == "-c"

    def test_strips_markdown_fences(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '```json\n{"key": "value"}\n```'
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            result = llm_invoke("prompt")
        assert result == {"key": "value"}

    def test_raises_on_oserror(self):
        with patch(
            "subprocess.run",
            side_effect=OSError("No such file or directory"),
        ):
            with pytest.raises(LLMInvokeError, match="subprocess failed"):
                llm_invoke("prompt")

    def test_cli_dispatch_default_backend(self):
        """backend=None uses DEFAULT_BACKEND cli path."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"ok": true}'
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = llm_invoke("prompt")
        assert result == {"ok": True}
        cmd = mock_run.call_args[0][0]
        assert "claude" in cmd[0]

    def test_cli_dispatch_custom_command(self):
        """cli backend with custom command uses specified binary."""
        backend = BackendConfig(
            name="custom", type="cli", model="", command="aicc"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"ok": true}'
        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch("shutil.which", return_value="/usr/bin/aicc"):
            result = llm_invoke("prompt", backend=backend)
        assert result == {"ok": True}
        cmd = mock_run.call_args[0][0]
        assert "aicc" in cmd[0] or "/usr/bin/aicc" in cmd[0]

    def test_cli_dispatch_custom_model(self):
        """cli backend with custom model passes --model."""
        backend = BackendConfig(
            name="test", type="cli", model="opus", command=""
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"ok": true}'
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            llm_invoke("prompt", backend=backend)
        cmd = mock_run.call_args[0][0]
        assert "opus" in cmd

    def test_api_dispatch_openai(self):
        """api backend with openai format makes HTTP call."""
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
            "choices": [{"message": {"content": '{"result": "pass"}'}}]
        }).encode("utf-8")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            result = llm_invoke("prompt", backend=backend)

        assert result == {"result": "pass"}
        req = mock_urlopen.call_args[0][0]
        assert "Bearer sk-test" in req.headers["Authorization"]
        assert req.full_url == "https://api.deepseek.com/v1/chat/completions"

    def test_api_dispatch_anthropic(self):
        """api backend with anthropic format makes HTTP call."""
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
            "content": [{"text": '{"result": "pass"}'}]
        }).encode("utf-8")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
             patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            result = llm_invoke("prompt", backend=backend)

        assert result == {"result": "pass"}
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
