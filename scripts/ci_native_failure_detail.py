"""Temporary opt-in native-test failure detail, never qualification evidence.

Only exception representations are recorded: no captured streams, locals,
function arguments, environment dump or audit store. GitHub artifact upload
DOES NOT mask secrets. The workflow must remain free of credential injection.
Known credential values and recognizable token forms are redacted here as a
second layer, not a substitute for reviewing CI and fixture inputs.
"""

import json
import os
import re

MODULE = "tests/test_ordinary_audit_native.py"
MAX_TEXT = 65536
SCHEMA = "forge-native-first-failure-v1"
_SECRET_NAME = re.compile(r"(?:TOKEN|PASSWORD|SECRET|CREDENTIAL|PRIVATE_KEY|API_KEY)", re.I)
_TOKEN = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----)"
)
_USERINFO = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")


def eligible(node):
    return type(node) is str and node.startswith(MODULE + "::")


def credential_values():
    # Read only to redact, never serialize the environment or its variable names.
    return tuple(
        sorted(
            {value for name, value in os.environ.items() if _SECRET_NAME.search(name) and value},
            key=len,
            reverse=True,
        )
    )


def scrub(text, secrets=()):
    forms = {
        form for value in secrets for form in (value, repr(value)[1:-1], json.dumps(value)[1:-1]) if form
    }
    for value in sorted(forms, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = _TOKEN.sub("[REDACTED]", text)
    return _USERINFO.sub(r"\1[REDACTED]@", text)


def capture(call, secrets):
    """Render the original failure once, with its cause/context and assert diff."""
    if call.excinfo is None:
        return None
    representation = call.excinfo.getrepr(
        showlocals=False,
        style="long",
        funcargs=False,
        chain=True,
    )
    text = scrub(str(representation), secrets)
    truncated = len(text) > MAX_TEXT
    return {"traceback": text[:MAX_TEXT], "truncated": truncated}


def validate(detail):
    return (
        type(detail) is dict
        and set(detail) == {"traceback", "truncated"}
        and type(detail["traceback"]) is str
        and 0 < len(detail["traceback"]) <= MAX_TEXT
        and type(detail["truncated"]) is bool
        and not any(ord(c) < 32 and c not in "\n\t\r" for c in detail["traceback"])
    )
