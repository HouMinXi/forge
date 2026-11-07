"""Projection/control isolation with reviewed Phase 4/6 presentation wiring."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from code_forge.report_projection import classify_report_records
from code_forge.state import Disposition, State, StateFinding, save_state, load_state

REPO_SRC = Path(__file__).resolve().parents[1] / "src"
# Reviewed pin evolution: 7d9a476 (#114) excludes MUTATION_INFRA;
# f1b4a78/e436c7a (#338) preserve Phase 6 unavailable product rows.
PINNED_HELPER = {
    "reporting_product_findings": "fdc29b4ca8b221ba620267149bf4a193579a0c2332d630ae5b1c390d210e1816",
    "is_provider_diagnostic": "e4b2a3ee37116704aa70bbfd112f3e5fd45ded8cb239b4104403687ccdf31f18",
    "is_provider_capacity": "5856e24d38543786fcde07e259c607503b3d0b1c3f3ca256622240aa12b09b52",
    "is_receipt_audit": "1842ab453651201431f675f2e2df0bc28e6fb4598fcb00e4cda73932ffa12d16",
    "product_round_history": "11e483aa626bed3fb651073110642377d86cb12a256bbc7fbc09111ca2a82cbc",
    "active_findings": "0f766a061520b338bd035d48c5032e701e9423804e3068fc068e733fb7330183",
    "_count": "2448527daca069c0d30704420e1f53257bc64d9180aa6bc65435c44a8d3ff7db",
    "_confirmed_hold_fingerprints": "6c5ad525554274f2677ae3998e46250acbb858e91243b9ca74c4a25889e1778a",
    "_product_history": "53d5ce4e7fbfbb64936caf4eb1d32fd5078866ffe92885d456752f0d9d1eccc4",
}
PINNED_DEPS = {
    "is_mutation_infra_diagnostic": "9c8209554cdeb290c180caecb8e9c4e0e4b642175af0517a30b9067af7b5e143",
    "_project_product_row": "a49fa58238079766148ce92c52a29aa1292e0e59c27c02499a6b246a04a43c0f",
    "derive_pass_outcomes": "1b5a8bd634e9801c5120a7c116d1229837d86994648e87d71e957165d54fc9cd",
    "_host_diagnostic_shape": "67c3aafc3fecac3ca4e52b1cd68f1107f8cec0f47cf8c4d80b4ae4591fef3d1e",
    "PassOutcome": "95fe52e974f04a5ea2eb1987f31b3a454bf961aa6314e8513d5d4500317bca7e",
    "FindingDiagnosticKind": "10e8b82c4f1a0465351c7e3e1f633341654dc8d89b4f1213e651b22599919c25",
    # W2-6 adds only receipt_subtype: str | None = None; the old class AST
    # remains pinned separately below, including all control fields/methods.
    "StateFinding": "114f01f4689f5123d7a79748d5ceb60549e5329f9c6e0277f24d5a4e20939e5f",
    "_PASS_NAMES": "81b557eb202cc74ccbcb5a1e71ec638a823daa3c8e27e575e4c76e725b8cfdd3",
    "_SEVERITY": "0c46db3bb670deb1d5dfde3e0cd1e629a6133aba6d55fca18670708106078cad",
    "Disposition": "4f1334a5130f9032310f3bb3dc64f67927aa523c04a239cfc0e7476925df9176",
}
PINNED_RUNTIME = {
    "_PASS_NAMES repr": "8ecec8b65d424f029b1ae7099427ed177fd6285e552d4abcfb78ccbfc63e81d9",
    "_SEVERITY repr": "52599584c59a23d84f2a291033d604102a96cdb94380517494dcdd49d53dfc7e",
    "PassOutcome members": "fdd99a6aba49c5f64bba3f1550508db91f714944afee5c087fe5d02e44baa266",
    "FindingDiagnosticKind members": "e7f7c59c1b1693c1688c5e8219a391df51bc62dda54815ec878cff07bc9bf306",
    "Disposition members": "21fcc4e2115ab5b0b2522a58e191c9ad6a9afc6b1e9652f6fcb3a4d391bf3acf",
}

_ALLOWED_STATE_NAMES = {
    "is_provider_capacity",
    "is_provider_diagnostic",
    "is_receipt_audit",
    "reporting_product_findings",
    "derive_pass_outcomes",
    "State",
    "StateFinding",
    "Disposition",
    "PassOutcome",
    "_PASS_NAMES",
}
_STDLIB = {"dataclasses", "typing", "copy"}


# Phase 4 f0951ad permits classification only in terminal CLI output. #291
# fe4c107 permits presentation DTO types, not the classifier, in the serializer.
_PRESENTATION_IMPORTS = {
    ("report_baseline_v2.py", ()): (
        "code_forge.report_projection",
        0,
        ("classify_report_records",),
    ),
    ("cli.py", (("FunctionDef", "_emit_ci_output"),)): (
        "report_projection",
        1,
        ("classify_report_records",),
    ),
    ("report_presentation.py", ()): (
        "code_forge.report_projection",
        0,
        (
            "AssociatedDiagnostic",
            "ClassificationError",
            "FallbackRecord",
            "HostFailureKind",
            "ReportProjection",
            "ReportRecord",
        ),
    ),
}


def _projection_import_hits(tree, module):
    hits = []

    def visit(node, scope=()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, (type(node).__name__, node.name))
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            touches_projection = (
                mod == "report_projection"
                or mod.endswith(".report_projection")
                or any(a.name in ("report_projection", "classify_report_records") for a in node.names)
            )
            if touches_projection:
                signature = (mod, node.level, tuple(a.name for a in node.names))
                if signature != _PRESENTATION_IMPORTS.get((module, scope)) or any(
                    a.asname is not None for a in node.names
                ):
                    hits.append((node.lineno, "importfrom"))
        if isinstance(node, ast.Import) and any(
            a.name == "report_projection" or a.name.endswith(".report_projection") for a in node.names
        ):
            hits.append((node.lineno, "import"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "report_projection" in node.value or "classify_report_records" in node.value:
                hits.append((node.lineno, "string"))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree)
    return hits


def test_control_modules_do_not_import_report_projection():
    files = sorted(
        p
        for p in (REPO_SRC / "code_forge").rglob("*.py")
        if "__pycache__" not in p.parts and p.name != "report_projection.py"
    )
    files.append(REPO_SRC / "code_forge_launcher.py")
    hits = []
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        # Relative module path prevents another package's cli.py being exempt.
        module = p.relative_to(REPO_SRC / "code_forge").as_posix() if p.parent != REPO_SRC else p.name
        hits.extend((str(p), *hit) for hit in _projection_import_hits(tree, module))
    assert hits == [], hits
    assert len(files) >= 130


@pytest.mark.parametrize(
    "module,source",
    [
        ("machine.py", "from .report_projection import classify_report_records"),
        ("cli.py", "from .report_projection import classify_report_records"),
        ("cli.py", "def _run():\n    from .report_projection import classify_report_records"),
        (
            "cli.py",
            "def _emit_ci_output():\n    from .report_projection import classify_report_records as decide",
        ),
        (
            "nested/cli.py",
            "def _emit_ci_output():\n    from .report_projection import classify_report_records",
        ),
        ("report_presentation.py", "from code_forge.report_projection import classify_report_records"),
        ("cli.py", "class _emit_ci_output:\n    from .report_projection import classify_report_records"),
        (
            "cli.py",
            "def _emit_ci_output():\n    def decide():\n        from .report_projection import classify_report_records",
        ),
        ("machine.py", "import code_forge.report_projection"),
        ("machine.py", "name = 'code_forge.report_projection'"),
    ],
)
def test_projection_import_guard_rejects_control_access(module, source):
    assert _projection_import_hits(ast.parse(source), module)


@pytest.mark.parametrize(
    "module,source",
    [
        ("cli.py", "def _emit_ci_output():\n    from .report_projection import classify_report_records"),
        (
            "report_presentation.py",
            "from code_forge.report_projection import AssociatedDiagnostic, ClassificationError, FallbackRecord, HostFailureKind, ReportProjection, ReportRecord",
        ),
    ],
)
def test_projection_import_guard_accepts_only_presentation_signatures(module, source):
    assert _projection_import_hits(ast.parse(source), module) == []


def test_report_projection_imports_only_whitelist():
    path = REPO_SRC / "code_forge" / "report_projection.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    future_count = 0
    state_imports = 0
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            assert [a.name for a in node.names] == ["annotations"]
            future_count += 1
            continue
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                assert top in _STDLIB, a.name
            continue
        if isinstance(node, ast.ImportFrom):
            if node.module in _STDLIB:
                continue
            if node.module == "code_forge.state":
                state_imports += 1
                names = {a.name for a in node.names}
                assert names == _ALLOWED_STATE_NAMES, names
                continue
            pytest.fail(f"disallowed import from {node.module}")
    assert future_count <= 1
    assert state_imports == 1

    forbidden_calls = {"__import__", "exec", "eval", "open"}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name) and n.func.id in forbidden_calls:
                pytest.fail(f"forbidden call {n.func.id}")
            if isinstance(n.func, ast.Attribute) and n.func.attr == "import_module":
                pytest.fail("importlib.import_module")
    # no `re` import
    src = path.read_text(encoding="utf-8")
    assert "import re" not in src
    # S4 prefixes via startswith
    assert "startswith" in src


def _digest(node: ast.AST) -> str:
    """Digest a definition in a form that survives a Python upgrade.

    `ast.dump` prints the tree's field layout, so a field the interpreter adds
    later changes every digest without the source changing at all: on 3.14 every
    pin below differs while the code is byte-identical. That turns a check on
    "this helper was not edited" into a check on "Python was not upgraded".

    `ast.unparse` normalizes source formatting while preserving indentation and
    literal contents. Do not collapse its whitespace: that would hide control
    flow changes and changes to spaces inside string literals. Cross-version
    stability must still be checked when the supported interpreters change.
    """
    return hashlib.sha256(ast.unparse(node).encode()).hexdigest()


def test_digest_preserves_control_flow_indentation():
    nested = ast.parse("def f(flag):\n    if flag:\n        return 1\n        return 2\n").body[0]
    outside = ast.parse("def f(flag):\n    if flag:\n        return 1\n    return 2\n").body[0]
    assert _digest(nested) != _digest(outside)


def test_digest_preserves_string_literal_spaces():
    one_space = ast.parse("def f(): return 'a b'").body[0]
    two_spaces = ast.parse("def f(): return 'a  b'").body[0]
    assert _digest(one_space) != _digest(two_spaces)


def test_digest_ignores_source_formatting():
    compact = ast.parse("def f(x): return x+1").body[0]
    formatted = ast.parse("def f( x ):\n    # incidental formatting\n    return (x + 1)\n").body[0]
    assert _digest(compact) == _digest(formatted)


def _ast_hash_defs(path: Path, names: set[str]) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in names
        ):
            # Prefer the first (outermost walk order); methods are found via walk.
            out.setdefault(node.name, _digest(node))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if getattr(t, "id", None) in names:
                    out[t.id] = _digest(node)
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) in names:
            out[node.target.id] = _digest(node)
    return out


def test_frozen_helper_bodies_unchanged():
    state_p = REPO_SRC / "code_forge" / "state.py"
    machine_p = REPO_SRC / "code_forge" / "machine.py"
    got = {}
    got.update(
        _ast_hash_defs(
            state_p,
            {
                "reporting_product_findings",
                "is_provider_diagnostic",
                "is_provider_capacity",
                "is_receipt_audit",
                "product_round_history",
            },
        )
    )
    got.update(
        _ast_hash_defs(
            machine_p, {"active_findings", "_count", "_confirmed_hold_fingerprints", "_product_history"}
        )
    )
    for name, expect in PINNED_HELPER.items():
        assert got[name] == expect, (name, got[name], expect)


def test_frozen_dependencies_unchanged():
    import code_forge.state as st
    import code_forge.state_rows as rows
    from code_forge.disposition import Disposition

    state_p = REPO_SRC / "code_forge" / "state.py"
    disp_p = REPO_SRC / "code_forge" / "disposition.py"
    rows_p = REPO_SRC / "code_forge" / "state_rows.py"
    got = {}
    got.update(
        _ast_hash_defs(
            state_p,
            {
                "is_mutation_infra_diagnostic",
                "derive_pass_outcomes",
                "_host_diagnostic_shape",
                "PassOutcome",
                "FindingDiagnosticKind",
                "StateFinding",
                "_PASS_NAMES",
                "_SEVERITY",
            },
        )
    )
    got.update(_ast_hash_defs(disp_p, {"Disposition"}))
    # Phase 7 (#346) moved this unchanged leaf into state_rows and re-exports
    # it from state. Pin both its body and the binding consumers actually use.
    got.update(_ast_hash_defs(rows_p, {"_project_product_row"}))
    assert st._project_product_row is rows._project_product_row
    for name, expect in PINNED_DEPS.items():
        assert got[name] == expect, (name, got[name], expect)

    def H(s):
        return hashlib.sha256(s.encode()).hexdigest()

    assert H(repr(st._PASS_NAMES)) == PINNED_RUNTIME["_PASS_NAMES repr"]
    assert (
        H(repr(sorted((k.value, v) for k, v in st._SEVERITY.items())))
        == PINNED_RUNTIME["_SEVERITY repr"]
    )
    assert H(repr([(m.name, m.value) for m in st.PassOutcome])) == PINNED_RUNTIME["PassOutcome members"]
    assert (
        H(repr([(m.name, m.value) for m in st.FindingDiagnosticKind]))
        == PINNED_RUNTIME["FindingDiagnosticKind members"]
    )
    assert H(repr([(m.name, m.value) for m in Disposition])) == PINNED_RUNTIME["Disposition members"]


_LEGACY_STATE_FINDING_HASH = "86a280bd5ecd91f7db8ad8909d86fd854d368b43f50b2301b11dae9fc5120a97"


def _assert_state_finding_metadata_extension(tree):
    """W2-6 may append optional display metadata, never change control semantics."""
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "StateFinding")
    fields = [
        n
        for n in node.body
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", None) == "receipt_subtype"
    ]
    assert len(fields) == 1
    expected = ast.parse("receipt_subtype: str | None = None").body[0]
    assert ast.dump(fields[0], include_attributes=False) == ast.dump(expected, include_attributes=False)
    node.body.remove(fields[0])
    assert _digest(node) == _LEGACY_STATE_FINDING_HASH


def test_state_finding_adds_only_optional_receipt_metadata():
    tree = ast.parse((REPO_SRC / "code_forge/state.py").read_text())
    _assert_state_finding_metadata_extension(tree)


@pytest.mark.parametrize("mutation", ["metadata-default", "control-field", "control-method"])
def test_state_finding_extension_guard_rejects_control_changes(mutation):
    tree = ast.parse((REPO_SRC / "code_forge/state.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "StateFinding")
    if mutation == "control-method":
        node.body.append(ast.parse("def is_confirmed(self): return False").body[0])
    else:
        name = "receipt_subtype" if mutation == "metadata-default" else "disposition"
        field = next(
            n
            for n in node.body
            if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", None) == name
        )
        field.value = ast.Constant(value="CONFIRMED")
    with pytest.raises(AssertionError):
        _assert_state_finding_metadata_extension(tree)


def test_control_equivalence_with_and_without_projection_call(tmp_path):
    """Calling the presentation adapter must not change control outputs for a saved state."""
    from code_forge.sarif import format_summary, _build_run

    d = tmp_path / "d"
    d.mkdir()
    findings = [
        StateFinding("p", "pf", "L1", Disposition.CONFIRMED, "a.py", [1, 2], "d"),
        StateFinding(
            "l1-adversarial-invoke-fail",
            "invoke-fail-adversarial",
            "INFRA",
            Disposition.CONFIRMED,
            "<llm-invoke>",
            [0, 0],
            "cap",
            diagnostic_kind=__import__(
                "code_forge.state", fromlist=["FindingDiagnosticKind"]
            ).FindingDiagnosticKind.PROVIDER_CAPACITY,
            provider_failure={"kind": "truncated"},
        ),
    ]
    from code_forge.state import Verdict, Mode

    state = State(findings=findings, verdict=Verdict.FAIL, mode=Mode.CI)
    save_state(state, d / "state.json")
    loaded = load_state(d / "state.json")
    assert loaded is not None

    summary_a = format_summary(loaded)
    run_a = _build_run(loaded, {}, "test")
    bytes_a = (d / "state.json").read_bytes()

    proj = classify_report_records(loaded)
    assert proj.classification_complete is True

    summary_b = format_summary(loaded)
    run_b = _build_run(loaded, {}, "test")
    bytes_b = (d / "state.json").read_bytes()
    assert summary_a == summary_b
    assert run_a == run_b
    assert bytes_a == bytes_b
