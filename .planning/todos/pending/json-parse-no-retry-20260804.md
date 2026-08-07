# A malformed-JSON response is one-shot fatal, so some diffs can never be reviewed (2026-08-04)

Found while trying to take a bash/python hook through the 4-consecutive-clean
gate in `mhou_workspace`. `code-forge 2.7.0`. Separate from
`review-pipeline-defects-20260804.md`, which was found against OmniRoute
worktrees; this one has a single named mechanism and a ten-line fix.

This is NOT the fail-open shape. Forge behaves correctly at the verdict layer:
it records `infra=1`, reports `passes=2/3`, and voids the cycle. The defect is
that it cannot recover from a transient sampling error, and for some inputs
that error is not transient enough to outrun.

---

## Mechanism

`src/code_forge/llm_invoke.py`. The retry loop ends at

    971:        break  # success

and the JSON parse happens after it:

    976:    content = _strip_fences(content)
    977:    try:
    978:        parsed_content = json.loads(content)
    979:    except json.JSONDecodeError as exc:
    983:        parsed_content = _extract_json_from_text(content, ...)
    984:        if parsed_content is None:
    988:            raise LLMInvokeError(...)

The retry machinery above (955-971) covers HTTP-level failures via
`LLMInvokeError.retryable`. A response that arrives HTTP-200 but carries
unparseable JSON never re-enters that loop -- it raises straight out. The
`_extract_json_from_text` fallback only helps when the JSON is intact but
wrapped in prose or fences; it does not repair a broken escape.

So a bad sample kills the pass, and the pass kills the cycle.

## Why it is not merely wasteful

A model writing a finding has to embed source code inside a JSON string, which
means doubling every backslash. When the code under review is backslash-dense,
that goes wrong often. Measured on a 1403-line diff of a regex-heavy bash hook:

| cycle | backend | outcome |
|---|---|---|
| 4 | sn-deepseek-flash | 3/3 complete |
| 5 | sn-deepseek-flash | void, 524 |
| 6 | onmi-gemini3.6 | void, `Invalid \escape` |
| 7 | agy/gemini-3.1-pro-high | void, 2400s timeout x2 |
| 7b | deepseek-v4-flash direct | void, "unexpected response structure" |
| 8 | deepseek-v4-flash direct | void, `Invalid \escape` |
| 9 | deepseek-v4-flash direct | void, `Expecting ',' delimiter` |

One complete cycle in seven attempts. Three different vendors produced the same
JSON-escape failure, which is what rules out "flaky backend" -- every configured
backend is `type: api` + `format: openai`, so all of them share this path.

The content forge printed when it died makes the mechanism unambiguous:

    x=$(false)\\"$(ls | head -1)\\" && echo y

The model meant a shell `\"`. It emitted JSON `\\"`, which reads as an escaped
backslash followed by a quote that terminates the string. Failure offsets were
char 818 and char 2305 -- early in the response, so this is not truncation
against `max_tokens`.

Consequence: for a file of this shape the 4-consecutive-clean gate is not merely
expensive, it is unreachable. Per-pass success runs about 83%, but the observed
all-three rate is 1/7 rather than the 0.57 independence would predict, because
the passes fail on the same hard content rather than independently.

## Suggested shapes, in preference order

1. Move the parse inside the retry loop. A `JSONDecodeError` becomes a
   retryable `LLMInvokeError`, so the next attempt draws a fresh sample.
   Cheapest, and it matches what the failure actually is: nondeterministic
   output, not a broken request. Cap it lower than the HTTP retry budget --
   two attempts would have rescued every void cycle in the table above.
2. If a retry is unacceptable on cost, attempt a repair before giving up:
   an invalid escape inside a string is recoverable by re-escaping lone
   backslashes. Fragile, and it silently edits model output, so it ranks
   below a retry.
3. At minimum, say so in the verdict. `passes=2/3` with `infra=1` does not
   tell a caller that a retry would probably have worked, and the cost table
   shows the surviving passes each burned 40-70K output tokens for a result
   that was then discarded.

Worth adding a `--only <pass>` or resume-single-pass flag independently: today
one dead pass forces all three to re-run, so the retry cost is 3x what the
failure warrants.

## Risk note, recorded because it shaped the decision to file rather than fix

Forge is shared by every project that reviews through it, so a regression in
`llm_invoke.py` is not a local failure -- it is every project's review going
quietly wrong at once. That is a strictly larger blast radius than the bug
being fixed, which currently fails loudly and voids the cycle.

There is also a circularity: a change to forge should go through forge's own
review pipeline, and that pipeline is broken in exactly this way on exactly
this class of file. Whoever picks this up should decide deliberately how to
break that loop -- a smaller test diff that is not backslash-dense would do it,
and the fix is small enough to be reviewed on a diff that does not trip the bug
it fixes.

Recorded rather than fixed inline on 2026-08-04, so the workspace task that
found it could route around with an external retry wrapper instead of carrying
a second review obligation in a shared tool.
