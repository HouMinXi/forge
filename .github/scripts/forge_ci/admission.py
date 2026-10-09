"""Late read-only admission against root-sealed setup, source and finite paths.

No loader, provider-Python inventory, historical image identity or cache class is
present. Privileged policy observations come only from the pre-action sealed
system observer. The candidate retains its mandatory production boundary tests.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

from . import facts, launch, payload, probes, setup_policy as setup


class AdmissionError(RuntimeError):
    """The setup seal, actual policy, source and bounded path contract must agree."""


def need(value, message):
    if not value:
        raise AdmissionError(message)


def source_rule(receipt):
    launch.validate_receipt(receipt)
    return {"schema_version": 2, "setup_spec_sha256": setup.SPEC_SHA256,
            "setup_module_sha256": receipt["source"]["helper_sha256"][".github/scripts/forge_ci/setup_policy.py"]}


CORPUS_SITE_PATHS = (
    "/usr/local/lib/python3.12/dist-packages",
    "/usr/lib/python3/dist-packages",
    "/home/runner/.local/lib/python3.12/site-packages",
)


def corpus_contract(repo: Path) -> dict:
    """The exact existing adapter env/mount/PATH calculation, without inventories."""
    from code_forge.mutation_engines.adapters import python_mutmut
    from code_forge.mutation_engines.adapters.base import ExecutionContext
    from code_forge.mutation_engines.isolate import SandboxSpec, Supervisor

    need(Path(python_mutmut.__file__).resolve() == repo / "src/code_forge/mutation_engines/adapters/python_mutmut.py",
         "corpus adapter not from bound source")
    need(all(Path(path).is_dir() and Path(path).resolve() == Path(path) for path in CORPUS_SITE_PATHS)
         and not os.path.lexists("/usr/lib/python3.12/dist-packages"), "corpus site path/bind presence changed")
    run_id = "corpus-contract"
    context = ExecutionContext(run_id=run_id, config_digest="c" * 64, execution_policy_digest="e" * 64,
        toolchain_fingerprint="py-test", cgroup_root="/unused", state_root="/unused/state", approved_python="/usr/bin/python3",
        memory_mb=256, pids=64, workspace_mb=64, process_headroom_mb=32, extra_python_paths=CORPUS_SITE_PATHS)
    expected_env = (
        ("PATH", "/opt/recorder:/opt/cargo:/opt/rustup/toolchains/1.88.0-x86_64-unknown-linux-gnu/bin:/opt/node-0:/usr/bin:/bin"),
        ("HOME", "/workspace"), ("FORGE_GO_JOURNAL", "/workspace/go-journal.jsonl"), ("FORGE_REAL_GO", "/opt/realgo/go"),
        ("GOROOT", "/usr/lib/go-1.22"), ("RUSTUP_HOME", "/opt/rustup"), ("CARGO_HOME", "/opt/cargo-home"),
        ("GOFLAGS", "-mod=mod"), ("GOPROXY", "off"), ("GO111MODULE", "on"),
        ("PYTHONPATH", "/opt/forge-src:/opt/extra-0:/opt/extra-1:/opt/extra-2"),
        ("FORGE_MUTATION_EVENTS_DIR", "/workspace/events"), ("FORGE_MUTATION_RUN_ID", run_id),
    )
    expected_binds = ((str(repo / "src"), "/opt/forge-src"),) + tuple((path, "/opt/extra-" + str(i)) for i, path in enumerate(CORPUS_SITE_PATHS))
    adapter = python_mutmut.MutmutAdapter()
    env, binds = adapter._sandbox_env(context), adapter._extra_binds(context)
    need(env == expected_env and binds == expected_binds, "corpus adapter environment/extra-bind contract changed")
    workspace = "/reviewed-corpus-workspace"
    command = ("/usr/bin/python3", "-m", "pytest")
    spec = SandboxSpec(run_id=run_id, command=command, cwd="/workspace", memory_mb=256, pids=64, workspace_mb=64,
                       env=env, workspace_host=workspace, extra_ro_binds=binds)
    need(spec.runtime_root is None and spec.extra_rw_binds == (), "new corpus executable mount")
    argv = probes.production_probe_argv()
    argv = argv[:argv.index("--chdir")] + ["--bind", workspace, "/workspace"]
    for host, inner in binds:
        argv.extend(("--ro-bind", host, inner))
    for key, value in env:
        argv.extend(("--setenv", key, value))
    argv.extend(("--chdir", "/workspace", "--", *command))
    need(Supervisor(spec, "/unused")._bwrap_argv() == argv, "corpus namespace/PATH/mount argv changed")
    return {"env": list(map(list, env)), "extra_ro_binds": list(map(list, binds)), "argv": argv,
            "memory_mb": 256, "pids": 64, "workspace_mb": 64, "cwd": "/workspace"}


def validate_observer(record, binding, rule):
    need(type(record) is dict and set(record) == {"schema_version", "status", "binding", "setup_receipt", "setup_receipt_sha256",
                                                "setup_policy_sha256", "observed", "live", "policy"}
         and type(record["schema_version"]) is int and record["schema_version"] == 1 and record["status"] == "PASS",
         "fixed root observer did not return complete PASS")
    need(setup.canonical(record["binding"]) == setup.canonical(binding), "setup run/attempt/source/boot mismatch")
    seal = record["setup_receipt"]
    need(type(seal) is dict and type(seal.get("schema_version")) is int and seal["schema_version"] == 1 and seal.get("status") == "PASS"
         and seal.get("load_attempted") is True and seal.get("positive_passed") is True
         and setup.canonical(seal.get("binding")) == setup.canonical(binding), "early setup was not a successful transition")
    need(seal.get("setup_module_sha256") == rule["setup_module_sha256"], "unreviewed early setup module")
    config = seal.get("config")
    setup.validate_config(config)
    setup.validate_binding(binding)
    need(type(record["live"]) is dict and record["live"].get("binding") == binding, "observer live binding mismatch")
    source = launch.read_regular(Path(setup.__file__), limit=256 * 1024)
    need(hashlib.sha256(source).hexdigest() == rule["setup_module_sha256"], "late setup helper changed")
    observer = setup.observer_source(source.decode(), config)
    need(seal.get("observer") == {"sha256": hashlib.sha256(observer).hexdigest(), "bytes": len(observer)},
         "sealed observer differs from fixed read-only code")
    need(record["setup_receipt_sha256"] == hashlib.sha256(setup.canonical(seal) + b"\n").hexdigest(), "seal digest mismatch")
    need(record["setup_policy_sha256"] == setup.digest(record["policy"])
         and setup.canonical(record["policy"]) == setup.canonical(seal.get("after")), "observer policy differs from sealed state")
    now = time.monotonic_ns()
    observed = record["observed"]
    need(type(observed) is dict and set(observed) == {"utc_ns", "monotonic_ns"}
         and all(type(x) is int and x > 0 for x in observed.values())
         and 0 <= now - observed["monotonic_ns"] <= 30 * 10**9, "stale root policy observation")
    need(seal.get("runner") == {"uid": os.getuid(), "gid": os.getgid()}, "sealed original runner changed")
    return seal


def observe_setup(binding, rule, tree_oid, evidence_dir, phase):
    """Run the sealed observer afresh; initial callers supply native identity only."""
    need(phase in {"initial", "prepare", "final"}, "unknown observer phase")
    evidence = Path(evidence_dir).resolve(strict=True)
    native = phase == "initial"
    argv = setup.observer_argv_native(binding) if native else setup.observer_argv(binding)
    result = payload.bounded_command(argv, 30, env=dict(setup.SYSTEM_ENV), limit=setup.MAX_JSON)
    need(type(result) is dict, "invalid root observer command result")
    prefix = "setup-observer-" + phase
    streams = {}
    for name in ("stdout", "stderr"):
        encoded = result.get(name + "_hex")
        need(type(encoded) is str and len(encoded) <= 2 * setup.MAX_JSON, "invalid observer diagnostic bound")
        try:
            raw = bytes.fromhex(encoded)
        except ValueError as exc:
            raise AdmissionError("invalid observer diagnostic bytes") from exc
        need(raw.hex() == encoded, "noncanonical observer diagnostic bytes")
        streams[name] = raw
    def preserve(name):
        fd = os.open(evidence / (prefix + "." + name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(streams[name])
            handle.flush()
            os.fsync(handle.fileno())
    preserve("stderr")
    diagnostic = {name: result.get(name) for name in ("argv", "wrapper_pid", "returncode", "error", "started", "ended")}
    diagnostic["streams"] = {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                                    "file": prefix + "." + name, "on_failure_only": name == "stdout"}
                             for name, raw in streams.items()}
    launch.write_receipt(evidence / (prefix + ".json"), diagnostic)
    try:
        need(not result.get("error") and type(result.get("returncode")) is int
             and result["returncode"] == 0 and result.get("argv") == argv
             and result.get("stderr") == "" and result.get("stderr_hex") == "", "root observer failed or emitted diagnostics")
        try:
            raw = payload.command_stdout_bytes(result, setup.MAX_JSON)
        except payload.ProbeError as exc:
            raise AdmissionError("invalid root observer output") from exc
        observed = setup.parse_json(raw)
        need(raw == setup.canonical(observed) + b"\n", "noncanonical root observation")
        actual_binding = observed.get("binding") if type(observed) is dict else None
        setup.validate_binding(actual_binding)
        if native:
            need({key: actual_binding[key] for key in setup.NATIVE_BINDING_KEYS} == binding,
                 "initial observer/native identity changed")
        else:
            need(actual_binding == binding, "observer identity changed")
        validate_observer(observed, actual_binding, rule)
        setup.validate_live_evidence(observed["live"], actual_binding, tree_oid, fresh=True)
        return observed
    except BaseException:
        preserve("stdout")
        raise


class Gate:
    def __init__(self, document, repo, evidence_dir):
        self.rule = source_rule(document)
        self.document = json.loads(setup.canonical(document))
        self.repo = Path(repo).resolve(strict=True)
        self.evidence = Path(evidence_dir).resolve(strict=True)
        self.receipt = None
        self.contract = None
        self.state = "CREATED"

    def _source(self):
        event = launch.parse_json(launch.read_regular(Path(os.environ["GITHUB_EVENT_PATH"]), limit=launch.MAX_API), limit=launch.MAX_API)
        checkout = launch.inspect_checkout(self.repo, self.document["binding"]["candidate_sha"])
        live = launch.validate_local_launch(os.environ, event, checkout, self.document)
        need(checkout == self.document["source"] and live["binding"] == self.document["binding"], "immutable source/live binding changed")
        return live

    def _binding(self, live):
        return setup.validate_binding(live["binding"])

    def _source_bytes(self):
        source = launch.inspect_checkout(self.repo, self.document["binding"]["candidate_sha"])
        need(source == self.document["source"], "immutable source changed during admission")
        return source

    def _observer(self, binding):
        return observe_setup(binding, self.rule, self.document["source"]["tree_oid"], self.evidence,
                             "prepare" if self.receipt is None else "final")

    def _paths(self):
        caller = probes.require_runner()
        try:
            pending = launch.read_regular(Path("/proc/self/attr/exec"), limit=4096)
        except launch.LaunchError as exc:
            cause = exc.__cause__
            need(isinstance(cause, OSError) and cause.errno == errno.EINVAL, "unknown pending exec transition")
            pending = b""
        need(pending in (b"", b"\n"), "pending named transition")
        need(sys.executable == setup.PROVIDER_ROOT + "/bin/python" and sys.version_info[:3] == (3, 12, 14),
             "wrong qualified Python/version")
        paths = setup.finite_paths(include_provider=True)
        class Reader:
            @staticmethod
            def read(path):
                return launch.read_regular(Path(path))
        guards = facts.collect_guards(self.repo, Reader())
        need(guards["unexpectedly_eligible"] == [] and not any(x["naturally_eligible"] for x in guards["adapters"].values()),
             "newly eligible adapter requires review")
        root = Path(f"/sys/fs/cgroup/user.slice/user-{caller['uid']}.slice/user@{caller['uid']}.service")
        info = root.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == caller["uid"] and root.resolve() == root,
             "ordinary owned delegated cgroup missing")
        return {"paths": paths, "guards": guards, "corpus": corpus_contract(self.repo), "cgroup_root": str(root)}

    def prepare(self):
        need(self.state == "CREATED", "setup admission is one-shot")
        self.state = "STOP"
        live = self._source()
        binding = self._binding(live)
        observed = self._observer(binding)
        contract = self._paths()
        self._source_bytes()
        self.contract = setup.canonical(contract)
        self.receipt = {"schema_version": 2, "status": "PASS", "binding": binding, "cgroup_root": contract["cgroup_root"],
                        "setup_receipt_sha256": observed["setup_receipt_sha256"], "setup_policy_sha256": observed["setup_policy_sha256"],
                        "source": self.document["source"]}
        launch.write_receipt(self.evidence / "setup-observation.json", observed, limit=setup.MAX_JSON)
        launch.write_receipt(self.evidence / "finite-contract.json", contract)
        self.state = "PREPARED"
        return json.loads(setup.canonical(self.receipt))

    def final_source_recheck(self, receipt):
        need(self.state == "PREPARED" and setup.canonical(receipt) == setup.canonical(self.receipt), "invalid final setup receipt/state")
        self.state = "STOP"
        binding = self._binding(self._source())
        need(setup.canonical(binding) == setup.canonical(self.receipt["binding"]), "final run/attempt/boot changed")
        observed = self._observer(binding)
        need(observed["setup_receipt_sha256"] == self.receipt["setup_receipt_sha256"]
             and observed["setup_policy_sha256"] == self.receipt["setup_policy_sha256"], "final sealed policy identity changed")
        need(setup.canonical(self._paths()) == self.contract, "final finite path/natural-guard/corpus contract changed")
        self._source_bytes()
        launch.write_receipt(self.evidence / "setup-final-observation.json", observed, limit=setup.MAX_JSON)
        self.state = "FINAL"
        return {"status": "PASS", "binding": binding, "source": self.document["source"], "live": observed["live"],
                "setup_final_observation_sha256": hashlib.sha256(setup.canonical(observed) + b"\n").hexdigest()}
