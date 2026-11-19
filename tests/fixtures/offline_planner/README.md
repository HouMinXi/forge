# Offline planner v1 frozen vectors

The base64 fixture is the exact XZ archive from the 04:40:47 UTC revision
of [the frozen #537 specification](https://github.com/HouMinXi/forge/issues/537#issuecomment-6999075145).
Its decoded SHA-256 is
`cdc2ab74a6aeb27bf4e220d004c2c00eeed6b1708c0363f9e26b546649d3407d`.

The replay verifies the frozen archive and each file before checking the
planner against the author-supplied expected bytes. Never regenerate these
expected bytes from the implementation. To run standalone from the repo root:

```sh
PYTHONPATH=src python -m tests.offline_planner_replay
pytest tests/test_offline_planner.py tests/test_offline_planner_lexical.py
```

The first command uses the C and C.UTF-8 locales and different hash seeds.
Both commands run offline and do not call a review backend.
