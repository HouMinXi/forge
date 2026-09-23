"""Pin every status branch of the backend error text.

The older check only looks at HTTP 402. A mutated comparison can move
403, 401, 429 and the 500 boundary onto another sentence and still pass.
"""

import pytest

from code_forge.llm_invoke import _format_error_message


class TestFormatErrorMessageBranches:
    @pytest.mark.parametrize(
        ("code", "problem", "tip"),
        [
            (402, "payment required", "Top up account balance"),
            (403, "forbidden", "Check API key permissions"),
            (401, "unauthorized", "Check API key configuration"),
            (429, "rate limited", "Retry after a short wait"),
            (500, "server error", "Retry or check provider status page"),
            (503, "server error", "Retry or check provider status page"),
            (404, "HTTP error", "Check provider documentation"),
        ],
    )
    def test_status_selects_its_own_sentence(self, code, problem, tip):
        assert _format_error_message("deepseek", code, "") == (
            f"code-forge: deepseek backend: {problem} ({code}). {tip}"
        )

    def test_body_is_appended_after_the_sentence(self):
        assert _format_error_message("deepseek", 404, "no such path") == (
            "code-forge: deepseek backend: HTTP error (404). "
            "Check provider documentation; body: no such path"
        )
