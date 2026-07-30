I now have everything needed for a grounded report. The diff is unapplied; I verified the proposed logic by executing it directly (including the real `verify.py` STEP A/B/C predicates and the actual `_HARDEN_DIFF` fixture).

---

## Review verdict

The fix is **correct and well-targeted**. The credited-line set now equals exactly the set of lines STEP C already verified against the post-image — credit ⟺ verified, which is the right invariant. I found no defect in the arithmetic, the test, or the consumer sweep. There is one genuine behavioral change in check 7 that I can construct in the abstract; it does **not** admit real rubber stamps, and your measurement (0 crossings in 111 pairs) confirms it never arises in practice. Details below.

---

### 1. Arithmetic — `min(end, start + shown - 1) + 1` — SOUND, no off-by-one

Tested the proposed function against every edge case by execution:

| case | shown | lines credited | correct? |
|------|-------|----------------|----------|
| empty content `""` | 0 | none | ✓ (earns nothing) |
| `content=None` (not str) | 0 | none | ✓ |
| single line, no newline | 1 | `{start}` | ✓ |
| trailing newline `"x = 1\n"` | 1 | `{start}` | ✓ |
| CRLF `"x\r\ny"` | 2 | `{start, start+1}` | ✓ |
| exact span (3 lines) | 3 | `{start..start+2}` | ✓ |
| content longer than span | 5 | capped at `{start..end}` | ✓ |
| `start > end` | any | none (range empty) | ✓ |
| blank line in content | counts as 1 | credited | ✓ |

Why it's right: `range(start, start+shown-1+1)` yields exactly `shown` lines starting at `start`; the `min(end, …)` caps it at the declared span. When `shown==0`, `start+shown-1 = start-1 < start`, so the range is empty — empty content earns zero, which is the whole point. The blank-line case is correct because STEP C (`verify.py:246`) maps content line *i* to post-image line `start+i` and verifies it, so a blank content line is a real verified line that deserves credit.

### 2. Does this WEAKEN check 7? — Only for offset-overlap pairs, NOT real rubber stamps

I constructed the crossing pair you asked for. On a 12-line diff (`all_diff` = 1..12), two cycles each passing check 6 under both regimes:

- **A**: declared `1-9`, 8 content lines (covers post-image 1-8)
- **B**: declared `2-9`, 8 content lines (covers post-image 2-9)

```
OLD: covA=9 covB=8  check6 75%/67%  J=0.889  -> CAUGHT
NEW: covA=8 covB=8  check6 67%/67%  J=0.778  -> passes
```

So yes — an **offset-overlap** pair the old check 7 flagged now passes. But this is **not a rubber stamp**: the two cycles cite genuinely different line ranges with genuinely different content. Declared-span overlap is poor evidence of copying (reviewers naturally cluster on the same hot spots), so lower Jaccard there is arguably *more* correct, not a regression.

The actual rubber-stamp threat — cycle B copying cycle A's excerpts **verbatim** (identical file/start/end/content) — produces **identical** credited sets under the new rule, so **J = 1.0 under both old and new** → still caught. Your `test_repeated_excerpts_still_read_as_rubber_stamp` pins exactly this with `findings=[{...}]` so check 7 isn't skipped. A fabricator trying to dodge by declaring the same wide range with thin content for both cycles still gets identical thin sets → J=1.0 → caught. And your on-disk measurement (0 of 111 cycle-pairs cross 0.8 in either direction) confirms the abstract crossing pair never occurs in real receipts.

### 3. `splitlines()` — behaves as assumed

`\r\n` → splits cleanly (counts as *n* real lines, not *n*+1); trailing `\n` is not counted as an extra line; a single line with no newline counts as 1. Crucially, STEP C at `verify.py:246` uses the **same** `content.splitlines()` and maps line *i* → post-image line `start+i`. So the set `_excerpt_covered` now credits is *exactly* the set STEP C verified — credit and verification can no longer diverge. This consistency is the strongest argument for the fix.

### 4. Does the new test assert what it claims? — YES, and not for the wrong reason

I ran the test's `inflated` excerpts through the real STEP A/B/C predicates:

- STEP A (witness): all 3 hunks witnessed ✓
- STEP B (anchor + nonempty): all anchored ✓
- STEP C (content vs post-image): overlap lines `{1},{6},{1}`, zero mismatches ✓

So the test genuinely **reaches check 6** (not stopped early at check 5). There:

```
OLD code: 9/9 = 100% -> check6 PASSES
NEW code: 3/9 = 33%  -> check6 FAILS "< 60%"
```

The assertion `"< 60%" in r.reason` pins the failure specifically to check 6. And with the fix **reverted**, the test fails (100% passes check 6, then check 7 is skipped because `findings=[]`) — so it is a valid regression test that only turns green because of the fix. It cannot pass for the wrong reason: a check-5 failure would produce a different reason string.

### 5. Third consumer of `_excerpt_covered` / `_cycle_excerpt_covered`? — NONE

`grep` over `src/` and `tests/` confirms the only call sites:
- `_excerpt_covered` → called only by `_cycle_excerpt_covered` (`verify.py:96`)
- `_cycle_excerpt_covered` → check 6 (`verify.py:273`) and check 7 (`verify.py:295-296`)

The legacy branch uses the **separate** `_covered`/`_cycle_covered` (`verify.py:64-77`, the `covered_line_ranges` path) — not a consumer of `_excerpt_covered`. Your count of two consumers (checks 6 and 7) is complete.

### 6. Can a fabricator still inflate coverage some other way? — No new vector

After the fix, every credited line passed STEP C (content matches post-image at that exact position). The ways to game it are closed:
- **Wide declared range + thin content**: closed — credit is capped at shown lines.
- **Padding content with blank/trailing-newline lines**: doesn't work — `splitlines()` ignores trailing newlines, and blank lines are counted only where STEP C actually verified a matching blank post-image line.
- **Many short excerpts**: each must independently pass STEP A/B/C (witness + anchor + content match), so each credited line is a verified post-image line. Inflation = genuinely citing more lines, i.e. legitimate reading.

The **pre-existing** gap that remains — and which this fix does not claim to close, and which it does not worsen — is the one documented at `verify.py:241-244`: STEP C only requires cited lines to match the post-image, not to be *changed* lines. A reviewer can still clear the 60% floor by citing 60% context (unchanged) lines, which match trivially. That limitation is orthogonal to this change.

---

### Bottom line

No defect. The arithmetic is correct, the test is sound (and fails without the fix), there are no hidden consumers, and the fix tightens the credit-set to exactly the verified set. The one check-7 behavioral change is bounded to offset-overlap pairs that aren't rubber stamps, never shows up in your 111 measured pairs, and verbatim copies (the real threat) still score J=1.0. Ship it.
