# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Shared SARIF 2.1.0 parser for ruff and semgrep (DRY)."""

import json
import re

from code_forge.parsers.base import Finding, ToolError


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("missing reference text")
    return value


def _index(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid reference index")
    return value


def _referenced_object(values: list, reference: dict) -> dict:
    if not isinstance(values, list):
        raise TypeError("malformed reference table")
    if "index" in reference:
        index = _index(reference["index"])
        if index >= len(values):
            raise ValueError("reference index out of range")
        selected = values[index]
    else:
        guid = _text(reference["guid"])
        matches = [value for value in values if isinstance(value, dict) and value.get("guid") == guid]
        if len(matches) != 1:
            raise ValueError("missing or ambiguous reference GUID")
        selected = matches[0]
    if not isinstance(selected, dict):
        raise TypeError("malformed referenced object")
    for key in ("guid", "name"):
        if key in reference and _text(reference[key]) != selected.get(key):
            raise ValueError("conflicting reference alias")
    return selected


def _artifact_uri(run: dict, artifact: dict) -> str:
    if "index" not in artifact:
        return _text(artifact["uri"])
    cached = _referenced_object(run.get("artifacts", []), artifact)["location"]
    uri = _text(cached["uri"])
    if "index" in cached and _index(cached["index"]) != artifact["index"]:
        raise ValueError("conflicting cached artifact index")
    if "uri" in artifact and (
        _text(artifact["uri"]) != uri or artifact.get("uriBaseId") != cached.get("uriBaseId")
    ):
        raise ValueError("conflicting artifact URI")
    return uri


def _rule_messages(run: dict, result: dict) -> tuple[dict, dict]:
    reference = result.get("rule", {})
    if not isinstance(reference, dict):
        raise TypeError("malformed rule reference")
    reference = dict(reference)
    for key, alias in (("index", "ruleIndex"), ("id", "ruleId")):
        if key in reference:
            (_index if key == "index" else _text)(reference[key])
        if alias in result:
            (_index if key == "index" else _text)(result[alias])
            if key in reference and reference[key] != result[alias]:
                raise ValueError("conflicting rule aliases")
            reference[key] = result[alias]
    component_ref = reference.get("toolComponent", {})
    if not isinstance(component_ref, dict):
        raise TypeError("malformed component reference")
    tool = run["tool"]
    if "index" in component_ref:
        component = _referenced_object(tool.get("extensions", []), component_ref)
    elif "guid" in component_ref:
        extensions = tool.get("extensions", [])
        if not isinstance(extensions, list):
            raise TypeError("malformed component table")
        component = _referenced_object([tool["driver"], *extensions], component_ref)
    else:
        component = tool["driver"]
        if "name" in component_ref and _text(component_ref["name"]) != component.get("name"):
            raise ValueError("conflicting default component name")
    rules = component.get("rules", [])
    if "index" in reference or "guid" in reference:
        rule = _referenced_object(rules, reference)
        identity = _text(rule["id"])
        if "id" in reference:
            suffix = reference["id"][len(identity) + 1 :]
            if reference["id"] != identity and not (
                reference["id"].startswith(identity + "/") and suffix and "/" not in suffix
            ):
                raise ValueError("conflicting rule ID")
    else:
        if not isinstance(rules, list):
            raise TypeError("malformed rule table")
        matches = [
            rule
            for rule in rules
            if isinstance(rule, dict) and "id" in reference and rule.get("id") == reference["id"]
        ]
        if len(matches) > 1:
            raise ValueError("ambiguous rule ID")
        rule = matches[0] if matches else {}
    return rule.get("messageStrings", {}), component.get("globalMessageStrings", {})


def _message_text(run: dict, result: dict, producer_profile: str | None = None) -> str:
    message = result["message"]
    if "text" in message and message["text"] != "":
        text = _text(message["text"])
        if producer_profile == "ruff" and "arguments" not in message:
            return text
    else:
        identity = _text(message["id"])
        for messages in _rule_messages(run, result):
            if not isinstance(messages, dict):
                raise TypeError("malformed message table")
            if identity in messages:
                text = _text(messages[identity]["text"])
                break
        else:
            raise ValueError("missing referenced message")
    arguments = message.get("arguments", [])
    if not isinstance(arguments, list) or any(not isinstance(value, str) for value in arguments):
        raise TypeError("malformed message arguments")

    def replace(match: re.Match) -> str:
        if match.group(1) is None:
            return match.group(0)[0]
        index = int(match.group(1))
        if index >= len(arguments):
            raise ValueError("message argument out of range")
        return arguments[index]

    return re.sub(r"{{|}}|{([0-9]+)}", replace, text)


def _parse_sarif(
    output: str,
    tool_name: str,
    exit_code: int = 0,
    *,
    producer_profile: str | None = None,
) -> list[Finding | ToolError]:
    """Parse SARIF 2.1.0 JSON into Finding objects.

    Shared by ruff and semgrep -- tool_name distinguishes them.

    Returns:
        [] on a complete, successful report with explicit empty results.
        [Finding, ...] on valid SARIF with results.
        [ToolError] on malformed/unparseable output.
    """

    def error(message: str) -> ToolError:
        return ToolError(tool_name, exit_code, "", message)

    if not output.strip():
        return [error(f"Missing {tool_name} SARIF output")]
    try:
        # Use raw_decode to parse the FIRST JSON value, ignoring
        # trailing noise (e.g. golangci-lint appends a text summary
        # after the SARIF JSON).
        dec = json.JSONDecoder()
        sarif, _end = dec.raw_decode(output.lstrip())
    except (json.JSONDecodeError, ValueError):
        return [error(f"Failed to parse {tool_name} SARIF output")]

    if not isinstance(sarif, dict) or sarif.get("version") != "2.1.0":
        return [error(f"Invalid {tool_name} SARIF 2.1.0 document")]
    runs = sarif.get("runs")
    if not isinstance(runs, list) or not runs:
        return [error(f"Invalid {tool_name} SARIF runs")]

    findings: list[Finding | ToolError] = []
    problems: list[str] = []
    for run in runs:
        if not isinstance(run, dict):
            problems.append("malformed run")
            continue
        tool = run.get("tool")
        driver = tool.get("driver") if isinstance(tool, dict) else None
        if not isinstance(driver, dict) or not isinstance(driver.get("name"), str) or not driver["name"]:
            problems.append("missing tool driver")
        invocations = run.get("invocations", [])
        if not isinstance(invocations, list):
            problems.append("malformed invocations")
        else:
            problems.extend(
                "failed or incomplete invocation"
                for invocation in invocations
                if not isinstance(invocation, dict) or invocation.get("executionSuccessful") is not True
            )
        results = run.get("results")
        if not isinstance(results, list):
            problems.append("missing or malformed results")
            continue
        for result in results:
            if not isinstance(result, dict):
                problems.append("malformed result")
                continue
            locations = result.get("locations")
            if not isinstance(locations, list) or not locations:
                problems.append("unrepresented result location")
                continue
            for location in locations:
                try:
                    phys = location["physicalLocation"]
                    artifact = phys["artifactLocation"]
                    region = phys.get("region", {})
                    uri = _artifact_uri(run, artifact)
                    if not isinstance(region, dict):
                        raise TypeError("malformed region")
                    message = _message_text(run, result, producer_profile)
                    rule_id = result.get("ruleId", "unknown")
                    level = result.get("level", "warning")
                    if not isinstance(message, str) or not message or not isinstance(rule_id, str):
                        raise ValueError("malformed diagnostic")
                    if level not in ("none", "note", "warning", "error"):
                        raise ValueError("malformed diagnostic level")
                    # Strip file:// prefix, preserving absolute path.
                    # file:///tmp/foo -> /tmp/foo (not tmp/foo).
                    if uri.startswith("file:///"):
                        uri = uri[len("file://") :]
                    elif uri.startswith("file://"):
                        uri = uri[len("file://") :]
                    start_line = region.get("startLine", 0)
                    end_line_raw = region.get("endLine")
                    column = region.get("startColumn", 0)
                    for name in ("startLine", "endLine", "startColumn"):
                        if name in region and (type(region[name]) is not int or region[name] <= 0):
                            raise ValueError("malformed position")
                    if end_line_raw is not None and end_line_raw < start_line:
                        raise ValueError("reversed line range")
                    findings.append(
                        Finding(
                            file=uri,
                            line=start_line,
                            end_line=(end_line_raw if end_line_raw is not None else start_line),
                            column=column,
                            rule_id=rule_id,
                            level=level,
                            message=message,
                            tool_name=tool_name,
                        )
                    )
                except (KeyError, TypeError, AttributeError, ValueError):
                    problems.append("malformed diagnostic location or fields")
    if producer_profile == "ruff":
        if exit_code not in (0, 1):
            problems.append(f"Ruff exited abnormally ({exit_code})")
        elif exit_code == 1 and not findings:
            problems.append("Ruff exited 1 without diagnostics in detection mode")
    elif exit_code != 0 and not findings:
        problems.append(f"tool exited {exit_code} without diagnostics")
    if problems:
        findings.append(
            error(f"Incomplete {tool_name} SARIF evidence: " + "; ".join(dict.fromkeys(problems)))
        )
    return findings
