"""Pin each provider error code to its own suggestion sentence.

Membership in the code tuple and the sentence text are both part of
the contract. A code moved out of its tuple, or a sentence rewritten,
must fail here.
"""

import pytest

from code_forge.llm_invoke import _suggestion


class TestSuggestionSentences:
    @pytest.mark.parametrize(
        ("provider", "code", "sentence"),
        [
            ("zhipu", "1113", "Top up at open.bigmodel.cn"),
            ("zhipu", "1008", "Top up at open.bigmodel.cn"),
            ("minimax", "1113", "Top up at platform.minimaxi.com"),
            ("minimax", "1008", "Top up at platform.minimaxi.com"),
            ("other", "1113", "Check provider status page"),
            ("zhipu", "1302", "Retry after a short wait or reduce request rate"),
            ("zhipu", "1002", "Retry after a short wait or reduce request rate"),
            ("zhipu", "1305", "Retry after a short wait or reduce request rate"),
            ("zhipu", "1041", "Retry after a short wait or reduce request rate"),
            ("zhipu", "2045", "Retry after a short wait or reduce request rate"),
            ("zhipu", "1000", "Check API key configuration"),
            ("zhipu", "1001", "Check API key configuration"),
            ("zhipu", "2049", "Check API key configuration"),
            ("zhipu", "1039", "Reduce prompt size or increase token limit"),
            ("zhipu", "1308", "Check usage limits on provider dashboard"),
            ("zhipu", "1309", "Check usage limits on provider dashboard"),
            ("zhipu", "2056", "Check usage limits on provider dashboard"),
            ("zhipu", "9999", "Check provider status page"),
        ],
    )
    def test_code_selects_its_own_sentence(self, provider, code, sentence):
        assert _suggestion(provider, code) == sentence
