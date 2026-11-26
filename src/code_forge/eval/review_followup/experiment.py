"""Measure paired prompt rendering without sending any requests."""

from contextlib import redirect_stderr
from io import StringIO

from .context_plan import _encoded, preview
from .experiment_adapter import BACKEND, MAX_ATTEMPTS, MAX_CAPTURE_BYTES, PASSES, Group, capture, freeze
from .fixtures import InputError, digest

CONTEXT_HEADER = b"\n## Context Sources\n"
PATHS = ("ordinary", "grouped")
ARMS = ("baseline", "targeted")


def _block(text):
    return CONTEXT_HEADER + text.encode("utf-8") + b"\n" if text else b""


def compare(control, arm, text, group, budget, field):
    """Verify one exact insertion at a known boundary, not a text replacement."""
    # This probe supplies no post-image, conventions, graph or manifest. The
    # fixture's nonempty Design Intent is always immediately after context.
    anchor = ("\n## Design Intent\n" + group.contract + "\n").encode()
    offset = control.prompt.find(anchor)
    if offset < 0 or control.prompt.find(anchor, offset + 1) >= 0:
        raise InputError("ambiguous-context-boundary", field)
    block = _block(text)
    prefix, suffix = control.prompt[:offset], control.prompt[offset:]
    if (arm.group, arm.pass_name, arm.evidence_required) != (
        control.group,
        control.pass_name,
        control.evidence_required,
    ) or arm.prompt != prefix + block + suffix:
        raise InputError("non-context-prompt-drift", field)
    if len(arm.prompt) > budget:
        raise InputError("prompt-over-budget", field)
    return {
        "prompt_bytes": len(arm.prompt),
        "prompt_sha256": digest(arm.prompt),
        "context_bytes": len(block),
        "context_sha256": digest(block),
        "context_span": [offset, offset + len(block)],
        "unchanged_prefix_sha256": digest(prefix),
        "unchanged_suffix_sha256": digest(suffix),
    }


def _selected_ids(row, arm):
    candidates = row["candidates"]
    selected = {item["id"] for item in candidates if item[arm] == "selected"}
    order = row["baseline_order"] if arm == "baseline" else (item["id"] for item in candidates)
    return [item for item in order if item in selected]


def dry_run(root, case, max_prompt_bytes):
    if type(max_prompt_bytes) is not int or not 1 <= max_prompt_bytes <= 4 * 1024 * 1024:
        raise InputError("invalid-prompt-budget", "max_prompt_bytes")

    plan = preview(root, case)
    groups = tuple(
        Group(r["group"], tuple(r["members"]), r["diff"], r["contract"])
        for r in (row["required"] for row in plan["groups"])
    )
    frozen = freeze(groups)
    empty = dict.fromkeys((g.name for g in groups), "")
    texts = {
        arm: {
            g.name: _encoded(row[arm]["optional"]).decode("utf-8") if row[arm]["optional"] else ""
            for g, row in zip(groups, plan["groups"], strict=True)
        }
        for arm in ARMS
    }
    controls = {}
    observations = {}
    # The in-process adapter is driven serially by the offline entry point.
    # Expected synthetic refusal/progress lines are not experiment findings.
    with redirect_stderr(StringIO()):
        for path in PATHS:
            controls[path] = capture(path, frozen, empty, max_prompt_bytes)
            observations[path] = {
                arm: capture(path, frozen, texts[arm], max_prompt_bytes) for arm in ARMS
            }

    # Same fixture inputs should render the same way whether grouped or
    # passed through the ordinary factory one group at a time.
    if (
        controls["ordinary"] != controls["grouped"]
        or observations["ordinary"] != observations["grouped"]
    ):
        raise InputError("factory-path-drift", case)
    by_name = {g.name: g for g in groups}
    rows = []
    for path in PATHS:
        for i, control in enumerate(controls[path]):
            group = by_name[control.group]
            pair = {arm: observations[path][arm][i] for arm in ARMS}
            rows.append(
                {
                    "path": path,
                    "group": group.name,
                    "pass": control.pass_name,
                    "byte_identical_arms": pair["baseline"].prompt == pair["targeted"].prompt,
                    **{
                        arm: compare(
                            control,
                            got,
                            texts[arm][group.name],
                            group,
                            max_prompt_bytes,
                            f"{path}/{group.name}/{control.pass_name}/{arm}",
                        )
                        for arm, got in pair.items()
                    },
                }
            )

    return {
        "schema": 1,
        "kind": "offline-l1-factory-render",
        "capture_boundary": "llm_invoke-entry",
        "case": case,
        "candidate": plan["candidate"],
        "review_input_sha256": plan["review_input_sha256"],
        "context_input_sha256": plan["context_input_sha256"],
        "planned_context": [
            {
                "group": row["required"]["group"],
                "members": row["required"]["members"],
                "limit_bytes": row["limit_bytes"],
                **{
                    arm: {
                        "bytes": row[arm]["bytes"],
                        "payload_sha256": row[arm]["payload_sha256"],
                        "selected": _selected_ids(row, arm),
                    }
                    for arm in ARMS
                },
                "displaced": [item["id"] for item in row["candidates"] if item["displaced"]],
            }
            for row in plan["groups"]
        ],
        "passes": [name for name, _ in PASSES],
        "dispatch": "serial",
        "backend_model": BACKEND.model,
        "backend_format": BACKEND.format,
        "configured_max_attempts_per_slot": MAX_ATTEMPTS,
        "configured_correction_slots": 0,
        "configured_output_token_cap": BACKEND.max_tokens,
        "max_prompt_bytes": max_prompt_bytes,
        "max_capture_bytes_per_render": MAX_CAPTURE_BYTES,
        "captured_slots_per_arm_per_path": len(groups) * len(PASSES),
        "sent_requests": 0,
        "provider_usage": None,
        "rows": rows,
    }
