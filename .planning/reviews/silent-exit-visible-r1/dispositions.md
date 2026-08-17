# Forge review R1 dispositions -- fix/silent-exit-visible, 2026-08-16

Review run: LOCAL mode, deepseek-nocache, diff = cli.py +136 (+2 new
test files). Log + receipts + state archived alongside this file.

## Round 0

1. [adversarial, CONFIRMED] "SystemExit handler catches exit code 0,
   converting a legitimate success exit into EXIT_CLI_ERROR."
   REJECTED -- user decision (AskUserQuestion 2026-08-16): any
   SystemExit escaping the pipeline means the review never completed;
   mislabeling it as a CLI error is the point. Message carries the
   original code, so the distinction stays visible.

2. [adversarial, CONFIRMED] "banner uses cwd.name, misleading from a
   subdirectory." ACCEPTED -- _repo_display_name() now resolves the
   git top-level basename (cli.py); unit + injection verified.

## Round 1

3. [adversarial, UNCERTAIN] "KeyboardInterrupt re-raise violates
   main() return annotation." PARTIAL -- adopted the UX half: print
   + raise SystemExit(130) (no interpreter traceback, keeps the 130
   convention). Annotation-purity half dismissed: every abort path
   violates the int annotation by nature.

4. [adversarial, CONFIRMED] "exc.code can be None; message prints
   'None'." ACCEPTED -- message reports the effective interpreter
   code (0 for bare sys.exit()). Injection-verified.

5. [adversarial, CONFIRMED] "override note shown even when the env
   var is unset." ACCEPTED, then refined by round 3's finding: the
   note now states which source determined the timeout, including
   the ignored case (see round 3).

## Round 2

6. [expert] "import traceback inside handler; move to top level."
   REJECTED -- matches the existing except Exception handler's local
   import (house style, same file).

7. [expert] "override variable name should be a shared constant."
   REJECTED -- single display site; the literal's owner is
   llm_invoke.py. A cross-module constant for one string is
   over-engineering at this size.

8. [adversarial, CONFIRMED] "except blocks placed after the existing
   handler are syntactically invalid." DISMISSED -- multiple except
   clauses are valid Python; ground truth: py_compile, 3340-test
   suite, and live smoke all executed the code. Falsifier
   rubber-stamped a hallucination.

## Round 3

9. [adversarial, CONFIRMED] "override note shown regardless of
   whether the env var is actually set." ACCEPTED -- three-state
   note via _banner_timeout_note(): from-env / ignored-because-
   backend-wins / absent. Live smoke showed all three. The ignored
   case exposed a deeper resolution contradiction, filed separately
   (todos/pending/timeout-env-resolution-contradiction-20260816.md).

R2 launched against the fixed diff with fresh state (R1 state moved
to .code-forge-r1-archived in the worktree).

## R2 (fixed diff)

10. [qodo, CONFIRMED, 2492] "_repo_display_name catches only
    SubprocessError; git missing raises FileNotFoundError."
    ACCEPTED -- both subprocess guards (repo name + sha) now catch
    (SubprocessError, OSError). Injection-verified.

11. [adversarial, CONFIRMED, 2524] "_banner_timeout_note derefs
    backend.timeout_s without a None check." DISMISSED -- the code
    short-circuits on `backend is not None`; the dedicated unit test
    test_backend_none_env_set passes. Falsifier rubber-stamped the
    hallucination (same pattern as R1's syntax-error finding).

## R3 (fixed diff)

12. [qodo, CONFIRMED, 2527] "timeout_s=0 is falsy; use `is not
    None`." DISMISSED -- effective_invoke_timeout_s documents
    timeout_s=0 as "not configured" (falls through to the next
    priority); `(timeout_s or 0) > 0` matches the resolution chain
    exactly. `is not None` would make a 0-valued backend win,
    contradicting the pipeline's own contract. Unit test
    test_backend_timeout_unset_env_set pins this.

13. [expert, CONFIRMED, 2533] "env_raw printed unsanitized; ANSI
    escapes corrupt the terminal." ACCEPTED -- non-printable
    characters stripped before embedding. Injection-verified.

14. [adversarial, UNCERTAIN, 3017] "banner's timeout may not match
    the pipeline's." DISMISSED -- the banner calls
    effective_invoke_timeout_s, the single shared resolver used by
    llm_invoke and the MCP watchdog; divergence is impossible by
    construction.

## R4 (after sanitize fix)

15. [expert, CONFIRMED, 2501] "git probes without a timeout can hang
    the banner." ACCEPTED -- both git subprocess calls carry
    timeout=5. Injection-verified via a kwargs-capturing test.

16. [expert, CONFIRMED, 1659] "limit the SystemExit catch to non-zero
    codes." DISMISSED -- substance-free repeat of R1 #1 (user decision
    stands: any pipeline SystemExit = review never completed).

17. [adversarial, UNCERTAIN, 3034] "parse_diff_files may raise on a
    malformed diff, crashing the banner." ACCEPTED -- wrapped; banner
    degrades to diff: n/a.

18. [adversarial, CONFIRMED, 3021] "effective_invoke_timeout_s may
    raise, crashing the banner." ACCEPTED -- wrapped; banner degrades
    to timeout n/a. Injection-verified (raising resolver test).

Banner design principle settled this round: diagnostics must never be
a crash surface (same principle as progress.emit's except).

## R5 (launched after the defensive fixes)

R5/R5b on deepseek-nocache died at t+1200s with zero bytes -- the LAN
gateway's upstream (free sn-deepseek-flash) was dead ~05:03-05:45; the
gateway recovered later. Rerun as R5c on deepseek-direct (v4-pro per
user direction; both configs switched from v4-flash). Two further
observations filed separately: the connect/TLS-phase hang escapes the
900s idle bound (triage doc), and the 401 "auth header format" episode
was the same infra window (N100 path also 401'd; all three models
answered once the window closed).

## R5c (v4-pro, paid)

19. [qodo, 3054] + 21. [adversarial, 3024] "banner reads os.environ
    while _run receives an env parameter." ACCEPTED (one line): the
    note lookup now uses the env parameter. The resolver still reads
    os.environ itself -- pre-existing, tracked in the timeout-
    resolution backlog item.

20. [qodo, 2525] + 22. [adversarial, 2525] "empty FORGE_LLM_TIMEOUT_S
    claims an honored override." ACCEPTED -- empty/whitespace values
    treated as unset. Injection-verified.

23. [expert, 2511] "repo name unsanitized; control chars corrupt the
    banner line." ACCEPTED -- same printable filter as the timeout
    value. Injection-verified.

24. [expert, 1659] + 25. [adversarial, 1659] "except SystemExit also
    catches argparse --help/--version." DISMISSED -- structurally
    false: parse_args runs before the review dispatch with its own
    SystemExit handler (cli.py:1628-1631); the try wraps _run only.
    Subprocess tests (--version, exit-code forwarding) pass.

## R6c (launched after the R5c fixes)

26. [qodo, 2531] + 27. [expert, 2550] + 28. [adversarial, 2531]
    (same substance) "non-numeric/zero/negative env values claimed as
    honored." ACCEPTED -- _banner_timeout_note now mirrors the
    resolver: only a positive integer produces the note.
    Injection-verified.

29. [expert, 3061, UNCERTAIN] "backend.name unsanitized." ACCEPTED --
    printable filter in _startup_banner_line. Injection-verified.

30. [qodo, 3034] + 31. [expert, 3034] "pass the env raw value to the
    resolver so value and note cannot contradict." DISMISSED -- the
    resolver's API takes an int timeout, not an env raw string, and
    its contract is os.environ; with the validation from 26-28 the
    value and note derive from the same env and cannot contradict
    through main() (env == os.environ). The custom-env embedder
    divergence is documented in the timeout-resolution backlog item.

32. (6/7 of the round) was DISMISSED by the falsifier itself after a
    313.6s deliberation.

## R7c (launched after the R6c fixes)

33. [qodo, 3052, CONFIRMED] "banner labels --head <sha>/WORKING/INDEX
    targets with the HEAD hash." ACCEPTED -- the banner now uses
    resolved.head_sha (the resolution layer's own target sha) and the
    HEAD probe is deleted. Injection-verified.

34. [expert, 1659, CONFIRMED] argparse repeat of R5c 24/25. DISMISSED
    (substance-free repeat).

35. [expert, 3046] + 36. [adversarial, 2548] env/os.environ repeat of
    R6c 30/31. DISMISSED (substance-free repeat; timeout-resolution
    backlog item covers it).

37. [expert, 3037, UNCERTAIN] "extract _emit_startup_banner helper."
    DEFERRED -- refactor suggestion, not a defect; filed as follow-up
    alongside the other banner items.

38. [adversarial, 2501, UNCERTAIN] "two serialized git probes, worst
    case 10s." ACCEPTED -- the HEAD probe is gone (see 33); only the
    single toplevel probe remains.

## R8c (launched after the R7c fixes)

39. [qodo, 3046, CONFIRMED] env-divergence repeat. DISMISSED.
40. [qodo, 2577, CONFIRMED] "sha not sanitized; --head can carry
    control sequences." ACCEPTED -- printable filter applied before
    the target string is built. Injection-verified (caught an
    order bug: the original edit sanitized after use).
41. [expert, 2548, CONFIRMED] "note duplicates the resolver's
    precedence rule; expose the winner instead." FOLDED INTO the
    timeout-resolution backlog item -- the priority reorder will
    reshape the resolver; until then the mirrored rule is pinned by
    unit tests and the note's docstring.
42. [adversarial, 1659, UNCERTAIN] argparse repeat (4th). DISMISSED;
    the falsifier itself dismissed the sibling instance this round.
43. [adversarial, 2540, CONFIRMED] "empty suffix violates the
    docstring contract." ACCEPTED as a docstring correction: the
    note's contract is now "explains env involvement only", matching
    the honest behavior.

Also this round the falsifier DISMISSED three of the eight findings
itself (3/8, 4/8, 7/8) -- the model panel converged with the
dispositions. Loop exit: remaining findings are substance-free
repeats (39, 42) or backlog-folded (41); continuing rounds against
the oscillating panel would be trap #4. Review complete.
