"""Single-use desktop runner for the #537 provider smoke; reports only aggregate metadata."""

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import chdir, redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

from code_forge import cli, factories
from code_forge.backend import credential_error, load_backend_configs
from code_forge.lenses import PASS_LENSES
from code_forge.trust import record_trust
from code_forge.user_config import load_user_backends, user_config_path

from transport_cap import capped_api

PROFILE = "agnes-cn"
WIRE_CONTRACT = (PROFILE, 131_072)
OVERLAY_HASHES = (
    (cli, "a50839f4219eaf21b3fac41d4ac249d12e28a9084e58722b57f72c89f0fcaee6"),
    (factories, "917e72a5a34b2690a731da794f55b45ee2b7276d8634ecaa031a219bd14db109"),
)


def overlay_present():
    return all(
        hashlib.sha256(Path(mod.__file__).read_bytes()).hexdigest() == digest
        for mod, digest in OVERLAY_HASHES
    )


def configured_profile():
    path = user_config_path()
    if path is None or path.name != "config.yaml":
        return None, None
    profile_config = load_user_backends().get(PROFILE)
    if profile_config is None:
        return None, None
    (backend,) = load_backend_configs({"backends": {PROFILE: profile_config}})
    return path, backend


def setup_repo(repo):
    def git(*args):
        subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            timeout=20,
        )

    repo.mkdir()
    # The fixture has no remote, no user files, and no ambient git configuration.
    git("init", "--template=")
    git("config", "user.email", "forge@example.invalid")
    git("config", "user.name", "Forge fixture")
    (repo / "f.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    git("add", "f.py")
    git("commit", "-m", "synthetic base")
    (repo / "f.py").write_text("def f(x):\n    y = x + 1\n    return y\n", encoding="utf-8")
    git("add", "f.py")
    git("commit", "-m", "synthetic candidate")


def setup_fixture_options(repo):
    gate = repo / ".code-forge" / "gate.yaml"
    gate.parent.mkdir()
    # This trust entry is for the generated fixture only and exists exclusively
    # in a throwaway XDG config directory owned by this process.
    gate.write_text("review_lenses: true\ntest: 'true'\n", encoding="utf-8")
    (gate.parent / "tools.yaml").write_text(
        "tools:\n  ruff:\n    command: ruff check --output-format=sarif\n"
        "    output_format: sarif\n    file_patterns: ['*.py']\n",
        encoding="utf-8",
    )
    record_trust(gate, {"review_lenses": True, "test": "true"})


def run_fixture(path, backend, cap):
    arms = {}
    with TemporaryDirectory(prefix="forge537-smoke-") as temp:
        scratch = Path(temp)
        repo = scratch / "fixture"
        experiment_keys = {
            "XDG_CONFIG_HOME": str(scratch / "xdg"),
            "FORGE_CONFIG_DIR": str(path.parent),
            "FORGE_L1_SERIAL": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        # Reject Git overrides that can address an existing user checkout.
        if any(
            k in os.environ
            for k in (
                "GIT_DIR",
                "GIT_COMMON_DIR",
                "GIT_WORK_TREE",
                "GIT_INDEX_FILE",
                "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_CONFIG_COUNT",
                "GIT_CONFIG_PARAMETERS",
            )
        ):
            raise RuntimeError("ambient Git overrides not supported")
        with patch.dict(os.environ, experiment_keys):
            setup_repo(repo)
            with chdir(repo):
                for arm in ("A", "B"):
                    setup_fixture_options(repo)
                    t0 = monotonic()
                    blocked_before = cap.blocked
                    # Raw stderr/results are not bridged to Codex, including
                    # any services' error echoes or arbitrary model prose.
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with (
                        redirect_stdout(stdout),
                        redirect_stderr(stderr),
                        patch.dict(os.environ, {"FORGE_EXPERIMENT_537_ARM": arm}),
                        patch.object(
                            sys,
                            "argv",
                            [
                                "code-forge",
                                "review",
                                "--allow-main",
                                "--mode",
                                "ci",
                                "--outlet",
                                "subprocess",
                                "--backend",
                                backend.name,
                                "--committed",
                            ],
                        ),
                    ):
                        returncode = cli.main()
                    completions = {name: cap.usage.get((arm, name), None) for name in PASS_LENSES}
                    arms[arm] = {
                        "elapsed_seconds": round(monotonic() - t0, 3),
                        "cli_exit_code": returncode,
                        "blocked_sends": cap.blocked - blocked_before,
                        "validated_passes": [
                            name for name in PASS_LENSES if (arm, name) in cap.validated
                        ],
                        "pass_usage": completions,
                    }
                    if not all((arm, name) in cap.validated for name in PASS_LENSES):
                        break
                    # Explicitize separation of the CI state across arms.
                    (repo / ".code-forge").rename(scratch / ("private-" + arm))
    return arms


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-base-url",
        required=True,
        help="Exact approved HTTPS /v1 base URL, also present in the selected config",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Send up to 6 L1 model requests through the selected profile",
    )
    args = parser.parse_args(argv)
    report = {"profile": PROFILE, "status": "not-run", "real_sends_admitted": 0}
    cap = None
    try:
        # Validation functions may print config paths and provider details.
        # Only predefined status codes leave the process.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path, backend = configured_profile()
            if not overlay_present():
                report["status"] = "overlay-not-loaded"
            elif backend is None:
                report["status"] = "profile-unavailable"
            elif (backend.type, backend.format) != ("api", "openai"):
                report["status"] = "profile-transport-unsupported"
            elif backend.base_url != args.expected_base_url or backend.model != PROFILE:
                report["status"] = "profile-route-or-model-mismatch"
            elif (
                backend.output_ceiling or backend.max_completion_tokens or backend.max_tokens
            ) != WIRE_CONTRACT[1]:
                report["status"] = "profile-output-cap-mismatch"
            elif credential_error(backend, os.environ) is not None:
                report["status"] = "credential-unavailable-in-process"
            elif not shutil.which("git") or not shutil.which("ruff"):
                report["status"] = "fixture-tool-unavailable"
            else:
                with capped_api(
                    backend,
                    match_system_proxy=True,
                    required_base_url=args.expected_base_url,
                    wire_contract=WIRE_CONTRACT,
                ) as cap:
                    if args.execute:
                        report["arms"] = run_fixture(path, backend, cap)
                        expected_slots = {(arm, name) for arm in ("A", "B") for name in PASS_LENSES}
                        report["status"] = (
                            "l1-only-smoke"
                            if cap.used == cap.validated == expected_slots
                            else "l1-smoke-incomplete"
                        )
                    else:
                        report["status"] = "ready-no-sends"
    except KeyboardInterrupt:
        report["status"] = "canceled"
    except Exception:  # noqa: BLE001 - report only a fixed status at the CLI boundary.
        report["status"] = "experiment-stopped"
    finally:
        if cap is not None:
            report["real_sends_admitted"] = len(cap.used)
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] in ("ready-no-sends", "l1-only-smoke") else 1


if __name__ == "__main__":
    sys.exit(main())
