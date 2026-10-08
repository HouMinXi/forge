"""Synthetic launch events/HTTP and disposable Git trees; no API or policy loads."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from forge_ci import launch  # noqa: E402


@pytest.fixture
def valid():
    nonce = "0123456789abcdef" * 2
    repository = {"id": 123, "name": "project", "full_name": "owner/project",
                  "owner": {"id": 456, "login": "owner", "type": "User"}}
    actor = {"id": 456, "login": "owner", "type": "User"}
    spec = {"nonce": nonce, "repository": repository, "publisher": actor,
            "authorized_retrier": copy.deepcopy(actor), "seed_sha": "a" * 40,
            "ref": "refs/heads/ci/qualify-apparmor-" + nonce,
            "workflow_path": ".github/workflows/qualify-apparmor-" + nonce + ".yml",
            "workflow_sha256": "b" * 64, "source_sha256": "c" * 64,
            "helper_sha256": dict.fromkeys(launch.HELPER_PATHS, "d" * 64)}
    document = {"schema_version": 1, "launch": spec, "admission": {"status": "separate-gate"}}
    context = {
        "GITHUB_EVENT_NAME": "push", "GITHUB_REF_TYPE": "branch", "GITHUB_REF": spec["ref"],
        "GITHUB_REPOSITORY": "owner/project", "GITHUB_REPOSITORY_OWNER": "owner",
        "GITHUB_REPOSITORY_ID": "123", "GITHUB_REPOSITORY_OWNER_ID": "456",
        "GITHUB_ACTOR": "owner", "GITHUB_ACTOR_ID": "456", "GITHUB_TRIGGERING_ACTOR": "owner",
        "GITHUB_WORKFLOW_REF": "owner/project/" + spec["workflow_path"] + "@" + spec["ref"],
        "GITHUB_WORKFLOW_SHA": "e" * 40, "GITHUB_SHA": "e" * 40, "GITHUB_RUN_NUMBER": "1",
        "GITHUB_RUN_ID": "789", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "qualify",
        "GITHUB_SERVER_URL": "https://github.com", "GITHUB_API_URL": "https://api.github.com",
    }
    event = {"created": False, "deleted": False, "forced": False, "ref": spec["ref"],
             "before": spec["seed_sha"], "after": context["GITHUB_SHA"],
             "repository": copy.deepcopy(repository), "sender": copy.deepcopy(actor)}
    checkout = {"head": context["GITHUB_SHA"], "parents": [spec["seed_sha"]],
                "source_sha256": spec["source_sha256"], "workflow_sha256": spec["workflow_sha256"],
                "helper_sha256": copy.deepcopy(spec["helper_sha256"])}
    public = {**copy.deepcopy(repository), "private": False}
    attempt = {"id": 789, "run_attempt": 1, "run_number": 1, "workflow_id": 987,
               "workflow_url": "https://api.github.com/repos/owner/project/actions/workflows/987",
               "url": "https://api.github.com/repos/owner/project/actions/runs/789",
               "path": spec["workflow_path"], "event": "push", "head_branch": spec["ref"][11:],
               "head_sha": context["GITHUB_SHA"], "head_commit": {"id": context["GITHUB_SHA"]},
               "status": "in_progress", "conclusion": None, "pull_requests": [],
               "repository": public, "head_repository": copy.deepcopy(public),
               "actor": copy.deepcopy(actor), "triggering_actor": copy.deepcopy(actor)}
    return SimpleNamespace(document=document, context=context, event=event, checkout=checkout,
                           attempt=attempt, urls=[])


def verify(valid):
    def fetch(url):
        valid.urls.append(url)
        return valid.attempt
    return launch.validate_launch(valid.document, valid.context, valid.event, valid.checkout, fetcher=fetch)


def test_first_transition_is_launch_identity_only(valid):
    report = verify(valid)
    assert report["status"] == "PASS"
    assert report["policy_authorized"] is False
    assert report["run_id"] == 789 and report["run_attempt"] == 1
    assert valid.urls == ["https://api.github.com/repos/owner/project/actions/runs/789/attempts/1"]
    assert report["manifest_sha256"] == hashlib.sha256(launch.canonical_bytes(valid.document)).hexdigest()


@pytest.mark.parametrize("attempt", [2, 3, 100])
def test_same_run_authorized_retry(valid, attempt):
    valid.context["GITHUB_RUN_ATTEMPT"] = str(attempt)
    valid.attempt["run_attempt"] = attempt
    assert verify(valid)["run_attempt"] == attempt
    assert valid.urls[-1].endswith(f"/runs/789/attempts/{attempt}")


@pytest.mark.parametrize("suffix", ["", "@{ref}", "@{branch}"])
def test_exact_rest_path_representations(valid, suffix):
    spec = valid.document["launch"]
    valid.attempt["path"] += suffix.format(ref=spec["ref"], branch=spec["ref"][11:])
    assert verify(valid)["status"] == "PASS"


@pytest.mark.parametrize("key,value", [
    ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_EVENT_NAME", "workflow_dispatch"),
    ("GITHUB_REF_TYPE", "tag"), ("GITHUB_REF", "refs/heads/main"),
    ("GITHUB_REPOSITORY", "elsewhere/project"), ("GITHUB_REPOSITORY_OWNER", "other"),
    ("GITHUB_REPOSITORY_ID", "124"), ("GITHUB_REPOSITORY_OWNER_ID", "457"),
    ("GITHUB_ACTOR_ID", "999"), ("GITHUB_ACTOR", "other"), ("GITHUB_TRIGGERING_ACTOR", "other"),
    ("GITHUB_RUN_NUMBER", "2"), ("GITHUB_RUN_NUMBER", 1), ("GITHUB_RUN_NUMBER", "01"),
    ("GITHUB_RUN_ID", "0"), ("GITHUB_RUN_ID", True), ("GITHUB_RUN_ID", 789),
    ("GITHUB_RUN_ATTEMPT", "-1"), ("GITHUB_RUN_ATTEMPT", "01"), ("GITHUB_RUN_ATTEMPT", "1.0"),
    ("GITHUB_API_URL", "https://evil.example"), ("GITHUB_SERVER_URL", "http://github.com"),
    ("GITHUB_WORKFLOW_REF", "owner/project/.github/workflows/other.yml@refs/heads/main"),
    ("GITHUB_WORKFLOW_SHA", "f" * 40), ("GITHUB_SHA", "no-sha"), ("GITHUB_JOB", "a;false"),
])
def test_wrong_or_malformed_context_stops_before_http(valid, key, value):
    valid.context[key] = value
    with pytest.raises(launch.LaunchError):
        verify(valid)
    assert valid.urls == []


@pytest.mark.parametrize("key", ["created", "deleted", "forced"])
@pytest.mark.parametrize("value", [True, 0, "false", "False", None, [], {}])
def test_nonboolean_or_nontransition_push_stops(valid, key, value):
    valid.event[key] = value
    with pytest.raises(launch.LaunchError):
        verify(valid)
    assert not valid.urls


@pytest.mark.parametrize("key,value", [
    ("before", "0" * 40), ("before", "f" * 40), ("after", "f" * 40),
    ("ref", "refs/tags/ci/qualify-apparmor-" + "0" * 32),
    ("sender", {"id": 999, "login": "owner", "type": "User"}),
])
def test_recreated_rewound_or_wrong_push_stops(valid, key, value):
    valid.event[key] = value
    with pytest.raises(launch.LaunchError):
        verify(valid)


@pytest.mark.parametrize("surface", ["event", "attempt"])
@pytest.mark.parametrize("key,value", [("id", "123"), ("id", True), ("id", 999),
                                        ("name", "elsewhere"), ("full_name", "other/project")])
def test_repository_identity_is_exact(valid, surface, key, value):
    getattr(valid, surface)["repository"][key] = value
    with pytest.raises(launch.LaunchError):
        verify(valid)


@pytest.mark.parametrize("key,value", [("head", "f" * 40), ("parents", []),
    ("parents", ["a" * 40, "f" * 40]), ("parents", ["f" * 40]),
    ("source_sha256", "f" * 64), ("workflow_sha256", "f" * 64), ("helper_sha256", {})])
def test_checkout_drift_stops_before_http(valid, key, value):
    valid.checkout[key] = value
    with pytest.raises(launch.LaunchError):
        verify(valid)
    assert not valid.urls


@pytest.mark.parametrize("key,value", [
    ("id", "789"), ("id", True), ("id", 790), ("run_number", 2), ("run_number", "1"),
    ("run_attempt", 2), ("run_attempt", "1"), ("workflow_id", 0), ("workflow_id", "987"),
    ("workflow_url", "https://api.github.com/repos/other/project/actions/workflows/987"),
    ("url", "https://api.github.com/repos/owner/project/actions/runs/790"),
    ("path", ".github/workflows/other.yml"), ("event", "pull_request"), ("head_branch", "main"),
    ("head_sha", "f" * 40), ("head_commit", {"id": "f" * 40}),
    ("status", "completed"), ("status", "queued"), ("conclusion", "failure"), ("pull_requests", [{}]),
])
def test_wrong_live_metadata_stops(valid, key, value):
    valid.attempt[key] = value
    with pytest.raises(launch.LaunchError):
        verify(valid)


def test_stale_attempt_endpoint_response_rejected(valid):
    valid.context["GITHUB_RUN_ATTEMPT"] = "2"
    with pytest.raises(launch.LaunchError, match="run_attempt"):
        verify(valid)


@pytest.mark.parametrize("key", ["actor", "triggering_actor"])
@pytest.mark.parametrize("field,value", [("id", 999), ("id", "456"), ("id", True),
                                         ("login", "other"), ("type", "Bot")])
def test_actor_numeric_id_not_name_controls_authorization(valid, key, field, value):
    valid.attempt[key][field] = value
    with pytest.raises(launch.LaunchError):
        verify(valid)


def test_api_failure_never_falls_back_to_original_actor(valid):
    def unavailable(url):
        raise OSError("unavailable")
    with pytest.raises(launch.LaunchError, match="unavailable"):
        launch.validate_launch(valid.document, valid.context, valid.event, valid.checkout, fetcher=unavailable)


@pytest.mark.parametrize("scope,key,value", [
    ("root", "schema_version", True), ("root", "command", "sudo anything"),
    ("root", "admission", {}), ("launch", "seed_sha", "main"),
    ("launch", "nonce", "not-fresh"), ("launch", "nonce", "0" * 32),
    ("launch", "workflow_path", "../../evil.py"), ("launch", "ref", "refs/heads/main"),
    ("launch", "helper_sha256", {}), ("launch", "dynamic_commands", []),
    ("launch", "source_sha256", None),
])
def test_strict_manifest_schema(valid, scope, key, value):
    target = valid.document if scope == "root" else valid.document["launch"]
    target[key] = value
    with pytest.raises(launch.LaunchError):
        verify(valid)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"x":{"a":1,"a":2}}',
                                  b'{"x":NaN}', b'{"x":Infinity}', b'[]', b'{}garbage', b'\xff'])
def test_invalid_json_rejected(raw):
    with pytest.raises(launch.LaunchError):
        launch.parse_json(raw)


def test_json_bounds_and_depth():
    for raw in (b" " * (launch.MAX_JSON + 1), b'{"a":' + b'[' * 30 + b'0' + b']' * 30 + b'}'):
        with pytest.raises(launch.LaunchError):
            launch.parse_json(raw)


def git(repo, *args):
    return subprocess.check_output(["/usr/bin/git", "-C", str(repo), *args],
                                   env={**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
                                        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}).decode().strip()


@pytest.fixture
def checkout_repo(tmp_path, valid):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "source.txt").write_bytes(b"reviewed source\n")
    spec = valid.document["launch"]
    records = [{
        "path": "source.txt", "mode": "100644", "sha256": hashlib.sha256(b"reviewed source\n").hexdigest(),
    }]
    for path in launch.HELPER_PATHS:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"# fixed reviewed helper\n")
        spec["helper_sha256"][path] = hashlib.sha256(target.read_bytes()).hexdigest()
        records.append({"path": path, "mode": "100644", "sha256": spec["helper_sha256"][path]})
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "reviewed source and helpers")
    spec["seed_sha"] = git(repo, "rev-parse", "HEAD")
    spec["source_sha256"] = launch.source_digest(records)
    workflow = repo / spec["workflow_path"]
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_bytes(b"# exact dedicated workflow\n")
    spec["workflow_sha256"] = hashlib.sha256(workflow.read_bytes()).hexdigest()
    manifest = repo / launch.MANIFEST_PATH
    manifest.write_bytes(launch.canonical_bytes(valid.document))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "one control transition")
    return repo, manifest, valid.document


def test_real_git_checkout_binding(checkout_repo):
    repo, manifest, document = checkout_repo
    result = launch.inspect_checkout(repo, document, manifest_path=manifest)
    assert result["head"] == git(repo, "rev-parse", "HEAD")
    for key in ("source_sha256", "workflow_sha256", "helper_sha256"):
        assert result[key] == document["launch"][key]


@pytest.mark.parametrize("target", ["source.txt", ".github/scripts/forge_ci/launch.py", launch.MANIFEST_PATH])
def test_uncommitted_content_drift(checkout_repo, target):
    repo, manifest, document = checkout_repo
    with (repo / target).open("ab") as stream:
        stream.write(b"drift")
    with pytest.raises(launch.LaunchError, match="bytes"):
        launch.inspect_checkout(repo, document, manifest_path=manifest)


def test_noncontrol_source_change_in_final_commit_rejected(checkout_repo):
    repo, manifest, document = checkout_repo
    (repo / "source.txt").write_text("changed source")
    git(repo, "add", ".")
    git(repo, "commit", "--amend", "-qm", "unauthorized source change")
    with pytest.raises(launch.LaunchError, match="non-control source"):
        launch.inspect_checkout(repo, document, manifest_path=manifest)


def test_second_commit_not_admitted(checkout_repo):
    repo, manifest, document = checkout_repo
    git(repo, "commit", "--allow-empty", "-qm", "replay")
    with pytest.raises(launch.LaunchError, match="single seed child"):
        launch.inspect_checkout(repo, document, manifest_path=manifest)


def test_wrong_manifest_location(checkout_repo, tmp_path):
    repo, manifest, document = checkout_repo
    other = tmp_path / "manifest.json"
    other.write_bytes(manifest.read_bytes())
    with pytest.raises(launch.LaunchError, match="fixed reviewed path"):
        launch.inspect_checkout(repo, document, manifest_path=other)


def test_helper_symlink_rejected(checkout_repo):
    repo, manifest, document = checkout_repo
    path = repo / ".github/scripts/forge_ci/launch.py"
    path.unlink()
    path.symlink_to(repo / "source.txt")
    with pytest.raises(launch.LaunchError):
        launch.inspect_checkout(repo, document, manifest_path=manifest)


def test_receipt_is_exclusive_and_no_symlink(tmp_path):
    path = tmp_path / "receipt.json"
    launch.write_receipt(path, {"status": "PASS"})
    with pytest.raises(FileExistsError):
        launch.write_receipt(path, {"status": "PASS"})
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        launch.write_receipt(link, {"status": "PASS"})


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status = body, status
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, limit):
        return self.body[:limit]


def install_http(monkeypatch, response):
    calls = []

    class Connection:
        def __init__(self, host, **kwargs):
            calls.append((host, kwargs))

        def request(self, method, path, headers):
            calls.append((method, path, headers))

        def getresponse(self):
            return response

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(launch.http.client, "HTTPSConnection", Connection)
    return calls


def test_public_http_has_no_secret_proxy_or_redirect(monkeypatch):
    calls = install_http(monkeypatch, FakeResponse(b'{"id":1}'))
    monkeypatch.setenv("GITHUB_TOKEN", "not-to-transmit")
    monkeypatch.setenv("HTTPS_PROXY", "http://evil.example")
    result = launch.fetch_public_attempt("https://api.github.com/repos/owner/project/actions/runs/1/attempts/1")
    assert result == {"id": 1}
    assert calls[0][0] == "api.github.com"
    assert calls[1][0:2] == ("GET", "/repos/owner/project/actions/runs/1/attempts/1")
    assert "Authorization" not in calls[1][2]
    assert "not-to-transmit" not in repr(calls)
    assert calls[-1] == "closed"


@pytest.mark.parametrize("response", [
    FakeResponse(b"{}", status=302, headers={"Location": "https://evil.example"}),
    FakeResponse(b"{}", status=403), FakeResponse(b"{}", status=404),
    FakeResponse(b"{}", headers={"Content-Type": "text/html"}),
    FakeResponse(b"{}", headers={"Content-Encoding": "gzip"}),
    FakeResponse(b"{}", headers={"Content-Length": str(launch.MAX_API + 1)}),
    FakeResponse(b"{}", headers={"Content-Length": "3"}),
    FakeResponse(b"x" * (launch.MAX_API + 1)), FakeResponse(b'{"id":1,"id":2}'),
])
def test_bad_http_fails_closed(monkeypatch, response):
    calls = install_http(monkeypatch, response)
    with pytest.raises(launch.LaunchError):
        launch.fetch_public_attempt("https://api.github.com/repos/owner/project/actions/runs/1/attempts/1")
    assert calls[-1] == "closed"


@pytest.mark.parametrize("url", [
    "http://api.github.com/repos/owner/project/actions/runs/1/attempts/1",
    "https://evil.example/repos/owner/project/actions/runs/1/attempts/1",
    "https://api.github.com/repos/owner/project/actions/runs/1/attempts/1?token=x",
    "https://api.github.com/repos/owner/project/actions/runs/1",
    "https://api.github.com/repos/owner/project/actions/runs/01/attempts/1",
])
def test_endpoint_cannot_be_redirected_or_generalized(monkeypatch, url):
    calls = install_http(monkeypatch, FakeResponse(b"{}"))
    with pytest.raises(launch.LaunchError, match="endpoint"):
        launch.fetch_public_attempt(url)
    assert not calls


def test_cli_success_uses_injected_fetcher_without_network(checkout_repo, monkeypatch, tmp_path):
    repo, manifest, document = checkout_repo
    event_path = tmp_path / "event.json"
    event_path.write_text("{}")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    observed = []

    def validate(document, context, event, checkout):
        observed.append(checkout)
        return {"status": "PASS", "policy_authorized": False}

    monkeypatch.setattr(launch, "validate_launch", validate)
    output = tmp_path / "receipt.json"
    assert launch.main(["--manifest", str(manifest), "--repo", str(repo), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["status"] == "PASS"
    assert observed[0]["head"] == git(repo, "rev-parse", "HEAD")
    assert launch.main(["--manifest", str(manifest), "--repo", str(repo), "--output", str(output)]) == 1


def test_cli_fail_does_not_create_pass_receipt(checkout_repo, monkeypatch, tmp_path):
    repo, manifest, _ = checkout_repo
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    output = tmp_path / "receipt.json"
    assert launch.main(["--manifest", str(manifest), "--repo", str(repo), "--output", str(output)]) == 1
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "STOP" and receipt["policy_authorized"] is False


@pytest.mark.parametrize("field", ["id", "login", "type"])
@pytest.mark.parametrize("malformed", [[], {}, None])
def test_malformed_identity_never_admitted(valid, field, malformed):
    valid.attempt["triggering_actor"][field] = malformed
    with pytest.raises(launch.LaunchError):
        verify(valid)


def test_later_source_commit_cannot_reuse_source_digest(checkout_repo):
    repo, manifest, document = checkout_repo
    helper = repo / ".github/scripts/forge_ci/controller.py"
    helper.write_bytes(b"# changed reviewed helper\n")
    git(repo, "add", ".")
    git(repo, "commit", "--amend", "-qm", "helper changed during control transition")
    document["launch"]["helper_sha256"][".github/scripts/forge_ci/controller.py"] = hashlib.sha256(helper.read_bytes()).hexdigest()
    with pytest.raises(launch.LaunchError, match="non-control source"):
        launch.inspect_checkout(repo, document, manifest_path=manifest)


def test_mode_drift_rejected(checkout_repo):
    repo, manifest, document = checkout_repo
    (repo / "source.txt").chmod(0o755)
    with pytest.raises(launch.LaunchError, match="mode drift"):
        launch.inspect_checkout(repo, document, manifest_path=manifest)


def test_seed_source_hash_includes_helpers(checkout_repo):
    repo, manifest, document = checkout_repo
    actual = launch.inspect_checkout(repo, document, manifest_path=manifest)
    source_only = launch.source_digest([{
        "path": "source.txt", "mode": "100644", "sha256": hashlib.sha256(b"reviewed source\n").hexdigest(),
    }])
    assert actual["source_sha256"] != source_only


def test_source_symlink_is_hashed_as_link_bytes(checkout_repo):
    repo, manifest, document = checkout_repo
    # Construct a new seed with the same fixed helpers and a normal source symlink.
    git(repo, "checkout", "-q", document["launch"]["seed_sha"])
    (repo / "source-link").symlink_to("source.txt")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "reviewed source symlink")
    document["launch"]["seed_sha"] = git(repo, "rev-parse", "HEAD")
    manifest.write_bytes(launch.canonical_bytes(document))
    workflow = repo / document["launch"]["workflow_path"]
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_bytes(b"# exact dedicated workflow\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "control transition")
    result = launch.inspect_checkout(repo, document, manifest_path=manifest)
    assert result["source_sha256"] != document["launch"]["source_sha256"]


def test_deadline_is_restored_after_http_failure(monkeypatch):
    previous_handler = launch.signal.getsignal(launch.signal.SIGALRM)
    install_http(monkeypatch, FakeResponse(b"{}", status=429))
    with pytest.raises(launch.LaunchError):
        launch.fetch_public_attempt("https://api.github.com/repos/owner/project/actions/runs/1/attempts/1")
    assert launch.signal.getitimer(launch.signal.ITIMER_REAL) == (0.0, 0.0)
    assert launch.signal.getsignal(launch.signal.SIGALRM) == previous_handler


def test_service_helper_is_mandatory_source_bound_launch_input(valid):
    path = ".github/scripts/forge_ci/user_service.py"
    assert path in launch.HELPER_PATHS
    missing = copy.deepcopy(valid.document)
    missing["launch"]["helper_sha256"].pop(path)
    with pytest.raises(launch.LaunchError):
        launch.validate_manifest(missing)
    valid.checkout["helper_sha256"][path] = "f" * 64
    with pytest.raises(launch.LaunchError):
        verify(valid)
