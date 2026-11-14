"""The correction lane writes its own artifacts and moves nothing else."""

import json
from pathlib import Path

from code_forge.receipt import write_receipts

DIFF = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n"


def _write(root: Path, **kwargs):
    receipts = root / "receipts"
    receipts.mkdir(parents=True)
    write_receipts(
        receipts,
        1,
        [],
        "0" * 64,
        [],
        root,
        diff_text=DIFF,
        **kwargs,
    )
    return {
        str(path.relative_to(receipts)): json.loads(path.read_text())
        for path in sorted(receipts.rglob("*.json"))
    }


def _without_timestamp(files: dict) -> dict:
    out = {}
    for name, body in files.items():
        body = dict(body)
        body.pop("timestamp", None)
        out[name] = body
    return out


def test_correction_lane_does_not_touch_existing_receipts(tmp_path):
    baseline = _write(
        tmp_path / "a",
        attempted_excerpts=[],
        raw_observations=[],
        pending_raw_responses=None,
        correction_observations=None,
    )
    with_correction = _write(
        tmp_path / "b",
        attempted_excerpts=[],
        raw_observations=[],
        pending_raw_responses=None,
        correction_observations=[
            {"pass_name": "qodo", "reason": "correction_failed", "selected": False}
        ],
    )

    assert set(baseline) == {"receipt-c2p1.json", "receipt-c2p2.json", "receipt-c2p3.json"}
    assert set(with_correction) == set(baseline) | {"correction/correction-c2p1-0.json"}
    # Nothing pre-existing moved or changed, except the wall-clock stamp.
    assert _without_timestamp(baseline) == _without_timestamp(
        {k: v for k, v in with_correction.items() if k in baseline}
    )


def test_correction_artifacts_are_not_marked_as_rejected_evidence(tmp_path):
    files = _write(
        tmp_path,
        attempted_excerpts=[],
        raw_observations=[],
        pending_raw_responses=None,
        correction_observations=[
            {"pass_name": "qodo", "reason": "correction_failed", "selected": False},
            {"pass_name": "expert", "reason": "correction_raw_oversize", "selected": False},
        ],
    )

    assert not [name for name in files if name.startswith("attempted/")]
    correction = {name: body for name, body in files.items() if name.startswith("correction/")}
    assert set(correction) == {
        "correction/correction-c2p1-0.json",
        "correction/correction-c2p2-1.json",
    }
    for body in correction.values():
        assert body["correction"] is True
        assert "attempted" not in body
        assert body["payload"]["selected"] is False


def test_correction_lane_starts_at_zero_next_to_attempted(tmp_path):
    """Attempted ordinals are the attempted lane's; correction counts its own."""
    with_attempted = _write(
        tmp_path / "a",
        attempted_excerpts=[
            {"pass_name": "qodo", "raw_response": "one"},
            {"pass_name": "expert", "raw_response": "two"},
        ],
        raw_observations=[],
        pending_raw_responses=None,
        correction_observations=None,
    )
    with_both = _write(
        tmp_path / "b",
        attempted_excerpts=[
            {"pass_name": "qodo", "raw_response": "one"},
            {"pass_name": "expert", "raw_response": "two"},
        ],
        raw_observations=[],
        pending_raw_responses=None,
        correction_observations=[
            {"pass_name": "adversarial", "reason": "correction_failed", "selected": False}
        ],
    )

    attempted_before = sorted(n for n in with_attempted if n.startswith("attempted/"))
    attempted_after = sorted(n for n in with_both if n.startswith("attempted/"))
    assert attempted_before == attempted_after
    assert len(attempted_before) == 2
    assert "correction/correction-c2p3-0.json" in with_both


def test_writer_returns_exactly_the_three_pass_receipts(tmp_path):
    """L4-02: a sidecar in the return value empties the machine's snapshot."""
    receipts = tmp_path / "receipts"
    receipts.mkdir(parents=True)
    written = write_receipts(
        receipts,
        1,
        [],
        "0" * 64,
        [],
        tmp_path,
        diff_text=DIFF,
        attempted_excerpts=[],
        raw_observations=[],
        pending_raw_responses=None,
        correction_observations=[
            {"pass_name": "qodo", "reason": "correction_failed", "selected": False}
        ],
    )

    names = sorted(path.name for path in written)
    assert names == ["receipt-c2p1.json", "receipt-c2p2.json", "receipt-c2p3.json"]
    # The sidecar is still on disk, just not part of the returned contract.
    assert (receipts / "correction" / "correction-c2p1-0.json").is_file()
