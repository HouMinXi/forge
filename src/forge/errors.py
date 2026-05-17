# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Forge-specific error types for state management."""


class SchemaVersionMismatchError(Exception):
    """Raised when state.json schema_version does not match expected."""


class CorruptedStateError(Exception):
    """Raised when state.json is corrupt or internally inconsistent."""


class BaselineResolutionError(Exception):
    """Raised when baseline resolution fails (invalid ref, bad combination)."""


class SnapshotSchemaMismatchError(Exception):
    """Raised when snapshot schema_version does not match expected."""


class CorruptedSnapshotError(Exception):
    """Raised when snapshot file is corrupt or unparseable."""
