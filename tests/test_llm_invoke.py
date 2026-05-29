import json
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from code_forge.llm_invoke import llm_invoke, LLMInvokeError


class TestLLMInvoke:
    def test_returns_parsed_json_on_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"findings": []})
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = llm_invoke("review this code", model="claude-sonnet-4-6")

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
