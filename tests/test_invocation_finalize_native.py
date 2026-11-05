"""Qualified native filesystem tests. NOT RUN on unsupported hosts.

No admission override: these need the recorder's actual supported filesystem.
Pure/MOCK controls elsewhere do not satisfy these tests.
"""

from dataclasses import replace

from code_forge import invocation_audit as audit
from code_forge.invocation_owner import InvocationOwner
from tests.test_invocation_audit import Clock, RUN, context, utc

pytest_plugins = ["tests.test_invocation_audit"]


def test_v2_native_incomplete_terminal_reopens_incomplete(disk_root):
    recorder = audit.AttemptRecorder(
        disk_root,
        context=replace(context(), source_hash=None, snapshot_id=None),
        monotonic=Clock(),
        utc_now=utc,
        audit_schema_version=2,
    )
    assert recorder.open().admitted
    assert recorder.finalize("incomplete", audit_incomplete=True).admitted
    assert not audit.AttemptRecorder.reopen_scoped(disk_root, RUN).audit_complete
    assert recorder.finalize("incomplete").admitted
    assert not audit.AttemptRecorder.reopen_scoped(disk_root, RUN).audit_complete


def test_v2_native_owner_incomplete_terminal_reopens_incomplete(disk_root):
    owner = InvocationOwner.create(
        artifact_root=disk_root,
        context=replace(context(), source_hash=None, snapshot_id=None),
        monotonic=Clock(),
        utc_now=utc,
        audit_schema_version=2,
    )
    summary = owner.finalize("incomplete", audit_incomplete=True)
    assert summary.state == "incomplete" and not summary.audit_complete
    assert not audit.AttemptRecorder.reopen_scoped(disk_root, RUN).audit_complete
