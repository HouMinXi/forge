# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <minxi@hou.email>
"""Unit tests for code_forge.manifest module (Phase 52: ENV-MANIFEST)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from code_forge.manifest import (
    EnvManifest,
    ManifestTier,
    _parse_cargo_lock,
    _parse_go_mod,
    _parse_package_lock_json,
    _parse_pipfile_lock,
    _parse_pnpm_lock_yaml,
    _parse_poetry_lock,
    _parse_requirements_txt,
    extract_manifest,
)


class TestManifestTier:
    def test_tier_values(self):
        assert ManifestTier.DECLARED.value == "declared"
        assert ManifestTier.OBSERVED.value == "observed"
        assert ManifestTier.ABSENT.value == "absent"


class TestEnvManifestDataclass:
    def test_to_dict(self):
        manifest = EnvManifest(
            tier=ManifestTier.DECLARED,
            runtime="python 3.14.0",
            runtime_name="python",
            runtime_version="3.14.0",
            runtime_bin="python3",
            manifest_path="poetry.lock",
            manifest_format="poetry",
            dependencies={"pytest": "8.0.0", "pyyaml": "6.0"},
            raw_summary="poetry.lock (2 deps)",
        )
        assert manifest.to_dict() == {
            "tier": "declared",
            "runtime": "python 3.14.0",
            "runtime_name": "python",
            "runtime_version": "3.14.0",
            "runtime_bin": "python3",
            "manifest_path": "poetry.lock",
            "manifest_format": "poetry",
            "dependencies": {"pytest": "8.0.0", "pyyaml": "6.0"},
            "raw_summary": "poetry.lock (2 deps)",
        }

    def test_to_prompt_block_absent(self):
        manifest = EnvManifest(tier=ManifestTier.ABSENT)
        block = manifest.to_prompt_block()
        assert "## Declared Environment" in block
        assert "Manifest Tier: absent" in block
        assert "No declared or observed environment lockfile found." in block

    def test_to_prompt_block_declared(self):
        manifest = EnvManifest(
            tier=ManifestTier.DECLARED,
            runtime="python 3.14.0",
            dependencies={"pytest": "8.0.0", "pyyaml": "6.0"},
        )
        block = manifest.to_prompt_block()
        assert "## Declared Environment" in block
        assert "Manifest Tier: declared" in block
        assert "Runtime: python 3.14.0" in block
        assert "Dependencies:" in block
        assert "- pytest: 8.0.0" in block
        assert "- pyyaml: 6.0" in block

    def test_to_prompt_block_without_runtime(self):
        manifest = EnvManifest(
            tier=ManifestTier.DECLARED,
            dependencies={"express": "4.18.2"},
        )
        block = manifest.to_prompt_block()
        assert "## Declared Environment" in block
        assert "Manifest Tier: declared" in block
        assert "Runtime:" not in block
        assert "- express: 4.18.2" in block

    def test_to_prompt_block_truncates_over_50_deps(self):
        deps = {f"pkg-{i:03d}": f"1.0.{i}" for i in range(75)}
        manifest = EnvManifest(
            tier=ManifestTier.DECLARED,
            dependencies=deps,
        )
        block = manifest.to_prompt_block()
        assert "- ... and 25 more dependencies" in block


class TestLockfileParsers:
    def test_parse_poetry_lock(self, tmp_path: Path):
        lock_file = tmp_path / "poetry.lock"
        lock_file.write_text(
            '[[package]]\nname = "pytest"\nversion = "8.1.1"\n\n'
            '[[package]]\nname = "ruff"\nversion = "0.5.0"\n',
            encoding="utf-8",
        )
        deps = _parse_poetry_lock(lock_file)
        assert deps == {"pytest": "8.1.1", "ruff": "0.5.0"}

    def test_parse_pipfile_lock(self, tmp_path: Path):
        lock_file = tmp_path / "Pipfile.lock"
        data = {
            "default": {"requests": {"version": "==2.31.0"}},
            "develop": {"pytest": {"version": "==8.0.0"}},
        }
        lock_file.write_text(json.dumps(data), encoding="utf-8")
        deps = _parse_pipfile_lock(lock_file)
        assert deps == {"requests": "2.31.0", "pytest": "8.0.0"}

    def test_parse_requirements_txt(self, tmp_path: Path):
        req_file = tmp_path / "requirements.txt"
        req_file.write_text(
            "# comment line\n-r base.txt\n\npytest>=8.0.0\npyyaml==6.0.1\nblack\n",
            encoding="utf-8",
        )
        deps = _parse_requirements_txt(req_file)
        assert deps["pytest"] == ">=8.0.0"
        assert deps["pyyaml"] == "==6.0.1"
        assert deps["black"] == "*"

    def test_parse_package_lock_json_v2(self, tmp_path: Path):
        lock_file = tmp_path / "package-lock.json"
        data = {
            "packages": {
                "": {"name": "root"},
                "node_modules/express": {"version": "4.18.2"},
                "node_modules/@types/node": {"version": "20.11.0"},
            }
        }
        lock_file.write_text(json.dumps(data), encoding="utf-8")
        deps = _parse_package_lock_json(lock_file)
        assert deps == {"express": "4.18.2", "@types/node": "20.11.0"}

    def test_parse_package_lock_json_v1(self, tmp_path: Path):
        lock_file = tmp_path / "package-lock.json"
        data = {
            "dependencies": {
                "lodash": {"version": "4.17.21"},
            }
        }
        lock_file.write_text(json.dumps(data), encoding="utf-8")
        deps = _parse_package_lock_json(lock_file)
        assert deps == {"lodash": "4.17.21"}

    def test_parse_pnpm_lock_yaml(self, tmp_path: Path):
        lock_file = tmp_path / "pnpm-lock.yaml"
        content = (
            "lockfileVersion: '6.0'\n\n"
            "packages:\n"
            "  /express@4.18.2:\n"
            "    resolution: {integrity: sha512-...}\n"
            "  /@types/node@20.11.0:\n"
            "    resolution: {integrity: sha512-...}\n"
        )
        lock_file.write_text(content, encoding="utf-8")
        deps = _parse_pnpm_lock_yaml(lock_file)
        assert deps == {"express": "4.18.2", "@types/node": "20.11.0"}

    def test_parse_cargo_lock(self, tmp_path: Path):
        lock_file = tmp_path / "Cargo.lock"
        content = (
            "version = 3\n\n"
            '[[package]]\nname = "serde"\nversion = "1.0.197"\n\n'
            '[[package]]\nname = "tokio"\nversion = "1.36.0"\n'
        )
        lock_file.write_text(content, encoding="utf-8")
        deps = _parse_cargo_lock(lock_file)
        assert deps == {"serde": "1.0.197", "tokio": "1.36.0"}

    def test_parse_go_mod(self, tmp_path: Path):
        mod_file = tmp_path / "go.mod"
        content = (
            "module example.com/foo\n\n"
            "go 1.22.0\n\n"
            "require (\n"
            "\tgithub.com/gin-gonic/gin v1.9.1\n"
            "\tgolang.org/x/crypto v0.18.0\n"
            ")\n"
        )
        mod_file.write_text(content, encoding="utf-8")
        runtime, deps = _parse_go_mod(mod_file)
        assert runtime == "go 1.22.0"
        assert deps == {
            "github.com/gin-gonic/gin": "v1.9.1",
            "golang.org/x/crypto": "v0.18.0",
        }


class TestExtractManifest:
    def test_extract_declared_poetry(self, tmp_path: Path):
        (tmp_path / "poetry.lock").write_text(
            '[[package]]\nname = "requests"\nversion = "2.31.0"\n',
            encoding="utf-8",
        )
        manifest = extract_manifest(tmp_path)
        assert manifest.tier == ManifestTier.DECLARED
        assert manifest.runtime_name == "python"
        assert manifest.manifest_path == "poetry.lock"
        assert manifest.manifest_format == "poetry"
        assert manifest.dependencies == {"requests": "2.31.0"}
        assert "poetry.lock" in manifest.raw_summary

    def test_extract_priority_poetry_over_requirements(self, tmp_path: Path):
        (tmp_path / "poetry.lock").write_text(
            '[[package]]\nname = "requests"\nversion = "2.31.0"\n',
            encoding="utf-8",
        )
        (tmp_path / "requirements.txt").write_text(
            "requests==2.30.0\n",
            encoding="utf-8",
        )
        manifest = extract_manifest(tmp_path)
        assert manifest.tier == ManifestTier.DECLARED
        assert manifest.dependencies == {"requests": "2.31.0"}

    def test_extract_corrupted_lockfile_graceful_degradation(self, tmp_path: Path):
        # Corrupted JSON in package-lock.json -> does not crash, falls back
        (tmp_path / "package-lock.json").write_text("{corrupted-json", encoding="utf-8")
        with patch(
            "code_forge.manifest._probe_toolchain",
            return_value=("node v20.11.0", "node", "20.11.0", "node", {}),
        ):
            manifest = extract_manifest(tmp_path)
            assert manifest.tier == ManifestTier.OBSERVED
            assert manifest.runtime_name == "node"

    def test_extract_observed_toolchain_fallback(self, tmp_path: Path):
        with patch(
            "code_forge.manifest._probe_toolchain",
            return_value=("python 3.14.0", "python", "3.14.0", "python3", {}),
        ):
            manifest = extract_manifest(tmp_path)
            assert manifest.tier == ManifestTier.OBSERVED
            assert manifest.runtime == "python 3.14.0"
            assert manifest.runtime_name == "python"
            assert manifest.runtime_version == "3.14.0"
            assert manifest.runtime_bin == "python3"
            assert "observed: python 3.14.0" in manifest.raw_summary

    def test_extract_absent_fallback(self, tmp_path: Path):
        with patch("code_forge.manifest._probe_toolchain", return_value=("", "", "", "", {})):
            manifest = extract_manifest(tmp_path)
            assert manifest.tier == ManifestTier.ABSENT
            assert manifest.runtime == ""
            assert manifest.dependencies == {}

    def test_probe_toolchain_uses_three_second_timeout(self, tmp_path: Path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Python 3.14.0\n"
            from code_forge.manifest import _probe_toolchain

            _probe_toolchain()
            assert mock_run.called
            for call in mock_run.call_args_list:
                assert call.kwargs.get("timeout") == 3.0
