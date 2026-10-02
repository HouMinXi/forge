"""Language dispatch for the mutation gate.

The review path used to filter the diff to .py and then call mutmut.
Registered adapters (js-stryker, go-gremlins, rust-cargo-mutants,
ps-mutant) never ran. These tests pin the suffix-to-adapter map and
the skip text the gate shows when no adapter owns the file.
"""

from code_forge.mutation_dispatch import adapter_for_path, dispatch_label


def test_typescript_selects_stryker():
    assert adapter_for_path("src/app.ts") == "js-stryker"
    assert adapter_for_path("src/app.tsx") == "js-stryker"


def test_javascript_selects_stryker():
    assert adapter_for_path("src/app.js") == "js-stryker"
    assert adapter_for_path("src/app.mjs") == "js-stryker"


def test_gate_probe_reads_gremlins_version_text(monkeypatch):
    """gremlins --version prints 'dev' and exits 0. Exit code is not a version."""
    import subprocess

    from code_forge.mutation_dispatch import _tool_version

    def fake_run(argv, **kwargs):
        del kwargs
        assert argv[0].endswith("gremlins")
        assert "--version" in argv
        return subprocess.CompletedProcess(argv, 0, stdout="gremlins version dev\n", stderr="")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gremlins")
    assert _tool_version("go-gremlins", fake_run) == "dev"


def test_tool_version_reads_pinned_numbers(monkeypatch):
    import subprocess

    from code_forge.mutation_dispatch import _tool_version

    seen = []

    def fake_run(argv, **kwargs):
        del kwargs
        seen.append(tuple(argv))
        text = "cargo-mutants 27.1.0\n" if "mutants" in argv else "10.0.0\n"
        return subprocess.CompletedProcess(argv, 0, stdout=text, stderr="")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    assert _tool_version("rust-cargo-mutants", fake_run) == "27.1.0"
    assert _tool_version("js-stryker", fake_run) == "10.0.0"
    assert any("mutants" in argv for argv in seen)
    assert any(argv[0].endswith("/stryker") for argv in seen)


def test_go_invoke_names_the_tool(tmp_path, monkeypatch):
    import subprocess

    from code_forge.mutation_engines.adapters.go_gremlins import GremlinsAdapter

    seen = []

    def fake_run(argv, **kwargs):
        del kwargs
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = GremlinsAdapter().invoke(tmp_path)
    assert seen and seen[0][0] == "gremlins"
    assert result.outcomes == ()
    assert result.reason == "no go test"


def test_rust_and_js_invoke_name_their_tools(tmp_path, monkeypatch):
    import subprocess

    from code_forge.mutation_engines.adapters.js_stryker import StrykerAdapter
    from code_forge.mutation_engines.adapters.rust_cargo_mutants import CargoMutantsAdapter

    seen = []

    def fake_run(argv, **kwargs):
        del kwargs
        seen.append(argv[0])
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    rust = CargoMutantsAdapter().invoke(tmp_path)
    js = StrykerAdapter().invoke(tmp_path)
    assert seen == ["cargo", "stryker"]
    assert rust.reason == "no cargo test"
    assert js.reason == "no vitest"
    assert rust.outcomes == () and js.outcomes == ()


def test_c_invoke_names_mull(tmp_path, monkeypatch):
    import subprocess

    from code_forge.mutation_engines.adapters.c_mull import MullAdapter

    seen = []

    def fake_run(argv, **kwargs):
        del kwargs
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = MullAdapter().invoke(tmp_path)
    assert seen and seen[0][0] == "mull-runner-22"
    assert result.outcomes == ()
    assert result.reason == "no c test binary"


def test_invoke_reports_diagnostic_completion_when_the_test_file_exists(tmp_path, monkeypatch):
    """A test file changes the reason. It still does not invent a score.

    The needle is the opposite of the empty-tree case: if the scan
    misses the test file, the reason stays 'no go test' and this fails.
    """
    import subprocess

    from code_forge.mutation_engines.adapters.go_gremlins import GremlinsAdapter
    from code_forge.mutation_engines.adapters.js_stryker import StrykerAdapter
    from code_forge.mutation_engines.adapters.ps_mutant import PSMutantAdapter

    monkeypatch.setattr(
        "subprocess.run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )
    (tmp_path / "pkg_test.go").write_text("package p\n")
    (tmp_path / "vitest.config.ts").write_text("export default {}\n")
    (tmp_path / "a.Tests.ps1").write_text("Describe 'a' {}\n")
    go = GremlinsAdapter().invoke(tmp_path)
    js = StrykerAdapter().invoke(tmp_path)
    ps = PSMutantAdapter().invoke(tmp_path)
    assert go.reason == "diagnostic complete", go.reason
    assert js.reason == "diagnostic complete", js.reason
    assert ps.reason == "diagnostic complete", ps.reason
    assert go.outcomes == () and js.outcomes == () and ps.outcomes == ()
    (tmp_path / "lib_test.rs").write_text("fn t() {}\n")
    binary = tmp_path / "math_test"
    binary.write_text("")
    binary.chmod(0o755)
    from code_forge.mutation_engines.adapters.c_mull import MullAdapter
    from code_forge.mutation_engines.adapters.rust_cargo_mutants import CargoMutantsAdapter

    rust = CargoMutantsAdapter().invoke(tmp_path)
    c = MullAdapter().invoke(tmp_path)
    assert rust.reason == "diagnostic complete", rust.reason
    assert c.reason == "diagnostic complete", c.reason
    assert rust.outcomes == () and c.outcomes == ()


def test_run_uses_the_same_probe_as_the_note(tmp_path, monkeypatch):
    import subprocess

    from code_forge.mutation_dispatch import run_note

    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="gremlins version 0.6.0\n", stderr="")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "run", fake_run)
    note = run_note(["pkg/a.go"], tmp_path)
    assert "go-gremlins" in note
    assert "no go test" in note
    assert calls[0][0] == ["/usr/bin/gremlins", "--version"]
    assert calls[1][1]["cwd"] == str(tmp_path.resolve())


def test_powershell_selects_psmutant():
    assert adapter_for_path("scripts/build.ps1") == "ps-mutant"
    assert adapter_for_path("scripts/build.psm1") == "ps-mutant"


def test_python_stays_on_mutmut():
    assert adapter_for_path("src/code_forge/mutation.py") == "python-mutmut"


def test_c_and_cpp_select_mull():
    assert adapter_for_path("src/main.c") == "c-mull"
    assert adapter_for_path("src/main.cc") == "c-mull"
    assert adapter_for_path("src/main.cpp") == "c-mull"
    assert adapter_for_path("include/main.h") == "c-mull"
    assert adapter_for_path("include/main.hpp") == "c-mull"


def test_unknown_suffix_has_no_adapter():
    assert adapter_for_path("README.md") is None
    assert adapter_for_path("notes.txt") is None


def test_skip_text_names_the_language_not_python_mvp():
    label = dispatch_label(["src/app.ts", "pkg/main.go", "README.md"])
    assert "js-stryker" in label
    assert "go-gremlins" in label
    assert "Python-only" not in label


def test_review_gate_names_every_selected_adapter():
    """The review gate's own summary must list the registry ids it selected."""
    from code_forge.mutation_dispatch import review_gate_summary

    summary = review_gate_summary(
        ["src/app.ts", "pkg/a.go", "lib.rs", "a.ps1", "src/main.c", "README.md"]
    )
    assert summary == "js-stryker, go-gremlins, rust-cargo-mutants, ps-mutant, c-mull"


def test_each_non_python_adapter_reports_its_tool(monkeypatch):
    """The gate asks every selected adapter whether its tool is present.

    A missing binary is a named skip, not a Python-only message. The
    gate does not invent a mutation score when the tool is absent.
    """
    from code_forge.mutation_dispatch import tool_status

    monkeypatch.setattr("shutil.which", lambda name: None)
    rows = tool_status(["src/app.ts", "pkg/a.go", "lib.rs", "a.ps1", "src/main.c"])
    by_id = {row.adapter_id: row for row in rows}
    assert set(by_id) == {
        "js-stryker",
        "go-gremlins",
        "rust-cargo-mutants",
        "ps-mutant",
        "c-mull",
    }
    assert by_id["js-stryker"].tool == "stryker"
    assert by_id["go-gremlins"].tool == "gremlins"
    assert by_id["rust-cargo-mutants"].tool == "cargo-mutants"
    assert by_id["ps-mutant"].tool == "pwsh"
    assert by_id["c-mull"].tool == "mull-runner-22"
    assert all(row.present is False for row in rows)


def test_note_names_a_missing_binary(monkeypatch):
    from code_forge.mutation_dispatch import other_adapter_note

    monkeypatch.setattr("shutil.which", lambda name: None)
    note = other_adapter_note(["src/app.ts", "pkg/a.go"])
    tool_part = note.split("; ")[1]
    assert tool_part == "stryker missing, gremlins missing"


def test_note_includes_the_run_when_a_root_is_given(tmp_path, monkeypatch):
    from code_forge.mutation_dispatch import other_adapter_note

    monkeypatch.setattr(
        "code_forge.mutation_engines.adapters.ps_mutant.PSMutantAdapter.invoke",
        lambda self, root: type("R", (), {"reason": "no pester"})(),
    )
    note = other_adapter_note(["a.ps1"], root=tmp_path)
    assert "mutation run:" in note
    assert "ps-mutant no pester" in note


def test_note_names_a_real_binary():
    """Present means the binary is on this host's PATH, not a stub."""
    import shutil

    from code_forge.mutation_dispatch import other_adapter_note

    if shutil.which("gremlins") is None:
        return
    note = other_adapter_note(["pkg/a.go"])
    assert note.split("; ")[1] == "gremlins present"


def test_gate_probes_each_selected_adapter(monkeypatch):
    """Selecting an adapter is not enough. The gate must call probe."""
    from code_forge.mutation_dispatch import probe_note
    from code_forge.mutation_engines.adapters.base import CapabilityReport, CapabilityState

    seen = []

    def fake_probe(self, target, context):
        del context
        seen.append((self.id, target.adapter))
        return CapabilityReport(
            state=CapabilityState.AVAILABLE,
            resolved_tool_version="test",
            evidence=(),
            errors=(),
        )

    monkeypatch.setattr(
        "code_forge.mutation_engines.adapters.c_mull.MullAdapter.probe",
        fake_probe,
    )
    note = probe_note(["src/main.c"])
    assert seen == [("c-mull", "c-mull")]
    assert "c-mull available" in note
