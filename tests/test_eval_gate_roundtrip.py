"""The harness gate.yaml must be readable by the thing that reads it.

Found while running the first real evaluation (Phase 57-6).
_create_gate_yaml wrote with yaml.dump and read back with yaml.safe_load.
backend_config arrives from dataclasses.asdict, so tuple-valued fields such
as env_set were written with a !!python/tuple tag that safe_load refuses
outright -- and the refusal surfaced as a raw ConstructorError traceback
partway through an evaluation, not as anything an operator could act on.
"""

import yaml

from code_forge.eval.runner import _create_gate_yaml


class TestGateYamlRoundTrip:
    def test_a_tuple_valued_field_survives_safe_load(self, tmp_path):
        path = _create_gate_yaml(
            tmp_path,
            "b",
            backend_config={
                "type": "api",
                "model": "m",
                "headers": {"A": "B"},
            },
        )
        # safe_load, because that is what every reader of this file uses,
        # including _create_gate_yaml itself on the merge path.
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["backends"]["b"]["headers"] == {"A": "B"}

    def test_a_nested_tuple_survives_too(self, tmp_path):
        path = _create_gate_yaml(
            tmp_path,
            "b",
            backend_config={"headers": {"pairs": [("k", "v")]}},
        )
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["backends"]["b"]["headers"]["pairs"] == [["k", "v"]]

    def test_the_merge_path_can_reread_what_it_wrote(self, tmp_path):
        _create_gate_yaml(tmp_path, "first", backend_config={"model": ("A",)})
        # The second call re-reads the file it just wrote. Before the fix
        # this raised instead of merging.
        path = _create_gate_yaml(tmp_path, "second", backend_config={"model": "m"})
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert set(loaded["backends"]) == {"first", "second"}
        assert loaded["backends"]["first"]["model"] == ["A"]


class TestTheRealLoaderAcceptsIt:
    """safe_load parsing it is not the bar; forge's own loader is.

    Three defects reached the first real evaluation because the harness
    gate.yaml was only ever checked for being valid YAML. It was valid
    YAML the loader refused: a !!python/tuple tag, then internal field
    names (env_set / env_unset) that backend.py rejects on sight, then
    type-inappropriate fields that dataclasses.asdict emits by default.
    Each ended the same way -- no backend, no review, and an entry scored
    as though the reviewer had found nothing.

    The fix upstream of this is to stop rebuilding the entry at all and
    pass the ORIGINAL yaml through; these tests hold that contract.
    """

    def test_a_real_config_entry_survives_the_harness(self, tmp_path):
        from code_forge.backend import load_backend_configs

        entry = {
            "type": "api",
            "format": "anthropic",
            "base_url": "https://example.invalid/anthropic",
            "model": "m",
            "api_key_env": "EXAMPLE_KEY",
            "timeout_s": 2400,
            "headers": {"User-Agent": "x"},
        }
        path = _create_gate_yaml(tmp_path, "harness", entry)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))

        loaded = {c.name: c for c in load_backend_configs(data)}
        assert "harness" in loaded
        assert loaded["harness"].model == "m"
        assert loaded["harness"].timeout_s == 2400
        assert dict(loaded["harness"].headers) == {"User-Agent": "x"}

    def test_a_cli_entry_survives_too(self, tmp_path):
        from code_forge.backend import load_backend_configs

        entry = {
            "type": "cli",
            "model": "m",
            "command": ["echo", "hi"],
            "env": {"unset": ["DROP_ME"], "set": {"KEEP": "1"}},
        }
        path = _create_gate_yaml(tmp_path, "harness", entry)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))

        loaded = {c.name: c for c in load_backend_configs(data)}
        assert loaded["harness"].env_unset == ("DROP_ME",)
        assert loaded["harness"].env_set == (("KEEP", "1"),)
