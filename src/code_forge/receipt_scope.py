"""Pinned repository namespaces for cross-repository review evidence."""
import re
from pathlib import PurePosixPath

from .source import compute_source_hash


def repository_scope(repositories: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Build qualified diffs from trusted orchestrator input, never receipts."""
    if not isinstance(repositories, dict) or not repositories:
        raise ValueError('INFRA: reviewed repositories must be a nonempty mapping')
    manifest = {}
    blocks = []
    for label, diff in repositories.items():
        if (not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', label)
                or not isinstance(diff, str)):
            raise ValueError('INFRA: unsupported repository identity')
        version = compute_source_hash(git_diff=diff)
        manifest[label] = version
        prefix = '%s@%s/' % (label, version)
        lines = []
        for line in diff.splitlines(keepends=True):
            # Git quoted paths need a real decoder, not partial unquoting.
            if line.startswith(('diff --git "', '+++ "', '--- "')):
                raise ValueError('INFRA: quoted repository diff paths are unsupported')
            if line.startswith(('+++ b/', '--- a/')):
                path = line[6:].rstrip('\r\n')
                if (not path or PurePosixPath(path).is_absolute()
                        or any(p in ('', '.', '..') for p in path.split('/'))
                        or '\\' in path or '@' in path):
                    raise ValueError('INFRA: ambiguous repository file path')
                line = line[:6] + prefix + line[6:]
            elif line.startswith('diff --git '):
                match = re.fullmatch(r'diff --git a/(.+) b/(.+)(\n?)', line)
                if not match:
                    raise ValueError('INFRA: unsupported repository diff header')
                line = 'diff --git a/%s%s b/%s%s%s' % (
                    prefix, match[1], prefix, match[2], match[3])
            lines.append(line)
        blocks.append(''.join(lines))
    return '\n'.join(blocks), manifest


def validate_scoped_paths(data: dict, repositories: dict[str, str]) -> None:
    """Reject model identities outside the exact reviewed file allowlist."""
    from .verify import parse_diff_files

    diff, _ = repository_scope(repositories)
    allowed = parse_diff_files(diff)
    for item in data.get('code_excerpts', []) + data.get('findings', []):
        if item.get('file') not in allowed:
            raise ValueError('INFRA: unknown repository, file, or reviewed source identity')
