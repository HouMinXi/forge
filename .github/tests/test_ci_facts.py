"""Offline fact-collector tests. Never inspect host securityfs or invoke sudo."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

MODULE = Path(__file__).parents[1] / "scripts/forge_ci/facts.py"
spec = importlib.util.spec_from_file_location("ci_facts_under_test", MODULE)
facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facts)


class FakeReader:
    def __init__(self):
        self.nodes = {}
        self.files = {}
        self.fail = set()
        self.reads = []
        self.trees = 0
        self.mutate = None

    def directory(self, path):
        if path not in self.nodes:
            parent = str(Path(path).parent)
            if parent != path and parent != "/":
                self.directory(parent)
            self.nodes[path] = dict(type="d", target="", mode=0o755, uid=0, gid=0, size=0)

    def file(self, path, content):
        self.directory(str(Path(path).parent))
        data = content.encode() if isinstance(content, str) else content
        self.files[path] = data
        self.nodes[path] = dict(type="f", target="", mode=0o444, uid=0, gid=0, size=len(data))

    def link(self, path, target):
        self.directory(str(Path(path).parent))
        self.nodes[path] = dict(type="l", target=target, mode=0o777, uid=0, gid=0, size=len(target))

    def read(self, path, *, limit=facts.MAX_READ):
        self.reads.append(path)
        if path in self.fail or path not in self.files:
            raise facts.FactError("unreadable " + path)
        if len(self.files[path]) > limit:
            raise facts.FactError("read limit exceeded")
        return self.files[path]

    def tree(self, path):
        self.trees += 1
        if path in self.fail:
            raise facts.FactError("unreadable " + path)
        if self.mutate:
            self.mutate(self)
        return copy.deepcopy({p: item for p, item in self.nodes.items()
                              if p == path or p.startswith(path + "/")})


def kernel_fixture(*, folder="usr.bin.example.101", namespace="", attachment="/usr/bin/example"):
    reader = FakeReader()
    for name, value in {"ns_level": "0", "ns_name": "root", "stacked": "no", "ns_stacked": "no"}.items():
        reader.file(facts.APPARMOR_ROOT + "/." + name, value + "\n")
    root = facts.POLICY_ROOT
    for directory in (root, root + "/profiles", root + "/namespaces"):
        reader.directory(directory)
    reader.file(root + "/revision", "7\n")
    base = root
    if namespace:
        base += "/namespaces/" + namespace
        reader.directory(base + "/profiles")
        reader.directory(base + "/namespaces")
        reader.file(base + "/revision", "2\n")
    profile = base + "/profiles/" + folder
    reader.file(profile + "/name", "example\n")
    reader.file(profile + "/mode", "enforce\n")
    reader.file(profile + "/attach", attachment + "\n")
    reader.file(profile + "/sha1", "abc123\n")
    prefix = f":{namespace}://" if namespace else ""
    reader.file(facts.APPARMOR_ROOT + "/profiles", prefix + "example (enforce)\n")
    return reader, profile


def test_complete_inventory_is_unreviewed_and_digest_ignores_kernel_ids():
    first, _ = kernel_fixture(folder="example.1")
    second, _ = kernel_fixture(folder="example.987654")
    one = facts.collect_kernel_inventory(first)
    two = facts.collect_kernel_inventory(second)
    assert one["semantic_sha256"] == two["semantic_sha256"]
    assert one["raw_tree"] != two["raw_tree"]
    assert one["reviewed_inventory_sha256"] is None
    assert one["attachment_review"] == "REQUIRED"


def test_authoritative_namespace_inventory_crosschecks_loaded_list():
    reader, _ = kernel_fixture(namespace="build")
    result = facts.collect_kernel_inventory(reader)
    assert result["semantic"]["namespaces"] == ["", "build"]
    assert result["semantic"]["profiles"][0]["namespace"] == "build"


@pytest.mark.parametrize("field", ["name", "mode", "attach"])
def test_missing_or_unreadable_profile_fact_is_stop(field):
    reader, profile = kernel_fixture()
    reader.fail.add(profile + "/" + field)
    with pytest.raises(facts.FactError, match="unreadable"):
        facts.collect_kernel_inventory(reader)


@pytest.mark.parametrize("attachment", ["<unknown>", "<opaque>", "unknown", ""])
def test_opaque_attachment_is_not_absence(attachment):
    reader, _ = kernel_fixture(attachment=attachment)
    with pytest.raises(facts.FactError, match="opaque|ambiguous"):
        facts.collect_kernel_inventory(reader)


def test_explicit_none_and_complex_expressions_are_preserved_for_manual_review():
    for attachment in ("<none>", "example", "/{usr/,}bin/**", "path xattrs=(security.foo=bar)"):
        reader, _ = kernel_fixture(attachment=attachment)
        result = facts.collect_kernel_inventory(reader)
        assert result["semantic"]["profiles"][0]["attachment"] == attachment
        assert result["reviewed_inventory_sha256"] is None


def test_duplicate_profile_names_fail_despite_distinct_kernel_directories():
    reader, profile = kernel_fixture()
    for field in ("name", "mode", "attach"):
        reader.file(profile + "duplicate/" + field, reader.files[profile + "/" + field])
    with pytest.raises(facts.FactError, match="duplicate profile"):
        facts.collect_kernel_inventory(reader)


@pytest.mark.parametrize("listing", ["example (enforce)\nexample (enforce)\n", "other (enforce)\n",
                                     "example (complain)\n", "", "example\n"])
def test_missing_duplicate_disagreeing_or_ambiguous_listing_fails(listing):
    reader, _ = kernel_fixture()
    reader.file(facts.APPARMOR_ROOT + "/profiles", listing)
    with pytest.raises(facts.FactError):
        facts.collect_kernel_inventory(reader)


def test_metadata_links_hash_bytes_without_binding_volatile_paths():
    results = []
    for revision in (".rawdata/1", ".rawdata/998877"):
        reader, profile = kernel_fixture()
        raw = facts.POLICY_ROOT + "/" + revision + "/raw_data"
        reader.file(raw, b"\0binary conditional and attachment policy\0")
        reader.link(profile + "/raw_data", "../../" + revision + "/raw_data")
        result = facts.collect_kernel_inventory(reader)
        assert result["semantic"]["profiles"][0]["metadata"]["raw_data"]["bytes"] > 0
        results.append(result["semantic_sha256"])
    assert results[0] == results[1]


@pytest.mark.parametrize("target", ["/etc/passwd", "raw_data", "../../missing"])
def test_missing_cyclic_or_escaping_metadata_link_stops(target):
    reader, profile = kernel_fixture()
    reader.link(profile + "/raw_data", target)
    with pytest.raises(facts.FactError, match="missing|cyclic|escaped"):
        facts.collect_kernel_inventory(reader)


def test_conflicting_names_are_retained_for_outer_fail_closed_gate():
    reader, profile = kernel_fixture()
    reader.file(profile + "/name", "bwrap\n")
    reader.file(facts.APPARMOR_ROOT + "/profiles", "bwrap (enforce)\n")
    assert facts.collect_kernel_inventory(reader)["conflicting_names"] == ["bwrap"]


def test_revision_change_stops_collection():
    reader, _ = kernel_fixture()
    def mutate(reader):
        if reader.trees == 2:
            reader.files[facts.POLICY_ROOT + "/revision"] = b"8\n"
    reader.mutate = mutate
    with pytest.raises(facts.FactError, match="revision changed"):
        facts.collect_kernel_inventory(reader)


def test_missing_namespace_directory_is_unknown_not_empty():
    reader, _ = kernel_fixture()
    del reader.nodes[facts.POLICY_ROOT + "/namespaces"]
    with pytest.raises(facts.FactError, match="missing namespace"):
        facts.collect_kernel_inventory(reader)


def policy_fixture():
    reader = FakeReader()
    reader.file("/etc/apparmor/parser.conf", "# no active overrides\n")
    reader.file(facts.PROFILE_ROOT + "/abi/4.0", "# ABI bytes\n")
    reader.file(facts.PROFILE_ROOT + "/tunables/global", "include <tunables/alias>\ninclude if exists <tunables/optional>\n")
    reader.file(facts.PROFILE_ROOT + "/tunables/alias", "alias /home/ -> /srv/home/,\n")
    return reader


def test_transitive_include_and_alias_inputs_are_preserved():
    result = facts.collect_policy_inputs(policy_fixture(), None)
    assert "tunables/alias" in result["include_closure"]
    assert "tunables/optional" in result["absent_optional"]
    assert result["forbidden_overrides"] == []


@pytest.mark.parametrize("relative", ["local/bwrap-userns-restrict", "local/unpriv_bwrap",
                                       "disable/bwrap-userns-restrict", "force-complain/bwrap"])
def test_relevant_local_and_mode_overrides_cannot_disappear(relative):
    reader = policy_fixture()
    reader.file(facts.PROFILE_ROOT + "/" + relative, "")
    assert facts.PROFILE_ROOT + "/" + relative in facts.collect_policy_inputs(reader, None)["forbidden_overrides"]


def test_force_complain_alias_to_relevant_profile_is_rejected():
    reader = policy_fixture()
    reader.link(facts.PROFILE_ROOT + "/force-complain/unrelated-name", "../bwrap-userns-restrict")
    assert facts.collect_policy_inputs(reader, None)["forbidden_overrides"]


@pytest.mark.parametrize("directive", ["include <missing>", "include <../escape>",
                                        "include <@{variable}>", "include malformed", "include <tunables/global>"])
def test_missing_escaped_opaque_or_cyclic_include_is_stop(directive):
    reader = policy_fixture()
    reader.file(facts.PROFILE_ROOT + "/tunables/alias", directive)
    with pytest.raises(facts.FactError):
        facts.collect_policy_inputs(reader, None)


def test_symlinked_include_is_not_silently_followed():
    reader = policy_fixture()
    reader.link(facts.PROFILE_ROOT + "/tunables/alias", "/outside/alias")
    with pytest.raises(facts.FactError, match="symlinked"):
        facts.collect_policy_inputs(reader, None)


def test_vendor_mismatch_is_stop(tmp_path):
    with pytest.raises(facts.FactError, match="differs from authenticated"):
        facts.collect_policy_inputs(policy_fixture(), tmp_path)


def test_semantic_digest_ordering_and_modes():
    entry = dict(namespace="", name="alpha", mode="enforce", attachment="alpha", metadata={})
    other = dict(entry, name="beta")
    first = facts.semantic_inventory([""], [entry, other])
    assert facts.digest(first) == facts.digest(facts.semantic_inventory([""], [other, entry]))
    other["mode"] = "complain"
    assert facts.digest(first) != facts.digest(facts.semantic_inventory([""], [entry, other]))


def test_unreadable_guard_is_not_false(monkeypatch):
    def denied(path):
        raise PermissionError("denied")
    monkeypatch.setattr(facts.os, "stat", denied)
    with pytest.raises(facts.FactError, match="unknown adapter eligibility"):
        facts.guard_path("/example", "isdir")


def test_natural_guards_are_source_checked_without_imports(monkeypatch):
    class SourceReader:
        def read(self, path):
            return Path(path).read_bytes()
    monkeypatch.setattr(facts, "guard_path", lambda path, kind: dict(path=path, kind=kind, matches=False))
    result = facts.collect_guards(MODULE.parents[3], SourceReader(), uid=1001)
    assert result["unexpectedly_eligible"] == []
    python = result["adapters"]["python"]["inputs"]
    assert python[0]["path"].endswith("user-1000.slice/user@1000.service")
    assert "user-1001.slice" in result["adapters"]["javascript"]["inputs"][0]["path"]


def test_extra_adapter_eligibility_is_reported_not_forced_skipped(monkeypatch):
    class SourceReader:
        def read(self, path):
            return Path(path).read_bytes()
    monkeypatch.setattr(facts, "guard_path", lambda path, kind: dict(path=path, kind=kind, matches=True))
    result = facts.collect_guards(MODULE.parents[3], SourceReader(), uid=1001)
    assert set(result["unexpectedly_eligible"]) == set(facts.GUARDS)


def test_changed_guard_is_refused(monkeypatch):
    class SourceReader:
        def read(self, path):
            return Path(path).read_bytes().replace(b"not (os.path.isdir(CGROUP_ROOT)", b"not (False")
    with pytest.raises(facts.FactError, match="predicate changed"):
        facts.collect_guards(MODULE.parents[3], SourceReader(), uid=1001)


def test_duplicate_interpreter_json_is_rejected():
    with pytest.raises(facts.FactError, match="duplicate JSON"):
        facts.strict_json(b'{"path":[],"path":["untrusted"]}')


def test_contaminated_interpreter_startup_is_rejected():
    with pytest.raises(facts.FactError, match="contaminated"):
        facts.strict_json(b'hello from sitecustomize\n{"path":[]}')


def test_command_runner_caps_output_and_preserves_stop_evidence(tmp_path):
    evidence = facts.Evidence(tmp_path / "evidence")
    command = facts.Commands(evidence)
    with pytest.raises(facts.FactError, match="output limit"):
        command.run([sys.executable, "-c", "print('x'*10000)"], limit=100)
    assert command.records[0]["error"] == "command output limit exceeded"
    assert command.records[0]["returncode"] is not None


def test_command_runner_deadline_is_bounded(tmp_path):
    evidence = facts.Evidence(tmp_path / "evidence")
    command = facts.Commands(evidence)
    with pytest.raises(facts.FactError, match="deadline"):
        command.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05)
    assert command.records[0]["returncode"] is not None


def test_output_must_be_fresh(tmp_path):
    (tmp_path / "old").write_text("stale")
    with pytest.raises(facts.FactError, match="fresh empty"):
        facts.Evidence(tmp_path)


def test_collector_refuses_root_and_persists_explicit_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(facts.os, "getuid", lambda: 0)
    result = facts.collect(tmp_path / "evidence", repo=tmp_path)
    report = json.loads((tmp_path / "evidence/facts.json").read_text())
    assert result == 2
    assert report["status"] == "STOP"
    assert report["admission"] is False
    assert report["reviewed_inventory_sha256"] is None
    assert report["commands"] == []


def test_privileged_reader_uses_only_bounded_read_only_utilities(tmp_path, monkeypatch):
    evidence = facts.Evidence(tmp_path / "evidence")
    class FakeCommands:
        deadline = float("inf")
        def run(self, argv, **kwargs):
            assert argv[:6] == ["/usr/bin/sudo", "-n", "--", "/usr/bin/timeout", "--signal=KILL", "5s"]
            assert argv[6] == "/usr/bin/head"
            assert "python" not in " ".join(argv)
            return b"enforce\n"
    original = open
    def denied(path, *args, **kwargs):
        if str(path).startswith(facts.APPARMOR_ROOT):
            raise PermissionError("root readable")
        return original(path, *args, **kwargs)
    monkeypatch.setattr("builtins.open", denied)
    reader = facts.Reader(FakeCommands(), evidence)
    assert reader.read(facts.APPARMOR_ROOT + "/profiles") == b"enforce\n"


def test_python_inventory_script_has_valid_syntax_and_no_root_execution():
    compile(facts.PYTHON_FACTS_SCRIPT, "python-inventory", "exec")
    assert "subprocess" not in facts.PYTHON_FACTS_SCRIPT
    assert "pip" not in facts.PYTHON_FACTS_SCRIPT


def test_missing_attach_file_is_not_explicit_nonattachment():
    reader, profile = kernel_fixture()
    del reader.nodes[profile + "/attach"]
    with pytest.raises(facts.FactError, match="missing authoritative profile attach"):
        facts.collect_kernel_inventory(reader)


def test_namespace_revision_uses_one_nonblocking_read(tmp_path, monkeypatch):
    evidence = facts.Evidence(tmp_path / "evidence")
    class FakeCommands:
        deadline = float("inf")
    calls = []
    def fake_open(path, flags):
        assert flags & facts.os.O_NONBLOCK
        calls.append(("open", path))
        return 123
    monkeypatch.setattr(facts.os, "open", fake_open)
    monkeypatch.setattr(facts.os, "read", lambda fd, size: calls.append(("read", fd, size)) or b"17\n")
    monkeypatch.setattr(facts.os, "close", lambda fd: calls.append(("close", fd)))
    assert facts.Reader(FakeCommands(), evidence).read(facts.POLICY_ROOT + "/revision") == b"17\n"
    assert len([call for call in calls if call[0] == "read"]) == 1


def test_snapshot_binding_cannot_escape_repository(tmp_path):
    with pytest.raises(facts.FactError, match="escaped snapshot"):
        facts.source_snapshot(tmp_path, "../outside", None)


def test_package_archive_hash_rejects_changed_or_nonregular_inputs(tmp_path):
    path = tmp_path / "input"
    path.mkdir()
    with pytest.raises(facts.FactError, match="type bound"):
        facts.hash_file(path)


def _stub_collection(monkeypatch):
    monkeypatch.setattr(facts.os, "getuid", lambda: 1001)
    monkeypatch.setattr(facts.os, "geteuid", lambda: 1001)
    monkeypatch.setattr(facts, "collect_host", lambda reader: {"caller": "fake"})
    monkeypatch.setattr(facts, "collect_collection_utilities", lambda: {})
    monkeypatch.setattr(facts, "collect_kernel_inventory", lambda reader: {
        "conflicting_names": [], "semantic": {"profiles": []}, "semantic_sha256": "test-digest"})
    monkeypatch.setattr(facts, "collect_policy_inputs", lambda *args: {"forbidden_overrides": []})
    monkeypatch.setattr(facts, "collect_system_tools", lambda *args: {})
    monkeypatch.setattr(facts, "collect_executables", lambda *args: {})
    monkeypatch.setattr(facts, "collect_guards", lambda *args: {"unexpectedly_eligible": []})
    monkeypatch.setattr(facts, "source_snapshot", lambda *args: {})
    monkeypatch.setattr(facts, "collect_python", lambda *args: {})


def test_success_means_collected_unreviewed_never_admitted(tmp_path, monkeypatch):
    _stub_collection(monkeypatch)
    assert facts.collect(tmp_path / "evidence", repo=tmp_path) == 0
    report = json.loads((tmp_path / "evidence/facts.json").read_text())
    approval = json.loads((tmp_path / "evidence/attachment-review-required.json").read_text())
    assert report["status"] == "COLLECTED_UNREVIEWED"
    assert report["admission"] is False and approval["admission"] is False
    assert approval["reviewed_inventory_sha256"] is None


@pytest.mark.parametrize("stage", ["kernel", "override", "adapter"])
def test_conflicts_preserve_facts_and_force_stop(tmp_path, monkeypatch, stage):
    _stub_collection(monkeypatch)
    if stage == "kernel":
        monkeypatch.setattr(facts, "collect_kernel_inventory", lambda reader: {
            "conflicting_names": ["bwrap"], "semantic": {"profiles": []}, "semantic_sha256": "digest"})
    elif stage == "override":
        monkeypatch.setattr(facts, "collect_policy_inputs", lambda *args: {"forbidden_overrides": ["local/bwrap"]})
    else:
        monkeypatch.setattr(facts, "collect_guards", lambda *args: {"unexpectedly_eligible": ["python"]})
    assert facts.collect(tmp_path / "evidence", repo=tmp_path) == 2
    report = json.loads((tmp_path / "evidence/facts.json").read_text())
    assert report["status"] == "STOP" and report["errors"]
    assert report["facts"]["host"] == {"caller": "fake"}
    assert "python" in report["facts"]
    assert report["reviewed_inventory_sha256"] is None


def test_magic_policy_root_only_uses_command_line_symlink_traversal(tmp_path):
    evidence = facts.Evidence(tmp_path / "evidence")
    class FakeCommands:
        deadline = float("inf")
        def run(self, argv, **kwargs):
            index = argv.index("/usr/bin/find")
            path = argv[index + 2]
            assert argv[index + 1] == ("-H" if path == facts.POLICY_ROOT else "-P")
            return ("d\0" + path + "\0\0" + "755\0" + "0\0" + "0\0" + "0\0").encode()
    reader = facts.Reader(FakeCommands(), evidence)
    assert reader.tree(facts.POLICY_ROOT)[facts.POLICY_ROOT]["type"] == "d"
    assert reader.tree(facts.PROFILE_ROOT)[facts.PROFILE_ROOT]["type"] == "d"


def test_same_local_child_name_under_different_profile_parents_is_not_duplicate():
    reader, profile = kernel_fixture()
    second = facts.POLICY_ROOT + "/profiles/other.102"
    for path, name in ((profile, "example"), (second, "other")):
        reader.file(path + "/name", name + "\n")
        reader.file(path + "/mode", "enforce\n")
        reader.file(path + "/attach", "/usr/bin/" + name + "\n")
        child = path + "/profiles/child.333"
        reader.file(child + "/name", "child\n")
        reader.file(child + "/mode", "enforce\n")
        reader.file(child + "/attach", "child\n")
    reader.file(facts.APPARMOR_ROOT + "/profiles", "example (enforce)\nexample//child (enforce)\nother (enforce)\nother//child (enforce)\n")
    result = facts.collect_kernel_inventory(reader)
    assert {item["name"] for item in result["semantic"]["profiles"]} == {
        "example", "example//child", "other", "other//child"}


def test_root_revision_fallback_is_single_read_only_nonblocking_dd(tmp_path, monkeypatch):
    evidence = facts.Evidence(tmp_path / "evidence")
    class FakeCommands:
        deadline = float("inf")
        def run(self, argv, **kwargs):
            assert argv[6:] == ["/usr/bin/dd", "if=" + facts.POLICY_ROOT + "/revision",
                                "iflag=nonblock", "bs=129", "count=1", "status=none"]
            assert not any(value.startswith("of=") for value in argv)
            return b"17\n"
    def denied(path, flags):
        raise PermissionError("requires root read")
    monkeypatch.setattr(facts.os, "open", denied)
    assert facts.Reader(FakeCommands(), evidence).read(facts.POLICY_ROOT + "/revision") == b"17\n"


@pytest.mark.parametrize("field", ["ns_level", "ns_name", "stacked", "ns_stacked"])
@pytest.mark.parametrize("problem", ["missing", "unreadable"])
def test_missing_or_unreadable_apparmor_namespace_scope_is_stop(field, problem):
    reader, _ = kernel_fixture()
    path = facts.APPARMOR_ROOT + "/." + field
    if problem == "missing":
        del reader.files[path]
    else:
        reader.fail.add(path)
    with pytest.raises(facts.FactError, match="unreadable"):
        facts.collect_kernel_inventory(reader)


@pytest.mark.parametrize("field,value", [("ns_level", "1"), ("ns_level", "unknown"),
                                          ("stacked", "yes"), ("ns_stacked", "yes"),
                                          ("stacked", "unknown")])
def test_nonroot_or_hidden_stack_scope_cannot_be_host_inventory(field, value):
    reader, _ = kernel_fixture()
    reader.file(facts.APPARMOR_ROOT + "/." + field, value + "\n")
    with pytest.raises(facts.FactError, match="host scope"):
        facts.collect_kernel_inventory(reader)


def test_host_scope_is_bound_into_semantic_inventory():
    reader, _ = kernel_fixture()
    result = facts.collect_kernel_inventory(reader)
    assert result["semantic"]["scope"] == {
        "ns_level": "0", "ns_name": "root", "stacked": "no", "ns_stacked": "no"}
    assert result["semantic_sha256"] == facts.digest(result["semantic"])

def test_opaque_stop_retains_complete_inventory_and_manual_review(tmp_path, monkeypatch):
    original = facts.collect_kernel_inventory
    _stub_collection(monkeypatch)
    reader, _ = kernel_fixture(attachment="<unknown>")
    monkeypatch.setattr(facts, "collect_kernel_inventory", lambda unused: original(reader))
    output = tmp_path / "evidence"
    assert facts.collect(output, repo=tmp_path) == 2
    report = json.loads((output / "facts.json").read_text())
    group = report["facts"]["kernel_policy"]
    assert group["unresolved_attachments"] == [
        {"namespace": "", "name": "example", "attachment": "<unknown>"}]
    assert group["semantic"]["profiles"][0]["attachment"] == "<unknown>"
    assert group["raw_profiles"] and group["raw_tree"] and group["loaded_profiles"]
    assert report["status"] == "STOP" and report["admission"] is False
    assert report["reviewed_inventory_sha256"] is None
    assert "opaque" in report["errors"][0]["error"]
    approval = json.loads((output / "attachment-review-required.json").read_text())
    assert approval["admission"] is False and approval["reviewed_inventory_sha256"] is None


def test_include_mismatch_preserves_all_reachable_bytes_without_admitting_them(tmp_path):
    reader = policy_fixture()
    reader.file(facts.PROFILE_ROOT + "/tunables/global",
                "include <tunables/home.d>\ninclude <tunables/alias>\n")
    reader.file(facts.PROFILE_ROOT + "/tunables/home.d/ubuntu", "# generated default\n")
    with pytest.raises(facts.FactError, match="differs from authenticated") as caught:
        facts.collect_policy_inputs(reader, tmp_path)
    result = caught.value.observations
    assert result["include_closure"]["tunables/home.d/ubuntu"]["matches_vendor_package"] is False
    assert result["include_closure"]["tunables/alias"]["matches_vendor_package"] is False
    assert facts.PROFILE_ROOT + "/tunables/alias" in reader.reads
    assert result["unresolved_include_mismatches"] == [
        "abi/4.0", "tunables/alias", "tunables/global", "tunables/home.d/ubuntu"]
    assert result["vendor_comparison"].startswith("STOP")


def test_include_mismatch_does_not_mask_an_unreadable_later_input(tmp_path):
    reader = policy_fixture()
    reader.fail.add(facts.PROFILE_ROOT + "/tunables/alias")
    with pytest.raises(facts.FactError, match="unreadable"):
        facts.collect_policy_inputs(reader, tmp_path)


def test_rejected_binary_identity_is_preserved_without_execution(tmp_path, monkeypatch):
    import errno
    binary = tmp_path / "python3.12"
    binary.write_bytes(b"\x7fELF" + b"not an executable test fixture")
    binary.chmod(0o777)
    link = tmp_path / "python"
    link.symlink_to(binary.name)
    def no_capabilities(*args):
        raise OSError(errno.ENODATA, "no capabilities")
    monkeypatch.setattr(facts.os, "getxattr", no_capabilities)
    with pytest.raises(facts.FactError, match="unexpected executable") as caught:
        facts.executable_identity(str(link), None, require_root=False)
    identity = caught.value.observations
    assert identity["mode"] == 0o777 and identity["canonical"] == str(binary)
    assert identity["file_capabilities_hex"] == "" and identity["elf"] is True
    assert identity["symlinks"] == [{"path": str(link), "target": "python3.12"}]
    assert identity["sha256"] == facts.hash_file(binary)
    class NeverExecute:
        def run(self, *args, **kwargs):
            pytest.fail("a rejected interpreter must not execute")
    class Reader:
        commands = NeverExecute()
    with pytest.raises(facts.FactError, match="unexpected executable") as python_error:
        facts.collect_python(Reader(), tmp_path, [str(link), sys.executable])
    assert python_error.value.observations["interpreters"][0] == {
        "binary": identity, "inventory": None, "status": "STOP"}


def host_fixture(monkeypatch):
    reader = FakeReader()
    fields = {"Uid": "1001 1001 1001 1001", "Gid": "1001 1001 1001 1001",
              "CapInh": "0", "CapPrm": "0", "CapEff": "0", "CapBnd": "ffff",
              "CapAmb": "0", "NoNewPrivs": "0", "Seccomp": "0"}
    reader.file("/proc/self/status", "\n".join(name + ": " + value for name, value in fields.items()))
    for path, value in {
        "/proc/self/attr/current": "unconfined", "/sys/module/apparmor/parameters/enabled": "Y",
        "/proc/sys/kernel/apparmor_restrict_unprivileged_userns": "1", "/etc/os-release": "Ubuntu",
        "/proc/sys/kernel/random/boot_id": "fake-boot", "/sys/fs/cgroup/cgroup.controllers": "memory pids"
    }.items():
        reader.file(path, value + "\n")
    names = ("GITHUB_REPOSITORY GITHUB_REPOSITORY_ID GITHUB_SHA GITHUB_WORKFLOW_SHA GITHUB_RUN_ID "
             "GITHUB_RUN_ATTEMPT GITHUB_JOB GITHUB_EVENT_NAME RUNNER_ARCH ImageOS ImageVersion").split()
    for name in names:
        monkeypatch.setenv(name, "synthetic")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("UNRELATED_SECRET", "must not be recorded")
    for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(facts.os, "getuid", lambda: 1001)
    monkeypatch.setattr(facts.os, "getgid", lambda: 1001)
    monkeypatch.setattr(facts.os, "getgroups", lambda: [1001])
    monkeypatch.setattr(facts.os, "readlink", lambda path: "namespace-observation")
    monkeypatch.setattr(facts, "guard_path", lambda path, kind: {"path": path, "matches": True})
    return reader


@pytest.mark.parametrize("name,value", [
    ("LD_LIBRARY_PATH", "/opt/hostedtoolcache/Python/3.12.14/x64/lib"),
    ("LD_LIBRARY_PATH", "/unexpected::relative"),
    ("LD_PRELOAD", "/unexpected.so"), ("LD_AUDIT", "/unexpected.so")])
def test_loader_observation_is_preserved_but_remains_stop(monkeypatch, name, value):
    reader = host_fixture(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(facts.FactError, match="unexpected dynamic-loader") as caught:
        facts.collect_host(reader)
    result = caught.value.observations
    assert result["loader_environment"][name] == value
    assert result["caller"]["label"] == "unconfined"
    assert result["userns_restriction"] == "1"
    assert "UNRELATED_SECRET" not in json.dumps(result)
    assert "must not be recorded" not in json.dumps(result)


@pytest.mark.parametrize("stage,function", [
    ("host", "collect_host"), ("policy_inputs", "collect_policy_inputs"), ("python", "collect_python")])
def test_partial_fact_error_never_becomes_success(tmp_path, monkeypatch, stage, function):
    _stub_collection(monkeypatch)
    observed = {"unapproved": "recorded bytes"}
    def fail(*args):
        raise facts.FactError("still rejected", observations=observed)
    monkeypatch.setattr(facts, function, fail)
    output = tmp_path / "evidence"
    assert facts.collect(output, repo=tmp_path) == 2
    report = json.loads((output / "facts.json").read_text())
    assert report["facts"][stage] == observed
    assert report["errors"] == [{"stage": stage, "error": "still rejected"}]
    assert report["status"] == "STOP" and report["admission"] is False
    assert report["reviewed_inventory_sha256"] is None


def test_second_rejected_interpreter_is_retained_but_never_executed(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.touch()
    second.touch()
    rejected = {"requested": str(second), "file_capabilities_hex": "unapproved"}
    def identity(path, reader, **kwargs):
        if path == str(second):
            raise facts.FactError("still rejected", observations=rejected)
        return {"requested": path}
    monkeypatch.setattr(facts, "executable_identity", identity)
    calls = []
    class Commands:
        def run(self, argv, **kwargs):
            calls.append(argv[0])
            return b'{"distributions":[{"name":"pytest"}],"freeze":["pytest==synthetic"],"complete":true}'
    class Reader:
        commands = Commands()
    with pytest.raises(facts.FactError, match="still rejected") as caught:
        facts.collect_python(Reader(), tmp_path, [str(first), str(second)])
    assert calls == [str(first)]
    assert len(caught.value.observations["interpreters"]) == 2
    assert caught.value.observations["interpreters"][1]["binary"] == rejected
    assert caught.value.observations["interpreters"][1]["inventory"] is None


@pytest.mark.parametrize("problem", ["listing", "revision"])
def test_opaque_display_does_not_bypass_inventory_consistency(problem):
    reader, _ = kernel_fixture(attachment="<unknown>")
    if problem == "listing":
        reader.file(facts.APPARMOR_ROOT + "/profiles", "other (enforce)\n")
    else:
        def mutate(reader):
            if reader.trees == 2:
                reader.files[facts.POLICY_ROOT + "/revision"] = b"8\n"
        reader.mutate = mutate
    with pytest.raises(facts.FactError, match="disagree|revision changed") as caught:
        facts.collect_kernel_inventory(reader)
    assert caught.value.observations is None


def _python_distribution(root, name="pytest", version="9.1.1", *, metadata_name=None):
    """Real stdlib metadata fixture. Package/plugin bodies must never be imported."""
    metadata = root / (metadata_name or f"{name}-{version}.dist-info")
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
    members = ["pytest.py", "_pytest.py", "forbidden_fixture_plugin.py",
               f"{metadata.name}/METADATA", f"{metadata.name}/entry_points.txt"]
    for module in members[:3]:
        (root / module).write_text("raise AssertionError('inventory must not import this module')\n")
    (metadata / "entry_points.txt").write_text("[pytest11]\nfixture = forbidden_fixture_plugin:plugin\n")
    (metadata / "RECORD").write_text("".join(f"{member},,\n" for member in members))
    return metadata


def _run_python_inventory(monkeypatch, roots):
    import contextlib
    import importlib.metadata
    import io
    import site
    import sysconfig

    sysconfig.get_paths()  # Load generated stdlib configuration before restricting sys.path.
    output = io.StringIO()
    error = None
    paths = [str(root) for root in roots]
    with monkeypatch.context() as patch:
        patch.setattr(sys, "path", paths)
        patch.setattr(site, "getsitepackages", lambda: paths[:1])
        patch.setattr(site, "getusersitepackages", lambda: paths[-1])
        patch.setattr(importlib.metadata, "distributions",
                      lambda: importlib.metadata.Distribution.discover(path=paths))
        for name in ("pytest", "_pytest", "sitecustomize", "usercustomize"):
            patch.delitem(sys.modules, name, raising=False)
        with contextlib.redirect_stdout(output):
            try:
                exec(compile(facts.PYTHON_FACTS_SCRIPT, "python-inventory", "exec"), {})  # noqa: S102
            except (RuntimeError, OSError, ValueError) as exc:
                error = str(exc)
        assert "pytest" not in sys.modules and "_pytest" not in sys.modules
        assert "forbidden_fixture_plugin" not in sys.modules
    return facts.strict_json(output.getvalue().encode()), error


@pytest.mark.parametrize("version,location", [
    ("9.1.1", "user-site"), ("9.2.0", "user-site"), ("9.1.1", "unexpected-site")])
def test_duplicate_distribution_instances_remain_visible_and_rejected(tmp_path, monkeypatch, version, location):
    first, second = tmp_path / "toolcache", tmp_path / location
    metadata = [_python_distribution(first), _python_distribution(second, "PyTeSt", version)]
    (first / "startup.pth").write_text("import forbidden_fixture_plugin\n")
    monkeypatch.setenv("PYTEST_PLUGINS", "forbidden_fixture_plugin")
    monkeypatch.setenv("PYTHONUSERBASE", str(second))
    inventory, error = _run_python_inventory(monkeypatch, [first, second])
    assert error == "duplicate distribution identities: pytest"
    assert inventory["duplicate_distributions"] == [{"normalized_name": "pytest", "instances": [0, 1]}]
    instances = inventory["distributions"]
    assert [entry["metadata_path"] for entry in instances] == list(map(str, metadata))
    assert [entry["metadata_canonical"] for entry in instances] == list(map(str, metadata))
    assert [entry["location"] for entry in instances] == [str(first), str(second)]
    assert [entry["version"] for entry in instances] == ["9.1.1", version]
    assert all(entry["files"] and entry["complete"] for entry in instances)
    assert all(entry["pytest_entry_points"][0]["value"] == "forbidden_fixture_plugin:plugin"
               for entry in instances)
    assert inventory["path"] == inventory["site_paths"] == [str(first), str(second)]
    assert inventory["module_resolution"]["pytest"]["origin"] == str(first / "pytest.py")
    assert inventory["module_resolution"]["_pytest"]["origin"] == str(first / "_pytest.py")
    assert inventory["module_resolution"]["pytest"]["file"]["sha256"] == facts.hash_file(first / "pytest.py")
    assert inventory["startup_inputs"][0]["path"] == str(first / "startup.pth")
    assert inventory["environment"]["PYTEST_PLUGINS"] == "forbidden_fixture_plugin"
    assert inventory["environment"]["PYTHONUSERBASE"] == str(second)


def test_same_site_different_versions_and_normalized_names_are_not_deduplicated(tmp_path, monkeypatch):
    _python_distribution(tmp_path)
    metadata = [_python_distribution(tmp_path, "plug.in", "1"),
                _python_distribution(tmp_path, "plug__in", "2")]
    inventory, error = _run_python_inventory(monkeypatch, [tmp_path])
    assert error == "duplicate distribution identities: plug-in"
    duplicate = inventory["duplicate_distributions"][0]
    assert duplicate["normalized_name"] == "plug-in"
    instances = [inventory["distributions"][index] for index in duplicate["instances"]]
    assert {entry["metadata_path"] for entry in instances} == set(map(str, metadata))
    assert {entry["version"] for entry in instances} == {"1", "2"}


@pytest.mark.parametrize("problem", ["unnamed", "missing-version", "missing-record", "missing-file", "directory", "oversize"])
def test_python_metadata_errors_emit_partial_inventory_but_still_fail(tmp_path, monkeypatch, problem):
    first, second, third = tmp_path / "first", tmp_path / "second", tmp_path / "third"
    _python_distribution(first)
    metadata = _python_distribution(second, "" if problem == "unnamed" else "broken")
    _python_distribution(third, "later")
    if problem == "missing-version":
        (metadata / "METADATA").write_text("Name: broken\n")
    elif problem == "missing-record":
        (metadata / "RECORD").unlink()
    elif problem in ("missing-file", "directory", "oversize"):
        member = second / "broken-input"
        if problem == "directory":
            member.mkdir()
        elif problem == "oversize":
            with member.open("wb") as stream:
                stream.truncate(256 * 1024 * 1024 + 1)
        with (metadata / "RECORD").open("a") as stream:
            stream.write("broken-input,,\n")
    inventory, error = _run_python_inventory(monkeypatch, [first, second, third])
    assert error and inventory["error"].endswith(error)
    assert inventory["complete"] is False
    assert inventory["distributions"][0]["complete"] is True
    assert inventory["distributions"][0]["files"]
    assert inventory["distributions"][1]["complete"] is False
    assert len(inventory["distributions"]) == 3
    assert inventory["distributions"][1]["metadata_path"] == str(metadata)
    assert inventory["distributions"][2]["name"] == "later"
    assert inventory["distributions"][2]["complete"] is True
    assert inventory["distributions"][2]["files"]
    assert len(inventory["distribution_errors"]) == 1
    assert inventory["distribution_errors"][0]["instance"] == 1
    assert inventory["distribution_errors"][0]["error"] == inventory["distributions"][1]["error"]
    assert inventory["module_resolution"]["pytest"]["origin"] == str(first / "pytest.py")


def test_command_failure_preserves_bounded_stdout_and_stderr(tmp_path):
    evidence = facts.Evidence(tmp_path / "evidence")
    commands = facts.Commands(evidence)
    with pytest.raises(facts.FactError, match="exit 1") as caught:
        commands.run([sys.executable, "-c",
                      "import sys; print('{\"complete\":false}'); print('metadata error', file=sys.stderr); sys.exit(1)"],
                     limit=1024)
    assert caught.value.stdout == b'{"complete":false}\n'
    record = commands.records[0]
    assert (evidence.output / record["stdout"]["artifact"]).read_bytes() == caught.value.stdout
    assert (evidence.output / record["stderr"]["artifact"]).read_bytes() == b"metadata error\n"


@pytest.mark.parametrize("output,reason", [
    (b'{"complete":false,"distributions":[{"name":"pytest","version":"9.1.1"}]}', "exit 1"),
    (b'{"incomplete":', "command deadline exceeded"),
    (b'contamination\n{}', "command output limit exceeded"),
    (None, "cannot execute interpreter")])
@pytest.mark.parametrize("failed_index", [0, 1])
def test_interpreter_failure_keeps_binary_and_other_inventory(tmp_path, monkeypatch, output, reason, failed_index):
    first, second = tmp_path / "first", tmp_path / "second"
    first.touch()
    second.touch()
    monkeypatch.setattr(facts, "executable_identity", lambda path, *args, **kwargs:
                        {"requested": path, "mode": 0o755, "sha256": "observed"})
    failed = (first, second)[failed_index]
    calls = []
    class Commands:
        def run(self, argv, **kwargs):
            calls.append(argv[0])
            if argv[0] == str(failed):
                raise facts.FactError(reason, stdout=output)
            return b'{"complete":true,"distributions":[{"name":"pytest"}],"freeze":["pytest==9.1.1"]}'
    class Reader:
        commands = Commands()
    with pytest.raises(facts.FactError, match=reason) as caught:
        facts.collect_python(Reader(), tmp_path, [str(first), str(second)])
    records = caught.value.observations["interpreters"]
    assert calls == [str(first), str(second)]
    assert len(records) == 2 and records[1 - failed_index]["inventory"]["complete"] is True
    record = records[failed_index]
    assert record["binary"] == {"requested": str(failed), "mode": 0o755, "sha256": "observed"}
    assert record["status"] == "STOP"
    if reason == "exit 1":
        assert record["inventory"] == json.loads(output)
        assert record["inventory_sha256"] == facts.digest(json.loads(output))
    else:
        assert record["inventory"] is None


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize("version,location", [
    ("9.1.1", "user-site"), ("9.2.0", "user-site"), ("9.1.1", "unexpected-site")])
def test_duplicate_python_evidence_persists_with_exit2_and_no_admission(
        tmp_path, monkeypatch, returncode, version, location):
    python_collector = facts.collect_python
    _stub_collection(monkeypatch)
    monkeypatch.setattr(facts, "collect_python", python_collector)
    first, second = tmp_path / "first", tmp_path / "second"
    first.touch()
    second.touch()
    monkeypatch.setattr(facts, "executable_identity", lambda path, *args, **kwargs: {"requested": path})
    inventory = {"complete": True, "freeze": ["pytest==9.1.1", "pytest==" + version],
                 "distributions": [{"name": "pytest", "version": "9.1.1", "location": "toolcache"},
                                   {"name": "PyTeSt", "version": version, "location": location}],
                 "duplicate_distributions": [{"normalized_name": "pytest", "instances": [0, 1]}]}
    calls = []
    def run(self, argv, **kwargs):
        calls.append(argv[0])
        if argv[0] == str(second):
            return b'{"complete":true,"distributions":[{"name":"pytest"}],"freeze":["pytest==9.1.1"]}'
        raw = facts.canonical_bytes(inventory)
        if returncode:
            raise facts.FactError("interpreter: exit 1", stdout=raw)
        return raw
    monkeypatch.setattr(facts.Commands, "run", run)
    output = tmp_path / "evidence"
    assert facts.collect(output, repo=tmp_path, pythons=[str(first), str(second)]) == 2
    report = json.loads((output / "facts.json").read_text())
    assert report["status"] == "STOP" and report["admission"] is False
    assert report["reviewed_inventory_sha256"] is None
    record = report["facts"]["python"]["interpreters"][0]
    assert record["status"] == "STOP" and record["inventory"] == inventory
    assert record["inventory_sha256"] == facts.digest(inventory)
    assert report["errors"][0]["stage"] == "python"
    assert calls == [str(first), str(second)]
    assert report["facts"]["python"]["interpreters"][1]["inventory"]["complete"] is True


def test_both_python_failures_are_explicit_and_preserved(tmp_path, monkeypatch):
    executables = [tmp_path / "first", tmp_path / "second"]
    for path in executables:
        path.touch()
    monkeypatch.setattr(facts, "executable_identity", lambda path, *args, **kwargs: {"requested": path})
    class Commands:
        def run(self, argv, **kwargs):
            raise facts.FactError("metadata failed for " + argv[0], stdout=b'{"complete":false}')
    class Reader:
        commands = Commands()
    with pytest.raises(facts.FactError) as caught:
        facts.collect_python(Reader(), tmp_path, list(map(str, executables)))
    records = caught.value.observations["interpreters"]
    assert len(records) == 2
    for path, record in zip(executables, records, strict=True):
        assert str(path) in str(caught.value)
        assert record["binary"]["requested"] == str(path)
        assert record["error"] == "metadata failed for " + str(path)
        assert record["status"] == "STOP" and record["inventory"] == {"complete": False}


def test_installed_record_hash_and_size_are_observed_beside_actual_bytes(tmp_path, monkeypatch):
    import base64
    import hashlib
    metadata = _python_distribution(tmp_path)
    for name in ("generated.pyc", "shipped.pyc"):
        (tmp_path / name).write_bytes(b"synthetic bytecode input; never executed")
    recorded = base64.urlsafe_b64encode(hashlib.sha256(b"wheel bytes").digest()).rstrip(b"=").decode()
    with (metadata / "RECORD").open("a") as stream:
        stream.write(f"generated.pyc,,\nshipped.pyc,sha256={recorded},123\n")
    inventory, error = _run_python_inventory(monkeypatch, [tmp_path])
    assert error is None
    files = {Path(entry["path"]).name: entry for entry in inventory["distributions"][0]["files"]}
    assert files["generated.pyc"]["record_hash"] is None and files["generated.pyc"]["record_size"] is None
    assert files["shipped.pyc"]["record_hash"] == {"algorithm": "sha256", "value": recorded}
    assert files["shipped.pyc"]["record_size"] == 123
    for name in ("generated.pyc", "shipped.pyc"):
        assert files[name]["sha256"] == facts.hash_file(tmp_path / name)
        assert files[name]["bytes"] == (tmp_path / name).stat().st_size


def test_missing_required_record_member_retains_raw_rows_hash_and_size(tmp_path, monkeypatch):
    import base64
    import hashlib
    metadata = _python_distribution(tmp_path)
    recorded = base64.urlsafe_b64encode(hashlib.sha256(b"missing bytes").digest()).rstrip(b"=").decode()
    with (metadata / "RECORD").open("a") as stream:
        stream.write(f"missing-essential.py,sha256={recorded},99\npytest.pyc,,\n")
    inventory, error = _run_python_inventory(monkeypatch, [tmp_path])
    assert error and inventory["complete"] is False
    entry = inventory["distributions"][0]
    assert "missing distribution file" in entry["error"] and entry["complete"] is False
    record = entry["record"]
    assert base64.b64decode(record["base64"]) == (metadata / "RECORD").read_bytes()
    row = record["rows"][-2]
    assert row["fields"] == ["missing-essential.py", "sha256=" + recorded, "99"]
    assert row["record_hash"] == {"algorithm": "sha256", "value": recorded}
    assert row["record_size"] == 99 and row["state"] == "missing"
    assert record["rows"][-1]["path"] == "pytest.pyc"


@pytest.mark.parametrize("raw", [
    b"", b"missing.py,\n", b"missing.py,,,\n", b'"unterminated,,\n', b"\xff,,\n",
    b"missing.py,,negative\n", b"missing.py,invalid,1\n", b"missing.py,sha256=AA,1\n",
    b"/absolute.py,,\n", b"path//file.py,,\n", b"path\\file.py,,\n", b"bad\0.py,,\n",
    b'"bad\nname.py",,\n', b"pytest.py,,\npytest.py,,\n"])
def test_malformed_real_record_is_preserved_and_stops_without_hiding_later_distribution(tmp_path, monkeypatch, raw):
    import base64
    first, later = tmp_path / "first", tmp_path / "later"
    metadata = _python_distribution(first)
    _python_distribution(later, "later")
    (metadata / "RECORD").write_bytes(raw)
    inventory, error = _run_python_inventory(monkeypatch, [first, later])
    assert error and inventory["complete"] is False
    entry = inventory["distributions"][0]
    assert entry["complete"] is False and entry["error"]
    assert base64.b64decode(entry["record"]["base64"]) == raw
    assert entry["record"]["identity"]["sha256"] == facts.hash_file(metadata / "RECORD")
    assert inventory["distributions"][1]["complete"] is True


def test_missing_generated_bytecode_and_relative_console_script_keep_existing_handling(tmp_path, monkeypatch):
    metadata = _python_distribution(tmp_path)
    console = tmp_path.parent / (tmp_path.name + "-console")
    console.write_bytes(b"console bytes; never executed")
    with (metadata / "RECORD").open("a") as stream:
        stream.write(f"missing.pyc,,\nmissing.pyo,,\n../{console.name},,\n")
    inventory, error = _run_python_inventory(monkeypatch, [tmp_path])
    assert error is None and inventory["complete"] is True
    rows = inventory["distributions"][0]["record"]["rows"]
    assert [row["state"] for row in rows[-3:]] == ["absent_generated_bytecode", "absent_generated_bytecode", "observed"]
    entry = inventory["distributions"][0]["files"][rows[-1]["file_index"]]
    assert entry["canonical"] == str(console) and entry["sha256"] == facts.hash_file(console)


def test_egg_info_without_authoritative_record_is_unknown_and_stop(tmp_path, monkeypatch):
    import base64
    metadata = _python_distribution(tmp_path, metadata_name="pytest.egg-info")
    (metadata / "RECORD").unlink()
    (metadata / "SOURCES.txt").write_text("pytest.py\nmissing-essential.py\n")
    (metadata / "installed-files.txt").write_text("../pytest.py\n../missing-essential.py\n")
    inventory, error = _run_python_inventory(monkeypatch, [tmp_path])
    assert error and inventory["complete"] is False
    entry = inventory["distributions"][0]
    assert entry["name"] == "pytest" and entry["complete"] is False
    assert "unknown installed file inventory" in entry["error"]
    inputs = {Path(item["path"]).name: item for item in entry["metadata_inputs"]}
    assert inputs["METADATA"]["state"] == "observed" and inputs["PKG-INFO"]["state"] == "absent"
    for name in ("SOURCES.txt", "installed-files.txt"):
        assert base64.b64decode(inputs[name]["base64"]) == (metadata / name).read_bytes()


def test_file_egg_info_is_retained_as_unknown_metadata_without_approval(tmp_path, monkeypatch):
    import base64
    _python_distribution(tmp_path)
    raw = b"Name: distro-input\nVersion: 1\n"
    metadata = tmp_path / "distro_input.egg-info"
    metadata.write_bytes(raw)
    inventory, error = _run_python_inventory(monkeypatch, [tmp_path])
    assert error and inventory["complete"] is False
    entry = next(item for item in inventory["distributions"] if item["name"] == "distro-input")
    assert entry["complete"] is False and "unknown installed file inventory" in entry["error"]
    assert base64.b64decode(entry["metadata_inputs"][0]["base64"]) == raw


def test_oversize_record_stops_before_reading_it(tmp_path, monkeypatch):
    metadata = _python_distribution(tmp_path)
    with (metadata / "RECORD").open("wb") as stream:
        stream.truncate(4 * 1024 * 1024 + 1)
    inventory, error = _run_python_inventory(monkeypatch, [tmp_path])
    assert error and inventory["complete"] is False
    entry = inventory["distributions"][0]
    assert "metadata type/size bound exceeded" in entry["error"]
    assert "base64" not in entry["record"]


def test_quoted_record_path_is_parsed_as_one_observed_member(tmp_path, monkeypatch):
    metadata = _python_distribution(tmp_path)
    path = tmp_path / "a,b.py"
    path.write_bytes(b"data only")
    with (metadata / "RECORD").open("a") as stream:
        stream.write('"a,b.py",,\n')
    inventory, error = _run_python_inventory(monkeypatch, [tmp_path])
    assert error is None
    row = inventory["distributions"][0]["record"]["rows"][-1]
    assert row["path"] == "a,b.py" and row["state"] == "observed"


def test_privilege_bearing_utility_observation_does_not_approve_execution(tmp_path, monkeypatch):
    import errno
    binary = tmp_path / "sudo-fixture"
    binary.write_bytes(b"\x7fELFsynthetic binary; never executed")
    binary.chmod(0o4755)
    def no_capabilities(*args):
        raise OSError(errno.ENODATA, "no capabilities")
    monkeypatch.setattr(facts.os, "getxattr", no_capabilities)
    observed = facts.observe_executable_identity(str(binary))
    assert observed["mode"] == 0o4755 and observed["sha256"] == facts.hash_file(binary)
    assert observed["canonical"] == str(binary) and observed["file_capabilities_hex"] == ""
    with pytest.raises(facts.FactError, match="unexpected executable") as caught:
        facts.executable_identity(str(binary), None, require_root=False)
    assert caught.value.observations == observed


@pytest.mark.parametrize("change,require_root,accepted", [
    ({}, True, True), ({"uid": 1001}, True, False), ({"uid": 1001}, False, True),
    ({"mode": 0o4755}, False, False), ({"mode": 0o2755}, False, False),
    ({"mode": 0o775}, True, False), ({"mode": 0o757}, True, False),
    ({"file_capabilities_hex": "01"}, True, False), ({"elf": False}, True, False),
    ({"shebang": "/usr/bin/python3"}, False, False)])
def test_executable_acceptance_predicate_is_unchanged(monkeypatch, change, require_root, accepted):
    observed = {"elf": True, "file_capabilities_hex": "", "mode": 0o755, "uid": 0, **change}
    monkeypatch.setattr(facts, "observe_executable_identity", lambda path: observed)
    if accepted:
        assert facts.executable_identity("/synthetic", None, require_root=require_root) == observed
    else:
        with pytest.raises(facts.FactError) as caught:
            facts.executable_identity("/synthetic", None, require_root=require_root)
        assert caught.value.observations == observed


@pytest.mark.parametrize("missing", [None, "/usr/bin/timeout"])
def test_collection_utilities_are_fixed_data_only_and_unapproved(monkeypatch, missing):
    calls = []
    def observe(path):
        calls.append(path)
        if path == missing:
            raise facts.FactError("unreadable utility")
        return {"requested": path, "canonical": path, "uid": 0, "gid": 0,
                "mode": 0o4755 if path == "/usr/bin/sudo" else 0o755,
                "sha256": "observed-digest", "file_capabilities_hex": ""}
    monkeypatch.setattr(facts, "observe_executable_identity", observe)
    if missing:
        with pytest.raises(facts.FactError, match="incomplete") as caught:
            facts.collect_collection_utilities()
        result = caught.value.observations
        assert result["errors"] == [{"path": missing, "error": "unreadable utility"}]
    else:
        result = facts.collect_collection_utilities()
        assert result["errors"] == []
    assert calls == ["/usr/bin/sudo", "/usr/bin/timeout", "/usr/bin/journalctl"]
    assert result["admission"] is False and result["trust_review"].startswith("REQUIRED")
    assert result["executables"]["/usr/bin/sudo"]["privilege_bearing"] is True
    assert result["executables"]["/usr/bin/journalctl"]["privilege_bearing"] is False
    assert result["executables"]["/usr/bin/sudo"]["mode"] == 0o4755
