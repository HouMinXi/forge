---
must_haves:
  - ALL_REGISTRIES list created in detect.py
  - All iteration sites use ALL_REGISTRIES loop
  - New language = define dict + append to ALL_REGISTRIES (2 lines)
  - REGISTRY_LANG_NAMES mapping for detection
  - REGISTRY_MARKERS / REGISTRY_EXTENSIONS for generic detection loop
depends_on: [45-02]
wave: 1.5
status: pending
---

# 45-02b: Refactor registry to ALL_REGISTRIES

## Context
Spec CORE CHANGE 2 recommends a single source of truth for all registries. Phase 2 added GO_TOOL_REGISTRY as a separate dict. This plan consolidates all iteration sites and adds generic detection for new languages.

## Task 1: Create ALL_REGISTRIES + metadata mappings

**files:** src/code_forge/detect.py
**action:**
1. Add `ALL_REGISTRIES = [PYTHON_TOOL_REGISTRY, SHELL_TOOL_REGISTRY, GO_TOOL_REGISTRY]` after registry definitions
2. Add `REGISTRY_LANG_NAMES` mapping (registry id -> language name string):
```python
REGISTRY_LANG_NAMES: dict[int, str] = {
    id(PYTHON_TOOL_REGISTRY): "python",
    id(SHELL_TOOL_REGISTRY): "shell",
    id(GO_TOOL_REGISTRY): "go",
}
```
3. Add `REGISTRY_MARKERS` mapping (language name -> marker files):
```python
REGISTRY_MARKERS: dict[str, list[str]] = {
    "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"],
    "shell": [],
    "go": ["go.mod"],
    "js": ["package.json"],
    "cs": [],  # *.csproj is a glob, rely on .cs extension
    "java": ["pom.xml", "build.gradle"],
    "c_cpp": ["Makefile", "Kbuild", "CMakeLists.txt"],
    "ruby": ["Gemfile"],
    "swift": ["Package.swift"],
    "php": ["composer.json"],
}
```
4. Add `REGISTRY_EXTENSIONS` mapping (language name -> dotted extensions):
```python
REGISTRY_EXTENSIONS: dict[str, list[str]] = {
    "python": [".py"], "shell": [".sh", ".bash"], "go": [".go"],
    "js": [".js", ".ts", ".jsx", ".tsx"], "cs": [".cs"],
    "java": [".java"], "c_cpp": [".c", ".cpp", ".hpp", ".cxx"],
    "ruby": [".rb"], "swift": [".swift"], "php": [".php"],
}
```
5. Update `_get_tool_meta` to iterate `ALL_REGISTRIES`

**verify:** `python3 -m py_compile src/code_forge/detect.py`
**done:** ALL_REGISTRIES + 3 metadata mappings defined; _get_tool_meta uses ALL_REGISTRIES

## Task 2: Update all iteration sites

**files:** src/code_forge/detect.py
**action:** For each iteration site:
- `_get_tool_meta` (~line 142): iterate ALL_REGISTRIES ✓
- `all_registry_names` (~line 459): `set().union(*(set(r) for r in ALL_REGISTRIES))` (no _name keys to filter)
- `detect_and_init` registry checks: iterate ALL_REGISTRIES
- pyproject.toml walk (~line 252): **KEEP as PYTHON_TOOL_REGISTRY** (Python-only, toml_section)
- flake8 binary lookup (~line 316): **KEEP as-is** (dict key lookup, not iteration)

Update Go command from v1.x `--out-format sarif:stdout` to v2.x `--output.sarif.path=stdout`.

**verify:** `python3 -m py_compile src/code_forge/detect.py && python3 -B -m pytest tests/test_detect.py -q`
**done:** All iteration sites use ALL_REGISTRIES; Go command uses v2.x syntax

## Task 3: Add generic detection loop for new languages

**files:** src/code_forge/detect.py
**action:** After existing Python/Shell detection branches in `detect_toolchain()`, add a generic loop for new languages:
```python
primary_lang = None
for registry in ALL_REGISTRIES:
    lang = REGISTRY_LANG_NAMES.get(id(registry))
    if lang in ("python", "shell"):
        continue  # already handled above
    markers = REGISTRY_MARKERS.get(lang, [])
    extensions = REGISTRY_EXTENSIONS.get(lang, [])
    has_marker = any((project_root / m).exists() for m in markers)
    has_files = any(
        list(project_root.glob(f"*{ext}")) or list(project_root.glob(f"*/*{ext}"))
        for ext in extensions
    )
    if has_marker or has_files:
        _scan_path_for_tools(which_fn, detected, missing, registry=registry)
        if primary_lang is None:
            primary_lang = lang

# Language assignment (after existing Python/Shell logic)
if has_python:
    language = "python"
elif has_shell:
    language = "shell"
elif primary_lang is not None:
    language = primary_lang
else:
    language = "python"
```
Note: glob preserves 2-level depth (root + one level deep). Python/Shell detection unchanged.

**verify:** `python3 -m py_compile src/code_forge/detect.py && python3 -B -m pytest tests/test_detect.py::TestGoDetection -v`
**done:** Generic detection loop works; Go tests pass; Python/Shell detection unchanged

## Language Priority (single source of truth)
Text rule IS the priority. No separate numeric table.
- Python/Shell retain existing precedence (existing detection branches)
- New language tie-breaker (when Python/Shell not detected): Go > C/C++ > Java > C# > JS/TS > Ruby > Swift > PHP
- ALL_REGISTRIES must be appended in this priority order
- Wave-2 plans execute in priority order: 45-06(C/C++) → 45-05(Java) → 45-04(C#) → 45-03(JS) → 45-07(Ruby) → 45-08(Swift) → 45-09(PHP)

## Task 3 refinement: Verify Go tests + Python regression

**files:** tests/test_detect.py
**action:** Run Go detection tests AND Python detection tests to confirm refactoring didn't break existing behavior
**verify:** `python3 -B -m pytest tests/test_detect.py::TestGoDetection tests/test_detect.py::TestPyprojectDetection -v`
**done:** Both Go and Python detection tests pass

## R5 lc findings

**D1 fix (Go command not committed):** 45-02b depends_on must include an explicit prerequisite: "Before 45-02b starts, amend 71584f1 on feat/multi-language with v2.x Go command: `golangci-lint run --output.sarif.path=stdout`". This is a one-line code change on the feature branch, not a new plan.

**D3 fix (wave-2 execution order):** Wave-2 plans must execute in priority order, not plan-number order. Update depends_on for each wave-2 plan:
- 45-06 (C/C++): depends_on [45-02b] (first in priority)
- 45-05 (Java): depends_on [45-02b, 45-06]
- 45-04 (C#): depends_on [45-02b, 45-05]
- 45-03 (JS): depends_on [45-02b, 45-04]
- 45-07 (Ruby): depends_on [45-02b, 45-03]
- 45-08 (Swift): depends_on [45-02b, 45-07]
- 45-09 (PHP): depends_on [45-02b, 45-08]
This ensures ALL_REGISTRIES append order matches priority table.
