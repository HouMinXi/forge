# SPDX-License-Identifier: Apache-2.0

"""Replay the pinned Stage 1 contract with an independent byte oracle."""

import argparse
import base64
import hashlib
import io
import json
import locale
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

from code_forge.offline_planner.planner import plan

MANIFEST_SHA256 = "e2114842073ca94450e2656498dd06b7593bba0447a4d29ecf631a7388fc20aa"
PROFILES = (("17", "C"), ("1147", "C.UTF-8"))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canon(value):
    # json.dumps escapes the five controls below with short escapes. Replace
    # only actual escape tokens, not a literal backslash followed by a letter.
    escapes = dict(zip("bfnrt", (8, 12, 10, 13, 9), strict=True))
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
    )
    return re.sub(
        r"\\(?:[bfnrt]|\\)",
        lambda m: f"\\u{escapes[m[0][1]]:04x}" if m[0][1] in escapes else m[0],
        encoded,
    ).encode("utf-8")


def require_equal(case, label, actual, expected):
    if actual == expected:
        return
    offset = next(
        (i for i, pair in enumerate(zip(actual, expected, strict=False)) if pair[0] != pair[1]),
        min(len(actual), len(expected)),
    )
    raise ValueError(
        f"{case}: {label}: first differing byte {offset} (zero-based); "
        f"actual bytes={len(actual)} sha256={digest(actual)}; "
        f"expected bytes={len(expected)} sha256={digest(expected)}"
    )


ARCHIVE = Path(__file__).parent / "fixtures/offline_planner/stage1-frozen.tar.xz.b64"
ARCHIVE_SHA256 = "cdc2ab74a6aeb27bf4e220d004c2c00eeed6b1708c0363f9e26b546649d3407d"


def inventory():
    compressed = base64.b64decode(b"".join(ARCHIVE.read_bytes().split()), validate=True)
    if digest(compressed) != ARCHIVE_SHA256:
        raise ValueError("archive does not match the GitHub freeze")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(compressed), mode="r:xz") as archive:
        for member in archive:
            if not member.isfile() or member.name in files:
                raise ValueError(f"unexpected archive member: {member.name}")
            with archive.extractfile(member) as stream:
                files[member.name] = stream.read()
    raw = files.pop("hash-manifest.json")
    if digest(raw) != MANIFEST_SHA256:
        raise ValueError("hash-manifest.json does not match the frozen manifest")
    manifest = json.loads(raw)
    if files.keys() != manifest.keys():
        raise ValueError("archive file set differs from manifest")
    for name, record in manifest.items():
        data = files[name]
        if len(data) != record["bytes"] or digest(data) != record["sha256"]:
            raise ValueError(f"{name}: length or SHA-256 mismatch")
    return files


def replay(planner=plan):
    files = inventory()
    rows = json.loads(files["cases.json"])
    if len(rows) != 58 or len({row["name"] for row in rows}) != len(rows):
        raise ValueError("unexpected fixture inventory")
    success = 0
    failures = 0
    for row in rows:
        case = row["name"]
        prefix = f"vectors/{case}/"
        request = files[prefix + "input.json"]
        expected = files[prefix + "expected.json"]
        result = json.loads(expected)
        require_equal(case, "canonical golden", canon(result), expected)
        bundle = json.loads(files[prefix + "artifacts.json"])
        artifacts = {key: base64.b64decode(value, validate=True) for key, value in bundle.items()}
        actual = planner(request, artifacts.get)
        if not isinstance(actual, bytes):
            raise TypeError(f"{case}: adapter/host failure: expected bytes")
        try:
            require_equal(case, "planner output", actual, expected)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            failures += 1
        else:
            result = json.loads(actual)

        if row["outcome"] == "success":
            success += 1
            preimage = files[prefix + "output-preimage.json"]
            declared = result.pop("plan_digest")
            require_equal(case, "plan digest preimage", canon(result), preimage)
            require_equal(case, "plan digest", declared.encode(), digest(preimage).encode())
            inp = json.loads(request)
            require_equal(
                case, "input digest", result["input_digest"].encode(), digest(canon(inp)).encode()
            )
        elif (result["error_code"], result["field_path"]) != (row["expected"], row["pointer"]):
            raise ValueError(f"{case}: golden error and case index disagree")
    print(
        f"planner replay: {len(rows) - failures}/{len(rows)} cases; "
        f"{success} success / {len(rows) - success} error vectors; "
        f"locale={locale.setlocale(locale.LC_ALL)}, hash seed={os.environ.get('PYTHONHASHSEED')}"
    )
    return int(bool(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        locale.setlocale(locale.LC_ALL, "")
        return replay()
    code = 0
    for seed, loc in PROFILES:
        env = dict(os.environ, PYTHONHASHSEED=seed, LC_ALL=loc, PYTHONCOERCECLOCALE="0", PYTHONUTF8="0")
        cmd = [sys.executable, "-m", "tests.offline_planner_replay", "--worker"]
        if subprocess.run(cmd, env=env, check=False).returncode:
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
