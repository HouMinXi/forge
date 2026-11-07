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
    "reporting_product_findings": "a6114ae324ae700f9a8b962dd9801ca6bcdc1347dd42fa11067429643e56f818",
    "is_provider_diagnostic": "d7b3aa5422d93e79e00a2828410c5ed91e305a5380a793a5a2708725b8b71a2e",
    "is_provider_capacity": "a24332bfca61068b21687d2d5862812175e4c52e689cfdcbed674ec20d97e255",
    "is_receipt_audit": "1b55ad47cddf6a133e038cdaa5bd50a7f548520c6be06e24361d162c5e607dbb",
    "product_round_history": "bd87f551c66aa0af2c1ec39bcb703ef0f3a3a304e3555946017ae9ecb33a2462",
    "active_findings": "3e6ff12b0adcc91c05b93b9b6d7b0a043886d8c665f9f195581f0bfa45788ad6",
    "_count": "5ecc43da194848975ce4ff13ceee265d8e8d9839a359eb0716347a4b3de9e954",
    "_confirmed_hold_fingerprints": "ea36344534479cc1bc8c4fb9af9a2f9807fd8de306fac693c1eeeb8a20263e8b",
    "_product_history": "bab32ce551bafd840ddc6e2254f541eccc9474a2ff33153faad1e13a5d9fa86f",
}
PINNED_DEPS = {
    "is_mutation_infra_diagnostic": "0420d92fbebc8f2e94a8d162539141147279631869f0a6c1f4e939f91174fe7b",
    "_project_product_row": "496e01e6153199ba47f5366ea1e63b5552c8e0d8af23ad9a0a05248a810596af",
    "derive_pass_outcomes": "0480d56ccb1b3c8b8697179c5c6c247cec0e3d6086714069ff8611afd35868fc",
    "_host_diagnostic_shape": "53a53358a0ce9ba5133ac6c837af6cc833d3a3cc0b303a0bc803063d4317a563",
    "PassOutcome": "2c66457d86070becff16b6a627310b3d28a54e2551dff2e17965bdf91d8fbae7",
    "FindingDiagnosticKind": "88faed9e2da9c99dabb6b1e2bfba71ccc9d7489df427d96f0f75940572b3da73",
    # W2-6 adds only receipt_subtype: str | None = None; the old class AST
    # remains pinned separately below, including all control fields/methods.
    "StateFinding": "56a7c43a3f36bbd4af762ea6aa586e9a77a08c1e9c8ed9139a35dba861a0b6f5",
    "_PASS_NAMES": "81b557eb202cc74ccbcb5a1e71ec638a823daa3c8e27e575e4c76e725b8cfdd3",
    "_SEVERITY": "0c46db3bb670deb1d5dfde3e0cd1e629a6133aba6d55fca18670708106078cad",
    "Disposition": "8a86a07368699136b4085f5396bf01b57d542c3e67de214790f6b3e8df08a251",
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

    `ast.unparse` regenerates source from the tree and whitespace is collapsed,
    so formatting is still ignored while the version is not part of the input.
    Verified identical on 3.12 and 3.14 for all nineteen pinned definitions.
    """
    return hashlib.sha256(" ".join(ast.unparse(node).split()).encode()).hexdigest()


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


_LEGACY_STATE_FINDING_HASH = "613859cd52559962a29a7152813ed5c64c5703320d9a659dd0ba9ead16dee9b6"


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
