"""Server-authorized, read-only access to explicit private diagnostic tickets.

Configuration is captured once at startup, never supplied by an MCP client.
Unsigned tickets remain caller-owned state, not same-UID authentication.
"""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
from collections.abc import Mapping
from . import _advisory_diagnostics as storage
from . import _diagnostic_history as history

_TICKET = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.diagnostic-ticket\.json", re.ASCII
)


@dataclass(frozen=True, slots=True)
class HistoryAccess:
    tickets: storage.StorageBinding
    artifacts: storage.StorageBinding


def _binding(path: Path, descriptors: storage._Descriptors) -> storage.StorageBinding:
    fd = history._directory(path, descriptors)
    return storage.StorageBinding(path, storage._identity(os.fstat(fd)))


def configure(environ: Mapping[str, str]) -> HistoryAccess | None:
    """Both existing canonical 0700 roots must be explicitly selected or neither."""
    tickets = environ.get("FORGE_DIAGNOSTIC_TICKET_DIR")
    artifacts = environ.get("FORGE_DIAGNOSTIC_STORAGE_ROOT")
    if tickets is None and artifacts is None:
        return None
    try:
        if not tickets or not artifacts:
            raise history.TicketError("invalid_history_configuration")
        with storage._Descriptors() as descriptors:
            return HistoryAccess(
                _binding(Path(tickets), descriptors), _binding(Path(artifacts), descriptors)
            )
    except Exception:  # noqa: BLE001 -- Do not echo private startup configuration.
        raise history.TicketError("invalid_history_configuration") from None


def read(access: HistoryAccess | None, ticket: str) -> dict:
    """Return only the public historical projection; no paths or execution context."""
    try:
        if access is None or type(ticket) is not str or _TICKET.fullmatch(ticket) is None:
            raise history.TicketError("historical_diagnostic_unavailable")
        with storage._Descriptors() as descriptors:
            parent = history._directory(access.tickets.source_root, descriptors)
            if storage._identity(os.fstat(parent)) != access.tickets.identity:
                raise history.TicketError("historical_diagnostic_unavailable")
            if _binding(access.artifacts.source_root, descriptors) != access.artifacts:
                raise history.TicketError("historical_diagnostic_unavailable")
            raw, _ = history._read_leaf(parent, ticket, descriptors)
            publication, expected = history._decode(raw)
            if publication.locator.binding != access.artifacts:
                raise history.TicketError("historical_diagnostic_unavailable")
            snapshot = storage.read_observation(publication, expected)
            # Replacing either configured directory never grants new authority.
            if (
                _binding(access.tickets.source_root, descriptors) != access.tickets
                or _binding(access.artifacts.source_root, descriptors) != access.artifacts
            ):
                raise history.TicketError("historical_diagnostic_unavailable")
        return {
            "kind": "historical-advisory-diagnostic",
            "current_run_credit": False,
            "analysis_state": snapshot.analysis_state,
            "diagnostics": [asdict(item) for item in snapshot.diagnostics],
        }
    except Exception:  # noqa: BLE001 -- Never disclose raw errors, roots or caller-held context.
        raise history.TicketError("historical_diagnostic_unavailable") from None
