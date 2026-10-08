"""Bounded read-only admission for one reviewed ephemeral-runner qualification.

The publisher authenticates the complete manifest/helpers before importing this
module. A runner observation, a collector STOP record, or a caller-supplied
receipt is never an approval. The offline attachment certificate is not shipped
as an online AppArmor-expression interpreter.

Python admission consumes the finite independently reviewed provider/pip/editable
classification in the source-bound .github/qualification-python.json. Legacy
schema-1 scaffolding remains closed. There is no "accept current", generic
missing-RECORD exemption, duplicate deduplication, or unhashed-bytecode rule.

Admission schema (under the launch manifest's ``admission`` member):
  schema_version: 1
  reviewed_inventory_sha256, reviewed_executable_manifest_sha256: fixed below
  host_projection_sha256, executables_sha256, system_tools_sha256,
  collection_utilities_sha256, natural_guards_sha256: reviewed SHA-256 values
  loader_libraries: exact file identities OR the fixed setup-python-provider class
  python: {schema_version: 2, policy_sha256: fixed reviewed class-policy digest,
           manifest_sha256: exact checked-in classification file digest}

File identity: {path, canonical, sha256, bytes, mode, uid, gid, symlinks}.
Fresh fixed-install receipts, every pending non-wrapper RECORD row and each
finite generated-cache/source pair remain mandatory. Every actual cache byte
is bound for this attempt. The separate verify_python_context(receipt, record)
transition must pass after load, before final_source_recheck can succeed.

Only prepare performs discovery. Later methods re-read a bounded exact file set
and freshly observe caller, guards, includes and kernel inventory. No parser,
probe, install, network, policy load, replace or unload is performed by Gate.
"""
from __future__ import annotations

import base64
import copy
import csv
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import time
from typing import Any

from . import facts, launch, probes

INVENTORY_SHA256 = "f92fbbd668859e0b2bce4cc4ebecab30db93a07bac082f40e6eeb7135c6c4c33"
EXECUTABLE_MANIFEST_SHA256 = "658039da05e2f09e873f1a1442bb68ac285f82b4c59196d16ed3553376fe187d"
INCLUDE_SHA256 = "1e4710f11c1d7d0fd63c49e225d00e23e691d6d0a2b285e3074c6607d2ad2fad"
FEATURES_SHA256 = "ca6d62da14ed52bcf639eff823bf10e8b35eda891850c9a3f6bb0bd4f8c2b3e5"
PARSER_CONF_SHA256 = "f8586f1c3bfd7abac6a182c1b45d22a8397c0db5a737b54bc8f9fc48da53d5bd"
POSTINST_SHA256 = "0244d28af22b9ae17deedbf204528c43a0dc557c80af5469cf57db9948f7b427"
PROFILE_MEMBER = "apparmor-profiles/usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
TOOLCACHE = "/opt/hostedtoolcache/Python/3.12.14/x64"
PROVIDER_LIBRARY_PATHS = [TOOLCACHE + "/lib/libpython3.12.so", TOOLCACHE + "/lib/libpython3.12.so.1.0"]
CONTEXTS = {"host": TOOLCACHE + "/bin/python", "system": "/usr/bin/python3", "payload": "/usr/bin/python3"}
ARCHIVES = {
    "apparmor-profiles_4.0.1really4.0.1-0ubuntu0.24.04.8_all.deb": facts.VENDOR_PACKAGE_SHA256,
    "apparmor_4.0.1really4.0.1-0ubuntu0.24.04.8_amd64.deb": "190fa2ae7b76a52982bd796fbd25a067f7c42b8ec75c3797bfa67b694bf43297",
    "bubblewrap_0.9.0-1ubuntu0.3_amd64.deb": "2461f1beee9cb04c8942739fe1a2b37e7b7c2a3d518f0779dc75f9245baa3094",
}
GENERATED = {
    "tunables/home.d/ubuntu": (337, "e1b3a24d2ffdcf2b02f8726941f3c6c32dee3c9222553959a335147494d74a2d"),
    "tunables/xdg-user-dirs.d/site.local": (730, "d9705b1ab368e32f4cce94f2242c6947b8624985b9c6de2ab4ee01b398dc4703"),
}
ABSENT_OPTIONAL = sorted([
    "local/bwrap-userns-restrict", "local/unpriv_bwrap", "tunables/alias.d", "tunables/etc.d",
    "tunables/global.d", "tunables/kernelvars.d", "tunables/proc.d", "tunables/run.d",
    "tunables/share.d", "tunables/system.d",
])
DIGEST_FIELDS = {
    "host_projection_sha256", "executables_sha256", "system_tools_sha256",
    "collection_utilities_sha256", "natural_guards_sha256",
}
BINDING_FIELDS = {"nonce", "control_sha", "source_sha256", "run_id", "run_attempt", "job", "boot_id"}
IDENTITY_FIELDS = {"path", "canonical", "sha256", "bytes", "mode", "uid", "gid", "symlinks"}
SANITIZED_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C", "HOME": "/nonexistent"}
PYTHON_POLICY_SHA256 = "3121264396f108425f6138b5029ee14c5d56858d1d08cccf1d6b388caed220f4"
PYTHON_MANIFEST_PATH = ".github/qualification-python.json"
PIP_WHEEL_SHA256 = "aa11702524a12e6e2ab98f3322cb3fb7e6df7f4615e3d1d334029fbb87d2a627"
PYTHON_FILE_FIELDS = IDENTITY_FIELDS - {"symlinks"}
MAX_MANIFEST = launch.MAX_JSON
MAX_FILES = 20000
MAX_BYTES = 2 * 1024 * 1024 * 1024
# Deliberately empty. Insertion requires actual independently reviewed code/data.
REVIEWED_PYTHON_CLASSIFICATIONS: frozenset[str] = frozenset()


class AdmissionError(RuntimeError):
    """Missing, ambiguous, changed or unreviewed input requires terminal STOP."""


def need(condition: bool, message: str) -> None:
    if not condition:
        raise AdmissionError(message)


def keys(value: Any, expected: set[str], label: str) -> dict:
    need(type(value) is dict and set(value) == expected, "invalid " + label + " fields")
    return value


def sha(value: Any, label: str) -> str:
    need(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None
         and value != "0" * 64, "invalid " + label + " digest")
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def canonical(value: Any) -> bytes:
    """Only JSON primitives; bool/int equality must never authorize a mutation."""
    def check(item: Any, depth: int = 0) -> None:
        need(depth <= 24, "JSON nesting bound exceeded")
        if type(item) is dict:
            need(len(item) <= MAX_FILES and all(type(k) is str for k in item), "invalid JSON object")
            for child in item.values():
                check(child, depth + 1)
        elif type(item) is list:
            need(len(item) <= MAX_FILES, "JSON array bound exceeded")
            for child in item:
                check(child, depth + 1)
        else:
            need(item is None or type(item) in {str, int, bool}, "non-JSON admission value")
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def absolute(value: Any, label: str) -> str:
    need(type(value) is str and 0 < len(value) <= 4096 and value.startswith("/")
         and str(Path(value)) == value and not any(part in {".", ".."} for part in value.split("/"))
         and not any(c in value for c in "*?[]{}\x00\n\r"), "invalid " + label + " path")
    return value


def validate_file_identity(value: Any) -> dict:
    keys(value, IDENTITY_FIELDS, "file identity")
    for key in ("path", "canonical"):
        absolute(value[key], key)
    sha(value["sha256"], "file")
    for key in ("bytes", "mode", "uid", "gid"):
        need(type(value[key]) is int and value[key] >= 0, "invalid file " + key)
    need(value["bytes"] <= 256 * 1024 * 1024 and value["mode"] <= 0o7777, "file identity bound exceeded")
    need(type(value["symlinks"]) is list and len(value["symlinks"]) <= 48, "invalid symlink chain")
    seen = set()
    for entry in value["symlinks"]:
        keys(entry, {"path", "target"}, "symlink")
        absolute(entry["path"], "symlink")
        need(entry["path"] not in seen and type(entry["target"]) is str and bool(entry["target"])
             and "\x00" not in entry["target"], "invalid symlink target")
        seen.add(entry["path"])
    return value


def validate_manifest(document: dict) -> dict:
    launch.validate_manifest(document)
    raw = canonical(document)
    need(len(raw) <= MAX_MANIFEST, "admission manifest too large")
    rule = keys(document["admission"], DIGEST_FIELDS | {
        "schema_version", "reviewed_inventory_sha256", "reviewed_executable_manifest_sha256",
        "loader_libraries", "python",
    }, "admission")
    need(type(rule["schema_version"]) is int and rule["schema_version"] == 1, "unsupported admission schema")
    need(rule["reviewed_inventory_sha256"] == INVENTORY_SHA256, "unreviewed attachment inventory")
    need(rule["reviewed_executable_manifest_sha256"] == EXECUTABLE_MANIFEST_SHA256,
         "unreviewed executable/path closure")
    for field in DIGEST_FIELDS:
        sha(rule[field], field)
    need(rule["executables_sha256"] == EXECUTABLE_MANIFEST_SHA256,
         "executable identities differ from the attachment review closure")
    libraries = rule["loader_libraries"]
    if type(libraries) is dict:
        keys(libraries, {"class", "paths"}, "provider library class")
        need(libraries["class"] == "setup-python-provider" and libraries["paths"] == PROVIDER_LIBRARY_PATHS,
             "unreviewed provider library class/paths")
    else:
        need(type(libraries) is list and 0 < len(libraries) <= 128, "missing finite loader library identities")
        paths = []
        for entry in libraries:
            validate_file_identity(entry)
            need(entry["path"].startswith(TOOLCACHE + "/lib/")
                 and entry["canonical"].startswith(TOOLCACHE + "/lib/"), "loader library escaped reviewed root")
            need(not entry["mode"] & 0o6022, "privileged or writable loader library")
            paths.append(entry["path"])
        need(paths == sorted(set(paths)), "duplicate or unordered loader library identities")
    python = rule["python"]
    need(type(python) is dict and type(python.get("schema_version")) is int, "invalid Python policy schema")
    if python["schema_version"] == 2:
        keys(python, {"schema_version", "policy_sha256", "manifest_sha256"}, "Python policy")
        need(python["policy_sha256"] == PYTHON_POLICY_SHA256, "unreviewed Python class policy")
        sha(python["manifest_sha256"], "Python manifest")
    else:
        # Older scaffolding remains recognizable but cannot ever admit a run.
        keys(python, {"schema_version", "classification_sha256", "contexts"}, "Python policy")
        need(python["schema_version"] == 1, "unsupported Python policy schema")
        sha(python["classification_sha256"], "Python classification")
        contexts = keys(python["contexts"], set(CONTEXTS), "Python contexts")
        for context, executable in CONTEXTS.items():
            entry = keys(contexts[context], {"interpreter", "inventory_sha256", "files_sha256"}, "Python context")
            need(entry["interpreter"] == executable, "wrong " + context + " interpreter")
            sha(entry["inventory_sha256"], "Python inventory")
            sha(entry["files_sha256"], "Python files")
    return rule


def require_python_classification(policy: dict) -> None:
    if policy.get("schema_version") == 2:
        keys(policy, {"schema_version", "policy_sha256", "manifest_sha256"}, "Python policy")
        need(policy["policy_sha256"] == PYTHON_POLICY_SHA256, "unreviewed Python class policy")
        sha(policy["manifest_sha256"], "Python manifest")
        return
    need(policy["classification_sha256"] in REVIEWED_PYTHON_CLASSIFICATIONS,
         "Python classification is absent or not independently reviewed; admission remains STOP")
    # A digest entry alone must never turn a future incomplete implementation on.
    raise AdmissionError("reviewed Python classification validator is not implemented")


def observation(callback, *, resolved_error: str | None = None) -> dict:
    """Recover only a complete observation for ONE exact individually resolved objection."""
    try:
        result = callback()
    except facts.FactError as exc:
        need(resolved_error is not None and str(exc) == resolved_error and type(exc.observations) is dict,
             "unresolved collection error: " + str(exc))
        result = exc.observations
    need(type(result) is dict, "missing collection observation")
    return result


def validate_host(value: dict, rule: dict, binding: dict) -> dict:
    keys(value, {"identity", "uname", "os_release", "boot_id", "caller", "apparmor_enabled",
                 "userns_restriction", "cgroup", "cgroup_controllers", "loader_environment", "python_roots"}, "host")
    identity = keys(value["identity"], {
        "GITHUB_REPOSITORY", "GITHUB_REPOSITORY_ID", "GITHUB_SHA", "GITHUB_WORKFLOW_SHA", "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_EVENT_NAME", "RUNNER_OS", "RUNNER_ARCH",
        "RUNNER_ENVIRONMENT", "ImageOS", "ImageVersion",
    }, "host identity")
    expected = {"GITHUB_SHA": binding["control_sha"], "GITHUB_WORKFLOW_SHA": binding["control_sha"],
                "GITHUB_RUN_ID": str(binding["run_id"]), "GITHUB_RUN_ATTEMPT": str(binding["run_attempt"]),
                "GITHUB_JOB": binding["job"], "GITHUB_EVENT_NAME": "push", "RUNNER_OS": "Linux",
                "RUNNER_ARCH": "X64", "RUNNER_ENVIRONMENT": "github-hosted", "ImageOS": "ubuntu24",
                "ImageVersion": "20261004.327.1"}
    need(all(identity.get(k) == v for k, v in expected.items()), "host/run/image identity mismatch")
    need(value["boot_id"] == binding["boot_id"], "boot identity changed")
    need(value["apparmor_enabled"] == "Y" and value["userns_restriction"] == "1", "AppArmor restriction changed")
    uname = value["uname"]
    need(type(uname) is list and len(uname) == 5 and uname[0] == "Linux"
         and uname[2] == "6.17.0-1022-azure" and uname[4] == "x86_64", "unreviewed kernel")
    need(value["loader_environment"] == {"LD_PRELOAD": None, "LD_AUDIT": None, "LD_LIBRARY_PATH": TOOLCACHE + "/lib"},
         "unreviewed dynamic-loader environment")
    need(value["python_roots"] == {"pythonLocation": TOOLCACHE, "Python_ROOT_DIR": TOOLCACHE,
                                  "Python3_ROOT_DIR": TOOLCACHE, "RUNNER_TOOL_CACHE": "/opt/hostedtoolcache"},
         "Python roots changed")
    caller = keys(value["caller"], {"pid", "ppid", "label", "status", "groups", "namespaces"}, "caller")
    need(caller["label"] == "unconfined", "caller is not unconfined")
    status = keys(caller["status"], {"Uid", "Gid", "CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb", "NoNewPrivs", "Seccomp"}, "caller status")
    for key, expected_id in (("Uid", os.getuid()), ("Gid", os.getgid())):
        need(expected_id > 0 and type(status[key]) is str
             and status[key].split() == [str(expected_id)] * 4, "caller identity mismatch")
    need(os.getuid() == os.geteuid() and os.getgid() == os.getegid(), "effective caller identity mismatch")
    for field in ("CapInh", "CapPrm", "CapEff", "CapAmb"):
        need(status[field] == "0000000000000000", "caller has capability grants")
    cgroup = keys(value["cgroup"], {"path", "kind", "exists", "matches", "mode", "uid", "gid"}, "cgroup")
    root = f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service"
    need(cgroup["path"] == root and cgroup["kind"] == "isdir" and cgroup["exists"] is True
         and cgroup["matches"] is True and cgroup["uid"] == os.getuid() and cgroup["gid"] == os.getgid(),
         "unowned or absent delegated cgroup")
    # Remove only enumerated fresh-run identifiers. Everything else stays byte-bound.
    projection = copy.deepcopy(value)
    projection.pop("boot_id")
    projection["uname"][1] = None
    projection["identity"] = {k: v for k, v in identity.items() if not k.startswith("GITHUB_")}
    for field in ("pid", "ppid", "namespaces"):
        projection["caller"].pop(field)
    need(digest(projection) == rule["host_projection_sha256"], "reviewed host projection changed")
    return projection


def validate_kernel(value: dict, *, before: dict | None = None, compiled: dict | None = None) -> dict:
    semantic = value.get("semantic")
    keys(semantic, {"schema", "scope", "namespaces", "profiles"}, "kernel semantic inventory")
    need(type(semantic["schema"]) is int and semantic["schema"] == 1, "invalid kernel schema")
    need(semantic["scope"] == {"ns_level": "0", "ns_name": "root", "stacked": "no", "ns_stacked": "no"},
         "non-root or stacked AppArmor namespace")
    need(value.get("scope") == semantic["scope"], "kernel scope disagreement")
    need(digest(semantic) == value.get("semantic_sha256"), "kernel semantic digest is not computed from inventory")
    # Reconstruct identities and listing independently; no self-reported absence.
    reconstructed = facts.semantic_inventory(semantic["namespaces"], semantic["profiles"], preserve_opaque=True)
    reconstructed["scope"] = semantic["scope"]
    need(canonical(reconstructed) == canonical(semantic), "noncanonical kernel semantic inventory")
    expected_listing = sorted([
        {"qualified_name": (f":{p['namespace']}://" if p["namespace"] else "") + p["name"], "mode": p["mode"]}
        for p in semantic["profiles"]
    ], key=lambda p: p["qualified_name"])
    need(canonical(value.get("loaded_profiles")) == canonical(expected_listing), "kernel listing disagreement")
    reserved = [p for p in semantic["profiles"] if p["name"].split("//")[0] in {"bwrap", "unpriv_bwrap"}]
    if before is None:
        need(not reserved and value.get("conflicting_names") == [], "reserved profile already exists")
        need(digest(semantic) == INVENTORY_SHA256, "kernel inventory lacks the exact independent review")
    else:
        need(len(reserved) == 2 and {(p["namespace"], p["name"], p["mode"]) for p in reserved}
             == {( "", "bwrap", "enforce"), ("", "unpriv_bwrap", "enforce")},
             "add did not create exactly two enforcing root profiles")
        keys(compiled, {"sha256", "bytes"}, "Gate-owned compiled policy")
        sha(compiled["sha256"], "compiled policy")
        need(type(compiled["bytes"]) is int and 0 < compiled["bytes"] <= 1024 * 1024,
             "invalid compiled policy length")
        for profile in reserved:
            need(profile["metadata"].get("raw_data") == compiled,
                 "new profile raw policy differs from authenticated no-load compilation")
        # No display-string/DFA interpretation: both profiles must expose the
        # exact complete load blob already compiled from the pinned vendor file.
        prior = copy.deepcopy(semantic)
        prior["profiles"] = [p for p in prior["profiles"] if p not in reserved]
        need(canonical(prior) == canonical(before), "prior policy or namespaces changed during add")
    return semantic


def validate_policy(value: dict, reader, vendor: Path) -> dict:
    keys(value, {"filesystem_inventory", "include_closure", "include_closure_sha256", "absent_optional",
                 "forbidden_overrides", "parser_conf", "vendor_comparison", "unresolved_include_mismatches"}, "policy inputs")
    closure = value["include_closure"]
    need(type(closure) is dict and digest(closure) == INCLUDE_SHA256
         and value["include_closure_sha256"] == INCLUDE_SHA256, "effective include closure changed")
    need(value["forbidden_overrides"] == [] and value["absent_optional"] == ABSENT_OPTIONAL,
         "local/disable/force-complain or optional policy input changed")
    need(value["unresolved_include_mismatches"] == sorted(GENERATED)
         and value["vendor_comparison"] == "STOP: include mismatches require review", "unreviewed include mismatch")
    conf = keys(value["parser_conf"], {"sha256", "text"}, "host parser configuration")
    need(type(conf["text"]) is str and hashlib.sha256(conf["text"].encode()).hexdigest() == PARSER_CONF_SHA256
         and conf["sha256"] == PARSER_CONF_SHA256, "host parser configuration changed")
    need(all(not line.strip() or line.lstrip().startswith("#") for line in conf["text"].splitlines()),
         "active host parser configuration")
    for relative, (size, expected) in GENERATED.items():
        entry = closure[relative]
        need(entry["sha256"] == expected and entry["bytes"] == size and entry["includes"] == []
             and entry["matches_vendor_package"] is False, "generated include identity changed")
        metadata = entry["metadata"]
        need(metadata == {"type": "f", "target": "", "mode": 0o644, "uid": 0, "gid": 0, "size": size},
             "generated include metadata changed")
        raw = reader.read(facts.PROFILE_ROOT + "/" + relative)
        need(len(raw) == size and hashlib.sha256(raw).hexdigest() == expected, "generated include bytes changed")
        need(all(not line.strip() or line.lstrip().startswith(b"#") for line in raw.splitlines()),
             "generated default is not comments-only")
    for relative, entry in closure.items():
        if entry["type"] == "file" and relative not in GENERATED:
            need(entry["matches_vendor_package"] is True, "non-generated include differs from package")
    postinst = reader.read("/var/lib/dpkg/info/apparmor.postinst")
    need(hashlib.sha256(postinst).hexdigest() == POSTINST_SHA256, "generated-default package generator changed")
    profile = file_identity(vendor / PROFILE_MEMBER)
    need(profile["canonical"] == str(vendor / PROFILE_MEMBER) and profile["sha256"] == facts.VENDOR_PROFILE_SHA256
         and profile["bytes"] == 1936 and not profile["symlinks"], "vendor profile/member changed")
    need(sorted(p.name for p in vendor.glob("*.deb")) == sorted(ARCHIVES), "vendor archive set changed")
    archives = {}
    for filename, expected in ARCHIVES.items():
        identity = file_identity(vendor / filename)
        need(identity["canonical"] == str(vendor / filename) and identity["sha256"] == expected
             and not identity["symlinks"], "unauthenticated vendor archive")
        archives[filename] = identity
    return {"includes": closure, "parser_conf": conf, "profile": profile, "archives": archives,
            "postinst_sha256": POSTINST_SHA256}


def file_identity(path: Path) -> dict:
    absolute(str(path), "input")
    target = path.resolve(strict=True)
    info = target.stat()
    need(stat.S_ISREG(info.st_mode), "nonregular bound input")
    # read_regular bounds FIFOs/symlinks and hashing, and reads from offset zero.
    raw = launch.read_regular(target, limit=256 * 1024 * 1024)
    after = target.stat()
    need((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
         == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
         and len(raw) == info.st_size and path.resolve(strict=True) == target, "input changed during hashing")
    chain = [{"path": str(p), "target": os.readlink(p)} for p in (path, *path.parents) if p.is_symlink()]
    return {"path": str(path), "canonical": str(target), "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw), "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid,
            "symlinks": chain}


def activation_environment() -> dict:
    names = ("COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG", "SETUPTOOLS_USE_DISTUTILS")
    result = {name: os.environ.get(name) for name in names}
    need(all(result[name] in (None, "") for name in names[:2]), "unreviewed coverage startup activation")
    need(result[names[2]] in (None, "local"), "unreviewed distutils startup condition")
    return result


def provider_libraries() -> list[dict]:
    """The approved finite setup-python provider class; no directory wildcard."""
    result = [file_identity(Path(path)) for path in PROVIDER_LIBRARY_PATHS]
    target = PROVIDER_LIBRARY_PATHS[1]
    for identity in result:
        need(identity["canonical"] == target and identity["uid"] == os.getuid()
             and identity["gid"] == os.getgid() and not identity["mode"] & 0o6022,
             "unexpected setup-python library identity/privilege")
        try:
            capability = os.getxattr(identity["canonical"], "security.capability")
        except OSError as exc:
            need(exc.errno == errno.ENODATA, "unknown setup-python library file capabilities")
            capability = b""
        need(capability == b"", "privileged setup-python library")
    need(result[0]["symlinks"] == [{"path": PROVIDER_LIBRARY_PATHS[0], "target": Path(target).name}]
         and result[1]["symlinks"] == [], "unreviewed setup-python library symlink chain")
    return result


def without_base64(value: Any) -> Any:
    if type(value) is dict:
        return {k: without_base64(v) for k, v in value.items() if k != "base64"}
    if type(value) is list:
        return [without_base64(v) for v in value]
    return value


def python_context_projection(inventory: dict) -> dict:
    fields = {"base_prefix", "enable_user_site", "environment", "executable", "loaded_hooks",
              "module_resolution", "path", "prefix", "search_path_states", "site_paths",
              "startup_inputs", "sysconfig_paths", "version", "duplicate_distributions"}
    need(fields <= set(inventory), "missing Python context observations")
    result = {key: without_base64(inventory[key]) for key in fields}
    need(type(inventory.get("distributions")) is list, "missing Python distribution instances")
    result["distribution_order"] = [d["metadata_path"] for d in inventory["distributions"]]
    return result


def parse_record(raw: bytes, hash_format: str) -> list[list[str]]:
    need(hash_format in {"sha256-urlsafe-base64", "sha256-hex"}, "unreviewed RECORD hash format")
    need(len(raw) <= 4 * 1024 * 1024, "RECORD byte bound exceeded")
    try:
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8"), newline=""), strict=True))
    except (UnicodeError, csv.Error) as exc:
        raise AdmissionError("malformed RECORD") from exc
    need(0 < len(rows) <= MAX_FILES, "empty or oversized RECORD")
    seen = set()
    for fields in rows:
        need(len(fields) == 3, "malformed RECORD row")
        relative, encoded, size = fields
        need(relative and not relative.startswith("/") and "\\" not in relative
             and not any(ord(c) < 32 or ord(c) == 127 for c in relative)
             and all(part not in {"", "."} for part in relative.split("/"))
             and relative not in seen, "duplicate/escaped RECORD path")
        seen.add(relative)
        need(not size or re.fullmatch(r"[0-9]{1,20}", size) is not None, "invalid RECORD size")
        if encoded:
            record_hash(encoded, hash_format)
    return rows


def record_hash(encoded: str, hash_format: str) -> str:
    need(encoded.startswith("sha256="), "unsupported RECORD hash algorithm")
    value = encoded.removeprefix("sha256=")
    if hash_format == "sha256-hex":
        return sha(value, "provider RECORD")
    need(hash_format == "sha256-urlsafe-base64" and re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None,
         "invalid RECORD digest")
    try:
        raw = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except ValueError as exc:
        raise AdmissionError("invalid RECORD base64") from exc
    need(len(raw) == 32 and base64.urlsafe_b64encode(raw).decode().rstrip("=") == value, "noncanonical RECORD digest")
    return raw.hex()


def generated_cache_header(raw: bytes, source: dict, source_mtime: float) -> None:
    need(len(raw) >= 16 and raw[:4] == bytes.fromhex("cb0d0d0a"), "not a CPython 3.12 cache")
    flags, timestamp, size = struct.unpack("<III", raw[4:16])
    need(flags == 0 and timestamp == int(source_mtime) % (2**32)
         and size == source["bytes"] % (2**32), "cache format/source header mismatch")


def validate_install_receipt(value: dict, binding: dict, data: dict) -> None:
    keys(value, {"schema_version", "control_sha", "run_id", "run_attempt", "job", "steps", "pip_version", "wheel_sha256"}, "install receipt")
    need(type(value["schema_version"]) is int and value["schema_version"] == 1, "invalid install receipt schema")
    for field in ("control_sha", "run_id", "run_attempt", "job"):
        need(canonical(value[field]) == canonical(binding[field]), "stale install receipt " + field)
    need(value["pip_version"] == "26.2.1" and value["wheel_sha256"] == PIP_WHEEL_SHA256,
         "unreviewed installer identity")
    expected = data["install_receipts"]
    need(type(value["steps"]) is list and len(value["steps"]) == len(expected) == 2, "missing install completion")
    for actual, reference in zip(value["steps"], expected, strict=True):
        keys(actual, {"name", "argv", "exit_code", "completed"}, "install step")
        need(actual["name"] == reference["id"] and actual["argv"] == reference["argv"]
             and type(actual["exit_code"]) is int and actual["exit_code"] == 0 and actual["completed"] is True,
             "fixed installation did not complete successfully")


def python_directory_state(data: dict) -> dict:
    roots = sorted({p["path"] for context in data["contexts"].values()
                    for p in context["search_path_states"] if p["state"] == "directory"})
    result = {}
    for root in roots:
        entries = []
        for path in Path(root).iterdir():
            hook_names = {name + suffix for name in ("sitecustomize", "usercustomize")
                          for suffix in ("", ".py", ".pyc", ".cpython-312-x86_64-linux-gnu.so", ".abi3.so", ".so")}
            if path.name.endswith((".pth", ".dist-info", ".egg-info")) or path.name in hook_names:
                details = path.lstat()
                entries.append({"name": path.name, "mode": details.st_mode,
                                "uid": details.st_uid, "gid": details.st_gid,
                                "target": os.readlink(path) if path.is_symlink() else None})
        result[root] = sorted(entries, key=lambda item: item["name"])
    return result


def validate_initial_startup(data: dict, directories: dict) -> None:
    approved = {item["path"] for context in data["contexts"].values() for item in context["startup_inputs"]}
    approved.update(item["path"] for context in data["contexts"].values()
                    for item in context["loaded_hooks"].values() if item is not None)
    for root, entries in directories.items():
        for entry in entries:
            if entry["name"].endswith((".dist-info", ".egg-info")):
                continue
            need(str(Path(root) / entry["name"]) in approved,
                 "unapproved initial Python startup input: " + str(Path(root) / entry["name"]))


def bind_python_search_roots(inputs, data: dict) -> None:
    for context in data["contexts"].values():
        for state in context["search_path_states"]:
            if state["state"] == "absent":
                keys(state, {"path", "state"}, "absent Python search root")
                inputs.absent_path(state["path"])
            elif state["state"] == "file":
                keys(state, {"path", "state", "identity"}, "Python archive root")
                inputs.capture(state["path"], state["identity"])
            else:
                keys(state, {"path", "state"}, "Python directory root")
                need(state["state"] == "directory" and Path(state["path"]).is_dir(), "Python directory root changed")


def context_comparison_view(projection: dict, instances: dict) -> dict:
    result = copy.deepcopy(projection)
    order = projection["distribution_order"]
    need(len(order) == len(set(order)), "repeated identical metadata location in a host context")
    def rank(path):
        need(path in instances, "unclassified context instance")
        location = instances[path]["location"]
        need(location in projection["path"], "distribution outside ordered import roots")
        return projection["path"].index(location), path
    result["distribution_order"] = sorted(order, key=rank)
    groups = []
    for group in projection["duplicate_distributions"]:
        keys(group, {"normalized_name", "instances"}, "duplicate distribution group")
        need(all(type(index) is int and 0 <= index < len(order) for index in group["instances"]), "invalid duplicate index")
        groups.append({"normalized_name": group["normalized_name"], "instances": [order[index] for index in group["instances"]]})
    result["duplicate_distributions"] = groups
    return result


def validate_python_observation(inventory: dict, expected: dict, instances: dict) -> None:
    projection = python_context_projection(inventory)
    need(set(expected) == set(projection) | {"binary", "pytest_plugins", "observed_inventory_sha256", "observed_status"},
         "unknown or missing Python context schema")
    need(canonical(context_comparison_view(projection, instances))
         == canonical(context_comparison_view({key: expected[key] for key in projection}, instances)), "Python context/order/startup drift")
    need(expected["observed_status"] == "STOP", "missing original unapproved observation state")
    errors = []
    for index, actual in enumerate(inventory["distributions"]):
        reference = instances.get(actual.get("metadata_path"))
        need(reference is not None, "new Python distribution instance")
        for key in ("name", "normalized_name", "version", "location", "metadata_canonical", "pytest_entry_points"):
            need(canonical(actual.get(key)) == canonical(reference[key]), "distribution identity/plugin drift: " + key)
        need(actual.get("complete") is reference["observed_complete"]
             and actual.get("error") == reference["observed_error"], "unrecognized distribution inventory error")
        if reference["observed_error"] is not None:
            errors.append({"instance": index, "phase": "files", "error": reference["observed_error"]})
        need(without_base64(actual.get("metadata_inputs")) == reference["metadata_inputs"], "metadata observation changed")
        actual_record = actual.get("record")
        need(type(actual_record) is dict, "missing raw RECORD observation")
        record = reference["record"]
        for field in ("path", "state", "identity"):
            need(actual_record.get(field) == record.get(field), "RECORD metadata observation changed")
        rows = actual_record.get("rows")
        need(type(rows) is list and len(rows) == record["row_count"]
             and digest([r["fields"] for r in rows]) == record["rows_sha256"], "raw RECORD rows changed")
        states = {}
        for row_index, row in enumerate(rows):
            states.setdefault(row.get("state", "unvalidated"), []).append(row_index)
        need(states == record["observation_rows"], "unrecognized missing/pending RECORD observation")
    need(inventory.get("distribution_errors") == errors and bool(errors)
         and inventory.get("complete") is False and inventory.get("error") == "RuntimeError: Python distribution metadata errors require review",
         "unrecognized Python collector failure")
    expected_plugins = [{"distribution": d["metadata_path"], "entry_point": ep}
                        for d in inventory["distributions"] for ep in d["pytest_entry_points"]]
    need(expected_plugins == [{"distribution": p["distribution"], "entry_point": p["entry_point"]} for p in expected["pytest_plugins"]],
         "plugin entry-point order changed")


class _PythonInputs:
    """Finite manifest consumption, not an OS/package certification framework."""
    def __init__(self, data: dict):
        self.data = data
        self.files: dict[str, dict] = {}
        self.absent: set[str] = set()
        self.total = 0

    def capture(self, spelling: str, expected: dict | None = None) -> dict:
        normalized = os.path.normpath(spelling)
        need(Path(spelling).resolve(strict=True) == Path(normalized).resolve(strict=True), "ambiguous Python row path")
        identity = file_identity(Path(normalized))
        self.total += 0 if normalized in self.files else identity["bytes"]
        need(len(self.files) < MAX_FILES and self.total <= MAX_BYTES, "Python input bounds exceeded")
        if normalized in self.files:
            need(canonical(self.files[normalized]) == canonical(identity), "Python file changed during admission")
        self.files[normalized] = identity
        compact = {key: identity[key] for key in PYTHON_FILE_FIELDS}
        compact["path"] = spelling
        if expected is not None:
            keys(expected, PYTHON_FILE_FIELDS, "Python file identity")
            need(canonical(compact) == canonical(expected), "reviewed Python input changed: " + spelling)
        return compact

    def absent_path(self, path: str) -> None:
        try:
            Path(path).lstat()
        except (FileNotFoundError, NotADirectoryError):
            self.absent.add(path)
        else:
            raise AdmissionError("previously absent Python metadata appeared: " + path)

    def metadata(self, item: dict) -> None:
        state = item.get("state")
        if state == "observed":
            self.capture(item["path"], item["identity"])
        elif state == "absent":
            self.absent_path(item["path"])
        else:
            raise AdmissionError("unknown metadata state")

    def instance(self, metadata_path: str, entry: dict) -> None:
        keys(entry, {"cache_binding", "cache_source_pairs", "class", "install_receipt", "location", "metadata_canonical",
                     "metadata_inputs", "name", "normalized_name", "observed_complete", "observed_error", "pytest_entry_points",
                     "record", "record_hash_format", "stable_file_rows", "stable_files_scope", "stable_files_sha256", "unused_wrappers", "version"},
             "Python instance classification")
        provenance = entry["class"]
        need(provenance in {"provider", "current-job-pip", "frozen-editable"}, "unreviewed Python provenance")
        need(str(Path(metadata_path).resolve(strict=True)) == entry["metadata_canonical"], "metadata instance canonical path changed")
        if provenance == "frozen-editable":
            need(metadata_path in self.data["editable"]["instances"] and entry["normalized_name"] == "code-review-forge"
                 and entry["version"] == "2.9.0", "unreviewed editable-project exception")
        if provenance == "current-job-pip":
            receipt = next((r for r in self.data["install_receipts"] if r["id"] == entry["install_receipt"]), None)
            need(receipt is not None and {"normalized_name": entry["normalized_name"], "version": entry["version"]} in receipt["installed"],
                 "distribution not established by fixed successful install")
        for item in entry["metadata_inputs"]:
            self.metadata(item)
        record = entry["record"]
        if record["state"] != "observed":
            need(provenance in {"provider", "frozen-editable"} and record["state"] in {"absent", "unknown"}
                 and record["row_count"] == 0 and record["rows_sha256"] == digest([])
                 and not entry["cache_source_pairs"] and not entry["unused_wrappers"]
                 and entry["stable_file_rows"] == [] and entry["stable_files_sha256"] == digest([]),
                 "unreviewed missing RECORD exception")
            self.absent_path(record["path"])
            return
        self.capture(record["path"], record["identity"])
        raw = launch.read_regular(Path(record["identity"]["canonical"]), limit=4 * 1024 * 1024)
        need(hashlib.sha256(raw).hexdigest() == record["identity"]["sha256"], "RECORD changed while reading")
        hash_format = entry["record_hash_format"]
        if hash_format == "sha256-hex":
            need(provenance == "provider" and (entry["normalized_name"], entry["version"]) in {("mdurl", "0.1.2"), ("ptyprocess", "0.7.0")},
                 "unreviewed hexadecimal RECORD")
        rows = parse_record(raw, hash_format)
        need(len(rows) == record["row_count"] and digest(rows) == record["rows_sha256"], "RECORD row identity changed")
        wrappers = {w["path"]: w for w in entry["unused_wrappers"]}
        allowed_wrappers = {
            ("pytest", "9.1.1", "/home/runner/.local/lib/python3.12/site-packages"): {"../../bin/py.test", "../../bin/pytest"},
            ("pygments", "2.21.0", "/home/runner/.local/lib/python3.12/site-packages"): {"../../bin/pygmentize"},
            ("markdown-it-py", "3.0.0", "/usr/lib/python3/dist-packages"): {"../scripts/markdown-it"},
        }.get((entry["normalized_name"], entry["version"], entry["location"]), set())
        need(set(wrappers) == allowed_wrappers, "unreviewed missing-wrapper exception")
        pairs = {p[0]: p for p in entry["cache_source_pairs"]}
        need(len(pairs) == len(entry["cache_source_pairs"]), "duplicate cache classification")
        if pairs:
            cache = entry["cache_binding"]
            need(provenance == "current-job-pip" and cache["class"] == "pip-generated-cpython312"
                 and cache["interpreter"] == TOOLCACHE + "/bin/python3.12" and cache["install_receipt"] == entry["install_receipt"]
                 and cache["uid"] == 1001 and cache["gid"] == 1001, "unreviewed cache provenance")
        stable_rows = entry["stable_file_rows"]
        need(entry["stable_files_scope"] == "observed-subset" and type(stable_rows) is list
             and all(type(n) is int and 0 <= n < len(rows) for n in stable_rows)
             and stable_rows == sorted(set(stable_rows)), "invalid stable row subset")
        stable = {}
        seen_pairs = set()
        row_by_path = {row[0]: row for row in rows}
        for index, row in enumerate(rows):
            relative, encoded, encoded_size = row
            spelling = str(Path(entry["location"]) / relative)
            if relative in wrappers:
                wrapper = wrappers[relative]
                need(wrapper["class"] == "unused-console-wrapper" and wrapper["row"] == index and wrapper["fields"] == row,
                     "wrapper row identity changed")
                # Only these four unused rows may be absent. Preserve the
                # reference missing/pending annotation; capture fresh reality.
                try:
                    Path(spelling).lstat()
                except (FileNotFoundError, NotADirectoryError):
                    self.absent_path(os.path.normpath(spelling))
                else:
                    unused = self.capture(spelling)
                    need(encoded and unused["sha256"] == record_hash(encoded, hash_format)
                         and (not encoded_size or unused["bytes"] == int(encoded_size)), "changed unused console wrapper")
                continue
            if index in record["observation_rows"].get("absent_generated_bytecode", []):
                need(provenance == "provider" and relative.endswith(".pyc") and not encoded and relative not in pairs,
                     "unreviewed absent cache")
                self.absent_path(os.path.normpath(spelling))
                continue
            actual = self.capture(spelling)
            if spelling.startswith("/usr/"):
                need(actual["uid"] == 0 and actual["gid"] == 0 and not actual["mode"] & 0o6022,
                     "unexpected writable/privileged provider input")
            if encoded:
                need(actual["sha256"] == record_hash(encoded, hash_format), "RECORD content mismatch: " + spelling)
            if encoded_size:
                need(actual["bytes"] == int(encoded_size), "RECORD size mismatch: " + spelling)
            if relative in pairs:
                pair = pairs[relative]
                need(len(pair) == 5 and not encoded and not encoded_size and pair[3] in {"observed", "pending"}, "invalid generated cache row")
                source_relative = pair[1]
                source_path = Path(source_relative)
                expected_cache = str(source_path.parent / "__pycache__" / (source_path.stem + ".cpython-312.pyc"))
                need(source_path.suffix == ".py" and relative == expected_cache and source_relative in row_by_path,
                     "cache/source spelling changed")
                source = self.capture(str(Path(entry["location"]) / source_relative))
                need(source["sha256"] == pair[2] and record_hash(row_by_path[source_relative][1], hash_format) == pair[2],
                     "generated cache source is not byte-bound")
                need(actual["uid"] == entry["cache_binding"]["uid"] and actual["gid"] == entry["cache_binding"]["gid"]
                     and actual["mode"] == ((source["mode"] & 0o666) | 0o200)
                     and (pair[4] is None or actual["mode"] == pair[4]), "generated cache ownership/mode changed")
                header = launch.read_regular(Path(actual["canonical"]), limit=256 * 1024 * 1024)
                need(hashlib.sha256(header).hexdigest() == actual["sha256"], "cache changed during header check")
                generated_cache_header(header, source, Path(source["canonical"]).stat().st_mtime)
                seen_pairs.add(relative)
            elif not encoded:
                known = next((m.get("identity") for m in entry["metadata_inputs"] if m["path"] == spelling and m["state"] == "observed"), None)
                if spelling == record["path"]:
                    known = record["identity"]
                need(known is not None or index in stable_rows or provenance == "provider",
                     "unclassified unhashed runtime input")
                if known:
                    need(canonical(actual) == canonical(known), "unhashed metadata identity changed")
            if index in stable_rows:
                need(relative not in pairs, "generated cache leaked into stable projection")
                stable[index] = actual
        need(set(pairs) == seen_pairs and set(stable) == set(stable_rows), "incomplete row validation")
        need(digest([stable[n] for n in stable_rows]) == entry["stable_files_sha256"], "stable installed input bytes/modes changed")


def payload_backing_path(path: str, payload: dict) -> tuple[str, tuple[str, str] | None]:
    absolute(path, "payload input")
    for host, inner in [payload["source_bind"], *payload["extra_binds"]]:
        if path == inner or path.startswith(inner + "/"):
            return host + path[len(inner):], (host, inner)
    need(path.startswith(("/usr/", "/etc/")), "unbound payload input path")
    return path, None


def payload_file(inputs: _PythonInputs, value: dict, payload: dict, *, pending_provider: bool = False) -> dict:
    host_path, alias = payload_backing_path(value["path"], payload)
    actual = inputs.capture(host_path)
    guest = dict(actual, path=value["path"])
    if alias and (actual["canonical"] == alias[0] or actual["canonical"].startswith(alias[0] + "/")):
        guest["canonical"] = alias[1] + actual["canonical"][len(alias[0]):]
    if pending_provider:
        need(host_path in {"/usr/lib/python3/dist-packages/packaging/__init__.py", "/usr/lib/python3/dist-packages/pygments/__init__.py"}
             and actual["uid"] == 0 and actual["gid"] == 0 and not actual["mode"] & 0o6022,
             "unreviewed provider module identity")
    for field in PYTHON_FILE_FIELDS:
        if field in value:
            need(canonical(guest[field]) == canonical(value[field]), "payload backing identity changed: " + field)
    return guest


def prepare_payload_projection(data: dict, inputs: _PythonInputs, repo: Path) -> dict:
    payload = data["payload"]
    need(payload["source_bind"] == [str(repo / "src"), "/opt/forge-src"]
         and payload["extra_binds"] == [["/usr/local/lib/python3.12/dist-packages", "/opt/extra-0"],
                                        ["/usr/lib/python3/dist-packages", "/opt/extra-1"],
                                        ["/home/runner/.local/lib/python3.12/site-packages", "/opt/extra-2"]],
         "corpus source/site mount contract changed")
    inputs.absent_path(payload["excluded_absent_site"])
    for item in payload["fixed_source_inputs"]:
        path = repo / item["path"]
        need(path.resolve(strict=True).is_relative_to(repo), "payload source binding escaped repository")
        observed = inputs.capture(str(path))
        need(observed["sha256"] == item["sha256"] and observed["bytes"] == item["bytes"], "fixed corpus source changed")
    scalar = {"version", "prefix", "base_prefix", "path", "site_paths", "enable_user_site", "environment"}
    result = {key: copy.deepcopy(payload[key]) for key in scalar}
    result.update(schema_version=1, kind="corpus-python-context", complete=True, executable=payload["interpreter"])
    result["startup_inputs"] = [payload_file(inputs, item, payload) for item in payload["startup_inputs"]]
    result["loaded_hooks"] = {name: payload_file(inputs, item, payload) if item else None
                              for name, item in payload["loaded_hooks"].items()}
    result["distributions"] = []
    for instance in payload["distribution_order"]:
        need(instance["backing_instance"] in data["instances"], "unclassified payload alias instance")
        entry = {key: copy.deepcopy(value) for key, value in instance.items() if key != "backing_instance"}
        for field in ("metadata_file", "entry_points_file"):
            if entry[field] is not None:
                entry[field] = payload_file(inputs, entry[field], payload)
        result["distributions"].append(entry)
    result["module_resolution"] = {}
    for name, value in payload["module_resolution"].items():
        need(value["binding"] in {"observed-backing-file", "pinned-record-pending-file-validation", "trusted-provider-pending-file-validation", "frozen-reviewed-source"},
             "unreviewed payload module class")
        entry = {key: copy.deepcopy(value[key]) for key in ("origin", "search_locations", "loader")}
        entry["file"] = payload_file(inputs, value.get("file", value.get("file_requirement")), payload,
                                     pending_provider=value["binding"] == "trusted-provider-pending-file-validation")
        result["module_resolution"][name] = entry
    return result


def translate_payload_owners(expected: dict, mapping: dict, caller: dict, process: dict) -> dict:
    keys(mapping, {"uid_map_raw", "gid_map_raw", "overflowuid_raw", "overflowgid_raw"}, "payload identity mapping")
    translated = {}
    for kind in ("uid", "gid"):
        raw = mapping[kind + "_map_raw"]
        need(type(raw) is str and len(raw) <= 4096, "invalid namespace identity map")
        lines = raw.splitlines()
        need(len(lines) == 1 and len(lines[0].split()) == 3, "non-singleton namespace identity mapping")
        fields = lines[0].split()
        need(all(re.fullmatch(r"[0-9]+", value) is not None for value in fields), "malformed namespace identity map")
        inside, outside, length = map(int, fields)
        need((inside, outside, length) == (process[kind], caller[kind], 1) and inside > 0,
             "payload identity map does not match ordinary runner")
        overflow = mapping["overflow" + kind + "_raw"]
        need(type(overflow) is str and re.fullmatch(r"[1-9][0-9]{0,9}\n", overflow) is not None
             and int(overflow) < 2**32, "invalid overflow owner identity")
        translated[kind] = (inside, outside, int(overflow))
    def visit(value):
        if type(value) is dict:
            result = {k: visit(v) for k, v in value.items()}
            if set(value) == PYTHON_FILE_FIELDS:
                for kind, (inside, outside, overflow) in translated.items():
                    result[kind] = inside if value[kind] == outside else overflow
            return result
        if type(value) is list:
            return [visit(v) for v in value]
        return value
    return visit(expected)


def payload_comparison_view(context: dict) -> dict:
    result = copy.deepcopy(context)
    distributions = result["distributions"]
    def key(item):
        need(item["location"] in result["path"], "payload distribution outside ordered roots")
        return result["path"].index(item["location"]), item["metadata_path"]
    # Preserve aliases/root rank; disregard only unrelated within-root listing order.
    result["distributions"] = sorted(distributions, key=key)
    need(len({item["metadata_path"] for item in distributions}) == len(distributions), "duplicate payload metadata path")
    return result


class _PrivateEvidence:
    """Preserve bounded raw observations under the controller's private directory."""
    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700)
        self.path, self.total, self.count = directory, 0, 0

    def save(self, source: str, data: bytes) -> dict:
        self.total += len(data)
        self.count += 1
        need(self.total <= facts.MAX_TOTAL and self.count <= facts.MAX_FILES, "raw evidence bound exceeded")
        checksum = hashlib.sha256(data).hexdigest()
        name = f"{self.count:05d}-{checksum}"
        launch.write_receipt(self.path / (name + ".json"), {"source": source, "sha256": checksum, "bytes": len(data)})
        fd = os.open(self.path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        return {"source": source, "sha256": checksum, "bytes": len(data), "artifact": str(self.path / name)}


class _Commands(facts.Commands):
    def run(self, argv, *, timeout=10, limit=facts.MAX_COMMAND, env=None, cwd=None):
        need(env is None or env == SANITIZED_ENV, "unexpected read-only command environment")
        # Only facts.Reader's finite read-only utilities are allowed here. No
        # callback-controlled argv, Python, parser, package manager or shell.
        tail = argv
        if argv[:len(facts.Reader.prefix())] == facts.Reader.prefix():
            tail = argv[len(facts.Reader.prefix()):]
        need(tail and tail[0] in {"/usr/bin/find", "/usr/bin/head", "/usr/bin/dd"}, "unapproved Gate command")
        return super().run(argv, timeout=timeout, limit=limit, env=dict(SANITIZED_ENV), cwd=cwd)


class Gate:
    """One-shot state machine retaining canonical immutable approval/receipt bytes."""
    def __init__(self, manifest: dict, repo: Path, vendor_dir: Path, evidence_dir: Path):
        validate_manifest(manifest)
        self._document_bytes = canonical(manifest)
        self.repo = Path(repo).absolute()
        self.vendor_dir = Path(vendor_dir).absolute()
        self.evidence_dir = Path(evidence_dir).absolute()
        for path in (self.repo, self.vendor_dir, self.evidence_dir):
            need(path.resolve(strict=True) == path and path.is_dir(), "noncanonical Gate directory")
        self._state = "CREATED"
        self._receipt_bytes: bytes | None = None
        self._binding_bytes: bytes | None = None
        self._baseline_bytes: bytes | None = None
        self._files_bytes: bytes | None = None
        self._kernel_bytes: bytes | None = None
        self._compiler_bytes: bytes | None = None
        self._python_data_bytes: bytes | None = None
        self._python_state_bytes: bytes | None = None
        self._payload_expected_bytes: bytes | None = None
        self._payload_overflow: dict | None = None
        self._prepared_ns: int | None = None
        self._stage = 0
        info = self.evidence_dir.lstat()
        self._evidence_identity = (info.st_dev, info.st_ino)
        self._check_evidence()

    def _document(self) -> dict:
        return json.loads(self._document_bytes)

    def _check_evidence(self) -> None:
        info = self.evidence_dir.lstat()
        need(self.evidence_dir.resolve(strict=True) == self.evidence_dir
             and (info.st_dev, info.st_ino) == self._evidence_identity
             and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o700, "private evidence directory changed")
        config = self.evidence_dir / "parser.conf"
        details = config.lstat()
        need(stat.S_ISREG(details.st_mode) and details.st_uid == os.getuid()
             and details.st_nlink == 1 and details.st_size == 0 and stat.S_IMODE(details.st_mode) == 0o600
             and launch.read_regular(config, limit=1) == b"", "private parser configuration changed")

    def _reader(self, seconds: float):
        self._check_evidence()
        self._stage += 1
        sink = _PrivateEvidence(self.evidence_dir / f"gate-{self._stage:02d}")
        return facts.Reader(_Commands(sink, total_seconds=seconds), sink)

    def _binding(self) -> dict:
        document = self._document()
        native = launch._native_context(document["launch"], os.environ)
        boot = launch.read_regular(Path("/proc/sys/kernel/random/boot_id"), limit=64).decode("ascii").strip()
        need(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot) is not None, "invalid boot id")
        return {"nonce": document["launch"]["nonce"], "control_sha": native["sha"],
                "source_sha256": document["launch"]["source_sha256"], "run_id": native["run_id"],
                "run_attempt": native["run_attempt"], "job": native["job"], "boot_id": boot}

    def _source(self) -> None:
        document = self._document()
        observed = launch.inspect_checkout(self.repo, document, manifest_path=self.repo / launch.MANIFEST_PATH)
        expected = document["launch"]
        need(observed["head"] == json.loads(self._binding_bytes)["control_sha"], "source control commit changed")
        for field in ("source_sha256", "workflow_sha256", "helper_sha256"):
            need(canonical(observed[field]) == canonical(expected[field]), "source/helper/workflow drift: " + field)

    def _tools(self, reader, rule: dict) -> dict:
        utilities = facts.collect_collection_utilities()
        need(utilities["errors"] == [] and digest(utilities) == rule["collection_utilities_sha256"],
             "reviewed collection utility identity changed")
        # sudo is intentionally privileged. Exact review, NOT the unprivileged
        # executable predicate, authenticates it. timeout/journalctl stay ordinary.
        for path, entry in utilities["executables"].items():
            need(entry["uid"] == 0 and entry["gid"] == 0 and not entry["mode"] & 0o022
                 and entry["elf"] is True and entry["canonical"] == path, "unsafe collection utility")
            if path != "/usr/bin/sudo":
                need(not entry["mode"] & 0o6000 and entry["file_capabilities_hex"] == "", "privileged ordinary utility")
        executable = facts.collect_executables(reader)
        need(digest(executable) == rule["executables_sha256"], "finite executable identities changed")
        binaries = {"apparmor": facts.executable_identity("/usr/sbin/apparmor_parser", reader),
                    "bubblewrap": executable["executables"]["/usr/bin/bwrap"]}
        for package, entry in binaries.items():
            options = [self.vendor_dir / package / entry["canonical"].lstrip("/")]
            if entry["canonical"].startswith("/usr/"):
                options.append(self.vendor_dir / package / entry["canonical"][5:])
            matches = [p for p in options if p.is_file() and not p.is_symlink()]
            need(len(matches) == 1 and file_identity(matches[0])["sha256"] == entry["sha256"], "installed/vendor tool mismatch")
        features = {}
        root = facts.APPARMOR_ROOT + "/features"
        for path, entry in reader.tree(root).items():
            if entry["type"] == "d":
                continue
            need(entry["type"] == "f", "unsupported kernel feature input")
            raw = reader.read(path)
            features[path.removeprefix(root + "/")] = {"sha256": hashlib.sha256(raw).hexdigest(), "text": raw.decode("utf-8")}
        need(digest(features) == FEATURES_SHA256, "kernel feature/ABI drift")
        tools = {"binaries": binaries, "features": features}
        need(digest(tools) == rule["system_tools_sha256"], "reviewed compiler/tool inputs changed")
        libraries = rule["loader_libraries"]
        if type(libraries) is dict:
            libraries = provider_libraries()
        else:
            for expected in libraries:
                need(canonical(file_identity(Path(expected["path"]))) == canonical(expected), "loader library changed")
        return {"utilities": utilities, "executables": executable, "tools": tools, "libraries": libraries}

    def _observe(self, *, seconds: float, after: bool = False) -> dict:
        reader = self._reader(seconds)
        rule = self._document()["admission"]
        # Authenticate sudo/timeout before a Reader can execute either one.
        tools = self._tools(reader, rule)
        binding = self._binding()
        need(canonical(binding) == self._binding_bytes, "run/attempt/boot binding changed")
        host = observation(lambda: facts.collect_host(reader), resolved_error="unexpected dynamic-loader environment")
        repo = self._document()["launch"]["repository"]
        need(host["identity"]["GITHUB_REPOSITORY"] == repo["full_name"]
             and host["identity"]["GITHUB_REPOSITORY_ID"] == str(repo["id"]), "host repository changed")
        validate_host(host, rule, binding)
        policy = observation(lambda: facts.collect_policy_inputs(reader, self.vendor_dir), resolved_error=(
            "policy include differs from authenticated apparmor package: " + ", ".join(sorted(GENERATED))))
        policy = validate_policy(policy, reader, self.vendor_dir)
        kernel = observation(lambda: facts.collect_kernel_inventory(reader), resolved_error="opaque attachments require review")
        semantic = validate_kernel(kernel, before=json.loads(self._kernel_bytes) if after else None,
                                   compiled=self._compiled_policy() if after else None)
        guards = facts.collect_guards(self.repo, reader)
        need(guards["unexpectedly_eligible"] == [] and all(p["naturally_eligible"] is False for p in guards["adapters"].values())
             and set(guards["adapters"]) == set(facts.GUARDS)
             and digest(guards) == rule["natural_guards_sha256"], "natural adapter guards changed")
        need(time.monotonic() <= reader.commands.deadline, "admission observation deadline exceeded")
        # Retain same-process caller identity/namespaces between all stages.
        return {"host": host, "tools": tools, "policy": policy, "guards": guards, "kernel": semantic,
                "activation_environment": activation_environment()}

    def _compiled_policy(self, *, capture: bool = False) -> dict:
        path = self.evidence_dir / "compile.stdout"
        info = path.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
             and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= 1024 * 1024,
             "missing or nonprivate authenticated compiler output")
        identity = file_identity(path)
        need(identity["canonical"] == str(path) and not identity["symlinks"], "compiler output symlink")
        current = canonical(identity)
        if capture:
            need(self._compiler_bytes is None, "compiler binding is one-shot")
            self._compiler_bytes = current
        need(self._compiler_bytes is not None and current == self._compiler_bytes, "compiler output changed")
        return {"sha256": identity["sha256"], "bytes": identity["bytes"]}

    def _load_python_data(self) -> dict:
        policy = self._document()["admission"]["python"]
        require_python_classification(policy)
        need(policy["schema_version"] == 2, "Python manifest schema is not reviewed")
        raw = launch.read_regular(self.repo / PYTHON_MANIFEST_PATH, limit=8 * 1024 * 1024)
        need(hashlib.sha256(raw).hexdigest() == policy["manifest_sha256"], "reviewed Python manifest bytes changed")
        data = launch.parse_json(raw, limit=8 * 1024 * 1024)
        keys(data, {"conditions", "contexts", "editable", "evidence", "install_receipts", "installer", "instances",
                    "payload", "projection", "provider", "schema_version"}, "Python classification manifest")
        need(type(data["schema_version"]) is int and data["schema_version"] == 1
             and data["evidence"]["policy_sha256"] == PYTHON_POLICY_SHA256,
             "unreviewed Python classification schema/policy")
        need(data["provider"]["ImageVersion"] == "20261004.327.1" and data["provider"]["kernel"] == "6.17.0-1022-azure",
             "Python provider image/kernel changed")
        keys(data["contexts"], {"host", "system"}, "Python host contexts")
        need(set(data["instances"]) == {path for context in data["contexts"].values() for path in context["distribution_order"]},
             "extra or missing Python instance classification")
        need(data["editable"]["source_root"] == str(self.repo / "src"), "editable project root changed")
        need(data["installer"]["version"] == "26.2.1"
             and data["installer"]["wheel_install_source"]["sha256"] == PIP_WHEEL_SHA256,
             "unreviewed pip installer")
        self._python_data_bytes = canonical(data)
        return data

    def _prepare_python(self) -> list[dict]:
        data = self._load_python_data()
        inputs = _PythonInputs(data)
        inputs.capture(str(self.repo / PYTHON_MANIFEST_PATH))
        environment = activation_environment()
        # All named harness Python settings are rechecked without re-running Python.
        bind_python_search_roots(inputs, data)
        names = set(data["contexts"]["host"]["environment"]) | set(data["contexts"]["system"]["environment"])
        environment.update({key: os.environ.get(key) for key in names})
        for context in data["contexts"].values():
            need({key: environment[key] for key in context["environment"]} == context["environment"], "host Python environment changed")
            for identity in context["startup_inputs"]:
                inputs.capture(identity["path"], identity)
        installer = data["installer"]
        inputs.capture(installer["wheel_install_source"]["path"], installer["wheel_install_source"])
        receipt_path = self.vendor_dir / "install-receipt.json"
        details = receipt_path.lstat()
        need(stat.S_ISREG(details.st_mode) and details.st_uid == os.getuid() and details.st_nlink == 1
             and stat.S_IMODE(details.st_mode) == 0o600, "nonprivate successful-install receipt")
        receipt_raw = launch.read_regular(receipt_path, limit=65536)
        validate_install_receipt(launch.parse_json(receipt_raw), json.loads(self._binding_bytes), data)
        inputs.capture(str(receipt_path))
        directories = python_directory_state(data)
        validate_initial_startup(data, directories)
        for name in ("host", "system"):
            expected = data["contexts"][name]
            executable = expected["binary"]["requested"]
            need(executable == CONTEXTS[name], "Python context executable changed")
            actual_binary = facts.executable_identity(executable, None, require_root=(name == "system"))
            need(canonical(actual_binary) == canonical(expected["binary"]), "Python interpreter identity changed")
            inputs.capture(executable)
            reader = self._reader(180)
            commands = facts.Commands(reader.evidence, total_seconds=180)
            try:
                raw = commands.run([executable, "-c", facts.PYTHON_FACTS_SCRIPT], timeout=90, cwd=self.repo)
            except facts.FactError as exc:
                # This exact original collector fails after preserving all
                # instances; each error and every remaining row is checked below.
                need(str(exc) == executable + ": exit 1" and type(exc.stdout) is bytes,
                     "Python observation failed/timed out/truncated")
                raw = exc.stdout
            need(type(raw) is bytes and 0 < len(raw) <= facts.MAX_COMMAND, "missing Python observation bytes")
            try:
                inventory = json.loads(raw.decode("utf-8"), object_pairs_hook=launch._pairs,
                                       parse_constant=lambda value: need(False, "nonfinite Python observation"))
            except (ValueError, UnicodeError) as exc:
                raise AdmissionError("incomplete/contaminated Python observation") from exc
            canonical(inventory)
            validate_python_observation(inventory, expected, data["instances"])
            for plugin in expected["pytest_plugins"]:
                inputs.capture(plugin["file"]["path"], plugin["file"])
        for path, entry in data["instances"].items():
            inputs.instance(path, entry)
        self._payload_expected_bytes = canonical(prepare_payload_projection(data, inputs, self.repo))
        self._payload_overflow = {"overflow" + kind + "_raw": launch.read_regular(
            Path("/proc/sys/kernel/overflow" + kind), limit=32).decode("ascii") for kind in ("uid", "gid")}
        need(python_directory_state(data) == directories, "Python metadata/startup set changed during admission")
        self._python_state_bytes = canonical({"absent": sorted(inputs.absent), "directories": directories, "environment": environment})
        launch.write_receipt(self.evidence_dir / "python-admitted-inputs.json", {
            "schema_version": 1, "binding": json.loads(self._binding_bytes), "files": list(inputs.files.values()),
            "state": json.loads(self._python_state_bytes), "classification_manifest_sha256": self._document()["admission"]["python"]["manifest_sha256"],
        })
        return list(inputs.files.values())

    def _recheck_files(self) -> None:
        need(self._files_bytes is not None, "no Gate-owned Python file binding")
        files = json.loads(self._files_bytes)
        if self._python_state_bytes is not None:
            state = json.loads(self._python_state_bytes)
            need({key: os.environ.get(key) for key in state["environment"]} == state["environment"], "Python startup environment drift")
            need(python_directory_state(json.loads(self._python_data_bytes)) == state["directories"], "new Python metadata/startup input")
            for path in state["absent"]:
                try:
                    Path(path).lstat()
                except (FileNotFoundError, NotADirectoryError):
                    continue
                raise AdmissionError("previously absent Python input appeared: " + path)
        total = 0
        for expected in files:
            total += expected["bytes"]
            need(total <= MAX_BYTES, "Python recheck byte bound exceeded")
            need(canonical(file_identity(Path(expected["path"]))) == canonical(expected), "Python/startup/plugin byte drift")

    def _receipt(self, supplied: dict, state: str) -> None:
        need(self._state == state and self._receipt_bytes is not None, "invalid admission state/sequence")
        need(canonical(supplied) == self._receipt_bytes, "caller receipt differs from Gate-owned receipt")
        self._check_evidence()

    def prepare(self) -> dict:
        previous = self._state
        self._state = "STOP"
        need(previous == "CREATED", "prepare is one-shot")
        # Fail before any subprocess or security read when data is still unreviewed.
        require_python_classification(self._document()["admission"]["python"])
        self._binding_bytes = canonical(self._binding())
        self._source()
        self._load_python_data()
        # Authenticate image, loader, compiler and provider input classes before
        # starting either ordinary metadata-only interpreter observation.
        observed = self._observe(seconds=240)
        files = self._prepare_python()
        need(type(files) is list and 0 < len(files) <= MAX_FILES, "missing Python bound file set")
        for identity in files:
            validate_file_identity(identity)
        need(len({i["path"] for i in files}) == len(files), "duplicate Python bound file")
        self._files_bytes = canonical(files)
        self._recheck_files()
        self._kernel_bytes = canonical(observed.pop("kernel"))
        self._baseline_bytes = canonical(observed)
        self._source()
        receipt = {"schema_version": 1, "status": "PASS", "vendor_profile": str(self.vendor_dir / PROFILE_MEMBER),
                   "cgroup_root": observed["host"]["cgroup"]["path"], "binding": json.loads(self._binding_bytes)}
        self._receipt_bytes = canonical(receipt)
        self._prepared_ns = time.monotonic_ns()
        self._state = "PREPARED"
        return json.loads(self._receipt_bytes)

    def _recheck(self, receipt: dict, *, state: str, next_state: str, after: bool) -> dict:
        try:
            self._receipt(receipt, state)
            self._state = "STOP"
            started = time.monotonic()
            self._compiled_policy(capture=not after)
            self._source()
            self._recheck_files()
            observed = self._observe(seconds=max(0.001, 45 - (time.monotonic() - started)), after=after)
            observed.pop("kernel")
            need(canonical(observed) == self._baseline_bytes, "authenticated admission inputs changed")
            need(time.monotonic() - started <= 45, "admission recheck freshness bound exceeded")
            self._state = next_state
            return {"schema_version": 1, "status": "PASS", "binding": json.loads(self._binding_bytes)}
        except BaseException:
            self._state = "STOP"
            raise

    def recheck_before_load(self, receipt: dict) -> dict:
        return self._recheck(receipt, state="PREPARED", next_state="PRELOAD_CHECKED", after=False)

    def verify_after_load(self, receipt: dict) -> dict:
        return self._recheck(receipt, state="PRELOAD_CHECKED", next_state="ADDED", after=True)

    def _validate_payload_context(self, record: dict) -> None:
        probes.validate_python_context_record(record)
        keys(record, {"schema_version", "kind", "caller", "contract", "run_id", "started", "argv", "command", "cgroup_path",
                      "wrapper_pid", "limits", "gate_opened_monotonic_ns", "returncode", "context", "ended", "workspace_records", "cleanup"},
             "completed context probe")
        need(record["cleanup"] == {"cancelled": True, "start_complete": True, "complete": True, "error": None},
             "context probe cleanup was not positively completed")
        need(record["caller"]["uid"] == os.getuid() and record["caller"]["euid"] == os.geteuid()
             and record["caller"]["gid"] == os.getgid() and record["caller"]["egid"] == os.getegid(), "context caller identity changed")
        need(self._payload_expected_bytes is not None and self._payload_overflow is not None, "missing pre-bound payload context")
        archived = launch.parse_json(launch.read_regular(self.evidence_dir / "python-context-probe.json", limit=1024 * 1024), limit=1024 * 1024)
        need(canonical(archived) == canonical(record), "context receipt differs from direct private evidence")
        for point in ("started", "ended"):
            keys(record[point], {"utc_ns", "monotonic_ns"}, "context operation time")
            need(all(type(v) is int and v > 0 for v in record[point].values()), "invalid context time")
        need(self._prepared_ns is not None and self._prepared_ns <= record["started"]["monotonic_ns"]
             <= record["ended"]["monotonic_ns"] <= time.monotonic_ns()
             and record["ended"]["monotonic_ns"] - record["started"]["monotonic_ns"] <= 45 * 10**9,
             "stale or unbounded context receipt")
        need(re.fullmatch(r"corpus-context-[0-9a-f]{12}", record["run_id"]) is not None, "invalid context run identity")
        context = record["context"]
        expected = json.loads(self._payload_expected_bytes)
        mapping = context["identity_mapping"]
        need(all(mapping[key] == value for key, value in self._payload_overflow.items()), "payload overflow identity differs from host")
        expected = translate_payload_owners(expected, mapping, record["caller"], context["process"])
        expected["environment"]["FORGE_MUTATION_RUN_ID"] = record["run_id"]
        keys(context, set(expected) | {"identity_mapping", "process"}, "payload Python context")
        observed = {key: context[key] for key in expected}
        need(canonical(payload_comparison_view(observed)) == canonical(payload_comparison_view(expected)),
             "payload alias/order/module/plugin/startup context changed")
        # Relevant duplicate/plugin precedence is kept in actual root order.
        def precedence(value):
            groups = {}
            plugins = []
            for item in value["distributions"]:
                groups.setdefault(item["normalized_name"], []).append(item["metadata_path"])
                if item["pytest_entry_points"]:
                    plugins.append({"metadata_path": item["metadata_path"], "entry_points": item["pytest_entry_points"]})
            return {key: paths for key, paths in groups.items() if len(paths) > 1}, plugins
        need(canonical(precedence(observed)) == canonical(precedence(expected)), "payload duplicate/plugin precedence changed")

    def verify_python_context(self, receipt: dict, record: dict) -> dict:
        try:
            self._receipt(receipt, "ADDED")
            self._state = "STOP"
            self._validate_payload_context(record)
            self._recheck_files()
            self._state = "CONTEXT_CHECKED"
            return {"schema_version": 1, "status": "PASS", "binding": json.loads(self._binding_bytes)}
        except BaseException:
            self._state = "STOP"
            raise

    def final_source_recheck(self, receipt: dict) -> dict:
        return self._recheck(receipt, state="CONTEXT_CHECKED", next_state="FINAL", after=True)
