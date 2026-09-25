"""Reject malformed contract fields at the input boundary."""

import pytest
import yaml

from code_forge.contract_loader import load_contract_digest, load_contracts_config
from code_forge.errors import CliError


@pytest.mark.parametrize("field,value", [
    ("path", 42),
    ("path", ["spec.md"]),
    ("path", {"name": "spec.md"}),
    ("path", True),
    ("max_raw_size", "nope"),
    ("max_raw_size", None),
    ("max_raw_size", []),
    ("max_raw_size", {}),
    ("max_raw_size", float("inf")),
    ("max_raw_size", float("nan")),
])
def test_bad_field_is_a_config_error(tmp_path, field, value):
    spec = {"path": "spec.md", "max_raw_size": 32768, field: value}
    config = tmp_path / "contracts.yaml"
    config.write_text(yaml.safe_dump({
        "repos": {"example": {"path": ".", "specs": [spec]}},
    }), encoding="utf-8")

    with pytest.raises(CliError) as caught:
        load_contracts_config(config)
    if field == "path":
        assert str(caught.value) == (
            "contracts.yaml spec in 'example' must have a 'path' string"
        )
        assert caught.value.__cause__ is None
    else:
        assert str(caught.value) == (
            "contracts.yaml spec 'spec.md' in 'example' has invalid max_raw_size"
        )
        try:
            int(value)
        except (TypeError, ValueError, OverflowError) as original:
            assert type(caught.value.__cause__) is type(original)
        else:
            pytest.fail("test input must fail integer conversion")

    # The public digest API must retain its bad-config fallback.
    assert load_contract_digest(config, tmp_path) == ""


@pytest.mark.parametrize("value,expected", [
    (32768, 32768), ("4096", 4096), (0, 0), (-1, -1),
    (True, 1), (12.5, 12),
])
def test_existing_size_conversions_are_preserved(tmp_path, value, expected):
    config = tmp_path / "contracts.yaml"
    config.write_text(yaml.safe_dump({
        "repos": {"example": {"path": ".", "specs": [
            "short.md", {"path": "long.md", "max_raw_size": value},
        ]}},
    }), encoding="utf-8")

    specs = load_contracts_config(config).repos["example"].specs
    assert specs[0].path == "short.md"
    assert specs[0].max_raw_size == 32768
    assert specs[1].path == "long.md"
    assert specs[1].max_raw_size == expected
