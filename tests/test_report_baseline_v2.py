# SPDX-License-Identifier: Apache-2.0
"""Actual emitter differential; no v2 default or CLI rewiring."""

import copy
import io

import pytest

from code_forge import __version__, cli, report_baseline_v2 as factory
from code_forge.advisory import AdvisoryFinding
from code_forge.assertion_outcome import AssertionOutcome, AssertionStatus
from code_forge.manifest import EnvManifest, ManifestTier
from code_forge.report_baseline_v2 import RenderingInputs, SnapshotLimits, build_owned_report_pair
from code_forge.state import (
    State,
    Mode,
    Verdict,
    StateFinding,
    FindingDiagnosticKind,
    save_state,
    load_state,
)
from code_forge.disposition import Disposition

LIMITS = SnapshotLimits(10000, 1000000, 100)


def context(**changes):
    return RenderingInputs(**(dict(forge_version=__version__, tool_versions={}) | changes))


def fixture_state(kind):
    findings = []
    if kind in ("product", "unresolved", "mutation"):
        findings = [
            StateFinding(
                "p",
                "p",
                "L1",
                Disposition.UNCERTAIN if kind == "unresolved" else Disposition.CONFIRMED,
                "a.py",
                [1, 1],
                "finding",
            )
        ]
    if kind == "mutation":
        findings[0].id = "MUTATION_ERROR"
        findings[0].fingerprint = "mutation-evidence-error"
        findings[0].source = "INFRA"
        findings[0].diagnostic_kind = FindingDiagnosticKind.MUTATION_INFRA
    return State(
        mode=Mode.CI,
        verdict=Verdict.FAIL if kind == "infra" else Verdict.PASS,
        findings=findings,
        infra_errors=["x" * 301] if kind == "infra" else [],
    )


@pytest.mark.parametrize("kind", ["empty", "product", "unresolved", "mutation", "infra"])
@pytest.mark.parametrize("locator", [True, False])
def test_complete_baseline_matches_actual_emitter(tmp_path, monkeypatch, kind, locator):
    path = tmp_path / "state.json"
    save_state(fixture_state(kind), path)
    snapshot = load_state(path)
    manifest = EnvManifest(ManifestTier.ABSENT)
    outcome = AssertionOutcome(AssertionStatus.COMPLETED_EMPTY, 0)
    advisory = AdvisoryFinding("advice", "maintainability", "a.py", description="advice")
    captured = context(
        manifest=manifest,
        assertion_outcome=outcome,
        advisories=(advisory,),
        resolved_locator=str(path.resolve()) if locator else None,
    )
    monkeypatch.setattr(cli, "_load_state", lambda _: snapshot)
    monkeypatch.setattr(cli, "_load_advisories", lambda _: [advisory])
    if not locator:

        def missing(_):
            raise OSError("unavailable")

        monkeypatch.setattr(cli, "_infra_state_identity", missing)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)
    hook_observations = []
    cli._emit_ci_output(
        path,
        {},
        manifest=manifest,
        assertion_outcome=outcome,
        post_emit_hook=lambda: hook_observations.append((out.getvalue(), err.getvalue())),
    )
    pair = build_owned_report_pair(snapshot, captured, limits=LIMITS)
    assert pair.baseline.stdout == out.getvalue()
    assert pair.baseline.stderr == err.getvalue()
    assert hook_observations == [(pair.baseline.stdout, pair.baseline.stderr)]


def test_same_private_state_reaches_real_baseline_and_v2(monkeypatch):
    source = fixture_state("mutation")
    captured = context()
    seen = []
    baseline = factory._build_baseline
    project = factory.classify_report_records_v2

    def record_baseline(state, inputs):
        seen.append(("baseline", state, inputs))
        return baseline(state, inputs)

    def record_projection(state):
        seen.append(("projection", state))
        return project(state)

    monkeypatch.setattr(factory, "_build_baseline", record_baseline)
    monkeypatch.setattr(factory, "classify_report_records_v2", record_projection)
    pair = build_owned_report_pair(source, captured, limits=LIMITS)
    assert seen[0][1] is seen[1][1] and seen[0][1] is not source
    assert seen[0][2] is not captured
    assert [item[0] for item in seen] == ["baseline", "projection"]
    before = copy.deepcopy(pair)
    source.findings[0].description = "later"
    captured.tool_versions["later"] = "changed"
    assert pair == before


@pytest.mark.parametrize("error", [MemoryError, RecursionError, TypeError])
def test_copy_failures_propagate_before_builders(monkeypatch, error):
    def fail(*args):
        raise error()

    monkeypatch.setattr(factory.copy, "deepcopy", fail)
    with pytest.raises(error):
        build_owned_report_pair(fixture_state("empty"), context(), limits=LIMITS)


def test_snapshot_budget_before_deepcopy(monkeypatch):
    def forbidden(*args):
        pytest.fail("copy started before source refusal")

    monkeypatch.setattr(factory.copy, "deepcopy", forbidden)
    with pytest.raises(ValueError, match="character limit"):
        build_owned_report_pair(fixture_state("infra"), context(), limits=SnapshotLimits(10000, 10, 100))


def test_classification_unavailable_matches_real_emitter(tmp_path, monkeypatch):
    from code_forge import report_presentation

    path = tmp_path / "state.json"
    save_state(fixture_state("empty"), path)
    snapshot = load_state(path)
    monkeypatch.setattr(report_presentation, "_block_fits", lambda *args: False)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)
    cli._emit_ci_output(path, {})
    pair = build_owned_report_pair(snapshot, context(), limits=LIMITS)
    assert pair.baseline.stdout == out.getvalue()
    assert pair.baseline.stderr == err.getvalue()
    assert "classification unavailable" in pair.baseline.stderr


def test_hook_failure_remains_post_publication(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    save_state(fixture_state("empty"), path)
    snapshot = load_state(path)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)

    def failed_hook():
        raise RuntimeError("post-publication")

    with pytest.raises(RuntimeError, match="post-publication"):
        cli._emit_ci_output(path, {}, post_emit_hook=failed_hook)
    pair = build_owned_report_pair(snapshot, context(), limits=LIMITS)
    assert (out.getvalue(), err.getvalue()) == (pair.baseline.stdout, pair.baseline.stderr)


def test_tool_versions_captured_once_and_match_real_emitter(tmp_path, monkeypatch):
    from types import SimpleNamespace

    path = tmp_path / "state.json"
    save_state(fixture_state("empty"), path)
    snapshot = load_state(path)
    calls = []

    def capture(command, args):
        calls.append((command, args))
        return "1.2.3"

    monkeypatch.setattr(cli, "capture_tool_version", capture)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)
    cli._emit_ci_output(path, {"tool": SimpleNamespace(command="tool", args=["--version"])})
    pair = build_owned_report_pair(snapshot, context(tool_versions={"tool": "1.2.3"}), limits=LIMITS)
    assert calls == [("tool", ["--version"])]
    assert (pair.baseline.stdout, pair.baseline.stderr) == (out.getvalue(), err.getvalue())


def test_context_mutation_after_copy_does_not_change_owned_render(monkeypatch):
    source = fixture_state("product")
    manifest = EnvManifest(ManifestTier.ABSENT, dependencies={"dep": "old"})
    captured = context(tool_versions={"tool": "old"}, manifest=manifest)
    real = factory._build_baseline

    def mutate_external(state, inputs):
        source.findings[0].description = "new outside"
        captured.tool_versions["tool"] = "new outside"
        manifest.dependencies["dep"] = "new outside"
        assert state.findings[0].description == "finding"
        assert inputs.tool_versions == {"tool": "old"}
        assert inputs.manifest.dependencies == {"dep": "old"}
        return real(state, inputs)

    monkeypatch.setattr(factory, "_build_baseline", mutate_external)
    pair = build_owned_report_pair(source, captured, limits=LIMITS)
    assert "new outside" not in pair.baseline.stdout
    assert pair.projection.product[0].description == "finding"


def test_baseline_failure_does_not_start_v2(monkeypatch):
    def failed(*args):
        raise ValueError("baseline failure")

    def forbidden(*args):
        pytest.fail("optional classifier called")

    monkeypatch.setattr(factory, "_build_baseline", failed)
    monkeypatch.setattr(factory, "classify_report_records_v2", forbidden)
    with pytest.raises(ValueError, match="baseline failure"):
        build_owned_report_pair(fixture_state("empty"), context(), limits=LIMITS)


def test_invalid_baseline_does_not_start_v2(monkeypatch):
    from code_forge.report_preparation_v2 import FinalText

    original = FinalText("invalid baseline\n", "same\n")
    monkeypatch.setattr(factory, "_build_baseline", lambda *args: original)

    def forbidden(*args):
        pytest.fail("optional classifier called")

    monkeypatch.setattr(factory, "classify_report_records_v2", forbidden)
    pair = build_owned_report_pair(fixture_state("empty"), context(), limits=LIMITS)
    assert pair.baseline is original
    assert pair.projection is None


def test_new_pure_footer_failure_timing_is_not_claimed_as_legacy_atomicity(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    save_state(fixture_state("empty"), path)
    snapshot = load_state(path)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)

    def failed(*args, **kwargs):
        raise RuntimeError("footer failure")

    monkeypatch.setattr(cli, "_format_infra_lines", failed)
    with pytest.raises(RuntimeError, match="footer failure"):
        cli._emit_ci_output(path, {})
    assert out.getvalue() and err.getvalue()
    out.seek(0)
    out.truncate(0)
    err.seek(0)
    err.truncate(0)
    monkeypatch.setattr(factory, "_frozen_infra_text", failed)
    with pytest.raises(RuntimeError, match="footer failure"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)
    assert out.getvalue() == err.getvalue() == ""


def test_unknown_copy_hook_is_not_admitted_by_preflight():
    from dataclasses import dataclass

    @dataclass
    class Unexpected:
        def __deepcopy__(self, memo):
            pytest.fail("unreviewed copy hook executed")

    with pytest.raises(TypeError, match="unsupported snapshot object"):
        build_owned_report_pair(fixture_state("empty"), context(manifest=Unexpected()), limits=LIMITS)


def test_alias_dag_expansion_is_bounded_before_copy(monkeypatch):
    import tracemalloc

    value = ["payload"]
    for _ in range(16):
        value = [value, value]
    snapshot = fixture_state("product")
    snapshot.findings[0].provider_failure = {"kind": value}

    def forbidden(*args):
        pytest.fail("alias DAG reached copy")

    monkeypatch.setattr(factory.copy, "deepcopy", forbidden)
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="snapshot node limit"):
            build_owned_report_pair(snapshot, context(), limits=SnapshotLimits(1000, 1000000, 100))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1000000


def test_cycles_rejected_before_copy(monkeypatch):
    cyclic = []
    cyclic.append(cyclic)
    snapshot = fixture_state("product")
    snapshot.findings[0].provider_failure = {"kind": cyclic}

    def forbidden(*args):
        pytest.fail("cycle reached copy")

    monkeypatch.setattr(factory.copy, "deepcopy", forbidden)
    with pytest.raises(ValueError, match="cyclic snapshot"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)


def test_unknown_enum_copy_callback_is_not_admitted():
    from enum import Enum

    class Unexpected(Enum):
        VALUE = "value"

        def __deepcopy__(self, memo):
            pytest.fail("unreviewed enum copy hook executed")

    with pytest.raises(TypeError, match="unsupported snapshot object"):
        build_owned_report_pair(
            fixture_state("empty"), context(manifest=Unexpected.VALUE), limits=LIMITS
        )


def test_repeated_finding_object_is_allowed_with_occurrence_budget():
    snapshot = fixture_state("product")
    snapshot.findings.append(snapshot.findings[0])
    pair = build_owned_report_pair(snapshot, context(), limits=LIMITS)
    assert [row.index for row in pair.projection.product] == [0, 1]


@pytest.mark.parametrize("path_case", ["unicode-controls", "too-long", "many-errors"])
def test_frozen_footer_matches_real_locator_and_truncation(tmp_path, monkeypatch, path_case):
    directory = tmp_path
    if path_case == "too-long":
        directory = tmp_path / ("a" * 150) / ("b" * 150)
        directory.mkdir(parents=True)
    path = directory / ("state-汉\n.json" if path_case == "unicode-controls" else "state.json")
    source = fixture_state("infra")
    if path_case == "many-errors":
        source.infra_errors = [" first\n message ", "", "second", "third", "fourth", "fifth", "sixth"]
    save_state(source, path)
    snapshot = load_state(path)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)
    cli._emit_ci_output(path, {})
    pair = build_owned_report_pair(
        snapshot, context(resolved_locator=str(path.resolve())), limits=LIMITS
    )
    assert (pair.baseline.stdout, pair.baseline.stderr) == (out.getvalue(), err.getvalue())


def test_exact_state_extra_callback_rejected_before_deepcopy():
    class Callback:
        def __deepcopy__(self, memo):
            pytest.fail("extra attribute callback executed")

    snapshot = fixture_state("empty")
    snapshot.unreviewed_extra = Callback()
    with pytest.raises(TypeError, match="unexpected snapshot attributes"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)


def test_exact_state_extra_oversize_data_rejected_without_copy():
    import tracemalloc

    snapshot = fixture_state("empty")
    snapshot.unreviewed_extra = "x" * 8_000_000
    tracemalloc.start()
    try:
        with pytest.raises(TypeError, match="unexpected snapshot attributes"):
            build_owned_report_pair(snapshot, context(), limits=LIMITS)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000


def test_exact_finding_extra_attribute_rejected():
    snapshot = fixture_state("product")
    snapshot.findings[0].unreviewed_extra = object()
    with pytest.raises(TypeError, match="unexpected snapshot attributes"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)


def test_findings_subclass_length_hook_rejected_before_access():
    calls = []

    class CallbackList(list):
        def __len__(self):
            calls.append("called")
            return 0

    snapshot = fixture_state("empty")
    snapshot.findings = CallbackList()
    with pytest.raises(TypeError, match="exact list"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)
    assert calls == []


def test_approved_enum_instance_copy_hook_never_called(monkeypatch):
    def forbidden(memo):
        pytest.fail("enum copy hook executed")

    monkeypatch.setattr(Mode.CI, "__deepcopy__", forbidden, raising=False)
    pair = build_owned_report_pair(fixture_state("empty"), context(), limits=LIMITS)
    assert pair.baseline.stdout


def test_real_legacy_survivor_snapshot_retains_opaque_bytes_without_archive(tmp_path, monkeypatch):
    import json

    path = tmp_path / "state.json"
    source = fixture_state("product")
    source.consecutive_survivor_rounds = 2
    save_state(source, path)
    document = json.loads(path.read_text())
    document.pop("mutation_survivor_counter_version")
    path.write_text(json.dumps(document))
    snapshot = load_state(path)
    original_bytes = path.read_bytes()
    assert snapshot._legacy_survivor_state == original_bytes
    assert snapshot._legacy_survivor_state is not None
    original_migration = copy.deepcopy(snapshot.survivor_counter_migration)
    observed = []
    real = factory._build_baseline

    def inspect_owned(state, inputs):
        observed.append(state._legacy_survivor_state)
        return real(state, inputs)

    monkeypatch.setattr(factory, "_build_baseline", inspect_owned)
    pair = build_owned_report_pair(snapshot, context(), limits=LIMITS)
    assert pair.baseline.stdout
    assert observed[0] is snapshot._legacy_survivor_state
    assert snapshot._legacy_survivor_state == original_bytes
    assert snapshot.survivor_counter_migration == original_migration
    assert path.read_bytes() == original_bytes
    assert sorted(item.name for item in tmp_path.iterdir()) == ["state.json"]


def test_opaque_legacy_bytes_exception_does_not_admit_other_bytes_or_subclasses():
    class BytesWithCallback(bytes):
        def __deepcopy__(self, memo):
            pytest.fail("unreviewed bytes subclass callback")

    source = fixture_state("empty")
    source._legacy_survivor_state = BytesWithCallback(b"legacy")
    with pytest.raises(TypeError, match="unsupported snapshot object"):
        build_owned_report_pair(source, context(), limits=LIMITS)
    source._legacy_survivor_state = None
    with pytest.raises(TypeError, match="unsupported snapshot object"):
        build_owned_report_pair(source, context(manifest=b"not a manifest"), limits=LIMITS)


def test_metaclass_equality_forgery_is_not_admitted():
    """Admission asks type identity, so a class claiming equality gains nothing."""

    class Forgiving(type):
        def __eq__(cls, other):
            return True

        def __hash__(cls):
            return 0

    class Forged(metaclass=Forgiving):
        def __deepcopy__(self, memo):
            pytest.fail("unreviewed copy hook executed")

    with pytest.raises(TypeError, match="unsupported snapshot object"):
        build_owned_report_pair(fixture_state("empty"), context(manifest=Forged()), limits=LIMITS)


def test_str_subclass_attribute_key_runs_no_hash_or_eq():
    """The key type is checked before any set operation can call its protocol."""
    seen = []

    class Hostile(str):
        def __hash__(self):
            seen.append("hash")
            return str.__hash__(self)

        def __eq__(self, other):
            seen.append("eq")
            return str.__eq__(self, other)

    snapshot = fixture_state("empty")
    # Swap one existing key for a hostile one so the count still matches; a
    # ninth key would be stopped by the length check before the key type runs.
    original = next(iter(snapshot.__dict__))
    value = snapshot.__dict__.pop(original)
    snapshot.__dict__[Hostile(original)] = value
    # The dict insert above hashed the key; that is the test's own doing.
    seen.clear()
    with pytest.raises(TypeError, match="unexpected snapshot attributes"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)
    assert seen == []


def test_non_string_attribute_key_is_rejected():
    snapshot = fixture_state("empty")
    snapshot.__dict__[7] = object()
    with pytest.raises(TypeError, match="unexpected snapshot attributes"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)


def test_locator_class_claiming_equality_is_not_an_exact_str():
    """`type(x) not in (str, NoneType)` asks the class; a forged __eq__ answers yes."""

    class Forgiving(type):
        def __eq__(cls, other):
            return True

        def __hash__(cls):
            return 0

    class LyingStr(str, metaclass=Forgiving):
        pass

    with pytest.raises(TypeError, match="exact str"):
        build_owned_report_pair(
            fixture_state("empty"), context(resolved_locator=LyingStr("/x")), limits=LIMITS
        )


def test_rejection_runs_no_copy_hook(monkeypatch):
    """A refused object must not reach deepcopy at all."""
    calls = []

    class Forged:
        def __deepcopy__(self, memo):
            calls.append("deepcopy")
            return self

        def __iter__(self):
            calls.append("iter")
            return iter(())

        def __len__(self):
            calls.append("len")
            return 0

    monkeypatch.setattr(factory.copy, "deepcopy", lambda *a, **k: pytest.fail("copy reached"))
    with pytest.raises(TypeError, match="unsupported snapshot object"):
        build_owned_report_pair(fixture_state("empty"), context(manifest=Forged()), limits=LIMITS)
    assert calls == []


def test_rejection_stops_before_baseline_and_classifier(monkeypatch):
    monkeypatch.setattr(factory, "_build_baseline_parts", lambda *a: pytest.fail("baseline started"))
    monkeypatch.setattr(
        factory, "classify_report_records_v2", lambda *a: pytest.fail("classifier started")
    )
    monkeypatch.setattr(
        factory, "classify_report_records", lambda *a: pytest.fail("v1 classifier started")
    )
    with pytest.raises(TypeError, match="unsupported snapshot object"):
        build_owned_report_pair(fixture_state("empty"), context(manifest=object()), limits=LIMITS)


def test_hostile_object_in_rendering_inputs_is_rejected(monkeypatch):
    monkeypatch.setattr(factory.copy, "deepcopy", lambda *a, **k: pytest.fail("copy reached"))
    hostile = {"a": {"b": object()}}
    # The position constraint rejects the map before the generic walk reaches
    # the hostile value, so either message is a correct refusal here.
    with pytest.raises(
        TypeError, match="unexpected snapshot field: tool_versions|unsupported snapshot object"
    ):
        build_owned_report_pair(fixture_state("empty"), context(tool_versions=hostile), limits=LIMITS)


def test_hostile_object_in_state_findings_is_rejected(monkeypatch):
    monkeypatch.setattr(factory.copy, "deepcopy", lambda *a, **k: pytest.fail("copy reached"))
    snapshot = fixture_state("product")
    snapshot.findings[0].provider_failure = {"kind": object()}
    with pytest.raises(TypeError, match="unsupported snapshot object"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)


def test_dataclass_fields_hook_is_not_run_on_a_refused_object():
    """fields(instance) would call .values() on an attacker-supplied object.

    __dataclass_fields__ is a plain class attribute, so an instance-dict entry of
    that name wins the getattr and dataclasses.fields() then calls values() on it.
    The refusal must not run that hook.
    """
    calls = []

    class Hostile:
        def values(self):
            calls.append("values")
            return []

    snapshot = fixture_state("empty")
    snapshot.__dict__["__dataclass_fields__"] = Hostile()
    with pytest.raises(TypeError, match="unexpected snapshot attributes"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)
    assert calls == []


def test_state_subclass_is_refused():
    """isinstance would admit a subclass whose copy hook then runs."""

    class Sneaky(State):
        def __deepcopy__(self, memo):
            pytest.fail("subclass copy hook executed")

    sneaky = Sneaky(mode=Mode.CI, verdict=Verdict.PASS, findings=[])
    with pytest.raises(TypeError, match="expected State"):
        build_owned_report_pair(sneaky, context(), limits=LIMITS)


def test_finding_subclass_is_refused():
    class SneakyFinding(StateFinding):
        def __deepcopy__(self, memo):
            pytest.fail("subclass copy hook executed")

    snapshot = fixture_state("empty")
    snapshot.findings.append(SneakyFinding("p", "p", "L1", Disposition.CONFIRMED, "a.py", [1, 1], "f"))
    with pytest.raises(TypeError, match="unsupported snapshot object"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)


def test_rendering_inputs_subclass_is_refused():
    class SneakyInputs(RenderingInputs):
        def __deepcopy__(self, memo):
            pytest.fail("subclass copy hook executed")

    sneaky = SneakyInputs(forge_version=__version__, tool_versions={})
    with pytest.raises(TypeError, match="expected State"):
        build_owned_report_pair(fixture_state("empty"), sneaky, limits=LIMITS)


def test_deep_nesting_is_refused_before_deepcopy_recurses():
    """The budget bounds breadth; depth needs its own bound.

    Without it preflight admits a deep chain and copy.deepcopy raises
    RecursionError from inside the copy it was supposed to guard.
    """
    snapshot = fixture_state("product")
    deep = {}
    cursor = deep
    for _ in range(400):
        nxt = {}
        cursor["k"] = nxt
        cursor = nxt
    snapshot.findings[0].provider_failure = deep
    with pytest.raises(ValueError, match="nesting depth"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)


def test_foreign_enum_in_the_verdict_position_is_refused():
    """An admitted enum is not automatically the right enum for the slot."""
    snapshot = fixture_state("empty")
    snapshot.verdict = ManifestTier.ABSENT
    with pytest.raises(TypeError, match="unexpected snapshot field: verdict"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)


def test_foreign_enum_in_the_disposition_position_is_refused():
    snapshot = fixture_state("product")
    snapshot.findings[0].disposition = ManifestTier.ABSENT
    with pytest.raises(TypeError, match="unexpected snapshot field: disposition"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)


def test_non_string_tool_version_is_refused():
    with pytest.raises(TypeError, match="unexpected snapshot field: tool_versions"):
        build_owned_report_pair(
            fixture_state("empty"), context(tool_versions={"tool": 1}), limits=LIMITS
        )


def test_non_finite_float_is_refused():
    """json.dumps(allow_nan=False) would raise far from this boundary."""
    snapshot = fixture_state("product")
    snapshot.findings[0].provider_failure = {"score": float("nan")}
    with pytest.raises(ValueError, match="non-finite"):
        build_owned_report_pair(snapshot, context(), limits=LIMITS)
