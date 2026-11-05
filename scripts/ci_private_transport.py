"""Publish only reviewed public identities while retaining the Linux CI validators.

The manifest is an independently reviewed, public input, never a collection output.
Unknown identities fail closed. Raw receipts, pytest output and JUnit stay local.
This module does not modify the original ci_test_shards command or validators.
"""

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from types import SimpleNamespace
import traceback

if __package__:
    from . import ci_test_shards as ci
else:
    import ci_test_shards as ci

MANIFEST = Path(__file__).with_name("ci_public_test_identities.json")
MANIFEST_SCHEMA = "forge-public-test-identities-v1"
SUMMARY_SCHEMA = "forge-public-linux-ci-summary-v1"
COLLECTION_PHASES = ("full", "ownership", "cgroup", "local")
JOBS = ("plan", "process-contracts", "service-free", "linux-tests")
STATUSES = ("success", "failure", "cancelled", "skipped")
MAX_BYTES = 128 * 1024 * 1024
MAX_NODES = 250_000
MAX_REPORTS = 1_000_000
MAX_NODE = 262_144
MAX_STRING = 16_384
MAX_REASON = MAX_STRING
MAX_DEPTH = 16
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff]")
JSON_STRUCTURE_RE = re.compile(r'[{}\[\]"]')
JSON_STRING_RE = re.compile(r'"(?:[^"\\]++|\\.)*+"', re.DOTALL)
JSON_ESCAPE_RE = re.compile(r"\\(?:u[0-9a-fA-F]{4}|.)", re.DOTALL)
MODULE_RE = re.compile(r"tests/(?:[A-Za-z0-9_][A-Za-z0-9_.-]*/)*[A-Za-z0-9_][A-Za-z0-9_.-]*\.py\Z")
RECEIPT_KEYS = {
    "sha",
    "phase",
    "exitstatus",
    "collected",
    "collection_skips",
    "collection_skip_reasons",
    "collection_errors",
    "reports",
    "subtests",
}
REPORT_KEYS = {"nodeid", "when", "outcome", "skip_reason", "wasxfail"}
POLICY_KEYS = {"module_seconds", "runtime_skips", "collection_skips", "collection_skip_reasons"}
ERROR = "STOP: Private CI validation failed."
DIAGNOSTIC_SCHEMA = "forge-public-linux-ci-diagnostics-v1"
DIAGNOSTIC_STAGES = (
    "initialization",
    "input_validation",
    "collection",
    "execution",
    "receipt_validation",
    "coverage_validation",
    "publication",
    "aggregate",
)
DIAGNOSTIC_OPERATIONS = ("plan", *ci.PHASES, "verify")
DIAGNOSTIC_FAILURES = ("NONZERO_EXIT", "INTERRUPTED", "REJECTED", "INTERNAL_ERROR")
DIAGNOSTIC_KEYS = {"operation", "stage", "state", "code"}
INVOCATION_RE = re.compile(r"[0-9a-f]{32}\Z")
FAILURE_STATE = "failure-state.json"
FAILURE_STAGES = (
    "prepare",
    "pytest",
    "receipt_read",
    "receipt_validation",
    "outcome_validation",
    "projection",
    "coverage_validation",
    "log_close",
    "publication",
    "output_validation",
)


def failure_context(sha, phase):
    """Bind private diagnostics to one GitHub job invocation, never public output."""
    _sha(sha)
    _require(phase in ci.PHASES)
    run = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    _require(re.fullmatch(r"[1-9][0-9]{0,19}", run) is not None)
    _require(re.fullmatch(r"[1-9][0-9]{0,9}", attempt) is not None)
    return {"sha": sha, "phase": phase, "run": run, "attempt": attempt}


class TransportError(ValueError):
    """A deliberately non-diagnostic error suitable for public output."""


def _require(condition):
    if not condition:
        raise TransportError(ERROR)


def _object(value, required=None, optional=()):
    _require(type(value) is dict and len(value) <= MAX_REPORTS)
    if required is not None:
        _require(set(required) <= set(value) <= set(required) | set(optional))
    return value


def _string(value, limit=MAX_STRING):
    _require(type(value) is str and len(value) <= limit)
    _require(CONTROL_RE.search(value) is None)
    return value


def _list(value, limit=MAX_NODES):
    _require(type(value) is list and len(value) <= limit)
    return value


def _strings(value, *, sorted_required=False, limit=MAX_NODES, string_limit=MAX_STRING):
    values = _list(value, limit)
    for item in values:
        _string(item, string_limit)
    _require(len(set(values)) == len(values))
    if sorted_required:
        _require(values == sorted(values))
    return values


def _sha(value):
    _require(type(value) is str and SHA_RE.fullmatch(value) is not None)
    return value


def _module(value):
    _require(type(value) is str and len(value) <= 1024 and ".." not in value)
    _require(MODULE_RE.fullmatch(value) is not None)
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _invalid_constant(_value):
    raise TransportError(ERROR)


def _check_encoded_string(text, start, end):
    size = end - start - 2
    if size <= MAX_NODE:
        return
    previous_end = -1
    previous_high = False
    for escape in JSON_ESCAPE_RE.finditer(text, start + 1, end - 1):
        width = escape.end() - escape.start()
        size -= width - 1
        code = int(text[escape.start() + 2 : escape.end()], 16) if width == 6 else -1
        if previous_high and escape.start() == previous_end and 0xDC00 <= code <= 0xDFFF:
            size -= 1
        previous_high = 0xD800 <= code <= 0xDBFF
        previous_end = escape.end()
    _require(size <= MAX_NODE)


def _decode_json(raw):
    _require(len(raw) <= MAX_BYTES)
    text = raw.decode("utf-8")
    # Bound nesting before json.loads, which otherwise reaches Python's recursion limit.
    depth = 0
    position = 0
    while True:
        token = JSON_STRUCTURE_RE.search(text, position)
        if token is None:
            break
        char = text[token.start()]
        if char == '"':
            # Match only at this opening quote, within a fixed token bound.
            # Never restart a failed match at escaped quotes inside a string.
            # An astral code point may use a 12-character surrogate pair.
            end = min(len(text), token.start() + 12 * MAX_NODE + 3)
            string = JSON_STRING_RE.match(text, token.start(), end)
            _require(string is not None)
            _require(string.end() - string.start() <= 12 * MAX_NODE + 2)
            _check_encoded_string(text, string.start(), string.end())
            position = string.end()
            continue
        if char in "[{":
            depth += 1
            _require(depth <= MAX_DEPTH)
        elif char in "]}":
            depth -= 1
            _require(depth >= 0)
        position = token.end()
    _require(depth == 0)
    value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is dict:
            _require(len(item) <= MAX_REPORTS)
            pending.extend(item.keys())
            pending.extend(item.values())
        elif type(item) is list:
            _require(len(item) <= MAX_REPORTS)
            pending.extend(item)
        elif type(item) is str:
            # Schema validation gives ordinary fields their smaller limits.
            _string(item, MAX_NODE)
        elif type(item) is float:
            _require(math.isfinite(item))
        else:
            _require(item is None or type(item) in (bool, int))
    return value


@contextmanager
def _directory(path, *, create=False, private=False):
    path = Path(path)
    if not path.is_absolute():
        path = Path.cwd() / path
    _require(".." not in path.parts)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        if private:
            os.fchmod(fd, 0o700)
        yield fd
    finally:
        os.close(fd)


def read_json(path, *, max_bytes=MAX_BYTES):
    """Read a bounded, regular file without following any path-component symlinks."""
    path = Path(path)
    with _directory(path.parent) as parent:
        fd = os.open(
            path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
        )
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_size <= max_bytes)
        chunks = []
        remaining = max_bytes + 1
        while remaining:
            block = os.read(fd, min(1024 * 1024, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(fd)
        _require(
            (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        )
        raw = b"".join(chunks)
        _require(len(raw) <= max_bytes)
        return _decode_json(raw)
    finally:
        os.close(fd)


def _write_json(path, value):
    path = Path(path)
    payload = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    _require(len(payload) <= MAX_BYTES)
    with _directory(path.parent, create=True) as parent:
        try:
            target = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _require(stat.S_ISREG(target.st_mode))
        temporary = ".ci-public-" + secrets.token_hex(16)
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def _clear_public(directory, names):
    with _directory(directory, create=True) as parent:
        for name in names:
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            _require(stat.S_ISREG(info.st_mode))
            os.unlink(name, dir_fd=parent)


def _prepare_private_outputs(directory, names):
    """Reserve raw writer targets without changing the test process's umask.

    The reviewed recorder and JUnit writer truncate these existing regular
    files, preserving their modes. The enclosing directory is private before
    any target is created. Empty reservations never count as valid receipts.
    """
    with _directory(directory, create=True, private=True) as parent:
        for name in names:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent,
            )
            try:
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)


@contextmanager
def _log(directory, name):
    with _directory(directory, create=True, private=True) as parent:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent,
        )
        try:
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
            os.fchmod(fd, 0o600)
            os.ftruncate(fd, 0)
        except BaseException:
            os.close(fd)
            raise
    stream = os.fdopen(fd, "w", encoding="utf-8")
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            yield stream
    except BaseException as primary:
        try:
            stream.close()
        except BaseException as close_error:  # noqa: BLE001 - cleanup cannot replace an active primary.
            # Keep cleanup evidence private without replacing the producer's
            # exception, exit code or chain. Never format either error here.
            try:
                state = BaseException.__dict__["__dict__"].__get__(primary)
                previous = dict.get(state, "_ci_log_close_errors", ())
                if type(previous) is not tuple:
                    previous = (previous,)
                dict.__setitem__(state, "_ci_log_close_errors", previous + (close_error,))
                note = "Private CI log close also failed; see _ci_log_close_errors."
                if not dict.__contains__(state, "__notes__"):
                    dict.__setitem__(state, "__notes__", [note])
                elif type(notes := dict.__getitem__(state, "__notes__")) is list:
                    notes.append(note)
            except BaseException:  # noqa: BLE001, S110 - metadata failure cannot replace the primary.
                pass
        raise
    else:
        stream.close()


def validate_manifest(data):
    _object(data, {"schema", "modules", "nodes"})
    _require(data["schema"] == MANIFEST_SCHEMA)
    modules = _strings(data["modules"], sorted_required=True)
    nodes = _strings(data["nodes"], sorted_required=True, string_limit=MAX_NODE)
    _require(bool(modules) and bool(nodes))
    for module in modules:
        _module(module)
    module_set = set(modules)
    for node in nodes:
        module, separator, suffix = node.partition("::")
        _require(separator == "::" and bool(suffix) and module in module_set)
    return data


def _identities(manifest):
    validate_manifest(manifest)
    return {
        node: node.partition("::")[0] + "::public-" + str(index)
        for index, node in enumerate(manifest["nodes"])
    }


def _validate_policy(policy, manifest):
    _object(policy, POLICY_KEYS, {"artifact_sha256", "source_run"})
    modules = set(manifest["modules"])
    nodes = set(manifest["nodes"])
    weights = _object(policy["module_seconds"])
    for module, weight in weights.items():
        _require(module in modules and type(weight) in (int, float))
        _require(math.isfinite(weight) and 0 <= weight <= 1_000_000_000)
    skips = _strings(policy["collection_skips"])
    _require(set(skips) <= modules)
    reasons = _object(policy["collection_skip_reasons"])
    _require(set(reasons) == set(skips))
    runtime = _object(policy["runtime_skips"])
    _require(set(runtime) <= nodes)
    for reason in [*reasons.values(), *runtime.values()]:
        _string(reason)
        _require(len(reason) <= MAX_REASON)
    if "artifact_sha256" in policy:
        _require(
            type(policy["artifact_sha256"]) is str
            and DIGEST_RE.fullmatch(policy["artifact_sha256"]) is not None
        )
    if "source_run" in policy:
        _require(type(policy["source_run"]) is int and 0 <= policy["source_run"] <= 2**63 - 1)
    return policy


def _reasons(policy):
    values = sorted(
        set(policy["collection_skip_reasons"].values()) | set(policy["runtime_skips"].values())
    )
    return {reason: "public-reason-" + str(index) for index, reason in enumerate(values)}


def project_policy(policy, manifest):
    identities = _identities(manifest)
    _validate_policy(policy, manifest)
    reasons = _reasons(policy)
    # Both digests bind already-public, reviewed source files, never runtime data.
    return {
        "module_seconds": {module: weight for module, weight in policy["module_seconds"].items()},
        "runtime_skips": {
            identities[node]: reasons[reason] for node, reason in policy["runtime_skips"].items()
        },
        "collection_skips": list(policy["collection_skips"]),
        "collection_skip_reasons": {
            module: reasons[reason] for module, reason in policy["collection_skip_reasons"].items()
        },
        "source_policy_sha256": ci.digest(policy),
        "public_manifest_sha256": ci.digest(manifest),
    }


def _validate_receipt(receipt, allowed_nodes, modules, policy):
    _object(receipt, RECEIPT_KEYS)
    _sha(receipt["sha"])
    _require(receipt["phase"] in (*COLLECTION_PHASES, *ci.PHASES))
    _require(type(receipt["exitstatus"]) is int and receipt["exitstatus"] == 0)
    _require(set(_strings(receipt["collected"], string_limit=MAX_NODE)) <= allowed_nodes)
    skips = _strings(receipt["collection_skips"])
    _require(set(skips) <= modules and set(skips) <= set(policy["collection_skips"]))
    reasons = _object(receipt["collection_skip_reasons"])
    _require(reasons == {module: policy["collection_skip_reasons"][module] for module in skips})
    _require(type(receipt["collection_errors"]) is list and not receipt["collection_errors"])
    for items in (receipt["reports"], receipt["subtests"]):
        for item in _list(items, MAX_REPORTS):
            _object(item, REPORT_KEYS)
            _require(type(item["nodeid"]) is str and item["nodeid"] in allowed_nodes)
            _require(item["when"] in ("setup", "call", "teardown"))
            _require(item["outcome"] in ("passed", "skipped"))
            _require(type(item["wasxfail"]) is bool and not item["wasxfail"])
            if item["outcome"] == "skipped":
                _require(type(item["skip_reason"]) is str and item["nodeid"] in policy["runtime_skips"])
                _require(item["skip_reason"] == policy["runtime_skips"][item["nodeid"]])
            else:
                _require(item["skip_reason"] is None)
    return receipt


def project_receipt(receipt, manifest, policy):
    identities = _identities(manifest)
    _validate_policy(policy, manifest)
    _validate_receipt(receipt, set(identities), set(manifest["modules"]), policy)
    ci.check_collection(receipt, receipt["sha"], receipt["phase"], policy)
    reasons = _reasons(policy)

    def report(item):
        return {
            "nodeid": identities[item["nodeid"]],
            "when": item["when"],
            "outcome": item["outcome"],
            "skip_reason": reasons[item["skip_reason"]] if item["outcome"] == "skipped" else None,
            "wasxfail": False,
        }

    return {
        "sha": receipt["sha"],
        "phase": receipt["phase"],
        "exitstatus": 0,
        "collected": [identities[node] for node in receipt["collected"]],
        "collection_skips": list(receipt["collection_skips"]),
        "collection_skip_reasons": {
            module: reasons[policy["collection_skip_reasons"][module]]
            for module in receipt["collection_skips"]
        },
        "collection_errors": [],
        "reports": [report(item) for item in receipt["reports"]],
        "subtests": [report(item) for item in receipt["subtests"]],
    }


def _validate_public_plan(plan, sha, projected_policy, manifest):
    _object(plan, {"sha", "full_nodes", "phases", "policy_sha256"})
    _sha(plan["sha"])
    _require(plan["sha"] == sha and plan["policy_sha256"] == ci.digest(projected_policy))
    nodes = set(_identities(manifest).values())
    modules = set(manifest["modules"])
    _require(set(_strings(plan["full_nodes"])) <= nodes)
    phases = _object(plan["phases"], set(ci.PHASES))
    for phase, part in phases.items():
        keys = {"nodes", "collection_skips"} | ({"modules"} if phase.startswith("shard-") else set())
        _object(part, keys)
        _require(set(_strings(part["nodes"])) <= nodes)
        _require(set(_strings(part["collection_skips"])) <= modules)
        if "modules" in part:
            _require(set(_strings(part["modules"])) <= modules)
    ci.validate_plan(plan, sha, projected_policy)
    return plan


@contextmanager
def _ci_io(reader, writer=None, runner=None):
    old_read, old_write, old_subprocess = ci.read, ci.write, ci.subprocess
    ci.read = reader
    if writer is not None:
        ci.write = writer
    if runner is not None:
        ci.subprocess = SimpleNamespace(run=runner)
    try:
        yield
    finally:
        ci.read, ci.write, ci.subprocess = old_read, old_write, old_subprocess


def _separate_directories(directory, public_directory):
    private, public = Path(directory).absolute(), Path(public_directory).absolute()
    _require(private != public and private not in public.parents and public not in private.parents)


def collect_plan(directory, public_directory, sha, policy, manifest, *, diagnostic=None):
    """Collect/validate privately before publishing the four reviewed projections."""
    directory, public_directory = Path(directory), Path(public_directory)
    _separate_directories(directory, public_directory)
    names = ["plan.json", *[f"collection-{phase}.json" for phase in COLLECTION_PHASES]]
    _clear_public(public_directory, names)
    _sha(sha)
    projected_policy = project_policy(policy, manifest)
    _existing_outputs(public_directory, None, projected_policy, manifest)
    _clear_public(directory, names)
    _prepare_private_outputs(directory, [f"collection-{phase}.json" for phase in COLLECTION_PHASES])
    with _log(directory, "collection.log") as log:
        _stage(diagnostic, "collection")

        def runner(*args, **kwargs):
            return subprocess.run(*args, stdout=log, stderr=subprocess.STDOUT, **kwargs)

        with _ci_io(read_json, _write_json, runner):
            ci.collect_plan(directory, sha, policy)
        _stage(diagnostic, "receipt_validation")
        raw_plan = read_json(directory / "plan.json")
        ci.validate_plan(raw_plan, sha, policy)
        collections = {
            phase: read_json(directory / f"collection-{phase}.json") for phase in COLLECTION_PHASES
        }
        _require(ci.build_plan(collections, sha, policy) == raw_plan)
        for receipt in collections.values():
            _require(not receipt["reports"] and not receipt["subtests"])
        projected = {
            phase: project_receipt(receipt, manifest, policy) for phase, receipt in collections.items()
        }
        _stage(diagnostic, "coverage_validation")
        plan = ci.build_plan(projected, sha, projected_policy)
        _validate_public_plan(plan, sha, projected_policy, manifest)
    _stage(diagnostic, "publication")
    # No output is published until all collections and the entire plan validate.
    for phase, receipt in projected.items():
        _write_json(public_directory / f"collection-{phase}.json", receipt)
    _write_json(public_directory / "plan.json", plan)
    expected = {
        "plan.json": plan,
        **{f"collection-{phase}.json": receipt for phase, receipt in projected.items()},
    }
    found = set()
    for path in _output_files(public_directory):
        if path.name == "summary.json":
            _validate_summary(read_json(path))
        else:
            _require(path.name in expected and read_json(path) == expected[path.name])
            found.add(path.name)
    _require(found == set(expected))
    return plan


def run_phase(
    phase,
    plan,
    directory,
    public_directory,
    policy,
    manifest,
    *,
    diagnostic=None,
    failure_diagnostic=False,
):
    """Preserve producer failures; optional private diagnostics cannot replace them."""
    state = {"stage": "prepare", "returncode": None}
    if failure_diagnostic:
        _clear_public(directory, [FAILURE_STATE])
    try:
        options = {} if diagnostic is None else {"diagnostic": diagnostic}
        return _run_phase(phase, plan, directory, public_directory, policy, manifest, state, **options)
    except BaseException:
        if failure_diagnostic:
            try:
                context = failure_context(plan["sha"], phase)
                _write_json(Path(directory) / FAILURE_STATE, {**context, **state})
            except BaseException:  # noqa: BLE001, S110 - Diagnostics must preserve the original failure.
                pass
        raise


def _run_phase(phase, plan, directory, public_directory, policy, manifest, state, *, diagnostic=None):
    """Run the original pytest argv, then independently enforce public-plan coverage."""
    _require(phase in ci.PHASES)
    directory, public_directory = Path(directory), Path(public_directory)
    _separate_directories(directory, public_directory)
    _clear_public(public_directory, [f"receipt-{phase}.json"])
    projected_policy = project_policy(policy, manifest)
    _validate_public_plan(plan, plan["sha"], projected_policy, manifest)
    _existing_outputs(public_directory, plan, projected_policy, manifest)
    raw_names = [f"receipt-{phase}.json", phase + ".xml"]
    _clear_public(directory, raw_names)
    _prepare_private_outputs(directory, raw_names)
    args = ci.selection(phase)
    if phase.startswith("shard-"):
        args = [*plan["phases"][phase]["modules"], *args]
    report_path = directory / f"receipt-{phase}.json"
    command = ci.command_for_pytest(
        phase,
        plan["sha"],
        report_path,
        [*args, "--durations=20", f"--junitxml={directory / (phase + '.xml')}"],
    )
    with _log(directory, phase + ".log") as log:
        _stage(diagnostic, "execution")
        state["stage"] = "pytest"
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        state["returncode"] = result.returncode
        if result.returncode:
            raise SystemExit(result.returncode)
        _stage(diagnostic, "receipt_validation")
        state["stage"] = "receipt_read"
        raw = read_json(report_path)
        identities = _identities(manifest)
        state["stage"] = "receipt_validation"
        _validate_receipt(raw, set(identities), set(manifest["modules"]), policy)
        # This first check proves outcomes only, NOT coverage. The second check
        # compares against the separately downloaded collection plan.
        raw_expected = {
            "sha": plan["sha"],
            "phases": {phase: {"nodes": raw["collected"], "collection_skips": raw["collection_skips"]}},
        }
        state["stage"] = "outcome_validation"
        ci.validate_execution(raw, phase, raw_expected, policy)
        state["stage"] = "projection"
        projected = project_receipt(raw, manifest, policy)
        _stage(diagnostic, "coverage_validation")
        state["stage"] = "coverage_validation"
        ci.validate_execution(projected, phase, plan, projected_policy)
        state["stage"] = "log_close"
    _stage(diagnostic, "publication")
    state["stage"] = "publication"
    _write_json(public_directory / f"receipt-{phase}.json", projected)
    state["stage"] = "output_validation"
    _existing_outputs(public_directory, plan, projected_policy, manifest)
    _require(read_json(public_directory / f"receipt-{phase}.json") == projected)
    return projected


def _public_files(directory):
    pending = [Path(directory)]
    files = []
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        _require(visited <= 256)
        with _directory(current) as fd:
            with os.scandir(fd) as entries:
                for entry in entries:
                    _string(entry.name)
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(current / entry.name)
                    else:
                        _require(stat.S_ISREG(info.st_mode))
                        files.append(current / entry.name)
                    _require(len(files) + len(pending) <= 256)
    return files


def _validate_summary(data):
    _object(data, {"schema", "job", "status", "job_result", "raw_evidence"})
    _require(data["schema"] == SUMMARY_SCHEMA)
    _require(data["job"] in JOBS and data["job_result"] in STATUSES)
    _require(data["status"] == ("PASS" if data["job_result"] == "success" else "STOP"))
    _require(data["raw_evidence"] == "NOT_PUBLISHED")


def _output_files(directory):
    files = []
    with _directory(directory) as fd:
        with os.scandir(fd) as entries:
            for entry in entries:
                _require(stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode))
                files.append(Path(directory) / entry.name)
                _require(len(files) <= 256)
    return files


def _existing_outputs(directory, plan, projected_policy, manifest):
    nodes = set(_identities(manifest).values())
    modules = set(manifest["modules"])
    for path in _output_files(directory):
        if path.name == "summary.json":
            _validate_summary(read_json(path))
            continue
        _require(plan is not None and path.name in {f"receipt-{phase}.json" for phase in ci.PHASES})
        phase = path.name.removeprefix("receipt-").removesuffix(".json")
        receipt = _validate_receipt(read_json(path), nodes, modules, projected_policy)
        ci.validate_execution(receipt, phase, plan, projected_policy)


def verify(plan, directory, sha, policy, manifest, needs, *, diagnostic=None):
    """Retain original manifest reconstruction, seven receipts, and job-state checks."""
    _sha(sha)
    projected_policy = project_policy(policy, manifest)
    _validate_public_plan(plan, sha, projected_policy, manifest)
    _object(needs, {"plan", "process-contracts", "service-free"})
    for entry in needs.values():
        _object(entry, {"result"}, {"outputs"})
        _require(entry["result"] in STATUSES)
        if "outputs" in entry:
            _object(entry["outputs"], set())
    nodes = set(_identities(manifest).values())
    modules = set(manifest["modules"])

    validated = {}

    def reader(path):
        key = str(Path(path).absolute())
        if key not in validated:
            receipt = read_json(path)
            _validate_receipt(receipt, nodes, modules, projected_policy)
            if Path(path).name.startswith("collection-"):
                _require(not receipt["reports"] and not receipt["subtests"])
            validated[key] = receipt
        return validated[key]

    _stage(diagnostic, "receipt_validation")
    # Reject additional raw files and unknown schemas, including symlinked
    # directories. Summaries are validated as data but cannot authorize a pass.
    plan_count = 0
    collection_names = {f"collection-{phase}.json" for phase in COLLECTION_PHASES}
    receipt_names = {f"receipt-{phase}.json" for phase in ci.PHASES}
    for path in _public_files(directory):
        if path.name == "plan.json":
            plan_count += 1
            candidate = read_json(path)
            _validate_public_plan(candidate, sha, projected_policy, manifest)
            _require(candidate == plan)
        elif path.name == "summary.json":
            _validate_summary(read_json(path))
        elif path.name in collection_names | receipt_names:
            reader(path)
        else:
            raise TransportError(ERROR)
    _require(plan_count == 1)
    # The original aggregate performs the file count, manifest rebuild, node
    # coverage, outcome, skip, xfail, subtest and upstream-status checks.
    _stage(diagnostic, "aggregate")
    with _ci_io(reader):
        ci.aggregate(plan, Path(directory), sha, projected_policy, needs)


def _diagnostic_record(operation, stage, state, code):
    record = {"operation": operation, "stage": stage, "state": state, "code": code}
    _validate_diagnostic_record(record, operation)
    return record


def _validate_diagnostic_record(record, operation):
    _object(record, DIAGNOSTIC_KEYS)
    _require(operation in DIAGNOSTIC_OPERATIONS and record["operation"] == operation)
    state, stage, code = record["state"], record["stage"], record["code"]
    if state == "UNAVAILABLE":
        _require(stage == "unknown" and code == "NOT_RECORDED")
    else:
        _require(stage in DIAGNOSTIC_STAGES)
        allowed_stages = {"initialization", "input_validation"}
        if operation == "plan":
            allowed_stages |= {"collection", "receipt_validation", "coverage_validation", "publication"}
        elif operation == "verify":
            allowed_stages |= {"receipt_validation", "aggregate"}
        else:
            allowed_stages |= {"execution", "receipt_validation", "coverage_validation", "publication"}
        _require(stage in allowed_stages)
        if state == "INCOMPLETE":
            _require(code == "NO_TERMINAL_RECORD")
        elif state == "FAILED":
            _require(code in DIAGNOSTIC_FAILURES)
        else:
            _require(state == "COMPLETED" and code == "COMPLETED")
            _require(stage == ("aggregate" if operation == "verify" else "publication"))
    return record


class _Diagnostic:
    """Code-owned progress, never a receipt, root-cause claim or qualification gate."""

    def __init__(self, directory, operation, invocation):
        _require(operation in DIAGNOSTIC_OPERATIONS)
        _require(type(invocation) is str and INVOCATION_RE.fullmatch(invocation) is not None)
        self.invocation = invocation
        self.path = Path(directory) / (operation + ".json")
        self.operation = operation
        self.stage = "initialization"
        self.enter(self.stage)

    def enter(self, stage):
        self.stage = stage
        self.write("INCOMPLETE", "NO_TERMINAL_RECORD")

    def write(self, state, code):
        _write_json(
            self.path,
            {
                "invocation": self.invocation,
                "record": _diagnostic_record(self.operation, self.stage, state, code),
            },
        )

    def failed(self, error):
        if isinstance(error, SystemExit) and error.code in (None, 0):
            self.write("INCOMPLETE", "NO_TERMINAL_RECORD")
            return
        if isinstance(error, (SystemExit, subprocess.CalledProcessError)):
            code = "NONZERO_EXIT"
        elif isinstance(error, KeyboardInterrupt):
            code = "INTERRUPTED"
        elif isinstance(error, ValueError):
            code = "REJECTED"
        else:
            code = "INTERNAL_ERROR"
        self.write("FAILED", code)


def _stage(diagnostic, stage):
    if diagnostic is not None:
        diagnostic.enter(stage)


def _diagnostic_operations(job, phase):
    _require(job in JOBS)
    if job == "service-free":
        _require(phase in ("shard-0", "shard-1", "shard-2", "shard-3"))
        return (phase,)
    _require(phase is None)
    return {
        "plan": ("plan",),
        "process-contracts": ("ownership", "cgroup", "local"),
        "linux-tests": ("verify",),
    }[job]


def project_diagnostics(directory, public_directory, job, status, phase=None, *, invocations=None):
    """Reconstruct only fixed codes; missing state is unknown, never NOT_RUN.

    A surviving INCOMPLETE record locates the last entered boundary, not the
    reason for interruption. Cancellation/VM retirement may prevent this step
    entirely. Neither these records nor job-local tracebacks survive by promise.
    """
    _separate_directories(directory, public_directory)
    _clear_public(public_directory, ["diagnostics.json"])
    operations = _diagnostic_operations(job, phase)
    _object(invocations, set(operations))
    for invocation in invocations.values():
        _require(type(invocation) is str)
        _require(invocation == "" or INVOCATION_RE.fullmatch(invocation) is not None)
    _require(status in STATUSES)
    expected = {op + suffix for op in operations for suffix in (".json", ".log")}
    try:
        files = _output_files(directory)
    except FileNotFoundError:
        files = []
    _require(all(path.name in expected for path in files))
    for path in files:
        with _directory(path.parent) as parent:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    present = {path.name for path in files}
    records = []
    for operation in operations:
        record = _diagnostic_record(operation, "unknown", "UNAVAILABLE", "NOT_RECORDED")
        if operation + ".json" in present:
            checkpoint = read_json(Path(directory) / (operation + ".json"), max_bytes=1024)
            _object(checkpoint, {"invocation", "record"})
            invocation = checkpoint["invocation"]
            _require(type(invocation) is str and INVOCATION_RE.fullmatch(invocation) is not None)
            candidate = _validate_diagnostic_record(checkpoint["record"], operation)
            # The expected generation comes from the fixed producer's workflow
            # output, never from this potentially stale checkpoint itself.
            if invocations[operation] and invocation == invocations[operation]:
                record = candidate
        # Never copy arbitrary input keys, strings, exception text or log bytes.
        records.append(_diagnostic_record(operation, record["stage"], record["state"], record["code"]))
    value = {
        "schema": DIAGNOSTIC_SCHEMA,
        "job": job,
        "job_result": status,
        "operations": records,
        "raw_evidence": "NOT_PUBLISHED",
    }
    _require(len(json.dumps(value, allow_nan=False)) <= 1024)
    _write_json(Path(public_directory) / "diagnostics.json", value)
    _require({p.name for p in _output_files(public_directory)} == {"diagnostics.json"})
    _require(read_json(Path(public_directory) / "diagnostics.json") == value)
    return value


def _clear_operation_outputs(args):
    if args.command in ("plan", "run"):
        _separate_directories(args.directory, args.public_directory)
        names = (
            ["plan.json", *[f"collection-{phase}.json" for phase in COLLECTION_PHASES]]
            if args.command == "plan"
            else [f"receipt-{args.phase}.json"]
        )
        # Remove this producer's outputs before reading any untrusted input.
        _clear_public(args.public_directory, names)


def _execute(args, diagnostic=None):
    _clear_operation_outputs(args)
    _stage(diagnostic, "input_validation")
    _sha(args.sha)
    manifest = validate_manifest(read_json(args.manifest))
    policy = _validate_policy(read_json(ci.POLICY), manifest)
    options = {} if diagnostic is None else {"diagnostic": diagnostic}
    if args.command == "plan":
        collect_plan(args.directory, args.public_directory, args.sha, policy, manifest, **options)
    else:
        plan = read_json(args.plan)
        projected_policy = project_policy(policy, manifest)
        _validate_public_plan(plan, args.sha, projected_policy, manifest)
        if args.command == "run":
            if args.failure_diagnostic:
                options["failure_diagnostic"] = True
            run_phase(
                args.phase, plan, args.directory, args.public_directory, policy, manifest, **options
            )
        else:
            needs = _decode_json(os.environ["CI_NEEDS_JSON"].encode("utf-8"))
            verify(plan, args.directory, args.sha, policy, manifest, needs, **options)


def _execute_diagnosed(args):
    directory = args.diagnostic_directory
    operation = args.phase if args.command == "run" else args.command
    # Validate all overlap constraints before any creation or chmod. A run's
    # diagnostics may be inside its private evidence directory, never public.
    if args.command == "verify":
        _separate_directories(directory, args.directory)
    else:
        _separate_directories(directory, args.public_directory)
    if args.command in ("run", "verify"):
        _separate_directories(directory, args.plan.parent)
    _separate_directories(directory, args.manifest.parent)
    _separate_directories(directory, ci.POLICY.parent)
    private, diagnostic_path = Path(args.directory).absolute(), Path(directory).absolute()
    _require(private != diagnostic_path and diagnostic_path not in private.parents)
    _require(type(args.diagnostic_invocation) is str)
    _require(INVOCATION_RE.fullmatch(args.diagnostic_invocation) is not None)
    try:
        _clear_public(directory, [operation + ".json"])
    except BaseException:
        try:
            _clear_operation_outputs(args)
        except Exception:  # noqa: BLE001, S110 - keep the original setup failure.
            pass
        raise
    _clear_operation_outputs(args)
    original_error = None
    original_traceback = None
    original_cause = None
    original_context = None
    original_suppression = False
    diagnostic = None
    try:
        with _log(directory, operation + ".log"):
            diagnostic = _Diagnostic(directory, operation, args.diagnostic_invocation)
            try:
                _execute(args, diagnostic)
                diagnostic.write("COMPLETED", "COMPLETED")
            except BaseException as error:
                original_error = error
                original_traceback = BaseException.__traceback__.__get__(error)
                original_cause = BaseException.__cause__.__get__(error)
                original_context = BaseException.__context__.__get__(error)
                original_suppression = BaseException.__suppress_context__.__get__(error)
                # No raw fallback, including logging/close failures. The outer
                # handler prints only ERROR. Preserve the original child's rc.
                try:
                    traceback.print_exc()
                except Exception:  # noqa: BLE001, S110 - no public log fallback.
                    pass
                try:
                    diagnostic.failed(error)
                except Exception:  # noqa: BLE001, S110 - preserve original failure.
                    pass
                raise
    except BaseException as error:
        if original_error is not None:
            if error is original_error:
                raise
            # Keep the displaced diagnostic error reachable, including any
            # private close evidence attached while its log was unwinding.
            # Never format it or replace the producer's existing chain.
            try:
                state = BaseException.__dict__["__dict__"].__get__(original_error)
                previous = dict.get(state, "_ci_diagnostic_errors", ())
                if type(previous) is not tuple:
                    previous = (previous,)
                dict.__setitem__(state, "_ci_diagnostic_errors", previous + (error,))
                note = "Private CI diagnostics also failed; see _ci_diagnostic_errors."
                if not dict.__contains__(state, "__notes__"):
                    dict.__setitem__(state, "__notes__", [note])
                elif type(notes := dict.__getitem__(state, "__notes__")) is list:
                    notes.append(note)
            except BaseException:  # noqa: BLE001, S110 - metadata cannot replace the primary.
                pass
            try:
                raise BaseException.with_traceback(original_error, original_traceback)
            finally:
                # Diagnostic cleanup may fail while the producer is unwinding.
                # Preserve its existing chain without invoking subclass hooks.
                BaseException.__cause__.__set__(original_error, original_cause)
                BaseException.__context__.__set__(original_error, original_context)
                BaseException.__suppress_context__.__set__(original_error, original_suppression)
        # A failure closing the private log is also a failed operation, even
        # after the workload returned. Never leave a fresh success if writable.
        if diagnostic is not None:
            try:
                diagnostic.failed(error)
            except Exception:  # noqa: BLE001, S110 - no raw fallback on I/O failure.
                pass
        raise


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise TransportError(ERROR)


def _parser():
    parser = _Parser(prog="ci_private_transport")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for command in ("plan", "run", "verify"):
        sub = commands.add_parser(command)
        sub.add_argument("--sha", required=True)
        sub.add_argument("--directory", type=Path, required=True)
        sub.add_argument("--manifest", type=Path, default=MANIFEST)
        sub.add_argument("--diagnostic-directory", type=Path)
        sub.add_argument("--diagnostic-invocation")
        if command in ("plan", "run"):
            sub.add_argument("--public-directory", type=Path, required=True)
        if command in ("run", "verify"):
            sub.add_argument("--plan", type=Path, required=True)
        if command == "run":
            sub.add_argument("--phase", choices=ci.PHASES, required=True)
            sub.add_argument("--failure-diagnostic", action="store_true")
    summary = commands.add_parser("summary")
    summary.add_argument("--job", choices=JOBS, required=True)
    summary.add_argument("--status", choices=STATUSES, required=True)
    summary.add_argument("--public-directory", type=Path, required=True)
    diagnostics = commands.add_parser("diagnostics")
    diagnostics.add_argument("--job", choices=JOBS, required=True)
    diagnostics.add_argument("--status", choices=STATUSES, required=True)
    diagnostics.add_argument("--phase", choices=ci.PHASES)
    diagnostics.add_argument("--invocation", action="append", default=[])
    diagnostics.add_argument("--directory", type=Path, required=True)
    diagnostics.add_argument("--public-directory", type=Path, required=True)
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        if args.command == "summary":
            _clear_public(args.public_directory, ["summary.json"])
            _write_json(
                args.public_directory / "summary.json",
                {
                    "schema": SUMMARY_SCHEMA,
                    "job": args.job,
                    "status": "PASS" if args.status == "success" else "STOP",
                    "job_result": args.status,
                    "raw_evidence": "NOT_PUBLISHED",
                },
            )
            return 0
        if args.command == "run" and args.failure_diagnostic:
            # Clear stale diagnostics before reading any untrusted plan/manifest.
            _clear_public(args.directory, [FAILURE_STATE])
        if args.command == "diagnostics":
            invocations = {}
            for entry in args.invocation:
                operation, separator, invocation = entry.partition("=")
                _require(separator == "=" and operation not in invocations)
                invocations[operation] = invocation
            project_diagnostics(
                args.directory,
                args.public_directory,
                args.job,
                args.status,
                args.phase,
                invocations=invocations,
            )
        elif args.diagnostic_directory is not None:
            _execute_diagnosed(args)
        else:
            _execute(args)
        return 0
    except subprocess.CalledProcessError as error:
        print(ERROR, file=sys.stderr)
        return error.returncode
    except SystemExit as error:
        if error.code in (None, 0):
            return 0
        print(ERROR, file=sys.stderr)
        return error.code if type(error.code) is int else 1
    except KeyboardInterrupt:
        print(ERROR, file=sys.stderr)
        return 130
    except Exception:  # noqa: BLE001 - Never publish raw exception text or arguments.
        print(ERROR, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
