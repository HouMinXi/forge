import json
import os
import subprocess
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

    def test_cli_streaming_event_array_extracts_result(self):
        """Streaming event-array (current claude CLI): extracts result event content."""
        events = [
            {"type": "system", "subtype": "init", "cwd": "/tmp"},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 4},
            {"type": "result", "subtype": "success",
             "result": json.dumps({"surfaces": ["nftables"], "findings": []}),
             "usage": {"input_tokens": 100, "output_tokens": 50}},
        ]
        mock_proc = _make_mock_proc(stdout=json.dumps(events))

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            result = llm_invoke("prompt")

        assert result.content == {"surfaces": ["nftables"], "findings": []}
        assert result.usage.input_tokens == 100
        assert result.usage.output_tokens == 50

    def test_cli_streaming_array_no_result_event_returns_list(self):
        """Streaming array with no result event falls back to list content."""
        events = [{"type": "system"}, {"type": "thinking"}]
        mock_proc = _make_mock_proc(stdout=json.dumps(events))

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            result = llm_invoke("prompt")

        assert isinstance(result.content, list)

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

    def test_api_bare_timeout_error_openai_raises_llm_invoke_error(self):
        """Regression: bare TimeoutError (OSError, not URLError) must not propagate.

        socket.timeout is an alias of TimeoutError on all supported interpreters.
        _invoke_openai and _invoke_anthropic only catch HTTPError / URLError, so
        a bare TimeoutError escapes to the caller and crashes code-forge (exit 1,
        full traceback). After the fix it must be converted to LLMInvokeError so
        the pipeline records an INFRA finding and exits FAIL gracefully.
        """
        backend = BackendConfig(
            name="test",
            type="api",
            model="model",
            format="openai",
            base_url="https://example.com",
            api_key_env="TEST_KEY",
        )
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=TimeoutError("read timed out")):
            with pytest.raises(LLMInvokeError, match="timed out"):
                llm_invoke("prompt", backend=backend)

    def test_api_bare_timeout_error_anthropic_raises_llm_invoke_error(self):
        """Regression: bare TimeoutError on anthropic format must not propagate."""
        backend = BackendConfig(
            name="test",
            type="api",
            model="model",
            format="anthropic",
            base_url="https://example.com",
            api_key_env="TEST_KEY",
        )
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=TimeoutError("read timed out")):
            with pytest.raises(LLMInvokeError, match="timed out"):
                llm_invoke("prompt", backend=backend)

    def test_unsupported_backend_type(self):
        """Unsupported backend type raises LLMInvokeError."""
        backend = BackendConfig(
            name="test", type="grpc", model="model"
        )
        with pytest.raises(LLMInvokeError, match="unsupported backend type"):
            llm_invoke("prompt", backend=backend)

    def test_api_fallback_extracts_falsify_envelope_with_expected_keys(self):
        """Falsify regression: api backend fallback path must extract verdict envelope.

        This test exercises _invoke_api -> _extract_json_from_text (the path that hid
        the regression). The cli backend does NOT call _extract_json_from_text and would
        give a false green -- api backend is mandatory here per brief requirement.

        Scenario: mimo-pro style response with prose before JSON (json.loads fails on the
        full string; the fallback extractor runs with expected_keys from the caller).
        """
        backend = BackendConfig(
            name="mimo-pro",
            type="api",
            model="mimo-v2.5-pro",
            format="anthropic",
            base_url="https://token-plan-cn.xiaomimimo.com/anthropic",
            api_key_env="MIMO_PRO_API_KEY",
        )
        # Prose before JSON forces json.loads to fail -> _extract_json_from_text runs.
        prose_wrapped = (
            'Let me verify this finding carefully. '
            '{"verdict": "DISMISSED", "reasoning": "safe"}'
        )
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "content": [{"type": "text", "text": prose_wrapped}],
            "usage": {"input_tokens": 50, "output_tokens": 30},
        }).encode("utf-8")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch.dict(os.environ, {"MIMO_PRO_API_KEY": "tp-test"}), \
             patch("urllib.request.urlopen", return_value=mock_response):
            result = llm_invoke(
                "falsify prompt",
                backend=backend,
                expected_keys=frozenset({"verdict", "reasoning"}),
            )

        assert result.content == {"verdict": "DISMISSED", "reasoning": "safe"}


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


class TestAnthropicThinkingBlock:
    """Guard for _invoke_anthropic thinking-block extraction.

    Some anthropic-compatible backends (MiniMax) prepend a thinking
    block before the text block.  _invoke_anthropic must skip the
    thinking block and extract the first type=="text" entry.
    """

    def _make_anthropic_backend(self):
        return BackendConfig(
            name="minimax",
            type="api",
            model="MiniMax-M3",
            format="anthropic",
            base_url="https://api.minimaxi.com/anthropic",
            api_key_env="MINIMAX_API_KEY",
        )

    def _mock_response(self, content_blocks):
        resp = Mock()
        resp.read.return_value = json.dumps({
            "content": content_blocks,
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)
        return resp

    def test_thinking_then_text(self):
        """Extracts text block when thinking block precedes it."""
        backend = self._make_anthropic_backend()
        content_blocks = [
            {"type": "thinking", "thinking": "let me think..."},
            {"type": "text", "text": '{"findings": []}'},
        ]
        resp = self._mock_response(content_blocks)

        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", return_value=resp):
            result = llm_invoke("prompt", backend=backend)

        assert result.content == {"findings": []}
        assert result.usage.input_tokens == 10

    def test_text_only_no_type_field(self):
        """Backward compat: block without type field treated as text."""
        backend = self._make_anthropic_backend()
        content_blocks = [
            {"text": '{"status": "ok"}'},
        ]
        resp = self._mock_response(content_blocks)

        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", return_value=resp):
            result = llm_invoke("prompt", backend=backend)

        assert result.content == {"status": "ok"}

    def test_thinking_only_no_text_block(self):
        """Raises LLMInvokeError when no text block exists."""
        backend = self._make_anthropic_backend()
        content_blocks = [
            {"type": "thinking", "thinking": "only thinking..."},
        ]
        resp = self._mock_response(content_blocks)

        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError, match="unexpected response"):
                llm_invoke("prompt", backend=backend)


# -- TestVertexInvoke -------------------------------------------------------


def _make_vertex_backend(**kwargs):
    """Helper: create a vertex BackendConfig."""
    defaults = dict(
        name="vtx", type="api", model="claude-sonnet-4-6",
        format="vertex", project_id="my-project", region="global",
        base_url=None, api_key_env=None, command="",
        default=False, max_tokens=8192,
    )
    defaults.update(kwargs)
    return BackendConfig(**defaults)


def _vertex_mock_response(content_str: str, usage: dict = None):
    """Build a mock urlopen response for Vertex."""
    resp_data = {
        "content": [{"type": "text", "text": content_str}],
        "usage": usage or {"input_tokens": 10, "output_tokens": 20},
    }
    resp = MagicMock()
    resp.read.return_value = json.dumps(resp_data).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestVertexBuildUrl:
    """Unit tests for _build_vertex_url."""

    def test_build_vertex_url_global(self):
        from code_forge.llm_invoke import _build_vertex_url
        url = _build_vertex_url("proj", "global", "claude-sonnet-4-6")
        assert url == (
            "https://aiplatform.googleapis.com/v1/projects/proj/"
            "locations/global/publishers/anthropic/models/"
            "claude-sonnet-4-6:rawPredict"
        )

    def test_build_vertex_url_regional(self):
        from code_forge.llm_invoke import _build_vertex_url
        url = _build_vertex_url("proj", "us-east5", "claude-sonnet-4-6")
        assert url == (
            "https://us-east5-aiplatform.googleapis.com/v1/projects/proj/"
            "locations/us-east5/publishers/anthropic/models/"
            "claude-sonnet-4-6:rawPredict"
        )

    def test_build_vertex_url_multiregion_us(self):
        from code_forge.llm_invoke import _build_vertex_url
        url = _build_vertex_url("proj", "us", "claude-sonnet-4-6")
        assert url == (
            "https://aiplatform.us.rep.googleapis.com/v1/projects/proj/"
            "locations/us/publishers/anthropic/models/"
            "claude-sonnet-4-6:rawPredict"
        )

    def test_build_vertex_url_multiregion_eu(self):
        from code_forge.llm_invoke import _build_vertex_url
        url = _build_vertex_url("proj", "eu", "claude-sonnet-4-6")
        assert url == (
            "https://aiplatform.eu.rep.googleapis.com/v1/projects/proj/"
            "locations/eu/publishers/anthropic/models/"
            "claude-sonnet-4-6:rawPredict"
        )


class TestVertexInvoke:
    """Tests for _invoke_vertex wire protocol and error handling."""

    def _mock_google_auth(self, monkeypatch, token="fake-token"):
        """Patch google-auth modules and return mock credentials."""
        mock_creds = MagicMock()
        mock_creds.token = token

        mock_sa = MagicMock()
        mock_sa.Credentials.from_service_account_file.return_value = mock_creds

        mock_ga = MagicMock()
        mock_ga.default.return_value = (mock_creds, "project")

        mock_transport = MagicMock()

        monkeypatch.setattr("code_forge.llm_invoke._invoke_vertex.__code__", None)
        return mock_creds, mock_sa, mock_ga, mock_transport

    def test_vertex_missing_google_auth_raises(self, monkeypatch):
        """Missing google-auth raises LLMInvokeError with install instructions."""
        from code_forge.llm_invoke import _invoke_vertex
        backend = _make_vertex_backend()

        import sys
        # Simulate ImportError by removing google from sys.modules
        saved = {k: v for k, v in sys.modules.items() if k.startswith('google')}
        for k in list(sys.modules.keys()):
            if k.startswith('google'):
                del sys.modules[k]

        with patch.dict(sys.modules, {"google.oauth2": None, "google.auth": None,
                                       "google.oauth2.service_account": None,
                                       "google.auth.transport.requests": None,
                                       "google.auth.exceptions": None}):
            with pytest.raises(LLMInvokeError, match="pip install code-review-forge"):
                _invoke_vertex("prompt", backend, 30)

        # Restore
        sys.modules.update(saved)

    def test_vertex_body_no_model_has_anthropic_version(self, monkeypatch):
        """Vertex body: has anthropic_version, NO model key."""
        from code_forge.llm_invoke import _invoke_vertex
        backend = _make_vertex_backend()

        captured_body = {}

        mock_creds = MagicMock()
        mock_creds.token = "tok"

        def fake_urlopen(req, timeout=None):
            import json as _json
            captured_body.update(_json.loads(req.data.decode()))
            return _vertex_mock_response(json.dumps({"findings": []}))

        with patch("google.oauth2.service_account.Credentials.from_service_account_file",
                   return_value=mock_creds), \
             patch("google.auth.default", return_value=(mock_creds, "proj")), \
             patch("google.auth.transport.requests.Request"), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            _invoke_vertex("prompt", backend, 30)

        assert "anthropic_version" in captured_body
        assert captured_body["anthropic_version"] == "vertex-2023-10-16"
        assert "model" not in captured_body

    def test_vertex_headers_bearer_no_api_key(self, monkeypatch):
        """Vertex headers: Bearer token, NO x-api-key, NO anthropic-version."""
        from code_forge.llm_invoke import _invoke_vertex
        backend = _make_vertex_backend()

        captured_headers = {}
        mock_creds = MagicMock()
        mock_creds.token = "my-bearer-token"

        def fake_urlopen(req, timeout=None):
            captured_headers.update(req.headers)
            return _vertex_mock_response(json.dumps({"findings": []}))

        with patch("google.auth.default", return_value=(mock_creds, "proj")), \
             patch("google.auth.transport.requests.Request"), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            _invoke_vertex("prompt", backend, 30)

        # Authorization header is lowercased by urllib
        auth = captured_headers.get("Authorization", captured_headers.get("authorization", ""))
        assert auth.startswith("Bearer ")
        assert "x-api-key" not in {k.lower() for k in captured_headers}
        assert "anthropic-version" not in {k.lower() for k in captured_headers}

    def test_vertex_returns_real_usage(self, monkeypatch):
        """Vertex response returns real token usage (D-14)."""
        from code_forge.llm_invoke import _invoke_vertex
        backend = _make_vertex_backend()
        mock_creds = MagicMock()
        mock_creds.token = "tok"

        resp_data = {
            "content": [{"type": "text", "text": json.dumps({"ok": True})}],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        resp = MagicMock()
        resp.read.return_value = json.dumps(resp_data).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)

        with patch("google.auth.default", return_value=(mock_creds, "proj")), \
             patch("google.auth.transport.requests.Request"), \
             patch("urllib.request.urlopen", return_value=resp):
            content, usage = _invoke_vertex("prompt", backend, 30)

        assert usage.get("input_tokens") == 100
        assert usage.get("output_tokens") == 50

    def test_vertex_default_creds_not_found(self, monkeypatch):
        """google.auth.default raises DefaultCredentialsError -> LLMInvokeError."""
        from code_forge.llm_invoke import _invoke_vertex
        backend = _make_vertex_backend()

        from google.auth.exceptions import DefaultCredentialsError
        with patch("google.auth.default", side_effect=DefaultCredentialsError("no creds")):
            with pytest.raises(LLMInvokeError, match="No GCP credentials found"):
                _invoke_vertex("prompt", backend, 30)

    def test_vertex_refresh_error(self, monkeypatch):
        """credentials.refresh raises RefreshError -> LLMInvokeError."""
        from code_forge.llm_invoke import _invoke_vertex
        backend = _make_vertex_backend()

        mock_creds = MagicMock()
        mock_creds.token = "tok"
        from google.auth.exceptions import RefreshError
        mock_creds.refresh.side_effect = RefreshError("token expired")

        with patch("google.auth.default", return_value=(mock_creds, "proj")), \
             patch("google.auth.transport.requests.Request"):
            with pytest.raises(LLMInvokeError, match="Failed to refresh"):
                _invoke_vertex("prompt", backend, 30)

    def test_vertex_http_error(self, monkeypatch):
        """HTTP error from Vertex -> LLMInvokeError with body excerpt."""
        from code_forge.llm_invoke import _invoke_vertex
        import io
        backend = _make_vertex_backend()
        mock_creds = MagicMock()
        mock_creds.token = "tok"

        http_err = urllib.error.HTTPError(
            url="https://example.com", code=400, msg="Bad Request",
            hdrs={}, fp=io.BytesIO(b'{"error":{"message":"bad"}}'),
        )

        with patch("google.auth.default", return_value=(mock_creds, "proj")), \
             patch("google.auth.transport.requests.Request"), \
             patch("urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(LLMInvokeError, match="HTTP 400"):
                _invoke_vertex("prompt", backend, 30)

    def test_vertex_missing_project_id_raises(self, monkeypatch):
        """project_id=None raises LLMInvokeError with configuration guidance."""
        from code_forge.llm_invoke import _invoke_vertex
        backend = _make_vertex_backend(project_id=None)

        mock_creds = MagicMock()
        mock_creds.token = "tok"

        with patch("google.auth.default", return_value=(mock_creds, "proj")), \
             patch("google.auth.transport.requests.Request"):
            with pytest.raises(LLMInvokeError, match="requires project_id"):
                _invoke_vertex("prompt", backend, 30)

    def test_invoke_api_vertex_skips_api_key_env(self, monkeypatch):
        """_invoke_api with vertex format doesn't raise on api_key_env=None."""
        from code_forge.llm_invoke import _invoke_api
        backend = _make_vertex_backend()

        mock_result = (json.dumps({"findings": []}), {"input_tokens": 1, "output_tokens": 1})
        with patch("code_forge.llm_invoke._invoke_vertex", return_value=mock_result):
            result = _invoke_api("prompt", backend, 30)
        assert result.usage.input_tokens == 1


class TestStripFences:
    def _strip(self, text):
        from code_forge.llm_invoke import _strip_fences
        return _strip_fences(text)

    def test_basic_fence(self):
        assert self._strip('```\n{"k":1}\n```') == '{"k":1}'

    def test_json_lang_specifier(self):
        assert self._strip('```json\n{"k":1}\n```') == '{"k":1}'

    def test_trailing_text_after_closing_fence(self):
        text = '```json\n{"findings":[]}\n```\n\nI am ready to review more code.'
        result = self._strip(text)
        assert result == '{"findings":[]}'
        assert "ready to review" not in result

    def test_trailing_text_with_blank_lines(self):
        text = '```json\n{"ok":true}\n```\n\n\nExtra explanation.'
        assert self._strip(text) == '{"ok":true}'

    def test_no_fence_passthrough(self):
        text = '{"key": "value"}'
        assert self._strip(text) == text

    def test_multiline_json_in_fence(self):
        text = '```json\n{\n  "findings": [],\n  "code_excerpts": []\n}\n```'
        assert json.loads(self._strip(text)) == {"findings": [], "code_excerpts": []}

    def test_content_with_backticks_not_confused_as_fence(self):
        text = '```\n{"desc": "use `x`"}\n```'
        assert self._strip(text) == '{"desc": "use `x`"}'

    def test_closing_fence_with_trailing_whitespace(self):
        """Closing fence with trailing spaces is detected via line.strip() == '```'.

        '```   '.strip() == '```' is True, so the fence IS matched and prose
        after it is correctly discarded.
        """
        text = '```json\n{"k": 1}\n```   \n\nExtra prose here.'
        assert self._strip(text) == '{"k": 1}'


class TestExtractJsonFromText:
    def _extract(self, text):
        from code_forge.llm_invoke import _extract_json_from_text
        return _extract_json_from_text(text)

    def _extract_with_keys(self, text, keys):
        from code_forge.llm_invoke import _extract_json_from_text
        return _extract_json_from_text(text, expected_keys=keys)

    def test_plain_json_envelope(self):
        """Dict with a known envelope key is returned."""
        assert self._extract('{"findings": []}') == {"findings": []}

    def test_json_with_preamble(self):
        text = 'Here are my findings:\n\n{"findings": [], "code_excerpts": []}'
        assert self._extract(text) == {"findings": [], "code_excerpts": []}

    def test_envelope_with_postamble(self):
        """Envelope dict (findings key) with postamble is extracted; trailing prose ignored."""
        text = '{"findings": []}\n\nLet me know if you need more.'
        assert self._extract(text) == {"findings": []}

    def test_json_array_returns_none(self):
        """Bare arrays are not valid forge envelopes -- returns None."""
        assert self._extract('Results: [1, 2, 3]') is None

    def test_non_envelope_dict_returns_none(self):
        """Dict without known envelope keys is not a valid envelope."""
        assert self._extract('{"ok": true}') is None

    def test_nested_envelope_json(self):
        """Nested dict with envelope key is returned."""
        text = 'Output: {"findings": [], "meta": {"b": [1, 2]}}'
        assert self._extract(text) == {"findings": [], "meta": {"b": [1, 2]}}

    def test_surfaces_key_is_valid_envelope(self):
        """RUNTIME axis uses 'surfaces' key -- must be accepted."""
        text = 'Done: {"surfaces": ["nftables"], "findings": []}'
        assert self._extract(text) == {"surfaces": ["nftables"], "findings": []}

    def test_no_json_returns_none(self):
        assert self._extract("No JSON here at all.") is None

    def test_empty_string_returns_none(self):
        assert self._extract("") is None

    def test_broken_json_returns_none(self):
        assert self._extract('{"findings": [') is None

    def test_brace_inside_string_in_envelope(self):
        """'{' inside a string value in an envelope dict must not confuse the extractor."""
        text = 'Result: {"findings": [], "key": "{not a start}"}'
        assert self._extract(text) == {"findings": [], "key": "{not a start}"}

    def test_escaped_quotes_in_envelope(self):
        """Escaped quotes inside envelope JSON strings are handled correctly."""
        text = '{"findings": [], "k": "value with \\"quote\\""}'
        result = self._extract(text)
        assert result == {"findings": [], "k": 'value with "quote"'}

    # -- falsify regression reproducer (RED on 652cbd6, GREEN after this fix) --

    def test_falsify_verdict_without_expected_keys_returns_none(self):
        """F1 safety preserved: verdict envelope is not a review envelope by default.

        Without expected_keys, _REVIEW_ENVELOPE_KEYS is used (findings/code_excerpts/
        surfaces). "verdict" is not in that set, so the dict is correctly rejected --
        preserving F1 protection for review-pass callers.
        """
        text = 'Let me verify. {"verdict": "DISMISSED", "reasoning": "safe"}'
        assert self._extract(text) is None

    def test_falsify_verdict_with_expected_keys_extracted(self):
        """Falsify regression reproducer: verdict envelope extracted when expected_keys set.

        On 652cbd6 (before this fix): returns None because "verdict" not in
        _REVIEW_ENVELOPE_KEYS.  After fix: passing expected_keys=frozenset({"verdict",
        "reasoning"}) returns the dict. This is the path RealFalsifier.falsify() uses.
        """
        text = 'Let me verify. {"verdict": "DISMISSED", "reasoning": "safe"}'
        result = self._extract_with_keys(text, frozenset({"verdict", "reasoning"}))
        assert result == {"verdict": "DISMISSED", "reasoning": "safe"}, (
            "falsify regression: expected verdict dict, got %r" % (result,)
        )

    # -- F1 reproducer (was RED on HEAD, must be GREEN after fix) --

    def test_f1_stray_array_before_real_envelope(self):
        """F1: stray array fragment in prose must NOT be returned; real envelope must be.

        The old implementation returned [1, 2] (first raw_decode-able token).
        The fix requires a dict with known envelope keys, skipping the array.
        """
        text = 'The array [1, 2] looks suspect. {"findings": ["REAL"]}'
        result = self._extract(text)
        assert result == {"findings": ["REAL"]}, (
            f"F1 fail: expected envelope dict, got {result!r}"
        )

    # -- F2 reproducer (was RED on HEAD, must be GREEN after fix) --

    def test_f2_many_invalid_braces_before_real_envelope(self):
        """F2: >10 invalid '{' chars before real envelope must not exhaust the scan.

        The old implementation capped at max_attempts=10; 10 '{a' fragments
        exhausted the cap and returned None, missing the real envelope.
        The fix removes the cap; raw_decode fails in O(1) for invalid JSON.
        """
        text = "{a" * 10 + ' {"findings": ["REAL"]}'
        result = self._extract(text)
        assert result == {"findings": ["REAL"]}, (
            f"F2 fail: expected envelope dict, got {result!r}"
        )


class TestMimoProCompatibility:
    """Guard: mimo-pro wraps JSON in ```json fence with trailing prose."""

    def _backend(self):
        return BackendConfig(
            name="mimo-pro", type="api", model="mimo-v2.5-pro",
            format="anthropic",
            base_url="https://token-plan-cn.xiaomimimo.com/anthropic",
            api_key_env="MIMO_PRO_API_KEY",
        )

    def _mock_response(self, text_content):
        resp = Mock()
        resp.read.return_value = json.dumps({
            "content": [
                {"type": "text", "text": text_content},
                {"type": "thinking", "thinking": "...", "signature": ""},
            ],
            "usage": {"input_tokens": 4151, "output_tokens": 710},
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)
        return resp

    def test_fence_with_trailing_text_parses_ok(self):
        """Core: mimo-pro appends prose after closing ``` -- must still parse."""
        backend = self._backend()
        mimo_response = (
            '```json\n'
            '{"findings": [], "code_excerpts": [{"file": "a.py", '
            '"start_line": 1, "end_line": 3, "content": "x=1"}]}\n'
            '```\n\n'
            "I'm ready to review more code in the format you specified."
        )
        with patch.dict(os.environ, {"MIMO_PRO_API_KEY": "tp-test"}), \
             patch("urllib.request.urlopen", return_value=self._mock_response(mimo_response)):
            result = llm_invoke("expert pass prompt", backend=backend)
        assert result.content["findings"] == []
        assert result.content["code_excerpts"][0]["file"] == "a.py"

    def test_thinking_block_after_text_is_ignored(self):
        """Thinking block does not interfere with text block extraction."""
        with patch.dict(os.environ, {"MIMO_PRO_API_KEY": "tp-test"}), \
             patch("urllib.request.urlopen", return_value=self._mock_response('{"ok": true}')):
            result = llm_invoke("prompt", backend=self._backend())
        assert result.content == {"ok": True}

    def test_fence_without_trailing_text_still_works(self):
        """Regression: clean ```json{...}``` (no trailing text) still parses ok."""
        with patch.dict(os.environ, {"MIMO_PRO_API_KEY": "tp-test"}), \
             patch("urllib.request.urlopen",
                   return_value=self._mock_response('```json\n{"findings": []}\n```')):
            result = llm_invoke("prompt", backend=self._backend())
        assert result.content == {"findings": []}
