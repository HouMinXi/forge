# Scope and historical ticket input-admission controls

This evidence-only supplement targets accepted G06 and T11–T12/G12 in
[#441](https://github.com/HouMinXi/forge/issues/441). Product baseline is
`d4917e9ba925e3330d4506bc45a24441a98a3bc0` (tree
`61f157d51bb99219c1d2461eb86107a861d30468`, #480). It does not recover the
original 78-test port, approve a replacement, or close G12, #441 or #33.

## New evidence

The six collected cases in
`tests/test_scope_admission_inputs.py::test_input_refusal_preserves_scope_and_ticket_evidence`
combine `exact-diff`, `lexical-root`, `canonical-root` with `cold` and `warm`.
They use the real registry/SQLite fixture, production runner, capture, scope
publisher/reader, and explicit ticket export. The optional provider is used when
installed; the fallback fixture is not provider qualification.

- Cold: change the input before the first query. No queries, receipts, tickets,
  error clearing or current delivery may occur.
- Warm: first obtain one real caller finding, scope receipt and saved ticket.
  Then change the input. No cached finding, query, receipt, ticket, error clearing
  or current delivery may be credited. All previous file inodes/bytes and owned
  records remain unchanged. The held independent context still reads the old
  physical scope receipt as historical evidence.
- Exact diff adds a newline. Lexical root changes to a symlink of the same target.
  Canonical root retargets the original alias to the sibling while retaining the
  same lexical spelling. These are three distinct supported admission checks,
  not arbitrary filesystem freshness or inode-generation guarantees.
- Restoring the original input on the same live host permits the real caller
  result: cold then performs the query/export; warm reuses findings without new
  queries or scope/ticket publication. This is not HOLD, restarted-process
  recovery, or a new host identity.

The pre-existing warm-cache tests already reject these changed inputs. The new
cases make the cold boundary and scope/ticket evidence explicit. No defect in
unchanged production code has been demonstrated.

## Dedicated source omissions

In an owned isolated source copy, remove exactly one conjunction from
`DiagnosticCapture.admit`, leaving every other check unchanged:

| Check removed | Exactly two failing parameters | First semantic failure |
| --- | --- | --- |
| `Path(repo_root) == self._root` | lexical-root cold/warm | real/cached finding incorrectly returned |
| `Path(repo_root).resolve() == self._canonical` | canonical-root cold/warm | cold clears held error view; warm returns cached finding |
| diff SHA-256 equality | exact-diff cold/warm | real/cached finding incorrectly returned |

Each unchanged baseline and byte-exact restoration passed all six cases. Each
omission failed only its two parameters, with zero setup errors/skips; other
four remained green. The first assertion failures demonstrate rejection-order
oracles, not that every later scope/ticket assertion separately killed a mutant.
These deliberately introduced failures are test discrimination evidence, not
baseline product failures. Production bytes were restored before regression.

Reproduce from a disposable copy of the pinned revision plus this test patch,
using Python 3.14.10 and pytest 9.2.0:

```sh
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
python -m pytest -p pytest_asyncio.plugin -p no:cacheprovider -q \
  tests/test_scope_admission_inputs.py
```

Preserve the original `_cross_repo_diagnostics.py` bytes and hash. Run baseline,
then each single source omission independently, checking exact parameter and
assertion identities, then restore byte-for-byte and repeat. Import/setup errors,
timeouts, unexpected failures or restoration mismatch stop qualification.
Do not publish an omitted guard or accumulate repeat runs as new coverage.

## Remaining acceptance and product boundaries

#478 adds current-execution counts-only scope consumption; #480 adds explicit
caller-held tickets for historical scope counts. Neither changes the old closed
v1 diagnostic envelope, Verdict or MCP. Tickets are unsigned, private caller
state, not proof against same-UID replacement, nor current review credit.
The old diagnostic Record/PublicationResult caller-interface decision under
G04/G09 remains distinct from scope-history usability. No implicit latest lookup
or historical diagnostic export is introduced by these tests.

Original-port disposition, complete G/T matrix, G01/G02/G08/G10 contract approval,
G11 asynchronous/outer cleanup limits, T14 recovery disposition, and same-revision
G13 independent acceptance remain open. Multi-hop graph analysis and the other
four parent roadmap capabilities are not implemented by this slice. The existing
scope/ticket documentation remains authoritative for its narrower support domain.

## Composition recheck

After #481 merged, this candidate was composed on
`104508fa901cb98a540ffda57b45de8ab55e1d7c`. Its manual diagnostic workflow/schema,
test changes and sixteen public identities are retained; all three upstream
blobs were verified (manifest comparison removes only this candidate's six nodes
and module). The admission production code and test evidence are unchanged.
The original focused regression passed 528 cases; the current composition plus
`test_cli_capture_stock.py` is recorded separately, not added to that count.
