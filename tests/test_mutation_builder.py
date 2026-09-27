"""The builder refuses a host that cannot map identities."""

import pytest

from code_forge.mutation_engines.adapters.builder_support import (
    BuilderUnavailable,
    identity_mapping_error,
    require_identity_mapping,
)


def test_nonewprivs_is_refused():
    reason = identity_mapping_error("NoNewPrivs:\t1\n", "builder:100000:65536\n")
    assert reason is not None
    assert "NoNewPrivs" in reason


def test_a_mapped_host_is_allowed(monkeypatch):
    monkeypatch.setenv("USER", "builder")
    assert identity_mapping_error("NoNewPrivs:\t0\n", "builder:100000:65536\n") is None


def test_missing_subuid_range_is_refused(monkeypatch):
    monkeypatch.setenv("USER", "builder")
    reason = identity_mapping_error("NoNewPrivs:\t0\n", "other:100000:65536\n")
    assert reason is not None
    assert "subordinate uid" in reason
    with pytest.raises(BuilderUnavailable, match="subordinate uid"):
        monkeypatch.setattr(
            "code_forge.mutation_engines.adapters.builder_support.identity_mapping_error",
            lambda: reason,
        )
        require_identity_mapping()


def test_this_session_cannot_map():
    reason = identity_mapping_error()
    assert reason is not None


def test_absent_subuid_file_is_refused(monkeypatch):
    """No subuid file means no range, not an allowed build."""
    monkeypatch.setenv("USER", "builder")
    reason = identity_mapping_error("NoNewPrivs:\t0\n", "")
    assert reason is not None
    assert "subordinate uid" in reason


def test_missing_unshare_is_a_refusal(monkeypatch):
    """A missing unshare is a refusal, not a crash."""
    def boom(*args, **kwargs):
        raise FileNotFoundError("unshare")

    monkeypatch.setattr(
        "code_forge.mutation_engines.adapters.builder_support.subprocess.run", boom
    )
    reason = identity_mapping_error()
    assert reason is not None
    assert "unshare" in reason


def test_prefix_user_is_not_a_match(monkeypatch):
    """builder must not inherit a range owned by builder-extra."""
    monkeypatch.setenv("USER", "builder")
    reason = identity_mapping_error("NoNewPrivs:\t0\n", "builder-extra:100000:65536\n")
    assert reason is not None
    assert "subordinate uid" in reason
