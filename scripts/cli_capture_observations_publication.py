"""Independent closed projection for offline capture observations, never raw evidence."""

import json

SCHEMA = "forge-offline-capture-observations-v1"
ENUMS = {
    "mode": {"baseline", "diagnostic"},
    "receipt_status": {"DELIVERED", "UNAVAILABLE", "INVALID"},
    "cleanup": {"COMPLETE", "INCOMPLETE", "NOT_OBSERVED"},
    "fixture_outcome": {"BOUND_EXPECTED_UNRELIABLE", "UNASSESSED"},
    "event_reason": {"NONE", "NOT_OBSERVED", "UNSUPPORTED_EVENT_SHAPE", "MALFORMED_OR_INCOMPLETE"},
    "event_integrity": {"COMPLETE", "INCOMPLETE", "UNAVAILABLE"},
    "trace_integrity": {"COMPLETE", "INCOMPLETE", "UNAVAILABLE"},
    "execution_identity": {"UNASSESSED"},
}
CONSTANTS = {
    "schema": SCHEMA,
    "event_scope": "SUPPORTED_REQUEST_SUBSET",
    "trace_scope": "SUPPORTED_SYNTAX_AND_PER_TASK_FRAMING",
    "fixture_scope": "CALLER_TRUSTED_LOCAL_COLLECTOR_RECORDS",
    "cross_stream_order": "NOT_ASSESSED",
    "ack_consistency": "NOT_ASSESSED",
    "power_loss_durability": "NOT_ASSESSED",
    "qualification": "NOT_ASSESSED",
    "raw_evidence": "NOT_PUBLISHED",
}
COUNTS = {"event_count", "trace_count", "sync_count"}
CODES = {"shell_exit", "worker_exit", "cli_exit"}


def validate(row):
    if type(row) is not dict or set(row) != set(ENUMS) | set(CONSTANTS) | COUNTS | CODES:
        raise ValueError("closed observation fields required")
    if any(type(row[k]) is not str or row[k] not in values for k, values in ENUMS.items()):
        raise ValueError("closed observation enum required")
    if any(row[k] != value for k, value in CONSTANTS.items()):
        raise ValueError("observation scope changed")
    for key in COUNTS:
        if row[key] is not None and (type(row[key]) is not int or not 0 <= row[key] <= 200000):
            raise ValueError("bounded observation count required")
    for key in CODES:
        if row[key] is not None and (type(row[key]) is not int or not -127 <= row[key] <= 255):
            raise ValueError("bounded original status required")
    if type(row["shell_exit"]) is not int or not 0 <= row["shell_exit"] <= 255:
        raise ValueError("independent shell status required")
    if row["fixture_outcome"] == "BOUND_EXPECTED_UNRELIABLE" and (
        row["receipt_status"] != "DELIVERED"
        or row["cleanup"] != "COMPLETE"
        or row["shell_exit"] != 7
        or row["worker_exit"] != 0
        or row["cli_exit"] != 7
    ):
        raise ValueError("fixture status contradiction")
    if (row["event_integrity"] == "COMPLETE") != (row["event_reason"] == "NONE") or (
        row["event_integrity"] == "UNAVAILABLE"
    ) != (row["event_reason"] == "NOT_OBSERVED"):
        raise ValueError("event reason contradiction")
    for status, count in (("event_integrity", "event_count"), ("trace_integrity", "trace_count")):
        if row[status] == "COMPLETE" and (
            row["receipt_status"] != "DELIVERED"
            or row["cleanup"] != "COMPLETE"
            or row[count] is None
            or row[count] == 0
        ):
            raise ValueError("complete observation contradiction")
        if row[status] != "COMPLETE" and row[count] is not None:
            raise ValueError("unvalidated observation count")
    if (row["trace_integrity"] == "COMPLETE") != (row["sync_count"] is not None):
        raise ValueError("unvalidated sync count")
    if row["sync_count"] is not None and row["sync_count"] > row["trace_count"]:
        raise ValueError("sync count contradiction")
    if row["mode"] == "baseline" and (
        row["event_integrity"] != "UNAVAILABLE"
        or row["trace_integrity"] != "UNAVAILABLE"
        or row["execution_identity"] != "UNASSESSED"
    ):
        raise ValueError("baseline cannot supply diagnostic evidence")
    return dict(row)


def encode(row):
    return json.dumps(validate(row), sort_keys=True) + "\n"
