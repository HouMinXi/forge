# Review assignment (DeepSeek): implementer-readiness + coverage

Read the shared briefing first:
/home/houminxi/code/forge/.planning/phases/54-router-onboarding-compat/cp-artifacts/cp1b-r1-briefing.md

Then review the plan:
/home/houminxi/code/forge/.planning/phases/54-router-onboarding-compat/54-01-PLAN.md

Your angle (play to your strength, ignore the rest):
1. IMPLEMENTER-READINESS — could you implement every task verbatim from the
   plan text alone, without asking a single question? For each task, mentally
   type the code: every file read, every function signature, every call site
   argument, every test assertion. Flag every spot where the plan text forces
   the implementer to guess.
2. ACCEPTANCE CHECKABILITY — every acceptance criterion must be decidable by
   a command, a source assertion, or an observable behavior. Flag any that
   two readers could score differently.
3. COVERAGE — walk REQUIREMENTS.md ROUTER-02..05 and CONTEXT.md D-01..D-12
   against the plan's tasks. Anything mapped but not actually implemented by
   a task's action text is a finding.

Anti-pattern guard (your known failure mode): do NOT re-raise items already
adjudicated in the briefing's history section — those are closed. Do NOT
propose redesigns of locked decisions (D-01..D-12); adjudicate the declared
positions A-E instead. Over-analysis beyond the plan's actual text costs a
full review round.

Verify every finding against the real source files under
/home/houminxi/code/forge/src/code_forge/ and /home/houminxi/code/forge/tests/
before reporting it. Follow the briefing's output contract exactly, ending
with `SCORECARD: B=<n> H=<n> M=<n> L=<n>`.
