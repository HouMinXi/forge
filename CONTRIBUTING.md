# Contributing

Issues and discussion: <https://github.com/HouMinXi/forge/issues>.

## Linting

The repository is Ruff-clean. CI runs both `ruff check` and
`ruff format --check` over the entire tree using `pyproject.toml` settings.
The historical `Lint changed files` workflow and check identity are retained
for compatibility, but its scope is no longer limited to changed files.
Python code fences in Markdown are also formatted by the pinned formatter.
Reconstructed corpus base files remain intentionally excluded: they are
invalid-Python diff inputs, not executable source.

Ruff is pinned in the `dev` extra so upgrades are explicit and reviewable:

```bash
pip install -e '.[dev]'
python scripts/lint_changed.py --all
```

For a fast local check of added, modified or renamed Python files only:

```bash
python scripts/lint_changed.py --base origin/main
```

That convenience command does not replace the full-tree CI gate. Deleted
and non-Python files are omitted from the diff check.

Prefer fixing findings to suppressing them. A line-local exemption must
explain an intentional contract (for example, quarantining malformed report
objects, preserving a public validation exception, or a deliberately unsafe
Semgrep test fixture). Do not broaden exclusions or disable rules to hide
new debt. This cleanup follows the baseline debt retained by #105 / #323.
