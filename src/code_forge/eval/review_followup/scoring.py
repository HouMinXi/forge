"""Pure projection of frozen observation and human labels."""

from dataclasses import asdict


def score(review, run, answer, human):
    judgments = {item.finding: item for item in human.findings}
    hits = set()
    root_reports = 0
    blockers = 0
    pending = 0
    details = []
    for finding in sorted(run.findings, key=lambda item: item.id):
        row = judgments.get(finding.id)
        pending += row is None or "uncertain" in (row.factual, row.blocking)
        if row is not None:
            if row.root is not None:
                hits.add(row.root)
                root_reports += 1
            if finding.blocking and row.blocking == "should_not_block":
                blockers += 1
        details.append({"finding": asdict(finding), "judgment": asdict(row) if row else None})

    revisions = {r[0]: r for r in human.revisions}
    followups = []
    for finding in sorted(review.prior_findings):
        _, outcome, evidence, rationale = revisions.get(finding, (finding, None, (), None))
        followups.append(
            {
                "finding": finding,
                "outcome": outcome,
                "evidence": list(evidence),
                "rationale": rationale,
                "open": outcome not in ("verified_fixed", "no_longer_applicable"),
            }
        )
    scorable = run.status == "completed" and answer.roots is not None
    return {
        "case": review.case,
        "candidate": review.candidate,
        "lineage": review.lineage,
        "split": review.split,
        "task": review.task,
        "run": run.run,
        "status": run.status,
        "input_sha256": review.sha256,
        "observation_sha256": run.sha256,
        "diagnostic": run.diagnostic,
        "usage": run.usage,
        "baseline": dict(run.baseline),
        "findings": details,
        "known_roots": len(answer.roots) if answer.roots is not None else None,
        "hits": len(hits) if scorable else None,
        "duplicates": root_reports - len(hits),
        "false_blockers": blockers,
        "pending_findings": pending,
        "followups": followups,
        "evidence": [
            {"id": key, "role": role, "revision": rev, "sha256": checksum}
            for key, role, rev, checksum, _ in review.evidence
        ],
    }
