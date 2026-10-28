# Contributing

Issues and discussion: <https://github.com/HouMinXi/forge/issues>.

## Linting

The repository is **not** ruff-clean. At the time of writing, `ruff check .`
reports about 200 findings and `ruff format --check .` would reformat about
180 files (see #105). Lint is therefore diff-scoped: the `Lint changed files`
workflow runs `ruff check` and `ruff format --check` only on the Python files
a pull request adds, modifies or renames, using the `[tool.ruff]` settings in
`pyproject.toml`. Files the pull request deletes, and files that are not
Python, are not linted.

The ruff version is pinned in the `dev` extra (`ruff==...`) so results do not
change when ruff releases. To run the same check locally:

```bash
pip install -e '.[dev]'
python scripts/lint_changed.py --base origin/main
```

A pull request that touches a file with old findings has to leave that file
clean. Fixing the existing debt in files you do not otherwise touch is a
separate change.
