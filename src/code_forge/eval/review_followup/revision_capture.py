"""Collect bounded source excerpts from local Git commits, not worktree files."""

import json
import os
from pathlib import Path
import re
import subprocess

from .fixtures import MAX_BYTES, InputError, digest, enum, fields, identifier, parse, read_file, require

_MAX_SELECTIONS = 16
_MAX_SPAN = 64 * 1024
_COMMIT = re.compile(r"git:[0-9a-f]{40}")


def _git(repository, *args):
    return subprocess.run(
        [
            "git",
            "--no-pager",
            "--no-replace-objects",
            "--literal-pathspecs",
            "-c",
            "protocol.allow=never",
            "-C",
            os.fspath(repository),
            *args,
        ],
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=True,
    ).stdout


def _valid_path(path):
    return (
        type(path) is str
        and 0 < len(path) <= 1024
        and all(part not in ("", ".", "..") for part in path.split("/"))
        and "\\" not in path
        and not any(ord(ch) < 32 or 0xD800 <= ord(ch) <= 0xDFFF for ch in path)
    )


def _selections(raw):
    req = parse(raw, "request")
    fields(req, "schema finding_id base candidate selections", "request")
    require(type(req["schema"]) is int and req["schema"] == 1, "request.schema")
    require(identifier(req["finding_id"]), "request.finding_id")
    for role in ("base", "candidate"):
        require(type(req[role]) is str and _COMMIT.fullmatch(req[role]) is not None, role)
    require(req["base"] != req["candidate"], "request.candidate")
    items = req["selections"]
    require(type(items) is list and 0 < len(items) <= _MAX_SELECTIONS, "request.selections")
    ids = set()
    for i, item in enumerate(items):
        field = f"request.selections/{i}"
        fields(item, "id role path start end", field)
        require(identifier(item["id"]) and item["id"] not in ids, f"{field}/id")
        require(enum(item["role"], "base", "candidate"), f"{field}/role")
        require(_valid_path(item["path"]), f"{field}/path")
        start, end = item["start"], item["end"]
        require(
            type(start) is int
            and type(end) is int
            and 0 <= start < end <= MAX_BYTES
            and end - start <= _MAX_SPAN,
            f"{field}/span",
        )
        ids.add(item["id"])
    return req


def _snapshot(repository, commit, path):
    entries = _git(repository, "ls-tree", "-z", "--full-tree", commit, "--", path)
    if not entries:
        return "path-not-in-tree", None, None
    records = entries.removesuffix(b"\x00").split(b"\x00")
    if len(records) != 1:
        return "path-not-unique", None, None
    header, actual = records[0].split(b"\t", 1)
    mode, kind, oid = header.split()
    if actual != path.encode("utf-8") or kind != b"blob" or mode not in (b"100644", b"100755"):
        return "not-regular-blob", None, None
    blob = oid.decode("ascii")
    size = int(_git(repository, "cat-file", "-s", blob))
    if not 0 <= size <= MAX_BYTES:
        return "blob-too-large", None, None
    raw = _git(repository, "cat-file", "blob", blob)
    if len(raw) != size:
        return "blob-size-mismatch", None, None
    return None, blob, raw


def capture_revision_evidence(repository, request_file):
    """Emit mechanical evidence only, including explicit holes in the request."""
    raw = read_file(request_file.parent, request_file.name, "request")
    req = _selections(raw)
    root = repository.resolve()
    try:
        # Accept an exact checkout root or an exact bare repo, not an implicitly
        # discovered enclosing checkout. v1 reuses the replay SHA-1 identity.
        bare = _git(root, "rev-parse", "--is-bare-repository") == b"true\n"
        flag = "--absolute-git-dir" if bare else "--show-toplevel"
        reported = Path(os.fsdecode(_git(root, "rev-parse", flag).removesuffix(b"\n")))
        if reported.resolve() != root:
            raise InputError("repository-root-mismatch", "repository")
        if _git(root, "rev-parse", "--show-object-format") != b"sha1\n":
            raise InputError("unsupported-object-format", "repository")
    except (OSError, subprocess.SubprocessError) as exc:
        raise InputError("repository-unavailable", "repository") from exc

    revision_errors = {}
    for role in ("base", "candidate"):
        try:
            match = _git(root, "cat-file", "-t", req[role][4:]) == b"commit\n"
            revision_errors[role] = None if match else "revision-not-commit"
        except (OSError, subprocess.SubprocessError):
            revision_errors[role] = "revision-unavailable"

    rows = []
    for item in req["selections"]:
        role = item["role"]
        rev = req[role]
        error = revision_errors[role]
        payload = None
        if error is None:
            try:
                error, oid, source = _snapshot(root, rev[4:], item["path"])
            except (OSError, subprocess.SubprocessError, ValueError):
                error = "blob-unavailable"
            if error is None:
                excerpt = source[item["start"] : item["end"]]
                if item["end"] > len(source):
                    error = "span-out-of-range"
                else:
                    try:
                        excerpt_text = excerpt.decode("utf-8")
                    except UnicodeDecodeError:
                        error = "excerpt-not-utf8"
                    else:
                        payload = {
                            "git_blob": oid,
                            "blob_sha256": digest(source),
                            "artifact_sha256": digest(excerpt),
                            "text": excerpt_text,
                            "selector": {"kind": "source_bytes", "start": 0, "end": len(excerpt)},
                        }
        rows.append({**item, "revision": rev, "error": error, "artifact": payload})
    return {
        "schema": 1,
        "kind": "revision_source_capture",
        "finding_id": req["finding_id"],
        "base": req["base"],
        "candidate": req["candidate"],
        "request_sha256": digest(raw),
        "revision_errors": revision_errors,
        "evidence": rows,
    }


def _location_digest(row):
    # Hash only this selection's origin. The rest of the request may change
    # without affecting a proposal that did not reference those selections.
    location = [
        "forge-capture-location-v1",
        row["revision"],
        row["path"],
        row["artifact"]["git_blob"],
        row["start"],
        row["end"],
    ]
    return digest(json.dumps(location, ensure_ascii=True, separators=(",", ":")).encode("ascii"))


def capture_evidence_snapshot(repository, request_file):
    """Return fresh source and a snapshot for the existing offline validator."""
    from .revision_validation import EvidenceSnapshot, _Evidence

    capture = capture_revision_evidence(repository, request_file)
    entries = []
    missing = []
    for row in capture["evidence"]:
        if row["error"] is not None:
            missing.append(row["id"])
            continue
        payload = row["artifact"]
        entries.append(
            _Evidence(
                row["id"],
                f"{row['role']}_source",
                row["revision"],
                payload["artifact_sha256"],
                len(payload["text"].encode("utf-8")),
                capture_location_sha256=_location_digest(row),
            )
        )
    snapshot = EvidenceSnapshot(
        capture["finding_id"],
        capture["candidate"],
        tuple(entries),
        tuple(role for role, error in capture["revision_errors"].items() if error is not None),
        tuple(missing),
    )
    return capture, snapshot


def validate_captured_proposal(repository, request_file, proposal_file, validation_id):
    """Reacquire source from trusted local Git and mechanically check a proposal."""
    from .revision_validation import validate_proposal

    raw = read_file(proposal_file.parent, proposal_file.name, "proposal")
    capture, snapshot = capture_evidence_snapshot(repository, request_file)
    record = validate_proposal(raw, snapshot, validation_id=validation_id)
    return {
        "schema": 1,
        "capture": capture,
        "host_validation": json.loads(record.canonical),
    }
