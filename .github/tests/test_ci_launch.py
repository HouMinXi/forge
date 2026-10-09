"""Offline owner-push, bounded API, full checkout and immutable receipt tests."""

from __future__ import annotations

import copy
import datetime
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from forge_ci import launch, setup_policy as setup  # noqa: E402
from test_setup_policy import native_fixture  # noqa: E402


@pytest.fixture
def valid(monkeypatch):
    monkeypatch.setattr(
        setup.http.client, "HTTPSConnection", lambda *_a, **_k: pytest.fail("live API forbidden")
    )
    cfg, context, event = native_fixture()
    native = setup.validate_initial_identity(cfg, context, event)
    root = setup.API_ROOT
    run = {
        "id": 123,
        "run_attempt": 1,
        "run_number": 42,
        "workflow_id": 987,
        "workflow_url": root + "/actions/workflows/987",
        "url": root + "/actions/runs/123",
        "path": setup.CONFIG["workflow_path"],
        "event": "push",
        "head_branch": setup.CONFIG["full_ref"][11:],
        "head_sha": "c" * 40,
        "head_commit": {"id": "c" * 40},
        "status": "in_progress",
        "conclusion": None,
        "pull_requests": [],
        "repository": dict(copy.deepcopy(setup.REPOSITORY), private=False),
        "head_repository": dict(copy.deepcopy(setup.REPOSITORY), private=False),
        "actor": copy.deepcopy(setup.REPOSITORY["owner"]),
        "triggering_actor": copy.deepcopy(setup.REPOSITORY["owner"]),
    }
    started = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    job = {
        "id": 456,
        "run_id": 123,
        "run_attempt": 1,
        "name": "linux-tests",
        "head_sha": "c" * 40,
        "head_branch": setup.CONFIG["full_ref"][11:],
        "status": "in_progress",
        "conclusion": None,
        "completed_at": None,
        "started_at": started,
    }
    documents = {
        "/actions/workflows/linux-tests.yml": {
            "id": 987,
            "path": setup.CONFIG["workflow_path"],
            "state": "active",
            "url": root + "/actions/workflows/987",
        },
        "/actions/runs/123": run,
        "/actions/runs/123/attempts/1/jobs?per_page=100&page=1": {"total_count": 1, "jobs": [job]},
        "/git/ref/heads/fix/review-correctness-linux-ci": {
            "ref": setup.CONFIG["full_ref"],
            "object": {"type": "commit", "sha": "c" * 40},
        },
        "/git/commits/" + "c" * 40: {
            "sha": "c" * 40, "tree": {"sha": "d" * 40}, "parents": [{"sha": "a" * 40}],
        },
        "/actions/workflows/987/runs?head_sha="
        + "c" * 40
        + "&branch=fix%2Freview-correctness-linux-ci&event=push&per_page=100&page=1": {
            "total_count": 1,
            "workflow_runs": [copy.deepcopy(run)],
        },
    }
    calls = []

    def fetch(url):
        assert "/compare/" not in url, "removed comparison endpoint was requested"
        calls.append(url)
        return setup.canonical(documents[url.removeprefix(root)]), ""

    return dict(
        config=cfg,
        context=context,
        event=event,
        native=native,
        documents=documents,
        fetch=fetch,
        calls=calls,
        job=job,
        run=run,
    )


def verify(valid):
    return setup.live_identity(valid["config"], valid["native"], fetcher=valid["fetch"])


def test_existing_workflow_positive_run_number_numeric_job_and_readonly_rechecks(valid, monkeypatch):
    monkeypatch.setattr(
        setup, "claim_activation", lambda *_: pytest.fail("readonly API recheck claimed activation")
    )
    first = verify(valid)
    assert first["binding"]["run_number"] == 42 and first["binding"]["job_id"] == 456
    assert first["binding"]["job_key"] == "linux-tests" and first["binding"]["run_attempt"] == 1
    assert len(valid["calls"]) == 6
    second = setup.live_identity(valid["config"], first["binding"], fetcher=valid["fetch"])
    assert second["binding"] == first["binding"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_EVENT_NAME", "workflow_dispatch"),
        ("GITHUB_EVENT_NAME", "workflow_call"),
        ("GITHUB_REF", "refs/heads/main"),
        ("GITHUB_REF", "refs/pull/26/merge"),
        ("GITHUB_REF_TYPE", "tag"),
        ("GITHUB_RUN_ATTEMPT", "2"),
        ("GITHUB_RUN_ATTEMPT", "0"),
        ("GITHUB_RUN_NUMBER", "0"),
        ("GITHUB_RUN_NUMBER", "01"),
        ("GITHUB_RUN_NUMBER", True),
        ("GITHUB_RUN_ID", str(2**63)),
        ("GITHUB_JOB", "qualification"),
        ("GITHUB_REPOSITORY_ID", "123"),
        ("GITHUB_REPOSITORY_OWNER_ID", "1"),
        ("GITHUB_ACTOR_ID", "1"),
        ("GITHUB_TRIGGERING_ACTOR", "other"),
        ("GITHUB_WORKFLOW_SHA", "a" * 40),
        ("GITHUB_SHA", "C" * 40),
        ("GITHUB_WORKFLOW_REF", "HouMinXi/forge/.github/workflows/other.yml@refs/heads/main"),
        ("GITHUB_API_URL", "https://example.com"),
        ("GITHUB_SERVER_URL", "http://github.com"),
    ],
)
def test_native_rejection_precedes_network(valid, field, value):
    valid["context"][field] = value
    with pytest.raises(setup.SetupError):
        setup.validate_initial_identity(valid["config"], valid["context"], valid["event"])
    assert not valid["calls"]


@pytest.mark.parametrize("name", ["created", "forced", "deleted"])
@pytest.mark.parametrize("value", [True, 0, "false", None])
def test_push_flags_are_literal_false(valid, name, value):
    valid["event"][name] = value
    with pytest.raises(setup.SetupError):
        setup.validate_initial_identity(valid["config"], valid["context"], valid["event"])


@pytest.mark.parametrize(
    "change",
    [
        "zero_before",
        "same_head",
        "missing_head",
        "head_mismatch",
        "owner",
        "sender",
        "extra_config",
        "bool_config",
    ],
)
def test_closed_native_transition(valid, change):
    event = valid["event"]
    if change == "zero_before":
        event["before"] = "0" * 40
    elif change == "same_head":
        event["before"] = event["after"]
    elif change == "missing_head":
        event.pop("head_commit")
    elif change == "head_mismatch":
        event["head_commit"]["id"] = "a" * 40
    elif change == "owner":
        event["repository"]["owner"]["id"] += 1
    elif change == "sender":
        event["sender"]["id"] += 1
    elif change == "extra_config":
        valid["config"]["secret"] = "not-allowed"
    else:
        valid["config"]["schema_version"] = True
    with pytest.raises(setup.SetupError):
        setup.validate_initial_identity(valid["config"], valid["context"], event)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_attempt", 2),
        ("run_number", 1),
        ("id", True),
        ("workflow_id", 111),
        ("path", ".github/workflows/other.yml"),
        ("status", "completed"),
        ("status", "queued"),
        ("conclusion", "success"),
        ("head_sha", "a" * 40),
        ("head_branch", "main"),
        ("event", "pull_request"),
        ("workflow_url", "https://example.com"),
        ("url", "https://example.com"),
    ],
)
def test_current_run_mismatch_stops(valid, field, value):
    valid["run"][field] = value
    with pytest.raises(setup.SetupError):
        verify(valid)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", None),
        ("id", True),
        ("id", "456"),
        ("run_id", 999),
        ("run_attempt", 2),
        ("name", "linux-tests (matrix)"),
        ("status", "completed"),
        ("completed_at", "2026-10-08T16:00:00Z"),
        ("head_sha", "a" * 40),
        ("head_branch", "main"),
        ("started_at", "2000-01-01T00:00:00Z"),
        ("started_at", "2100-01-01T00:00:00Z"),
        ("started_at", "bad"),
    ],
)
def test_numeric_job_external_binding_is_strict(valid, field, value):
    valid["job"][field] = value
    with pytest.raises(setup.SetupError):
        verify(valid)


@pytest.mark.parametrize(
    "change",
    [
        "head_moved",
        "workflow_changed",
        "missing_job",
        "duplicate_job",
        "duplicate_run",
        "rerun",
        "partial",
        "commit_changed",
        "tree_invalid",
    ],
)
def test_replay_head_and_completeness(valid, change):
    docs = valid["documents"]
    jobs = next(v for k, v in docs.items() if "/jobs?" in k)
    runs = next(v for k, v in docs.items() if "/runs?" in k)
    if change == "head_moved":
        docs["/git/ref/heads/fix/review-correctness-linux-ci"]["object"]["sha"] = "a" * 40
    elif change == "workflow_changed":
        docs["/actions/workflows/linux-tests.yml"]["id"] = 999
    elif change == "missing_job":
        jobs.update(total_count=0, jobs=[])
    elif change == "duplicate_job":
        jobs["jobs"].append(copy.deepcopy(valid["job"]))
        jobs["total_count"] = 2
    elif change == "duplicate_run":
        runs["workflow_runs"].append(copy.deepcopy(valid["run"]))
        runs["total_count"] = 2
    elif change == "rerun":
        runs["workflow_runs"][0]["run_attempt"] = 2
    elif change == "partial":
        jobs["total_count"] = 2
    elif change == "commit_changed":
        docs["/git/commits/" + "c" * 40]["sha"] = "b" * 40
    else:
        docs["/git/commits/" + "c" * 40]["tree"]["sha"] = "bad"
    with pytest.raises(setup.SetupError):
        verify(valid)


def test_unrelated_provider_fields_are_not_authority(valid):
    valid["run"]["future_provider_field"] = {"notice": "ignored"}
    valid["run"]["pull_requests"] = [{"number": 26, "head": {"sha": "c" * 40}}]
    assert verify(valid)["binding"]["run_number"] == 42


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]", b"{}" * (setup.MAX_API // 2 + 1)]
)
def test_malformed_or_oversize_http_body_stops(valid, raw):
    valid["fetch"] = lambda _: (raw, "")
    with pytest.raises(setup.SetupError):
        verify(valid)


def test_deadline_and_request_and_total_bytes_are_finite(monkeypatch):
    reader = setup.MetadataReader({"/known"}, lambda _: (b"{}", ""))
    reader.deadline = 0
    with pytest.raises(setup.SetupError, match="deadline"):
        reader.get("/known")
    reader.deadline = float("inf")
    reader.requests = setup.MAX_REQUESTS
    with pytest.raises(setup.SetupError, match="request"):
        reader.get("/known")
    reader.requests = 0
    reader.total = setup.MAX_API_TOTAL
    with pytest.raises(setup.SetupError, match="total"):
        reader.get("/known")
    with pytest.raises(setup.SetupError, match="unreviewed"):
        reader.get("https://example.com")


@pytest.mark.parametrize("count", [0, 2, 100, 101, 200, 201, -1, True, 1.0, "1", None])
def test_collection_rejects_non_singleton_count_without_continuation(count):
    path = "/actions/test?per_page=100&page=1"
    calls = []

    def fetch(url):
        calls.append(url)
        return setup.canonical({"total_count": count, "items": [{}]}), ""

    reader = setup.MetadataReader({path}, fetch)
    with pytest.raises(setup.SetupError, match="missing or ambiguous"):
        reader.collection(path, "items")
    assert calls == [setup.API_ROOT + path] and reader.requests == 1


@pytest.mark.parametrize("items", [None, {}, True, [], [{}, {}]])
def test_collection_rejects_missing_partial_or_extra_items(items):
    path = "/actions/test?per_page=100&page=1"
    reader = setup.MetadataReader({path}, lambda _: (setup.canonical({"total_count": 1, "items": items}), ""))
    with pytest.raises(setup.SetupError, match="partial"):
        reader.collection(path, "items")
    assert reader.requests == 1


@pytest.mark.parametrize("link", [
    '<https://example.com>; rel="next"',
    '<' + setup.API_ROOT + '/actions/test?per_page=100&page=2>; rel="next"',
    '<' + setup.API_ROOT + '/actions/test?per_page=100&page=3>; rel="next"',
    '<' + setup.API_ROOT + '/actions/test?per_page=100&page=1>; rel="last"',
    "malformed",
])
def test_collection_rejects_any_pagination_without_following_it(link):
    path = "/actions/test?per_page=100&page=1"
    calls = []

    def fetch(url):
        calls.append(url)
        return setup.canonical({"total_count": 1, "items": [{}]}), link

    reader = setup.MetadataReader({path}, fetch)
    with pytest.raises(setup.SetupError, match="unexpected singleton pagination"):
        reader.collection(path, "items")
    assert calls == [setup.API_ROOT + path] and reader.requests == 1


def test_collection_accepts_exactly_one_complete_first_page():
    path = "/actions/test?per_page=100&page=1"
    reader = setup.MetadataReader({path}, lambda _: (b'{"total_count":1,"items":[{"id":123}]}', ""))
    assert reader.collection(path, "items") == [{"id": 123}]
    assert reader.requests == 1


def git(repo, *args):
    return (
        subprocess.check_output(
            ["/usr/bin/git", "-C", str(repo), *args],
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
        )
        .decode()
        .strip()
    )


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    for path in launch.HELPER_PATHS | {
        launch.WORKFLOW_PATH,
        launch.RENDERER_PATH,
        "src/product.py",
        ".github/metadata.json",
    }:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# " + path + "\n")
    (repo / "safe-link").symlink_to("src/product.py")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Offline Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return repo, git(repo, "rev-parse", "HEAD")


def test_full_source_includes_workflow_metadata_helpers_and_regular_symlinks(repository, monkeypatch):
    repo, head = repository
    source = launch.inspect_checkout(repo, head)
    records = []
    for relative, (mode, _) in launch._tree(repo, head).items():
        path = repo / relative
        raw = os.readlink(path).encode() if mode == "120000" else path.read_bytes()
        records.append(dict(path=relative, mode=mode, sha256=hashlib.sha256(raw).hexdigest()))
    assert source["source_sha256"] == launch.source_digest(records)
    assert source["candidate_sha"] == head and source["tree_oid"] == git(
        repo, "rev-parse", "HEAD^{tree}"
    )
    monkeypatch.setenv("GITHUB_SHA", head)
    monkeypatch.setenv("GITHUB_WORKFLOW_SHA", head)
    assert launch.verify_initial_checkout(repo, source["helper_sha256"]) == source


@pytest.mark.parametrize(
    "path",
    [
        launch.WORKFLOW_PATH,
        ".github/metadata.json",
        ".github/scripts/forge_ci/launch.py",
        "src/product.py",
    ],
)
def test_no_source_projection_exclusions(repository, path):
    repo, head = repository
    (repo / path).write_text("changed")
    with pytest.raises(launch.LaunchError, match="immutable tree"):
        launch.inspect_checkout(repo, head)


@pytest.mark.parametrize(
    "change",
    [
        "head",
        "mode",
        "escape",
        "untracked_import",
        "untracked_executable",
        "cache",
        "parent_link",
        "helper_link",
    ],
)
def test_checkout_rejects_identity_mode_escape_and_import_artifacts(repository, change):
    repo, head = repository
    if change == "head":
        head = "a" * 40
    elif change == "mode":
        (repo / "src/product.py").chmod(0o755)
    elif change == "escape":
        (repo / "safe-link").unlink()
        (repo / "safe-link").symlink_to(repo.parent)
    elif change == "untracked_import":
        (repo / "src/extra.py").write_text("x = 1")
    elif change == "untracked_executable":
        (repo / "evil").write_text("bad")
        (repo / "evil").chmod(0o700)
    elif change == "cache":
        (repo / "src/__pycache__").mkdir()
    elif change == "parent_link":
        (repo / "src").rename(repo / "other")
        (repo / "src").symlink_to("other")
    else:
        path = repo / ".github/scripts/forge_ci/launch.py"
        path.unlink()
        path.symlink_to("../../../src/product.py")
    with pytest.raises(launch.LaunchError):
        launch.inspect_checkout(repo, head)


def test_manifest_and_seed_api_removed():
    assert not hasattr(launch, "MANIFEST_PATH") and not hasattr(launch, "validate_manifest")
    assert not hasattr(launch, "fetch_public_attempt") and not hasattr(launch, "_native_context")


def test_source_and_live_receipt_closed_and_reusable(valid, tmp_path):
    source = {
        "candidate_sha": "c" * 40,
        "tree_oid": "d" * 40,
        "source_sha256": "b" * 64,
        "workflow_sha256": "e" * 64,
        "helper_sha256": dict.fromkeys(launch.HELPER_PATHS, "f" * 64),
    }
    live = verify(valid)
    receipt = launch.validate_receipt({"schema_version": 1, "status": "PASS", "binding": live["binding"], "source": source, "live": live})
    path = tmp_path / "receipt.json"
    launch.write_receipt(path, receipt)
    assert launch.load_receipt(path) == receipt
    receipt["source"]["excluded"] = []
    with pytest.raises(launch.LaunchError):
        launch.validate_receipt(receipt)


@pytest.mark.parametrize(
    "status,headers,body,expected",
    [
        (
            403,
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1791478000", "Retry-After": "60"},
            b"secret body",
            "X-RateLimit-Remaining",
        ),
        (429, {"Retry-After": "secret-invalid-header"}, b"secret body", '"status":429'),
        (302, {"Location": "https://example.com"}, b"", '"status":302'),
        (200, {"Content-Type": "text/plain"}, b"{}", "not JSON"),
        (200, {"Content-Type": "application/json", "Content-Encoding": "gzip"}, b"{}", "encoded"),
        (200, {"Content-Type": "application/json", "Content-Length": "3"}, b"{}", "truncated"),
    ],
)
def test_transport_rejects_http_redirect_rate_limit_and_partial_body(
    monkeypatch, status, headers, body, expected
):
    requests = []

    class Response:
        def getheader(self, name, default=None):
            return headers.get(name, default)

        def read(self, limit):
            assert limit == setup.MAX_API + 1
            return body

    response = Response()
    response.status = status

    class Connection:
        def request(self, method, path, headers):
            requests.append((method, path, headers))

        def getresponse(self):
            return response

        def close(self):
            requests.append("closed")

    def connection(host, timeout, context):
        assert host == "api.github.com" and 0 < timeout <= 5
        return Connection()

    monkeypatch.setattr(setup.http.client, "HTTPSConnection", connection)
    from test_setup_policy import binding_fixture

    sentinel = b"offline-metadata-sentinel"
    binding = binding_fixture()
    monkeypatch.setattr(setup, "_read_metadata_credential", lambda value: sentinel if value == binding else pytest.fail("wrong credential binding"))
    reader = setup.MetadataReader({"/actions/workflows/linux-tests.yml"}, binding=binding)
    with pytest.raises(setup.SetupError, match=expected) as error:
        reader.one("/actions/workflows/linux-tests.yml")
    assert "secret" not in str(error.value)
    assert requests[0][0] == "GET" and requests[-1] == "closed"
    assert requests[0][2]["Authorization"] == b"Bearer " + sentinel
    assert sentinel.decode() not in str(error.value)
    assert all(sentinel.decode() not in str(value) for value in reader.__dict__.values())


def test_http_deadline_includes_slow_injected_read(monkeypatch):
    reader = setup.MetadataReader({"/known"}, lambda _: (b"{}", ""))
    reader.deadline = 100
    ticks = iter((99, 101))
    monkeypatch.setattr(setup.time, "monotonic", lambda: next(ticks))
    with pytest.raises(setup.SetupError, match="deadline"):
        reader.one("/known")


def test_full_source_rejects_added_tracked_helper_or_submodule(repository):
    repo, _head = repository
    extra = repo / ".github/scripts/forge_ci/unreviewed.py"
    extra.write_text("# unexpected import root")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Offline Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "extra",
    )
    with pytest.raises(launch.LaunchError, match="unexpected helper"):
        launch.inspect_checkout(repo, git(repo, "rev-parse", "HEAD"))
    git(repo, "rm", ".github/scripts/forge_ci/unreviewed.py")
    git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        "160000," + git(repo, "rev-parse", "HEAD") + ",submodule",
    )
    git(
        repo,
        "-c",
        "user.name=Offline Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "submodule",
    )
    with pytest.raises(launch.LaunchError, match="unsupported Git tree"):
        launch.inspect_checkout(repo, git(repo, "rev-parse", "HEAD"))


def compact_receipt(valid):
    source = {
        "candidate_sha": "c" * 40,
        "tree_oid": "d" * 40,
        "source_sha256": "b" * 64,
        "workflow_sha256": "e" * 64,
        "helper_sha256": dict.fromkeys(launch.HELPER_PATHS, "f" * 64),
    }
    live = verify(valid)
    return launch.validate_receipt({"schema_version": 1, "status": "PASS", "binding": live["binding"], "source": source, "live": live})


@pytest.fixture
def root_launch(tmp_path, monkeypatch):
    """Fake only the root process boundary; keep both receipt validators real."""
    from forge_ci import admission
    from test_ci_admission import document, observation

    def denied(*_args, **_kwargs):
        pytest.fail("initial checkout attempted ordinary-user transport or a live command")

    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(setup.http.client, "HTTPSConnection", denied)
    monkeypatch.setattr(setup, "live_identity", denied)
    monkeypatch.setattr(setup, "MetadataReader", denied)
    monkeypatch.setattr(setup, "_read_metadata_credential", denied)
    monkeypatch.setattr(setup, "claim_activation", denied)
    doc, observed = document(), observation()
    monkeypatch.setattr(setup, "trusted_boot_id", lambda: doc["binding"]["boot_id"])
    _cfg, context, event = native_fixture()
    evidence = tmp_path / "initial-evidence"
    evidence.mkdir()
    calls = []
    native = {key: doc["binding"][key] for key in setup.NATIVE_BINDING_KEYS}
    fixture = {"context": context, "event": event, "source": doc["source"], "observed": observed,
               "evidence": evidence, "native": native, "calls": calls, "change_result": lambda result: None}

    def command(argv, timeout, **kwargs):
        assert argv == setup.observer_argv_native(native)
        assert argv == setup.observer_argv(doc["binding"])
        assert timeout == 30 and kwargs == {"env": dict(setup.SYSTEM_ENV), "limit": setup.MAX_JSON}
        calls.append((list(argv), copy.deepcopy(kwargs)))
        raw = setup.canonical(fixture["observed"]) + b"\n"
        result = {"argv": list(argv), "returncode": 0, "stdout": raw.decode(), "stdout_hex": raw.hex(),
                  "stderr": "", "stderr_hex": "", "wrapper_pid": 123,
                  "started": setup.stamp(), "ended": setup.stamp()}
        fixture["change_result"](result)
        return result

    monkeypatch.setattr(admission.payload, "bounded_command", command)
    return fixture


def invoke_root_launch(fixture):
    return launch.validate_launch(fixture["context"], fixture["event"], fixture["source"],
                                  evidence_dir=fixture["evidence"])


def test_initial_launch_uses_fresh_fixed_root_observer_and_preserves_receipt_schema(root_launch, monkeypatch):
    sentinel = "offline-token-must-not-reach-observer"
    monkeypatch.setenv("GITHUB_TOKEN", sentinel)
    monkeypatch.setenv("PYTHONPATH", "/unreviewed/imports")
    receipt = invoke_root_launch(root_launch)
    assert receipt == {"schema_version": 1, "status": "PASS", "binding": root_launch["observed"]["binding"],
                       "source": root_launch["source"], "live": root_launch["observed"]["live"]}
    assert set(root_launch["native"]) == setup.NATIVE_BINDING_KEYS
    assert {"job_id", "workflow_id", "job_started_at", "job_started_ns"}.isdisjoint(root_launch["native"])
    assert receipt["binding"]["job_id"] == 456 and receipt["binding"]["workflow_id"] == 987
    assert len(root_launch["calls"]) == 1
    first = copy.deepcopy(receipt)
    # Even an existing valid receipt cannot replace a second fresh root invocation.
    launch.write_receipt(root_launch["evidence"] / "launch.json", receipt)
    root_launch["evidence"] = root_launch["evidence"].parent / "second-evidence"
    root_launch["evidence"].mkdir()
    launch.write_receipt(root_launch["evidence"] / "launch.json", receipt)
    root_launch["observed"]["observed"] = setup.stamp()
    root_launch["observed"]["live"]["checked"] = setup.stamp()
    second = invoke_root_launch(root_launch)
    assert len(root_launch["calls"]) == 2
    assert second["live"]["checked"]["monotonic_ns"] > first["live"]["checked"]["monotonic_ns"]
    for evidence in root_launch["evidence"].parent.iterdir():
        assert not (evidence / "setup-observer-initial.stdout").exists()
        assert (evidence / "setup-observer-initial.stderr").read_bytes() == b""
        diagnostic = launch.parse_json((evidence / "setup-observer-initial.json").read_bytes())
        assert diagnostic["argv"] == root_launch["calls"][0][0]
        for path in evidence.iterdir():
            assert sentinel.encode() not in path.read_bytes()
    assert sentinel not in repr(root_launch["calls"]) and sentinel not in repr(receipt)


def test_initial_numeric_job_identity_comes_only_from_consistent_root_receipt(root_launch):
    observed = root_launch["observed"]
    for binding in (observed["binding"], observed["setup_receipt"]["binding"], observed["live"]["binding"]):
        binding["job_id"] = 789
    observed["setup_receipt_sha256"] = hashlib.sha256(setup.canonical(observed["setup_receipt"]) + b"\n").hexdigest()
    root_launch["context"]["GITHUB_JOB_ID"] = "999"
    root_launch["context"]["GITHUB_WORKFLOW_ID"] = "999"
    assert invoke_root_launch(root_launch)["binding"]["job_id"] == 789
    assert len(root_launch["calls"]) == 1


def test_existing_launch_receipt_cannot_replace_a_failed_fresh_root_observation(root_launch):
    observed = root_launch["observed"]
    previous = {"schema_version": 1, "status": "PASS", "binding": observed["binding"],
                "source": root_launch["source"], "live": observed["live"]}
    path = root_launch["evidence"] / "launch.json"
    launch.write_receipt(path, launch.validate_receipt(previous))
    root_launch["change_result"] = lambda result: result.update(returncode=1)
    with pytest.raises(launch.LaunchError, match="root observer failed"):
        invoke_root_launch(root_launch)
    assert len(root_launch["calls"]) == 1
    assert path.read_bytes() == launch.canonical_bytes(previous) + b"\n"
    assert (root_launch["evidence"] / "setup-observer-initial.stdout").is_file()


@pytest.mark.parametrize("change", ["candidate", "source_schema", "native_attempt", "native_actor", "event_head"])
def test_initial_launch_rejects_unverified_source_or_native_identity_before_root(root_launch, change):
    if change == "candidate":
        root_launch["source"]["candidate_sha"] = "9" * 40
    elif change == "source_schema":
        root_launch["source"]["excluded"] = []
    elif change == "native_attempt":
        root_launch["context"]["GITHUB_RUN_ATTEMPT"] = "2"
    elif change == "native_actor":
        root_launch["context"]["GITHUB_ACTOR"] = "other"
    else:
        root_launch["event"]["head_commit"]["id"] = "9" * 40
    with pytest.raises(launch.LaunchError):
        invoke_root_launch(root_launch)
    assert not root_launch["calls"] and not list(root_launch["evidence"].iterdir())


@pytest.mark.parametrize("change", [
    "run", "run_number", "attempt", "boot", "head", "parent", "missing_job", "bool_job", "job_start",
    "seal_job", "live_job", "tree", "stale_live", "future_live", "stale_policy", "missing_metadata",
    "extra_metadata", "metadata_digest", "module", "observer", "policy", "seal_digest", "incomplete",
])
def test_initial_launch_rejects_root_identity_source_policy_and_freshness_drift(root_launch, change):
    observed = root_launch["observed"]
    field = {"run": "run_id", "run_number": "run_number", "attempt": "run_attempt",
             "boot": "boot_id", "head": "candidate_sha", "parent": "before_sha"}.get(change)
    if field:
        observed["binding"][field] = ({"boot": "22222222-2222-2222-2222-222222222222",
                                      "head": "9" * 40, "parent": "9" * 40}.get(change, 2))
    elif change == "missing_job":
        observed["binding"].pop("job_id")
    elif change == "bool_job":
        observed["binding"]["job_id"] = True
    elif change == "job_start":
        observed["binding"]["job_started_ns"] += 1
    elif change == "seal_job":
        observed["setup_receipt"]["binding"]["job_id"] += 1
    elif change == "live_job":
        observed["live"]["binding"]["job_id"] += 1
    elif change == "tree":
        observed["live"]["tree_oid"] = "9" * 40
    elif change in {"stale_live", "future_live", "stale_policy"}:
        stamp = observed["observed"] if change == "stale_policy" else observed["live"]["checked"]
        stamp["monotonic_ns"] = 1 if change != "future_live" else setup.time.monotonic_ns() + 10**9
    elif change == "missing_metadata":
        observed["live"]["metadata_sha256"].popitem()
    elif change == "extra_metadata":
        observed["live"]["metadata_sha256"]["/unreviewed"] = "a" * 64
    elif change == "metadata_digest":
        path = next(iter(observed["live"]["metadata_sha256"]))
        observed["live"]["metadata_sha256"][path] = "invalid"
    elif change == "module":
        root_launch["source"]["helper_sha256"][".github/scripts/forge_ci/setup_policy.py"] = "9" * 64
    elif change == "observer":
        observed["setup_receipt"]["observer"]["sha256"] = "9" * 64
    elif change == "policy":
        observed["policy"] = {"different": True}
    elif change == "seal_digest":
        observed["setup_receipt_sha256"] = "9" * 64
    else:
        observed.pop("setup_receipt")
    with pytest.raises(launch.LaunchError):
        invoke_root_launch(root_launch)
    assert len(root_launch["calls"]) == 1
    assert (root_launch["evidence"] / "setup-observer-initial.stdout").is_file()
    assert not (root_launch["evidence"] / "launch.json").exists()


@pytest.mark.parametrize("change", ["exit", "bool_exit", "error", "argv", "stderr", "raw_stderr",
                                     "noncanonical", "display", "malformed", "duplicate", "bad_hex", "oversize"])
def test_initial_launch_requires_exact_bounded_root_command_result(root_launch, change):
    def mutate(result):
        if change in {"exit", "bool_exit"}:
            result["returncode"] = 1 if change == "exit" else False
        elif change == "error":
            result["error"] = "command deadline exceeded"
        elif change == "argv":
            result["argv"] = ["/unreviewed/observer.py"]
        elif change == "stderr":
            result.update(stderr="unexpected diagnostics", stderr_hex=b"unexpected diagnostics".hex())
        elif change == "raw_stderr":
            result["stderr_hex"] = b"hidden diagnostics".hex()
        elif change == "display":
            result["stdout"] = "different display"
        elif change == "bad_hex":
            result["stdout_hex"] = "ffgg"
        elif change == "oversize":
            result["stdout_hex"] = "00" * (setup.MAX_JSON + 1)
        else:
            raw = {"noncanonical": b" " + bytes.fromhex(result["stdout_hex"]),
                   "malformed": b"not JSON\n", "duplicate": b'{"binding":{},"binding":{}}\n'}[change]
            result.update(stdout=raw.decode(), stdout_hex=raw.hex())
    root_launch["change_result"] = mutate
    with pytest.raises(launch.LaunchError):
        invoke_root_launch(root_launch)
    assert len(root_launch["calls"]) == 1


def test_local_comparison_is_distinct_and_never_network_or_activation(valid, monkeypatch):
    receipt = compact_receipt(valid)

    def forbidden(*_args, **_kwargs):
        pytest.fail("local receipt check attempted live API or activation")

    monkeypatch.setattr(setup, "live_identity", forbidden)
    monkeypatch.setattr(setup, "MetadataReader", forbidden)
    monkeypatch.setattr(setup, "claim_activation", forbidden)
    local = launch.validate_local_launch(valid["context"], valid["event"], receipt["source"], receipt)
    assert set(local) == {
        "schema_version",
        "status",
        "observation_kind",
        "binding",
        "source",
        "receipt_sha256",
        "local_checked",
    }
    assert local["observation_kind"] == "local_receipt_check" and "live" not in local
    assert local["binding"] == receipt["binding"] and local["source"] == receipt["source"]
    assert local["receipt_sha256"] == hashlib.sha256(launch.canonical_bytes(receipt) + b"\n").hexdigest()
    assert len(valid["calls"]) == 6


@pytest.mark.parametrize(
    "change",
    [
        "run",
        "attempt",
        "ref",
        "boot",
        "source",
        "workflow",
        "helper",
        "tree",
        "receipt_schema",
        "receipt_job",
        "receipt_native",
    ],
)
def test_local_comparison_rejects_receipt_native_source_and_boot_drift(valid, monkeypatch, change):
    receipt = compact_receipt(valid)
    source = copy.deepcopy(receipt["source"])
    if change == "run":
        valid["context"]["GITHUB_RUN_ID"] = "124"
    elif change == "attempt":
        valid["context"]["GITHUB_RUN_ATTEMPT"] = "2"
    elif change == "ref":
        valid["context"]["GITHUB_REF"] = "refs/heads/main"
    elif change == "boot":
        monkeypatch.setattr(setup, "trusted_boot_id", lambda: "22222222-2222-2222-2222-222222222222")
    elif change == "source":
        source["source_sha256"] = "9" * 64
    elif change == "workflow":
        source["workflow_sha256"] = "9" * 64
    elif change == "helper":
        source["helper_sha256"][next(iter(launch.HELPER_PATHS))] = "9" * 64
    elif change == "tree":
        source["tree_oid"] = "9" * 40
    elif change == "receipt_schema":
        receipt["secret"] = "not allowed"
    elif change == "receipt_job":
        receipt["binding"]["job_id"] = True
    else:
        receipt["binding"]["run_id"] += 1
        receipt["live"]["binding"]["run_id"] = receipt["binding"]["run_id"]
    with pytest.raises((launch.LaunchError, setup.SetupError)):
        launch.validate_local_launch(valid["context"], valid["event"], source, receipt)


def test_receipt_small_and_observation_existing_large_bounds_separate(tmp_path):
    # The qualified route's genuine policy observation was 294347 bytes.
    report = {"observed_policy": "x" * 294_347}
    with pytest.raises(launch.LaunchError, match="byte bound"):
        launch.write_receipt(tmp_path / "compact.json", report)
    launch.write_receipt(tmp_path / "policy.json", report, limit=setup.MAX_JSON)
    assert (tmp_path / "policy.json").stat().st_size > 294_347
    with pytest.raises(launch.LaunchError, match="unreviewed"):
        launch.write_receipt(tmp_path / "too-big.json", report, limit=setup.MAX_JSON + 1)


@pytest.mark.parametrize("parents", [
    [{"sha": "a" * 40}],
    [{"sha": "a" * 40}, {"sha": "b" * 40}],
])
def test_direct_first_parent_single_commit_and_two_parent_merge(valid, parents):
    valid["documents"]["/git/commits/" + "c" * 40]["parents"] = parents
    # A direct merge can introduce several commits from its secondary history.
    valid["event"]["commits"] = [{"id": "b" * 40}, {"id": "e" * 40}, {"id": "c" * 40}]
    valid["native"] = setup.validate_initial_identity(valid["config"], valid["context"], valid["event"])
    live = verify(valid)
    assert live["binding"]["before_sha"] == parents[0]["sha"]
    assert set(live["metadata_sha256"]) == set(valid["documents"])
    assert valid["calls"] == [setup.API_ROOT + path for path in valid["documents"]]
    assert len(valid["calls"]) == setup.MAX_REQUESTS == 6


@pytest.mark.parametrize("parents", [
    None, True, False, 1, 1.5, "unknown", {}, [],
    [None], [True], [False], [1], [1.5], ["unknown"], [[]], [{}],
    [{"sha": None}], [{"sha": True}], [{"sha": 1}], [{"sha": []}], [{"sha": {}}],
    [{"sha": "0" * 40}], [{"sha": "A" * 40}], [{"sha": "g" * 40}],
    [{"sha": "a" * 39}], [{"sha": "a" * 41}],
    [{"sha": "a" * 40 + "\n"}],
    [{"sha": "b" * 40}],  # A transitive predecessor is not the direct parent.
    [{"sha": "b" * 40}, {"sha": "a" * 40}],  # Reordered merge parents.
    [{"sha": "a" * 40}, {"sha": "a" * 40}],
    [{"sha": "a" * 40}, {"sha": "0" * 40}],
    [{"sha": "a" * 40}, {"sha": True}],
    [{"sha": "a" * 40}, {}],
    [{"sha": "a" * 40}, {"sha": "b" * 40}, {"sha": "d" * 40}],
])
def test_direct_parent_rejects_transitive_reordered_duplicate_and_malformed_vectors(valid, parents):
    valid["documents"]["/git/commits/" + "c" * 40]["parents"] = parents
    with pytest.raises(setup.SetupError, match="parent"):
        verify(valid)
    assert len(valid["calls"]) == 5


def test_missing_parent_vector_rejects(valid):
    valid["documents"]["/git/commits/" + "c" * 40].pop("parents")
    with pytest.raises(setup.SetupError, match="parents"):
        verify(valid)


def test_transitive_only_before_does_not_trigger_history_fallback(valid):
    docs = valid["documents"]
    docs["/git/commits/" + "c" * 40]["parents"] = [{"sha": "b" * 40}]
    docs["/git/commits/" + "b" * 40] = {"sha": "b" * 40, "parents": [{"sha": "a" * 40}]}
    with pytest.raises(setup.SetupError, match="direct first parent"):
        verify(valid)
    assert setup.API_ROOT + "/git/commits/" + "b" * 40 not in valid["calls"]
    assert len(valid["calls"]) == 5


def test_native_before_change_cannot_reuse_old_direct_parent(valid):
    valid["event"]["before"] = "b" * 40
    valid["native"] = setup.validate_initial_identity(valid["config"], valid["context"], valid["event"])
    with pytest.raises(setup.SetupError, match="direct first parent"):
        verify(valid)


def test_preserved_commit_parent_tree_fields_admit_without_compare(valid):
    # Exact relevant fields from the preserved run 37818889925 git-commit response.
    commit = {
        "sha": "c0ba5293daf401a6c100b868c3248cb5b0261ceb",
        "tree": {"sha": "a9eda3178719a77a736d023ab5fe6e3fb5578e03"},
        "parents": [
            {"sha": "749a4cba4f62d67a166c9e73e4d62ded0e15d632"},
            {"sha": "ae554df2314fdff37546fe660c6e6caf70dfd490"},
        ],
    }
    rebind_commit(valid, commit)
    live = verify(valid)
    assert live["tree_oid"] == commit["tree"]["sha"]
    assert live["binding"]["before_sha"] == commit["parents"][0]["sha"]
    assert live["binding"]["candidate_sha"] == commit["sha"]
    assert len(valid["calls"]) == 6 and "files" not in commit


def rebind_commit(valid, commit):
    candidate = commit["sha"]
    before = commit["parents"][0]["sha"]
    valid["context"].update(GITHUB_SHA=candidate, GITHUB_WORKFLOW_SHA=candidate)
    valid["event"].update(before=before, after=candidate, head_commit={"id": candidate})
    valid["native"] = setup.validate_initial_identity(valid["config"], valid["context"], valid["event"])
    documents = valid["documents"]
    documents["/git/ref/heads/fix/review-correctness-linux-ci"]["object"]["sha"] = candidate
    documents.pop("/git/commits/" + "c" * 40)
    documents["/git/commits/" + candidate] = commit
    runs_path = next(path for path in documents if "/runs?" in path)
    runs = documents.pop(runs_path)
    documents[runs_path.replace("c" * 40, candidate)] = runs
    for run in (valid["run"], runs["workflow_runs"][0]):
        run.update(head_sha=candidate, head_commit={"id": candidate})
    valid["job"]["head_sha"] = candidate


@pytest.mark.parametrize("parser", [setup.parse_json, launch.parse_json])
def test_unused_compare_patch_still_exceeds_unchanged_string_guard(parser):
    raw = setup.canonical({"files": [{"patch": "x" * 65537}]})
    assert len(raw) < setup.MAX_API == launch.MAX_API == 1024 * 1024
    with pytest.raises((setup.SetupError, launch.LaunchError), match="JSON string bound exceeded"):
        parser(raw, limit=setup.MAX_API)


def test_only_exact_six_endpoints_are_admitted_and_compare_is_uncallable(valid, monkeypatch):
    readers = []
    original = setup.MetadataReader

    class CapturedReader(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            readers.append(self)

    monkeypatch.setattr(setup, "MetadataReader", CapturedReader)
    live = verify(valid)
    reader = readers[0]
    assert reader.allowed == set(valid["documents"]) == set(live["metadata_sha256"])
    assert reader.requests == len(valid["calls"]) == 6
    forbidden = [
        "/compare/" + "a" * 40 + "..." + "c" * 40,
        "/compare/" + "a" * 40 + "..." + "c" * 40 + "?per_page=100&page=1",
        "/compare/" + "a" * 40 + "..." + "c" * 40 + "?per_page=100&page=2",
    ]
    forbidden += [path[:-1] + "2" for path in reader.allowed if path.endswith("&page=1")]
    for path in forbidden:
        with pytest.raises(setup.SetupError, match="unreviewed"):
            reader.get(path)
    assert reader.requests == len(valid["calls"]) == 6


def test_six_request_ceiling_prevents_seventh_fetch_even_if_allowlisted():
    paths = ["/test/" + str(index) for index in range(7)]
    calls = []

    def fetch(url):
        calls.append(url)
        return b"{}", ""

    reader = setup.MetadataReader(paths, fetch)
    for path in paths[:6]:
        assert reader.one(path) == {}
    with pytest.raises(setup.SetupError, match="request"):
        reader.one(paths[6])
    assert reader.requests == len(calls) == setup.MAX_REQUESTS == 6


@pytest.mark.parametrize("change", [
    "missing", "extra", "old_seven", "old_eight", "foreign", "compare", "second_page",
    "other_run", "other_candidate", "other_workflow", "other_branch", "other_attempt",
    "bad_digest", "zero_digest", "not_object",
])
@pytest.mark.parametrize("validator", ["live", "launch", "local"])
def test_live_and_launch_evidence_require_exact_bound_six_endpoint_keys(valid, change, validator):
    receipt = compact_receipt(valid)
    evidence = receipt["live"]["metadata_sha256"]
    path = next(iter(evidence))
    if change == "missing":
        evidence.pop(path)
    elif change in {"extra", "old_seven", "old_eight"}:
        evidence["/unreviewed"] = "a" * 64
        if change == "old_eight":
            evidence["/unreviewed2"] = "a" * 64
    elif change == "not_object":
        receipt["live"]["metadata_sha256"] = list(evidence)
    elif change in {"bad_digest", "zero_digest"}:
        evidence[path] = "invalid" if change == "bad_digest" else "0" * 64
    else:
        if change == "second_page":
            path = next(key for key in evidence if key.endswith("&page=1"))
            replacement = path[:-1] + "2"
        elif change == "other_run":
            path = "/actions/runs/123"
            replacement = "/actions/runs/124"
        elif change == "other_candidate":
            path = "/git/commits/" + "c" * 40
            replacement = "/git/commits/" + "b" * 40
        elif change == "other_workflow":
            path = next(key for key in evidence if "/runs?" in key)
            replacement = path.replace("/987/", "/988/")
        elif change == "other_branch":
            path = "/git/ref/heads/fix/review-correctness-linux-ci"
            replacement = "/git/ref/heads/main"
        elif change == "other_attempt":
            path = next(key for key in evidence if "/jobs?" in key)
            replacement = path.replace("/attempts/1/", "/attempts/2/")
        else:
            replacement = "/foreign" if change == "foreign" else "/compare/" + "a" * 40 + "..." + "c" * 40
        evidence[replacement] = evidence.pop(path)
        assert len(evidence) == 6
    with pytest.raises((setup.SetupError, launch.LaunchError), match="endpoint|digest"):
        if validator == "live":
            setup.validate_live_evidence(receipt["live"], receipt["binding"], receipt["source"]["tree_oid"])
        elif validator == "launch":
            launch.validate_receipt(receipt)
        else:
            launch.validate_local_launch(valid["context"], valid["event"], receipt["source"], receipt)


def test_exact_live_call_ownership_stays_at_six_runtime_boundaries():
    import ast

    root = Path(launch.__file__).parent

    def callers(path, name):
        tree = ast.parse(path.read_text())
        found = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                count = sum(
                    isinstance(child, ast.Call)
                    and (
                        isinstance(child.func, ast.Name)
                        and child.func.id == name
                        or isinstance(child.func, ast.Attribute)
                        and child.func.attr == name
                    )
                    for child in ast.walk(node)
                )
                if count:
                    found[node.name] = count
        return found

    assert callers(root / "setup_policy.py", "live_identity") == {
        "bootstrap": 2,
        "install_vendor": 1,
        "observe_sealed": 1,
    }
    assert callers(root / "launch.py", "live_identity") == {}
    assert callers(root / "launch.py", "observe_setup") == {"validate_launch": 1}
    assert callers(root / "admission.py", "live_identity") == {}
    assert callers(root / "admission.py", "observe_setup") == {"Gate": 1}
    assert callers(root / "admission.py", "validate_launch") == {}
    assert callers(root / "controller.py", "validate_launch") == {}
    assert callers(root / "user_service.py", "validate_launch") == {}
    # One root observation at checkout, plus one during prepare and one at final.
    admission_tree = ast.parse((root / "admission.py").read_text())
    gate = next(
        node for node in admission_tree.body if isinstance(node, ast.ClassDef) and node.name == "Gate"
    )
    observers = [
        node.name
        for node in gate.body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "_observer"
            for child in ast.walk(node)
        )
    ]
    assert observers == ["prepare", "final_source_recheck"]
    assert 6 * setup.MAX_REQUESTS == 36


@pytest.mark.parametrize("kind", ["directory", "regular_file", "symlink", "other"])
def test_cache_rejection_reports_first_relative_path_without_following(tmp_path, kind):
    import json
    import time

    parent = tmp_path / "src"
    parent.mkdir()
    cache = parent / "__pycache__"
    if kind == "directory":
        cache.mkdir()
    elif kind == "regular_file":
        cache.write_bytes(b"not read")
    elif kind == "symlink":
        cache.symlink_to("private-dangling-target")
    else:
        os.mkfifo(cache)
    with pytest.raises(launch.LaunchError) as caught:
        launch._unexpected_artifacts(tmp_path, set(), time.monotonic() + 5)
    message = str(caught.value)
    assert message.startswith("unexpected import cache ")
    assert json.loads(message.removeprefix("unexpected import cache ")) == {
        "kind": "unexpected_import_cache",
        "path": "src/__pycache__", "path_truncated": False, "type": kind,
    }
    assert str(tmp_path) not in message and "private-dangling-target" not in message
    assert cache.lstat()  # Rejection never removes the offending object.


def test_cache_rejection_long_unicode_path_is_escaped_and_bounded(tmp_path):
    import json
    import time

    # Non-BMP code points maximize ensure_ascii expansion to12 bytes each.
    part = "\U0001f600" * 48 + "\n"
    parent = tmp_path / part / part
    parent.mkdir(parents=True)
    (parent / "__pycache__").mkdir()
    relative = (parent / "__pycache__").relative_to(tmp_path).as_posix()
    with pytest.raises(launch.LaunchError) as caught:
        launch._unexpected_artifacts(tmp_path, set(), time.monotonic() + 5)
    message = str(caught.value)
    record = message.removeprefix("unexpected import cache ")
    assert len(record.encode("ascii")) < 896 and len(message) < 1024
    assert "\n" not in message and str(tmp_path) not in message
    assert json.loads(record) == {
        "kind": "unexpected_import_cache",
        "path": relative[:64], "path_truncated": True, "type": "directory",
    }
    assert (parent / "__pycache__").is_dir()
