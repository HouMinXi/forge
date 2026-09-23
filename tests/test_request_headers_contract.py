"""Pin the request-header failure text the existing suite only samples.

The collision path already checks that the backend name, one header, and
PROTECTED_HEADER_KEYS appear somewhere in the message. That still lets a
mutant rewrite either sentence, swap the two header lists, or drop the
backend name from the earlier grammar check. These cases close that gap.
"""
import pytest

from code_forge.backend import BackendConfig
from code_forge.llm_invoke import LLMInvokeError, _request_headers


def _backend(**headers):
    return BackendConfig(
        name="deepseek",
        type="api",
        model="deepseek-chat",
        format="openai",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        headers=headers,
    )


class TestRequestHeaderMessages:
    def test_invalid_header_names_the_backend(self):
        backend = _backend(**{"not a token": "v"})
        with pytest.raises(LLMInvokeError) as exc:
            _request_headers({"Authorization": "Bearer k"}, backend)
        assert str(exc.value).startswith("backend 'deepseek': header name")

    def test_collision_message_is_exact(self):
        backend = _backend(**{
            "x-tenant-id": "acme",
            "x-omniroute-compression": "off",
        })
        base = {
            "Authorization": "Bearer sk-real",
            "x-tenant-id": "forge",
            "x-omniroute-compression": "on",
        }
        names = sorted(("x-tenant-id", "x-omniroute-compression"))
        clash = ", ".join(repr(k) for k in names)
        with pytest.raises(LLMInvokeError) as exc:
            _request_headers(base, backend)
        assert str(exc.value) == (
            "backend 'deepseek': configured header(s) "
            f"{clash} collide with what this "
            "request already sends, and were not refused by name -- so "
            "PROTECTED_HEADER_KEYS is missing "
            f"{clash}. That is a forge bug: "
            "config accepts a header the wire then overwrites."
        )
