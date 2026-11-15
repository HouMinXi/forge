# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""One reading of an opt-in environment switch.

Several switches exist so a caller can turn a behaviour on without a config
change. They all mean the same thing by "on", so they read it the same way: only
the four tokens below count, everything else (including unset and empty) leaves
the default in place. A second, slightly different reading in another module is
how a switch starts behaving differently depending on which code path asks.
"""

from __future__ import annotations

_TRUTHY = frozenset(("1", "true", "yes", "on"))


def truthy(value) -> bool:
    """Whether an environment value means "on"."""
    if value is None:
        return False
    try:
        return str(value).strip().lower() in _TRUTHY
    except Exception:  # noqa: BLE001 - a value that cannot be read is not an opt-in
        return False


def requested(environ, name: str) -> bool:
    """Whether ``name`` asks for the behaviour.

    A mapping that cannot answer for that key stays off; a mapping that raises
    anything else surfaces it, exactly as it would for any other read.
    """
    try:
        value = environ.get(name)
    except (AttributeError, TypeError, LookupError):
        return False
    return truthy(value)
