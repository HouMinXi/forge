"""Tests for OutletC subagent dispatch."""
from __future__ import annotations

from unittest.mock import MagicMock


from code_forge.outlet_c import OutletC


class TestOutletC:
    """Tests for OutletC subagent dispatch."""

    def test_spawn_review_dispatches(self):
        """Verify spawn_review calls the spawn function."""
        mock_spawn = MagicMock(return_value='{"findings": []}')
        outlet = OutletC(spawn_fn=mock_spawn)
        result = outlet.spawn_review("qodo", "diff --git a/f.py")
        mock_spawn.assert_called_once_with("qodo", "diff --git a/f.py")
        assert result == '{"findings": []}'

    def test_spawn_review_fresh_context(self):
        """Verify each pass gets independent context."""
        mock_spawn = MagicMock(side_effect=["r1", "r2", "r3"])
        outlet = OutletC(spawn_fn=mock_spawn)
        results = [outlet.spawn_review(p, "diff") for p in ["qodo", "expert", "adversarial"]]
        assert results == ["r1", "r2", "r3"]
        assert mock_spawn.call_count == 3
