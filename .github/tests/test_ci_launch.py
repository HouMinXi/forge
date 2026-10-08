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
        "/git/commits/" + "c" * 40: {"sha": "c" * 40, "tree": {"sha": "d" * 40}},
        "/compare/" + "a" * 40 + "..." + "c" * 40 + "?per_page=100&page=1": {
            "total_commits": 1,
            "commits": [{"sha": "c" * 40}],
            "base_commit": {"sha": "a" * 40},
            "merge_base_commit": {"sha": "a" * 40},
            "status": "ahead",
            "ahead_by": 1,
            "behind_by": 0,
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
    assert len(valid["calls"]) == 7
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
        "wrong_base",
        "wrong_merge_base",
        "wrong_head",
        "diverged",
        "commit_changed",
        "tree_invalid",
    ],
)
def test_replay_head_ancestry_and_completeness(valid, change):
    docs = valid["documents"]
    jobs = next(v for k, v in docs.items() if "/jobs?" in k)
    runs = next(v for k, v in docs.items() if "/runs?" in k)
    compare = next(v for k, v in docs.items() if k.startswith("/compare/"))
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
    elif change == "wrong_base":
        compare["base_commit"]["sha"] = "b" * 40
    elif change == "wrong_merge_base":
        compare["merge_base_commit"]["sha"] = "b" * 40
    elif change == "wrong_head":
        compare["commits"][0]["sha"] = "b" * 40
    elif change == "diverged":
        compare["status"] = "diverged"
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


@pytest.mark.parametrize(
    "change",
    [
        None,
        "missing_link",
        "external_link",
        "third_page",
        "changed_total",
        "truncated",
        "extra_continuation",
    ],
)
def test_collection_consumes_all_bounded_pages(change):
    path = "/actions/test?per_page=100"
    pages = {
        path + "&page=1": (
            {"total_count": 101, "items": list(range(100))},
            "<" + setup.API_ROOT + path + '&page=2>; rel="next"',
        ),
        path + "&page=2": ({"total_count": 101, "items": [100]}, ""),
    }
    if change == "missing_link":
        pages[path + "&page=1"] = (pages[path + "&page=1"][0], "")
    elif change in {"external_link", "third_page"}:
        url = "https://example.com" if change == "external_link" else setup.API_ROOT + path + "&page=3"
        pages[path + "&page=1"] = (pages[path + "&page=1"][0], "<" + url + '>; rel="next"')
    elif change == "changed_total":
        pages[path + "&page=2"][0]["total_count"] = 100
    elif change == "truncated":
        pages[path + "&page=2"][0]["items"] = []
    elif change == "extra_continuation":
        pages[path + "&page=2"] = (
            pages[path + "&page=2"][0],
            "<" + setup.API_ROOT + path + '&page=2>; rel="next"',
        )

    def fetch(url):
        body, link = pages[url.removeprefix(setup.API_ROOT)]
        return setup.canonical(body), link

    reader = setup.MetadataReader(pages, fetch)
    if change:
        with pytest.raises(setup.SetupError):
            reader.collection(path, "items")
    else:
        items, _ = reader.collection(path, "items")
        assert items == list(range(101)) and reader.requests == 2


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
    receipt = launch.validate_launch(valid["context"], valid["event"], source, fetcher=valid["fetch"])
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
    reader = setup.MetadataReader({"/actions/workflows/linux-tests.yml"})
    with pytest.raises(setup.SetupError, match=expected) as error:
        reader.one("/actions/workflows/linux-tests.yml")
    assert "secret" not in str(error.value)
    assert requests[0][0] == "GET" and requests[-1] == "closed"
    assert "Authorization" not in requests[0][2]


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
    return launch.validate_launch(valid["context"], valid["event"], source, fetcher=valid["fetch"])


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
    assert len(valid["calls"]) == 7


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


def test_admitted_ancestry_second_page_fits_eight_request_cap(valid):
    docs = valid["documents"]
    first = next(key for key in docs if key.startswith("/compare/"))
    compare = docs[first]
    compare.update(total_commits=101, ahead_by=101)
    compare["commits"] = [{"sha": f"{index:040x}"} for index in range(1, 101)]
    docs[first[:-1] + "2"] = dict(copy.deepcopy(compare), commits=[{"sha": "c" * 40}])

    def fetch(url):
        path = url.removeprefix(setup.API_ROOT)
        valid["calls"].append(url)
        link = "<" + setup.API_ROOT + first[:-1] + '2>; rel="next"' if path == first else ""
        return setup.canonical(docs[path]), link

    valid["fetch"] = fetch
    assert verify(valid)["binding"]["job_id"] == 456
    assert len(valid["calls"]) == setup.MAX_REQUESTS == 8


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
    assert callers(root / "launch.py", "live_identity") == {"validate_launch": 1}
    assert callers(root / "admission.py", "validate_launch") == {}
    assert callers(root / "controller.py", "validate_launch") == {}
    assert callers(root / "user_service.py", "validate_launch") == {}
    # The observer is invoked exactly once during prepare and once during final.
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
