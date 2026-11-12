"""Synthetic launch contrasts; never execute namespaces or target code."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


control = module("cli_capture_launch_contrast")
publication = module("cli_capture_publication")


def owned():
    return dict(cleanup_complete=True, owned=[dict(remaining=False)])


def completed(code=0, stdout=control.MARKER, stderr=b""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr, ownership=owned())


@pytest.mark.parametrize("code", [0, 1, 7, 73, -15, 255])
def test_preserve_codes_and_exact_output(code, tmp_path):
    result = control.arm(lambda *a, **k: completed(code), ["fixed"], tmp_path, {})
    assert result == dict(
        execution="COMPLETED", returncode=code, stdout="EXACT_MARKER", stderr="EMPTY", cleanup="COMPLETE"
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b"", "EMPTY"),
        (b"secret/path", "OTHER"),
        (control.MARKER + b"\n", "OTHER"),
        (control.MARKER + b"secret", "OTHER"),
    ],
)
def test_no_raw_output(raw, expected, tmp_path):
    result = control.arm(lambda *a, **k: completed(stdout=raw, stderr=b"secret/path"), [], tmp_path, {})
    assert result["stdout"] == expected
    assert result["stderr"] == "PRESENT"
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        dict(cleanup_complete=1, owned=[]),
        dict(cleanup_complete=True, owned=[dict(remaining=0)]),
    ],
)
def test_cleanup_strict(value):
    assert control.cleanup(value) == "UNKNOWN"


@pytest.mark.parametrize(
    "error,expected",
    [
        (subprocess.TimeoutExpired(["fixed"], 5), "TIMED_OUT"),
        (KeyboardInterrupt(), "INTERRUPTED"),
        (PermissionError("secret"), "OWNER_ERROR"),
    ],
)
def test_owner_errors(error, expected, tmp_path):
    error.ownership = owned()

    def owner(*args, **kwargs):
        raise error

    result = control.arm(owner, [], tmp_path, {})
    assert result == control.empty_arm() | dict(execution=expected, cleanup="COMPLETE")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    binding = dict(
        schema=1,
        controller={"fixed": "python"},
        unshare={"path": "/bound/unshare", "sha256": "hash"},
        installed_provenance={"owner": "hash"},
    )
    calls = []

    def owner(argv, **kwargs):
        calls.append((argv, kwargs))
        return completed(0 if len(calls) == 1 else 1, stderr=b"private")

    def read(path):
        if path.name == "result.json":
            return dict(schema=1, mode=path.parent.name, ownership=owned())
        return copy.deepcopy(binding)

    def require(value, message):
        if not value:
            raise ValueError(message)

    capture = SimpleNamespace(
        new_private_directory=lambda p: p,
        sanitize_controller=lambda p: None,
        apply_limits=lambda: None,
        checked_directory=lambda *a, **k: None,
        read_json=read,
        require=require,
        verify_installation=lambda p, **k: (binding["installed_provenance"], {}, tmp_path),
        interpreter_identity=lambda p: binding["controller"],
        utility=lambda n: binding["unshare"],
        load_owner=lambda *a: owner,
        safe_environment=lambda p: {"PATH": "/bin"},
    )
    monkeypatch.setattr(control.resource, "setrlimit", lambda *a: None)
    return capture, binding, calls, tmp_path


def run(fake):
    capture, binding, calls, root = fake
    return control.contrast(
        capture,
        root / "installed",
        root / "baseline/binding.json",
        root / "diagnostic/binding.json",
        root / "control",
    )


def test_bound_commands_are_exact_contrast(fake):
    result = run(fake)
    capture, binding, calls, root = fake
    direct, namespaced = [c[0] for c in calls]
    assert direct == [control.sys.executable, "-I", "-B", "-c", control.PROGRAM]
    assert namespaced == ["/bound/unshare", "--user", "--map-root-user", "--net", "--", *direct]
    assert (
        calls[0][1]
        == calls[1][1]
        == dict(timeout=5, cwd=str(root / "control"), env={"PATH": "/bin"}, memory_limit_bytes=1024**3)
    )
    assert result["direct"]["returncode"] == 0 and result["namespaced"]["returncode"] == 1
    assert publication.validate_contrast(result) == result


@pytest.mark.parametrize(
    "bad",
    [
        None,
        dict(cleanup_complete=False, owned=[]),
        dict(cleanup_complete=True, owned=[dict(remaining=True)]),
    ],
)
def test_old_target_cleanup_required(fake, bad):
    capture, binding, calls, root = fake
    original = capture.read_json
    capture.read_json = lambda p: (
        original(p) | {"ownership": bad} if p.name == "result.json" else original(p)
    )
    result = run(fake)
    assert result["binding"] == "INVALID" and not calls


def test_changed_binding_no_launch(fake):
    fake[0].interpreter_identity = lambda p: {"changed": True}
    assert run(fake)["binding"] == "CHANGED"
    assert not fake[2]


@pytest.mark.parametrize("state", ["INCOMPLETE", "UNKNOWN", "INTERRUPTED"])
def test_no_second_arm_after_unresolved_owner(fake, monkeypatch, state):
    observations = []

    def arm(*args):
        observations.append(args)
        return control.empty_arm() | dict(
            execution="INTERRUPTED" if state == "INTERRUPTED" else "OWNER_ERROR",
            cleanup="COMPLETE" if state == "INTERRUPTED" else state,
        )

    monkeypatch.setattr(control, "arm", arm)
    result = run(fake)
    assert len(observations) == 1 and result["namespaced"] == control.empty_arm()


@pytest.mark.parametrize(
    "mutation",
    ["extra", "bool_code", "raw", "causal", "cleanup", "unbound", "second", "missing", "type"],
)
def test_public_contrast_rejects_contradictions(fake, mutation):
    value = run(fake)
    if mutation == "extra":
        value["secret"] = "canary"
    if mutation == "bool_code":
        value["direct"]["returncode"] = True
    if mutation == "raw":
        value["direct"]["stderr"] = "secret"
    if mutation == "causal":
        value["causal_attribution"] = "UNSHARE_CAUSE"
    if mutation == "cleanup":
        value["direct"]["cleanup"] = "NOT_ASSESSED"
    if mutation == "unbound":
        value["binding"] = "CHANGED"
    if mutation == "second":
        value["direct"]["cleanup"] = "UNKNOWN"
    if mutation == "missing":
        del value["direct"]["stdout"]
    if mutation == "type":
        value["direct"]["execution"] = []
    with pytest.raises(ValueError):
        publication.validate_contrast(value)


def test_capability_v2_publication_pipeline(fake):
    root = fake[3] / "public"
    root.mkdir(mode=0o700)
    value = dict(
        schema="forge-cli-capture-capability-v2",
        status="PRESENT_NOT_QUALIFIED",
        facts=dict.fromkeys(publication.FACTS, True),
        live_admission="NOT_RUN",
        qualification="NOT_ASSESSED",
        raw_evidence="NOT_PUBLISHED",
        launch_contrast=run(fake),
    )
    source = root / "capability.json"
    source.write_text(json.dumps(value) + "\n")
    source.chmod(0o600)
    publication.publish(root, root / "published")
    raw = (root / "published/capability.json").read_text()
    assert json.loads(raw) == value
    assert "private" not in raw and "/bound" not in raw


def test_workflow_opt_in_post_capture_and_no_status_replacement():
    source = (ROOT / ".github/workflows/cli-capture-diagnostic.yml").read_text()
    section = source.split("      run_launch_contrast:")[1].split("      expected_source_sha:")[0]
    assert "default: false" in section
    step = source.split("      - name: Observe independent inert launch contrast after captures")[
        1
    ].split("      - name: Validate fixed public envelopes")[0]
    assert "inputs.run_launch_contrast" in step and "continue-on-error: true" in step
    assert source.index("      - name: Capture preenabled diagnostic once") < source.index(
        "      - name: Observe independent inert launch contrast after captures"
    )
    gate = source.split("      - name: Preserve original execution failures")[1]
    assert "contrast" not in gate


@pytest.mark.parametrize(
    "field,value",
    [
        ("returncode", True),
        ("returncode", 256),
        ("stdout", "secret"),
        ("stdout", b"x" * (control.LIMIT + 1)),
        ("stderr", b"x" * (control.LIMIT + 1)),
    ],
)
def test_owner_result_bounds(field, value, tmp_path):
    done = completed()
    setattr(done, field, value)
    result = control.arm(lambda *a, **k: done, [], tmp_path, {})
    assert result["execution"] == "OWNER_ERROR" and result["returncode"] is None
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "error,binding", [(FileNotFoundError("secret"), "UNAVAILABLE"), (ValueError("secret"), "INVALID")]
)
def test_precondition_failure_never_launches(fake, error, binding):
    def fail(*a, **k):
        raise error

    fake[0].verify_installation = fail
    assert run(fake)["binding"] == binding and not fake[2]


def test_diagnostic_binding_disagreement_never_launches(fake):
    capture, binding, calls, root = fake
    path = root / "diagnostic/binding.json"
    path.parent.mkdir()
    path.touch()
    original = capture.read_json
    capture.read_json = lambda p: (
        original(p) | {"controller": {"wrong": True}} if p == path else original(p)
    )
    assert run(fake)["binding"] == "INVALID" and not calls


def test_direct_fixed_program_real_python_without_target(tmp_path):
    done = subprocess.run(
        [control.sys.executable, "-I", "-B", "-c", control.PROGRAM],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert (done.returncode, done.stdout, done.stderr) == (0, control.MARKER, b"")


def test_opt_in_seed_survives_missing_control_delivery():
    source = (ROOT / ".github/workflows/cli-capture-diagnostic.yml").read_text()
    assert "if os.environ.get('REQUEST_LAUNCH_CONTRAST') == 'true':" in source
    assert "value.update(schema='forge-cli-capture-capability-v2', launch_contrast={" in source
    value = dict(
        schema="forge-cli-capture-capability-v2",
        status="PRESENT_NOT_QUALIFIED",
        facts=dict.fromkeys(publication.FACTS, True),
        live_admission="NOT_RUN",
        qualification="NOT_ASSESSED",
        raw_evidence="NOT_PUBLISHED",
        launch_contrast=control.empty(),
    )
    assert publication.validate(value, "capability.json") == value


def test_missing_diagnostic_binding_refuses_launch(fake):
    capture, binding, calls, root = fake
    original = capture.read_json

    def read(path):
        if path == root / "diagnostic/binding.json":
            raise FileNotFoundError("secret")
        return original(path)

    capture.read_json = read
    assert run(fake)["binding"] == "UNAVAILABLE" and not calls


@pytest.mark.parametrize("interrupt,expected_exit", [(False, 0), (True, 130)])
def test_cli_process_to_capability_publication(tmp_path, interrupt, expected_exit):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    public = tmp_path / "public"
    public.mkdir(mode=0o700)
    binding = dict(
        schema=1,
        controller={"fixed": "python"},
        unshare={"path": "/bound/unshare", "sha256": "hash"},
        installed_provenance={"owner": "hash"},
    )

    def save(path, value):
        path.write_text(json.dumps(value) + "\n")
        path.chmod(0o600)

    for mode in ("baseline", "diagnostic"):
        parent = private / mode
        parent.mkdir(mode=0o700)
        save(parent / "binding.json", binding)
        save(parent / "result.json", dict(schema=1, mode=mode, ownership=owned()))
    cap = dict(
        schema="forge-cli-capture-capability-v2",
        status="PRESENT_NOT_QUALIFIED",
        facts=dict.fromkeys(publication.FACTS, True),
        live_admission="NOT_RUN",
        qualification="NOT_ASSESSED",
        raw_evidence="NOT_PUBLISHED",
        launch_contrast=control.empty(),
    )
    save(public / "capability.json", cap)
    source = """
import importlib.util,sys
from pathlib import Path
from types import SimpleNamespace
path=Path(sys.argv[1]); private=Path(sys.argv[2]); public=Path(sys.argv[3]); interrupted=sys.argv[4]=='True'
spec=importlib.util.spec_from_file_location('control',path); control=importlib.util.module_from_spec(spec);spec.loader.exec_module(control)
capture=control.load_capture()
binding=capture.read_json(private/'baseline/binding.json')
capture.verify_installation=lambda p,**k:(binding['installed_provenance'],{},private)
capture.interpreter_identity=lambda p:binding['controller']
capture.utility=lambda n:binding['unshare']
def owner(argv,**kwargs):
    if interrupted:
        error=KeyboardInterrupt();error.ownership={'cleanup_complete':True,'owned':[]};raise error
    return SimpleNamespace(returncode=7,stdout=control.MARKER,stderr=b'secret-canary',ownership={'cleanup_complete':True,'owned':[]})
capture.load_owner=lambda *a:owner
control.load_capture=lambda:capture
sys.argv=['control','--evidence',str(private/'installed'),'--baseline',str(private/'baseline/binding.json'),'--diagnostic',str(private/'diagnostic/binding.json'),'--output',str(private/'contrast'),'--capability',str(public/'capability.json')]
raise SystemExit(control.main())
"""
    done = subprocess.run(
        [
            control.sys.executable,
            "-I",
            "-B",
            "-c",
            source,
            str(ROOT / "scripts/cli_capture_launch_contrast.py"),
            str(private),
            str(public),
            str(interrupt),
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert done.returncode == expected_exit, done.stderr
    publication.publish(public, public / "published")
    raw = (public / "published/capability.json").read_text()
    value = json.loads(raw)["launch_contrast"]
    assert value["direct"]["execution"] == ("INTERRUPTED" if interrupt else "COMPLETED")
    assert value["direct"]["returncode"] == (None if interrupt else 7)
    assert value["namespaced"]["execution"] == ("NOT_RUN" if interrupt else "COMPLETED")
    assert "secret-canary" not in raw and str(private) not in raw


@pytest.mark.parametrize("version", [True, 2, None])
def test_diagnostic_binding_schema_strict(fake, version):
    capture, binding, calls, root = fake
    original = capture.read_json
    capture.read_json = lambda p: (
        original(p) | {"schema": version} if p == root / "diagnostic/binding.json" else original(p)
    )
    assert run(fake)["binding"] == "INVALID" and not calls
