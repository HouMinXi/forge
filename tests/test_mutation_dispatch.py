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


def test_go_selects_gremlins():
    assert adapter_for_path("pkg/main.go") == "go-gremlins"


def test_rust_selects_cargo_mutants():
    assert adapter_for_path("src/lib.rs") == "rust-cargo-mutants"


def test_powershell_selects_psmutant():
    assert adapter_for_path("scripts/build.ps1") == "ps-mutant"
    assert adapter_for_path("scripts/build.psm1") == "ps-mutant"


def test_python_stays_on_mutmut():
    assert adapter_for_path("src/code_forge/mutation.py") == "python-mutmut"


def test_unknown_suffix_has_no_adapter():
    assert adapter_for_path("README.md") is None
    assert adapter_for_path("notes.c") is None


def test_skip_text_names_the_language_not_python_mvp():
    label = dispatch_label(["src/app.ts", "pkg/main.go", "README.md"])
    assert "js-stryker" in label
    assert "go-gremlins" in label
    assert "Python-only" not in label


def test_review_gate_names_every_selected_adapter():
    """The review gate's own summary must list the registry ids it selected."""
    from code_forge.mutation_dispatch import review_gate_summary

    summary = review_gate_summary(["src/app.ts", "pkg/a.go", "lib.rs", "a.ps1", "README.md"])
    assert summary == "js-stryker, go-gremlins, rust-cargo-mutants, ps-mutant"

