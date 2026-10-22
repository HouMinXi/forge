# SPDX-License-Identifier: Apache-2.0
"""#276: write_receipts pairs passes with skills strictly and rejects a
non-dict excerpt as a type error."""

import pytest

from code_forge import receipt


def test_baseline_writes_one_receipt_per_pass(tmp_path):
    paths = receipt.write_receipts(tmp_path / "receipts", 0, [], "x" * 64, [], tmp_path)
    assert [p.name for p in paths] == ["receipt-c1p1.json", "receipt-c1p2.json", "receipt-c1p3.json"]


def test_pass_skill_length_mismatch_raises_before_any_write(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, "_SKILL_NAMES", receipt._SKILL_NAMES[:2])
    rd = tmp_path / "receipts"
    with pytest.raises(ValueError, match="zip"):
        receipt.write_receipts(rd, 0, [], "x" * 64, [], tmp_path)
    assert not list(rd.glob("receipt-*.json"))  # no partial cycle on disk


def test_non_dict_excerpt_is_a_type_error(tmp_path):
    with pytest.raises(TypeError, match="reviewer_excerpt item is not a dict"):
        receipt.write_receipts(
            tmp_path / "receipts", 0, [], "x" * 64, [], tmp_path, reviewer_excerpts=["nope"]
        )
