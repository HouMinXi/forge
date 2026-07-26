# Phase 42 GREEN/execution -- EXIT verifier (HELD OUT)

**DO NOT hand to the executor or include in any phase context bundle.**
**Frozen:** 2026-07-25, before execution starts (pre-registration).
**Baseline:** planning-local `8e76266099ad`; test branch
`phase-42-cli-key-claim-type` @ `4ac9a78`; main @ `74adbf2`.

Scope: the GREEN phase implementing 42-01 (F8 guard extension) and 42-02
(claim_type oracle). Both plans passed CP1b and the plan-stage exit.

## Part 1 -- mechanical gates

Run in the worktree with `PYTHONPATH=src` forced. A bare import resolves to
the main tree's installed package and would verify the wrong code.

    E1  PYTHONPATH=src python -m pytest tests/ -q          # whole suite green
    E2  grep -c 'axis_claim="review"' src/code_forge/machine.py   -> 0
    E3  grep -c 'axis_claim="manual"' src/code_forge/cli.py       -> 1
    E4  grep -c 'version_sensitive' src/code_forge/ledger.py      -> >=1
    E5  grep -c '_check_backend_credentials' src/code_forge/cli.py -> >=2  (def + call)
    E6  git diff HEAD -U0 | grep '^+' | grep -cP '[^\x00-\x7F]'   -> 0
    E7  grep -rnE '#.*(F[0-9]+:|[Dd]-[0-9])' src/ tests/          -> empty (no plan-ref comments)

## Part 2 -- held-out checks (NOT in any plan or order)

### E8 -- the xfail must be REMOVED, not neutered

`strict=True` makes a stale marker self-reporting: wire it, forget to remove
it, and XPASS fails the suite. That safety only holds if the marker is
deleted outright.

FAIL if: the decorator survives in any form -- including
`xfail(strict=False)`, `xfail(condition=False)`, or a `reason=` reworded to
look current. Those turn a self-correcting guard into a silent pass.

    awk '/def test_write_ledger_derives_claim_type/,0' tests/test_machine_ledger.py
    grep -B8 'def test_write_ledger_derives_claim_type' tests/test_machine_ledger.py | grep -c xfail   -> 0

### E9 -- Test 13's source-text assertion is defeatable; check what it greps

Test 13 asserts the literal `axis_claim="review"` is absent from machine.py.
That is a string match, and a string match is satisfied by indirection:

    claim = "review"
    ledger_append(..., axis_claim=claim, ...)

Test 13 goes green, the hardcode survives. The behavioural test is what
actually protects this, which is the whole point of round 2.

FAIL if: `_write_ledger_rows` reaches `axis_claim` through any literal or
intermediate variable rather than `derive_claim_type(f.source)`. Read the
function; do not grep for it.

### E10 -- all four injections must run, each with its own asymmetry

The plan carries four. Each has a distinct expected signature, and equal-
looking output means the wrong thing was injected:

| # | injection | expected |
|---|-----------|----------|
| 1 | re-hardcode `"review"` | test 13 RED **and** behavioural RED |
| 2 | `derive_claim_type("L1")` | behavioural L1... see below; test 13 GREEN |
| 3 | remove `version_sensitive` write | test 13(c) RED |
| 4 | `derive_claim_type("L0")` (mirror) | behavioural L1 RED, L0 assertions GREEN |

For #2 the L0 assertion is the one that fires (feeding L0 yields "review"
!= "lint"); for #4 the L1 assertion fires (feeding L1 yields "lint" !=
"review"). If a transcript shows the same assertion failing for both, the
executor ran one injection twice.

FAIL if: any injection is reported without pytest output, or #2 and #4
produce identical failure lines.

### E11 -- version_sensitive must survive serialisation, not just construction

`append_row` writes JSON. If it enumerates fields explicitly rather than
using `asdict`, a new field can be silently dropped while every in-memory
assertion still passes.

Check the round trip on disk, not in memory: write a row, read the raw JSONL
line, confirm the `version_sensitive` key is physically present in the file.
The behavioural test reads back through `iter_rows`, which would catch a
total drop, but not a default-masked one.

### E12 -- backward compat proven with a REAL old-format line

Test 12 claims old rows deserialise with `version_sensitive=False`. A test
that constructs the old line by deleting the key from a new row proves less
than one using a genuine pre-Phase-42 ledger line.

FAIL if: no test feeds `iter_rows` a JSONL line that never had the key.

### E13 -- F8 guard: the two new branches each need their own injection

42-01 extends the guard to api_key_file and vertex. Golden Rule 2 is
per-site: removing the `elif` block must break the api_key_file tests, and
removing the vertex `if` must break the vertex test, checked separately.

FAIL if: one combined injection is reported for both, or either is injected
at the pre-existing `api_key_env` guard (that one stays green and proves
nothing -- the trap 42-01 already names).

### E14 -- diff coverage

Every new or modified logic line executed by at least one test.

    PYTHONPATH=src python -m pytest tests/ --cov=code_forge --cov-report=term-missing

FAIL if: any new line in claim.py, the machine.py wiring, the ledger field,
or the cli.py guard extension is unexecuted. Suite-green is liveness, not
coverage -- that distinction already burned this project once.

### E15 -- scope

    git diff --stat 74adbf2

FAIL if: files outside claim.py, machine.py, ledger.py, cli.py,
test_claim_type.py, test_fast_fail.py, test_machine_ledger.py changed.
Watch for opportunistic fixes to neighbouring code -- those are separate
labelled commits, never silent.

## Part 3 -- PM discipline

Standing rule from this session, both violations the same shape: **no absence
claim from a paginated, truncated, or limited view.** Any "X is not there"
comes from an unbounded enumeration, with the command quoted beside it.

Second rule, added after round 2: **do not infer content from a diffstat.**
"36 insertions is too few for an L1 case" was wrong -- a restructure absorbed
the additions. Read the code.

Third: the executor's numbers are a claim. Re-run the suite and the
injections here, with `PYTHONPATH` forced, before accepting any of them.
