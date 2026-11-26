"""Offline seam probe for the existing L1 factories, not a transport runner."""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from code_forge import factories, llm_invoke
from code_forge.backend import BackendConfig
from code_forge.baseline import ResolvedReview
from code_forge.lenses import has_selection_suffix
from code_forge.reviewer_json import REVIEW_REPLY_KEYS

from .fixtures import InputError

PASSES = (
    ("qodo", "structural code reviewer: correctness and logic errors"),
    ("expert", "senior engineer: SOLID, architecture, security"),
    ("adversarial", "adversarial QE: assume bugs exist"),
)
BACKEND = BackendConfig("offline-seam", "api", "offline-placeholder", format="openai", max_tokens=1024)
MAX_ATTEMPTS = 1
MAX_CAPTURE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Group:
    name: str
    members: tuple[str, ...]
    diff: str
    contract: str


@dataclass(frozen=True)
class Capture:
    group: str
    pass_name: str
    prompt: bytes
    evidence_required: bool


def _resolved(group):
    # These are fixture member identities, not paths opened by this probe.
    return ResolvedReview([Path(name) for name in group.members], None, group.diff, "git")


def capture(path, groups, optional, budget):
    """Run a single serial offline capture. Never call the actual invoker.

    The grouped factory has only a shared context arg. Substitute group-local
    kwargs at its child construction seam, not in production.
    """
    records = []
    ordinary = factories.build_l1_provider
    subjects = {id(resolved): group for resolved, group in groups}
    allowance = llm_invoke._ExcerptCorrectionAllowance(slots=0)
    controls = {
        "backend": BACKEND,
        "max_attempts": MAX_ATTEMPTS,
        "initial_delay_s": 0.0,
        "retry_timeout": False,
        "review_lenses": True,
    }
    active = None
    fault = None
    captured_bytes = 0

    def build_child(engine, resolved, **kwargs):
        group = subjects.get(id(resolved))
        if group is None or "context_sources_text" in kwargs or "contract_spec" in kwargs:
            raise InputError("capture-topology-drift", path)
        provider = ordinary(
            engine,
            resolved,
            context_sources_text=optional[group.name],
            contract_spec=group.contract,
            **kwargs,
        )

        # Preserve the real _L1Call and its side channels for the outer factory.
        run = provider._body

        def scoped(*args, **kw):
            nonlocal active
            active = group
            try:
                return run(*args, **kw)
            finally:
                active = None

        provider._body = scoped
        return provider

    def refuse(code, field):
        nonlocal fault
        fault = fault or InputError(code, field)
        raise fault

    def capture_invoke(prompt, **kwargs):
        nonlocal captured_bytes
        if fault is not None:
            raise fault
        position = len(records)
        if position >= len(groups) * len(PASSES):
            refuse("capture-topology-drift", path)
        group = groups[position // len(PASSES)][1]
        name, role = PASSES[position % len(PASSES)]
        # Pin both the role and the canonical lens suffix even when all
        # renders agree with one another.
        tail = prompt.rsplit("\n", 4)
        if (
            active != group
            or len(tail) != 5
            or tail[1] != f"You are a {role}. Review this diff."
            or tail[2] != ""
            or not has_selection_suffix(prompt, name)
        ):
            refuse("capture-topology-drift", path)
        if (
            kwargs["backend"] is not BACKEND
            or kwargs["max_attempts"] != MAX_ATTEMPTS
            or kwargs["initial_delay_s"] != 0.0
            or kwargs["retry_timeout"]
            or kwargs.get("reply_keys") != REVIEW_REPLY_KEYS
            or kwargs["review_diff"] != group.diff
            or kwargs["_excerpt_allowance"] is not allowance
        ):
            refuse("capture-invocation-drift", f"{path}/{group.name}/{name}")
        rendered = prompt.encode("utf-8")
        if len(rendered) > budget:
            refuse("prompt-over-budget", f"{path}/{group.name}/{name}")
        captured_bytes += len(rendered)
        if captured_bytes > MAX_CAPTURE_BYTES:
            refuse("capture-over-budget", path)
        records.append(Capture(group.name, name, rendered, kwargs["l1_evidence_required"]))
        # The synthetic preview is not a production review input. Stop at the
        # invoker boundary; the factory will fold this as a known offline refusal.
        raise llm_invoke.LLMInvokeError("offline capture only", retryable=False, kind="offline-capture")

    with (
        patch.object(llm_invoke, "llm_invoke", capture_invoke),
        patch.object(factories, "_serial_passes_requested", return_value=True),
    ):
        if path == "ordinary":
            findings = [
                f
                for resolved, _ in groups
                for f in build_child("real", resolved, excerpt_allowance=allowance, **controls)()[0]
            ]
        elif path == "grouped":
            with patch.object(factories, "build_l1_provider", build_child):
                runner = factories.build_grouped_l1_provider(
                    "real",
                    [{"name": g.name, "resolved": resolved} for resolved, g in groups],
                    excerpt_allowance=allowance,
                    **controls,
                )
            findings = runner()[0]
        else:
            raise ValueError("unknown factory path")
    if fault is not None:
        raise fault
    if len(records) != len(groups) * len(PASSES) or any(
        not f.fingerprint.startswith("invoke-fail-")
        or not f.description.endswith("offline capture only")
        for f in findings
    ):
        raise InputError("capture-incomplete", path)
    return records


def freeze(groups):
    return tuple((_resolved(group), group) for group in groups)
