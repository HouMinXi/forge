# SPDX-License-Identifier: Apache-2.0
"""A test run must not write the developer's real trust store.

``record_trust`` resolves its file from ``XDG_CONFIG_HOME`` at call time.
The suite-wide isolation has to point that variable at a scratch directory
before any test body runs, otherwise every test that records trust appends
to the trust store under the developer's real config directory.
"""

import json

from code_forge.trust import record_trust

from tests.conftest import _real_config_home


def test_recording_trust_leaves_the_real_store_untouched(tmp_path, request):
    """The gate this test records must not land in the real store.

    The real directory is the one captured before the suite redirected
    ``XDG_CONFIG_HOME``, so this checks the store the developer actually
    uses, not the scratch copy. When that file already exists, the key
    written here must be absent from it. When it does not, the call must
    not create it. Comparing the whole file would trip on an unrelated
    process writing its own entry at the same time.
    """
    real = request.node.stash[_real_config_home] / "code-forge" / "trusted.json"
    existed = real.exists()
    gate = tmp_path / "gate.yaml"
    gate.write_text(
        "backends: {x: {base_url: https://example.test}}\n", encoding="utf-8"
    )

    record_trust(
        gate,
        json.loads('{"backends": {"x": {"base_url": "https://example.test"}}}'),
    )

    if not existed:
        assert not real.exists()
        return
    stored = json.loads(real.read_text(encoding="utf-8"))
    assert str(gate) not in stored
