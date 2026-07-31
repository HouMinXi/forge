end_line"] + shift)
    for shift in (0, 1, -1, 2, -2, 3, -3)
]
# attempts is [(None, None), (None, None), (None, None), (None, None), (None, None), (None, None), (None, None)]
if any(ok for ok, _ in attempts): # ok is None for all -> False
    continue
bad_line = attempts[0][1] # attempts[0][1] is None!
if bad_line is None:
    # Nothing overlaps the post-image here, so there is nothing
    # this check can verify. Coverage is checks 3 and 6.
    continue
```
`bad_line` is `None`!
So `if bad_line is None: continue` IS EXECUTED!
STEP C PASSES FOR A FILE NOT IN POST_IMAGE!

Wait! Let's check check 3 or STEP A or test `test_stray_file_not_in_diff_rejected` in `test_verify.py`!
Look at `test_stray_file_not_in_diff_rejected` in `test_verify.py` (lines 897-927 of test_verify.py in the diff)!
Wait! Let's look at `test_stray_file_not_in_diff_rejected` in `diff.patch`:
```python
    def test_stray_file_not_in_diff_rejected(self, tmp_path):
        """Excerpt referencing a file absent from diff -> FAIL (not in diff)."""
        ...
        for c in range(1, 4):
            for p in range(1, 4):
                receipt = _receipt(c, p, sha)
                receipt["code_excerpts"][0]["content"] = "def f():\n return 2"
                # Add excerpt for a file that does not appear in the diff at all
                receipt["code_excerpts"].append({
                    "file": "src/other.py", "start_line": 1, "end_line": 2,
                    "content": "x = 1\n",
                    "rationale": "stray"
                })
                name = "receipt-c%dp%d.json" % (c, p)
                (rd / name).write_text(json.dumps(receipt))
        r = run_verify(tmp_path, sha, diff_files, diff_text=diff_content)
        assert not r.passed, "excerpt for file not in diff should fail"
        assert "not in diff" in r.reason
```

WAIT A MINUTE!
Let's check where `"not in diff"` comes from in `run_verify`!
Is check 3 (anchor files appear in diff) checking all excerpt
