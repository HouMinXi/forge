#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare one fixed Forge grouping receipt against a separately trusted sidecar."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import sys
import zipfile
import zlib

PROFILE = "forge-r3-grouping-v1"
IDENTITY = {"workflow_sha", "subject_sha", "run_id", "run_attempt"}
SIDE_FIELDS = {"schema", "profile", "identity", "archive_sha256"}
NODE = "tests/test_e2e_check.py::TestGroupSourceFiles::test_two_top_dirs_produce_two_groups"
TEST_SHA = "302edd36eb8e2160071f328103679a73c1adff1aef6d0720983a52493d0e78dd"
SOURCE_SHA = "ae6b4d1374e98b976e1b68e580adb4d34db0d17daa102377263a633e83ed756e"
VARIANT_SHA = "3a4dad81c3f6948a006674bf769231b74545c2372e21efa5a2f29db5804173a0"
ZIP_LIMIT = 4096
SUMMARY_LIMIT = 4096
SIDECAR_LIMIT = 2048
MEMBER = "r3-summary.json"


class Reject(ValueError):
    """An input does not conform to independently reviewed expectations."""


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Reject("duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(_value):
    raise Reject("non-finite JSON constant")


def parse_object(raw, limit, label):
    if not 0 < len(raw) <= limit:
        raise Reject(f"{label} size")
    try:
        data = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_keys,
            parse_constant=invalid_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise Reject(f"malformed {label} JSON") from error
    if type(data) is not dict:
        raise Reject(f"{label} shape")
    return data


def hex_value(value, length):
    return type(value) is str and re.fullmatch(f"[0-9a-f]{{{length}}}", value) and value != "0" * length


def positive_decimal(value):
    return type(value) is str and re.fullmatch("[1-9][0-9]{0,19}", value)


def parse_expectation(raw):
    data = parse_object(raw, SIDECAR_LIMIT, "expectation")
    if (
        set(data) != SIDE_FIELDS
        or type(data["schema"]) is not int
        or data["schema"] != 1
        or data["profile"] != PROFILE
    ):
        raise Reject("unsupported expectation contract")
    ident = data["identity"]
    if (
        type(ident) is not dict
        or set(ident) != IDENTITY
        or not hex_value(ident["workflow_sha"], 40)
        or not hex_value(ident["subject_sha"], 40)
        or not positive_decimal(ident["run_id"])
        or not positive_decimal(ident["run_attempt"])
    ):
        raise Reject("invalid expectation identity")
    if not hex_value(data["archive_sha256"], 64):
        raise Reject("invalid expected digest")
    return data


def get_expected(expectation):
    rows = [
        {
            "stage": stage,
            "exit": exit_code,
            "source_sha256": source,
            "outcomes": ["passed", call, "passed"],
            "line": 151,
            "observation_phase": "call",
            "observations": observations,
            "oracle": oracle,
        }
        for stage, exit_code, source, call, observations, oracle in (
            ("baseline", 0, SOURCE_SHA, "passed", ["a", "b"], "pass"),
            ("variant", 1, VARIANT_SHA, "failed", ["one_group", "one_group"], "expected_assertion"),
            ("restored", 0, SOURCE_SHA, "passed", ["a", "b"], "pass"),
        )
    ]
    return {
        "schema": 1,
        "case": "grouping",
        **expectation["identity"],
        "node": NODE,
        "test_sha256": TEST_SHA,
        "stages": rows,
    }


def check_summary(raw, expectation):
    data = parse_object(raw, SUMMARY_LIMIT, "summary")
    for key in sorted(IDENTITY):
        want = expectation["identity"][key]
        if type(data.get(key)) is not str or data[key] != want:
            raise Reject(f"summary identity mismatch: {key}")
    expected = get_expected(expectation)
    # Canonical JSON retains the type distinctions between 1, True and 1.0.
    try:
        actual_json = json.dumps(data, sort_keys=True, allow_nan=False)
        expected_json = json.dumps(expected, sort_keys=True, allow_nan=False)
    except (ValueError, RecursionError) as error:
        raise Reject("invalid summary value") from error
    if actual_json != expected_json:
        raise Reject("closed summary does not match the grouping profile")
    return data


def summary_from_zip(raw):
    if not 0 < len(raw) <= ZIP_LIMIT:
        raise Reject("archive size")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise Reject("archive must contain exactly one entry")
            entry = members[0]
            mode = entry.external_attr >> 16
            if (
                entry.orig_filename != MEMBER
                or entry.filename != MEMBER
                or not stat.S_ISREG(mode)
                or entry.flag_bits & 1
                or not 0 < entry.file_size <= SUMMARY_LIMIT
                or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            ):
                raise Reject("unexpected archive member")
            # Do not extract anything to disk.
            with archive.open(entry) as stream:
                data = stream.read(SUMMARY_LIMIT + 1)
            if len(data) != entry.file_size:
                raise Reject("summary member length mismatch")
            return data
    except (EOFError, zipfile.BadZipFile, NotImplementedError, RuntimeError, zlib.error) as error:
        raise Reject("invalid ZIP archive") from error


def verify(archive, expectation_bytes):
    expectation = parse_expectation(expectation_bytes)
    if not 0 < len(archive) <= ZIP_LIMIT:
        raise Reject("archive size")
    if hashlib.sha256(archive).hexdigest() != expectation["archive_sha256"]:
        raise Reject("archive SHA-256 mismatch")
    return check_summary(summary_from_zip(archive), expectation)


def read_bounded(path, size):
    with path.open("rb") as stream:
        return stream.read(size + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expectation", type=Path, help="independently reviewed trusted sidecar")
    parser.add_argument("archive", type=Path, help="receipt archive (not a source of expectations)")
    args = parser.parse_args()
    try:
        verify(read_bounded(args.archive, ZIP_LIMIT), read_bounded(args.expectation, SIDECAR_LIMIT))
    except (Reject, OSError) as error:
        print(
            f"REJECT: {error}" if isinstance(error, Reject) else "REJECT: cannot read input",
            file=sys.stderr,
        )
        return 1
    print("PASS: grouping receipt matches supplied review expectations (not attestation)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
