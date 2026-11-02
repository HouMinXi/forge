# Partial installed-wheel runtime controls (#335)

These twelve controls execute outside the checkout, in a fresh venv installed from
an actual wheel. The standalone config deliberately does not inherit the source
suite's `pythonpath = ["src"]` or `tests/conftest.py`. Ordinary source pytest does
not collect this directory. The harness freezes the case roster and requires no
skips/errors/failures; `build-proof.json` binds commit/archive, artifact, dependency
wheels and portable controls, while `runtime-proof.json` records imported module
origins and RECORD hashes plus the admitted local process calls, including independent console-child proofs.

Run after provisioning an official dependency wheelhouse (setup may use network):

```sh
python -m pip wheel --index-url https://pypi.org/simple --wheel-dir /tmp/forge-wheels \
  'setuptools>=77' wheel 'pytest==9.1.1' 'pyyaml>=6.0' 'unidiff>=0.7.5,<0.8.0'
python scripts/qualify_installed_wheel.py --wheelhouse /tmp/forge-wheels \
  --output /tmp/forge-wheel-evidence
```

The output directory must be new and outside the checkout. Build and install are
`--no-index`, with no editable or system-site package installation. Qualification
runs isolated Python, disables unrelated pytest plugin discovery, scrubs caller
Python/Git/provider routing and uses private HOME/XDG locations. Python audit
tripwires admit only fixed read-only Git discovery commands in synthetic fixture
repositories an exact isolated interpreter `python3 --version` metadata probe, and exact local
console help/ledger-list child commands. Console
children install the same tripwires before loading the real installed entry script. This does not qualify OS shebang/executable-bit
launch behavior.
No git hooks, network Git operations, external providers, Semgrep or mutation
engine commands are admitted. These are regression tripwires, not an OS sandbox
against adversarial native code.

The real CI StateMachine rejects malformed persisted mutation results and writes
synthetic L0 confirmed findings to the real ledger. Acquisition is synthetic;
consumer, persistence and source/environment guards are not replaced. Console
checks use only read-only `--help` and `ledger list --json`. There is no CLI review
or full toolchain run, no gate opt-out, and no native-engine claim.

This is **partial packaging/runtime coverage**, not #335 closure or completion of
its installed-wheel checkbox. Exact mutation ruling, revocation, changed native
source, current native survivor, ABA, phase/reload, branch coverage and native
filesystem qualification remain open. Unsupported native filesystems are not
converted into skips or synthetic green results by these controls.
