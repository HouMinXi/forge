# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
import hashlib
import io
import json
import stat
import unittest
import zipfile

import verify_receipt as v

# Both fixture identities are synthetic. The historical archive is tested
# manually from its retained original bytes, not included in the repository.
IDENTITY_A = {
    "workflow_sha": "a" * 40,
    "subject_sha": "b" * 40,
    "run_id": "999000001",
    "run_attempt": "1",
}
IDENTITY_B = {
    "workflow_sha": "c" * 40,
    "subject_sha": "d" * 40,
    "run_id": "999000002",
    "run_attempt": "2",
}


def fixture_summary(identity):
    """Literal expected facts from the fixed grouping profile, not verifier output."""
    source = "ae6b4d1374e98b976e1b68e580adb4d34db0d17daa102377263a633e83ed756e"
    variant = "3a4dad81c3f6948a006674bf769231b74545c2372e21efa5a2f29db5804173a0"
    return {
        "schema": 1,
        "case": "grouping",
        **identity,
        "node": "tests/test_e2e_check.py::TestGroupSourceFiles::test_two_top_dirs_produce_two_groups",
        "test_sha256": "302edd36eb8e2160071f328103679a73c1adff1aef6d0720983a52493d0e78dd",
        "stages": [
            {
                "stage": stage,
                "exit": rc,
                "source_sha256": digest,
                "outcomes": ["passed", call, "passed"],
                "line": 151,
                "observation_phase": "call",
                "observations": keys,
                "oracle": oracle,
            }
            for stage, rc, digest, call, keys, oracle in (
                ("baseline", 0, source, "passed", ["a", "b"], "pass"),
                ("variant", 1, variant, "failed", ["one_group", "one_group"], "expected_assertion"),
                ("restored", 0, source, "passed", ["a", "b"], "pass"),
            )
        ],
    }


def fixture_expectation(identity, archive):
    return {
        "schema": 1,
        "profile": "forge-r3-grouping-v1",
        "identity": identity,
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
    }


def encode(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def pack(payload, filename=v.MEMBER, mode=stat.S_IFREG, extra=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        entry = zipfile.ZipInfo(filename)
        entry.create_system = 3
        entry.external_attr = (mode | 0o600) << 16
        archive.writestr(entry, payload, compress_type=zipfile.ZIP_DEFLATED)
        if extra:
            archive.writestr("extra.txt", b"not permitted")
    return buf.getvalue()


def reanchor(sidecar, archive):
    side = deepcopy(sidecar)
    # Negative controls deliberately pass transport digest matching without
    # changing the independent expected workflow/subject/run/attempt.
    side["archive_sha256"] = hashlib.sha256(archive).hexdigest()
    return encode(side)


class ReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a_value = fixture_summary(IDENTITY_A)
        cls.a_payload = encode(cls.a_value)
        cls.a_archive = pack(cls.a_payload)
        cls.a_side = fixture_expectation(IDENTITY_A, cls.a_archive)
        cls.a_side_bytes = encode(cls.a_side)
        cls.b_archive = pack(encode(fixture_summary(IDENTITY_B)))
        cls.b_side = fixture_expectation(IDENTITY_B, cls.b_archive)
        cls.b_side_bytes = encode(cls.b_side)

    def check_change(self, field, value, *, stage=None, reason="closed summary"):
        data = deepcopy(self.a_value)
        target = data if stage is None else data["stages"][stage]
        target[field] = value
        archive = pack(encode(data))
        with self.assertRaisesRegex(v.Reject, reason):
            v.verify(archive, reanchor(self.a_side, archive))

    def test_first_synthetic_identity(self):
        self.assertEqual(v.verify(self.a_archive, self.a_side_bytes), self.a_value)

    def test_alternate_synthetic_identity(self):
        value = v.verify(self.b_archive, self.b_side_bytes)
        self.assertEqual(value["run_id"], "999000002")
        self.assertNotEqual(value["run_id"], self.a_value["run_id"])

    def test_cross_pair_digest_both_directions(self):
        for archive, side in ((self.a_archive, self.b_side_bytes), (self.b_archive, self.a_side_bytes)):
            with self.subTest(), self.assertRaisesRegex(v.Reject, "archive SHA-256"):
                v.verify(archive, side)

    def test_cross_pair_identity_even_with_matching_digest(self):
        for archive, side in ((self.a_archive, self.b_side), (self.b_archive, self.a_side)):
            with self.subTest(), self.assertRaisesRegex(v.Reject, "summary identity mismatch"):
                v.verify(archive, reanchor(side, archive))

    def test_stale_run_matching_digest(self):
        self.check_change("run_id", "999000000", reason="identity mismatch: run_id$")

    def test_wrong_attempt_matching_digest(self):
        self.check_change("run_attempt", "2", reason="identity mismatch: run_attempt$")

    def test_wrong_workflow_or_subject(self):
        for field in ("workflow_sha", "subject_sha"):
            with self.subTest(field=field):
                self.check_change(field, "e" * 40, reason=f"identity mismatch: {field}$")

    def test_wrong_node_or_source_or_test(self):
        self.check_change("node", "other")
        self.check_change("source_sha256", "e" * 64, stage=1)
        self.check_change("test_sha256", "f" * 64)

    def test_wrong_phase(self):
        for phase in ("setup", "teardown", "collection"):
            with self.subTest(phase=phase):
                self.check_change("observation_phase", phase, stage=1)

    def test_wrong_assertion_or_type(self):
        self.check_change("oracle", "another_assertion", stage=1)
        self.check_change("exit", 1.0, stage=1)
        self.check_change("schema", True)

    def test_extra_fields(self):
        self.check_change("unexpected", True)
        self.check_change("arbitrary_extra", None, stage=2)

    def test_duplicate_summary(self):
        payload = self.a_payload.replace(b'"schema": 1', b'"schema": 1, "schema": 1')
        archive = pack(payload)
        with self.assertRaisesRegex(v.Reject, "duplicate JSON key"):
            v.verify(archive, reanchor(self.a_side, archive))

    def test_bad_sidecar_shapes(self):
        for key, value in (
            ("unexpected", True),
            ("profile", "unknown"),
            ("schema", True),
            ("archive_sha256", "A" * 64),
        ):
            side = deepcopy(self.a_side)
            side[key] = value
            with self.subTest(field=key), self.assertRaisesRegex(v.Reject, "expect"):
                v.verify(self.a_archive, encode(side))

    def test_invalid_sidecar_identity_values(self):
        for key, value in (
            ("run_id", "01"),
            ("run_attempt", 1),
            ("workflow_sha", "f" * 39),
            ("subject_sha", "0" * 40),
            ("extra", "1"),
        ):
            side = deepcopy(self.a_side)
            side["identity"][key] = value
            with (
                self.subTest(field=key),
                self.assertRaisesRegex(v.Reject, "invalid expectation identity"),
            ):
                v.verify(self.a_archive, encode(side))

    def test_duplicate_sidecar_keys_top_and_nested(self):
        for name, val in (("schema", b"1"), ("run_attempt", b'"1"')):
            key = ('"' + name + '": ').encode() + val
            raw = self.a_side_bytes.replace(key, key + b", " + key)
            with self.subTest(field=name), self.assertRaisesRegex(v.Reject, "duplicate JSON key"):
                v.verify(self.a_archive, raw)

    def test_zip_member_shape(self):
        for archive in (
            pack(self.a_payload, extra=True),
            pack(self.a_payload, filename="other"),
            pack(b"other", mode=stat.S_IFLNK),
            pack(b"x" * (v.SUMMARY_LIMIT + 1)),
        ):
            with self.subTest(), self.assertRaisesRegex(v.Reject, "archive"):
                v.verify(archive, reanchor(self.a_side, archive))

    def test_bounded_inputs_and_corrupt_archive(self):
        with self.assertRaisesRegex(v.Reject, "expectation size"):
            v.verify(self.a_archive, b"x" * (v.SIDECAR_LIMIT + 1))
        blob = b"x" * (v.ZIP_LIMIT + 1)
        with self.assertRaisesRegex(v.Reject, "archive size"):
            v.verify(blob, reanchor(self.a_side, blob))
        blob = b"not a zip"
        with self.assertRaisesRegex(v.Reject, "invalid ZIP"):
            v.verify(blob, reanchor(self.a_side, blob))


if __name__ == "__main__":
    unittest.main()
