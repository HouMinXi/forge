# Phase 40: Honest partial results + convergence - Research

**Researched:** 2026-07-14
**Domain:** forge review pipeline (verdict honesty, convergence semantics, LLM pass salvage, large-diff handling)
**Confidence:** MEDIUM overall. Scope provenance HIGH (verbatim quotes located). Code seams MEDIUM (cited from ground-truthed planning/memory docs, NOT re-read from src/ this session -- see Anchor Drift Warning). Items marked UNVERIFIED were not located or not read; the planner must ground them before locking decisions.

**Session note:** this research session was OOM-killed twice mid-investigation.
Everything below comes from evidence gathered BEFORE the kill; areas cut off
are marked UNVERIFIED rather than guessed. No CONTEXT.md exists for this
phase (phases/40-honest-partial-convergence/ was empty), so there are no
locked user decisions; the Design Forks section is the substitute and every
fork is an open user adjudication.

## Summary

Phase 40 bundles five items: (1) F4 partial SARIF, (2) the P3 adversarial
timeout pain, (3) convergence plateau (7.2), (4) prior-round memory (7.3),
(5) large-diff summary/chunking. The single unifying requirement is the
founding principle: "a green verdict is honest or declares what it did not
verify" -- partial results must NEVER silently become a full PASS.

Two halves with different risk profiles, and the schedule of record already
names the split as a USER decision: a mechanical plumbing half (partial SARIF
representation, timeout salvage presentation) and a semantic half (plateau +
prior-round memory) that CHANGES CONVERGENCE SEMANTICS and therefore needs a
before/after eval delta (Phase 44-6 retro-eval exists precisely to close that
gap if 40 lands first).

Key ground-truth correction the planner must absorb: P3's "findings lost"
claim was MISDIAGNOSED per the PM triage -- outlet_c.py already accumulates
findings across passes and returns them; the FAIL verdict on a timed-out pass
is BY DESIGN (INFRA finding, disposition=CONFIRMED, blocks CLEAN). So the F4/
P3 work is a REPRESENTATION and POLICY question (how a partial run is
declared), not a data-recovery question. Much of the raw material already
exists: B5 infra=N SARIF split, Phase 20 "not verified" verdict section,
StateFinding.fingerprint, diagnose_non_convergence, the Phase 43 ledger.

**Primary recommendation:** plan the plumbing half as reuse-and-surface
(extend B5 split + Phase 20 unverified-surface declaration into a per-pass
completion manifest); treat the semantic half as a separate wave gated on an
eval-delta plan; surface all five forks below to the user before locking.
