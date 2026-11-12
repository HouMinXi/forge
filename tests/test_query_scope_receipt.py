"""Local-only receipt codec/transport controls; not an approved public contract."""

from dataclasses import replace
import json
from uuid import uuid4
import pytest
from code_forge import _advisory_diagnostics as storage
from code_forge import _query_scope_receipt as scope


@pytest.fixture
def context():
    return storage.ExpectedContext(str(uuid4()), str(uuid4()), "source", "a" * 64, 1)


@pytest.fixture
def binding(tmp_path):
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    return storage.bind_storage(root)


@pytest.fixture
def facts():
    return dict(
        scope="established",
        registered=2,
        attempted=1,
        completed=1,
        unavailable=1,
        with_callers=0,
        query_occurrences_accounted=True,
    )


def path(publication):
    locator = publication.locator
    return locator.binding.source_root.joinpath(
        *(name for name, _ in locator.directories), locator.filename
    )


def test_real_fs_closed_namespace_and_old_v1_unchanged(binding, context, facts):
    pub = scope.publish(binding, context, facts)
    assert pub.status == "published" and scope.read(pub, context) == scope.ScopeSnapshot(**facts)
    assert "query-scope-receipts" in path(pub).parts
    old = storage.publish_observation(binding, storage.make_observation(context, "targets_resolved"))
    assert old.status == "published" and path(old) != path(pub)
    assert storage.read_observation(old, context).analysis_state == "targets_resolved"
    with pytest.raises(storage.DiagnosticError):
        storage.read_observation(pub, context)
    with pytest.raises(storage.DiagnosticError):
        scope.read(old, context)
    before = path(pub).read_bytes()
    repeat = scope.publish(binding, context, facts)
    assert repeat.status == "not_published" and repeat.failure == "name_conflict"
    assert path(pub).read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("registered", True),
        ("registered", -1),
        ("registered", 257),
        ("attempted", 3),
        ("completed", 2),
        ("with_callers", 2),
        ("unavailable", 2),
        ("query_occurrences_accounted", False),
        ("query_occurrences_accounted", 1),
        ("scope", "complete"),
        ("scope", "not_reached"),
        ("extra", 1),
    ],
)
def test_bad_closed_facts_rejected_before_creating_paths(binding, context, facts, field, value):
    facts[field] = value
    with pytest.raises(storage.DiagnosticError):
        scope.encode(context, facts)
    assert scope.publish(binding, context, facts).status == "not_published"
    assert not (binding.source_root / ".code-forge").exists()


@pytest.mark.parametrize("state", ["not_reached", "unavailable"])
def test_unknown_does_not_fabricate_zero_counts(context, state):
    facts = scope.ScopeSnapshot(scope=state)
    assert scope.decode(scope.encode(context, facts), context) == facts
    with pytest.raises(storage.DiagnosticError):
        scope.encode(context, replace(facts, registered=0))


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ("repository_id", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ("source_hash", "other"),
        ("diff_sha256", "b" * 64),
        ("observation_sequence", 2),
    ],
)
def test_context_replay_refused_by_decoder_and_real_reader(binding, context, facts, field, value):
    foreign = replace(context, **{field: value})
    pub = scope.publish(binding, context, facts)
    raw = path(pub).read_bytes()
    with pytest.raises(storage.DiagnosticError, match="context_mismatch"):
        scope.decode(raw, foreign)
    with pytest.raises(storage.DiagnosticError):
        scope.read(pub, foreign)


@pytest.mark.parametrize(
    "change",
    [
        "extra-envelope",
        "extra-context",
        "extra-snapshot",
        "missing",
        "duplicate",
        "oversize",
        "wrong-version",
    ],
)
def test_strict_codec_refuses_malformed(context, facts, change):
    raw = scope.encode(context, facts)
    doc = json.loads(raw)
    if change == "extra-envelope":
        doc["extra"] = 1
    elif change == "extra-context":
        doc["context"]["extra"] = 1
    elif change == "extra-snapshot":
        doc["snapshot"]["extra"] = 1
    elif change == "missing":
        del doc["snapshot"]["completed"]
    elif change == "wrong-version":
        doc["envelope"] = "forge-cross-repo-query-scope-v2"
    if change == "duplicate":
        token = b'"completed":1'
        assert raw.count(token) == 1
        raw = raw.replace(token, token + b"," + token)
    elif change == "oversize":
        raw = raw + b" " * 4096
    else:
        raw = json.dumps(doc).encode()
    with pytest.raises(storage.DiagnosticError):
        scope.decode(raw, context)


@pytest.mark.parametrize("change", ["tamper", "replace", "missing"])
def test_real_record_changes_refused(binding, context, facts, change):
    pub = scope.publish(binding, context, facts)
    target = path(pub)
    original = target.read_bytes()
    if change == "tamper":
        target.write_bytes(original.replace(b'"completed":1', b'"completed":0'))
    elif change == "replace":
        replacement = target.with_suffix(".replacement")
        replacement.write_bytes(original)
        replacement.chmod(0o600)
        assert replacement.stat().st_ino != target.stat().st_ino
        replacement.replace(target)
    else:
        target.unlink()
    with pytest.raises(storage.DiagnosticError):
        scope.read(pub, context)


def test_unknown_does_not_grant_locator_or_retry(binding, context, facts, monkeypatch):
    calls = []

    def failed(*args, **kwargs):
        calls.append(True)
        raise OSError("private link failure")

    monkeypatch.setattr(storage.os, "link", failed)
    pub = scope.publish(binding, context, facts)
    assert pub.status == "unknown" and pub.locator is None and calls == [True]
    with pytest.raises(storage.DiagnosticError):
        scope.read(pub, context)


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
def test_cancellation_preserves_original_and_cleans_temp(binding, context, facts, monkeypatch, kind):
    error = kind("private control")

    def failed(*args, **kwargs):
        raise error

    monkeypatch.setattr(storage.os, "write", failed)
    with pytest.raises(kind) as caught:
        scope.publish(binding, context, facts)
    assert caught.value is error
    assert not list(binding.source_root.rglob(".pending-*"))


@pytest.mark.parametrize("sequence", [0, 65, True])
def test_sequence_budget_before_creation(binding, context, facts, sequence):
    expected = replace(context, observation_sequence=sequence)
    assert scope.publish(binding, expected, facts).status == "not_published"
    assert not (binding.source_root / ".code-forge").exists()


@pytest.mark.parametrize("size", [4096, 4097])
def test_exact_codec_byte_limit(context, facts, size):
    raw = scope.encode(context, facts)
    padded = raw + b" " * (size - len(raw))
    assert json.loads(padded) == json.loads(raw)
    if size == 4096:
        assert scope.decode(padded, context) == scope.ScopeSnapshot(**facts)
    else:
        with pytest.raises(storage.DiagnosticError):
            scope.decode(padded, context)


def test_commit_then_error_reconciles_owned_bytes(binding, context, facts, monkeypatch):
    original = storage.os.link
    calls = []

    def linked(*args, **kwargs):
        calls.append(True)
        original(*args, **kwargs)
        raise OSError("private after commit")

    monkeypatch.setattr(storage.os, "link", linked)
    pub = scope.publish(binding, context, facts)
    assert pub.status == "published" and calls == [True]
    assert scope.read(pub, context) == scope.ScopeSnapshot(**facts)


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
def test_read_control_preserves_exception(binding, context, facts, monkeypatch, kind):
    pub = scope.publish(binding, context, facts)
    error = kind("private read control")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(storage, "_read_leaf", fail)
    with pytest.raises(kind) as caught:
        scope.read(pub, context)
    assert caught.value is error


@pytest.mark.parametrize("count", [99, 100, 256])
def test_full_occurrence_budget_roundtrips_codec_and_real_storage(binding, context, count):
    facts = scope.ScopeSnapshot("established", count, count, count, 0, count, True)
    raw = scope.encode(context, facts)
    assert scope.decode(raw, context) == facts
    publication = scope.publish(binding, context, facts)
    assert publication.status == "published"
    assert scope.read(publication, context) == facts


@pytest.mark.parametrize("count", [257, 999])
def test_over_budget_wire_counts_rejected(context, facts, count):
    document = json.loads(scope.encode(context, facts))
    document["snapshot"].update(
        registered=count, attempted=count, completed=count, unavailable=0, with_callers=count
    )
    with pytest.raises(storage.DiagnosticError, match="invalid_schema"):
        scope.decode(json.dumps(document).encode(), context)


def test_long_integer_token_refused_before_integer_conversion(monkeypatch):
    def forbidden(value):
        pytest.fail("oversized token must not reach integer conversion")

    monkeypatch.setattr(scope, "int", forbidden, raising=False)
    with pytest.raises(storage.DiagnosticError, match="invalid_schema"):
        scope._bounded_integer("9" * 10000)


def test_long_wire_integer_refused_within_byte_limit(context, facts):
    raw = scope.encode(context, facts)
    token = b'"registered":2'
    assert raw.count(token) == 1
    raw = raw.replace(token, b'"registered":' + b"9" * 300)
    assert len(raw) < 4096
    with pytest.raises(storage.DiagnosticError, match="invalid_schema"):
        scope.decode(raw, context)


@pytest.mark.parametrize("sequence", [65, 100, 256])
def test_three_digit_integer_parser_does_not_expand_sequence_contract(context, facts, sequence):
    document = json.loads(scope.encode(context, facts))
    document["context"]["observation_sequence"] = sequence
    with pytest.raises(storage.DiagnosticError, match="invalid_context"):
        scope.decode(json.dumps(document).encode(), context)
