# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Owned test instrumentation around the unmodified native CLI/CI consumer.

Reports are a test oracle, not a production result outlet. No exception text or
tracebacks are serialized. The backend is a local executable owned by the test.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import types


def _receipts(machine):
    return {
        path.name: path.read_bytes()
        for path in (machine.cwd / ".code-forge/receipts").glob("receipt-*.json")
    }


def main():
    try:
        import code_review_graph.registry  # noqa: F401
    except ImportError:
        package = types.ModuleType("code_review_graph")
        package.__path__ = []
        provider = types.ModuleType("code_review_graph.registry")
        provider.default_registry_path = lambda: Path(os.environ["CRG_HOME"]) / "registry.json"
        package.registry = provider
        sys.modules[package.__name__] = package
        sys.modules[provider.__name__] = provider

    from code_forge import _advisory_diagnostics as storage
    from code_forge import _cross_repo_diagnostics as capture_module
    from code_forge import cli
    from code_forge.disposition import Disposition
    from code_forge.machine import StateMachine

    control = Path(os.environ["FORGE_FIXTURE_CONTROL"])
    reports, before, receipts = {}, {}, {}
    captures, reads, presentations, publications = [], [], [], []
    native_init = capture_module.DiagnosticCapture.__init__
    native_read = storage.read_observation
    native_publish = storage.publish_observation
    native_present = capture_module.DiagnosticCapture.present
    native_close = capture_module.DiagnosticCapture.close
    native_run = StateMachine.run

    def initialize(owner, **kwargs):
        native_init(owner, **kwargs)
        # Retained at issuance, before any publisher or consumer runs.
        expected = storage.ExpectedContext(
            kwargs["execution_id"],
            owner.repository_id,
            kwargs["source_hash"],
            hashlib.sha256(kwargs["diff_text"].encode("utf-8")).hexdigest(),
            1,
        )
        captures.append((owner, expected))

    def publish(binding, observation):
        publications.append({"issued_first": len(captures) == 1})
        return native_publish(binding, observation)

    def read(publication, expected):
        entry = {"expected": asdict(expected), "succeeded": False, "rejected": False}
        reads.append(entry)
        if os.environ.get("FORGE_FIXTURE_READ_LOSS") == "1":
            # Damage only this committed publication, not the expected context.
            locator = publication.locator
            path = locator.binding.source_root.joinpath(
                *(name for name, _ in locator.directories), locator.filename
            )
            path.unlink()
        try:
            result = native_read(publication, expected)
        except storage.DiagnosticError:
            entry["rejected"] = True
            raise
        entry.update(
            succeeded=True,
            context=asdict(result.context),
            state=result.analysis_state,
            diagnostic_count=len(result.diagnostics),
            message_matches=tuple(item.message for item in result.diagnostics) == (storage.MESSAGE,),
        )
        return result

    def present(owner, record, **kwargs):
        result = native_present(owner, record, **kwargs)
        presentations.append(
            {
                "owned": len(captures) == 1 and owner is captures[0][0],
                "state": record.analysis_state,
                "publication": record.publication.status,
                "context": asdict(record.observation.context) if record.observation else None,
                "message_count": len(result),
                "status": owner.delivery_status,
            }
        )
        return result

    def profile(frame, event, value):
        if frame.f_code is StateMachine._run_ci.__code__ and event == "return":
            machine = frame.f_locals["self"]
            report = reports.setdefault(machine.baseline_spec_repr, {})
            report.setdefault("ci_returns", []).append(value.value if value else None)
        if frame.f_code is StateMachine._run_advisory_axes.__code__:
            machine = frame.f_locals["self"]
            label = machine.baseline_spec_repr
            report = reports.setdefault(label, {})
            if event == "call":
                before[label] = deepcopy(machine._state)
                receipts[label] = _receipts(machine)
                report["advisory_calls"] = report.get("advisory_calls", 0) + 1
            elif event == "return":
                state = before[label]
                report["advisory_returns"] = report.get("advisory_returns", 0) + 1
                report["state_unchanged"] = (
                    replace(machine._state, infra_errors=state.infra_errors) == state
                )
                report["gate_infra_empty"] = state.infra_errors == []
                report["coverage_gap_count"] = machine._count_coverage_gaps()
                report["coverage_l1_active"] = machine.coverage_l1_active
                report["coverage_note_only"] = len(state.infra_errors) == 1 and (
                    state.infra_errors[0].startswith("coverage: ")
                )
                report["diagnostic_not_in_gate"] = all(
                    "cross-repo-impact" not in error for error in machine._state.infra_errors
                ) and all(
                    storage.MESSAGE not in finding.description for finding in machine._state.findings
                )
                report["converged"] = state.converged
                confirmed = [f for f in state.findings if f.disposition is Disposition.CONFIRMED]
                report["confirmed_count"] = len(confirmed)
                report["confirmed_all_l1"] = all(f.source == "L1" for f in confirmed)
                report["receipts_unchanged"] = receipts[label] == _receipts(machine)
                report["receipts"] = {
                    name: {
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "pass_status": json.loads(raw)["pass_status"],
                        "findings_count": json.loads(raw)["findings_count"],
                        "excerpt_count": len(json.loads(raw)["code_excerpts"]),
                    }
                    for name, raw in receipts[label].items()
                }
        if (
            frame.f_code
            in (
                StateMachine._receipt_gate_round_errors.__code__,
                StateMachine._receipt_gate_terminal_errors.__code__,
            )
            and event == "return"
        ):
            machine = frame.f_locals["self"]
            report = reports.setdefault(machine.baseline_spec_repr, {})
            report.setdefault("receipt_gates", []).append([frame.f_code.co_name, not value])

    def observed_run(machine):
        verdict = native_run(machine)
        report = reports.setdefault(machine.baseline_spec_repr, {})
        report.update(
            pid=os.getpid(),
            cwd=str(machine.cwd),
            source_root=str(machine._source_root()),
            verdict=verdict.value,
        )
        if machine.baseline_spec_repr == "primary":
            (control / "primary-ready").touch()
            deadline = time.monotonic() + 30
            while not (control / "release").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("owned native launcher release deadline")
                time.sleep(0.01)
        return verdict

    def close(owner):
        native_close(owner)
        report = {
            "expected": [asdict(expected) for _, expected in captures],
            "publications": publications,
            "reads": reads,
            "presentations": presentations,
            "closed": not owner._active,
            "delivery_status": owner.delivery_status,
            "delivery_origin": owner.last_delivery_origin,
        }
        (control / "capture.json").write_text(json.dumps(report), encoding="utf-8")

    capture_module.DiagnosticCapture.__init__ = initialize
    capture_module.DiagnosticCapture.present = present
    capture_module.DiagnosticCapture.close = close
    storage.publish_observation = publish
    storage.read_observation = read
    StateMachine.run = observed_run
    previous = threading.getprofile()
    threading.setprofile(profile)
    try:
        return cli.main()
    finally:
        threading.setprofile(previous)
        (control / "machines.json").write_text(json.dumps(reports), encoding="utf-8")
