"""Release only the original qualified wheel after a complete public-byte check.

The expected ZIP is an in-memory comparator, never the published deliverable.
Only six generated members' bounded build-clock timestamps may vary. Everything
else is derived from Git blobs or the reviewed backend's fixed serialization.
"""

import base64
import calendar
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import tomllib
import zipfile

WHEEL_NAME = "code_review_forge-2.9.0-py3-none-any.whl"
DIST_INFO = "code_review_forge-2.9.0.dist-info"
MAX_WHEEL = 16 * 1024 * 1024
MAX_SOURCE = 16 * 1024 * 1024
MAX_PROOF = 65536
# Reviewed public setuptools distribution metadata, not an arbitrary WHEEL value.
GENERATOR = "setuptools (84.2.1)"
# Exact serializations used by the reviewed backend for this project's declarations.
REQUIREMENTS = {
    "pyyaml>=6.0": "pyyaml>=6.0",
    "unidiff>=0.7.5,<0.8.0": "unidiff<0.8.0,>=0.7.5",
    "pytest==9.2.0": "pytest==9.2.0",
    "pytest-asyncio>=1.0": "pytest-asyncio>=1.0",
    "ruff==0.17.3": "ruff==0.17.3",
    "mutmut>=3.4,<4.0": "mutmut<4.0,>=3.4",
    "jsonschema>=4.18": "jsonschema>=4.18",
    "google-auth>=2.35.0": "google-auth>=2.35.0",
    "requests>=2.20.0": "requests>=2.20.0",
    "mcp>=1.27,<2": "mcp<2,>=1.27",
    "datasets>=5.0,<6": "datasets<6,>=5.0",
}
# Exact platform-qualified metadata from the reviewed backend. Keep these
# separate from unmarked requirements so arbitrary marker text is never modeled.
PLATFORM_EXTRA_REQUIREMENTS = {
    ("semgrep", "semgrep>=1.176,<1.183; platform_system == 'Darwin' and platform_machine == 'x86_64'"): (
        'semgrep<1.183,>=1.176; (platform_system == "Darwin" and platform_machine == "x86_64") and extra == "semgrep"'
    ),
    ("semgrep", "semgrep>=1.176,<1.184; platform_system != 'Darwin' or platform_machine != 'x86_64'"): (
        'semgrep<1.184,>=1.176; (platform_system != "Darwin" or platform_machine != "x86_64") and extra == "semgrep"'
    ),
}


def read_regular(directory, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise ValueError("file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(limit + 1)
        after = os.fstat(fd)
        if len(raw) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("changed")
        return raw
    finally:
        os.close(fd)


def open_directory(path, *, directory=None):
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)


def unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate")
        value[key] = item
    return value


def reject_number(_value):
    raise ValueError("number")


def source_from_git(root):
    env = {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C.UTF-8",
    }

    def git(*args):
        result = subprocess.run(
            ["git", *args], cwd=root, env=env, capture_output=True, timeout=10, check=True
        )
        if len(result.stdout) > MAX_SOURCE:
            raise ValueError("source size")
        return result.stdout

    commit = git("rev-parse", "HEAD").decode("ascii").strip()
    epoch = int(git("show", "-s", "--format=%ct", commit))
    listing = git("ls-tree", "-rz", commit, "--", "src", "pyproject.toml", "README.md", "LICENSE")
    files = {}
    total = 0
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, blob = metadata.split()
        name = raw_name.decode("ascii")
        if mode != b"100644" or kind != b"blob" or name in files:
            raise ValueError("source shape")
        content = git("cat-file", "blob", blob.decode("ascii"))
        total += len(content)
        if total > MAX_SOURCE:
            raise ValueError("source size")
        files[name] = content
    return {"commit": commit, "epoch": epoch, "files": files}


def single_line(value):
    if type(value) is not str or not value or any(c in value for c in "\r\n\0"):
        raise ValueError("metadata field")
    return value


def metadata_bytes(files):
    config = tomllib.loads(files["pyproject.toml"].decode("utf-8"))
    project = config["project"]
    if set(project) != {
        "name",
        "version",
        "description",
        "readme",
        "requires-python",
        "license",
        "authors",
        "classifiers",
        "dependencies",
        "optional-dependencies",
        "scripts",
    } or (project["name"], project["version"], project["readme"], project["license"]) != (
        "code-review-forge",
        "2.9.0",
        "README.md",
        "Apache-2.0",
    ):
        raise ValueError("project metadata")
    if project["scripts"] != {
        "code-forge": "code_forge_launcher:cli_main",
        "code-forge-mcp": "code_forge_launcher:stdio_main",
    }:
        raise ValueError("entry points")
    if len(project["authors"]) != 1 or set(project["authors"][0]) != {"name", "email"}:
        raise ValueError("authors")
    author = project["authors"][0]
    headers = [
        "Metadata-Version: 2.4",
        "Name: code-review-forge",
        "Version: 2.9.0",
        "Summary: " + single_line(project["description"]),
        "Author-email: " + single_line(author["name"]) + " <" + single_line(author["email"]) + ">",
        "License-Expression: Apache-2.0",
    ]
    headers.extend("Classifier: " + single_line(item) for item in project["classifiers"])
    headers.extend(
        [
            "Requires-Python: " + single_line(project["requires-python"]),
            "Description-Content-Type: text/markdown",
            "License-File: LICENSE",
        ]
    )
    headers.extend("Requires-Dist: " + REQUIREMENTS[item] for item in project["dependencies"])
    for extra, dependencies in project["optional-dependencies"].items():
        headers.append("Provides-Extra: " + single_line(extra))
        headers.extend(
            "Requires-Dist: "
            + (
                PLATFORM_EXTRA_REQUIREMENTS[(extra, item)]
                if (extra, item) in PLATFORM_EXTRA_REQUIREMENTS
                else REQUIREMENTS[item] + '; extra == "' + extra + '"'
            )
            for item in dependencies
        )
    headers.append("Dynamic: license-file")
    return ("\n".join(headers) + "\n\n").encode("utf-8") + files["README.md"]


def expected_members(source):
    """Model the reviewed backend's file-first, sorted-directory traversal."""
    files = source["files"]
    payload = {}
    for name, content in files.items():
        if not name.startswith("src/"):
            continue
        target = name.removeprefix("src/")
        if (
            not target.isascii()
            or any(part in {"", ".", ".."} for part in target.split("/"))
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-"
                for c in target
            )
            or not (
                target == "code_forge_launcher.py"
                or target.startswith("code_forge/")
                and (
                    target.endswith(".py")
                    or target.startswith(("code_forge/skills/", "code_forge/rules/"))
                    or target in {"code_forge/gate.schema.json", "code_forge/THIRD_PARTY_NOTICES.md"}
                )
            )
        ):
            raise ValueError("payload path")
        payload[target] = content
    if not payload or len(payload) > 512:
        raise ValueError("payload count")

    def visit(prefix):
        children = sorted(
            {name[len(prefix) :].split("/", 1)[0] for name in payload if name.startswith(prefix)}
        )
        result = []
        for child in children:
            name = prefix + child
            if name in payload:
                mode = 0o100600 if name.endswith(".py") else 0o100644
                result.append((name, payload[name], mode))
        for child in children:
            name = prefix + child
            if name not in payload:
                result.extend(visit(name + "/"))
        return result

    members = visit("")
    members.extend(
        [
            (DIST_INFO + "/licenses/LICENSE", files["LICENSE"], 0o100644),
            (DIST_INFO + "/METADATA", metadata_bytes(files), 0o100644),
            (
                DIST_INFO + "/WHEEL",
                (
                    "Wheel-Version: 1.0\nGenerator: "
                    + GENERATOR
                    + "\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
                ).encode("ascii"),
                0o100600,
            ),
            (
                DIST_INFO + "/entry_points.txt",
                b"[console_scripts]\ncode-forge = code_forge_launcher:cli_main\ncode-forge-mcp = code_forge_launcher:stdio_main\n",
                0o100600,
            ),
            (DIST_INFO + "/top_level.txt", b"code_forge\ncode_forge_launcher\n", 0o100600),
        ]
    )
    rows = io.StringIO(newline="")
    writer = csv.writer(rows, lineterminator="\n")
    for name, content, _mode in members:
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
        writer.writerow([name, "sha256=" + digest, len(content)])
    writer.writerow([DIST_INFO + "/RECORD", "", ""])
    members.append((DIST_INFO + "/RECORD", rows.getvalue().encode("utf-8"), 0o100664))
    return members


def validate_wheel(raw, source, started, finished):
    if not 0 < len(raw) <= MAX_WHEEL or not 0 <= finished - started <= 900:
        raise ValueError("size or duration")
    members = expected_members(source)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        observed = archive.infolist()
    if [item.filename for item in observed] != [name for name, _content, _mode in members]:
        raise ValueError("members")
    source_time = time.gmtime(max(source["epoch"], 315532800))[:6]
    source_time = (*source_time[:5], source_time[5] // 2 * 2)
    generated_times = []
    expected = io.BytesIO()
    with zipfile.ZipFile(expected, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False) as archive:
        for (name, content, mode), actual in zip(members, observed, strict=True):
            timestamp = source_time
            if name.startswith(DIST_INFO + "/"):
                # Approved public build metadata only. No other original ZIP field
                # is used to construct the expected archive.
                timestamp = actual.date_time
                seconds = calendar.timegm(timestamp)
                if time.gmtime(seconds)[:6] != timestamp or not started - 1 <= seconds <= finished:
                    raise ValueError("build timestamp")
                generated_times.append(seconds)
            info = zipfile.ZipInfo(name, date_time=timestamp)
            info.create_system = 3
            info.create_version = info.extract_version = 20
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    if generated_times[-1] < max(generated_times[:-1]):
        raise ValueError("record timestamp")
    if raw != expected.getvalue():
        raise ValueError("noncanonical wheel")


def integer_time(raw):
    if not raw.endswith(b"\n") or not raw[:-1].isdigit() or not 1 <= len(raw[:-1]) <= 12:
        raise ValueError("clock")
    return int(raw)


def now_seconds():
    return int(time.time())


def publish(runner_temp, public_directory, source):
    root = open_directory(runner_temp)
    private = qualification = dist = None
    try:
        names = [name for name in os.listdir(root) if name.startswith("forge-wheel-qualification.")]
        if (
            len(names) != 1
            or len(names[0]) != len("forge-wheel-qualification.") + 8
            or not names[0].removeprefix("forge-wheel-qualification.").isascii()
            or not names[0].removeprefix("forge-wheel-qualification.").isalnum()
        ):
            raise ValueError("private directory")
        private = open_directory(names[0], directory=root)
        if stat.S_IMODE(os.fstat(private).st_mode) != 0o700:
            raise ValueError("private permissions")
        started = integer_time(read_regular(private, "started", 32))
        finished = integer_time(read_regular(private, "finished", 32))
        now = now_seconds()
        if not started <= finished <= now or now - started > 900:
            raise ValueError("stale or future clock")
        qualification = open_directory("qualification", directory=private)
        proof = json.loads(
            read_regular(qualification, "build-proof.json", MAX_PROOF),
            object_pairs_hook=unique_keys,
            parse_constant=reject_number,
            parse_float=reject_number,
        )
        dist = open_directory("dist", directory=qualification)
        if os.listdir(dist) != [WHEEL_NAME]:
            raise ValueError("artifact roster")
        raw = read_regular(dist, WHEEL_NAME, MAX_WHEEL)
        if (
            type(proof) is not dict
            or proof.get("source_commit") != source["commit"]
            or proof.get("wheel") != WHEEL_NAME
            or proof.get("wheel_sha256") != hashlib.sha256(raw).hexdigest()
        ):
            raise ValueError("qualification binding")
        validate_wheel(raw, source, started, finished)
    finally:
        for fd in (dist, qualification, private, root):
            if fd is not None:
                os.close(fd)
    os.mkdir(public_directory, 0o700)
    directory = open_directory(public_directory)
    try:
        fd = os.open(
            WHEEL_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)  # ORIGINAL qualified bytes, not expected.getvalue().
        if read_regular(directory, WHEEL_NAME, MAX_WHEEL) != raw:
            raise ValueError("publication changed")
    finally:
        os.close(directory)


def main():
    if len(sys.argv) != 3 or any(
        os.environ.get(name) != "success"
        for name in ("SETUP_OUTCOME", "QUALIFICATION_OUTCOME", "JOB_STATUS")
    ):
        raise ValueError("qualification not successful")
    root = Path(__file__).resolve().parents[1]
    private_root = Path(sys.argv[1])
    if (
        sys.argv[1] != os.environ.get("RUNNER_TEMP")
        or not private_root.is_absolute()
        or private_root.resolve(strict=True) != private_root
        or sys.argv[2] != "forge-installed-wheel-artifact"
        or Path.cwd() != root
    ):
        raise ValueError("workflow paths")
    publish(sys.argv[1], sys.argv[2], source_from_git(root))


if __name__ == "__main__":
    main()
