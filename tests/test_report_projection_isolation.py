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
    "reporting_product_findings": "b69f8493dc02e5f2206a16d2934767104c9fb07328d6a821e050769cf8e47d6e",
    "is_provider_diagnostic": "205a477f709e149cf636897f6350c28d6ae2050d61a21b0668880396d9b5ae91",
    "is_provider_capacity": "f664f56646baed3b39c93d8ae533b9721d766bc47b76b0feb09d8fc6cd3a816c",
    "is_receipt_audit": "ccd447f4f80c6284239d9c254319abf4a00d56e5ad44f93fdfc38f0276d48e3b",
    "product_round_history": "b472ec5e54553f2d57e3494eb0275dc9c71e473efc46bd0a5b0b9e72c088299c",
    "active_findings": "bf3c4b60b8afac6cf869ba45a29fa52cf82c9b365510ebd492912f5ab1382116",
    "_count": "519752fc8df2dddb973bde31cc57eefd8809d1eabb38a2f2c20840c73b3cdc0b",
    "_confirmed_hold_fingerprints": "184e0b3fcd0bc4d06457fe721f81d1a3ba81b95d3a88d38109129ec7582d3e19",
    "_product_history": "d94e9caaf836cd307e8f7bd29749cfe65aef1aa10b5a87b095232510f8db41e6",
}
PINNED_DEPS = {
    "is_mutation_infra_diagnostic": "736dcfb05cc60950072cb244c9133fbdd02bcac0aa1cac521767710b2d287ed9",
    "_project_product_row": "a4651f9c099b286a2bd9f603c4a0af2f566a7ef92f8ad48c44bb858058066173",
    "derive_pass_outcomes": "db726ccdf6123503e268921e4ee6c504b59bb077d8d325371e85fecf6ed55e51",
    "_host_diagnostic_shape": "1c1bf71383382ca44638b4ae0046d854f7324a5ecf030608ab7ce03ca69557e1",
    "PassOutcome": "f388e795081e7b34ac65ef463ffc711fe59ee64b0aebf82b784b65aceab74b09",
    "FindingDiagnosticKind": "a91891577bbd53345190161d05114fadd7c8afc2577e3d48ab0298f6485a787a",
    "StateFinding": "33f58d435d10f884ac3b661c3b382d6ba8ac3da4c9e40e8c0d60a719a8fd934f",
    "_PASS_NAMES": "9928d9693da1c9706ff45658c83bb70221d29b4f7e8115f204ef11769964ec3c",
    "_SEVERITY": "b08e25560456dc875217b6bc22d6b650a87194c2d2cef60a871fa9bbffba8305",
    "Disposition": "c88655eef53e38c6fe142356996a73599766bb48667077e79fe64a2d420a5eb6",
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


def _ast_hash_defs(path: Path, names: set[str]) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in names
        ):
            # Prefer the first (outermost walk order); methods are found via walk.
            out.setdefault(
                node.name,
                hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest(),
            )
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if getattr(t, "id", None) in names:
                    out[t.id] = hashlib.sha256(
                        ast.dump(node, include_attributes=False).encode()
                    ).hexdigest()
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) in names:
            out[node.target.id] = hashlib.sha256(
                ast.dump(node, include_attributes=False).encode()
            ).hexdigest()
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
