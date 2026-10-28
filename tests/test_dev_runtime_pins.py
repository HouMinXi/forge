# SPDX-License-Identifier: Apache-2.0
"""The dev extra, the classifiers and the docs agree with QUALIFIED_RUNTIMES (#102).

The pytest gate refuses any (cache tag, pytest version) pair that is not in
QUALIFIED_RUNTIMES, so an unpinned dev extra or an undocumented interpreter makes
the gate tests fail on a fresh install.
"""

import re
import tomllib
from pathlib import Path

import pytest

from code_forge import _gate_pytest as evidence

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
README = (ROOT / "README.md").read_text(encoding="utf-8")
README_ZH = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "linux-tests.yml").read_text(encoding="utf-8")
DOC_PREFIX = "Python versions in the classifiers that are not qualified for the pinned pytest: "
DOC_PREFIX_ZH = "classifiers 中没有为钉死的 pytest 认证的 Python 版本："
DOCS = [pytest.param(README, DOC_PREFIX, id="en"), pytest.param(README_ZH, DOC_PREFIX_ZH, id="zh-CN")]


def _pytest_pins():
    """Every requirement on pytest itself (any case), in the runtime and in every extra."""
    requirements = list(PYPROJECT["project"].get("dependencies", []))
    for extra in PYPROJECT["project"]["optional-dependencies"].values():
        requirements.extend(extra)
    return [req for req in requirements if re.match(r"pytest(?![\w.-])", req, re.IGNORECASE)]


def _classifier_versions():
    prefix = "Programming Language :: Python :: "
    versions = [c[len(prefix) :] for c in PYPROJECT["project"]["classifiers"] if c.startswith(prefix)]
    return [version for version in versions if re.fullmatch(r"3\.\d+", version)]


def _pinned_version():
    (pin,) = _pytest_pins()
    match = re.fullmatch(r"pytest==(\d+\.\d+\.\d+)", pin, re.IGNORECASE)
    assert match, f"dev extra must pin one exact pytest version, got {pin!r}"
    return match.group(1)


def _unqualified_versions():
    return [
        version
        for version in _classifier_versions()
        if ("cpython-" + version.replace(".", ""), _pinned_version()) not in evidence.QUALIFIED_RUNTIMES
    ]


def _oldest_classifier():
    return min(_classifier_versions(), key=lambda version: int(version.split(".")[1]))


def _compact(text):
    return re.sub(r"\s+", "", text)


def _runtime_paragraph(doc):
    (paragraph,) = [block for block in doc.split("\n\n") if "QUALIFIED_RUNTIMES" in block]
    return paragraph


def test_pytest_is_pinned_once_and_only_in_the_dev_extra():
    # A second requirement on pytest, even a range, can pull in a pair that is not qualified.
    dev = PYPROJECT["project"]["optional-dependencies"]["dev"]

    assert len(_pytest_pins()) == 1
    assert _pytest_pins()[0] in dev
    assert _pinned_version()


def test_pinned_pytest_is_qualified_on_the_interpreter_the_suite_runs_on():
    # CI runs the suite on 3.12; the pinned pytest has to be qualified there.
    assert ("cpython-312", _pinned_version()) in evidence.QUALIFIED_RUNTIMES


def test_ci_installs_the_same_pytest_as_the_dev_extra():
    versions = set(re.findall(r"pytest==(\d+\.\d+\.\d+)", CI))

    assert versions == {_pinned_version()}


def test_classifiers_list_the_supported_interpreters():
    assert _classifier_versions() == ["3.12", "3.13", "3.14"]


def test_requires_python_matches_the_oldest_classifier():
    match = re.fullmatch(r">=(3\.\d+)", PYPROJECT["project"]["requires-python"])
    assert match, PYPROJECT["project"]["requires-python"]

    assert match.group(1) == _oldest_classifier()


def test_the_pinned_pytest_is_qualified_only_on_the_oldest_interpreter():
    # Both READMEs say the oldest interpreter is the only one whose pair is qualified.
    qualified = [v for v in _classifier_versions() if v not in _unqualified_versions()]

    assert qualified == [_oldest_classifier()]


@pytest.mark.parametrize(("doc", "prefix"), DOCS)
def test_every_unqualified_classifier_version_is_documented_and_only_those(doc, prefix):
    lines = [line for line in doc.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1
    listed = lines[0][len(prefix) :].rstrip(".。")
    documented = set() if listed == "none" else set(listed.split(", "))

    assert documented == set(_unqualified_versions())


@pytest.mark.parametrize(("doc", "_prefix"), DOCS)
def test_the_runtime_paragraph_names_the_pin_the_interpreter_and_a_real_path(doc, _prefix):
    # Key tokens only, with whitespace removed so a rewrap or a rewording cannot fail it.
    paragraph = _compact(_runtime_paragraph(doc))
    path = Path(evidence.__file__).resolve().relative_to(ROOT).as_posix()

    assert f"`pytest=={_pinned_version()}`" in paragraph
    assert f"CPython{_oldest_classifier()}" in paragraph
    assert f"`{path}`" in paragraph
