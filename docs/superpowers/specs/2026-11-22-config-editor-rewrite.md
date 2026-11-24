# Config editor rewrite: parse → mutate → exact print

Status: implemented in the worktree; the review gate has not passed yet.
Scope: the editing core of `scripts/forge-provider.py`, the new `src/code_forge/config_edit.py` module it delegates to, and the tests that pin both.

Measured on the candidate tree: the script was 1426 lines before the rewrite and is 1884 now; the
new module `src/code_forge/config_edit.py` is 1197 lines; `tests/test_config_edit.py` holds 81 tests
and `tests/test_forge_provider.py` 262, all passing.

## Why now

Nineteen review rounds ran against this script and none was clean. The artifact is the disposition
log (`.planning/quick/20261121-provider-cleanup/review-r*/dispositions.md`), one row per finding:

| rounds | rows | per round |
| --- | --- | --- |
| r2-r6 | 122 | 24.4 |
| r7-r11 | 78 | 15.6 |
| r12-r16 | 29 | 5.8 |
| r17-r20 | 34 | 8.5 |

Two corrections to an earlier draft of this spec:

- The total is **263 disposition rows** across r2-r20, not 155. The 155 counted report headings,
  and a heading is not a unit: some rounds wrote one heading for three findings, others one per
  finding. The heading count is retracted.
- The trend is **downward**, 24.4 rows per round early against 8.5 late, so "no convergence" was
  wrong. What did not move is the residual *class*: a YAML spelling or guard interaction the text editor did
  not model (quoted key, alias, anchor on the header, one-line mapping, multi-line flow, merge key,
  CRLF, indentation variant). Counts fall; the class does not, because each fix adds a detector rather
  than removing the need for one.

Each fix in that class added a detector rather than removing the need for one: quoted key, alias,
anchor on the header, one-line mapping, multi-line flow, merge key, CRLF, indentation variant. An earlier draft put a
percentage on how large the class is; two hand counts of the same disposition log disagreed, so the
number is withdrawn and only the class is claimed.

The second cost is in the same artifact: three of the last six rounds caught a regression an
earlier round's fix introduced (user-layer entries became unremovable; `add` wrote into the
previous entry; the parse guard sat at twelve of fourteen call sites).

## Literature

Four channels, run before this spec; every URL below was fetched or searched.

**Theory — edit the data model, print exactly**
- Xie, Schrijvers, Hu. *Biparsers: Exact Printing for Data Synchronisation.* POPL'25.
  https://doi.org/10.5281/zenodo.13939727 (also https://dl.acm.org/doi/10.1145/3704910)
- Bohannon, Foster, Pierce, Pilkiewicz, Schmitt. *Boomerang: resourceful lenses for string data.*
  POPL'08. https://doi.org/10.1145/1328897.1328487
- Foster, Greenwald, Moore, Pierce, Schmitt. *Combinators for bidirectional tree
  transformations.* TOPLAS 29(3), 2007. https://doi.org/10.1145/1232420.1232424
- Ko, Zan, Hu. *BiGUL.* PEPM'16. https://doi.org/10.1145/2847538.2847544

**Practice — round-trip YAML in Python**
- ruamel.yaml overview: preserves comments, block style, key order, flow-style sequences, anchor
  names and merges; per-line indentation is *not* preserved, mapping/sequence indents are
  configurable. https://yaml.dev/doc/ruamel.yaml/overview/
- PyPI: ruamel.yaml 0.19.1, requires-python >=3.9 (fetched 2026-11-22).
  https://pypi.org/project/ruamel.yaml/
- Real Python, YAML in Python — names round-trip parsing as the reason to prefer ruamel over
  PyYAML for hand-edited files. https://realpython.com/python-yaml

**Mature implementations**
- mikefarah/yq (Go): 16345 stars, last push 2026-11-19, explicit comment-preservation mode.
  https://github.com/mikefarah/yq
- kislyuk/yq (Python wrapper): 3004 stars, last push 2026-10-11. https://github.com/kislyuk/yq
- wwkimball/yamlpath: 137 stars, last push 2026-11-06. https://github.com/wwkimball/yamlpath
- pycontribs/ruamel-yaml is a mirror (10 stars); ruamel's home is sourcehut, so star counts there
  carry no signal.

**Channel failures, recorded**
- `openshift/yaml-patch` returns 404 (moved or deleted) — no star figure.
- GitHub REST answered 403 once (unauthenticated rate limit) before `gh api` succeeded.
- No channel returned 401/429; all four (exa, tavily, firecrawl, context7) answered.

**What this repository already has**
- `pyproject.toml` depends on `pyyaml>=6.0` and `unidiff`; no round-trip loader. `yaml.dump`
  appears in `cross_repo.py`, `detect.py` and `eval/export.py`, and all three write *new* files,
  so none carries the comment-preservation requirement.
- No module edits a config in place; the editing surface was the text editor in
  `scripts/forge-provider.py`.

## Options

### A. Parse → mutate → exact print (chosen)

Load with a round-trip loader, mutate the mapping, dump with the same loader. The locator shrinks
to `Document.span(name)`: line coordinates come from the parsed model (`lc.data`), with a text scan
as the fallback for nodes ruamel does not locate, and one reader (`_block_step`) for the mapping's
own indent step. There is no shape taxonomy for a block that parses.

What still needs care, answered below: per-line indentation is not preserved (so the loader reads
the file's own indent), a restructuring edit loses comments on removed nodes (so removals are
checked), and a replaced value's trailing comment lives on the node's `.ca` attribute.

### B. Keep text editing, add a post-write structural check

Kills the "silently wrote the wrong thing" class cheaply, but the shape taxonomy stays: the locator
code remains, and unaddressable shapes stay refused rather than handled.

### C. Freeze

Ship the current candidate, file the residue as issues. Cheapest, leaves the shape class on the
table with the maintenance cost it carries.

## Design for option A

The design is not "parse and dump" — the probes and the design review showed three ways a
whole-file rewrite goes wrong without any shape taxonomy: it can change lines nobody asked about,
it can rewrite a file it did not change, and it can authorise bytes the gate never read.

**C1 — one named mutation per command, one write.** `add`, `remove`, `rename`, `set`,
`set --header`, `default`, and `sync`. `sync` applies rename, add, field alignment, headers and the
default flag to one document and writes once, so a refusal on any step leaves the file exactly as
it was. Field
writes and header writes compose through `set_fields_and_headers`, so `set --header --model X` is
one document and one write. A mutation that changes nothing returns `changed is False`: no backup,
no write, no re-seal. Equality is semantic (`_equivalent`), not textual: `max_tokens: 1.0` in the
file and `--max-tokens 1.0` on the command line are the same setting and write nothing.

**C2 — the edit is local.** A mutation may change only the lines it declares, except for the
whitespace-only normalisation C2.5 describes.

1. **Span of the entry**, from the text: the key line starts the span; it ends at the last
   following line indented deeper than the key (comments included), or before the first non-blank
   line at or left of the key's indent.
2. **Changed regions**, from a line-level diff of input and output.
3. **Local** iff every changed region falls inside one of the spans the mutation declares, in
   **input coordinates**. An insertion is local when its position is inside a span or at the
   declared insertion point. `set_default` declares one or two spans: the entry gaining the flag,
   and the entry losing it when there is one (the first default has no loser); `add` declares the
   insertion point; a removal declares the removed entry.
4. **The comparison is against the text this mutation started from**, not the dump: the "did
   anything change" decision uses the pre-mutation dump, the locality check uses `Document.source`.
   For the first mutation of a command that is the bytes on disk; `sync` applies five mutations to
   one document, so from the second on it is the previous mutation's output. With both from
   the dump, a four-space file was re-indented by an ordinary field edit.
5. **A change outside the spans that only moves whitespace is a note, not a refusal.** The dumper
   takes one mapping indent for the whole document, so a config written 2/4/8 at successive depths
   comes back normalised. Refusing that made 18 of this host's 50 readable configs uneditable;
   re-indentation changes no value, and the note says so (`_collapsed`). A region whose words are
   identical cannot have changed a value, and C2.7 checks that independently. A region that
   **reads** as the same data is tolerated too, which covers a flow collection being re-spaced
   (`[a,b]` → `[a, b]`). The oracle is the reader (PyYAML), not the writer's model, because a
   construct the model drops can still change what the file means: `timeout_s: !!float 1e3`
   re-emitted as `1e3` is a string to PyYAML, and that is refused where it lands outside the entries the mutation
   declared (inside them, C2.7's comparison cannot see it; see C2.7). One limit is known and
   measured: a fragment that cannot stand alone as a document (it ends at a shallower indent than
   it starts) cannot be vouched for. Two configs on this host contain such a fragment (a `deepseek`
   entry in `text-captcha-solver` and in `trinity-router`); that observation comes from the removal
   sweep, where those two are refused rather than mis-read. A field edit is a different sweep over a
   different set of files, so its counts are quoted on their own line and do not add up with the
   removal figures. A removal re-indents the neighbour ruamel
   keeps in the same block, which the hunk-level view cannot see as one move, so a document whose
   only parsed difference lies inside the entries the mutation declared is accepted with a note
   (`_only_backends_moved`). Measured over 545 removal attempts on the same fleet: 336 succeed, 209
   are refused by the deliberate guards (default-first, comment loss, comment reassignment) and
   **0** by the locality sentence; without the fallback the same sweep refuses 67 more on locality
   alone. (A further 3 would be refused by another guard either way, so they are already inside the
   209 and are not added here.)
   Field-edit sweep (its own run): 47 configs found, **44 would change, 0 refused**, the rest
   declare no backend; five of them hold a block scalar and each edit that touched one kept its
   value. The two fragment configs above edit fine here, which is why the sweeps are counted
   separately.

   An earlier version also accepted a region when its top-level section parsed to the same data; that
   was withdrawn after it accepted real edits to other top-level scalars (`outlet`, a shared header
   mapping), and `_top_key_at` could not tell a key line from a line ending in a colon inside a
   comment.

6. **Two checks text locality cannot see**: a removal is refused when it would delete a comment
   written outside the entry being removed (a comment inside it goes with it, per the table below)
   or hand one to the next entry (`_lost_comments`, `_reassigned_comments`, which compare each comment's
   owning entry before and after). Separately, `remove_backend` refuses when the entry hosts an anchor
   a survivor still aliases (`_shared_anchor_hosts`).

Measured on ruamel 0.19.1, each shape matching its expected verdict:

```
field edit:                                  LOCAL
remove alpha:                                LOCAL
four-space file, field edit:                 LOCAL    (indent read from the file)
remove with a comment above the entry:       REFUSE   (the comment would move)
nested header edit:                          LOCAL
remove the anchor host:                      REFUSE   (a survivor aliases it)
add:                                         LOCAL    (insertion after the last entry)
set_default, one span declared:              REFUSE   (the second entry's flag is outside it)
set_default, both spans declared:            LOCAL
```

**Indentation belongs to the file.** `_detect_indent` reads the mapping indent of the file's own
`backends:` block; two spaces is only the default when there is no block to read. A file written at
four spaces keeps four spaces through a field edit (test: `test_a_file_written_at_four_spaces_is_not_reindented`).

**Comments.** The rule the build settled on, after three failing shapes taught it:

| situation | behaviour |
| --- | --- |
| a comment inside the entry being removed | goes with the entry; nothing is reported |
| a comment on the removed entry's own key line | goes with the entry, its text recorded in `notes` |
| a comment above the removed entry, or after its block, that would read as a neighbour's | the removal is refused and the comment is named |
| a comment on a `default:` flag that `set_default` clears | cleared with the flag, text recorded in `notes` |
| an inline comment on a field whose value is rewritten | kept; ruamel pads to the comment's original column, so the run of spaces may change |

**C2.0 — the repository's own gates.** `scripts/lint_changed.py --all` (`ruff check` plus
`ruff format --check` over the tree, with the pinned version named in `pyproject.toml`; the run itself uses whatever `ruff` the interpreter finds) must exit 0, and the
fourteen-module surface must be green, before a candidate is called reviewable. Both are run every
round in the worktree; the formatting half caught four files that the rewrite had left unformatted.

**C2.7 — the dump must read back as the data the document holds.** After a mutation, the output text
is re-parsed by the same loader and compared, type-strict, with the document's own data
(`_same_data`, memoised so an alias graph is linear). Any difference refuses the write. This is what
catches a block scalar that lost its indentation or a quoted number that became a number. It compares
the writer's model with itself, so it cannot see a tag the model drops: editing an entry that holds
`timeout_s: !!float 1e3` writes `1e3`, which the reader then takes for a string. The region oracle of
C2.5 does see that difference, but only outside the entries the mutation declared. Both sides use one dialect, so
a `on:`/`yes:` key that the loader keeps as text is not a false positive (a `010` key is an
integer to both loaders, and `010` against `8` is a real difference). The comparison is
memoised on object identity; without that an alias chain made the check exponential.

**C2.8 — the difference walk is bounded by paths, not by how many lines the file has.** The removal
fallback compares the parsed documents path by path. Three things keep that walk from dominating the
command: a per-pair cache so a shared structure is walked once, a work budget of 200 000 on the paths
produced while walking, and a limit of 10 000 on the paths finally reported. Past either budget the
write is refused rather than given a partial answer, and the refusal names the walk's budget
("differs in more places than the comparison walk can enumerate") instead of borrowing the locality
sentence: a resource limit and an out-of-span edit are different events. What the
limits depend on is how many places differ and how deeply they are nested, not the size of the file: a
config padded with 8 000 untouched keys still edits, while a structure with more differing paths than
the limit is refused. Measured: 10 000 reported paths pass and 10 001 are refused; a 3 000-leaf
document nested 20 deep answers in full.

**C3 — authorisation binds to the bytes that were read.** The gate records the digest of the exact
text it approved; `_load_for_edit` refuses if the file changed since. Edits are built on the
approved text, not on a second read.

**C4 — the CLI-to-backend mapping is explicit.** A backend written by `add` must be loadable by
forge's own reader, which requires `type`, `format`, `base_url`, `api_key_env`, `model`,
`max_tokens`, `timeout_s` and treats `default`/`stream` as booleans:

| CLI | written key | note |
| --- | --- | --- |
| `--key-env` | `api_key_env` | not `key_env`; `backend.py` rejects an api backend without it |
| — | `type: api` | implicit, always |
| — | `stream: false` | implicit, matches the previous writer |
| `--format` | `format` | |
| `--base-url` | `base_url` | required by the reader; `add` refuses a value it cannot quote |
| `--model` | `model` | required by the reader |
| `--timeout-s`, `--max-tokens` | same names | |
| `--header K: V` (repeatable) | `headers` mapping | |

The "added by forge-provider.py <date>" comment is kept as the first line of the inserted block.

**C5 — the semantic guards the text editor had are preserved.**

| guard | where it lives now |
| --- | --- |
| refuse removing the entry the default flag points at | `remove_backend` |
| refuse removing the last entry | `remove_backend` |
| refuse a removal that changes the resolved backend | `remove_backend(guards=Guards(resolve=...))`, compared before and after |
| refuse a name the user layer would take over | `cmd_remove`, because only the CLI knows the layers |
| refuse removing the host of an anchor a survivor aliases | `remove_backend` via `_shared_anchor_hosts` |
| report a lost key-line comment | `remove_backend`, in `notes` |
| refuse a removal that would hand a comment on | `remove_backend` via `_reassigned_comments` |

The module deliberately does **not** model layers: it is given a `resolve` function and asks
whether the answer moves. An earlier draft put `is_user_layer`/`user_layer_names` on `Guards`;
nothing read them, and they were removed rather than wired.

**C6 — a refusal leaves the document unchanged.** Mutations validate before mutating, and
`_finish` reloads the document when the resulting text fails a check, so a refusal cannot leave a
half-edited file. A test pins the multi-field case
(`test_a_refused_multi_field_write_leaves_the_document_alone`).

**C7 — a key name must be addressable by the reader forge uses.**
`pyyaml.safe_load(f"{name}: 1")` must yield a dict whose single key is a `str` equal to `name`.
Measured: ruamel keeps `yes`/`on` as strings while PyYAML reads them as booleans, so a
ruamel-only check would write a backend forge can never address.

**C8 — refusals that survive.** A file that does not parse, nests past the recursion limit, has no
`backends` mapping, or whose root is not a mapping is refused and counted. A file whose entry
cannot be addressed in the text (a one-line mapping, a block alias) is refused as
`NotLocatable` — distinct from `NotDeclared`, because telling the operator "not declared" for an
entry that is declared is a lie and makes a sweep report success while skipping the file.

## Probes and their results (ruamel 0.19.1, python 3.13)

Run in a throwaway venv, script `ruamel_probe.py`, output verbatim:

```
1.crlf_preserved_by_default: false
1.crlf_with_line_break_setting: true
2.untouched_single_quote_kept: true
2.touched_value_written_quoted: true
2.without_preserve_quotes_quotes_dropped: true
3.alpha_comment_gone: true
3.beta_comment_kept: true
4.beta_untouched_by_alpha_edit: true
4.anchor_kept: true
4.alpha_resolves_merged_key: "shared-model"
5.four_space_input_reindented_to_two: true
5.first_write_differs: true
5.second_write_is_byte_identical: true
```

What each answer changed:

1. **Line endings.** Not preserved by default, preserved by configuring `line_break` from the
   input. A file that mixes CRLF and LF is refused instead of converted wholesale.
2. **Quote style.** `preserve_quotes=True` keeps untouched quotes and keeps a rewritten value
   quoted; without it quotes are dropped everywhere, so the flag is not optional.
3. **Deletion.** A comment attached to a deleted node is gone; one inside a survivor stays. Hence
   the refusal table above rather than an announcement.
4. **Shared anchors.** Editing one entry leaves the other, the anchor and the `<<:` merge intact.
   Deleting the anchor's host is still refused: the alias becomes an inline block.
5. **Indentation.** ruamel re-indents to the configured mapping indent, so the loader reads the
   file's own indent. With that, `4/8/4` round-trips byte-identically; the probe's
   "reindented to two" is what happens when the indent is *not* read from the file.

## Evidence

- Tests: `tests/test_config_edit.py` (81) and `tests/test_forge_provider.py` (262). The wider
  affected surface runs as fourteen modules (924 tests).
- Guard withdrawals: fourteen rows (twelve in the module, two in the script), each turning its
  named test red and green again; the drivers are `withdraw_repo.py` and `withdraw_script.py` and
  their output is `config-edit-build/withdrawals-{repo,script}.json`.
- Guards added during review were each verified by injection (mutate the guard, watch its test go
  red, restore): `NotLocatable` instead of "not declared", the flow-block removal path, the
  non-mapping root refusal, the `set_fields` alias refusal, the semantic-equality check, the
  column-width setting, the `headers:`-must-be-a-mapping refusal, int-field equality, `set`
  writing a missing field, the header-removal report, `sync`'s single write, and the read-back
  comparison of C2.7. The record is
  `.planning/quick/20261121-provider-cleanup/review-r21-r3/injections.md`.
- Review: nineteen rounds on frozen candidates (r2-r20, fifteen of them with all seven lenses); every round's reports and dispositions live
  under `.planning/quick/20261121-provider-cleanup/review-r21*/`, and `review-r21-r3/injections.md`
  records each guard that was verified by removing it and watching a test fail.

## Decisions taken

1. Option A, not B or C.
2. `ruamel.yaml==0.19.1` as an optional extra (`provider`, also in `dev`), not a core
   requirement: `scripts/forge-provider.py` is a repository tool and is not shipped in the wheel.
   The script says which extra to install if the import fails.
