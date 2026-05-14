#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Shared file utilities -- atomic writes and JSON loaders."""

import json
import os
import tempfile


def atomic_write(filepath, data):
    """Atomically write JSON data to filepath.

    Uses tempfile.mkstemp + os.replace to avoid corruption on crash
    or concurrent access.
    """
    dir_name = os.path.dirname(filepath) or '.'
    os.makedirs(dir_name, exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, filepath)
        tmp = None
    except Exception:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def load_json_file(filepath, default_structure):
    """Load a JSON file with fallback to default structure.

    Returns default_structure if file is missing or corrupted.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(default_structure)
        return data
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return dict(default_structure)


def validate_diff_spec(spec):
    """Validate diff_spec is a safe git ref or range.

    Rejects specs starting with '--' (flag injection) or containing
    shell metacharacters.
    """
    if not spec:
        return spec
    if spec.startswith('-'):
        raise ValueError(
            "Invalid diff_spec: '%s' looks like a flag" % spec,
        )
    bad_chars = ('`', '$', ';', '|', '&', '>', '<')
    if any(c in spec for c in bad_chars):
        raise ValueError(
            "Invalid diff_spec: '%s' contains shell metacharacters" % spec,
        )
    return spec
