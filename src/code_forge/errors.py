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


class CorruptedReceiptError(Exception):
    """Raised when a review receipt file is corrupt or unparseable.

    Carries the offending filename. Receipts are the attestation that
    review passes ran, so an unreadable one is reported rather than
    skipped: skipping would silently turn tampering into a lower
    receipt count.
    """


class UnreadableGateError(Exception):
    """Raised when gate.yaml exists but cannot be read or parsed.

    A missing gate.yaml is not this: a repo that never configured one has
    no policy to lose, and falls back to the default. But a file that is
    present and unreadable is a policy we cannot see, and defaulting there
    would silently relax a repo that asked for more.
    """


class CliError(Exception):
    """Raised on invalid CLI args or env values.

    main() catches and maps to EXIT_CLI_ERROR (exit 2).
    """

    def __init__(self, message: str, *, remediation: str | None = None):
        super().__init__(message)
        self.remediation = remediation


class ComponentsConfigError(Exception):
    """Raised when .code-forge/components.yaml fails schema validation."""


class CoverageConfigError(Exception):
    """Raised when .code-forge/coverage.yaml fails schema validation."""
