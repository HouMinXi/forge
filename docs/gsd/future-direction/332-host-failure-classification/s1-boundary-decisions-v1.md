# S1 boundary: frozen decisions

Decisions for the remaining S1 work on the report boundary. Each one names what
it rules out, so a later reader can tell a decision from an oversight.

## 1. Snapshot nesting depth is weighted, not counted

`copy.deepcopy` recurses, and the cost per level depends on the container.
Measured in this worktree on CPython 3.12.12 at the default 1000-frame limit:

| chain | deepest that copies | first that raises |
|---|---|---|
| dict (`{"k": ...}`) | 497 | 500 |
| frozenset (`frozenset({...})`) | 198 | 200 |

A frozenset level costs roughly two and a half dict levels. Sets are real here —
`State.promoted_fingerprints` is a `set[str]` — so a bound that counts levels
would admit a set-heavy graph deep enough to raise `RecursionError` from inside
the copy it exists to protect.

Depth is therefore charged in dict-equivalent units: one per dict, list or tuple
level, three per set or frozenset level. With `_MAX_DEPTH = 200` that admits a
200-level dict chain (about 400 frames) or a 66-level frozenset chain (about 330).

Ruled out:

- **Counting levels.** Sound for one container kind only, and a mixed graph can be
  worse than any single-kind measurement.
- **`max_depth` on `SnapshotLimits`.** That is a public parameter. A caller
  passing `10_000` would be asking for the exact `RecursionError` this bound
  exists to prevent. A safety bound the guarded party can raise is not a bound.
- **Following `sys.getrecursionlimit()`.** It is process-global and mutable. A
  bound that moves when unrelated code calls `setrecursionlimit` makes the
  refusal depend on import order.

## 2. V2 rides `run.properties.report_classification`, dispatched by schema

SARIF 2.1.0 §3.8.1: *"every object defined in this document **MAY** contain a
property named `properties` whose value is a property bag. This allows SARIF
producers to include information about each object that is not explicitly
specified in the SARIF format."*

The repo already works this way. `report_presentation_v2.py:19-20` defines
`SCHEMA_V2 = "forge-report-classification/v2"`; the block is written under
`run.properties["report_classification"]`; and `report_reader_v2.py:430-441`
dispatches on the `schema` string inside it, mapping
`forge-report-classification/v1` to v1 and `/v2` to v2.

S1 therefore adds no extension mechanism. A v2 artifact is a
`report_classification` block whose `schema` says v2; a v1 artifact says v1. One
key, one discriminator, one reader.

Ruled out:

- **A second `runs[]` entry.** A run is an execution. A second run would assert a
  second execution that did not happen.
- **A sibling key next to `report_classification`.** Two blocks under different
  names would need a second reader path and would break the existing dispatch,
  which keys on `schema` inside a single block.

**Dual output is not the default.** One block carries one schema, so an artifact
is v1 or v2. Whether a consumer needs both stays open per the issue; producing it
needs a container change, not another key.

## 3. The bytes survivor slot stays outside both budgets

`State._legacy_survivor_state` is a `bytes | None` read only by
`_archive_legacy_survivor_state`. No serializer touches it, and `copy.deepcopy`
on `bytes` returns the same object (measured), so the copy allocates nothing.

Charging it would be wrong twice over: the payload is not characters, and the copy
is not a copy. Measured here: 8 MiB and 64 MiB of opaque bytes both under a
1000-node / 1000-character budget are admitted, with a tracemalloc peak under
15 KiB in either case and identity preserved.

Ruled out: charging it "to be safe". A budget entry that can never bind is a claim
that the budget covers that input. It does not.

## 4. A refusal names its check and states the constraint

OWASP's error-handling guidance asks for three things at once — a meaningful
message for the user, diagnostics for the maintainer, and nothing useful for an
attacker — and notes that inconsistency alone leaks structure.

Applied:

- **Every refusal names its own check.** Five sites sharing one message cannot be
  told apart by a reader, and cannot be told apart by a test either. Each of the
  five attribute-map refusals now says which one it is, and every field refusal
  names the field.
- **State the constraint, not the observed value.** No `repr` of what arrived: it
  is attacker-controlled text, and the caller already holds the value.
- **Field names are safe to name.** They are public dataclass fields, not secrets.
  Without the name the caller cannot act.
- **No traceback, path or internal call sequence.** Those go to the maintainer
  through the failure path that already exists.

Ruled out: one generic `invalid snapshot` for every refusal. That is the shape
OWASP warns about — it hides structure from the user while still leaking it
through which inputs pass and which do not.

## 5. A position test must use a value the walk would admit

This entry exists because the first version of this change got it wrong and
called the result coverage.

A negative control built from a value the generic walk already refuses proves
nothing about the position table: delete the table entry, the walk still refuses,
and the test stays green. Six entries were in exactly that state while the commit
message claimed each was proven by deletion.

Every position control therefore uses a value the walk admits — a safe enum, an
int, a record from `_SAFE_RECORDS` — so only the position constraint can refuse
it. Removing any of the 33 entries reddens at least one test.

The same rule covers messages: an assertion matching two different messages pins
neither. Each assertion states the one message its fixture produces.

## 6. `description` may be None, and that is status quo, not a guarantee

`StateFinding.description` is declared `str`, and production never passes None
(48 constructions in `src/`, none null). The boundary still admits None because
it did not inspect this field before, and what the pipeline does with a null is a
split this change does not settle:

- `sarif.py:246` writes `{"message": {"text": None}}` with no guard.
- `report_projection_v2.py:408` calls `_require_str` and quarantines the record.
- `report_projection.py:142` runs it through `_safe_str` and drops it.

An earlier version of the code comment claimed both emitters guard for None.
That was false for two of the three, and a test now records the real behaviour.

Ruled out: refusing None here. It would move where the failure surfaces without
fixing either emitter, and this slice's constraint is to preserve existing
behaviour. The three-way split is a separate defect with its own fix.
