# Technology Stack: Forge v2.4 "Honest Green"

**Project:** code-review-forge
**Researched:** 2026-06-09
**Scope:** Stack additions for 6 new review axes + eval scorecard
**Baseline:** Python 3.12+, pyyaml>=6.0, unidiff>=0.7.5, mutmut>=3.3 (dev)

## Recommended Stack Additions

### 1. Semgrep -- Config-to-Sink Taint Analysis (Phase 18)

| Property | Value |
|----------|-------|
| Package | `semgrep` (PyPI) |
| Version | `>=1.165.0` (latest: 1.165.0, 2026-06-03) |
| License | LGPL-2.1 (engine); Semgrep Rules License v1.0 (Semgrep-authored rules) |
| Integration | External binary via `shutil.which("semgrep")` + `subprocess.run` |
| Dependency type | **Soft dependency** -- loud-fail when absent, never silent-skip |
| Python requires | >=3.10 (compatible with forge's >=3.12) |
| Output format | SARIF (reuses existing `parsers/semgrep.py` -> `parsers/_sarif.py`) |

**Why semgrep:** Forge already has a semgrep SARIF parser (`parsers/semgrep.py`). The
taint axis (REVIEW-TRUST-01) needs config-to-sink dataflow detection that pattern
matching alone cannot provide. Semgrep CE taint mode handles intraprocedural analysis
-- sufficient for detecting `yaml.safe_load(open(config))` -> `subprocess.run(cmd,
shell=True)` within a single function, which is the exact attack surface (gate.yaml
exfil pattern, CVE-2026-21852).

**Taint tier reality:**

| Tier | Scope | Cost | Forge needs? |
|------|-------|------|--------------|
| CE (free) | Intraprocedural (single function) | Free | YES -- covers the config-read-to-subprocess path in one function |
| Pro `--pro-intrafile` | Cross-function, single file | Paid | MAYBE -- useful but not blocking; CE catches the direct paths |
| Pro cross-file | Cross-function, cross-file | Paid | NO -- forge's config loading is single-file |

**Invocation pattern** (matches existing detect.py convention):

```python
# Detect
if shutil.which("semgrep") is None:
    # LOUD-FAIL: log warning + emit TAINT_SKIPPED finding
    # NEVER silent-skip

# Run with custom rule
subprocess.run(
    ["semgrep", "--config", rules_path, "--sarif", "--quiet", target_path],
    capture_output=True, text=True, timeout=120, check=False,
)
```

**Custom taint rule** (YAML, shipped with forge under `src/code_forge/rules/`):

```yaml
rules:
  - id: forge-config-to-subprocess
    languages: [python]
    message: >
      Data from config file (yaml.safe_load / json.load / open / os.environ)
      flows to subprocess/shell/urlopen sink without sanitization.
    severity: ERROR
    mode: taint
    pattern-sources:
      - pattern: yaml.safe_load(...)
      - pattern: yaml.load(...)
      - pattern: json.load(...)
      - pattern: os.environ.get(...)
      - pattern: os.environ[...]
      - pattern: open(...)
    pattern-sinks:
      - pattern: subprocess.run($CMD, shell=True, ...)
      - pattern: subprocess.call($CMD, shell=True, ...)
      - pattern: subprocess.Popen($CMD, shell=True, ...)
      - pattern: os.system(...)
      - pattern: urllib.request.urlopen(...)
    pattern-sanitizers:
      - pattern: shlex.quote(...)
```

**What NOT to do:**
- Do NOT add semgrep to `[project.dependencies]` -- it is 150+ MB, pulls in OCaml
  runtime. Keep it as an external binary detected at runtime via `shutil.which`.
- Do NOT use Semgrep-authored community rules (Semgrep Rules License v1.0 restricts
  commercial redistribution). Write custom rules in-repo under Apache-2.0.
- Do NOT require Semgrep Pro -- CE intraprocedural taint is sufficient for the
  gate.yaml exfil pattern. Pro is a nice-to-have, not a blocker.

**Licensing constraint:** Semgrep engine = LGPL-2.1 (forge only invokes it as an
external binary via subprocess, no linking -- LGPL is satisfied). Custom rules
authored by forge = Apache-2.0. Semgrep-authored community rules = Semgrep Rules
License v1.0 (DO NOT vendor these).

**Confidence:** HIGH (verified via PyPI, official docs, existing forge parser)

---

### 2. sem-cli -- Entity Extraction + Blast-Radius (Phase 22)

| Property | Value |
|----------|-------|
| Package | `sem-cli` (Homebrew, cargo, npm, GitHub Releases) |
| Version | `>=0.8.0` (latest: v0.8.0, 2026-06-07) |
| License | MIT OR Apache-2.0 (dual-licensed, user's choice) |
| Integration | External binary via `shutil.which("sem")` + `subprocess.run` |
| Dependency type | **Opt-in soft dependency** (Phase 22 is opt-in) |
| Output format | JSON (`--format json`) |
| NOT a pip package | No Python bindings exist; Rust binary only |

**Why sem-cli (not inspect-core, not tree-sitter-py):**

| Option | License | Integration | Why/Why Not |
|--------|---------|-------------|-------------|
| sem-cli | MIT/Apache-2.0 | Subprocess, JSON output | YES: license clean, covers entity extraction + impact + blame for 28 languages |
| inspect-core | FSL-1.1-ALv2 | Rust library | NO: FSL prohibits competing use; forge IS a code review tool (directly competing) |
| tree-sitter (Python) | MIT | pip, native binding | FALLBACK: build entity extraction from scratch; massive effort for 28-language support |
| code-review-graph (MCP) | Unknown | MCP protocol | NO: crashes on 50K+ node repos, 0 callers for shell (per user memory) |

**Critical: inspect-core is FSL-licensed.** The FSL-1.1-ALv2 license explicitly
prohibits "Competing Use" defined as offering software that "offers the same or
substantially similar functionality as the Software." Forge is a code review tool.
Inspect is a code review tool. Using inspect-core in forge is a direct license
violation. This is confirmed in REQUIREMENTS.md ("Do NOT vendor inspect-core (FSL)").
After 2 years FSL converts to Apache-2.0, but the current release (2025 copyright)
means the earliest safe date is ~2027.

**sem-cli integration pattern:**

```python
# Entity extraction for changed files
result = subprocess.run(
    ["sem", "diff", "--format", "json", "--staged"],
    capture_output=True, text=True, timeout=60, check=False,
)
entities = json.loads(result.stdout)

# Blast-radius for a specific entity
result = subprocess.run(
    ["sem", "impact", entity_name, "--json", "--file", file_path],
    capture_output=True, text=True, timeout=30, check=False,
)
impact = json.loads(result.stdout)
```

**What NOT to do:**
- Do NOT vendor inspect-core (FSL license violation for a competing code review tool)
- Do NOT build tree-sitter entity extraction from scratch -- 28 languages is a
  multi-month effort; sem-cli already does it
- Do NOT make sem-cli a hard dependency -- Phase 22 is opt-in, gated behind
  `gate.yaml` flag or CLI `--graph-triage`

**Confidence:** HIGH (verified via crates.io, GitHub, license files)

---

### 3. Eval/Benchmark Harness (Phase 17) -- CUSTOM CODE

| Property | Value |
|----------|-------|
| Package | None -- custom code in `src/code_forge/eval/` |
| Dependencies | pyyaml (existing), subprocess (stdlib), json (stdlib) |
| License | Apache-2.0 (part of forge) |

**Why custom:** No existing framework fits forge's specific eval model (drive a real
LLM backend on a labeled bug corpus, compute false-green rate). Existing benchmarks:

| Benchmark | Format | Language | Relevance |
|-----------|--------|----------|-----------|
| BugsInPy | projects/<repo>/bugs/<id>/ with buggy.txt, bugsinpy_bug.info | Python | HIGH -- Python corpus, real bugs from 17 projects |
| Defects4J | <project>/<id>{b,f} checkout via CLI | Java | LOW -- Java only, heavyweight CLI |
| SWE-bench | GitHub issue + PR patch | Python | MEDIUM -- too large, not labeled for review quality |
| Greptile Benchmark | 50 real-world bugs, hidden in PRs | Multi | MEDIUM -- proprietary, not reproducible |

**Forge's eval corpus is small and specific** (E1-E6, gate.yaml RCE, BUG-P12-01,
ttl_class = ~10 pairs). The right approach is a simple YAML manifest + git fixtures:

```yaml
# .code-forge/eval/corpus.yaml
corpus:
  - id: E1-runtime-escape
    repo: fixtures/e1
    buggy_ref: buggy
    fixed_ref: fixed
    expected_verdict: FAIL  # forge should NOT give green
    axis: RUNTIME
  - id: gate-yaml-rce
    repo: fixtures/gate-rce
    buggy_ref: buggy
    fixed_ref: fixed
    expected_verdict: FAIL
    axis: TRUST
```

**Integration with R2 mutation engine:** The existing `mutation.py` can generate
additional corpus entries by creating surviving mutants from known-good code. Each
surviving mutant becomes a "buggy" version where the "fixed" version is the original.
This reuses forge's own infrastructure rather than importing BugsInPy wholesale.

**What NOT to do:**
- Do NOT import BugsInPy/Defects4J wholesale -- they are multi-GB, need conda/JDK
  environments, and are overkill for a 10-pair corpus
- Do NOT mock the LLM backend in eval -- the entire point is measuring real backend
  false-green rate (from REQUIREMENTS.md: "drives real backend, never mocks")
- Do NOT build a generic benchmark framework -- forge needs a scorecard, not a
  platform

**Confidence:** HIGH (design follows requirements directly)

---

### 4. Revert-RED Fixture Mechanism (Phase 19) -- CUSTOM CODE + EXISTING DEPS

| Property | Value |
|----------|-------|
| Package | `unidiff` (existing dep, >=0.7.5) + `subprocess` (stdlib) |
| New deps | None |
| License | MIT (unidiff) |

**Why unidiff + git apply -R:** Forge already depends on `unidiff>=0.7.5,<0.8.0` for
diff parsing. The revert-RED mechanism needs to:

1. Parse the diff into hunks via `unidiff.PatchSet`
2. Separate test hunks from non-test hunks (file path heuristic: `test_*.py`, `*_test.py`, `tests/`)
3. Reconstruct a partial patch containing only non-test hunks
4. Apply it in reverse via `git apply -R <partial.patch>`
5. Run the test suite -- expect RED (new tests fail on buggy code)
6. Restore via `git apply <partial.patch>`
7. Run the test suite -- expect GREEN

**This is a natural extension of the existing `mutation.py` pattern:**
- `mutation.py` already calls `subprocess.run` to execute test commands
- `mutation.py` already manages temporary file creation/cleanup
- The revert-RED is conceptually "a single reversion mutant" (from REQUIREMENTS.md)

**Hunk classification heuristic:**

```python
from unidiff import PatchSet

def classify_hunks(diff_text: str) -> tuple[str, str]:
    """Split diff into test-hunks and non-test-hunks."""
    patch = PatchSet(diff_text)
    test_files = []
    code_files = []
    for patched_file in patch:
        path = patched_file.path
        if _is_test_file(path):
            test_files.append(patched_file)
        else:
            code_files.append(patched_file)
    return str(PatchSet(code_files)), str(PatchSet(test_files))
```

**STING overfit guard implementation:**

STING (arXiv 2604.01518) uses behavior-preserving transforms to detect overfitting
tests. The forge implementation needs a minimal subset:

| Transform | Description | Implementation |
|-----------|-------------|----------------|
| Identifier rename | Rename local variables in the fix | AST (stdlib `ast` module) or regex |
| Operand swap | `a + b` -> `b + a` for commutative ops | `ast.NodeTransformer` |
| Control-flow restructure | `if not x: return` -> `if x: pass else: return` | `ast.NodeTransformer` |

These transforms use Python's stdlib `ast` module -- no new dependencies. The test
must pass on all transformed variants. A test that fails on any variant is overfitting
to implementation details, not behavior.

**What NOT to do:**
- Do NOT use gitpython -- forge uses subprocess for all git ops (consistency with
  `git.py` module; gitpython is a heavy dep)
- Do NOT apply the full STING paper (LLM-based mutation + screening) -- start with
  the 3 deterministic transforms above; LLM screening is future work
- Do NOT make revert-RED run on every review -- only on diffs that modify test files
  alongside non-test code (fix-validation scenario)

**Confidence:** HIGH (unidiff is existing dep, git apply -R is well-documented)

---

### 5. Verdict Calibration -- Test Type Classification (Phase 20) -- CUSTOM CODE

| Property | Value |
|----------|-------|
| Package | None -- custom heuristic in forge |
| Dependencies | subprocess (stdlib), json (stdlib) |
| New deps | None |

**Why custom:** No prior art exists for programmatic "did a real smoke test run vs.
did a logic simulation run" classification. The closest analogue is pytest marker
detection, but forge's problem is different: it needs to detect WHAT the test suite
actually exercised, not how tests are labeled.

**Approach: evidence-based classification**

The verdict does not need to classify individual tests. It needs to classify the
REVIEW SESSION's verification level:

| Signal | Source | Interpretation |
|--------|--------|---------------|
| R1 gate passed with exit 0 | gate_check.py exit code | Real tests ran and passed |
| R1 gate returned MUTATION_SKIPPED | mutation.py finding | No mutation possible (no Python files, or flaky) |
| No R1 gate configured | gate.yaml absent | No test gate at all |
| subprocess.run calls in test files | Static scan of test code | Tests that call subprocess are likely integration/smoke |
| `@pytest.mark.real_api` present | Static scan of test code | Explicitly labeled real-API tests |
| `mock.patch` / `unittest.mock` usage | Static scan of test code | Tests using mocks are NOT smoke tests |
| `conftest.py` fixtures with external deps | Static scan of conftest | Fixtures creating DB/network connections |

**Classification output** (for verdict.py):

```python
@dataclass(frozen=True)
class VerificationLevel:
    real_tests_ran: bool       # R1 gate exit 0
    mutation_ran: bool         # R2 no MUTATION_SKIPPED
    coverage_checked: bool     # R3 no skip
    has_mock_only_tests: bool  # All test files use mock.patch
    unverified_surfaces: list[str]  # e.g. ["network I/O", "filesystem"]
```

**What NOT to do:**
- Do NOT attempt to parse pytest output to determine test "type" -- pytest markers
  are voluntary and unreliable
- Do NOT spin up real environments -- RUNTIME-01 is ADVISORY, not a gate
- Do NOT block on unverified surfaces -- this is information-only in the verdict

**Confidence:** MEDIUM (no prior art; heuristic-based approach is novel but
reasonable; the key innovation is that the verdict DECLARES what it did not verify
rather than claiming everything is verified)

---

### 6. Legacy + Intent Classification (Phase 21) -- CUSTOM CODE + EXISTING DEPS

| Property | Value |
|----------|-------|
| Package | None -- custom code using git blame subprocess |
| Dependencies | subprocess (stdlib), existing baseline.py primitives |
| New deps | None |

**Integration with R1 baseline:** The legacy detection reuses the R1 baseline
primitive (NEW vs baseline delta from `baseline.py`). When forge finds an issue in
code the diff touches but did not change, it checks `git blame` for attribution:

```python
result = subprocess.run(
    ["git", "blame", "-L", "%d,%d" % (start, end), "--porcelain", file_path],
    capture_output=True, text=True, timeout=10, check=False,
)
```

**Intent classification:** Uses the LLM backend (existing `llm_invoke.py`) with a
prompt asking whether the flagged code is "intended (workaround/SATD)" vs "unintended
(bug)" based on the commit message and surrounding context. No new dependencies --
reuses the falsify_real.py pattern.

**Confidence:** HIGH (reuses existing forge primitives)

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Taint analysis | Semgrep CE (subprocess) | Bandit, CodeQL | Bandit has no taint mode; CodeQL needs a database build step + BUSL license |
| Entity extraction | sem-cli (subprocess) | tree-sitter-py, inspect-core | tree-sitter-py needs 28 language configs from scratch; inspect-core is FSL (competing use violation) |
| Dependency graph | sem-cli `impact` | code-review-graph MCP | MCP crashes on 50K+ repos, returns 0 for shell (per user memory) |
| Hunk manipulation | unidiff (existing dep) | gitpython, pygit2 | gitpython is a heavy dep forge does not use; pygit2 needs libgit2 C lib |
| Mutation engine | mutmut (existing dev dep) | cosmic-ray, mutatest | Already integrated in mutation.py; switching gains nothing |
| Eval framework | Custom YAML + git fixtures | BugsInPy, pytest-benchmark | BugsInPy needs conda; corpus is <10 pairs, framework is overkill |
| STING transforms | stdlib `ast` module | parso, rope | stdlib ast is sufficient for the 3 transforms needed; no new dep |
| Test classification | Custom heuristic | pytest introspection API | pytest API requires pytest as runtime dep; heuristic is simpler |
| LLM invoke | Existing backend.py + llm_invoke.py | LangChain, LiteLLM | Already works; adding LLM framework is anti-YAGNI |

---

## Dependency Summary

### New Runtime Dependencies: NONE

Forge v2.4 adds **zero new runtime dependencies**. All new functionality uses:
- Existing deps (pyyaml, unidiff)
- Python stdlib (ast, subprocess, json, shutil, dataclasses)
- External binaries detected at runtime via `shutil.which` (semgrep, sem)

### New Dev Dependencies: NONE

mutmut is already in `[project.optional-dependencies] dev`.

### External Binary Dependencies (soft, opt-in)

| Binary | Required by | When needed | How to install |
|--------|-------------|-------------|----------------|
| `semgrep` | Phase 18 (taint) | When taint gate is enabled | `pipx install semgrep` or `brew install semgrep` |
| `sem` | Phase 22 (graph triage) | When `--graph-triage` flag or gate.yaml enables it | `brew install sem-cli` or `cargo install sem-cli` |

Both follow the existing `shutil.which` pattern from `detect.py` and `mutation.py`.
Both loud-fail when absent (TAINT_SKIPPED / GRAPH_SKIPPED finding emitted).

---

## Installation (unchanged from v2.3)

```bash
# Core (no new deps)
pip install code-review-forge

# Dev (no new deps)
pip install code-review-forge[dev]

# Optional: external binaries for advanced axes
pipx install semgrep          # Phase 18: taint analysis
brew install sem-cli          # Phase 22: graph triage
```

---

## Integration Points with Existing Forge Code

| New Feature | Existing Module | Integration |
|-------------|----------------|-------------|
| Semgrep taint | `parsers/semgrep.py`, `detect.py` | Add semgrep to TOOL_REGISTRY with `shutil.which` detection; reuse existing SARIF parser |
| Eval harness | `mutation.py`, `backend.py` | Eval reuses R2 mutation for corpus generation; drives `llm_invoke` for real backend scoring |
| Revert-RED | `mutation.py`, `git.py` | Extends mutation pattern (subprocess test execution + cleanup); uses `git.py` for diff operations |
| Verdict calibration | `verdict.py`, `gate_check.py` | Verdict reads R1/R2/R3 outcomes + scan results to declare unverified surfaces |
| Legacy detection | `baseline.py`, `git.py` | Reuses R1 NEW-vs-baseline delta; adds git blame attribution subprocess call |
| Intent classification | `falsify_real.py`, `llm_invoke.py` | Follows falsifier pattern: prompt LLM, parse JSON verdict |
| Graph triage | `detect.py` pattern | New module following detect.py's shutil.which + subprocess + JSON parse pattern |
| Danger-score | `gate_check.py` | Static analysis of gate.yaml fields (base_url, api_key_env, shell) at config load time |

---

## Licensing Summary

| Component | License | Forge interaction | Constraint |
|-----------|---------|-------------------|------------|
| Semgrep CE engine | LGPL-2.1 | Subprocess (no linking) | LGPL satisfied by subprocess invocation |
| Semgrep community rules | Semgrep Rules License v1.0 | DO NOT USE | Restricts commercial redistribution |
| Custom taint rules (forge-authored) | Apache-2.0 | Shipped in-repo | No constraint |
| sem-cli | MIT OR Apache-2.0 | Subprocess (no linking) | No constraint |
| sem-core (Rust lib) | MIT OR Apache-2.0 | NOT used (sem-cli subprocess instead) | N/A |
| inspect-core | FSL-1.1-ALv2 | DO NOT USE | Competing use prohibition (forge = code review tool) |
| unidiff | MIT | Existing pip dep | No constraint |
| pyyaml | MIT | Existing pip dep | No constraint |
| mutmut | BSD-3 | Existing dev dep | No constraint |
| Python stdlib (ast, subprocess) | PSF | Used directly | No constraint |
| tree-sitter (Python) | MIT | NOT used (sem-cli preferred) | N/A |
| tree-sitter-language-pack | MIT | NOT used | N/A |

---

## Sources

### Semgrep
- [Semgrep Taint Analysis Overview](https://semgrep.dev/docs/writing-rules/data-flow/taint-mode/overview) -- HIGH confidence
- [Semgrep PyPI](https://pypi.org/project/semgrep/) -- v1.165.0, 2026-06-03 -- HIGH confidence
- [Semgrep Community Edition](https://semgrep.dev/products/community-edition/) -- LGPL-2.1 -- HIGH confidence
- [Semgrep Licensing](https://semgrep.dev/docs/licensing) -- engine LGPL-2.1, rules license change -- HIGH confidence
- [Semgrep Cross-File Analysis (Pro)](https://semgrep.dev/docs/semgrep-code/semgrep-pro-engine-intro) -- Pro-only features -- HIGH confidence
- [Semgrep CLI Reference](https://semgrep.dev/docs/cli-reference) -- invocation patterns -- HIGH confidence

### sem-cli / sem-core
- [sem-cli crates.io](https://crates.io/crates/sem-cli) -- v0.8.0, MIT/Apache-2.0 -- HIGH confidence
- [Ataraxy-Labs/sem GitHub](https://github.com/ataraxy-labs/sem) -- source, license files -- HIGH confidence
- [Ataraxy-Labs/inspect GitHub](https://github.com/Ataraxy-Labs/inspect) -- FSL-1.1-ALv2 -- HIGH confidence
- [inspect LICENSE.md](https://github.com/Ataraxy-Labs/inspect/blob/main/LICENSE.md) -- FSL competing use prohibition -- HIGH confidence

### Eval/Benchmark
- [BugsInPy GitHub](https://github.com/soarsmu/BugsInPy) -- 493 bugs, 17 Python projects -- HIGH confidence
- [Defects4J GitHub](https://github.com/rjust/defects4j) -- 835 bugs, Java -- HIGH confidence
- [Greptile AI Code Review Benchmarks](https://www.greptile.com/benchmarks) -- 50 bugs, proprietary -- MEDIUM confidence

### STING / Cleverest (Fix Validation)
- [STING: arXiv 2604.01518](https://arxiv.org/html/2604.01518v1) -- behavior-preserving transforms for test overfit detection -- HIGH confidence
- [Cleverest: arXiv 2501.11086](https://arxiv.org/abs/2501.11086) -- LLM regression test generation -- MEDIUM confidence

### tree-sitter (considered, not selected)
- [tree-sitter PyPI](https://pypi.org/project/tree-sitter/) -- v0.25.2 -- HIGH confidence
- [tree-sitter-language-pack PyPI](https://pypi.org/project/tree-sitter-language-pack/) -- v1.8.1 -- HIGH confidence

### unidiff (existing dep)
- [unidiff PyPI](https://pypi.org/project/unidiff/) -- existing dep -- HIGH confidence
- [python-unidiff GitHub](https://github.com/matiasb/python-unidiff) -- hunk manipulation API -- HIGH confidence

### mutmut (existing dev dep)
- [mutmut PyPI](https://pypi.org/project/mutmut/) -- v3.3.1, BSD-3 -- HIGH confidence
