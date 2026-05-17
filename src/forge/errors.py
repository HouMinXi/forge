# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Forge-specific error types for state management."""


class SchemaVersionMismatchError(Exception):
    """Raised when state.json schema_version does not match expected."""


class CorruptedStateError(Exception):
    """Raised when state.json is corrupt or internally inconsistent."""
