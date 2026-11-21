import os
from unittest.mock import patch

from code_forge.lenses import LENS_BLOCK, PASS_LENSES, selection_line
from tests import test_lens_prompts as fixtures

DELETIONS = {"qodo": 1020, "expert": 695, "adversarial": 747}


def test_ordinary_transport_arm_changes_only_known_lens_block():
    fake_post_image = fixtures.POST_IMAGE + LENS_BLOCK
    with patch.object(fixtures, "POST_IMAGE", fake_post_image):
        with patch.dict(os.environ, {"FORGE_EXPERIMENT_537_ARM": "A"}):
            a = fixtures._a_prompts(review_lenses=True)
        with patch.dict(os.environ, {"FORGE_EXPERIMENT_537_ARM": "B"}):
            b = fixtures._a_prompts(review_lenses=True)
    prefix, cards = LENS_BLOCK.split("L1 ", 1)
    rows = ("L1 " + cards).splitlines(keepends=True)
    for name, baseline, variant in zip(PASS_LENSES, a, b, strict=True):
        block = prefix + "".join(row for row in rows if row[:2] in PASS_LENSES[name])
        assert baseline.count(LENS_BLOCK) == 2  # actual injected block and evidence lookalike
        assert variant.count(LENS_BLOCK) == 1  # evidence lookalike remains intact
        assert variant == baseline.replace(LENS_BLOCK, block, 1)
        assert variant.endswith(selection_line(name))
        assert len(baseline.encode()) - len(variant.encode()) == DELETIONS[name]


def test_arm_b_without_trusted_lenses_stops_without_invocation():
    import pytest

    with patch.dict(os.environ, {"FORGE_EXPERIMENT_537_ARM": "B"}):
        with pytest.raises(ValueError, match="trusted lenses"):
            fixtures._a_prompts(review_lenses=False)
