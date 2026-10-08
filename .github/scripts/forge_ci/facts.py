"""Finite unchanged natural-adapter eligibility checks for the late driver."""
from __future__ import annotations
import ast
import hashlib
import os
from pathlib import Path
import stat
from typing import Any

class FactError(RuntimeError):
    pass

GUARDS = {
    "javascript": ("test_mutation_js_real.py", "requires", {
        "NODE_MODULES": "/home/houminxi/code/hermes/cache/scratch/js-qual/node_modules",
        "NODE": "/home/houminxi/.local/bin/node"},
        "not (os.path.isdir(CGROUP_ROOT) and os.path.isdir(NODE_MODULES) and os.path.isfile(NODE))"),
    "css": ("test_mutation_css_real.py", "requires", {
        "NODE": "/home/houminxi/code/hermes/node/bin",
        "PLAYWRIGHT": "/home/houminxi/code/hermes/cache/scratch/css-tools/node_modules",
        "CHROME": "/opt/google/chrome"},
        "not (os.path.isdir(CGROUP_ROOT) and os.path.isdir(NODE) and os.path.isdir(PLAYWRIGHT) and os.path.isdir(CHROME))"),
    "rust": ("test_mutation_rust_real.py", "requires", {
        "CARGO_MUTANTS": "/home/houminxi/code/hermes/cache/scratch/cargo-tools/bin/cargo-mutants"},
        "not (os.path.isdir(CGROUP_ROOT) and os.path.isfile(CARGO_MUTANTS))"),
    "go": ("test_mutation_go_real.py", "requires_isolation", {
        "GREMLINS": "/home/houminxi/code/hermes/cache/scratch/go-tools"},
        'not os.path.isdir(CGROUP_ROOT) or not os.path.isfile(GREMLINS + "/gremlins")'),
    "python": ("test_mutation_pyadapter_real.py", None, {}, "not os.path.isdir(CGROUP_ROOT)"),
}


def guard_path(path: str, kind: str) -> dict[str, Any]:
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return {"path": path, "kind": kind, "exists": False, "matches": False}
    except OSError as exc:
        raise FactError(f"unknown adapter eligibility: {path}: {exc}") from exc
    match = stat.S_ISDIR(info.st_mode) if kind == "isdir" else stat.S_ISREG(info.st_mode)
    return {"path": path, "kind": kind, "exists": True, "matches": match,
            "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}


def collect_guards(repo: Path, reader, *, uid: int | None = None) -> dict[str, Any]:
    uid = os.getuid() if uid is None else uid
    results = {}
    for name, (filename, variable, constants, expected) in GUARDS.items():
        source = reader.read(str(repo / "tests" / filename))
        module = ast.parse(source, filename)
        assignments = {node.targets[0].id: node.value for node in module.body
                       if isinstance(node, ast.Assign) and len(node.targets) == 1
                       and isinstance(node.targets[0], ast.Name)}
        values = dict(constants)
        values["CGROUP_ROOT"] = ("/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service" if name == "python"
                                 else f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service")
        for key, value in constants.items():
            if not isinstance(assignments.get(key), ast.Constant) or assignments[key].value != value:
                raise FactError(f"natural {name} adapter guard path changed: {key}")
        cgroup = assignments.get("CGROUP_ROOT")
        if name == "python":
            if not isinstance(cgroup, ast.Constant) or cgroup.value != values["CGROUP_ROOT"]:
                raise FactError("Python-real fixed user-1000 guard changed")
        else:
            reference = ast.parse('"/sys/fs/cgroup/user.slice/user-%d.slice/user@%d.service" % (os.getuid(), os.getuid())', mode="eval").body
            if ast.dump(cgroup) != ast.dump(reference):
                raise FactError(f"natural {name} cgroup guard changed")
        if variable:
            candidates = [assignments.get(variable)]
        else:
            candidates = [decorator for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                          for decorator in node.decorator_list if isinstance(decorator, ast.Call)
                          and ast.unparse(decorator.func) == "pytest.mark.skipif"]
        if len(candidates) != 1 or not isinstance(candidates[0], ast.Call) or not candidates[0].args:
            raise FactError(f"ambiguous {name} availability guard")
        call = candidates[0]
        if ast.unparse(call.func) != "pytest.mark.skipif":
            raise FactError(f"changed {name} availability guard")
        predicate = call.args[0]
        if ast.dump(predicate) != ast.dump(ast.parse(expected, mode="eval").body):
            raise FactError(f"natural {name} availability predicate changed")
        observations = []

        def evaluate(node: ast.AST, values=values, observations=observations, name=name) -> Any:
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name) and node.id in values:
                return values[node.id]
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return evaluate(node.left) + evaluate(node.right)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                return not evaluate(node.operand)
            if isinstance(node, ast.BoolOp):
                children = [evaluate(child) for child in node.values]  # inspect every guard input
                return all(children) if isinstance(node.op, ast.And) else any(children)
            if isinstance(node, ast.Call) and ast.unparse(node.func) in {"os.path.isdir", "os.path.isfile"}:
                observation = guard_path(evaluate(node.args[0]), node.func.attr)
                observations.append(observation)
                return observation["matches"]
            raise FactError(f"unsupported guard expression: {name}")
        skipped = evaluate(predicate)
        results[name] = {"source": "tests/" + filename, "source_sha256": hashlib.sha256(source).hexdigest(),
                         "predicate": ast.unparse(predicate), "inputs": observations,
                         "naturally_eligible": not skipped}
    return {"adapters": results, "unexpectedly_eligible": sorted(name for name, entry in results.items()
                                                                if entry["naturally_eligible"])}


