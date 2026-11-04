"""Derive bounded typed relations from one supplied snapshot, not admission.

No collection or freshness is established here; paths and identifiers are private.
"""

MAX_UNITS = 256
MAX_CANDIDATES = 64
MAX_EDGES = 4096
MAX_STRING = 4096
ROOTS = ("basic.target", "app.slice")
FORWARD = (
    "Requires",
    "Wants",
    "BindsTo",
    "Upholds",
    "Triggers",
    "Conflicts",
    "OnFailure",
    "OnSuccess",
    "PropagatesStopTo",
)
# Direction is preserved exactly as reported; no transaction semantics inferred.
RELATIONS = (
    "Requires",
    "Requisite",
    "Wants",
    "BindsTo",
    "PartOf",
    "Upholds",
    "RequiredBy",
    "RequisiteOf",
    "WantedBy",
    "BoundBy",
    "UpheldBy",
    "ConsistsOf",
    "Conflicts",
    "ConflictedBy",
    "Before",
    "After",
    "OnSuccess",
    "OnSuccessOf",
    "OnFailure",
    "OnFailureOf",
    "Triggers",
    "TriggeredBy",
    "PropagatesReloadTo",
    "ReloadPropagatedFrom",
    "PropagatesStopTo",
    "StopPropagatedFrom",
    "JoinsNamespaceOf",
    "SliceOf",
)


class Unknown(ValueError):
    pass


def derive(snapshot):
    try:
        return _derive(snapshot)
    except (Unknown, KeyError, TypeError):
        return {"status": "UNKNOWN", "reason": "incomplete_or_ambiguous"}


def _derive(snapshot):
    if not isinstance(snapshot, dict):
        raise Unknown()
    roster, units = snapshot["roster"], snapshot["units"]
    if not isinstance(roster, list) or not isinstance(units, list) or len(roster) > MAX_UNITS:
        raise Unknown()
    if len(roster) != len(units):
        raise Unknown()
    paths = {}
    for row in roster:
        if not isinstance(row, (list, tuple)) or len(row) != 5:
            raise Unknown()
        name, path, load, active, sub = row
        if not all(type(item) is str and 0 < len(item) <= MAX_STRING for item in row) or path in paths:
            raise Unknown()
        paths[path] = (name, load, active, sub)
    aliases, objects = {}, {}
    for unit in units:
        path, props = unit["path"], unit["properties"]
        if path not in paths or path in objects or not isinstance(props, dict):
            raise Unknown()
        name, load, active, sub = paths[path]
        if tuple(props[k] for k in ("Id", "LoadState", "ActiveState", "SubState")) != (
            name,
            load,
            active,
            sub,
        ):
            raise Unknown()
        names = props["Names"]
        if not isinstance(names, list) or not names or len(names) > MAX_UNITS or name not in names:
            raise Unknown()
        for alias in names:
            if type(alias) is not str or not alias or len(alias) > MAX_STRING or alias in aliases:
                raise Unknown()
            aliases[alias] = name
        objects[name] = props
    if len(objects) != len(units) or any(root not in aliases for root in ROOTS):
        raise Unknown()
    edges = []
    unresolved = False
    for name, props in objects.items():
        for kind in RELATIONS:
            targets = props[kind]
            if not isinstance(targets, list) or len(targets) > MAX_UNITS:
                raise Unknown()
            for target in targets:
                if type(target) is not str or not target or len(target) > MAX_STRING:
                    raise Unknown()
                resolved = aliases.get(target)
                unresolved |= resolved is None
                edges.append((name, kind, target, resolved))
                if len(edges) > MAX_EDGES:
                    raise Unknown()
    candidates = {aliases[root] for root in ROOTS}
    pending = list(candidates)
    while pending:
        current = pending.pop()
        if objects[current]["LoadState"] != "loaded":
            raise Unknown()
        for source, kind, _, target in edges:
            if source == current and kind in FORWARD:
                if target is None:
                    raise Unknown()
                if target not in candidates:
                    candidates.add(target)
                    if len(candidates) > MAX_CANDIDATES:
                        raise Unknown()
                    pending.append(target)
    boundary = sorted(
        {
            (source, kind, target)
            for source, kind, _, target in edges
            if target is not None and (source in candidates or target in candidates)
        }
    )
    return {
        "status": "DERIVED_OFFLINE_ONLY",
        "candidates": sorted(candidates),
        "edges": [list(edge) for edge in boundary],
        "inbound_coverage": "UNKNOWN" if unresolved else "REPORTED_FIELDS_ONLY",
        "qualification": "NOT_RUN",
        "atomicity": "NOT_ESTABLISHED",
        "transaction_closure": "NOT_ESTABLISHED",
        "file_evidence": "NOT_COLLECTED",
    }
