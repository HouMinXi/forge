"""Versioned in-tree classifier for paths unmatched by any target.

Implements the specification's routing policy: only Markdown/plain-text
documentation and raster image assets are exempt, and only when they carry
no executable mode bit and no script signature.  Everything else, including
uncertain formats, is uncovered scope.  A classification record carries the
path, mode, raw digest, rule identifier and classifier version; it is a
routing policy, not proof that a document cannot contain executable
material.
"""

import hashlib
from dataclasses import dataclass

from code_forge.mutation_engines.schemas import _validate_relative_path

CLASSIFIER_VERSION = "1"

RULE_DOC_MARKDOWN = "doc-markdown"
RULE_DOC_TEXT = "doc-text"
RULE_IMAGE_RASTER = "image-raster"

CATEGORY_EXEMPT = "exempt"
CATEGORY_UNCOVERED = "uncovered"

_MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown"})
_TEXT_EXTENSIONS = frozenset({".txt", ".text"})
_RASTER_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})

_SCRIPT_SIGNATURE = b"#!"


@dataclass(frozen=True)
class Classification:
    """One classification record per unmatched path."""

    path: str
    mode: int
    digest: str
    rule: str | None
    classifier_version: str
    category: str  # "exempt" | "uncovered"


def _rule_for(path: str) -> str | None:
    """Return the allowlist rule identifier for *path*, or None.

    Extension comparison is case-insensitive: a raster image named
    PHOTO.PNG is still a raster image.  This is an allowlist lookup
    only; executable mode and script signatures are checked by the
    caller and always defeat the exemption.
    """
    dot = path.rfind(".")
    if dot <= 0:
        return None
    ext = path[dot:].lower()
    if ext in _MARKDOWN_EXTENSIONS:
        return RULE_DOC_MARKDOWN
    if ext in _TEXT_EXTENSIONS:
        return RULE_DOC_TEXT
    if ext in _RASTER_EXTENSIONS:
        return RULE_IMAGE_RASTER
    return None


def classify_path(path: str, mode: int, content: bytes) -> Classification:
    """Classify one path unmatched by any target declaration.

    *mode* is the filesystem mode (only the executable bits are
    consulted); *content* is the raw file bytes.  Raises ValueError on
    a path that is not a safe repository-relative POSIX path.
    """
    _validate_relative_path(path, "classify path")

    rule = _rule_for(path)
    if rule is not None:
        if mode & 0o111:
            rule = None
        elif content.startswith(_SCRIPT_SIGNATURE):
            rule = None

    return Classification(
        path=path,
        mode=mode,
        digest=hashlib.sha256(content).hexdigest(),
        rule=rule,
        classifier_version=CLASSIFIER_VERSION,
        category=CATEGORY_EXEMPT if rule is not None else CATEGORY_UNCOVERED,
    )
