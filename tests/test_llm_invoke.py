import http.client
import json
import re
import os
import ssl
import subprocess
import threading
import time
import urllib.error
from unittest.mock import patch, MagicMock, Mock

import pytest

from code_forge.llm_invoke import (
    llm_invoke,
    LLMInvokeError,
    LLMResult,
    Usage,
    _read_with_deadline,
)
from code_forge.backend import BackendConfig, DEFAULT_BACKEND


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


class TestReadWithDeadlineIdle:
    """The idle bound catches a connection that stops producing bytes,
    which the caller's slow-pass timeout cannot: a non-streaming backend
    generates the whole answer before the first body byte, so a slow
    pass and a hung connection look identical to the socket until one
    of them is much too late."""

    @staticmethod
    def _fake_response(read_side_effect=None):
        resp = MagicMock()
        if read_side_effect is not None:
            resp.read.side_effect = read_side_effect
        return resp

    def test_installs_idle_timeout_on_socket(self):
        resp = self._fake_response()
        _read_with_deadline(resp, time.monotonic() + 30, "test-backend")
        # The bound is clamped to the remaining deadline, so a path
        # whose total budget is already tighter than the idle window
        # keeps the deadline as the guard.
        resp.fp.raw._sock.settimeout.assert_called_once()
        installed = resp.fp.raw._sock.settimeout.call_args[0][0]
        assert 29.9 <= installed <= 30.0

    def test_silent_socket_raises_llm_invoke_error(self):
        resp = self._fake_response(read_side_effect=TimeoutError("timed out"))
        with pytest.raises(LLMInvokeError, match="went silent") as exc:
            _read_with_deadline(resp, time.monotonic() + 30, "test-backend")
        assert exc.value.is_timeout is True
        # The message reports the bound actually installed (clamped to
        # the remaining deadline), not the module constant.
        assert re.search(r"for \d+s", str(exc.value))
        # Clamped scenario: the remaining deadline (~30s) is far below
        # the idle constant, so the message must not report 900.
        assert "for 900s" not in str(exc.value)

    def test_other_read_errors_pass_through(self):
        resp = self._fake_response(read_side_effect=OSError("reset"))
        with pytest.raises(OSError):
            _read_with_deadline(resp, time.monotonic() + 30, "test-backend")


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
                llm_invoke("prompt", backend=DEFAULT_BACKEND, timeout_s=120)
            assert exc.value.is_timeout is True

    def test_raises_on_nonzero_exit(self):
        mock_proc = _make_mock_proc(returncode=1, stderr="error: rate limited")

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(LLMInvokeError, match="exited with code 1"):
                llm_invoke("prompt", backend=DEFAULT_BACKEND)

    def test_raises_on_invalid_json(self):
        mock_proc = _make_mock_proc(stdout="not json at all")

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            with pytest.raises(LLMInvokeError, match="non-JSON"):
                llm_invoke("prompt", backend=DEFAULT_BACKEND)

    def test_raises_when_claude_not_found(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(LLMInvokeError, match="not found"):
                llm_invoke("prompt", backend=DEFAULT_BACKEND)

    def test_respects_forge_llm_model_env(self):
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch.dict(os.environ, {"FORGE_LLM_MODEL": "opus-4-7"}):
            llm_invoke("prompt", backend=DEFAULT_BACKEND)
            cmd = mock_popen.call_args[0][0]
            assert "opus-4-7" in cmd

    def test_large_prompt_uses_shell_command(self):
        large_prompt = "x" * 1_100_000
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen:
            result = llm_invoke(large_prompt, backend=DEFAULT_BACKEND)
        assert result.content == {"ok": True}
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "sh"
        assert cmd[1] == "-c"

    def test_strips_markdown_fences(self):
        mock_proc = _make_mock_proc(stdout='```json\n{"key": "value"}\n```')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            result = llm_invoke("prompt", backend=DEFAULT_BACKEND)
        assert result.content == {"key": "value"}

    def test_raises_on_oserror(self):
        with patch(
            "code_forge.llm_invoke.subprocess.Popen",
            side_effect=OSError("No such file or directory"),
        ):
            with pytest.raises(LLMInvokeError, match="subprocess failed"):
                llm_invoke("prompt", backend=DEFAULT_BACKEND)

    def test_cli_dispatch_explicit_default_backend(self):
        """An explicitly passed DEFAULT_BACKEND still dispatches to the
        cli path -- distinct from backend=None, which now raises rather
        than falling through to this same backend implicitly."""
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc) as mock_popen:
            result = llm_invoke("prompt", backend=DEFAULT_BACKEND)
        assert result.content == {"ok": True}
        cmd = mock_popen.call_args[0][0]
        assert "claude" in cmd[0]

    def test_backend_none_raises_instead_of_implicit_fallthrough(self):
        """backend=None must fail closed, not silently spawn the old
        implicit claude -p subprocess via DEFAULT_BACKEND."""
        with pytest.raises(LLMInvokeError, match="no backend"):
            llm_invoke("prompt", backend=None)

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
            llm_invoke("prompt", backend=DEFAULT_BACKEND)
        kwargs = mock_popen.call_args[1]
        assert kwargs.get("start_new_session") is True

    def test_cli_usage_zero_when_no_envelope(self):
        """Direct JSON response (no Claude CLI envelope) returns Usage(0, 0)."""
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            result = llm_invoke("prompt", backend=DEFAULT_BACKEND)
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
            result = llm_invoke("prompt", backend=DEFAULT_BACKEND)
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
            result = llm_invoke("prompt", backend=DEFAULT_BACKEND)

        assert result.content == {"surfaces": ["nftables"], "findings": []}
        assert result.usage.input_tokens == 100
        assert result.usage.output_tokens == 50

    def test_cli_streaming_array_no_result_event_returns_list(self):
        """Streaming array with no result event falls back to list content."""
        events = [{"type": "system"}, {"type": "thinking"}]
        mock_proc = _make_mock_proc(stdout=json.dumps(events))

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            result = llm_invoke("prompt", backend=DEFAULT_BACKEND)

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

    def test_a_configured_header_reaches_the_request(self):
        """Some gateways take per-request options as headers.

        Nothing else in the backend config can express one, so a backend
        that needs it has no way to be configured at all without this.
        """
        backend = BackendConfig(
            name="deepseek",
            type="api",
            model="deepseek-chat",
            format="openai",
            base_url="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
            headers={"x-omniroute-compression": "off"},
        )
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"result": "pass"}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode("utf-8")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}), \
             patch("urllib.request.urlopen",
                   return_value=mock_response) as mock_urlopen:
            llm_invoke("prompt", backend=backend)

        req = mock_urlopen.call_args[0][0]
        # urllib capitalises the first letter of every name it stores.
        folded = {k.lower(): v for k, v in req.headers.items()}
        assert folded.get("x-omniroute-compression") == "off"
        assert folded.get("authorization") == "Bearer sk-test"

    def test_anthropic_carries_a_configured_header(self):
        """Each format builds its own header dict, so each needs wiring.

        This one is not a copy of the openai case for the sake of
        symmetry. Removing the merge from this call site alone broke no
        test until this existed, while the same removal at either of the
        other two was caught -- three sites that looked equally covered
        were not, and only injecting at each separately said so.

        Its base headers are also the odd ones out: the credential is
        x-api-key rather than Authorization, and anthropic-version is
        protocol framing with no equivalent elsewhere.
        """
        backend = BackendConfig(
            name="claude-api",
            type="api",
            model="claude-sonnet-4-20250514",
            format="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
            headers={"x-omniroute-compression": "off"},
        )
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "content": [{"text": '{"result": "pass"}'}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }).encode("utf-8")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
             patch("urllib.request.urlopen",
                   return_value=mock_response) as mock_urlopen:
            llm_invoke("prompt", backend=backend)

        req = mock_urlopen.call_args[0][0]
        folded = {k.lower(): v for k, v in req.headers.items()}
        assert folded.get("x-omniroute-compression") == "off"
        assert folded.get("x-api-key") == "sk-ant-test"
        assert folded.get("anthropic-version") == "2023-06-01"

    def test_every_header_a_call_site_sends_is_a_protected_name(self):
        """The premise the collision assertion rests on.

        Every name a call site puts in `base` has to be one the config
        parser already refuses. When that holds, a configured header can
        never collide, and the assertion below is unreachable -- which
        is the state we want it in.

        Read off the real call sites rather than typed out here, because
        a hand-copied list stops tracking the code the moment a format
        gains a header, which is the exact event this is here to catch.
        """
        import inspect
        import re
        from code_forge import llm_invoke as mod
        from code_forge.backend import is_protected_header

        src = inspect.getsource(mod)
        blocks = re.findall(r"_request_headers\(\{(.*?)\}", src, re.S)
        assert len(blocks) >= 3, (
            "found %d _request_headers call sites, expected the three "
            "format dispatchers -- if a call site changed shape this "
            "test stopped reading it" % len(blocks)
        )

        names = {n for b in blocks for n in re.findall(r'"([^"]+)":', b)}
        assert "Content-Type" in names, (
            "the regex matched %r, which does not look like header "
            "names -- it is reading the wrong thing" % sorted(names)
        )

        unlisted = sorted(n for n in names if not is_protected_header(n))
        assert not unlisted, (
            "call sites send %s, which PROTECTED_HEADER_KEYS does not "
            "list. A backend could configure one, config load would "
            "accept it, and the wire would overwrite it. Add it to the "
            "list." % unlisted
        )

    def test_a_collision_here_is_reported_not_absorbed(self):
        """The assertion itself, forced to fire.

        It cannot fire through any real call site -- the test above is
        what keeps that true. So the collision is staged here with a
        base header the protected list does not know, which is exactly
        the situation the assertion exists to announce: a format that
        gained a header nobody added to the list.

        It has to be loud. Dropping the configured value would leave
        the config saying one thing and the wire doing another, and
        would discard the only signal that the list needs updating.
        """
        from code_forge.llm_invoke import _request_headers, LLMInvokeError
        from code_forge.backend import is_protected_header

        backend = BackendConfig(
            name="deepseek",
            type="api",
            model="deepseek-chat",
            format="openai",
            base_url="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
            headers={
                "x-tenant-id": "acme",
                "x-omniroute-compression": "off",
            },
        )
        # Stands in for a header some future format sends and nobody
        # listed. Asserted unlisted, so that the day it IS listed this
        # test says so instead of passing for the wrong reason.
        base = {
            "Authorization": "Bearer sk-real",
            "x-tenant-id": "forge",
        }
        assert not is_protected_header("x-tenant-id"), (
            "x-tenant-id became a protected name, so check_headers now "
            "refuses it first and this no longer reaches the assertion"
        )

        with pytest.raises(LLMInvokeError) as exc:
            _request_headers(base, backend)

        msg = str(exc.value)
        assert "deepseek" in msg
        assert "'x-tenant-id'" in msg
        assert "PROTECTED_HEADER_KEYS" in msg, (
            "whoever fixes forge needs to be told which list is short"
        )
        assert exc.value.retryable is False, (
            "a gate.yaml does not change between attempts, so retrying "
            "this only spends the backoff budget on the same answer"
        )

    def test_a_collision_is_not_retried(self):
        """The flag above is only worth asserting if the loop honours it.

        Measured before the flag was set: five attempts over 31.5s of
        exponential backoff, and the same unfixable line logged four
        times, for every pass of every review. LLMInvokeError defaults
        to retryable, so this is what omitting one keyword buys.

        The sleep is mocked, which is not only for speed. What a
        non-retryable raise means is that the loop never reaches its
        backoff at all, so "no sleep was requested" says the thing
        directly, where a count of attempts only implies it. Left
        unmocked, this test would still catch the regression -- after
        sitting through the whole 31.5s to do it.
        """
        from code_forge.llm_invoke import LLMInvokeError
        import code_forge.llm_invoke as mod

        backend = BackendConfig(
            name="deepseek",
            type="api",
            model="deepseek-chat",
            format="openai",
            base_url="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
            # Constructed straight, skipping the parser that refuses
            # this name -- which is what check_headers then catches on
            # the way out. Any of its refusals would do; what is being
            # pinned is that the loop does not retry one.
            headers={"content-type": "text/plain"},
        )

        # Spy, not stand-in: the real _request_headers has to be the one
        # that raises, or this would still pass with the flag deleted
        # from it. It raises before any socket is touched, so the real
        # _invoke_openai can run and no network mock is needed.
        real = mod._request_headers
        attempts = []
        slept = []

        def counting(base, be):
            attempts.append(1)
            return real(base, be)

        with patch.object(mod, "_request_headers", counting), \
             patch.object(mod.time, "sleep", slept.append), \
             patch.dict(os.environ, {"DEEPSEEK_API_KEY": "k"}):
            with pytest.raises(LLMInvokeError, match="forge controls"):
                mod.llm_invoke("prompt", backend, timeout_s=5)

        assert slept == [], (
            "the loop backed off %r before giving the same answer" % (slept,)
        )
        assert len(attempts) == 1, (
            "a config error was attempted %d times" % len(attempts)
        )

    def test_a_header_of_its_own_still_gets_through(self):
        """A header that breaks no rule has to survive all of them.

        Without this, a check that refused everything would satisfy
        every refusal test above and take the whole feature with it.
        """
        from code_forge.llm_invoke import _request_headers

        backend = BackendConfig(
            name="deepseek",
            type="api",
            model="deepseek-chat",
            format="openai",
            base_url="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
            headers={"x-omniroute-compression": "off"},
        )
        base = {
            "Authorization": "Bearer sk-real",
            "Content-Type": "application/json",
        }

        merged = _request_headers(base, backend)

        assert merged["x-omniroute-compression"] == "off"
        assert merged["Authorization"] == "Bearer sk-real"
        assert merged["Content-Type"] == "application/json"

    def test_headers_that_are_not_a_mapping_fail_as_a_config_error(self):
        """A code-built backend skips the parser's type check.

        Without the guard inside check_headers, .items() raises
        AttributeError -- naming neither the backend nor the field, from
        inside the retry loop, where the default is to retry. The type
        has to be reported the way every other bad header is.
        """
        from code_forge.llm_invoke import _request_headers

        # Both halves of the truth table. The first three are truthy and
        # were all this test originally covered; the empty containers are
        # falsy, and an earlier `backend.headers or {}` sent them down
        # the no-headers branch, skipping the very check this asserts.
        # Picking only non-empty wrong types is the natural mistake --
        # "wrong type" brings examples with content to mind.
        for bad in ([("X-A", "v")], "X-A: v", 42,
                    [], (), "", False, 0):
            backend = BackendConfig(
                name="built-in-code",
                type="api",
                model="m",
                format="openai",
                base_url="https://x/v1",
                api_key_env="K",
                headers=bad,
            )
            with pytest.raises(LLMInvokeError, match="must be a mapping"):
                _request_headers({"Authorization": "Bearer k"}, backend)

    def test_a_cli_backend_carrying_headers_is_refused_not_ignored(self):
        """A subprocess sends no HTTP, so headers could only be dropped.

        Config load refuses the field on a cli backend by name; a
        backend built in code reaches neither that nor any request.
        Without this the field is discarded in silence -- the one
        outcome the whole feature exists to prevent.

        Empty dict is included deliberately: it is not None, so it must
        be refused too. The guard has to read "not None", not "truthy",
        or it repeats the `or {}` bug this file already pins.
        """
        from code_forge import llm_invoke as mod

        for hv in ({"X-A": "v"}, {}):
            backend = BackendConfig(
                name="cli-with-headers",
                type="cli",
                model="m",
                command="echo",
                headers=hv,
            )
            with pytest.raises(LLMInvokeError, match="sends no HTTP"):
                mod.llm_invoke("prompt", backend, timeout_s=5)

    def test_header_values_stay_out_of_the_repr(self):
        """The values are the reason the field exists, and are secrets.

        A gateway option is often a token. A dataclass repr reaches
        tracebacks and debug logs without anyone choosing to put it
        there, so the field carries repr=False and this pins it.
        """
        backend = BackendConfig(
            name="gw",
            type="api",
            model="m",
            format="openai",
            base_url="https://x/v1",
            api_key_env="K",
            headers={"X-Gw-Token": "s3cret-value"},
        )

        text = repr(backend)
        assert "s3cret-value" not in text, (
            "repr leaked a header value: %s" % text
        )
        assert "gw" in text, (
            "repr lost the backend name too -- the field was dropped "
            "rather than its value hidden"
        )
        assert backend.headers == {"X-Gw-Token": "s3cret-value"}, (
            "the value must still be readable; only the repr is hidden"
        )

    def test_param_values_stay_out_of_the_repr_too(self):
        """params holds the same class of value and gets the same shield.

        Kept separate from the headers case rather than parametrised
        over the two fields: they are shielded for the same reason but
        by two independent field declarations, and one test covering
        both would still pass with either declaration reverted.
        """
        backend = BackendConfig(
            name="gw",
            type="api",
            model="m",
            format="openai",
            base_url="https://x/v1",
            api_key_env="K",
            params={"x_gw_token": "s3cret-param"},
        )

        text = repr(backend)
        assert "s3cret-param" not in text, (
            "repr leaked a param value: %s" % text
        )
        assert "gw" in text, (
            "repr lost the backend name too -- the field was dropped "
            "rather than its value hidden"
        )
        assert backend.params == {"x_gw_token": "s3cret-param"}, (
            "the value must still be readable; only the repr is hidden"
        )

    def test_params_that_are_not_a_mapping_fail_even_when_falsy(self):
        """`or {}` turns every empty container into "none configured".

        This is the second time on this branch: the headers path was
        written with `or {}`, fixed to `is not None`, and params then
        reproduced the bug one line above the check meant to catch it.
        Both halves of the truth table are asserted for that reason --
        a test using only non-empty wrong types passes with `or {}`
        still in place, which is exactly how it survived the first fix.
        """
        from code_forge.llm_invoke import _apply_params

        def _cfg(params):
            return BackendConfig(
                name="code-built", type="api", model="m",
                format="openai", base_url="https://x/v1",
                api_key_env="K", max_tokens=100, params=params,
            )

        for bad in ([("a", "b")], "a=b", 42, [], (), "", 0, False):
            with pytest.raises(LLMInvokeError, match="must be a mapping"):
                _apply_params(
                    {"model": "m"}, _cfg(bad), outcap_key="max_tokens",
                    allow_thinking=False, allow_effort=False,
                )

        for ok in (None, {}):
            body = {"model": "m"}
            _apply_params(
                body, _cfg(ok), outcap_key="max_tokens",
                allow_thinking=False, allow_effort=False,
            )
            assert body["max_tokens"] == 100

    def test_params_cannot_reach_the_nested_effort_key(self):
        """reasoning_effort lands in body["output_config"]["effort"].

        The typed field writes that dict and the generic params copy
        runs afterwards, so a params entry named output_config replaces
        the whole thing -- the typed field would appear to be ignored
        with nothing saying why. Protected by name rather than by
        merging the two, because a config that half-overrides a typed
        field is not a case worth supporting.
        """
        from code_forge.llm_invoke import _apply_params

        backend = BackendConfig(
            name="code-built", type="api", model="m",
            format="openai", base_url="https://x/v1", api_key_env="K",
            max_tokens=100, reasoning_effort="high",
            params={"output_config": {"effort": "HIJACKED"}},
        )
        body = {"model": "m"}
        with pytest.raises(LLMInvokeError, match="output_config"):
            _apply_params(
                body, backend, outcap_key="max_tokens",
                allow_thinking=False, allow_effort="output_config",
            )

    def test_a_cli_backend_carrying_params_is_refused_too(self):
        """params is as API-only as headers, and as silently dropped.

        The guard reads a two-name tuple rather than _API_ONLY_FIELDS
        because only these two say "unset" as None. The last case pins
        that: a cli backend with neither field must still run, so a
        guard widened to the sentinel-carrying fields would fail here.
        """
        from code_forge import llm_invoke as mod

        for field in ("headers", "params"):
            for value in ({"a": "b"}, {}):
                backend = BackendConfig(
                    name="cli-with-%s" % field, type="cli", model="m",
                    command="echo", **{field: value},
                )
                with pytest.raises(LLMInvokeError, match="sends no HTTP"):
                    mod.llm_invoke("prompt", backend, timeout_s=5)

        clean = BackendConfig(
            name="cli-clean", type="cli", model="m", command="echo",
        )
        with pytest.raises(LLMInvokeError) as caught:
            mod.llm_invoke("prompt", clean, timeout_s=5)
        assert "sends no HTTP" not in str(caught.value), (
            "a cli backend configuring neither field was refused: the "
            "guard is reading something other than these two"
        )

    def test_a_code_built_config_cannot_override_a_protected_param(self):
        """The send path is the only thing between code and the wire.

        Config load refuses these by name, so nothing parsed from yaml
        reaches here -- which is exactly why the guard is worth having:
        a BackendConfig constructed in code skips the parser entirely.

        Asserted through _apply_params rather than check_params: the
        guard lives at the call site, and a test that calls the
        validator directly stays green with that call deleted.

        The body is checked afterwards because a raise is not the point
        -- the point is that the resolved cap and model this function
        spent forty lines computing are still the ones it computed.
        """
        from code_forge.llm_invoke import _apply_params

        for key, value in (
            ("model", "HIJACKED"),
            ("max_tokens", 1),
            ("stream", True),
            ("messages", []),
        ):
            backend = BackendConfig(
                name="code-built",
                type="api",
                model="real-model",
                format="openai",
                base_url="https://x/v1",
                api_key_env="K",
                max_tokens=4096,
                params={key: value},
            )
            body = {"model": backend.model, "messages": [{"role": "user"}]}
            with pytest.raises(LLMInvokeError, match="protected key"):
                _apply_params(
                    body, backend, outcap_key="max_tokens",
                    allow_thinking=False, allow_effort=False,
                )
            assert body["model"] == "real-model", (
                "param %r reached the body before the guard ran" % key
            )

    def test_a_protected_param_is_not_retried(self):
        """Nothing about a config changes between attempts.

        The retry default is retryable, and _apply_params runs inside
        the loop, so without this the whole backoff budget is spent
        reaching the same unfixable answer.
        """
        from code_forge.llm_invoke import _apply_params

        backend = BackendConfig(
            name="code-built",
            type="api",
            model="real-model",
            format="openai",
            base_url="https://x/v1",
            api_key_env="K",
            max_tokens=4096,
            params={"model": "HIJACKED"},
        )
        with pytest.raises(LLMInvokeError) as caught:
            _apply_params(
                {"model": "real-model"}, backend, outcap_key="max_tokens",
                allow_thinking=False, allow_effort=False,
            )
        assert caught.value.retryable is False

    def test_ordinary_params_still_reach_the_body(self):
        """The guard refuses a named few, not the feature.

        Without this the strictest possible guard -- refuse everything
        -- would pass every other test in this group.
        """
        from code_forge.llm_invoke import _apply_params

        backend = BackendConfig(
            name="gw",
            type="api",
            model="m",
            format="openai",
            base_url="https://x/v1",
            api_key_env="K",
            max_tokens=8192,
            params={"top_p": 0.9, "response_format": {"type": "json_object"}},
        )
        body = {"model": "m", "messages": []}
        _apply_params(
            body, backend, outcap_key="max_tokens",
            allow_thinking=False, allow_effort=False,
        )

        assert body["top_p"] == 0.9
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 8192

    def test_a_reserved_name_is_refused_even_when_nothing_collides(self):
        """Reserved names that this request does not itself send.

        The collision check cannot see these: a request carrying neither
        Host nor Cookie has nothing for them to collide with, so they
        would go out on the wire. Config load refuses them by name, and
        is the only thing that fills the field today -- this is what
        keeps that true for a backend built in code instead.

        One name per protection mechanism, since they are separate
        lookups: the frozenset, and the prefix tuple.
        """
        from code_forge.llm_invoke import _request_headers

        base = {
            "Authorization": "Bearer sk-real",
            "Content-Type": "application/json",
        }
        for hk in ("Host", "Cookie", "Sec-Fetch-Mode",
                   "Proxy-Authorization"):
            backend = BackendConfig(
                name="built-in-code",
                type="api",
                model="m",
                format="openai",
                base_url="https://x/v1",
                api_key_env="K",
                headers={hk: "v"},
            )
            assert hk.lower() not in {k.lower() for k in base}, (
                "%s collides with base, so this test would pass via the "
                "collision check and prove nothing" % hk
            )
            with pytest.raises(LLMInvokeError, match="forge controls"):
                _request_headers(base, backend)

    def test_two_spellings_of_one_name_are_refused_at_send_time(self):
        """Config load refuses these; a code-built backend skips that.

        Both spellings reach urllib, which folds them into one line and
        keeps whichever was written last -- so the value that survives
        is decided by dict order, and the other is dropped silently.
        """
        from code_forge.llm_invoke import _request_headers

        backend = BackendConfig(
            name="built-in-code",
            type="api",
            model="m",
            format="openai",
            base_url="https://x/v1",
            api_key_env="K",
            headers={"X-Note": "one", "x-note": "two"},
        )

        with pytest.raises(LLMInvokeError, match="HTTP treats"):
            _request_headers(
                base={"Authorization": "Bearer sk-real"},
                backend=backend,
            )

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

    def test_api_dispatch_key_file(self, tmp_path):
        """T2a: api_key_file reads key from file at invoke time."""
        kf = tmp_path / "key.txt"
        kf.write_text("sk-from-file\n")
        backend = BackendConfig(
            name="test",
            type="api",
            model="model",
            format="openai",
            base_url="https://example.com",
            api_key_file=str(kf),
        )
        mock_response = Mock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode("utf-8")
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_response):
            result = llm_invoke("prompt", backend=backend)
        assert result.content == {"ok": True}

    def test_api_dispatch_key_file_empty(self, tmp_path):
        """Empty key file raises LLMInvokeError."""
        kf = tmp_path / "key.txt"
        kf.write_text("   \n")
        backend = BackendConfig(
            name="test",
            type="api",
            model="model",
            format="openai",
            base_url="https://example.com",
            api_key_file=str(kf),
        )
        with pytest.raises(LLMInvokeError, match="empty"):
            llm_invoke("prompt", backend=backend)

    def test_api_dispatch_key_file_missing(self, tmp_path):
        """Missing key file raises LLMInvokeError."""
        backend = BackendConfig(
            name="test",
            type="api",
            model="model",
            format="openai",
            base_url="https://example.com",
            api_key_file=str(tmp_path / "gone.txt"),
        )
        with pytest.raises(LLMInvokeError, match="cannot read"):
            llm_invoke("prompt", backend=backend)

    def test_api_dispatch_no_credential_at_all(self):
        """Neither api_key_env nor api_key_file -> LLMInvokeError."""
        backend = BackendConfig(
            name="test",
            type="api",
            model="model",
            format="openai",
            base_url="https://example.com",
        )
        with pytest.raises(LLMInvokeError, match="no api_key_env or"):
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
                llm_invoke("test", backend=DEFAULT_BACKEND, timeout_s=1)
            assert exc.value.is_timeout is True

        assert len(kill_called) == 1, "_kill_tree must be called exactly once on timeout"
        assert kill_called[0] is mock_proc

    def test_active_proc_cleared_after_success(self):
        """_active_proc is cleared after successful invocation."""
        import code_forge.llm_invoke as m
        mock_proc = _make_mock_proc(stdout='{"ok": true}')

        with patch("code_forge.llm_invoke.subprocess.Popen", return_value=mock_proc):
            llm_invoke("prompt", backend=DEFAULT_BACKEND)

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
            assert "output=16384" in str(exc_info.value)
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

    def test_openai_length_below_ceiling_names_the_backend_clamp(self):
        """When output lands below the configured ceiling, the backend
        clamped on its own; the message must say so instead of telling
        the user to raise output_ceiling (which would change nothing)."""
        from code_forge.llm_invoke import _invoke_openai, LLMInvokeError

        backend = BackendConfig(
            name="ds", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K", max_tokens=32768,
        )
        resp = Mock()
        resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"find'}, "finish_reason": "length"}],
            # 16384 = the SenseNova-family hard clamp, well below 32768.
            "usage": {"prompt_tokens": 800, "completion_tokens": 16384},
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
                _invoke_openai("p", backend, api_key="k", timeout_s=10)
            assert "clamped below" in str(exc_info.value)
            assert "will not help" in str(exc_info.value)
            # Callers branch on the structured fields, not the message.
            assert exc_info.value.kind == "truncated"
            assert exc_info.value.retryable is False

    def test_openai_length_at_ceiling_uses_the_generic_path(self):
        """out_tok == the configured cap is the ordinary full-capacity
        truncation: the message tells the user to raise output_ceiling,
        not that the backend clamped on its own."""
        from code_forge.llm_invoke import _invoke_openai, LLMInvokeError

        backend = BackendConfig(
            name="ds", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K", max_tokens=32768,
        )
        resp = Mock()
        resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"find'}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 32768},
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
                _invoke_openai("p", backend, api_key="k", timeout_s=10)
            assert "clamped below" not in str(exc_info.value)
            assert "output capacity" in str(exc_info.value)
            assert exc_info.value.kind == "truncated"

    def test_openai_length_with_zero_output_uses_the_generic_path(self):
        """out_tok == 0 is the empty-content case, not a clamp."""
        from code_forge.llm_invoke import _invoke_openai, LLMInvokeError

        backend = BackendConfig(
            name="ds", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K", max_tokens=32768,
        )
        resp = Mock()
        resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"find'}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 0},
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
                _invoke_openai("p", backend, api_key="k", timeout_s=10)
            assert "clamped below" not in str(exc_info.value)
            assert "output capacity" in str(exc_info.value)

    def test_openai_length_without_a_usable_cap_names_the_missing_knob(self):
        """max_tokens: 0 is the config's absence marker, not a capacity:
        the message must say no usable cap is set instead of reporting
        'capacity (0 tokens)' and telling the user to raise it."""
        from code_forge.llm_invoke import _invoke_openai, LLMInvokeError

        backend = BackendConfig(
            name="ds", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K", max_tokens=0,
        )
        resp = Mock()
        resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"find'}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 1200},
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
                _invoke_openai("p", backend, api_key="k", timeout_s=10)
            assert "no usable output cap" in str(exc_info.value)
            assert "output capacity" not in str(exc_info.value)
            assert "Set max_tokens" in str(exc_info.value)
            assert exc_info.value.kind == "truncated"


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
            assert "output=8192" in str(exc_info.value)
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


class TestTruncationCarrier:
    """The three truncation raises carry the partial payload.

    kind/retryable/message behavior is unchanged from the plain raises
    these replaced (TestTruncationDetection); what the carrier adds is
    the partial content, the usage dict, and the resolved cap, which the
    recovery path needs and the old raises discarded.
    """

    def test_openai_truncation_carries_partial(self):
        from code_forge.llm_invoke import _invoke_openai, _TruncatedResponse

        backend = BackendConfig(
            name="ds", type="api", model="m", format="openai",
            base_url="http://x", api_key_env="K", max_tokens=8192,
        )
        resp = Mock()
        resp.read.return_value = json.dumps({
            "choices": [{
                "message": {"content": '{"findings": [{"fil'},
                "finish_reason": "length",
            }],
            "usage": {"prompt_tokens": 800, "completion_tokens": 8192},
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(_TruncatedResponse, match="truncated") as exc_info:
                _invoke_openai("p", backend, api_key="k", timeout_s=10)
            exc = exc_info.value
            assert exc.content == '{"findings": [{"fil'
            assert exc.usage_data == {
                "prompt_tokens": 800, "completion_tokens": 8192,
            }
            assert exc.resolved_cap == 8192
            assert exc.kind == "truncated"
            assert exc.retryable is False

    def test_anthropic_truncation_carries_partial(self):
        from code_forge.llm_invoke import _invoke_anthropic, _TruncatedResponse

        backend = BackendConfig(
            name="mimo", type="api", model="m", format="anthropic",
            base_url="http://x", api_key_env="K",
        )
        resp = Mock()
        resp.read.return_value = json.dumps({
            "content": [{"type": "text", "text": '{"findings": [{"fil'}],
            "usage": {"input_tokens": 500, "output_tokens": 16384},
            "stop_reason": "max_tokens",
        }).encode("utf-8")
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(_TruncatedResponse, match="truncated") as exc_info:
                _invoke_anthropic("p", backend, api_key="k", timeout_s=10)
            exc = exc_info.value
            assert exc.content == '{"findings": [{"fil'
            assert exc.usage_data == {
                "input_tokens": 500, "output_tokens": 16384,
            }
            assert exc.resolved_cap == 16384
            assert exc.kind == "truncated"
            assert exc.retryable is False

    def test_vertex_truncation_carries_partial(self):
        from code_forge.llm_invoke import _invoke_vertex, _TruncatedResponse

        backend = _make_vertex_backend()
        mock_creds = MagicMock()
        mock_creds.token = "tok"
        resp_data = {
            "content": [{"type": "text", "text": '{"findings": [{"fil'}],
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
            with pytest.raises(_TruncatedResponse, match="truncated") as exc_info:
                _invoke_vertex("p", backend, timeout_s=10)
            exc = exc_info.value
            assert exc.content == '{"findings": [{"fil'
            assert exc.usage_data == {
                "input_tokens": 600, "output_tokens": 8192,
            }
            assert exc.resolved_cap == 8192
            assert exc.kind == "truncated"
            assert exc.retryable is False


class TestTruncationBreaker:
    """The run-level truncation counter: threshold trip and reset.

    Review passes run in parallel worker threads, so every mutation and
    read is lock-protected; a tripped breaker stays tripped and every
    later check raises without touching the count.
    """

    def test_breaker_count_is_monotonic(self):
        """The run-level count only ever rises: no reset API exists, so
        truncate/clean alternation cannot evade the threshold."""
        from code_forge.llm_invoke import TruncationBreaker

        breaker = TruncationBreaker(5)
        for _ in range(4):
            breaker.record_truncation()
        assert breaker.count == 4
        assert breaker.tripped is False
        assert not hasattr(breaker, "record_success")

    def test_breaker_trips_and_check_tripped(self):
        from code_forge.llm_invoke import (
            LLMInvokeError,
            TruncationBreaker,
            TruncationBreakerError,
        )

        breaker = TruncationBreaker(5)
        for _ in range(4):
            breaker.record_truncation()
        with pytest.raises(TruncationBreakerError) as exc_info:
            breaker.record_truncation()
        exc = exc_info.value
        assert isinstance(exc, LLMInvokeError)
        assert exc.kind == "truncated"
        assert exc.retryable is False
        assert "truncations" in str(exc)
        assert "timeout" not in str(exc).lower()
        # The advice must not unconditionally say "raise output_ceiling":
        # the backend may already clamp below the configured cap, in
        # which case raising the knob changes nothing.
        assert "may already clamp below the configured" in str(exc)
        assert breaker.count == 5
        assert breaker.tripped is True
        with pytest.raises(TruncationBreakerError):
            breaker.check_tripped()
        assert breaker.count == 5

    def test_breaker_thread_safe_increments(self):
        from code_forge.llm_invoke import TruncationBreaker

        breaker = TruncationBreaker(threshold=100)
        errors = []

        def _hammer():
            try:
                for _ in range(10):
                    breaker.record_truncation()
            except Exception as exc:  # noqa: BLE001 -- collected for assert
                errors.append(exc)

        threads = [threading.Thread(target=_hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert breaker.count == 80


# Truncation continuation fixtures: partial + tail concatenate to a
# valid forge envelope; the usage pair mirrors a clamped response plus
# a short continuation request.
_PARTIAL = '{"findings": [{"file": "a.c",'
_TAIL = '"line": 1, "severity": "LOW"}]}'


def _truncated_response(partial=_PARTIAL, usage_data=None, **kw):
    from code_forge.llm_invoke import _TruncatedResponse

    return _TruncatedResponse(
        "ds backend response truncated (finish_reason=length)",
        content=partial,
        usage_data=usage_data if usage_data is not None else {
            "prompt_tokens": 800, "completion_tokens": 16384,
        },
        resolved_cap=65536,
        kind="truncated",
        retryable=False,
        **kw,
    )


class TestTruncationRecover:
    """Bounded continuation turns a truncated reply into a result.

    The recovery dispatch is patched at the module seam
    (code_forge.llm_invoke._invoke_openai), which both the retry loop
    and the continuation helper call, so one side_effect list covers
    the original attempt plus every continuation request.
    """

    def test_continuation_success(self):
        backend = _make_api_backend(name="ds", fmt="openai")
        side_effect = [
            _truncated_response(),
            (_TAIL, {"prompt_tokens": 5, "completion_tokens": 20}),
        ]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            result = llm_invoke("p", backend=backend, max_attempts=5)

        assert result.content == {
            "findings": [{"file": "a.c", "line": 1, "severity": "LOW"}],
        }
        assert result.usage == Usage(805, 16404)
        assert result.is_truncated is True
        assert mock_invoke.call_count == 2

    def test_continuation_exhausted(self):
        """Initial truncation + 2 continuation attempts (budget=2
        exhausted, both truncated again) = 3 total _invoke_openai calls."""
        backend = _make_api_backend(name="ds", fmt="openai")
        side_effect = [
            _truncated_response(),
            _truncated_response('{"findings": [{"file": "b.c",'),
            _truncated_response('{"findings": [{"file": "c.c",'),
        ]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep") as mock_sleep:
            with pytest.raises(
                LLMInvokeError,
                match="continuation exhausted after 2 attempts",
            ) as exc_info:
                llm_invoke("p", backend=backend, max_attempts=5)

        assert exc_info.value.kind == "truncated"
        assert exc_info.value.retryable is False
        assert mock_invoke.call_count == 3
        # The exhaustion message carries the last failure's diagnosis,
        # not just the counter.
        assert "last failure" in str(exc_info.value)
        assert "finish_reason=length" in str(exc_info.value)
        # One fixed delay, only before the second continuation attempt.
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args[0][0] == 2.0

    def test_zero_partial_raises_no_continuation(self):
        backend = _make_api_backend(name="ds", fmt="openai")
        side_effect = [_truncated_response(partial=None)]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
                llm_invoke("p", backend=backend, max_attempts=5)

        assert exc_info.value.kind == "truncated"
        assert mock_invoke.call_count == 1

    def test_no_brace_partial_raises_no_continuation(self):
        backend = _make_api_backend(name="ds", fmt="openai")
        side_effect = [_truncated_response(partial="prose with no JSON")]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
                llm_invoke("p", backend=backend, max_attempts=5)

        assert exc_info.value.kind == "truncated"
        assert mock_invoke.call_count == 1

    def test_combined_parse_failure_counts_as_attempt(self):
        """Initial truncation + 2 continuation attempts whose combined
        output never parses (budget=2 exhausted) = 3 total _invoke_openai
        calls."""
        backend = _make_api_backend(name="ds", fmt="openai")
        usage_c = {"prompt_tokens": 5, "completion_tokens": 20}
        side_effect = [
            _truncated_response(),
            ("plain prose continuation, no json at all", usage_c),
            ("more plain prose, still no json", usage_c),
        ]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            with pytest.raises(
                LLMInvokeError,
                match="continuation exhausted after 2 attempts",
            ):
                llm_invoke("p", backend=backend, max_attempts=5)

        assert mock_invoke.call_count == 3

    def test_continuation_does_not_consume_max_attempts(self):
        backend = _make_api_backend(name="ds", fmt="openai")
        side_effect = [
            _truncated_response(),
            (_TAIL, {"prompt_tokens": 5, "completion_tokens": 20}),
        ]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            result = llm_invoke("p", backend=backend, max_attempts=2)

        assert result.content == {
            "findings": [{"file": "a.c", "line": 1, "severity": "LOW"}],
        }
        assert mock_invoke.call_count == 2

    def test_pre_tripped_breaker_raises_before_dispatch(self):
        from code_forge.llm_invoke import (
            TruncationBreaker,
            TruncationBreakerError,
        )

        backend = _make_api_backend(name="ds", fmt="openai")
        breaker = TruncationBreaker(5)
        with pytest.raises(TruncationBreakerError):
            for _ in range(5):
                breaker.record_truncation()

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai") as mock_invoke:
            with pytest.raises(TruncationBreakerError) as exc_info:
                llm_invoke(
                    "p", backend=backend, continuation_breaker=breaker,
                )

        assert exc_info.value.kind == "truncated"
        assert mock_invoke.call_count == 0

    def test_vertex_continuation(self):
        """The vertex format recovers through the same helper, dispatching
        without an api_key and summing input/output token keys."""
        backend = _make_vertex_backend()
        side_effect = [
            _truncated_response(usage_data={
                "input_tokens": 600, "output_tokens": 8192,
            }),
            (_TAIL, {"input_tokens": 5, "output_tokens": 20}),
        ]
        with patch("code_forge.llm_invoke._invoke_vertex",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            result = llm_invoke("p", backend=backend, max_attempts=5)

        assert result.content == {
            "findings": [{"file": "a.c", "line": 1, "severity": "LOW"}],
        }
        assert result.usage == Usage(605, 8212)
        assert result.is_truncated is True
        assert mock_invoke.call_count == 2

    def test_non_str_partial_raises_no_continuation(self):
        """A truthy non-str partial (content=123) hits the isinstance
        guard: original raise, exactly one call, no AttributeError."""
        backend = _make_api_backend(name="ds", fmt="openai")
        side_effect = [_truncated_response(partial=123)]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
                llm_invoke("p", backend=backend, max_attempts=5)

        assert exc_info.value.kind == "truncated"
        assert mock_invoke.call_count == 1

    def test_trip_propagates_not_budgeted(self):
        """A continuation-request truncation that trips the breaker
        propagates the trip: no further network call is issued and the
        trip is never converted into a budgeted failure."""
        from code_forge.llm_invoke import (
            TruncationBreaker,
            TruncationBreakerError,
        )

        backend = _make_api_backend(name="ds", fmt="openai")
        breaker = TruncationBreaker(5)
        for _ in range(3):
            breaker.record_truncation()
        side_effect = [
            _truncated_response(),
            _truncated_response('{"findings": [{"file": "b.c",'),
        ]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            with pytest.raises(TruncationBreakerError) as exc_info:
                llm_invoke(
                    "p", backend=backend, continuation_breaker=breaker,
                )

        assert exc_info.value.kind == "truncated"
        assert mock_invoke.call_count == 2

    def test_usage_none_normalized(self):
        """usage_data=None on the truncated payload and usage=None on the
        continuation sum to (0,0) with no AttributeError."""
        from code_forge.llm_invoke import _TruncatedResponse

        backend = _make_api_backend(name="ds", fmt="openai")
        side_effect = [
            _TruncatedResponse(
                "ds backend response truncated (finish_reason=length)",
                content=_PARTIAL, usage_data=None, resolved_cap=65536,
                kind="truncated", retryable=False,
            ),
            (_TAIL, None),
        ]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            result = llm_invoke("p", backend=backend, max_attempts=5)

        assert result.content == {
            "findings": [{"file": "a.c", "line": 1, "severity": "LOW"}],
        }
        assert result.usage == Usage(0, 0)
        assert result.is_truncated is True
        assert mock_invoke.call_count == 2

    def test_non_str_continuation_normalized(self):
        """A continuation returning content=123 counts as a failed
        attempt (budget decrement) and never raises TypeError."""
        backend = _make_api_backend(name="ds", fmt="openai")
        side_effect = [
            _truncated_response(),
            (123, {"prompt_tokens": 5, "completion_tokens": 20}),
            (_TAIL, {"prompt_tokens": 5, "completion_tokens": 20}),
        ]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            result = llm_invoke("p", backend=backend, max_attempts=5)

        assert result.content == {
            "findings": [{"file": "a.c", "line": 1, "severity": "LOW"}],
        }
        assert mock_invoke.call_count == 3

    def test_anthropic_continuation_passes_api_key(self):
        """The anthropic dispatch hands the resolved api_key to the
        continuation request and sums input/output token keys."""
        backend = _make_api_backend(name="anthrop", fmt="anthropic")
        state = {"calls": 0}
        seen = []

        def _alternate(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                raise _truncated_response(usage_data={
                    "input_tokens": 500, "output_tokens": 16384,
                })
            seen.append(args)
            return (_TAIL, {"input_tokens": 5, "output_tokens": 20})

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_anthropic",
                   side_effect=_alternate), \
             patch("time.sleep"):
            result = llm_invoke("p", backend=backend, max_attempts=5)

        assert result.content == {
            "findings": [{"file": "a.c", "line": 1, "severity": "LOW"}],
        }
        assert result.usage == Usage(505, 16404)
        assert len(seen) == 1
        prompt_c, got_backend, got_api_key, got_timeout = seen[0]
        assert "Continue the JSON output" in prompt_c
        assert got_backend is backend
        assert got_api_key == "sk-test"
        assert got_timeout > 0

    def test_fence_marker_stripped_from_continuation_prompt(self):
        """A partial whose content contains the prompt's own fence
        markers (here inside a JSON string value) cannot break out of
        the fenced data block: every occurrence is stripped from the
        embedded tail, leaving exactly the real opening and closing
        fence in the continuation prompt."""
        backend = _make_api_backend(name="ds", fmt="openai")
        seen = []
        state = {"calls": 0}
        partial = '{"findings": [{"file": "a.c", "n": "</partial><partial>'
        tail = '"}, {"line": 1, "severity": "LOW"}]}'

        def _capture(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                raise _truncated_response(partial=partial)
            seen.append(args[0])
            return (tail, {"prompt_tokens": 5, "completion_tokens": 20})

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=_capture), \
             patch("time.sleep"):
            result = llm_invoke("p", backend=backend, max_attempts=5)

        assert result.is_truncated is True
        assert len(seen) == 1
        prompt_c = seen[0]
        assert prompt_c.count("<partial>") == 1
        assert prompt_c.count("</partial>") == 1

    def test_wrong_shaped_continuation_is_a_failed_attempt(self):
        """A continuation that completes the JSON into a non-envelope
        dict is a failed attempt, never a result: initial truncation +
        2 failed continuations = 3 total calls."""
        backend = _make_api_backend(name="ds", fmt="openai")
        usage_c = {"prompt_tokens": 5, "completion_tokens": 20}
        side_effect = [
            _truncated_response(partial='{"wrong": [{"file": "a.c",'),
            ('"line": 1}]}', usage_c),
            ('"line": 1}]}', usage_c),
        ]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            with pytest.raises(
                LLMInvokeError,
                match="continuation exhausted after 2 attempts",
            ):
                llm_invoke("p", backend=backend, max_attempts=5)

        assert mock_invoke.call_count == 3


class TestTruncationBreakerWiring:
    """The run-level breaker shared across calls and threaded into the
    review provider.

    Calls 1-4 each survive one truncation and recover (one truncation
    event each); call 5's truncation event trips the breaker before a
    continuation can run, and the pre-dispatch check makes call 6 fail
    before any network call. The provider test patches the SOURCE name
    before build_l1_provider runs its lazy from-import, which is the
    only seam that intercepts the closure the pass runner calls.
    """

    def test_breaker_trips_across_calls(self):
        from code_forge.llm_invoke import (
            TruncationBreaker,
            TruncationBreakerError,
        )

        backend = _make_api_backend(name="ds", fmt="openai")
        breaker = TruncationBreaker(5)

        def _alternating(*args, **kwargs):
            mock_invoke.inc += 1
            if mock_invoke.inc % 2 == 1:
                raise _truncated_response()
            return (_TAIL, {"prompt_tokens": 5, "completion_tokens": 20})

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=_alternating) as mock_invoke, \
             patch("time.sleep"):
            mock_invoke.inc = 0
            for _ in range(4):
                result = llm_invoke(
                    "p", backend=backend, continuation_breaker=breaker,
                )
                assert result.is_truncated is True
            assert mock_invoke.call_count == 8
            with pytest.raises(TruncationBreakerError):
                llm_invoke(
                    "p", backend=backend, continuation_breaker=breaker,
                )
            assert mock_invoke.call_count == 9
            with pytest.raises(TruncationBreakerError):
                llm_invoke(
                    "p", backend=backend, continuation_breaker=breaker,
                )
            assert mock_invoke.call_count == 9

    def test_breaker_default_fresh_per_call(self):
        """Without a shared breaker each call gets a fresh instance, so
        two truncation-and-recovery calls never accumulate a trip."""
        backend = _make_api_backend(name="ds", fmt="openai")
        side_effect = [
            _truncated_response(),
            (_TAIL, {"prompt_tokens": 5, "completion_tokens": 20}),
            _truncated_response(),
            (_TAIL, {"prompt_tokens": 5, "completion_tokens": 20}),
        ]
        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=side_effect) as mock_invoke, \
             patch("time.sleep"):
            first = llm_invoke("p", backend=backend, max_attempts=5)
            second = llm_invoke("p", backend=backend, max_attempts=5)

        assert first.is_truncated is True
        assert second.is_truncated is True
        assert mock_invoke.call_count == 4

    def test_provider_passes_breaker(self):
        from types import SimpleNamespace
        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import TruncationBreaker

        breaker_obj = TruncationBreaker(5)
        fake_resolved = SimpleNamespace(git_diff="x")
        with patch("code_forge.llm_invoke.llm_invoke") as mock_invoke:
            mock_invoke.return_value = LLMResult(
                content={
                    "findings": [],
                    "code_excerpts": [{
                        "file": "a.py", "start_line": 1, "end_line": 2,
                        "content": "x = 1",
                    }],
                },
                usage=Usage(10, 5),
            )
            provider = build_l1_provider(
                "auto", fake_resolved, backend=None,
                continuation_breaker=breaker_obj,
            )
            provider()

        assert mock_invoke.call_count == 3
        for call in mock_invoke.call_args_list:
            assert call.kwargs["continuation_breaker"] is breaker_obj

    def test_fold_never_resets_truncation_breaker(self):
        """The truncation breaker is monotonic: neither recovered nor
        clean pass results reset its count, so a tripped run cannot be
        evaded by truncate/clean alternation across parallel passes."""
        from types import SimpleNamespace
        from code_forge.factories import build_l1_provider
        from code_forge.llm_invoke import TruncationBreaker

        breaker = TruncationBreaker(5)
        breaker.record_truncation()
        fake_resolved = SimpleNamespace(git_diff="x")
        with patch("code_forge.llm_invoke.llm_invoke") as mock_invoke:
            mock_invoke.return_value = LLMResult(
                content={
                    "findings": [],
                    "code_excerpts": [{
                        "file": "a.py", "start_line": 1, "end_line": 2,
                        "content": "x = 1",
                    }],
                },
                usage=Usage(10, 5),
                is_truncated=True,
            )
            provider = build_l1_provider(
                "auto", fake_resolved, backend=None,
                continuation_breaker=breaker,
            )
            provider()
            assert breaker.count == 1

            mock_invoke.return_value = LLMResult(
                content={
                    "findings": [],
                    "code_excerpts": [{
                        "file": "a.py", "start_line": 1, "end_line": 2,
                        "content": "x = 1",
                    }],
                },
                usage=Usage(10, 5),
            )
            provider()
            assert breaker.count == 1


def _empty_content_backend(tmp_path, fmt="openai"):
    kf = tmp_path / "key.txt"
    kf.write_text("sk-test\n")
    return BackendConfig(
        name="deepseek", type="api", model="m", format=fmt,
        base_url="http://x", api_key_file=str(kf),
    )


def _mock_body(payload):
    resp = Mock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__ = Mock(return_value=resp)
    resp.__exit__ = Mock(return_value=False)
    return resp


def _openai_body(content, finish="stop"):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0},
    }


class TestEmptyContentDetection:
    """A present-but-null content field is an empty response, not bad JSON.

    deepseek returns "content": null with finish_reason "stop" now and then.
    The value is not a missing key, so the extraction site raises no KeyError
    and the None travels downstream.  Reaching _strip_fences it used to raise
    AttributeError; short-circuiting there instead yields "" and the run dies
    one line past the retry loop reporting invalid JSON, which sends the
    reader after a format bug that is not there.  Detection belongs in the
    dispatch, where the retry loop can still act and the backend name is
    still in hand.
    """

    def test_openai_null_content_raises_empty(self, tmp_path):
        backend = _empty_content_backend(tmp_path)
        resp = _mock_body(_openai_body(None))
        with patch("urllib.request.urlopen", return_value=resp), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="no content") as exc_info:
                llm_invoke("p", backend=backend, max_attempts=2)
        assert exc_info.value.kind == "empty"
        assert "deepseek" in str(exc_info.value)

    def test_openai_empty_string_content_raises_empty(self, tmp_path):
        """An empty string reaches the same dead end as null."""
        backend = _empty_content_backend(tmp_path)
        resp = _mock_body(_openai_body("   "))
        with patch("urllib.request.urlopen", return_value=resp), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="no content") as exc_info:
                llm_invoke("p", backend=backend, max_attempts=2)
        assert exc_info.value.kind == "empty"

    def test_null_content_is_retried_and_recovers(self, tmp_path):
        """The point of the fix: an intermittent empty reply self-heals.

        Downstream of the loop this same response ends the run outright, so
        this is the assertion that distinguishes the two placements.
        """
        backend = _empty_content_backend(tmp_path)
        # A third response is supplied so that an over-eager retry fails on
        # the call_count assertion rather than on StopIteration, which would
        # hide which behaviour actually broke.
        responses = [
            _mock_body(_openai_body(None)),
            _mock_body(_openai_body('{"findings": []}')),
            _mock_body(_openai_body('{"findings": ["extra call"]}')),
        ]
        with patch("urllib.request.urlopen", side_effect=responses) as mock_open, \
             patch("time.sleep"):
            result = llm_invoke("p", backend=backend, max_attempts=3)
        assert result.content == {"findings": []}
        assert mock_open.call_count == 2

    def test_null_content_with_length_still_reports_truncated(self, tmp_path):
        """Ordering guard: a capped response keeps its actionable message.

        finish_reason=length raises inside _invoke_openai before the dispatch
        check runs, so the user still gets the output_ceiling advice rather
        than a generic empty-response error.
        """
        backend = _empty_content_backend(tmp_path)
        resp = _mock_body(_openai_body(None, finish="length"))
        with patch("urllib.request.urlopen", return_value=resp) as mock_open, \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="truncated") as exc_info:
                llm_invoke("p", backend=backend, max_attempts=2)
        assert exc_info.value.kind == "truncated"
        assert "output capacity" in str(exc_info.value)
        # Truncation is not retryable, so the budget must not be spent
        # re-submitting a prompt whose cap is already known to be too low.
        assert mock_open.call_count == 1

    def test_non_string_content_raises_empty(self, tmp_path):
        """A backend loose enough to send null can send a number.

        Rejecting only None would move the AttributeError rather than
        remove it, since .strip() is what fails in either case.
        """
        backend = _empty_content_backend(tmp_path)
        resp = _mock_body(_openai_body(123))
        with patch("urllib.request.urlopen", return_value=resp), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="no content") as exc_info:
                llm_invoke("p", backend=backend, max_attempts=2)
        assert exc_info.value.kind == "empty"

    def test_anthropic_null_text_raises_empty(self, tmp_path):
        """The block-shaped formats carry the same hole via "text": null."""
        backend = _empty_content_backend(tmp_path, fmt="anthropic")
        resp = _mock_body({
            "content": [{"type": "text", "text": None}],
            "usage": {"input_tokens": 10, "output_tokens": 0},
            "stop_reason": "end_turn",
        })
        with patch("urllib.request.urlopen", return_value=resp), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="no content") as exc_info:
                llm_invoke("p", backend=backend, max_attempts=2)
        assert exc_info.value.kind == "empty"


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

    def test_vertex_carries_a_configured_header(self, monkeypatch):
        """The third of the three formats has to be wired too.

        Each format builds its own header dict, so a merge added to one
        of them says nothing about the other two.
        """
        from code_forge.llm_invoke import _invoke_vertex
        backend = _make_vertex_backend()
        object.__setattr__(
            backend, "headers", {"x-omniroute-compression": "off"}
        )

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

        folded = {k.lower(): v for k, v in captured_headers.items()}
        assert folded.get("x-omniroute-compression") == "off"
        # exact, not a prefix: the token is mocked to a known value just
        # above, so a prefix check would pass on a truncated or
        # doubled-up credential too
        assert folded.get("authorization") == "Bearer my-bearer-token"

    def test_vertex_returns_real_usage(self, monkeypatch):
        """Vertex response returns real token usage."""
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
    """_format_error_message output format per -08."""

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


class TestMalformedResponseBody:
    """A non-JSON HTTP 200 body from the provider must raise LLMInvokeError,
    not an unwrapped json.JSONDecodeError, so retry/circuit-breaker callers
    that catch LLMInvokeError actually see the failure.
    """

    def _garbled_response(self, garbage):
        resp = Mock()
        if isinstance(garbage, str):
            garbage = garbage.encode("utf-8")
        resp.read.return_value = garbage
        resp.__enter__ = Mock(return_value=resp)
        resp.__exit__ = Mock(return_value=False)
        return resp

    def test_openai_non_json_body_raises_llm_invoke_error(self):
        from code_forge.llm_invoke import _invoke_openai

        backend = _make_api_backend(name="ds", fmt="openai")
        resp = self._garbled_response("<html>502 Bad Gateway</html>")

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError) as exc:
                _invoke_openai("p", backend, api_key="k", timeout_s=10)
        assert exc.value.retryable is True
        assert "ds" in str(exc.value)
        assert "502 Bad Gateway" in str(exc.value)

    def test_anthropic_non_json_body_raises_llm_invoke_error(self):
        from code_forge.llm_invoke import _invoke_anthropic

        backend = _make_api_backend(name="mimo", fmt="anthropic")
        resp = self._garbled_response("<html>502 Bad Gateway</html>")

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError) as exc:
                _invoke_anthropic("p", backend, api_key="k", timeout_s=10)
        assert exc.value.retryable is True
        assert "mimo" in str(exc.value)
        assert "502 Bad Gateway" in str(exc.value)

    def test_vertex_non_json_body_raises_llm_invoke_error(self):
        from code_forge.llm_invoke import _invoke_vertex

        backend = _make_vertex_backend()
        mock_creds = MagicMock()
        mock_creds.token = "tok"
        resp = self._garbled_response("<html>502 Bad Gateway</html>")

        with patch("google.auth.default", return_value=(mock_creds, "proj")), \
             patch("google.auth.transport.requests.Request"), \
             patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError) as exc:
                _invoke_vertex("p", backend, timeout_s=10)
        assert exc.value.retryable is True
        assert "vtx" in str(exc.value)
        assert "502 Bad Gateway" in str(exc.value)

    def test_non_utf8_body_raises_llm_invoke_error(self):
        from code_forge.llm_invoke import _invoke_openai

        backend = _make_api_backend(name="ds", fmt="openai")
        resp = self._garbled_response(b"\xff\xfe\x00garbage")

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(LLMInvokeError) as exc:
                _invoke_openai("p", backend, api_key="k", timeout_s=10)
        assert exc.value.retryable is True
        assert "ds" in str(exc.value)
        assert "garbage" in str(exc.value)


class TestApiNoJsonDiagnostic:
    """API path must surface model output in str(exc) when JSON parsing fails.

    Bug-injection proof: narrow the message back to the bare literal
    'API response content is not valid JSON' (no diagnostic interpolated)
    -- this test must FAIL because 'weather is nice' won't appear in
    str(exc). The sampling path (llm_invoke.py:1427) already interpolates
    its diagnostic; this test closes the equivalent gap on the API path.
    """

    def test_api_no_json_surfaces_content_in_message(self):
        from code_forge.llm_invoke import _invoke_api

        backend = _make_api_backend(name="ds", fmt="openai")

        def _mock_openai_no_json(*args, **kwargs):
            return "The weather is nice today.", {
                "prompt_tokens": 10, "completion_tokens": 5,
            }

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=_mock_openai_no_json):
            with pytest.raises(LLMInvokeError) as exc_info:
                _invoke_api(
                    "prompt", backend, timeout_s=10, max_attempts=1,
                )

        msg = str(exc_info.value)
        # The diagnostic must survive str(exc) -- this is the whole point.
        assert "weather is nice" in msg, \
            "model output missing from str(exc): %s" % msg
        # The original prefix must still be recognizable.
        assert "API response content is not valid JSON" in msg
        # stderr attribute must also carry the diagnostic.
        assert "weather is nice" in exc_info.value.stderr


class TestBadJsonRetry:
    """An HTTP-200 reply whose body is not valid JSON is retried.

    A model embedding source code in a JSON string must double every
    backslash, and on backslash-dense diffs it gets that wrong often
    enough that the old parse-outside-the-loop shape voided every cycle
    of the run. The parse now lives inside the retry loop, so a bad
    sample draws a fresh attempt bounded by max_attempts like every
    other retry; the embedded-JSON fallback still rescues a reply that
    wraps intact JSON in prose without spending an attempt.

    Bug-injection proof: move the parse back below the loop (or set
    retryable=False on the no_json raise) -- the success test must FAIL
    with the first bad reply raising straight out instead of retrying.
    """

    def test_bad_json_response_retries_and_succeeds(self):
        from code_forge.llm_invoke import _invoke_api

        backend = _make_api_backend(name="ds", fmt="openai")
        calls = [0]

        def _mock_openai_bad_then_good(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 1:
                # Unbalanced JSON: json.loads fails and the embedded-JSON
                # fallback finds no balanced object either.
                return '{"findings": [{"unterminated', {
                    "prompt_tokens": 10, "completion_tokens": 5,
                }
            return '{"findings": [{"ok": true}]}', {
                "prompt_tokens": 20, "completion_tokens": 8,
            }

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=_mock_openai_bad_then_good), \
             patch("time.sleep"):
            result = _invoke_api(
                "prompt", backend, timeout_s=10, max_attempts=5,
            )

        assert result.content == {"findings": [{"ok": True}]}
        assert calls[0] == 2, "bad JSON must draw a fresh attempt"
        assert result.usage.output_tokens == 8

    def test_persistent_bad_json_exhausts_attempts_with_kind(self):
        from code_forge.llm_invoke import _invoke_api

        backend = _make_api_backend(name="ds", fmt="openai")
        calls = [0]

        def _mock_openai_always_bad(*args, **kwargs):
            calls[0] += 1
            return '{"broken', {"prompt_tokens": 10, "completion_tokens": 5}

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=_mock_openai_always_bad), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError) as exc_info:
                _invoke_api(
                    "prompt", backend, timeout_s=10, max_attempts=3,
                )

        assert calls[0] == 3, "every attempt parses its own reply"
        assert exc_info.value.kind == "no_json"

    def test_prose_wrapped_json_is_rescued_without_a_retry(self):
        from code_forge.llm_invoke import _invoke_api

        backend = _make_api_backend(name="ds", fmt="openai")
        calls = [0]

        def _mock_openai_prose_wrapped(*args, **kwargs):
            calls[0] += 1
            return 'Here you go: {"findings": []}', {
                "prompt_tokens": 10, "completion_tokens": 5,
            }

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("code_forge.llm_invoke._invoke_openai",
                   side_effect=_mock_openai_prose_wrapped):
            result = _invoke_api(
                "prompt", backend, timeout_s=10, max_attempts=5,
            )

        assert result.content == {"findings": []}
        assert calls[0] == 1, "the fallback rescues without spending an attempt"


class TestCliNoJsonDiagnostic:
    """CLI path must surface subprocess stdout in str(exc) when JSON parsing
    fails.

    Bug-injection proof: narrow the message back to the bare literal
    'LLM subprocess returned non-JSON stdout' (no diagnostic interpolated)
    -- this test must FAIL because 'prose not json' won't appear in
    str(exc). Mirrors TestApiNoJsonDiagnostic for the API path.
    """

    @patch("shutil.which", return_value="/usr/bin/echo")
    @patch("subprocess.Popen")
    def test_cli_no_json_surfaces_stdout_in_message(self, mock_popen, _):
        from code_forge.llm_invoke import _invoke_cli

        mock_proc = Mock()
        mock_proc.communicate.return_value = (
            "subprocess emitted this prose not json", "",
        )
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        backend = BackendConfig(
            name="local", type="cli", model="m", command="echo",
        )

        with pytest.raises(LLMInvokeError) as exc_info:
            _invoke_cli("prompt", backend, timeout_s=10)

        msg = str(exc_info.value)
        # The diagnostic must survive str(exc) -- this is the whole point.
        assert "prose not json" in msg, \
            "stdout missing from str(exc): %s" % msg
        # The original prefix must still be recognizable.
        assert "LLM subprocess returned non-JSON stdout" in msg
        # stderr attribute must also carry the diagnostic.
        assert "prose not json" in exc_info.value.stderr


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

from code_forge.llm_invoke import _apply_params  # noqa: E402


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

    def test_stream_false_is_sent_explicitly(self):
        """A non-streaming backend must say so on the wire.

        Leaving the field out lets the server choose, and OmniRoute chooses
        SSE, which arrives as an unparseable "data: {...}" body.
        """
        body = {}
        _apply_params(body, _cfg(stream=False),
                      outcap_key="max_completion_tokens",
                      allow_thinking=True, allow_effort=True)
        assert body["stream"] is False

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


class TestApiTimeoutCap:
    """API backends cap at _API_TIMEOUT_CAP_S when no explicit timeout is set."""

    def test_default_timeout_capped_at_600(self):
        backend = _cfg(type="api", timeout_s=0)
        with patch.dict(os.environ, {"K": "sk-test"}):
            with patch("code_forge.llm_invoke._invoke_api") as m:
                m.return_value = Mock(content="{}", usage={},
                                     duration_s=1.0)
                llm_invoke("p", backend=backend)
                called_timeout = m.call_args[0][2]
                assert called_timeout == 600

    def test_explicit_timeout_not_capped(self):
        backend = _cfg(type="api", timeout_s=0)
        with patch.dict(os.environ, {"K": "sk-test"}):
            with patch("code_forge.llm_invoke._invoke_api") as m:
                m.return_value = Mock(content="{}", usage={},
                                     duration_s=1.0)
                llm_invoke("p", backend=backend, timeout_s=1800)
                called_timeout = m.call_args[0][2]
                assert called_timeout == 1800

    def test_per_backend_timeout_honored(self):
        backend = _cfg(type="api", timeout_s=900)
        with patch.dict(os.environ, {"K": "sk-test"}):
            with patch("code_forge.llm_invoke._invoke_api") as m:
                m.return_value = Mock(content="{}", usage={},
                                     duration_s=1.0)
                llm_invoke("p", backend=backend)
                called_timeout = m.call_args[0][2]
                assert called_timeout == 900


class TestErrorMessageBackendName:
    """Error messages must contain backend.name, not backend.format."""

    def test_timeout_error_contains_backend_name(self):
        backend = _cfg(name="my-mimo", type="api", format="openai")
        with patch.dict(os.environ, {"K": "sk-test"}):
            with patch("code_forge.llm_invoke._invoke_openai",
                       side_effect=TimeoutError("timed out")):
                with pytest.raises(LLMInvokeError) as exc_info:
                    llm_invoke("p", backend=backend)
                msg = str(exc_info.value)
                assert "my-mimo" in msg, (
                    "timeout error must name the backend, got: %s" % msg
                )
                assert "openai" not in msg, (
                    "timeout error must not contain format name, got: %s"
                    % msg
                )


# -- Wave 4: SSE streaming tests --------------------------------------

from code_forge.llm_invoke import _read_sse  # noqa: E402
from code_forge.errors import CliError  # noqa: E402


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

    def test_first_token_emit(self):
        """First content delta emits exactly one first-token progress event."""
        with patch("code_forge.llm_invoke.progress.emit") as mock_emit:
            # Zero events while only role/reasoning deltas are consumed.
            preamble = _sse_lines(
                {"choices": [{"delta": {"role": "assistant"}}]},
                {"choices": [{"delta": {"reasoning_content": "think..."}}]},
            )
            _read_sse(preamble, backend_name="test")
            assert mock_emit.call_count == 0

            resp = _sse_lines(
                {"choices": [{"delta": {"content": "Hello"}}]},
                {"choices": [{"delta": {"content": " world"}}]},
            )
            result = _read_sse(resp, backend_name="test")
            assert result["choices"][0]["message"]["content"] == "Hello world"
            assert mock_emit.call_count == 1
            assert mock_emit.call_args[0][0] == "backend test: first token"

    def test_no_emit_without_content(self):
        """Reasoning-only + error stream emits nothing; error dict returned."""
        with patch("code_forge.llm_invoke.progress.emit") as mock_emit:
            resp = _sse_lines(
                {"choices": [{"delta": {"reasoning_content": "think..."}}]},
                {"error": {"message": "rate limit", "code": 429}},
            )
            result = _read_sse(resp, backend_name="test")
            assert "error" in result
            mock_emit.assert_not_called()

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
        from code_forge.llm_invoke import invoke_sampling, Usage
        from mcp.types import CreateMessageResult, TextContent
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


class TestStreamFlagOnTheWire:
    """stream reaches the request body, not just the params dict.

    _apply_params setting the key proves nothing about what is sent: only
    reading it back off the captured request does.
    """

    def test_non_streaming_request_carries_stream_false(self):
        backend = BackendConfig(
            name="test", type="api", model="m", format="openai",
            base_url="https://example.com", api_key_env="TEST_KEY",
            max_tokens=1024, stream=False,
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

        assert "stream" in captured_body, (
            "stream omitted; the server picks its own default and "
            "OmniRoute picks SSE"
        )
        assert captured_body["stream"] is False


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

    def test_ceiling_overrides_max_completion_tokens(self):
        """output_ceiling takes priority over max_completion_tokens too."""
        backend = BackendConfig(
            name="test", type="api", model="m", format="openai",
            base_url="https://example.com", api_key_env="TEST_KEY",
            max_tokens=16384, max_completion_tokens=8192,
            output_ceiling=65536,
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

        # max_completion_tokens=8192 would be used without ceiling;
        # ceiling=65536 overrides both
        assert captured_body["max_completion_tokens"] == 65536

    def test_ceiling_works_on_anthropic_format(self):
        """output_ceiling overrides cap on anthropic format (max_tokens key)."""
        backend = BackendConfig(
            name="test", type="api", model="m", format="anthropic",
            base_url="https://example.com", api_key_env="TEST_KEY",
            max_tokens=16384, output_ceiling=65536,
        )
        captured_body = {}

        def fake_urlopen(req, timeout=None):
            captured_body.update(json.loads(req.data.decode()))
            resp = Mock()
            resp.read.return_value = json.dumps({
                "content": [{"type": "text", "text": '{"findings": []}'}],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }).encode()
            resp.__enter__ = Mock(return_value=resp)
            resp.__exit__ = Mock(return_value=False)
            return resp

        with patch.dict(os.environ, {"TEST_KEY": "sk-test"}), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            llm_invoke("prompt", backend=backend)

        # anthropic uses "max_tokens" key
        assert captured_body["max_tokens"] == 65536

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


class TestVertexURLErrorRetryable:
    """vertex URLError must be explicitly retryable, matching openai/anthropic."""

    def test_vertex_urlerror_is_retryable(self):
        backend = _make_vertex_backend()
        mock_creds = MagicMock()
        mock_creds.token = "fake"
        url_error = urllib.error.URLError("connection refused")
        with patch(
                 "google.oauth2.service_account.Credentials"
                 ".from_service_account_file",
                 return_value=mock_creds), \
             patch("google.auth.default", return_value=(mock_creds, "p")), \
             patch("google.auth.transport.requests.Request"), \
             patch("urllib.request.urlopen", side_effect=url_error), \
             patch("time.sleep"):
            with pytest.raises(LLMInvokeError, match="URLError") as exc:
                llm_invoke("prompt", backend=backend)
            assert exc.value.retryable is True


class TestReadWithDeadline:
    """Deadline-aware read helper enforces total wall time."""

    def test_fast_read_succeeds(self):
        """Normal fast response completes within deadline."""
        from code_forge.llm_invoke import _read_with_deadline
        import io
        data = b'{"choices": [{"message": {"content": "ok"}}]}'
        response = io.BytesIO(data)
        deadline = time.monotonic() + 10
        result = _read_with_deadline(response, deadline, "test")
        assert result == data

    def test_slow_drip_raises_timeout(self):
        """Slow read that exceeds deadline triggers timeout."""
        from code_forge.llm_invoke import _read_with_deadline, LLMInvokeError

        class SlowRead:
            """Simulates a slow response: blocks past deadline."""
            def read(self):
                time.sleep(0.3)
                return b'{"ok": true}'
            def close(self):
                pass

        deadline = time.monotonic() + 0.1
        with pytest.raises(LLMInvokeError, match="total read deadline"):
            _read_with_deadline(SlowRead(), deadline, "test")

    def test_deadline_already_expired(self):
        """Pre-expired deadline (checked before read) raises."""
        from code_forge.llm_invoke import _read_with_deadline, LLMInvokeError
        import io

        response = io.BytesIO(b"ok")
        deadline = time.monotonic() - 1  # already expired
        with pytest.raises(LLMInvokeError, match="total read deadline"):
            _read_with_deadline(response, deadline, "test")

    def test_backend_name_in_error(self):
        """Error message includes backend name."""
        from code_forge.llm_invoke import _read_with_deadline, LLMInvokeError

        class SlowRead:
            def read(self):
                time.sleep(0.3)
                return b"x"
            def close(self):
                pass

        deadline = time.monotonic() + 0.1
        with pytest.raises(LLMInvokeError, match="my-backend"):
            _read_with_deadline(SlowRead(), deadline, "my-backend")


class TestReadSSEDeadline:
    """_read_sse enforces total wall deadline per line."""

    def test_fast_sse_succeeds(self):
        """Normal SSE stream completes within deadline."""
        from code_forge.llm_invoke import _read_sse
        lines = [
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            b'"usage":{"prompt_tokens":1,"completion_tokens":1}}\n',
            b'data: [DONE]\n',
        ]
        response = iter(lines)
        deadline = time.monotonic() + 10
        result = _read_sse(response, deadline=deadline, backend_name="test")
        assert "hello" in str(result)

    def test_slow_sse_raises_timeout(self):
        """Slow SSE stream triggers deadline."""
        from code_forge.llm_invoke import _read_sse, LLMInvokeError

        def _slow_lines():
            for i in range(100):
                time.sleep(0.05)
                yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n'

        deadline = time.monotonic() + 0.1
        with pytest.raises(LLMInvokeError, match="total read deadline"):
            _read_sse(_slow_lines(), deadline=deadline, backend_name="test")

    def test_no_deadline_no_check(self):
        """When deadline=None, no deadline check (backward compat)."""
        from code_forge.llm_invoke import _read_sse
        lines = [
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
            b'data: [DONE]\n',
        ]
        result = _read_sse(iter(lines), deadline=None, backend_name="test")
        assert "ok" in str(result)


class TestReadWithDeadlineRealPath:
    """Real-path tests using actual socket (Golden Rule #3)."""

    def test_real_drip_interrupted_at_deadline(self):
        """Real drip server: wall bounded + zombie reader exits promptly."""
        import socket as _socket
        from code_forge.llm_invoke import (
            _read_with_deadline, LLMInvokeError,
        )

        BODY = b'{"ok": true}'
        DRIP_INTERVAL = 0.3
        DRIP_CHUNKS = 6  # ~1.8s total
        TIMEOUT = 0.5

        def _drip_server(port_box, ready):
            srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port_box.append(srv.getsockname()[1])
            ready.set()
            conn, _ = srv.accept()
            try:
                req = b""
                while b"\r\n\r\n" not in req:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    req += chunk
                headers = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: "
                    + str(len(BODY)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                )
                conn.sendall(headers)
                step = max(1, len(BODY) // DRIP_CHUNKS)
                for i in range(0, len(BODY), step):
                    try:
                        conn.sendall(BODY[i : i + step])
                    except OSError:
                        break  # client force-closed
                    time.sleep(DRIP_INTERVAL)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
                srv.close()

        import urllib.request
        port_box = []
        ready = threading.Event()
        srv_t = threading.Thread(
            target=_drip_server, args=(port_box, ready), daemon=True
        )
        srv_t.start()
        ready.wait()

        baseline_threads = threading.active_count()
        url = "http://127.0.0.1:%d/" % port_box[0]
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        deadline = time.monotonic() + TIMEOUT
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            with pytest.raises(LLMInvokeError, match="total read"):
                _read_with_deadline(resp, deadline, "test")
        elapsed = time.monotonic() - t0
        # Wall must be ~TIMEOUT, not ~DRIP_CHUNKS * DRIP_INTERVAL
        assert elapsed < DRIP_CHUNKS * DRIP_INTERVAL * 0.5, (
            "wall %.2fs should be ~%.1fs, not %.1fs"
            % (elapsed, TIMEOUT, DRIP_CHUNKS * DRIP_INTERVAL)
        )
        # Zombie-death: reader thread must exit promptly after
        # shutdown wakes recv.  With os.close the zombie lingers
        # one drip interval+; with shutdown it exits immediately.
        for _ in range(20):
            if threading.active_count() <= baseline_threads:
                break
            time.sleep(0.05)
        assert threading.active_count() <= baseline_threads, (
            "zombie reader thread lingered: %d > %d baseline"
            % (threading.active_count(), baseline_threads)
        )
        srv_t.join(timeout=1)

    def test_sse_backend_name_in_error(self):
        """_read_sse includes backend_name in timeout error."""
        from code_forge.llm_invoke import _read_sse, LLMInvokeError

        def _slow_lines():
            for i in range(100):
                time.sleep(0.05)
                yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n'

        deadline = time.monotonic() + 0.1
        with pytest.raises(LLMInvokeError, match="my-backend"):
            _read_sse(
                _slow_lines(),
                deadline=deadline,
                backend_name="my-backend",
            )


# -- effective_invoke_timeout_s --


class TestEffectiveInvokeTimeoutS:
    """Tests for the shared timeout-resolution helper."""

    def test_backend_timeout_s_wins(self):
        from code_forge.llm_invoke import effective_invoke_timeout_s
        be = BackendConfig(
            name="mimo", type="api", model="x",
            timeout_s=1800, format=None,
        )
        assert effective_invoke_timeout_s(be) == 1800

    def test_api_default_no_explicit(self):
        from code_forge.llm_invoke import effective_invoke_timeout_s
        be = BackendConfig(
            name="api-default", type="api", model="x",
            timeout_s=0, format=None,
        )
        # API default: DEFAULT_TIMEOUT_S=1800 capped to _API_TIMEOUT_CAP_S=600
        assert effective_invoke_timeout_s(be) == 600

    def test_cli_default_no_explicit(self):
        from code_forge.llm_invoke import effective_invoke_timeout_s
        be = DEFAULT_BACKEND  # type=cli, timeout_s=0
        # CLI default: DEFAULT_TIMEOUT_S=1800 capped to _CLI_TIMEOUT_CAP_S=300
        assert effective_invoke_timeout_s(be) == 300

    def test_caller_explicit_wins_over_env(self, monkeypatch):
        from code_forge.llm_invoke import effective_invoke_timeout_s
        monkeypatch.setenv("FORGE_LLM_TIMEOUT_S", "999")
        be = BackendConfig(
            name="x", type="api", model="x",
            timeout_s=0, format=None,
        )
        # caller explicit 500 > env 999?  No: caller_explicit wins.
        assert effective_invoke_timeout_s(be, timeout_s=500) == 500

    def test_env_override_wins_over_default(self, monkeypatch):
        from code_forge.llm_invoke import effective_invoke_timeout_s
        monkeypatch.setenv("FORGE_LLM_TIMEOUT_S", "42")
        be = BackendConfig(
            name="x", type="api", model="x",
            timeout_s=0, format=None,
        )
        # env 42 < _API_TIMEOUT_CAP_S, so no cap applied
        assert effective_invoke_timeout_s(be) == 42

    def test_backend_timeout_s_bypasses_cap(self):
        from code_forge.llm_invoke import effective_invoke_timeout_s
        be = BackendConfig(
            name="slow", type="api", model="x",
            timeout_s=3600, format=None,
        )
        # backend.timeout_s > 0 -> no cap
        assert effective_invoke_timeout_s(be) == 3600

    def test_helper_matches_invoke_path(self):
        """Helper return matches what invoke() applies for the same config."""
        from code_forge.llm_invoke import effective_invoke_timeout_s
        # For a CLI backend with timeout_s=0, invoke() caps at _CLI_TIMEOUT_CAP_S=300
        be = DEFAULT_BACKEND
        assert effective_invoke_timeout_s(be) == 300
        # For an API backend with timeout_s=0, invoke() caps at _API_TIMEOUT_CAP_S=600
        be_api = BackendConfig(
            name="api", type="api", model="x",
            timeout_s=0, format=None,
        )
        assert effective_invoke_timeout_s(be_api) == 600
