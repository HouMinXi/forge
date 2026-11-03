"""Offline-only characterization; these fixtures are invented, not a VM baseline."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "dependency_candidates", Path(__file__).resolve().parents[1] / "scripts/dependency_candidates.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def snapshot():
    units = []
    for name in ("basic.target", "app.slice", "example.service", "inbound.service"):
        props = {
            "Id": name,
            "Names": [name],
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "SubState": "dead",
            **{key: [] for key in module.RELATIONS},
        }
        units.append({"path": "/unit/" + name, "properties": props})
    units[0]["properties"]["Wants"] = ["example.service"]
    units[3]["properties"]["Conflicts"] = ["example.service"]
    return {
        "roster": [[u["properties"]["Id"], u["path"], "loaded", "inactive", "dead"] for u in units],
        "units": units,
    }


def test_typed_outbound_and_inbound():
    result = module.derive(snapshot())
    assert result["candidates"] == ["app.slice", "basic.target", "example.service"]
    assert ["inbound.service", "Conflicts", "example.service"] in result["edges"]
    assert result["atomicity"] == "NOT_ESTABLISHED"
    assert result["qualification"] == "NOT_RUN"


def test_alias_resolves_without_expanding_candidate_set():
    value = snapshot()
    value["units"][2]["properties"]["Names"].append("alias.service")
    value["units"][0]["properties"]["Wants"] = ["alias.service"]
    assert "example.service" in module.derive(value)["candidates"]


def test_missing_ambiguous_and_changed_are_unknown_without_echo():
    for change in ("missing", "ambiguous", "changed"):
        value = snapshot()
        if change == "missing":
            value["units"][0]["properties"]["Wants"] = ["private-absent.service"]
        elif change == "ambiguous":
            value["units"][1]["properties"]["Names"].append("example.service")
        else:
            value["units"][0]["properties"]["SubState"] = "changed"
        result = module.derive(value)
        assert result == {"status": "UNKNOWN", "reason": "incomplete_or_ambiguous"}


def test_over_limit_and_missing_property_are_unknown():
    value = snapshot()
    value["roster"] *= 65
    assert module.derive(value)["status"] == "UNKNOWN"
    value = snapshot()
    del value["units"][0]["properties"]["Triggers"]
    assert module.derive(value)["status"] == "UNKNOWN"


def test_candidate_budget_is_fail_closed():
    value = snapshot()
    root = value["units"][0]["properties"]
    for index in range(64):
        name = f"invented-{index}.service"
        props = {
            "Id": name,
            "Names": [name],
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "SubState": "dead",
            **{key: [] for key in module.RELATIONS},
        }
        path = "/unit/" + name
        value["units"].append({"path": path, "properties": props})
        value["roster"].append([name, path, "loaded", "inactive", "dead"])
        root["Wants"].append(name)
    assert module.derive(value)["status"] == "UNKNOWN"


def test_duplicate_roster_and_nonloaded_are_unknown():
    value = snapshot()
    value["roster"][1][1] = value["roster"][0][1]
    assert module.derive(value)["status"] == "UNKNOWN"
    value = snapshot()
    value["roster"][0][2] = "not-found"
    value["units"][0]["properties"]["LoadState"] = "not-found"
    assert module.derive(value)["status"] == "UNKNOWN"


def test_unrelated_not_found_and_unresolved_inbound_are_not_silently_complete():
    value = snapshot()
    for index in range(3):
        name = f"redacted-absent-{index}.service"
        path = "/unit/" + name
        props = {
            "Id": name,
            "Names": [name],
            "LoadState": "not-found",
            "ActiveState": "inactive",
            "SubState": "dead",
            **{key: [] for key in module.RELATIONS},
        }
        value["roster"].append([name, path, "not-found", "inactive", "dead"])
        value["units"].append({"path": path, "properties": props})
    value["units"][3]["properties"]["StopPropagatedFrom"] = ["example.service"]
    value["units"][3]["properties"]["Requisite"] = ["not-on-roster.service"]
    result = module.derive(value)
    assert result["status"] == "DERIVED_OFFLINE_ONLY"
    assert ["inbound.service", "StopPropagatedFrom", "example.service"] in result["edges"]
    assert result["inbound_coverage"] == "UNKNOWN"


def test_redacted_observed_roster_shape_without_reusing_candidate_list():
    # Aggregate shape of the previous receipt only: 181 records, three not-found,
    # and observed typed relations. All names, paths and edge endpoints here are
    # invented; this fixture is not an input list for any real collection.
    value = snapshot()
    for index in range(177):
        name = f"redacted-{index}.service"
        load = "not-found" if index >= 174 else "loaded"
        path = "/redacted/" + name
        props = {
            "Id": name,
            "Names": [name],
            "LoadState": load,
            "ActiveState": "inactive",
            "SubState": "dead",
            **{key: [] for key in module.RELATIONS},
        }
        value["units"].append({"path": path, "properties": props})
        value["roster"].append([name, path, load, "inactive", "dead"])
    incoming = value["units"][3]["properties"]
    for relation in ("After", "Before", "Requires", "RequiredBy", "Triggers", "Wants", "ConflictedBy"):
        incoming[relation] = ["example.service"]
    result = module.derive(value)
    assert len(value["units"]) == 181
    assert result["status"] == "DERIVED_OFFLINE_ONLY"
    assert result["candidates"] == ["app.slice", "basic.target", "example.service"]
    assert {edge[1] for edge in result["edges"]} >= {
        "After",
        "Before",
        "Requires",
        "RequiredBy",
        "Triggers",
        "Wants",
        "ConflictedBy",
    }
