# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Unit tests for e2e_check.py: both layers, config validation, and teeth."""

import pytest
import unidiff

from forge.disposition import Disposition
from forge.e2e_check import (
    check_layer_1,
    check_layer_2,
    detect_signature_changes,
    find_e2e_artifacts,
    group_source_files,
    load_components_yaml,
    run_e2e_check,
)
from forge.errors import ComponentsConfigError


# ---------------------------------------------------------------------------
# Shared diff fixtures as module-level constants.
# All hunk @@ counts are exact so unidiff parses cleanly.
# ---------------------------------------------------------------------------

# Single file, adds a Python def.
_DIFF_PY_DEF = (
    "diff --git a/a/x.py b/a/x.py\n"
    "--- a/a/x.py\n"
    "+++ b/a/x.py\n"
    "@@ -1,1 +1,3 @@ class Foo:\n"
    " class Foo:\n"
    "+    def bar(self, x: int) -> None:\n"
    "+        pass\n"
)

# Single file, adds a plain assignment -- no signature.
_DIFF_NO_SIG = (
    "diff --git a/a/x.py b/a/x.py\n"
    "--- a/a/x.py\n"
    "+++ b/a/x.py\n"
    "@@ -1,1 +1,2 @@\n"
    " x = 1\n"
    "+y = 2\n"
)

# Deleted file.
_DIFF_REMOVED = (
    "diff --git a/a/x.py b/a/x.py\n"
    "deleted file mode 100644\n"
    "--- a/a/x.py\n"
    "+++ /dev/null\n"
    "@@ -1,2 +0,0 @@\n"
    "-def foo():\n"
    "-    pass\n"
)

# Shell function added -- matches _SH_FUNC_RE.
_DIFF_SH_FUNC = (
    "diff --git a/scripts/run.sh b/scripts/run.sh\n"
    "--- a/scripts/run.sh\n"
    "+++ b/scripts/run.sh\n"
    "@@ -1,1 +1,4 @@\n"
    " #!/bin/bash\n"
    "+setup() {\n"
    "+    echo done\n"
    "+}\n"
)

# Multiline def: section_header contains "def foo(", added line is a param.
_DIFF_SECTION_HEADER = (
    "diff --git a/a/x.py b/a/x.py\n"
    "--- a/a/x.py\n"
    "+++ b/a/x.py\n"
    "@@ -10,3 +10,4 @@ def foo(\n"
    "     b: int,\n"
    "+    c: str,\n"
    " ) -> None:\n"
    "     pass\n"
)

# Two files, two dirs, adds a def in a/ -- multi-group + signature.
_DIFF_MULTIGROUP_SIG = (
    "diff --git a/a/x.py b/a/x.py\n"
    "--- a/a/x.py\n"
    "+++ b/a/x.py\n"
    "@@ -1,1 +1,3 @@ class A:\n"
    " x = 1\n"
    "+def helper(a, b):\n"
    "+    return a + b\n"
    "diff --git a/b/y.py b/b/y.py\n"
    "--- a/b/y.py\n"
    "+++ b/b/y.py\n"
    "@@ -1,1 +1,2 @@\n"
    " y = 2\n"
    "+z = 3\n"
)

# Two files, two dirs, no signature added.
_DIFF_MULTIGROUP_NO_SIG = (
    "diff --git a/a/x.py b/a/x.py\n"
    "--- a/a/x.py\n"
    "+++ b/a/x.py\n"
    "@@ -1,1 +1,2 @@\n"
    " x = 1\n"
    "+y = 2\n"
    "diff --git a/b/y.py b/b/y.py\n"
    "--- a/b/y.py\n"
    "+++ b/b/y.py\n"
    "@@ -1,1 +1,2 @@\n"
    " y = 2\n"
    "+z = 3\n"
)

# Two-component diff: common + bonding.
_DIFF_COMMON_BONDING = (
    "diff --git a/common/foo.sh b/common/foo.sh\n"
    "--- a/common/foo.sh\n"
    "+++ b/common/foo.sh\n"
    "@@ -1,1 +1,2 @@\n"
    " x=1\n"
    "+y=2\n"
    "diff --git a/bonding/bar.sh b/bonding/bar.sh\n"
    "--- a/bonding/bar.sh\n"
    "+++ b/bonding/bar.sh\n"
    "@@ -1,1 +1,2 @@\n"
    " a=1\n"
    "+b=2\n"
)


def _make_components_yaml(tmp_path, content):
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir(exist_ok=True)
    (forge_dir / "components.yaml").write_text(content)


# ===========================================================================
# Group A -- detect_signature_changes
# ===========================================================================

class TestDetectSignatureChanges:
    """detect_signature_changes: two-arm detection (added-line + section_header)."""

    def test_empty_diff_returns_empty(self):
        assert detect_signature_changes("") == set()

    def test_garbage_diff_returns_empty(self):
        # Malformed input must not raise; graceful empty return.
        assert detect_signature_changes("not a diff at all") == set()

    def test_python_def_added_line_detected(self):
        result = detect_signature_changes(_DIFF_PY_DEF)
        assert "a/x.py" in result

    def test_no_added_signature_returns_empty(self):
        result = detect_signature_changes(_DIFF_NO_SIG)
        assert result == set()

    def test_section_header_arm_fires_for_multiline_def(self):
        # Added line is a parameter, but section_header contains "def foo(".
        result = detect_signature_changes(_DIFF_SECTION_HEADER)
        assert "a/x.py" in result

    def test_removed_file_excluded(self):
        result = detect_signature_changes(_DIFF_REMOVED)
        assert "a/x.py" not in result

    def test_shell_function_detected(self):
        result = detect_signature_changes(_DIFF_SH_FUNC)
        assert "scripts/run.sh" in result

    def test_flat_shell_no_section_header_no_sig(self):
        # Variable assignment -- no function def, empty section_header.
        diff = (
            "diff --git a/run.sh b/run.sh\n"
            "--- a/run.sh\n"
            "+++ b/run.sh\n"
            "@@ -1,1 +1,2 @@\n"
            " #!/bin/bash\n"
            "+VAR=1\n"
        )
        result = detect_signature_changes(diff)
        assert result == set()

    def test_section_header_attribute_exists_on_hunks(self):
        """Canary: unidiff Hunk must expose .section_header.

        If a library upgrade drops the attribute, Layer 1 Arm 2 silently
        falls back to Arm 1 only. This assertion surfaces the regression
        at test time rather than as a production false-negative.
        """
        patchset = unidiff.PatchSet(_DIFF_MULTIGROUP_SIG)
        for patched_file in patchset:
            for hunk in patched_file:
                assert hasattr(hunk, "section_header"), (
                    "unidiff Hunk missing .section_header; "
                    "check library version pin in pyproject.toml"
                )


# ===========================================================================
# Group B -- group_source_files
# ===========================================================================

class TestGroupSourceFiles:
    """group_source_files: segment-based grouping with component override."""

    def test_two_top_dirs_produce_two_groups(self):
        result = group_source_files(["a/x.py", "b/y.py"])
        assert set(result.keys()) == {"a", "b"}

    def test_nested_files_same_dir_one_group(self):
        result = group_source_files(["a/x.py", "a/sub/y.py"])
        assert set(result.keys()) == {"a"}

    def test_tests_dir_excluded_by_default(self):
        result = group_source_files(["a/x.py", "tests/test_a.py"])
        assert "a" in result
        assert "tests" not in result

    def test_all_test_dir_variants_excluded(self):
        # Regression guard: exclusion set covers {test, tests, spec} -- not just "tests".
        files = ["a/x.py", "test/a.py", "spec/a.py", "tests/a.py"]
        result = group_source_files(files)
        assert set(result.keys()) == {"a"}

    def test_components_map_assigns_matching_component(self):
        comps = {"foo": ["a/**", "b/**"]}
        result = group_source_files(["a/x.py", "b/y.py"], components=comps)
        assert set(result.keys()) == {"foo"}

    def test_exclude_test_dirs_false_keeps_tests(self):
        result = group_source_files(
            ["a/x.py", "tests/test_a.py"], exclude_test_dirs=False
        )
        assert "tests" in result
        assert "a" in result


# ===========================================================================
# Group C -- load_components_yaml
# ===========================================================================

class TestLoadComponentsYaml:
    """load_components_yaml: absence + all schema validation branches."""

    def test_absent_file_returns_none(self, tmp_path):
        assert load_components_yaml(tmp_path) is None

    def test_valid_minimal_yaml_returns_dict_with_default_patterns(
        self, tmp_path
    ):
        _make_components_yaml(tmp_path, (
            "version: 1\n"
            "components:\n"
            "  alpha:\n"
            "    paths: [alpha/**]\n"
        ))
        result = load_components_yaml(tmp_path)
        assert isinstance(result, dict)
        assert "e2e_patterns" in result

    def test_wrong_version_raises_with_keyword(self, tmp_path):
        _make_components_yaml(tmp_path, (
            "version: 2\n"
            "components:\n"
            "  alpha:\n"
            "    paths: [alpha/**]\n"
        ))
        with pytest.raises(ComponentsConfigError, match="version"):
            load_components_yaml(tmp_path)

    def test_undefined_depends_on_raises_naming_typo(self, tmp_path):
        _make_components_yaml(tmp_path, (
            "version: 1\n"
            "components:\n"
            "  alpha:\n"
            "    paths: [alpha/**]\n"
            "    depends_on: [missing_comp]\n"
        ))
        with pytest.raises(ComponentsConfigError, match="missing_comp"):
            load_components_yaml(tmp_path)

    def test_self_reference_raises_with_keyword(self, tmp_path):
        _make_components_yaml(tmp_path, (
            "version: 1\n"
            "components:\n"
            "  alpha:\n"
            "    paths: [alpha/**]\n"
            "    depends_on: [alpha]\n"
        ))
        with pytest.raises(ComponentsConfigError, match="self"):
            load_components_yaml(tmp_path)

    def test_cycle_raises_with_keyword(self, tmp_path):
        _make_components_yaml(tmp_path, (
            "version: 1\n"
            "components:\n"
            "  alpha:\n"
            "    paths: [alpha/**]\n"
            "    depends_on: [beta]\n"
            "  beta:\n"
            "    paths: [beta/**]\n"
            "    depends_on: [alpha]\n"
        ))
        with pytest.raises(ComponentsConfigError, match="cycle"):
            load_components_yaml(tmp_path)

    def test_e2e_absent_ok_unknown_component_raises(self, tmp_path):
        _make_components_yaml(tmp_path, (
            "version: 1\n"
            "components:\n"
            "  alpha:\n"
            "    paths: [alpha/**]\n"
            "e2e_absent_ok:\n"
            "  - component: nonexistent\n"
        ))
        with pytest.raises(ComponentsConfigError, match="undefined"):
            load_components_yaml(tmp_path)

    def test_data_paths_unknown_component_raises_naming_component(
        self, tmp_path
    ):
        _make_components_yaml(tmp_path, (
            "version: 1\n"
            "components:\n"
            "  alpha:\n"
            "    paths: [alpha/**]\n"
            "data_paths:\n"
            "  - [alpha, ghost]\n"
        ))
        with pytest.raises(ComponentsConfigError, match="ghost"):
            load_components_yaml(tmp_path)

    def test_malformed_yaml_raises(self, tmp_path):
        _make_components_yaml(tmp_path, "version: 1\ncomponents: [\nbroken yaml")
        with pytest.raises(ComponentsConfigError):
            load_components_yaml(tmp_path)

    def test_load_components_yaml_data_paths_not_list_raises(self, tmp_path):
        """data_paths declared as a non-list raises ComponentsConfigError."""
        _make_components_yaml(tmp_path, (
            "version: 1\n"
            "components:\n"
            "  a:\n"
            "    paths: [a/**]\n"
            "  b:\n"
            "    paths: [b/**]\n"
            "data_paths: \"not_a_list\"\n"
        ))
        with pytest.raises(
            ComponentsConfigError, match="data_paths.*must be a list"
        ):
            load_components_yaml(tmp_path)


# ===========================================================================
# Group D -- find_e2e_artifacts
# ===========================================================================

class TestFindE2eArtifacts:
    """find_e2e_artifacts: recursive ** glob and middle-segment wildcard."""

    def test_recursive_glob_matches_nested_files(self, tmp_path):
        e2e_dir = tmp_path / "tests" / "e2e" / "sub"
        e2e_dir.mkdir(parents=True)
        (tmp_path / "tests" / "e2e" / "foo.py").write_text("x")
        (e2e_dir / "bar.py").write_text("x")
        result = find_e2e_artifacts(tmp_path, ["tests/e2e/**"])
        assert "tests/e2e/foo.py" in result
        assert "tests/e2e/sub/bar.py" in result

    def test_wildcard_matches_middle_segment(self, tmp_path):
        # */integration/** matches bonding/integration/test.sh.
        integ_dir = tmp_path / "bonding" / "integration"
        integ_dir.mkdir(parents=True)
        (integ_dir / "test.sh").write_text("#!/bin/bash")
        result = find_e2e_artifacts(tmp_path, ["*/integration/**"])
        assert "bonding/integration/test.sh" in result

    def test_find_e2e_artifacts_invalid_pattern_returns_empty(self, tmp_path):
        """invalid glob pattern is non-fatal; the loop continues on the next pattern."""
        # Create a real file so a valid pattern would match.
        (tmp_path / "real_file.py").write_text("x = 1")

        # A NUL byte in the pattern causes scandir to raise ValueError on
        # all supported Python versions. The except (OSError, ValueError)
        # branch in find_e2e_artifacts must swallow it and return an empty
        # set for that pattern -- the loop then continues to the next one.
        bad_pattern = "tests/\x00bad/**"
        valid_pattern = "*.py"

        result = find_e2e_artifacts(tmp_path, [bad_pattern, valid_pattern])

        # The invalid pattern contributes nothing.
        for path in result:
            assert "\x00" not in path, "result must not contain paths from bad pattern"

        # The valid pattern still finds the real file (loop continued).
        assert "real_file.py" in result, (
            "valid pattern after the bad one must still be evaluated"
        )


# ===========================================================================
# Group E -- check_layer_1
# ===========================================================================

class TestCheckLayer1:
    """check_layer_1: heuristic nudge for cross-group changes with signatures."""

    def test_empty_diff_returns_empty(self):
        assert check_layer_1("") == []

    def test_single_group_with_signature_no_finding(self):
        # Same prefix "a" -> one group; Layer 1 threshold not met.
        diff = (
            "diff --git a/a/x.py b/a/x.py\n"
            "--- a/a/x.py\n"
            "+++ b/a/x.py\n"
            "@@ -1,1 +1,3 @@\n"
            " x = 1\n"
            "+def bar():\n"
            "+    pass\n"
            "diff --git a/a/y.py b/a/y.py\n"
            "--- a/a/y.py\n"
            "+++ b/a/y.py\n"
            "@@ -1,1 +1,2 @@\n"
            " y = 2\n"
            "+z = 3\n"
        )
        assert check_layer_1(diff) == []

    def test_multi_group_no_signature_no_finding(self):
        assert check_layer_1(_DIFF_MULTIGROUP_NO_SIG) == []

    def test_multi_group_with_signature_produces_one_finding(self):
        result = check_layer_1(_DIFF_MULTIGROUP_SIG)
        assert len(result) == 1
        f = result[0]
        assert f.source == "E2E_CHECK"
        assert f.disposition == Disposition.DISMISSED
        assert f.fingerprint.startswith("e2e-l1")

    def test_fingerprint_is_stable(self):
        r1 = check_layer_1(_DIFF_MULTIGROUP_SIG)
        r2 = check_layer_1(_DIFF_MULTIGROUP_SIG)
        assert r1[0].fingerprint == r2[0].fingerprint


# ===========================================================================
# Group F -- check_layer_2
# ===========================================================================

_COMMON_BONDING_YAML = (
    "version: 1\n"
    "components:\n"
    "  common:\n"
    "    paths: [common/**]\n"
    "  bonding:\n"
    "    paths: [bonding/**]\n"
    "    depends_on: [common]\n"
)


class TestCheckLayer2:
    """check_layer_2: co-occurrence detection with all arms."""

    def test_none_components_returns_empty(self, tmp_path):
        result = check_layer_2(_DIFF_MULTIGROUP_NO_SIG, tmp_path, components=None)
        assert result == []

    def test_hub_and_dependent_both_touched_no_artifact_produces_p2(
        self, tmp_path
    ):
        _make_components_yaml(tmp_path, _COMMON_BONDING_YAML)
        result = check_layer_2(
            _DIFF_COMMON_BONDING, tmp_path, load_components_yaml(tmp_path)
        )
        assert len(result) == 1
        f = result[0]
        assert f.source == "E2E_CHECK"
        assert f.disposition == Disposition.UNCERTAIN
        assert f.fingerprint.startswith("e2e-l2")

    def test_hub_only_touched_produces_no_p2_and_no_dependent_mention(
        self, tmp_path
    ):
        """Hub-only diff must not fire P2 for any declared dependent.

        Two dependents D1 and D2 are defined. Only hub H is in the diff.
        Result must be empty AND no finding description names D1 or D2.
        """
        content = (
            "version: 1\n"
            "components:\n"
            "  hub:\n"
            "    paths: [hub/**]\n"
            "  dep1:\n"
            "    paths: [dep1/**]\n"
            "    depends_on: [hub]\n"
            "  dep2:\n"
            "    paths: [dep2/**]\n"
            "    depends_on: [hub]\n"
        )
        _make_components_yaml(tmp_path, content)
        diff = (
            "diff --git a/hub/core.py b/hub/core.py\n"
            "--- a/hub/core.py\n"
            "+++ b/hub/core.py\n"
            "@@ -1,1 +1,2 @@\n"
            " x = 1\n"
            "+y = 2\n"
        )
        result = check_layer_2(diff, tmp_path, load_components_yaml(tmp_path))
        assert result == []
        for f in result:
            assert "dep1" not in f.description
            assert "dep2" not in f.description

    def test_dependent_has_artifact_suppresses_p2(self, tmp_path):
        content = (
            "version: 1\n"
            "components:\n"
            "  common:\n"
            "    paths: [common/**]\n"
            "  bonding:\n"
            "    paths: [bonding/**]\n"
            "    depends_on: [common]\n"
            "e2e_patterns: [bonding/tests/e2e/**]\n"
        )
        _make_components_yaml(tmp_path, content)
        e2e_dir = tmp_path / "bonding" / "tests" / "e2e"
        e2e_dir.mkdir(parents=True)
        (e2e_dir / "test_bond.py").write_text("pass")
        result = check_layer_2(
            _DIFF_COMMON_BONDING, tmp_path, load_components_yaml(tmp_path)
        )
        assert result == []

    def test_e2e_absent_ok_suppresses_p2(self, tmp_path):
        content = (
            "version: 1\n"
            "components:\n"
            "  common:\n"
            "    paths: [common/**]\n"
            "  bonding:\n"
            "    paths: [bonding/**]\n"
            "    depends_on: [common]\n"
            "e2e_absent_ok:\n"
            "  - component: bonding\n"
        )
        _make_components_yaml(tmp_path, content)
        result = check_layer_2(
            _DIFF_COMMON_BONDING, tmp_path, load_components_yaml(tmp_path)
        )
        assert result == []

    def test_peer_one_side_touched_no_p2(self, tmp_path):
        content = (
            "version: 1\n"
            "components:\n"
            "  alpha:\n"
            "    paths: [alpha/**]\n"
            "  beta:\n"
            "    paths: [beta/**]\n"
            "data_paths:\n"
            "  - [alpha, beta]\n"
        )
        _make_components_yaml(tmp_path, content)
        diff = (
            "diff --git a/alpha/x.py b/alpha/x.py\n"
            "--- a/alpha/x.py\n"
            "+++ b/alpha/x.py\n"
            "@@ -1,1 +1,2 @@\n"
            " x=1\n"
            "+y=2\n"
        )
        result = check_layer_2(diff, tmp_path, load_components_yaml(tmp_path))
        assert result == []

    def test_peer_both_sides_touched_no_artifact_produces_p2(self, tmp_path):
        content = (
            "version: 1\n"
            "components:\n"
            "  alpha:\n"
            "    paths: [alpha/**]\n"
            "  beta:\n"
            "    paths: [beta/**]\n"
            "data_paths:\n"
            "  - [alpha, beta]\n"
        )
        _make_components_yaml(tmp_path, content)
        diff = (
            "diff --git a/alpha/x.py b/alpha/x.py\n"
            "--- a/alpha/x.py\n"
            "+++ b/alpha/x.py\n"
            "@@ -1,1 +1,2 @@\n"
            " x=1\n"
            "+y=2\n"
            "diff --git a/beta/y.py b/beta/y.py\n"
            "--- a/beta/y.py\n"
            "+++ b/beta/y.py\n"
            "@@ -1,1 +1,2 @@\n"
            " a=1\n"
            "+b=2\n"
        )
        result = check_layer_2(diff, tmp_path, load_components_yaml(tmp_path))
        assert len(result) == 1

    def test_same_pair_depends_on_and_data_paths_dedup_to_one_finding(
        self, tmp_path
    ):
        """Both depends_on and data_paths express the same A-B pair.

        sorted_pair_hash must collapse both expressions into one fingerprint,
        so exactly one finding is returned -- not two.
        """
        content = (
            "version: 1\n"
            "components:\n"
            "  A:\n"
            "    paths: [A/**]\n"
            "    depends_on: [B]\n"
            "  B:\n"
            "    paths: [B/**]\n"
            "data_paths:\n"
            "  - [A, B]\n"
        )
        _make_components_yaml(tmp_path, content)
        diff = (
            "diff --git a/A/x.py b/A/x.py\n"
            "--- a/A/x.py\n"
            "+++ b/A/x.py\n"
            "@@ -1,1 +1,2 @@\n"
            " x=1\n"
            "+y=2\n"
            "diff --git a/B/y.py b/B/y.py\n"
            "--- a/B/y.py\n"
            "+++ b/B/y.py\n"
            "@@ -1,1 +1,2 @@\n"
            " a=1\n"
            "+b=2\n"
        )
        result = check_layer_2(diff, tmp_path, load_components_yaml(tmp_path))
        assert len(result) == 1

    def test_transitive_not_traversed(self, tmp_path):
        """A depends on B, B depends on C; diff touches A and C only -> no P2.

        Layer 2 is one-level co-occurrence only; it does not traverse the
        transitive closure.
        """
        content = (
            "version: 1\n"
            "components:\n"
            "  A:\n"
            "    paths: [A/**]\n"
            "    depends_on: [B]\n"
            "  B:\n"
            "    paths: [B/**]\n"
            "    depends_on: [C]\n"
            "  C:\n"
            "    paths: [C/**]\n"
        )
        _make_components_yaml(tmp_path, content)
        diff = (
            "diff --git a/A/x.py b/A/x.py\n"
            "--- a/A/x.py\n"
            "+++ b/A/x.py\n"
            "@@ -1,1 +1,2 @@\n"
            " x=1\n"
            "+y=2\n"
            "diff --git a/C/z.py b/C/z.py\n"
            "--- a/C/z.py\n"
            "+++ b/C/z.py\n"
            "@@ -1,1 +1,2 @@\n"
            " a=1\n"
            "+b=2\n"
        )
        result = check_layer_2(diff, tmp_path, load_components_yaml(tmp_path))
        assert result == []


# ===========================================================================
# Group G -- run_e2e_check orchestrator
# ===========================================================================

class TestRunE2eCheck:
    """run_e2e_check: orchestration, dedup, and config-error surfacing."""

    def test_empty_diff_no_config_returns_empty(self, tmp_path):
        findings, errors = run_e2e_check("", tmp_path)
        assert findings == []
        assert errors == []

    def test_invalid_yaml_emits_config_error_finding(self, tmp_path):
        # version: 2 triggers ComponentsConfigError; Layer 1 still runs.
        _make_components_yaml(tmp_path, "version: 2\ncomponents: {}")
        findings, _ = run_e2e_check(_DIFF_MULTIGROUP_SIG, tmp_path)
        config_findings = [
            f for f in findings if f.fingerprint == "e2e-config-error"
        ]
        assert len(config_findings) == 1
        assert config_findings[0].disposition == Disposition.UNCERTAIN

    def test_layer2_fires_suppresses_layer1(self, tmp_path):
        """When Layer 2 returns findings, Layer 1 is dropped entirely."""
        _make_components_yaml(tmp_path, _COMMON_BONDING_YAML)
        findings, _ = run_e2e_check(_DIFF_COMMON_BONDING, tmp_path)
        l1 = [f for f in findings if f.fingerprint.startswith("e2e-l1")]
        l2 = [f for f in findings if f.fingerprint.startswith("e2e-l2")]
        assert len(l2) >= 1
        assert len(l1) == 0

    def test_layer1_alone_passes_through_when_no_components(self, tmp_path):
        findings, _ = run_e2e_check(_DIFF_MULTIGROUP_SIG, tmp_path)
        l1 = [f for f in findings if f.fingerprint.startswith("e2e-l1")]
        assert len(l1) == 1


# ===========================================================================
# Bug-inject teeth tests
# ===========================================================================

class TestBugInjectTeeth:
    """Each test proves BOTH sides: firing and clearing on state change."""

    def test_t1_layer2_p2_three_state_cycle(self, tmp_path):
        """Layer 2 P2 fires on missing artifact, clears when added, refires when removed.

        e2e_patterns is set explicitly to '*/integration/**'. The default
        patterns ['tests/e2e/**', 'test_*integration*'] do not match
        bonding/integration/test.sh, so omitting the explicit pattern would
        make the inverse assertion pass by luck -- the artifact would never
        be counted, not because the logic is correct.
        """
        content = (
            "version: 1\n"
            "components:\n"
            "  common:\n"
            "    paths: [common/**]\n"
            "  bonding:\n"
            "    paths: [bonding/**]\n"
            "    depends_on: [common]\n"
            "e2e_patterns: ['*/integration/**']\n"
        )
        _make_components_yaml(tmp_path, content)

        diff = _DIFF_COMMON_BONDING

        # State 1: no artifact exists -> P2 fires.
        result1 = check_layer_2(diff, tmp_path, load_components_yaml(tmp_path))
        assert len(result1) == 1, "P2 must fire when no e2e artifact present"

        # State 2: create artifact under bonding/integration/ -> P2 clears.
        integ_dir = tmp_path / "bonding" / "integration"
        integ_dir.mkdir(parents=True)
        artifact = integ_dir / "test.sh"
        artifact.write_text("#!/bin/bash\necho test\n")
        result2 = check_layer_2(diff, tmp_path, load_components_yaml(tmp_path))
        assert result2 == [], "P2 must clear when e2e artifact is present"

        # State 3: delete artifact -> P2 fires again.
        artifact.unlink()
        result3 = check_layer_2(diff, tmp_path, load_components_yaml(tmp_path))
        assert len(result3) == 1, "P2 must refire after artifact removed"

    def test_t2_layer1_fires_and_clears_with_signature(self):
        """Layer 1 fires on multi-group + signature change, clears without signature."""
        result_with = check_layer_1(_DIFF_MULTIGROUP_SIG)
        assert len(result_with) == 1
        assert result_with[0].disposition == Disposition.DISMISSED

        result_without = check_layer_1(_DIFF_MULTIGROUP_NO_SIG)
        assert result_without == []

    def test_t3_layer1_no_fire_on_single_component_even_with_sig(self):
        """Single-component change must not fire Layer 1 even with a signature.

        Both files share the 'a' prefix -> one group -> threshold not met.
        """
        diff = (
            "diff --git a/a/x.py b/a/x.py\n"
            "--- a/a/x.py\n"
            "+++ b/a/x.py\n"
            "@@ -1,1 +1,3 @@\n"
            " x = 1\n"
            "+def foo(x):\n"
            "+    pass\n"
            "diff --git a/a/y.py b/a/y.py\n"
            "--- a/a/y.py\n"
            "+++ b/a/y.py\n"
            "@@ -1,1 +1,2 @@\n"
            " y = 2\n"
            "+z = 3\n"
        )
        assert check_layer_1(diff) == []

    def test_t4_depends_on_typo_surfaces_as_config_error_finding(
        self, tmp_path
    ):
        """A typo in depends_on must produce an UNCERTAIN config-error finding.

        The description must name the undefined reference so the author can
        identify and correct the typo.
        """
        content = (
            "version: 1\n"
            "components:\n"
            "  common:\n"
            "    paths: [common/**]\n"
            "  bonding:\n"
            "    paths: [bonding/**]\n"
            "    depends_on: [cmmon]\n"
        )
        _make_components_yaml(tmp_path, content)
        findings, _ = run_e2e_check(_DIFF_MULTIGROUP_SIG, tmp_path)
        config_findings = [
            f for f in findings if f.fingerprint == "e2e-config-error"
        ]
        assert len(config_findings) == 1
        f = config_findings[0]
        assert f.disposition == Disposition.UNCERTAIN
        assert "cmmon" in f.description or "undefined" in f.description
