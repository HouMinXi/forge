# SPDX-License-Identifier: Apache-2.0
"""The backend probe cache never resolves to the real home during tests."""

from pathlib import Path

from code_forge import backend


def test_default_probe_cache_dir_is_not_real_home():
    real = Path.home() / ".cache" / "code-forge"
    assert backend._default_cache_dir() != real
    assert backend.CACHE_FILENAME == "backend_probe_cache.json"
