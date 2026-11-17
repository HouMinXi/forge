# Mutation testing interface

Status: implemented. This note records the decision behind Forge's mutation
interface and where the shipped code differs from the design written on
2026-09-22. The design is the set of commits on
`feat/mutation-interface-design`, ending at `0bdba022e`.

## The decision

Forge keeps the language-specific mutation tools and takes responsibility for
one thing those tools do not: deciding whether a requested check actually ran.
An empty survivor list is not evidence that the measurement finished. A run
that produced no output, a parser that dropped native outcomes, and a detached
result that omitted the infrastructure-error list all look identical if the
only thing recorded is "no survivors".

The scope asked for covers Python, Go, Rust, JavaScript, TypeScript,
stylesheets, Dockerfile, and executable configuration. Their tests are not all
variants of a pytest command, so a single Python-shaped runner cannot host
them.

The chosen shape is a small internal adapter interface plus a mutation block in
the existing gate configuration. A declaration names a reviewed adapter, the
source scope, the behavioral tests, the prepared environment, and explicit
limits. It cannot add new executable hooks, and it cannot declare its own
result successful. A project that has no mutation block keeps the legacy
Python configuration. Writing both the new block and the legacy mutation keys
is an error, and an old result file is replaced by a fresh measurement rather
than merged with one.

Native tools stay responsible for generating mutants and running them. Formats
without a validated native tool go through a bounded patch-corpus adapter,
which applies reviewed, source-bound mutations and runs the target's own
behavioral tests. That is corpus coverage. It is not a claim that every
construct in those formats is understood.

## What was rejected

A public plugin platform would have added compatibility and executable-loading
duties before any consumer existed. A general recipe language would have
hidden the orchestration inside configuration. Both were deferred.

Adding more language branches to the Python runner would have spread that
runner's assumptions into languages that do not share them. Leaving the gaps
documented would have cost nothing and covered nothing. Shipping Python alone
was accepted as a checkpoint, not as completion.

## What the implementation has to guarantee

The core keeps baseline evidence, the complete set of outcomes, the exact
source identity, and the cleanup status. It compares its normalized results
against the native tool's own inventory of generated mutants. Two result lists
that agree with each other can still both be missing the same mutant, so
agreement between lists is not the check; agreement with the inventory is.

The comparison comes before any new adapter. A gate that reports failure where
it used to report an advisory will surface missing tools and weak tests that
were previously invisible, and the migration notes have to say so rather than
quietly downgrade the result.

Execution needs a real isolation boundary. Copying the tree into another
directory does not protect the host. The Linux boundary uses namespaces and
aggregate resource limits, with no host credentials, no hardware devices, and
no shared service sockets inside it. Tests that build container images need
their own disposable builder. Where the boundary is unavailable, the affected
target is blocked. It is not reclassified as optional.

No performance improvement was measured, so the first version prefers a fresh
measurement over reusing cached results. One operator-owned budget covers both
the engine and its supervision. Runs and targets execute one at a time, with
swap disabled, and a competing request gets a busy response instead of a
second workload. That trades throughput for not having to build a scheduler.
A run that exceeds its budget is incomplete evidence, not a successful test.

## Where the code is

`src/code_forge/mutation.py` is the entry (`run_mutation`). The adapters live
in `src/code_forge/mutation_engines/adapters/`:

| Adapter module | Tool it drives |
|---|---|
| `python_mutmut.py` | mutmut |
| `go_gremlins.py` | gremlins |
| `rust_cargo_mutants.py` | cargo-mutants |
| `js_stryker.py` | stryker |
| `c_mull.py` | mull |
| `ps_mutant.py` | the PowerShell mutator |
| `patch_corpus.py` | the bounded patch corpus |

`isolate.py` holds the execution boundary, `reconcile.py` the comparison
against the native inventory, `classify.py` the versioned in-tree allowlist
classifier, and `targets.py` the declaration parsing. `schemas.py` defines the
result envelope.

## What the design required that is not re-checked here

The design said every adapter must show a strong test catching a real mutation
and a weakened test exposing a survivor, both through the real tool path
rather than a mock. Those demonstrations belong to the commits that added each
adapter. This note does not reproduce them and does not claim they were
re-run for this document.
