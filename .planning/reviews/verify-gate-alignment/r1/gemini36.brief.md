SEVERITY: BLOCKER
FILE:LINE: src/code_forge/verify.py:396
CLAIM: `_compare_at` ignores excerpt lines that fall outside `file_lines`, allowing fabricated lines trailing a valid diff hunk line to pass STEP C.
FAILURE: For a file with diff post-image lines 1..4, an excerpt with `start_line=4`, `end_line=6`, and `content="<real line 4>\nFABRICATED 1\nFABRICATED 2"` has line 4 matched (`overlap=1`), while lines 5 and 6 trigger `ln not in file_lines` and are skipped via `continue`. `_compare_at` returns `(True, None)` because `overlap > 0`, accepting the fabricated excerpt.
EVIDENCE:
```python
            for i, ln in enumerate(range(first_line, last_line + 1)):
                if i >= len(actual_lines) or ln not in file_lines:
                    continue
                overlap += 1
                if actual_lines[i].rstrip() != file_lines[ln].rstrip():
                    return False, ln
            return (True, None) if overlap else (None, None)
```

SEVERITY: BLOCKER
FILE:LINE: src/code_forge/verify.py:423
CLAIM: When shift 0 has no overlap but a non-zero shift overlaps and mismatches, `bad_line` is extracted only from `attempts[0]`, ignoring the mismatch and passing the check.
FAILURE: If an excerpt at shift 0 does not overlap `file_lines` (`attempts[0] = (None, None)`), but at shift 1 overlaps and mismatches (`attempts[1] = (False, 10)`), `any(ok for ok, _ in attempts)` is `False`. `bad_line` receives `attempts[0][1]` (`None`), triggering `if bad_line is None: continue` and bypassing failure reporting for the mismatch.
EVIDENCE:
```python
            bad_line = attempts[0][1]
            if bad_line is None:
                # Nothing overlaps the post-image here, so there is nothing
                # this check can verify. Coverage is checks 3 and 6.
                continue
```

SCORECARD: blocker=2 major=0 minor=0 nit=0
