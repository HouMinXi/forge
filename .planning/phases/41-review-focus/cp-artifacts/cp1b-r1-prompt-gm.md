You are a runtime-precision reviewer for a Python project plan.

CONTEXT: Phase 41 adds review-focus to the forge code review pipeline. The plan contains pseudocode and code snippets for:
- _merge_focus_spec: concatenates yaml + file content, warns on >8192 bytes
- Trust functions: hash_focus_text (sha256 of canonical JSON), is_trusted_focus (short-circuit for empty)
- _dispatch_cli: focus_tmp lifecycle mirroring contract_tmp
- start_job: new focus_tempfile_path key in _jobs dict
- build_sampling_l1_provider: focus_spec param

YOUR ANGLE: If I were to compile and run this plan, what breaks? Check:
- Type correctness (str | None vs str, dict key types)
- Edge cases (empty string vs None vs missing key)
- Python semantics (tempfile lifecycle, dict iteration, exception handling)
- Off-by-one or missing cleanup paths

THE PLAN IS ATTACHED BELOW. Be specific — cite task numbers, pseudocode lines, and function signatures.

SEVERITY SCALE:
- B (Blocker): will crash, corrupt data, or produce wrong output at runtime
- H (High): logic error that causes incorrect behavior
- M (Medium): edge case that may cause problems
- L (Low): minor style or documentation issue

OUTPUT FORMAT (MANDATORY):
For each finding:
```
[SEVERITY] Task X-Y: finding title
  Location: specific reference in the plan
  Issue: what is wrong
  Impact: what happens if this is not fixed
  Suggestion: how to fix
```

At the end, output a summary line:
```
SUMMARY: B=<count> H=<count> M=<count> L=<count>
```

Do NOT output anything except findings and the summary line. Do not explain the plan back to me. Only report defects.

---

ATTACH THE PLAN CONTENT FROM /tmp/p41-cp1b-plan.md:
