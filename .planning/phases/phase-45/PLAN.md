# Phase 45: Multi-Language Support

## Goal
Make forge a general-purpose reviewer supporting all mainstream languages (TIOBE-ranked). Architecture: SARIF-first, registry-driven. Adding a language = one registry entry, not a code change.

## Decision (RESOLVED)
Phase 3 scope: full Tier-1 (JS/TS, C#, Java, C++, Ruby, Swift, PHP). All 7 languages.

## Status
- Phase 1 (P1a+P1b): DONE @ 580bc25
- Phase 2 (SARIF+Go): DONE @ 71584f1
- Phase 3 (Tier-1 batch): NOT STARTED
- Phase 4 (docs): NOT STARTED

## Spec
/tmp/forge_multilang_spec_20260710.txt (PM-authored, CP1b-converged)

---

## Phase 1+2 (DONE)

### P1a: MCP allow_main @ 580bc25
- forge_review: allow_main parameter -> FORGE_ALLOW_MAIN=1

### P1b: Worktree error msg @ 580bc25
- Error message points fork-clone users to --allow-main

### CORE CHANGE 1: SARIF hardening @ 71584f1
- json.loads -> raw_decode (trailing noise tolerance)

### CORE CHANGE 2: Go registry @ 71584f1
- GO_TOOL_REGISTRY + detection + priority

---

## Phase 3: Tier-1 Language Batch

Each language follows the spec's Section 4 spike process:
1. Minimal project with ONE known lint
2. Run linter with SARIF flag; check for leading/trailing noise
3. Feed SARIF to _parse_sarif; confirm finding surfaces
4. Add registry entry + real-SARIF fixture test

### Plan 3a: JS/TS (ESLint)

**Task 1: Spike**
- Create /tmp/eslint_sarif_spike/ with index.js containing known lint
- Run: `npx eslint --format @microsoft/eslint-formatter-sarif index.js`
- Check: SARIF clean or trailing noise? Leading banner?
- Feed to _parse_sarif; confirm finding (file/line/rule)

**Task 2: Registry + detection**
- Add JS_TOOL_REGISTRY in detect.py: eslint + eslint-formatter-sarif
- Detection: package.json or *.js/*.ts files
- Update _get_tool_meta + iteration sites

**Task 3: Fixture test**
- Capture real SARIF from spike as fixture
- Test: fixture -> _parse_sarif -> Finding with correct file/line/rule

**Acceptance:** ESLint finding surfaces end-to-end via forge

### Plan 3b: C# (Roslyn)

**Task 1: Spike**
- Create /tmp/roslyn_sarif_spike/ with Program.cs containing known lint
- Run: `dotnet build /p:ErrorLog=sarif.json`
- Check: SARIF format, noise handling
- Feed to _parse_sarif; confirm finding

**Task 2: Registry + detection**
- Add CS_TOOL_REGISTRY: dotnet build + SARIF logger
- Detection: *.csproj or *.cs files

**Task 3: Fixture test**
- Real SARIF fixture + _parse_sarif test

### Plan 3c: Java (PMD)

**Task 1: Spike**
- Create /tmp/pmd_sarif_spike/ with Main.java containing known lint
- Run: `pmd check -d . -R rulesets/java/quickstart -f sarif`
- Check: SARIF format, noise handling
- Feed to _parse_sarif; confirm finding

**Task 2: Registry + detection**
- Add JAVA_TOOL_REGISTRY: pmd + sarif renderer
- Detection: pom.xml or build.gradle or *.java files

**Task 3: Fixture test**
- Real SARIF fixture + _parse_sarif test

### Plan 3d: C++ (clang-tidy/cppcheck)

**Task 1: Spike**
- Create /tmp/cppcheck_sarif_spike/ with main.cpp containing known lint
- Run: `cppcheck --addon=sarif .` or `clang-tidy --export-fixes=sarif.json`
- Check: SARIF format, noise handling
- Feed to _parse_sarif; confirm finding

**Task 2: Registry + detection**
- Add CPP_TOOL_REGISTRY: cppcheck + sarif adapter
- Detection: CMakeLists.txt or *.cpp/*.hpp files

**Task 3: Fixture test**
- Real SARIF fixture + _parse_sarif test

### Plan 3e: Ruby (RuboCop)

**Task 1: Spike**
- Create /tmp/rubocop_sarif_spike/ with main.rb containing known lint
- Run: `rubocop --format sarif .`
- Check: SARIF format, noise handling
- Feed to _parse_sarif; confirm finding

**Task 2: Registry + detection**
- Add RUBY_TOOL_REGISTRY: rubocop + sarif format
- Detection: Gemfile or *.rb files

**Task 3: Fixture test**
- Real SARIF fixture + _parse_sarif test

### Plan 3f: Swift (SwiftLint)

**Task 1: Spike**
- Create /tmp/swiftlint_sarif_spike/ with main.swift containing known lint
- Run: `swiftlint lint --reporter sarif`
- Check: SARIF format, noise handling
- Feed to _parse_sarif; confirm finding

**Task 2: Registry + detection**
- Add SWIFT_TOOL_REGISTRY: swiftlint + sarif reporter
- Detection: Package.swift or *.swift files

**Task 3: Fixture test**
- Real SARIF fixture + _parse_sarif test

### Plan 3g: PHP (PHPStan)

**Task 1: Spike**
- Create /tmp/phpstan_sarif_spike/ with index.php containing known lint
- Run: `phpstan analyse --error-format=sarif .`
- Check: SARIF format, noise handling
- Feed to _parse_sarif; confirm finding

**Task 2: Registry + detection**
- Add PHP_TOOL_REGISTRY: phpstan + sarif format
- Detection: composer.json or *.php files

**Task 3: Fixture test**
- Real SARIF fixture + _parse_sarif test

---

## Phase 4: Docs

**Task 1: output_format keys doc**
- Document all supported output_format values
- Map to parser functions in PARSER_DISPATCH

**Task 2: Per-language onboarding guide**
- Step-by-step process from spec Section 4
- Example: Go (golangci-lint) and JS (ESLint)

**Task 3: gate.yaml template update**
- Add Go/JS examples to default gate.yaml template

---

## Acceptance Gates (all phases)
- Each language: known-answer spike IS the acceptance oracle
- Real-SARIF fixtures (not hand-written)
- Full three-cycle review on parser/registry changes
- Step 0: py_compile + ruff + non-ASCII

## Dependencies
- Phase 2 (SARIF+Go) is prerequisite for Phase 3
- Phase 3 languages are independent (parallel-safe)
- Phase 4 depends on Phase 3
