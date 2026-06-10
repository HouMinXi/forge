"""OutletC: subagent dispatch outlet."""
from __future__ import annotations

from typing import Callable


class OutletC:
    """Dispatch review passes to subagent LLM calls."""

    def __init__(self, spawn_fn: Callable[[str, str], str]) -> None:
        self._spawn_fn = spawn_fn

    def review(self, diff_text: str) -> list[str]:
        """Run all three review passes."""
        passes = ["qodo", "expert", "adversarial"]
        return [self.spawn_review(p, diff_text) for p in passes]

    def spawn_review(self, pass_name: str, diff_text: str) -> str:
        """Spawn a subagent review pass.

        Dispatches to llm_invoke per pass with fresh context.
        """
        return self._spawn_fn(pass_name, diff_text)
