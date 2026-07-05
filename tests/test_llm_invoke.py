import http.client
import json
import os
import ssl
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


class TestLLMInvokeError:
    def test_is_timeout_defaults_to_false(self):
        err = LLMInvokeError("test")
        assert err.is_timeout is False

    def test_is_timeout_can_be_set_true(self):
        err = LLMInvokeError("test", is_timeout=True)
        assert err.is_timeout is True


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
            with pytest.raises(LLMInvokeError, match="timed out") as exc:
                llm_invoke("prompt", timeout_s=120)
            assert exc.value.is_timeout is True

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
             patch("urllib.request.urlopen", side_effect=http_error), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="429"):
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
             patch("urllib.request.urlopen", side_effect=url_error), \
             patch("time.sleep"):
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
             patch("urllib.request.urlopen", side_effect=TimeoutError("read timed out")), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="timed out") as exc:
                llm_invoke("prompt", backend=backend)
            assert exc.value.is_timeout is True

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
             patch("urllib.request.urlopen", side_effect=TimeoutError("read timed out")), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="timed out") as exc:
                llm_invoke("prompt", backend=backend)
            assert exc.value.is_timeout is True

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
            with pytest.raises(LLMInvokeError, match="timed out") as exc:
                llm_invoke("test", timeout_s=1)
            assert exc.value.is_timeout is True

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


class TestTruncationDetection:
    """All three API paths raise kind=truncated on output-cap stop signals.

    Covers M1a: anthropic stop_reason=max_tokens, openai finish_reason=length,
    vertex stop_reason=max_tokens.  The sampling path (stopReason=maxTokens)
    was already covered in TestInvokeSampling.
    """

    def test_anthropic_stop_reason_max_tokens(self):
        from code_forge.llm_invoke import _invoke_anthropic, LLMInvokeError

        backend = BackendConfig(
            name="mimo", type="api", model="m", format="anthropic",
            base_url="http://x", api_key_env="K",
        )
        resp = Mock()
        resp.read.return_value = json.dumps({
            "content": [{"type": "text", "text": '{"findings": ['}],
            "usage": {"input_tokens": 500, "output_tokens": 16384},
            "stop_reason": "max_tokens",
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
                _invoke_anthropic("p", backend, api_key="k", timeout_s=10)
            assert exc_info.value.kind == "truncated"
            assert exc_info.value.retryable is False
            assert "input=500" in str(exc_info.value)
            assert "output capacity" in str(exc_info.value)

    def test_anthropic_stop_reason_end_turn_passes(self):
        from code_forge.llm_invoke import _invoke_anthropic

        backend = BackendConfig(
            name="mimo", type="api", model="m", format="anthropic",
            base_url="http://x", api_key_env="K",
        )
        resp = Mock()
        resp.read.return_value = json.dumps({
            "content": [{"type": "text", "text": '{"findings": []}'}],
            "usage": {"input_tokens": 500, "output_tokens": 200},
            "stop_reason": "end_turn",
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            content, usage = _invoke_anthropic("p", backend, api_key="k", timeout_s=10)
            assert "findings" in content

    def test_openai_finish_reason_length(self):
        from code_forge.llm_invoke import _invoke_openai, LLMInvokeError

        backend = BackendConfig(
            name="ds", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K",
        )
        resp = Mock()
        resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"find'}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 8192},
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
                _invoke_openai("p", backend, api_key="k", timeout_s=10)
            assert exc_info.value.kind == "truncated"
            assert exc_info.value.retryable is False
            assert "finish_reason=length" in str(exc_info.value)

    def test_openai_finish_reason_stop_passes(self):
        from code_forge.llm_invoke import _invoke_openai

        backend = BackendConfig(
            name="ds", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K",
        )
        resp = Mock()
        resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"findings": []}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            content, usage = _invoke_openai("p", backend, api_key="k", timeout_s=10)
            assert "findings" in content


    def test_vertex_stop_reason_max_tokens(self):
        from code_forge.llm_invoke import _invoke_vertex, LLMInvokeError

        backend = _make_vertex_backend()
        mock_creds = MagicMock()
        mock_creds.token = "tok"
        resp_data = {
            "content": [{"type": "text", "text": '{"find'}],
            "usage": {"input_tokens": 600, "output_tokens": 8192},
            "stop_reason": "max_tokens",
        }
        resp = Mock()
        resp.read.return_value = json.dumps(resp_data).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("google.auth.default", return_value=(mock_creds, "proj")), \
             patch("google.auth.transport.requests.Request"), \
             patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
                _invoke_vertex("p", backend, timeout_s=10)
            assert exc_info.value.kind == "truncated"
            assert exc_info.value.retryable is False
            assert "input=600" in str(exc_info.value)
            assert "output capacity" in str(exc_info.value)

    def test_vertex_stop_reason_end_turn_passes(self):
        from code_forge.llm_invoke import _invoke_vertex

        backend = _make_vertex_backend()
        mock_creds = MagicMock()
        mock_creds.token = "tok"
        resp_data = {
            "content": [{"type": "text", "text": '{"findings": []}'}],
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "stop_reason": "end_turn",
        }
        resp = Mock()
        resp.read.return_value = json.dumps(resp_data).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("google.auth.default", return_value=(mock_creds, "proj")), \
             patch("google.auth.transport.requests.Request"), \
             patch("urllib.request.urlopen", return_value=resp):
            content, usage = _invoke_vertex("p", backend, timeout_s=10)
            assert "findings" in content



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


# -- Task 1: LLMInvokeError retryable/retry_after, provider map, helpers ------


class TestLLMInvokeErrorRetryable:
    """LLMInvokeError gains retryable and retry_after attributes."""

    def test_retryable_defaults_to_true(self):
        err = LLMInvokeError("test")
        assert err.retryable is True

    def test_retryable_can_be_set_false(self):
        err = LLMInvokeError("test", retryable=False)
        assert err.retryable is False

    def test_retry_after_defaults_to_none(self):
        err = LLMInvokeError("test")
        assert err.retry_after is None

    def test_retry_after_can_be_set(self):
        err = LLMInvokeError("test", retry_after=5.0)
        assert err.retry_after == 5.0


class TestProviderErrorCodes:
    """PROVIDER_ERROR_CODES module-level dict and RETRYABLE_HTTP_STATUSES."""

    def test_zhipu_1302_retryable(self):
        from code_forge.llm_invoke import PROVIDER_ERROR_CODES
        assert PROVIDER_ERROR_CODES["zhipu"]["1302"] == "retryable"

    def test_zhipu_1113_non_retryable(self):
        from code_forge.llm_invoke import PROVIDER_ERROR_CODES
        assert PROVIDER_ERROR_CODES["zhipu"]["1113"] == "non-retryable"

    def test_zhipu_1305_retryable(self):
        from code_forge.llm_invoke import PROVIDER_ERROR_CODES
        assert PROVIDER_ERROR_CODES["zhipu"]["1305"] == "retryable"

    def test_minimax_1039_non_retryable(self):
        from code_forge.llm_invoke import PROVIDER_ERROR_CODES
        assert PROVIDER_ERROR_CODES["minimax"]["1039"] == "non-retryable"

    def test_minimax_1008_non_retryable(self):
        from code_forge.llm_invoke import PROVIDER_ERROR_CODES
        assert PROVIDER_ERROR_CODES["minimax"]["1008"] == "non-retryable"

    def test_minimax_1002_retryable(self):
        from code_forge.llm_invoke import PROVIDER_ERROR_CODES
        assert PROVIDER_ERROR_CODES["minimax"]["1002"] == "retryable"

    def test_retryable_http_statuses(self):
        from code_forge.llm_invoke import RETRYABLE_HTTP_STATUSES
        assert RETRYABLE_HTTP_STATUSES == frozenset({429, 500, 502, 503, 504})


class TestParseRetryAfter:
    """_parse_retry_after header parser."""

    def test_valid_retry_after(self):
        from code_forge.llm_invoke import _parse_retry_after
        headers = {"Retry-After": "5"}
        assert _parse_retry_after(headers) == 5.0

    def test_absent_header_returns_none(self):
        from code_forge.llm_invoke import _parse_retry_after
        assert _parse_retry_after({}) is None

    def test_negative_value_returns_none(self):
        from code_forge.llm_invoke import _parse_retry_after
        assert _parse_retry_after({"Retry-After": "-1"}) is None

    def test_non_numeric_returns_none(self):
        from code_forge.llm_invoke import _parse_retry_after
        assert _parse_retry_after({"Retry-After": "abc"}) is None

    def test_capped_at_120(self):
        from code_forge.llm_invoke import _parse_retry_after
        assert _parse_retry_after({"Retry-After": "300"}) == 120.0


class TestIsBodyCodeRetryable:
    """_is_body_code_retryable lookup helper."""

    def test_zhipu_1302_true(self):
        from code_forge.llm_invoke import _is_body_code_retryable
        assert _is_body_code_retryable("zhipu", "1302") is True

    def test_zhipu_1113_false(self):
        from code_forge.llm_invoke import _is_body_code_retryable
        assert _is_body_code_retryable("zhipu", "1113") is False

    def test_unknown_provider_defaults_true(self):
        from code_forge.llm_invoke import _is_body_code_retryable
        assert _is_body_code_retryable("unknown_provider", "9999") is True

    def test_unknown_code_defaults_true(self):
        from code_forge.llm_invoke import _is_body_code_retryable
        assert _is_body_code_retryable("zhipu", "9999") is True

    def test_substring_match_zhipu_backend(self):
        from code_forge.llm_invoke import _is_body_code_retryable
        assert _is_body_code_retryable("zhipu-cn", "1113") is False


class TestFormatErrorMessage:
    """_format_error_message output format per D-31-08."""

    def test_contains_provider_and_code(self):
        from code_forge.llm_invoke import _format_error_message
        msg = _format_error_message("deepseek", 402, "balance exhausted")
        assert "deepseek" in msg
        assert "402" in msg

    def test_starts_with_code_forge_prefix(self):
        from code_forge.llm_invoke import _format_error_message
        msg = _format_error_message("deepseek", 402, "balance exhausted")
        assert msg.startswith("code-forge: deepseek backend:")

    def test_contains_actionable_suggestion(self):
        from code_forge.llm_invoke import _format_error_message
        msg = _format_error_message("deepseek", 402, "balance exhausted")
        # Must have non-trivial content after the code
        parts = msg.split(")")
        assert len(parts) >= 2
        suffix = ")".join(parts[1:]).strip()
        assert len(suffix) > 0


class TestCheckBodyError:
    """_check_body_error detects Zhipu/MiniMax body errors."""

    def _make_backend(self, name):
        return BackendConfig(name=name, type="api", model="m", format="openai",
                             base_url="http://x", api_key_env="K")

    def test_zhipu_error_code_raises(self):
        from code_forge.llm_invoke import _check_body_error
        resp = {"error": {"code": "1113", "message": "balance low"}}
        with pytest.raises(LLMInvokeError) as exc:
            _check_body_error(resp, self._make_backend("zhipu"))
        assert exc.value.retryable is False

    def test_zhipu_retryable_code(self):
        from code_forge.llm_invoke import _check_body_error
        resp = {"error": {"code": "1302", "message": "rate limited"}}
        with pytest.raises(LLMInvokeError) as exc:
            _check_body_error(resp, self._make_backend("zhipu"))
        assert exc.value.retryable is True

    def test_minimax_base_resp_raises(self):
        from code_forge.llm_invoke import _check_body_error
        resp = {"base_resp": {"status_code": 1008, "status_msg": "no balance"}}
        with pytest.raises(LLMInvokeError) as exc:
            _check_body_error(resp, self._make_backend("minimax"))
        assert exc.value.retryable is False

    def test_no_error_returns_none(self):
        from code_forge.llm_invoke import _check_body_error
        resp = {"choices": [{"message": {"content": "ok"}}]}
        # Should not raise
        result = _check_body_error(resp, self._make_backend("zhipu"))
        assert result is None


# -- Task 2: HTTP classification, body wiring, retry loop, stderr progress ----


def _make_api_backend(name="test", fmt="openai"):
    return BackendConfig(
        name=name, type="api", model="model", format=fmt,
        base_url="https://example.com", api_key_env="TEST_KEY",
    )


def _mock_ok_response(content_json='{"findings": []}'):
    """Build a mock urlopen response for successful API calls."""
    resp = Mock()
    if content_json.startswith("{"):
        # openai format
        body = json.dumps({
            "choices": [{"message": {"content": content_json}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })
    resp.read.return_value = body.encode("utf-8")
    resp.__enter__ = Mock(return_value=resp)
    resp.__exit__ = Mock(return_value=False)
    return resp


class TestHTTPErrorClassification:
    """HTTP errors from _invoke_openai/_invoke_anthropic carry retryable flag."""

    def test_openai_429_retryable_with_retry_after(self):
        backend = _make_api_backend()
        headers = {"Retry-After": "5"}
        http_error = urllib.error.HTTPError(
            "https://example.com", 429, "Rate limited", headers, None,
        )
        http_error.read = Mock(return_value=b"rate limit exceeded")

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(LLMInvokeError) as exc:
                llm_invoke("prompt", backend=backend, max_attempts=1)
        assert exc.value.retryable is True
        assert exc.value.retry_after == 5.0

    def test_openai_402_non_retryable(self):
        backend = _make_api_backend()
        http_error = urllib.error.HTTPError(
            "https://example.com", 402, "Payment required", {}, None,
        )
        http_error.read = Mock(return_value=b"balance exhausted")

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(LLMInvokeError) as exc:
                llm_invoke("prompt", backend=backend)
        assert exc.value.retryable is False

    def test_openai_403_non_retryable(self):
        backend = _make_api_backend()
        http_error = urllib.error.HTTPError(
            "https://example.com", 403, "Forbidden", {}, None,
        )
        http_error.read = Mock(return_value=b"forbidden")

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(LLMInvokeError) as exc:
                llm_invoke("prompt", backend=backend)
        assert exc.value.retryable is False

    def test_openai_500_retryable(self):
        backend = _make_api_backend()
        http_error = urllib.error.HTTPError(
            "https://example.com", 500, "Server error", {}, None,
        )
        http_error.read = Mock(return_value=b"internal error")

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(LLMInvokeError) as exc:
                llm_invoke("prompt", backend=backend, max_attempts=1)
        assert exc.value.retryable is True

    def test_anthropic_429_retryable(self):
        backend = _make_api_backend(fmt="anthropic")
        http_error = urllib.error.HTTPError(
            "https://example.com", 429, "Rate limited", {}, None,
        )
        http_error.read = Mock(return_value=b"rate limit exceeded")

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(LLMInvokeError) as exc:
                llm_invoke("prompt", backend=backend, max_attempts=1)
        assert exc.value.retryable is True

    def test_anthropic_401_non_retryable(self):
        backend = _make_api_backend(fmt="anthropic")
        http_error = urllib.error.HTTPError(
            "https://example.com", 401, "Unauthorized", {}, None,
        )
        http_error.read = Mock(return_value=b"bad key")

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(LLMInvokeError) as exc:
                llm_invoke("prompt", backend=backend)
        assert exc.value.retryable is False

    def test_openai_urlerror_retryable(self):
        backend = _make_api_backend()
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("conn refused")):
            with pytest.raises(LLMInvokeError) as exc:
                llm_invoke("prompt", backend=backend, max_attempts=1)
        assert exc.value.retryable is True


class TestBodyDetectionWiring:
    """_check_body_error called in _invoke_openai before content extraction."""

    def test_zhipu_body_error_before_content(self):
        """Zhipu error.code in body raises before content extraction."""
        backend = _make_api_backend(name="zhipu")
        resp = Mock()
        resp.read.return_value = json.dumps({
            "error": {"code": "1113", "message": "balance low"},
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError) as exc:
                llm_invoke("prompt", backend=backend)
        assert exc.value.retryable is False
        assert "1113" in str(exc.value)

    def test_minimax_base_resp_before_content(self):
        """MiniMax base_resp error in body raises before content extraction."""
        backend = _make_api_backend(name="minimax")
        resp = Mock()
        resp.read.return_value = json.dumps({
            "base_resp": {"status_code": 1008, "status_msg": "no balance"},
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError) as exc:
                llm_invoke("prompt", backend=backend)
        assert exc.value.retryable is False


class TestRetryLoop:
    """_invoke_api retry loop with backoff, jitter, Retry-After, stderr."""

    def test_retry_429_then_success(self):
        """429 twice then success returns LLMResult."""
        backend = _make_api_backend()
        http_error = urllib.error.HTTPError(
            "https://example.com", 429, "Rate limited", {}, None,
        )
        http_error.read = Mock(return_value=b"rate limit")

        ok_resp = _mock_ok_response()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                err = urllib.error.HTTPError(
                    "https://example.com", 429, "Rate limited", {}, None,
                )
                err.read = Mock(return_value=b"rate limit")
                raise err
            return ok_resp

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=side_effect), \
             patch("time.sleep"):
            result = llm_invoke("prompt", backend=backend,
                                max_attempts=5, initial_delay_s=0.01)
        assert isinstance(result, LLMResult)

    def test_402_no_retry(self):
        """402 raises immediately without retrying."""
        backend = _make_api_backend()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            err = urllib.error.HTTPError(
                "https://example.com", 402, "Payment required", {}, None,
            )
            err.read = Mock(return_value=b"balance exhausted")
            raise err

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=side_effect), \
             patch("time.sleep") as mock_sleep:
            with pytest.raises(LLMInvokeError) as exc:
                llm_invoke("prompt", backend=backend,
                            max_attempts=5, initial_delay_s=0.01)
        assert exc.value.retryable is False
        assert call_count[0] == 1
        mock_sleep.assert_not_called()

    def test_exhaustion_raises(self):
        """After max_attempts exhausted, raises LLMInvokeError."""
        backend = _make_api_backend()

        def side_effect(*args, **kwargs):
            err = urllib.error.HTTPError(
                "https://example.com", 429, "Rate limited", {}, None,
            )
            err.read = Mock(return_value=b"rate limit")
            raise err

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=side_effect), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="429"):
                llm_invoke("prompt", backend=backend,
                            max_attempts=3, initial_delay_s=0.01)

    def test_stderr_progress(self):
        """Retry progress printed to stderr."""
        backend = _make_api_backend()
        ok_resp = _mock_ok_response()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                err = urllib.error.HTTPError(
                    "https://example.com", 429, "Rate limited", {}, None,
                )
                err.read = Mock(return_value=b"rate limit")
                raise err
            return ok_resp

        import io
        stderr_capture = io.StringIO()
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=side_effect), \
             patch("time.sleep"), \
             patch("sys.stderr", stderr_capture):
            llm_invoke("prompt", backend=backend,
                        max_attempts=3, initial_delay_s=0.01)
        output = stderr_capture.getvalue()
        assert "code-forge: retrying" in output
        assert "2/3" in output

    def test_timeout_not_retried_raises_immediately(self):
        """TimeoutError is non-retryable -- single attempt, no sleep."""
        backend = _make_api_backend()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            raise TimeoutError("read timed out")

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=side_effect), \
             patch("time.sleep") as mock_sleep:
            with pytest.raises(LLMInvokeError, match="timed out"):
                llm_invoke("prompt", backend=backend,
                           max_attempts=3, initial_delay_s=0.01)
        assert call_count[0] == 1
        mock_sleep.assert_not_called()

    def test_max_attempts_1_no_retry(self):
        """max_attempts=1 means no retry on 429."""
        backend = _make_api_backend()

        def side_effect(*args, **kwargs):
            err = urllib.error.HTTPError(
                "https://example.com", 429, "Rate limited", {}, None,
            )
            err.read = Mock(return_value=b"rate limit")
            raise err

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=side_effect), \
             patch("time.sleep") as mock_sleep:
            with pytest.raises(LLMInvokeError):
                llm_invoke("prompt", backend=backend,
                            max_attempts=1, initial_delay_s=0.01)
        mock_sleep.assert_not_called()

    def test_retry_after_overrides_computed_delay(self):
        """Retry-After header value used when larger than computed delay."""
        backend = _make_api_backend()
        ok_resp = _mock_ok_response()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                err = urllib.error.HTTPError(
                    "https://example.com", 429, "Rate limited",
                    {"Retry-After": "10"}, None,
                )
                err.read = Mock(return_value=b"rate limit")
                raise err
            return ok_resp

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=side_effect), \
             patch("time.sleep") as mock_sleep:
            llm_invoke("prompt", backend=backend,
                        max_attempts=3, initial_delay_s=0.01)
        # Retry-After=10 should override computed delay (0.01 * 2^0 + jitter)
        actual_delay = mock_sleep.call_args[0][0]
        assert actual_delay >= 10.0

    def test_llm_invoke_forwards_retry_kwargs(self):
        """llm_invoke passes max_attempts and initial_delay_s to _invoke_api."""
        backend = _make_api_backend()
        ok_resp = _mock_ok_response()

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", return_value=ok_resp):
            # Should not raise - just verifies kwargs are accepted
            result = llm_invoke("prompt", backend=backend,
                                max_attempts=2, initial_delay_s=1.0)
        assert isinstance(result, LLMResult)

    def test_backoff_capped_at_max(self):
        """Computed backoff never exceeds MAX_BACKOFF_S regardless of config."""
        from code_forge.llm_invoke import MAX_BACKOFF_S
        backend = _make_api_backend()
        recorded_delays = []

        def side_effect(*args, **kwargs):
            err = urllib.error.HTTPError(
                "https://example.com", 429, "Rate limited", {}, None,
            )
            err.read = Mock(return_value=b"rate limit")
            raise err

        def record_sleep(delay):
            recorded_delays.append(delay)

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=side_effect), \
             patch("time.sleep", side_effect=record_sleep):
            with pytest.raises(LLMInvokeError):
                llm_invoke("prompt", backend=backend,
                           max_attempts=10, initial_delay_s=30.0)

        assert len(recorded_delays) == 9  # 10 attempts, 9 sleeps
        for delay in recorded_delays:
            assert delay <= MAX_BACKOFF_S + 0.5  # +0.5 for jitter ceiling
        # Jitter is added after min(computed, MAX_BACKOFF_S), so delays can
        # exceed MAX_BACKOFF_S by up to 0.5s.
        capped = [d for d in recorded_delays if d > MAX_BACKOFF_S]
        assert len(capped) > 0, "jitter after cap should produce delays > MAX_BACKOFF_S"

    def test_max_attempts_zero_raises_value_error(self):
        """max_attempts < 1 raises ValueError before any network call."""
        backend = _make_api_backend()
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}):
            with pytest.raises(ValueError, match="max_attempts must be >= 1"):
                llm_invoke("prompt", backend=backend, max_attempts=0)

    def test_negative_initial_delay_raises_value_error(self):
        """initial_delay_s < 0 raises ValueError before any network call."""
        backend = _make_api_backend()
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}):
            with pytest.raises(ValueError, match="initial_delay_s must be non-negative"):
                llm_invoke("prompt", backend=backend, initial_delay_s=-1.0)


# -- Wave 3: _apply_params body mapping tests -------------------------

from code_forge.llm_invoke import _apply_params


def _cfg(**kw):
    """Shortcut to build a BackendConfig with provider fields."""
    defaults = dict(
        name="t", type="api", model="m", format="openai",
        base_url="http://x", api_key_env="K",
    )
    defaults.update(kw)
    return BackendConfig(**defaults)


class TestApplyParams:
    """Unit tests for _apply_params shared helper."""

    def test_unconfigured_openai_temperature_zero(self):
        body = {"model": "m", "messages": []}
        _apply_params(body, _cfg(), outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True,
                      default_temperature=0.0)
        assert body["temperature"] == 0

    def test_unconfigured_anthropic_no_temperature(self):
        body = {"model": "m", "messages": []}
        _apply_params(body, _cfg(), outcap_key="max_tokens",
                      allow_thinking=True, allow_effort=False)
        assert "temperature" not in body

    def test_configured_temperature(self):
        body = {"model": "m", "messages": []}
        _apply_params(body, _cfg(temperature=0.7),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True,
                      default_temperature=0.0)
        assert body["temperature"] == 0.7

    def test_temperature_sentinel_uses_format_default(self):
        """Sentinel -1 falls to default_temperature (openai=0.0)."""
        body = {"model": "m", "messages": []}
        _apply_params(body, _cfg(temperature=-1.0),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True,
                      default_temperature=0.0)
        assert body["temperature"] == 0.0

    def test_temperature_omitted_when_both_negative(self):
        """Both sentinel and default -1 -> no temperature key."""
        body = {"model": "m", "messages": []}
        _apply_params(body, _cfg(temperature=-1.0),
                      outcap_key="max_tokens",
                      allow_thinking=True, allow_effort=False)
        assert "temperature" not in body

    def test_single_cap_key_openai_default(self):
        body = {}
        _apply_params(body, _cfg(max_completion_tokens=32768),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True)
        assert body["max_completion_tokens"] == 32768
        assert "max_tokens" not in body

    def test_single_cap_key_deepseek_outcap(self):
        body = {}
        _apply_params(body, _cfg(outcap_key="max_tokens", max_tokens=32768),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True)
        assert body["max_tokens"] == 32768
        assert "max_completion_tokens" not in body

    def test_cap_fallback_to_max_tokens_field_selects(self):
        """openai: mct=0 + max_tokens set -> key is max_tokens (field-derived)."""
        body = {}
        _apply_params(body, _cfg(max_completion_tokens=0, max_tokens=8192),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True,
                      field_selects_key=True)
        assert body["max_tokens"] == 8192
        assert "max_completion_tokens" not in body

    def test_anthropic_pin_maps_mct_to_max_tokens(self):
        """anthropic: mct field set -> value mapped onto max_tokens key."""
        body = {}
        _apply_params(body, _cfg(max_completion_tokens=32768),
                      outcap_key="max_tokens",
                      allow_thinking=True, allow_effort=False)
        assert body["max_tokens"] == 32768
        assert "max_completion_tokens" not in body

    def test_thinking_type_enabled_with_budget(self):
        body = {}
        _apply_params(body, _cfg(thinking_type="enabled",
                                 thinking_budget=16000),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True)
        assert body["thinking"] == {"type": "enabled",
                                    "budget_tokens": 16000}

    def test_thinking_type_without_budget(self):
        body = {}
        _apply_params(body, _cfg(thinking_type="enabled"),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True)
        assert body["thinking"] == {"type": "enabled"}

    def test_no_thinking_when_type_empty(self):
        body = {}
        _apply_params(body, _cfg(),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True)
        assert "thinking" not in body

    def test_effort_openai_top_level(self):
        body = {}
        _apply_params(body, _cfg(reasoning_effort="high"),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True)
        assert body["reasoning_effort"] == "high"
        assert "output_config" not in body

    def test_effort_vertex_nested(self):
        body = {}
        _apply_params(body, _cfg(reasoning_effort="high"),
                      outcap_key="max_tokens",
                      allow_thinking=True,
                      allow_effort="output_config")
        assert body["output_config"] == {"effort": "high"}
        assert "reasoning_effort" not in body

    def test_effort_anthropic_skipped(self):
        body = {}
        _apply_params(body, _cfg(reasoning_effort="high"),
                      outcap_key="max_tokens",
                      allow_thinking=True, allow_effort=False)
        assert "reasoning_effort" not in body
        assert "output_config" not in body

    def test_stream_true(self):
        body = {}
        _apply_params(body, _cfg(stream=True),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True)
        assert body["stream"] is True

    def test_params_passthrough(self):
        body = {}
        _apply_params(body, _cfg(params={"top_p": 0.9}),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True)
        assert body["top_p"] == 0.9


class TestPerBackendTimeout:
    """Per-backend timeout_s overrides caller default."""

    def test_backend_timeout_overrides_caller(self):
        backend = _cfg(timeout_s=1800)
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}):
            with patch("code_forge.llm_invoke._invoke_api") as m:
                m.return_value = Mock(content="{}", usage={},
                                     duration_s=1.0)
                llm_invoke("p", backend=backend, timeout_s=120)
                _, kwargs = m.call_args
                assert kwargs.get("timeout_s", m.call_args[0][2]) == 1800

    def test_backend_timeout_zero_uses_default(self):
        backend = _cfg(timeout_s=0)
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}):
            with patch("code_forge.llm_invoke._invoke_api") as m:
                m.return_value = Mock(content="{}", usage={},
                                     duration_s=1.0)
                llm_invoke("p", backend=backend)
                _, kwargs = m.call_args
                called_timeout = kwargs.get(
                    "timeout_s", m.call_args[0][2]
                )
                assert called_timeout > 0


# -- Wave 4: SSE streaming tests --------------------------------------

from code_forge.llm_invoke import _read_sse
from code_forge.errors import CliError


def _sse_lines(*chunks):
    """Build fake SSE response lines (bytes iterator)."""
    lines = []
    for c in chunks:
        lines.append(("data: " + json.dumps(c) + "\n").encode())
    lines.append(b"data: [DONE]\n")
    return iter(lines)


class TestReadSSE:
    """Unit tests for SSE stream reassembly."""

    def test_assembles_content(self):
        resp = _sse_lines(
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
        )
        result = _read_sse(resp)
        assert result["choices"][0]["message"]["content"] == "Hello world"

    def test_message_shape_not_delta(self):
        """Assembled response uses message.content, not delta."""
        resp = _sse_lines(
            {"choices": [{"delta": {"content": "x"}}]},
        )
        result = _read_sse(resp)
        assert "message" in result["choices"][0]
        assert "delta" not in result["choices"][0]

    def test_reasoning_content_discarded(self):
        """reasoning_content from thinking is not in assembled content."""
        resp = _sse_lines(
            {"choices": [{"delta": {"reasoning_content": "think..."}}]},
            {"choices": [{"delta": {"content": "answer"}}]},
        )
        result = _read_sse(resp)
        assert result["choices"][0]["message"]["content"] == "answer"

    def test_empty_delta_no_crash(self):
        """Delta without content key -> empty string, no crash."""
        resp = _sse_lines(
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": "ok"}}]},
        )
        result = _read_sse(resp)
        assert result["choices"][0]["message"]["content"] == "ok"

    def test_error_only_chunk_no_crash(self):
        """Error chunk without choices -> returned for _check_body_error."""
        resp = _sse_lines(
            {"error": {"message": "rate limit", "code": 429}},
        )
        result = _read_sse(resp)
        assert "error" in result

    def test_stream_on_anthropic_raises(self):
        from code_forge.llm_invoke import _invoke_anthropic

        backend = BackendConfig(
            name="mm", type="api", model="m", format="anthropic",
            base_url="http://x", api_key_env="K", stream=True,
        )
        # Call the real function: the guard fires before any network I/O.
        with pytest.raises(CliError, match="streaming not supported"):
            _invoke_anthropic("p", backend, api_key="k", timeout_s=1)

    def test_stream_on_vertex_raises(self):
        from code_forge.llm_invoke import _invoke_vertex

        backend = BackendConfig(
            name="v", type="api", model="m", format="vertex",
            base_url=None, api_key_env=None,
            project_id="p", stream=True,
        )
        with pytest.raises(CliError, match="streaming not supported"):
            _invoke_vertex("p", backend, timeout_s=1)


# -- Wave 5: CLI backend env tests ------------------------------------


class TestCliEnv:
    """Verify Popen env= respects env_unset and env_set."""

    def _cli_backend(self, **kw):
        return BackendConfig(
            name="local", type="cli", model="m", command="echo",
            **kw,
        )

    @patch("shutil.which", return_value="/usr/bin/echo")
    @patch("subprocess.Popen")
    def test_no_env_popen_env_none(self, mock_popen, _):
        """No env fields -> Popen env=None (inherits parent env)."""
        mock_proc = Mock()
        mock_proc.communicate.return_value = ('{"result": "ok"}', "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        backend = self._cli_backend()
        from code_forge.llm_invoke import _invoke_cli
        _invoke_cli("test", backend, 120)
        _, kwargs = mock_popen.call_args
        assert kwargs.get("env") is None

    @patch("shutil.which", return_value="/usr/bin/echo")
    @patch("subprocess.Popen")
    def test_env_unset_removes_key(self, mock_popen, _):
        """env_unset removes the named key from child env."""
        mock_proc = Mock()
        mock_proc.communicate.return_value = ('{"result": "ok"}', "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        backend = self._cli_backend(env_unset=("SECRET_KEY",))
        with patch.dict(os.environ, {"SECRET_KEY": "s3cr3t", "PATH": "/bin"}):
            from code_forge.llm_invoke import _invoke_cli
            _invoke_cli("test", backend, 120)
        _, kwargs = mock_popen.call_args
        child_env = kwargs["env"]
        assert isinstance(child_env, dict)
        assert "SECRET_KEY" not in child_env
        assert "PATH" in child_env

    @patch("shutil.which", return_value="/usr/bin/echo")
    @patch("subprocess.Popen")
    def test_env_set_adds_key(self, mock_popen, _):
        """env_set adds the named key=value to child env."""
        mock_proc = Mock()
        mock_proc.communicate.return_value = ('{"result": "ok"}', "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        backend = self._cli_backend(env_set=(("MY_VAR", "hello"),))
        from code_forge.llm_invoke import _invoke_cli
        _invoke_cli("test", backend, 120)
        _, kwargs = mock_popen.call_args
        assert kwargs["env"]["MY_VAR"] == "hello"

    @patch("shutil.which", return_value="/usr/bin/echo")
    @patch("subprocess.Popen")
    def test_env_unset_absent_var_no_crash(self, mock_popen, _):
        """Unsetting a var not in env -> silently skipped."""
        mock_proc = Mock()
        mock_proc.communicate.return_value = ('{"result": "ok"}', "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc
        backend = self._cli_backend(env_unset=("NONEXISTENT",))
        from code_forge.llm_invoke import _invoke_cli
        _invoke_cli("test", backend, 120)


@pytest.mark.asyncio
class TestInvokeSampling:
    async def test_invoke_sampling_success(self):
        from code_forge.llm_invoke import invoke_sampling, LLMResult, Usage
        from mcp.types import CreateMessageResult, TextContent, SamplingMessage
        from unittest.mock import AsyncMock, MagicMock
        
        session = MagicMock()
        session.create_message = AsyncMock()
        session.create_message.return_value = CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text='{"findings": [], "code_excerpts": []}'),
            model="test-model",
            stopReason="endTurn",
        )
        
        res = await invoke_sampling(session, prompt="test prompt")
        assert res.is_truncated is False
        assert res.usage == Usage(0, 0)
        assert res.content == {"findings": [], "code_excerpts": []}

    async def test_invoke_sampling_truncation_raises(self):
        from code_forge.llm_invoke import invoke_sampling, LLMInvokeError
        from mcp.types import CreateMessageResult, TextContent
        from unittest.mock import AsyncMock, MagicMock

        session = MagicMock()
        session.create_message = AsyncMock()
        session.create_message.return_value = CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text='{"findings": []}'),
            model="test-model",
            stopReason="maxTokens",
        )

        with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
            await invoke_sampling(session, prompt="test prompt")
        assert exc_info.value.retryable is False

    async def test_invoke_sampling_json_parse_fallback(self):
        from code_forge.llm_invoke import invoke_sampling
        from mcp.types import CreateMessageResult, TextContent
        from unittest.mock import AsyncMock, MagicMock
        
        session = MagicMock()
        session.create_message = AsyncMock()
        session.create_message.return_value = CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text='Here is the review: ```json\n{"findings": []}\n```'),
            model="test-model",
            stopReason="endTurn",
        )
        
        res = await invoke_sampling(session, prompt="test prompt")
        assert res.content == {"findings": []}

    async def test_invoke_sampling_non_text_content(self):
        from code_forge.llm_invoke import invoke_sampling, LLMInvokeError
        from mcp.types import CreateMessageResult, ImageContent
        from unittest.mock import AsyncMock, MagicMock
        
        session = MagicMock()
        session.create_message = AsyncMock()
        session.create_message.return_value = CreateMessageResult(
            role="assistant",
            content=ImageContent(type="image", data="base64==", mimeType="image/png"),
            model="test-model",
            stopReason="endTurn",
        )
        
        with pytest.raises(LLMInvokeError, match="sampling response contains no valid JSON"):
            await invoke_sampling(session, prompt="test prompt")

    async def test_invoke_sampling_model_hint(self):
        from code_forge.llm_invoke import invoke_sampling
        from mcp.types import CreateMessageResult, TextContent
        from unittest.mock import AsyncMock, MagicMock
        
        session = MagicMock()
        session.create_message = AsyncMock()
        session.create_message.return_value = CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text='{"findings": []}'),
            model="test-model",
            stopReason="endTurn",
        )
        
        await invoke_sampling(session, prompt="test prompt", model_hint="claude-sonnet")
        kwargs = session.create_message.call_args[1]
        assert "model_preferences" in kwargs
        hints = kwargs["model_preferences"].hints
        assert any(hint.name == "claude-sonnet" for hint in hints)

    async def test_invoke_sampling_empty_response_raises(self):
        """TEST-2: empty TextContent raises LLMInvokeError with 'empty'."""
        from code_forge.llm_invoke import invoke_sampling, LLMInvokeError
        from mcp.types import CreateMessageResult, TextContent
        from unittest.mock import AsyncMock, MagicMock

        session = MagicMock()
        session.create_message = AsyncMock()
        session.create_message.return_value = CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text=""),
            model="copilotcli/auto",
            stopReason="endTurn",
        )

        with pytest.raises(LLMInvokeError, match="empty"):
            await invoke_sampling(session, prompt="test prompt")

    async def test_invoke_sampling_copilotcli_model_raises(self):
        """TEST-3: copilotcli/* model raises LLMInvokeError."""
        from code_forge.llm_invoke import invoke_sampling, LLMInvokeError
        from mcp.types import CreateMessageResult, TextContent
        from unittest.mock import AsyncMock, MagicMock

        session = MagicMock()
        session.create_message = AsyncMock()
        session.create_message.return_value = CreateMessageResult(
            role="assistant",
            content=TextContent(type="text", text='{"findings": []}'),
            model="copilotcli/auto",
            stopReason="endTurn",
        )

        with pytest.raises(LLMInvokeError, match="copilotcli"):
            await invoke_sampling(session, prompt="test prompt")


class TestConnectionErrorHandling:
    """Connection-level OSError (RemoteDisconnected, SSLError) must be caught
    and wrapped as retryable LLMInvokeError, not propagate as a raw crash.

    Tests go through invoke() so the retry loop is exercised end-to-end.
    Each direction is tested on vertex AND openai to prove the fix is
    cross-backend.
    """

    # -- helpers --

    @staticmethod
    def _openai_backend():
        return BackendConfig(
            name="test-oai", type="api", model="m", format="openai",
            base_url="https://example.com", api_key_env="TEST_KEY",
        )

    @staticmethod
    def _vertex_backend():
        return _make_vertex_backend()

    @staticmethod
    def _vertex_auth_patches():
        """Context managers that satisfy vertex google-auth without real creds."""
        mock_creds = MagicMock()
        mock_creds.token = "fake-token"
        return (
            patch(
                "google.oauth2.service_account.Credentials"
                ".from_service_account_file",
                return_value=mock_creds,
            ),
            patch("google.auth.default", return_value=(mock_creds, "proj")),
            patch("google.auth.transport.requests.Request"),
        )

    # -- Direction 1: RemoteDisconnected -> retryable --

    def test_remote_disconnected_openai_retryable(self):
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen",
                   side_effect=http.client.RemoteDisconnected(
                       "Remote end closed connection")), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="connection error") as exc:
                llm_invoke("prompt", backend=self._openai_backend())
            assert exc.value.retryable is True

    def test_remote_disconnected_vertex_retryable(self):
        p1, p2, p3 = self._vertex_auth_patches()
        with p1, p2, p3, \
             patch("urllib.request.urlopen",
                   side_effect=http.client.RemoteDisconnected(
                       "Remote end closed connection")), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="connection error") as exc:
                llm_invoke("prompt", backend=self._vertex_backend())
            assert exc.value.retryable is True

    # -- Direction 2: TimeoutError regression guard --
    # TimeoutError IS an OSError subclass. The new except-OSError must NOT
    # intercept it -- the retry loop's inner except-TimeoutError at line ~791
    # deliberately converts it to retryable=False. If the guard is missing,
    # except-OSError swallows TimeoutError and flips retryable to True.

    def test_timeout_error_openai_still_not_retryable(self):
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen",
                   side_effect=TimeoutError("read timed out")), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="timed out") as exc:
                llm_invoke("prompt", backend=self._openai_backend())
            assert exc.value.retryable is False
            assert exc.value.is_timeout is True

    def test_timeout_error_vertex_still_not_retryable(self):
        p1, p2, p3 = self._vertex_auth_patches()
        with p1, p2, p3, \
             patch("urllib.request.urlopen",
                   side_effect=TimeoutError("read timed out")), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="timed out") as exc:
                llm_invoke("prompt", backend=self._vertex_backend())
            assert exc.value.retryable is False
            assert exc.value.is_timeout is True

    # -- Direction 3: SSLError -> retryable --
    # ssl.SSLError is OSError but NOT ConnectionError. The except-OSError
    # (not except-ConnectionError) is what catches it.

    def test_ssl_error_openai_retryable(self):
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen",
                   side_effect=ssl.SSLError(1, "[SSL] decryption failed")), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="connection error") as exc:
                llm_invoke("prompt", backend=self._openai_backend())
            assert exc.value.retryable is True

    def test_ssl_error_vertex_retryable(self):
        p1, p2, p3 = self._vertex_auth_patches()
        with p1, p2, p3, \
             patch("urllib.request.urlopen",
                   side_effect=ssl.SSLError(1, "[SSL] decryption failed")), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="connection error") as exc:
                llm_invoke("prompt", backend=self._vertex_backend())
            assert exc.value.retryable is True


class TestOutputCeiling:
    """output_ceiling overrides max_tokens as the API output cap.

    Direction 1: ceiling overrides default cap in the request body.
    Direction 2: truncation message names output capacity, not diff size.
    """

    def test_ceiling_overrides_max_tokens_in_request(self):
        """When output_ceiling > 0, the API request uses ceiling as cap."""
        backend = BackendConfig(
            name="test", type="api", model="m", format="openai",
            base_url="https://example.com", api_key_env="TEST_KEY",
            max_tokens=16384, output_ceiling=65536,
        )
        captured_body = {}

        def fake_urlopen(req, timeout=None):
            captured_body.update(json.loads(req.data.decode()))
            resp = Mock()
            resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": '{"findings": []}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }).encode()
            resp.__enter__ = Mock(return_value=resp)
            resp.__exit__ = Mock(return_value=False)
            return resp

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            llm_invoke("prompt", backend=backend)

        # ceiling=65536 overrides max_tokens=16384
        # openai with max_completion_tokens=0 uses "max_tokens" key
        assert captured_body["max_tokens"] == 65536

    def test_no_ceiling_uses_max_tokens(self):
        """When output_ceiling == 0 (default), max_tokens is used."""
        backend = BackendConfig(
            name="test", type="api", model="m", format="openai",
            base_url="https://example.com", api_key_env="TEST_KEY",
            max_tokens=16384, output_ceiling=0,
        )
        captured_body = {}

        def fake_urlopen(req, timeout=None):
            captured_body.update(json.loads(req.data.decode()))
            resp = Mock()
            resp.read.return_value = json.dumps({
                "choices": [{"message": {"content": '{"findings": []}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }).encode()
            resp.__enter__ = Mock(return_value=resp)
            resp.__exit__ = Mock(return_value=False)
            return resp

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            llm_invoke("prompt", backend=backend)

        assert captured_body["max_tokens"] == 16384

    def test_truncation_message_shows_resolved_cap(self):
        """Truncation error shows the actual cap, not backend.max_tokens."""
        backend = BackendConfig(
            name="test", type="api", model="m", format="openai",
            base_url="https://example.com", api_key_env="TEST_KEY",
            max_tokens=16384, output_ceiling=65536,
        )
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "choices": [{
                "message": {"content": "partial"},
                "finish_reason": "length",
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 65536},
        }).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", return_value=mock_response), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="truncated") as exc:
                llm_invoke("prompt", backend=backend)
            msg = str(exc.value)
            assert "output capacity (65536 tokens)" in msg
            assert "reduce diff size" not in msg.lower()
            assert exc.value.kind == "truncated"

    def test_truncation_message_no_reduce_diff_size_anthropic(self):
        """Anthropic truncation also uses new message format."""
        backend = BackendConfig(
            name="test", type="api", model="m", format="anthropic",
            base_url="https://example.com", api_key_env="TEST_KEY",
            max_tokens=8192, output_ceiling=0,
        )
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "content": [{"type": "text", "text": "partial"}],
            "usage": {"input_tokens": 100, "output_tokens": 8192},
            "stop_reason": "max_tokens",
        }).encode()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", return_value=mock_response), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="truncated") as exc:
                llm_invoke("prompt", backend=backend)
            msg = str(exc.value)
            assert "output capacity (8192 tokens)" in msg
            assert "reduce diff size" not in msg.lower()
