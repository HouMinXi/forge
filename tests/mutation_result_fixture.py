"""Explicit native-shaped data for tests that replace the external tool."""

import json


def write_inventory(root, relative, mutants=None):
    mutants = {"x_example__mutmut_1": "killed"} if mutants is None else mutants
    mirror = root / "mutants" / relative
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text("\n".join(f"def {name}():\n    return 1" for name in mutants))
    module = relative.removesuffix(".py").replace("/", ".").removeprefix("src.")
    records = {f"{module}.{name}".replace(".__init__.", "."): status for name, status in mutants.items()}
    exits = {name: 0 if status == "survived" else 1 for name, status in records.items()}
    mirror.with_name(mirror.name + ".meta").write_text(
        json.dumps(
            {
                "exit_code_by_key": exits,
                "durations_by_key": {},
                "estimated_durations_by_key": {},
                "type_check_error_by_key": {},
            }
        )
    )
    return "\n".join(f"{name}: {status}" for name, status in records.items())
