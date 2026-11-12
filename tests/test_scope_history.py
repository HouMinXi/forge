"""Caller-selected ticket persistence; never unsigned-file authenticity."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import pytest
from code_forge import _advisory_diagnostics as storage
from code_forge import _query_scope_receipt as scope
from code_forge import _scope_history as history


@pytest.fixture
def sample(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(mode=0o700)
    tickets = tmp_path / "tickets"
    tickets.mkdir(mode=0o700)
    context = storage.ExpectedContext(str(uuid4()), str(uuid4()), "source", "a" * 64, 1)
    facts = scope.ScopeSnapshot("established", 2, 1, 1, 1, 0, True)
    publication = scope.publish(storage.bind_storage(root), context, facts)
    assert publication.status == "published"
    return root, tickets, context, facts, publication


def leaf(publication):
    loc = publication.locator
    return loc.binding.source_root.joinpath(*(n for n, _ in loc.directories), loc.filename)


def test_explicit_ticket_in_fresh_python_process(sample):
    root, tickets, expected, facts, pub = sample
    history.validate_ticket_directory(tickets)
    path = history.export_ticket(tickets, pub, expected)
    assert path.stat().st_mode & 0o777 == 0o600
    assert history.read_ticket(path) == facts
    script = "from dataclasses import asdict; import json,sys; from pathlib import Path; from code_forge._scope_history import read_ticket; print(json.dumps(asdict(read_ticket(Path(sys.argv[1]))),sort_keys=True))"
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == asdict(facts)
    assert str(root) not in result.stdout and expected.execution_id not in result.stdout
    raw = path.read_bytes()
    with pytest.raises(history.TicketError):
        history.export_ticket(tickets, pub, expected)
    assert path.read_bytes() == raw and not list(tickets.glob(".scope-ticket-pending-*"))


@pytest.mark.parametrize(
    "change", ["extra", "context", "root", "inode", "digest", "duplicate", "oversize", "wrong-format"]
)
def test_corrupt_or_rebound_ticket_refused(sample, change):
    root, tickets, expected, _, pub = sample
    path = history.export_ticket(tickets, pub, expected)
    raw = path.read_bytes()
    doc = json.loads(raw)
    if change == "extra":
        doc["extra"] = 1
    elif change == "context":
        doc["expected"]["source_hash"] = "foreign"
    elif change == "root":
        doc["locator"]["root"] = str(tickets)
    elif change == "inode":
        doc["locator"]["identity"]["inode"] += 1
    elif change == "digest":
        doc["locator"]["digest"] = "0" * 64
    elif change == "wrong-format":
        doc["format"] = "other"
    if change == "duplicate":
        token = b'"source_hash":"source"'
        assert raw.count(token) == 1
        raw = raw.replace(token, token + b"," + token)
    elif change == "oversize":
        raw = b" " * (history.MAX_TICKET_BYTES + 1)
    else:
        raw = json.dumps(doc).encode()
    path.write_bytes(raw)
    with pytest.raises(history.TicketError):
        history.read_ticket(path)


@pytest.mark.parametrize("change", ["record-bytes", "record-replace", "root-replace"])
def test_artifact_changes_are_not_rebound(sample, change):
    root, tickets, expected, _, pub = sample
    path = history.export_ticket(tickets, pub, expected)
    record = leaf(pub)
    if change == "record-bytes":
        record.write_bytes(record.read_bytes().replace(b'"completed":1', b'"completed":0'))
    elif change == "record-replace":
        replacement = record.with_suffix(".replacement")
        replacement.write_bytes(record.read_bytes())
        replacement.chmod(0o600)
        assert replacement.stat().st_ino != record.stat().st_ino
        replacement.replace(record)
    else:
        root.rename(root.with_name("original"))
        root.mkdir(mode=0o700)
    with pytest.raises(history.TicketError):
        history.read_ticket(path)


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o770])
def test_directory_mode_not_silently_repaired(sample, mode):
    _, tickets, expected, _, pub = sample
    tickets.chmod(mode)
    with pytest.raises(history.TicketError):
        history.validate_ticket_directory(tickets)
    with pytest.raises(history.TicketError):
        history.export_ticket(tickets, pub, expected)
    assert tickets.stat().st_mode & 0o777 == mode
    assert list(tickets.iterdir()) == []


@pytest.mark.parametrize("change", ["mode", "symlink", "fifo"])
def test_ticket_mode_symlink_fifo_refused_without_blocking(sample, change):
    _, tickets, expected, _, pub = sample
    path = history.export_ticket(tickets, pub, expected)
    if change == "mode":
        path.chmod(0o644)
    else:
        saved = path.with_suffix(".saved")
        path.rename(saved)
        if change == "symlink":
            path.symlink_to(saved)
        else:
            os.mkfifo(path, 0o600)
    with pytest.raises(history.TicketError):
        history.read_ticket(path)


def test_export_requires_matching_strictly_read_record(sample, monkeypatch):
    _, tickets, expected, _, pub = sample
    with pytest.raises(history.TicketError):
        history.export_ticket(tickets, pub, replace(expected, diff_sha256="b" * 64))
    unknown = storage.PublicationResult("unknown", failure="commit_uncertain")
    with pytest.raises(history.TicketError):
        history.export_ticket(tickets, unknown, expected)
    leaf(pub).unlink()
    with pytest.raises(history.TicketError):
        history.export_ticket(tickets, pub, expected)
    assert list(tickets.iterdir()) == []


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("committed", [False, True])
def test_cancel_preserves_identity_cleans_owned_temp_and_never_retries(
    sample, monkeypatch, kind, committed
):
    _, tickets, expected, facts, pub = sample
    error = kind("control")
    calls = []
    original = history.os.link

    def fail(*args, **kwargs):
        calls.append(True)
        if committed:
            original(*args, **kwargs)
        raise error

    monkeypatch.setattr(history.os, "link", fail)
    with pytest.raises(kind) as caught:
        history.export_ticket(tickets, pub, expected)
    assert caught.value is error and calls == [True]
    assert not list(tickets.glob(".scope-ticket-pending-*"))
    finals = list(tickets.glob("*.scope-ticket.json"))
    assert len(finals) == int(committed)
    if committed:
        assert history.read_ticket(finals[0]) == facts


def test_directory_must_exist_and_be_canonical(sample):
    _, tickets, expected, _, pub = sample
    missing = tickets / "missing"
    with pytest.raises(history.TicketError):
        history.export_ticket(missing, pub, expected)
    assert not missing.exists()
    alias = tickets.with_name("alias")
    alias.symlink_to(tickets, target_is_directory=True)
    with pytest.raises(history.TicketError):
        history.export_ticket(alias, pub, expected)
    assert list(tickets.iterdir()) == []


def test_unsigned_ticket_requires_caller_trust_not_origin_authentication(sample):
    _, tickets, expected, facts, pub = sample
    first = history.export_ticket(tickets, pub, expected)
    another = replace(expected, execution_id=str(uuid4()))
    different = scope.ScopeSnapshot("established", 1, 1, 1, 0, 1, True)
    other_pub = scope.publish(pub.locator.binding, another, different)
    second = history.export_ticket(tickets, other_pub, another)
    # A same-UID actor able to rewrite this trusted credential can substitute
    # another complete valid credential. Permissions and hashes are not signing.
    first.write_bytes(second.read_bytes())
    assert history.read_ticket(first) == different
    assert different != facts


def test_cleanup_fault_cannot_replace_primary_control(sample, monkeypatch):
    _, tickets, expected, _, pub = sample
    primary = KeyboardInterrupt("primary")
    secondary = SystemExit("cleanup")
    cleanup = history.storage._cleanup_temp

    def fail_link(*args, **kwargs):
        raise primary

    def fail_cleanup(*args, **kwargs):
        cleanup(*args, **kwargs)
        raise secondary

    monkeypatch.setattr(history.os, "link", fail_link)
    monkeypatch.setattr(history.storage, "_cleanup_temp", fail_cleanup)
    with pytest.raises(KeyboardInterrupt) as caught:
        history.export_ticket(tickets, pub, expected)
    assert caught.value is primary
    assert list(tickets.iterdir()) == []


def test_ticket_read_control_is_not_converted_to_ordinary_failure(sample, monkeypatch):
    _, tickets, expected, _, pub = sample
    path = history.export_ticket(tickets, pub, expected)
    error = KeyboardInterrupt("read control")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(history.os, "read", fail)
    with pytest.raises(KeyboardInterrupt) as caught:
        history.read_ticket(path)
    assert caught.value is error
