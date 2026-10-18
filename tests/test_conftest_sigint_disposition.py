# SPDX-License-Identifier: Apache-2.0
"""Tests that raise SIGINT need Python's default handler under the chain."""

import signal

from code_forge import llm_invoke


def test_llm_invoke_chains_to_default_int_handler():
    # Fails when the suite is launched with SIGINT ignored (`pytest &`)
    # unless conftest restores default_int_handler before import.
    assert llm_invoke._original_sigint is signal.default_int_handler
