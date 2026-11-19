# SPDX-License-Identifier: Apache-2.0

"""Phase 1 of the offline planner. No schema validation or artifact reads."""

from dataclasses import dataclass
import json


class InvalidJSON(ValueError):
    """The request failed lexical validation."""


@dataclass(frozen=True)
class NonSchemaNumber:
    """A valid JSON number token that no field of schema 1 can accept."""

    token: str


def _check_negative_zero(token):
    if token.startswith("-"):
        significand = token[1:].lower().partition("e")[0]
        if all(c in "0." for c in significand):
            raise InvalidJSON("negative zero")


def _integer(token):
    _check_negative_zero(token)
    # Avoid Python's integer conversion limit: schema 1 has no field that
    # accepts integers longer than sixteen digits, but they are legal JSON.
    return int(token) if len(token.lstrip("-")) <= 16 else NonSchemaNumber(token)


def _float(token):
    _check_negative_zero(token)
    return NonSchemaNumber(token)


def _constant(token):
    raise InvalidJSON(f"invalid numeric constant: {token}")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidJSON("duplicate object key")
        result[key] = value
    return result


def decode_request(raw: bytes):
    """Decode strict UTF-8 JSON; leave schema-invalid numbers to Phase 2."""
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_int=_integer,
            parse_float=_float,
            parse_constant=_constant,
            object_pairs_hook=_object,
        )
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, dict):
                pending.extend(item)
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        return value
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidJSON("invalid UTF-8 JSON") from exc
