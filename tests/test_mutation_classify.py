"""Tests for the versioned in-tree path classifier."""

import hashlib

import pytest

from code_forge.mutation_engines.classify import (
    CLASSIFIER_VERSION,
    RULE_DOC_MARKDOWN,
    RULE_DOC_TEXT,
    RULE_IMAGE_RASTER,
    classify_path,
)

PLAIN = b"# Notes\n\nSome prose.\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SCRIPT = b"#!/bin/sh\necho hi\n"


def test_markdown_doc_is_exempt():
    c = classify_path("docs/guide.md", 0o644, PLAIN)
    assert c.category == "exempt"
    assert c.rule == RULE_DOC_MARKDOWN
    assert c.classifier_version == CLASSIFIER_VERSION


def test_plain_text_doc_is_exempt():
    c = classify_path("README.txt", 0o644, PLAIN)
    assert c.category == "exempt"
    assert c.rule == RULE_DOC_TEXT


@pytest.mark.parametrize("name", ["a.png", "b.jpg", "c.jpeg", "d.webp"])
def test_raster_images_are_exempt(name):
    c = classify_path("assets/" + name, 0o644, PNG_BYTES)
    assert c.category == "exempt"
    assert c.rule == RULE_IMAGE_RASTER


def test_executable_mode_defeats_exemption():
    c = classify_path("docs/run.md", 0o755, PLAIN)
    assert c.category == "uncovered"
    assert c.rule is None


def test_script_signature_defeats_exemption():
    c = classify_path("docs/setup.md", 0o644, SCRIPT)
    assert c.category == "uncovered"
    assert c.rule is None


def test_executable_bit_in_group_or_other_defeats_exemption():
    assert classify_path("d.md", 0o654, PLAIN).category == "uncovered"
    assert classify_path("d.md", 0o645, PLAIN).category == "uncovered"


@pytest.mark.parametrize(
    "name",
    ["config.yaml", "data.json", "Makefile", "drawing.svg", "page.html", "x.c"],
)
def test_uncertain_formats_are_uncovered(name):
    c = classify_path("tree/" + name, 0o644, PLAIN)
    assert c.category == "uncovered"
    assert c.rule is None


def test_extensionless_is_uncovered():
    assert classify_path("LICENSE", 0o644, PLAIN).category == "uncovered"


def test_uppercase_extension_matches():
    c = classify_path("assets/PHOTO.PNG", 0o644, PNG_BYTES)
    assert c.category == "exempt"
    assert c.rule == RULE_IMAGE_RASTER


def test_digest_is_sha256_of_raw_bytes():
    content = b"raw \x00 bytes \xff"
    c = classify_path("notes.md", 0o644, content)
    assert c.digest == hashlib.sha256(content).hexdigest()


def test_record_carries_path_and_mode():
    c = classify_path("a/b/readme.md", 0o100644, PLAIN)
    assert c.path == "a/b/readme.md"
    assert c.mode == 0o100644
    assert c.category == "exempt"


def test_empty_content_classifies():
    c = classify_path("empty.md", 0o644, b"")
    assert c.category == "exempt"
    assert c.digest == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize(
    "bad",
    ["/abs/x.md", "../x.md", "a/../x.md", "C:/x.md", "back\\slash.md", ""],
)
def test_invalid_paths_rejected(bad):
    with pytest.raises(ValueError):
        classify_path(bad, 0o644, PLAIN)
