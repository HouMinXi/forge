# SPDX-License-Identifier: Apache-2.0
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


class CliError(Exception):
    """Raised on invalid CLI args or env values.

    main() catches and maps to EXIT_CLI_ERROR (exit 2).
    """


class ComponentsConfigError(Exception):
    """Raised when .code-forge/components.yaml fails schema validation."""


class CoverageConfigError(Exception):
    """Raised when .code-forge/coverage.yaml fails schema validation."""
