#!/usr/bin/env python3
"""Build the J1 forge review workload from real diffs.

Reproduces the EXACT L1 pass prompt forge's pipeline sends to a
backend (template copied verbatim from code_forge factories.py, the
_provider prompt builder). Three passes x three real diff sizes.

Regenerate:
    python3 make_workload.py <small.diff> <medium.diff> <large.diff>
Outputs prompt files + manifest.tsv into the current directory.
"""
import sys
from pathlib import Path

PASS_CONFIGS = [
    ("qodo", "structural code reviewer: correctness and logic errors"),
    ("expert", "senior engineer: SOLID, architecture, security"),
    ("adversarial", "adversarial QE: assume bugs exist"),
]


def build_prompt(role: str, diff_text: str) -> str:
    # Verbatim from forge's pipeline prompt builder.
    prompt = (
        "You are a " + role + ". Review this diff.\n"
        'Return JSON: {"findings": [{"file": "...", "line": N, '
        '"severity": "P0"|"P1"|"P2"|"P3", '
        '"description": "..."}], '
        '"code_excerpts": [{"file": "...", "start_line": N, '
        '"end_line": M, "content": "..."}]}\n'
        "Each diff hunk MUST have at least one code_excerpt.\n"
        "Even if findings is empty, provide code_excerpts "
        "covering each changed hunk.\n"
        "code_excerpts content must be actual source code lines, "
        "not diff format -- no +/- prefixes, no @@ headers.\n"
    )
    prompt += "\nDiff:\n" + diff_text
    return prompt


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    sizes = ["small", "medium", "large"]
    rows = []
    for size, diff_path in zip(sizes, sys.argv[1:4]):
        diff_text = Path(diff_path).read_text(encoding="utf-8")
        for pass_name, role in PASS_CONFIGS:
            out = Path("prompt_%s_%s.txt" % (size, pass_name))
            content = build_prompt(role, diff_text)
            out.write_text(content, encoding="utf-8")
            n_bytes = len(content.encode("utf-8"))
            # ~4 bytes/token heuristic for code-heavy English text.
            rows.append((out.name, n_bytes, n_bytes // 4))
    with open("manifest.tsv", "w", encoding="utf-8") as f:
        f.write("file\tbytes\test_tokens\n")
        for name, b, t in rows:
            f.write("%s\t%d\t%d\n" % (name, b, t))
    for name, b, t in rows:
        print("%-32s %8d bytes  ~%6d tokens" % (name, b, t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
