"""Phase 1 isolation / frozen-helper / whitelist tests (Forge #332 slice §5)."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from code_forge.report_projection import classify_report_records
from code_forge.state import Disposition, State, StateFinding, save_state, load_state

REPO_SRC = Path(__file__).resolve().parents[1] / "src"
PINNED_HELPER = {
    "reporting_product_findings": "6631152a8f1ca8151a7f7f098402f0a5be95b819ecd0db917c5ecf94c0dd6d63",
    "is_provider_diagnostic": "205a477f709e149cf636897f6350c28d6ae2050d61a21b0668880396d9b5ae91",
    "is_provider_capacity": "f664f56646baed3b39c93d8ae533b9721d766bc47b76b0feb09d8fc6cd3a816c",
    "is_receipt_audit": "ccd447f4f80c6284239d9c254319abf4a00d56e5ad44f93fdfc38f0276d48e3b",
    "product_round_history": "31f6bc635fa9058a515d6c73340f3d6a91c6e3f7696abf4fe1f502ce87755621",
    "active_findings": "bf3c4b60b8afac6cf869ba45a29fa52cf82c9b365510ebd492912f5ab1382116",
    "_count": "519752fc8df2dddb973bde31cc57eefd8809d1eabb38a2f2c20840c73b3cdc0b",
    "_confirmed_hold_fingerprints": "184e0b3fcd0bc4d06457fe721f81d1a3ba81b95d3a88d38109129ec7582d3e19",
    "_product_history": "d94e9caaf836cd307e8f7bd29749cfe65aef1aa10b5a87b095232510f8db41e6",
}
PINNED_DEPS = {
    "derive_pass_outcomes": "db726ccdf6123503e268921e4ee6c504b59bb077d8d325371e85fecf6ed55e51",
    "_host_diagnostic_shape": "1c1bf71383382ca44638b4ae0046d854f7324a5ecf030608ab7ce03ca69557e1",
    "PassOutcome": "f388e795081e7b34ac65ef463ffc711fe59ee64b0aebf82b784b65aceab74b09",
    "FindingDiagnosticKind": "74c2c5f0e532ede2a8bd5bdeef3eb69d9eb6dac5b69c82b33875404d65364703",
    "StateFinding": "33f58d435d10f884ac3b661c3b382d6ba8ac3da4c9e40e8c0d60a719a8fd934f",
    "_PASS_NAMES": "9928d9693da1c9706ff45658c83bb70221d29b4f7e8115f204ef11769964ec3c",
    "_SEVERITY": "b08e25560456dc875217b6bc22d6b650a87194c2d2cef60a871fa9bbffba8305",
    "Disposition": "c88655eef53e38c6fe142356996a73599766bb48667077e79fe64a2d420a5eb6",
}
PINNED_RUNTIME = {
    "_PASS_NAMES repr": "8ecec8b65d424f029b1ae7099427ed177fd6285e552d4abcfb78ccbfc63e81d9",
    "_SEVERITY repr": "52599584c59a23d84f2a291033d604102a96cdb94380517494dcdd49d53dfc7e",
    "PassOutcome members": "fdd99a6aba49c5f64bba3f1550508db91f714944afee5c087fe5d02e44baa266",
    "FindingDiagnosticKind members": "218a2b34e50a15be572106d74be47514fdc6e31722f24486377e839a864fe7d1",
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


def test_control_modules_do_not_import_report_projection():
    root = REPO_SRC
    files = sorted(
        p
        for p in (root / "code_forge").rglob("*.py")
        if "__pycache__" not in p.parts and p.name != "report_projection.py"
    )
    files.append(root / "code_forge_launcher.py")
    hits = []
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for n in ast.walk(tree):
            if isinstance(n, ast.Import) and any(
                a.name == "code_forge.report_projection" or a.name.endswith(".report_projection")
                for a in n.names
            ):
                hits.append((str(p), n.lineno, "import"))
            if isinstance(n, ast.ImportFrom):
                mod = n.module or ""
                if (
                    mod == "code_forge.report_projection"
                    or mod.endswith(".report_projection")
                    or mod == "report_projection"
                ):
                    hits.append((str(p), n.lineno, "importfrom-mod"))
                if any(a.name in ("report_projection", "classify_report_records") for a in n.names):
                    hits.append((str(p), n.lineno, "importfrom-name"))
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                if "report_projection" in n.value or "classify_report_records" in n.value:
                    hits.append((str(p), n.lineno, "string"))
    assert hits == [], hits
    assert len(files) >= 130


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
    from code_forge.disposition import Disposition

    state_p = REPO_SRC / "code_forge" / "state.py"
    disp_p = REPO_SRC / "code_forge" / "disposition.py"
    got = {}
    got.update(
        _ast_hash_defs(
            state_p,
            {
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
    """Calling the unwired adapter must not change control outputs for a saved state."""
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
