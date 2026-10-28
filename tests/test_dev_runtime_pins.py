# SPDX-License-Identifier: Apache-2.0
"""The dev extra, the classifiers and the docs agree with QUALIFIED_RUNTIMES (#102).

The pytest gate refuses any (cache tag, pytest version) pair that is not in
QUALIFIED_RUNTIMES, so an unpinned dev extra or an undocumented interpreter makes
the gate tests fail on a fresh install.
"""

import re
import tomllib
from pathlib import Path

from code_forge import _gate_pytest as evidence

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
README = (ROOT / "README.md").read_text(encoding="utf-8")
README_ZH = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "linux-tests.yml").read_text(encoding="utf-8")
DOC_PREFIX = "Python versions in the classifiers that are not qualified for the pinned pytest: "
DOC_PREFIX_ZH = "classifiers 中没有为钉死的 pytest 认证的 Python 版本："


def _pytest_pins():
    """Every requirement on pytest itself, in the runtime and in every extra."""
    requirements = list(PYPROJECT["project"].get("dependencies", []))
    for extra in PYPROJECT["project"]["optional-dependencies"].values():
        requirements.extend(extra)
    return [req for req in requirements if re.match(r"pytest\b(?!-)", req)]


def _classifier_versions():
    prefix = "Programming Language :: Python :: "
    versions = [c[len(prefix) :] for c in PYPROJECT["project"]["classifiers"] if c.startswith(prefix)]
    return [version for version in versions if re.fullmatch(r"3\.\d+", version)]


def _pinned_version():
    (pin,) = _pytest_pins()
    match = re.fullmatch(r"pytest==(\d+\.\d+\.\d+)", pin)
    assert match, f"dev extra must pin one exact pytest version, got {pin!r}"
    return match.group(1)


def test_dev_extra_pins_one_exact_pytest():
    dev = PYPROJECT["project"]["optional-dependencies"]["dev"]

    assert [req for req in dev if req in _pytest_pins()] == _pytest_pins()
    assert len(_pytest_pins()) == 1
    assert _pinned_version()


def test_no_other_extra_or_dependency_requires_pytest():
    # A second requirement on pytest, even a range, can pull in a pair that is not qualified.
    assert len(_pytest_pins()) == 1


def test_pinned_pytest_is_qualified_on_the_interpreter_the_suite_runs_on():
    # CI runs the suite on 3.12; the pinned pytest has to be qualified there.
    assert ("cpython-312", _pinned_version()) in evidence.QUALIFIED_RUNTIMES


def test_ci_installs_the_same_pytest_as_the_dev_extra():
    versions = set(re.findall(r"pytest==(\d+\.\d+\.\d+)", CI))

    assert versions == {_pinned_version()}


def test_classifiers_list_the_supported_interpreters():
    assert _classifier_versions() == ["3.12", "3.13", "3.14"]


def test_every_unqualified_classifier_version_is_documented_and_only_those():
    unqualified = {
        version
        for version in _classifier_versions()
        if ("cpython-" + version.replace(".", ""), _pinned_version()) not in evidence.QUALIFIED_RUNTIMES
    }
    lines = [line for line in README.splitlines() if line.startswith(DOC_PREFIX)]
    assert len(lines) == 1
    listed = lines[0][len(DOC_PREFIX) :].rstrip(".")
    documented = set() if listed == "none" else set(listed.split(", "))

    assert documented == unqualified


def _unqualified_versions():
    return [
        version
        for version in _classifier_versions()
        if ("cpython-" + version.replace(".", ""), _pinned_version()) not in evidence.QUALIFIED_RUNTIMES
    ]


def test_requires_python_matches_the_oldest_classifier():
    match = re.fullmatch(r">=(3\.\d+)", PYPROJECT["project"]["requires-python"])
    assert match, PYPROJECT["project"]["requires-python"]
    oldest = min(_classifier_versions(), key=lambda version: int(version.split(".")[1]))

    assert match.group(1) == oldest


def test_readme_version_facts_match_the_pins_and_classifiers():
    oldest = PYPROJECT["project"]["requires-python"].removeprefix(">=")
    qualified = [v for v in _classifier_versions() if v not in _unqualified_versions()]
    listed = " and ".join(_unqualified_versions())

    assert qualified == [oldest]
    prose = " ".join(README.split())
    assert f"The dev extra pins `pytest=={_pinned_version()}`." in prose
    assert f"Run the test suite on CPython {oldest}:" in prose
    assert f"Forge itself installs on Python {oldest} or newer" in prose
    assert f"the classifiers keep listing {listed}" in prose


def test_chinese_readme_mirrors_the_pin_and_the_unqualified_list():
    oldest = PYPROJECT["project"]["requires-python"].removeprefix(">=")
    lines = [line for line in README_ZH.splitlines() if line.startswith(DOC_PREFIX_ZH)]
    assert len(lines) == 1
    listed = lines[0][len(DOC_PREFIX_ZH) :].rstrip("。.")

    assert listed == ", ".join(_unqualified_versions())
    assert f"dev extra 钉死 `pytest=={_pinned_version()}`" in README_ZH
    assert f"请在 CPython {oldest} 上运行测试套件" in README_ZH
    assert f"Python {oldest} 或更新的版本上" in README_ZH
    assert "列出 " + " 和 ".join(_unqualified_versions()) in README_ZH
