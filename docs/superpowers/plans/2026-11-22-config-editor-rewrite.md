# Config editor rewrite — execution record and remaining work

**Status:** the document layer, every mutation, the CLI rewiring, the locator deletion and the test
migration are done and green in the worktree. What remains is the review gate (seven lenses; no clean
round yet) and the delivery decision.

**Measured on the candidate tree**
- `scripts/forge-provider.py` — 1884 lines, was 1426 before the rewrite.
- `src/code_forge/config_edit.py` — 1197 lines.
- `tests/test_config_edit.py` — 81 tests; `tests/test_forge_provider.py` — 262 tests; all pass.
- Affected-surface run: fourteen modules, 924 tests.
- Guard withdrawals: fourteen rows (twelve module, two script), all red when withdrawn and green
  when restored, `mismatches: []`
  (`.planning/quick/20261121-provider-cleanup/config-edit-build/withdrawals-{repo,script}.json`,
  merged in `results.json`).

Why this plan is short: v1 and v2 were written first and reviewed twice; the reviews found 30+
defects in documents that had never run, including a `load_document` that was imported and never
defined and a locality check that refused every insertion. Building the module first and writing
the plan from what ran is the order that works.

## What the built module does

> `Document(text)` — round-trip load; the line ending comes from the bytes, the mapping indent from
> the file's own `backends:` block, `preserve_quotes` on. `dumped()`, `span(name)`, `reload()`,
> `.notes`, `.changed`, `.mixed_endings`.

> `locality(before, after, spans, insertion_after=None)` — input-coordinate opcodes; a change is
> local when it sits inside a declared span; an insertion is local inside a span or at the declared
> insertion point.

> `_finish(doc, before, spans, insertion_after, allow_lost)` — returns False when the dump did not
> change (a no-op writes nothing); otherwise it compares the result against **`doc.source`**, the
> bytes on disk, and refuses when the change reaches outside the spans, when a comment was lost or
> reassigned to another entry, or when the file mixes CRLF and LF. On refusal it reloads the
> document so the caller's copy is not half-mutated.

Mutations: `add_backend` (writes `type`, `format`, `base_url`, `api_key_env`, `model`,
`max_tokens`, `timeout_s`, `stream`, and headers when given), `set_field`, `set_fields`,
`set_headers`, `set_fields_and_headers`, `set_default` (declares one or two spans: the entry gaining the
flag and the one losing it), `rename_backend`, `remove_backend`.

`remove_backend` refuses: the last entry, the default entry, an entry hosting an anchor a survivor
aliases, a removal that would change the resolved backend, and a removal that would delete a
comment or hand it to the next entry. It reports a comment lost with the removed key line.

Measured behaviour that shaped the code, each first found by a review of the documents:

1. **Locality needs input coordinates.** Comparing output-coordinate region ends against
   input-coordinate spans refused every insertion (`add`, `set --header`).
2. **Locality must compare against the file, not the dump.** With `_finish` diffing the pre-mutation
   dump, a four-space config was re-indented by a plain field edit.
3. **`set_default` touches two entries.** One declared span refuses it; the module declares both.
4. **`set_field`'s equality short-circuit proved nothing.** Withdrawing it left every test green,
   because an idempotent assignment dumps identically. Deleted. The no-op guarantee is now
   `_equivalent`: a value that reads back the same writes nothing. It is semantic but
   type-strict — a stored `2400` and `--timeout-s 2400` agree, while `2400.0` and `"2400"` do not,
   because the timeout reader takes a real int; those two are rewritten to the int, which is the
   repair.
5. **The name guard must use PyYAML.** `yes`/`on` are strings to ruamel and `True` to the reader
   forge uses; `1.5`, `null`, `""`, `a: b` all fail the probe.
6. **"Declared" and "addressable" are different refusals.** A one-line mapping and a block alias
   parse and declare the entry but cannot be spliced; reporting them as "not declared" made `sync`
   print success while skipping the file. `NotLocatable` carries that case.

## What the integration changed, measured

Every item below was found by a failing test, not by reading:

| what broke | why | fix |
| --- | --- | --- |
| `remove` accepted an untrusted file | `cmd_remove` pre-seeded the checked digest, so the gate saw "already approved" | `_load_for_edit(..., require_record=True)` re-judges with `trust_problem`; only `remove` asks for it |
| a stray carriage return in every CRLF line | the loader kept the byte inside tokens and the writer added the line break again | parse LF-only, re-apply the file's line ending on dump; a file with both endings is refused |
| a four-space file was re-indented by a one-field edit | the loader always used the canonical two-space mapping indent | `_detect_indent()` reads the file's own indent |
| `span("beta")` found `retry: beta:` | the search was over the whole file | `entry_span()` searches only at the key indent inside `backends:` |
| writing `default` on `beta: *shared` changed both entries | an alias is the same object | `_aliased()` refuses the write and names the partner |
| a deletion dropped a comment that followed the block | ruamel attaches that comment to the deleted block's last node | refused, and the comment is named |
| a comment on the removed key line blocked the removal | the same guard, with no exception for the entry's own line | allowed, with the text in `notes` |
| `set_default` could not clear a commented flag | the same guard | the dropped comment is allowed and reported |
| an empty backend body (`beta:`) could not take a field | `_entry` insisted on a mapping | a null value becomes an empty mapping |
| `remove` refused when the survivor never resolved | resolution errors were treated identically before and after | an error only the removed entry caused is allowed with a note; a survivor error still refuses |
| a flow block aborted a sweep with `KeyError` | the anchor-host check called the raw locator, bypassing the conversion | it goes through `Document.span`, so the file is refused and counted |
| `set` wrote one field at a time | each field was its own load and write | `set_fields_and_headers` applies fields and headers in one document and one write |

## Guards, and how each is proven

Each guard below is named with the test that covers it. `injections.md` is the record of which of
them were verified by removing the guard and watching that test fail; a row without an injection has
a test, not proof. The recorded runs are
14 withdrawal rows (`mismatches: []`) plus the injections listed in
`review-r21-r3/injections.md`; that file, not this table, is the record of what was executed.

| guard | test |
| --- | --- |
| `api_key_env`, not `key_env` | `test_add_declares_a_working_backend` |
| a name the reader cannot address is refused | `test_adding_refuses_a_name_that_cannot_be_addressed` |
| a no-op writes nothing | `test_same_value_is_not_a_change` |
| `set_default` clears the other flag | `test_default_moves_the_flag` |
| removing the default entry is refused | `test_removing_the_default_is_refused` |
| removing the last entry is refused | `test_removing_the_last_entry_is_refused` |
| a removal that changes the resolved backend is refused | `test_removing_an_entry_that_would_change_the_effective_backend_is_refused` |
| a removal that would hand a comment on is refused | `test_a_removal_that_would_hand_its_comment_on_is_refused` |
| removing an anchor host a survivor aliases is refused | `test_removing_the_anchor_host_is_refused_because_a_survivor_aliases_it` |
| a change outside the declared span is refused | `test_a_change_outside_the_span_is_refused_where_the_mutation_ends` |
| a lost key-line comment is reported | `test_a_key_with_a_trailing_comment_is_declared_and_removable` |
| `remove` refuses a file the trust record does not cover | `test_refuses_to_authorize_an_untrusted_survivor` |
| a write failure is not reported as kept | `test_a_write_failure_is_not_summarised_as_kept` |
| `NotLocatable` is not reported as "not declared" | `test_a_flow_block_is_declared_but_not_addressable` |
| a non-mapping root is refused, not crashed | `test_a_root_that_is_not_a_mapping_is_refused_not_crashed` |
| `set_fields` refuses an aliased entry | `test_aligning_fields_refuses_an_aliased_entry` |
| a value that reads back the same is not rewritten | `test_a_value_that_reads_back_the_same_is_not_rewritten` |
| `set` writes fields and headers in one write | `test_set_writes_fields_and_headers_in_one_write` |
| a flow block is refused instead of aborting the sweep | `test_remove_on_a_flow_block_refuses_without_a_traceback` |
| `yaml.width` keeps long neighbour scalars intact | `test_a_long_neighbour_scalar_survives_an_edit` |
| a `headers:` that is not a mapping is refused | `test_headers_that_are_not_a_mapping_are_refused` |
| an int field stored as a float is still a change | `test_an_int_field_stored_as_a_float_is_still_a_change` |
| `set` writes a field the entry lacks | `test_set_writes_a_field_the_entry_does_not_carry_yet` |
| header names removed by `set --header` are reported | `test_set_reports_the_header_names_it_replaces` |
| `sync` writes once, so a later refusal leaves the file alone | `test_a_later_refusal_leaves_the_file_as_it_was` |
| `sync` reports a file it cannot address | `test_a_file_it_cannot_address_reaches_the_status` |
| a comment at entry indent that ruamel keeps inside belongs to the entry | `test_a_comment_at_entry_indent_that_ruamel_keeps_inside_is_part_of_it` |
| a file whose levels step differently still edits, with a note | `test_a_file_whose_levels_step_by_different_amounts_still_edits` |
| a header key YAML made an integer is reported as text | `test_a_header_key_that_is_not_text_is_reported_as_text` |
| `sync` reports the header names it replaces | `test_sync_reports_the_header_names_it_replaces` |
| `sync` counts what happened, not what it attempted | `test_the_summary_counts_what_happened_not_what_was_attempted` |

## How to run it

```
# affected surface (the iteration gate)
PYTHONPATH=src $PWD/.venv/bin/python -m pytest tests/test_forge_provider.py tests/test_config_edit.py \
  tests/test_backend.py tests/test_backend_timeout_s.py tests/test_trust.py tests/test_trust_lenses.py \
  tests/test_trust_store_isolation.py tests/test_trust_fixture_allocation.py tests/test_user_config.py \
  tests/test_focus.py tests/test_cli_trust.py tests/test_doctor.py tests/test_installed_wheel_artifact.py \
  tests/test_ci_stock_runner.py -q

# guard withdrawals (drivers live in the planning directory)
$PWD/.venv/bin/python .planning/quick/20261121-provider-cleanup/withdrawals/withdraw_repo.py
$PWD/.venv/bin/python .planning/quick/20261121-provider-cleanup/withdrawals/withdraw_script.py
```

The full suite runs once before delivery, not as the iteration gate: 649 `tests/test_*.py` files at
the top level, 655 `test_*.py` files anywhere under `tests/`, 43 610 tests collected on this host (18
modules fail collection here because they need a live gate or missing packages), about six minutes,
including mutmut, semgrep, docker, Go and Rust integration, and 16 failures that reproduce on the
baseline HEAD on this host (environment, not this change).

## Work still open

- **Review gate.** Twenty rounds have run. Each returned findings; rounds 18 and 19 ended with no
  P0/P1/P2 at all but each of them still changed code, so neither counts as a clean round and none is
  banked. Round 21 runs
  on the current candidate. Reports and dispositions are under
  `.planning/quick/20261121-provider-cleanup/review-r21*/`.
- **Delivery.** The candidate is uncommitted on `fix/provider-remove-pr`. Committing, pushing and
  updating the pull request are a separate decision behind the review gate and the user's approval;
  PR #551 is the published state of the earlier change.
