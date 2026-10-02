"""Builder identity checks cover allowed and refused environments."""

import subprocess

import pytest

from code_forge.mutation_engines.adapters.builder_support import (
    BuilderUnavailable,
    identity_mapping_error,
    require_identity_mapping,
)


@pytest.fixture
def mapping_host(monkeypatch):
    import code_forge.mutation_engines.adapters.builder_support as support

    class HostFile:
        def __init__(self, path):
            self.path = path

        def is_file(self):
            return True

        def read_text(self, *, encoding):
            assert encoding == "utf-8"
            return {
                "/proc/self/status": "NoNewPrivs:\t0\n",
                "/etc/subuid": "builder:100000:65536\n",
            }[self.path]

    monkeypatch.setenv("USER", "builder")
    monkeypatch.setattr(support, "Path", HostFile)


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


@pytest.mark.parametrize(
    "code,stdout,stderr,expected",
    [
        (0, "", "", None),
        (1, "", "unshare: permission denied\n", "permission denied"),
        (1, "mapping unavailable\n", "", "mapping unavailable"),
        (1, "", "", "unshare exited 1"),
    ],
)
def test_namespace_probe_result_is_reported(mapping_host, monkeypatch, code, stdout, stderr, expected):
    def probe(argv, **kwargs):
        assert argv == ["unshare", "--user", "--map-root-user", "true"]
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "timeout": 10,
            "check": False,
        }
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    monkeypatch.setattr("code_forge.mutation_engines.adapters.builder_support.subprocess.run", probe)
    reason = identity_mapping_error()
    if expected is None:
        assert reason is None
        assert require_identity_mapping() is None
    else:
        assert reason is not None and expected in reason
        with pytest.raises(BuilderUnavailable, match=expected):
            require_identity_mapping()


def test_absent_subuid_file_is_refused(monkeypatch):
    """No subuid file means no range, not an allowed build."""
    monkeypatch.setenv("USER", "builder")
    reason = identity_mapping_error("NoNewPrivs:\t0\n", "")
    assert reason is not None
    assert "subordinate uid" in reason


def test_missing_unshare_is_a_refusal(mapping_host, monkeypatch):
    """A missing unshare is a refusal, not a crash."""

    def boom(*args, **kwargs):
        raise FileNotFoundError("unshare")

    monkeypatch.setattr("code_forge.mutation_engines.adapters.builder_support.subprocess.run", boom)
    reason = identity_mapping_error()
    assert reason is not None
    assert "unshare" in reason


def test_namespace_probe_timeout_is_a_refusal(mapping_host, monkeypatch):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr("code_forge.mutation_engines.adapters.builder_support.subprocess.run", timeout)
    with pytest.raises(BuilderUnavailable, match="user namespace probe failed"):
        require_identity_mapping()


def test_prefix_user_is_not_a_match(monkeypatch):
    """builder must not inherit a range owned by builder-extra."""
    monkeypatch.setenv("USER", "builder")
    reason = identity_mapping_error("NoNewPrivs:\t0\n", "builder-extra:100000:65536\n")
    assert reason is not None
    assert "subordinate uid" in reason
