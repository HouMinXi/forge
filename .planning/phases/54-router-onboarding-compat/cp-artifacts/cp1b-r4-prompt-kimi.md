# Review assignment (Kimi) — R4, final confirmation of a text-only micro-fix

Your R3 review (cp1b-r3-kimi.md, B=0 H=0 M=0 L=0) was run against the plan
text BEFORE this round's two tiny fixes landed. A gemini reviewer (running
without repo access, on a standalone copy of the plan text embedded in its
prompt) independently flagged two textual gaps, both confirmed true against
the live plan file and fixed:

1. Task 5's `<action>` never wrote out the explicit
   `from .backend import probe_backend_live` import statement, even though
   the task's own `<behavior>` section repeatedly asserts doctor.py imports
   it function-locally (used as the rationale for the test patch target).
   Every other task in this plan (Task 2, Task 4) writes its new
   function-local import explicitly; Task 5 was the only one that didn't.
   Fixed: the action now reads "_check_backends: add `from .backend import
   probe_backend_live` to doctor.py's existing function-local import block
   (match the style at doctor.py:110/126/128/168)."
2. Task 5's own `<verify>` automated command chain omitted
   tests/test_mcp_server.py, while VALIDATION.md's quick-run command and
   Task 4's verify command both include it. Fixed: Task 5's verify command
   now appends tests/test_mcp_server.py.

Please re-verify ONLY these two edits against the live plan file at
/home/houminxi/code/forge/.planning/phases/54-router-onboarding-compat/54-01-PLAN.md
(Task 5, both the <action> and <verify> blocks) and confirm:
(a) the edits are textually correct (no XML/tag corruption, no dangling
    reference),
(b) the edits don't contradict anything else in the plan or your prior R3
    findings,
(c) no NEW issue is introduced by these two specific lines.

Do not re-review the rest of the plan (already exit-confirmed at 0/0/0/0 by
you in R3). Follow the standard output contract, ending with
`SCORECARD: B=<n> H=<n> M=<n> L=<n>`.
