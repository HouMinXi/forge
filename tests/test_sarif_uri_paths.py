# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""SARIF URI decoding must preserve the reviewed file's lexical identity."""

import json
import subprocess

import pytest

from code_forge.delta import filter_delta
from code_forge.diff import extract_changed_lines
from code_forge.parsers import parse_output
from code_forge.parsers.base import Finding, ToolError
from code_forge.parsers.ruff import parse_ruff
from code_forge.parsers.semgrep import parse_semgrep


def _generic(output, **kwargs):
    return parse_output(output, "sarif", **kwargs)


PARSERS = [parse_ruff, parse_semgrep, _generic]


def _location(artifact, line=2):
    return {
        "physicalLocation": {
            "artifactLocation": artifact,
            "region": {"startLine": line, "endLine": line, "startColumn": 7},
        }
    }


def _result(*artifacts, message="undefined subject_name", line=2):
    return {
        "ruleId": "F821",
        "level": "error",
        "message": {"text": message},
        "locations": [_location(artifact, line) for artifact in artifacts],
    }


def _packet(results, artifacts=()):
    return json.dumps(
        {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "URI test producer"}},
                    "artifacts": [{"location": artifact} for artifact in artifacts],
                    "results": results,
                }
            ],
        }
    )


def _parse(parser, results, artifacts=()):
    return parser(_packet(results, artifacts), tool_name="uri-test", exit_code=1)


@pytest.mark.parametrize("parser", PARSERS)
@pytest.mark.parametrize(
    "uri,expected",
    [
        ("file:///tmp/plain.py", "/tmp/plain.py"),
        ("file:///tmp/space%20name.py", "/tmp/space name.py"),
        ("file:///tmp/space%2520name.py", "/tmp/space%20name.py"),
        ("file:///tmp/percent%25.py", "/tmp/percent%.py"),
        ("file:///tmp/hash%23query%3F.py", "/tmp/hash#query?.py"),
        ("file:///tmp/hash%2523query%253f.py", "/tmp/hash%23query%3f.py"),
        ("file:///tmp/literal+encoded%2B.py", "/tmp/literal+encoded+.py"),
        ("file:///tmp/caf%C3%A9.py", "/tmp/caf\u00e9.py"),
        ("file:///tmp/caf%c3%a9.py", "/tmp/caf\u00e9.py"),
        ("file:///tmp/caf\u00e9.py", "/tmp/caf\u00e9.py"),
        ("file:///tmp/cafe%CC%81.py", "/tmp/cafe\u0301.py"),
        ("file:///tmp/%E7%A9%BA%20name.py", "/tmp/\u7a7a name.py"),
        ("FiLe:///tmp/MixedCase.py", "/tmp/MixedCase.py"),
        ("FILE:///tmp/space%20name.py", "/tmp/space name.py"),
        ("file:///tmp/%252F%255c.py", "/tmp/%2F%5c.py"),
        ("file:///tmp/%2500%25ff%25.py", "/tmp/%00%ff%.py"),
        ("file:///tmp/parent/../target.py", "/tmp/parent/../target.py"),
        ("file:///tmp/./nested//target.py", "/tmp/./nested//target.py"),
        ("file:///tmp/%2E%2E/target.py", "/tmp/../target.py"),
        # Preserve stripped spelling; this does not implement Windows translation.
        ("file:///C:/Folder/space%20name.py", "/C:/Folder/space name.py"),
    ],
)
def test_local_file_uri_maps_once_without_normalizing(parser, uri, expected):
    items = _parse(parser, [_result({"uri": uri})])
    assert items == [Finding(expected, 2, 2, 7, "F821", "error", "undefined subject_name", "uri-test")]


@pytest.mark.parametrize("parser", PARSERS)
@pytest.mark.parametrize(
    "path",
    [
        "relative.py",
        "src/space name.py",
        "src/space%20name.py",
        "src/space%2520name.py",
        "src/%ff%00%2F%5C%2.py",
        "/tmp/space%20name.py",
        "/tmp/hash#query?plus+.py",
        "src/caf\u00e9.py",
        "src/cafe\u0301.py",
        "../src/./name.py",
        "/tmp/parent/../name.py",
        "//server/share/literal%20.py",
        "C:\\Folder\\literal%20.py",
        "/tmp/raw\\name.py",
        "%SRCROOT%/literal%20.py",
        "src/FILE:name.py",
    ],
)
def test_bare_producer_paths_are_not_uri_decoded(parser, path):
    items = _parse(parser, [_result({"uri": path, "uriBaseId": "%SRCROOT%"})])
    assert len(items) == 1 and isinstance(items[0], Finding)
    assert items[0].file == path


@pytest.mark.parametrize("parser", PARSERS)
@pytest.mark.parametrize("reference", ["inline", "indexed", "matching"])
@pytest.mark.parametrize("uri_base_id", [None, "%SRCROOT%"])
def test_reference_forms_decode_once_after_resolution(parser, reference, uri_base_id):
    cached = {"uri": "file:///tmp/space%2520name.py"}
    if uri_base_id is not None:
        cached["uriBaseId"] = uri_base_id
    artifact = dict(cached) if reference == "inline" else {"index": 0}
    if reference == "matching":
        artifact.update(cached)
    items = _parse(parser, [_result(artifact)], [cached])
    assert len(items) == 1 and isinstance(items[0], Finding)
    assert items[0].file == "/tmp/space%20name.py"


@pytest.mark.parametrize("parser", PARSERS)
@pytest.mark.parametrize("reference", ["inline", "indexed"])
@pytest.mark.parametrize(
    "uri",
    [
        "file:///tmp/bad%.py",
        "file:///tmp/bad%2.py",
        "file:///tmp/bad%GG.py",
        "file:///tmp/bad%ff.py",
        "file:///tmp/bad%c3.py",
        "file:///tmp/bad%C3%28.py",
        "file:///tmp/bad%ED%A0%80.py",
        "file:///tmp/bad%00.py",
        "file:///tmp/hidden%2fname.py",
        "file:///tmp/hidden%2Fname.py",
        "file:///tmp/hidden%5cname.py",
        "file:///tmp/hidden%5Cname.py",
        "file:///tmp/raw?query.py",
        "file:///tmp/raw.py?",
        "file:///tmp/raw#fragment.py",
        "file:///tmp/raw.py#",
        "file:///tmp/raw\x00name.py",
        "file:///tmp/raw\nname.py",
        "file:///tmp/raw\rname.py",
        "file:///tmp/raw\tname.py",
        "file:///tmp/raw\x1fname.py",
        "file:///tmp/raw\x7fname.py",
        "file:///tmp/raw\x85name.py",
        "file:///tmp/raw\\name.py",
        "file:///C:\\Folder\\name.py",
        # Valid URI forms below are unsupported, rather than inherently malformed.
        "file://localhost/tmp/name.py",
        "file://server/share/name.py",
        "file:/tmp/name.py",
        "file:relative.py",
        "file:////server/share/name.py",
        "file://///server/share/name.py",
        "FILE://localhost/tmp/name.py",
    ],
)
def test_unrepresentable_uri_retains_neighbors_and_incomplete_error(parser, reference, uri):
    artifact = {"uri": uri} if reference == "inline" else {"index": 0}
    items = _parse(
        parser,
        [
            _result(artifact, {"uri": "file:///tmp/valid%20neighbor.py"}),
            _result({"uri": "other.py"}, message="another diagnostic", line=4),
        ],
        [{"uri": uri}],
    )
    findings = [item for item in items if isinstance(item, Finding)]
    errors = [item for item in items if isinstance(item, ToolError)]
    assert [(item.file, item.line, item.message) for item in findings] == [
        ("/tmp/valid neighbor.py", 2, "undefined subject_name"),
        ("other.py", 4, "another diagnostic"),
    ]
    assert len(errors) == 1
    assert errors[0].tool_name == "uri-test" and errors[0].exit_code == 1
    assert errors[0].message.startswith("Incomplete uri-test SARIF evidence:")
    delta, all_findings = filter_delta(items, {})
    assert delta == errors and all_findings == items


@pytest.mark.parametrize("parser", PARSERS)
@pytest.mark.parametrize(
    "cached,reference",
    [
        # Even equivalent decoded paths must not bypass raw alias validation.
        ({"uri": "file:///tmp/%61.py"}, {"index": 0, "uri": "file:///tmp/a.py"}),
        ({"uri": "file:///tmp/a.py"}, {"index": 0, "uri": "FILE:///tmp/a.py"}),
        (
            {"uri": "file:///tmp/space%20name.py", "uriBaseId": "%SRCROOT%"},
            {"index": 0, "uri": "file:///tmp/space%20name.py"},
        ),
        ({"uri": "file:///tmp/space%20name.py", "index": 1}, {"index": 0}),
        ({"uri": "file:///tmp/space%20name.py"}, {"index": 1}),
    ],
)
def test_reference_conflicts_remain_incomplete_with_valid_neighbor(parser, cached, reference):
    items = _parse(parser, [_result(reference, {"uri": "neighbor.py"})], [cached])
    assert len(items) == 2
    assert isinstance(items[0], Finding) and items[0].file == "neighbor.py"
    assert isinstance(items[1], ToolError)
    assert items[1].message.startswith("Incomplete uri-test SARIF evidence:")


def _staged_diff(root, names):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True, timeout=10
        ).stdout

    git("init", "-q")
    git("add", "--", *names)
    return git("diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-color", "-U0")


@pytest.mark.parametrize("parser", PARSERS)
@pytest.mark.parametrize("name", ["space name.py", "space%20name.py", "caf\u00e9#query?+%.py"])
def test_real_git_delta_keeps_encoded_file_and_message_identity(tmp_path, parser, name):
    source = tmp_path / name
    source.write_text("# subject\nprint(subject_name)\n", encoding="utf-8")
    plain = tmp_path / "plain.py"
    plain.write_text("print(plain_name)\n", encoding="utf-8")
    changed = extract_changed_lines(_staged_diff(tmp_path, [name, plain.name]), repo_root=tmp_path)
    assert changed == {name: {1, 2}, str(source): {1, 2}, plain.name: {1}, str(plain): {1}}
    items = _parse(
        parser,
        [
            _result({"uri": source.as_uri()}, message="undefined subject_name"),
            _result({"uri": plain.as_uri()}, message="undefined plain_name", line=1),
        ],
    )
    delta, all_findings = filter_delta(items, changed)
    assert [(item.file, item.line, item.message) for item in delta] == [
        (str(source), 2, "undefined subject_name"),
        (str(plain), 1, "undefined plain_name"),
    ]
    assert all_findings == items and all_findings is not items


@pytest.mark.parametrize("parser", PARSERS)
@pytest.mark.parametrize(
    "subject_name,decoy_name", [("space name.py", "space%20name.py"), ("space%20name.py", "space%2520name.py")]
)
@pytest.mark.parametrize("change_subject", [False, True])
def test_real_git_delta_never_assigns_finding_to_clean_percent_decoy(
    tmp_path, parser, subject_name, decoy_name, change_subject
):
    source = tmp_path / subject_name
    source.write_text("# subject\nprint(subject_name)\n", encoding="utf-8")
    decoy = tmp_path / decoy_name
    decoy.write_text("# clean decoy\nprint('clean')\n", encoding="utf-8")
    names = [decoy.name, *([source.name] if change_subject else [])]
    changed = extract_changed_lines(_staged_diff(tmp_path, names), repo_root=tmp_path)
    assert changed == {key: {1, 2} for name in names for key in (name, str(tmp_path / name))}
    items = _parse(parser, [_result({"uri": source.as_uri()})])
    delta, all_findings = filter_delta(items, changed)
    assert [(item.file, item.line, item.message) for item in delta] == (
        [(str(source), 2, "undefined subject_name")] if change_subject else []
    )
    assert len(items) == 1 and isinstance(items[0], Finding)
    assert items[0].file == str(source) and all_findings == items
