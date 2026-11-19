# SPDX-License-Identifier: Apache-2.0

"""Supplemental synthetic contract checks; no review, backend, or network access.

These tests consume frozen documentation inputs and call only the offline planner.
They intentionally do not use the planner's serializer for expected bytes.
"""

import base64
import copy
import hashlib
import json
import unittest

from code_forge.offline_planner.planner import plan
from tests.offline_planner_replay import inventory, replay


def canonical(value):
    if value is None:
        return b"null"
    if type(value) is bool:
        return b"true" if value else b"false"
    if type(value) is int:
        return str(value).encode("ascii")
    if type(value) is str:
        escaped = "".join(
            "\\u%04x" % ord(ch) if ord(ch) < 32 else "\\" + ch if ch in '\\"' else ch for ch in value
        )
        return ('"' + escaped + '"').encode("utf-8")
    if type(value) is list:
        return b"[" + b",".join(map(canonical, value)) + b"]"
    if type(value) is dict:
        return b"{" + b",".join(canonical(k) + b":" + canonical(value[k]) for k in sorted(value)) + b"}"
    raise TypeError(type(value))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def error(code, path):
    return canonical(
        dict(serialization_version="plan-json-v1", schema_version=1, error_code=code, field_path=path)
    )


class SupplementalPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = inventory()

    def test_frozen_replay_exact_bytes(self):
        self.assertEqual(replay(), 0)

    def setUp(self):
        case = "vectors/mixed/"
        self.request = json.loads(self.files[case + "input.json"])
        self.bundle = {
            k: base64.b64decode(v, validate=True)
            for k, v in json.loads(self.files[case + "artifacts.json"]).items()
        }

    def run_request(self):
        return plan(canonical(self.request), self.bundle.get)

    def forbid_reads(self, raw, expected):
        reads = []

        def read(digest):
            reads.append(digest)
            raise AssertionError("artifact access before phase 4")

        self.assertEqual(plan(raw, read), expected)
        self.assertEqual(reads, [])

    def rebind_manifest(self):
        self.request["candidate"]["context_manifest_sha256"] = sha(
            canonical(self.request["context_manifest"])
        )

    def test_lexical_failures_never_read_artifacts(self):
        for raw in (
            b"\xef\xbb\xbf{}",
            b'{"x":1,"\\u0078":2}',
            b'"\\ud800"',
            b'"\xff"',
            b'{"x":-0.0e999999}',
        ):
            with self.subTest(raw=raw):
                self.forbid_reads(raw, error("invalid-json", ""))

    def test_phase2_eligible_composite_precedes_later_scalar_without_reads(self):
        self.request["context_manifest"][0]["range_start"] = 2
        self.request["recognition"][1]["state"] = "not-a-state"
        self.forbid_reads(
            canonical(self.request), error("invalid-schema", "/context_manifest/0/range_end")
        )

    def test_phase2_invalid_operand_suppresses_only_own_composite(self):
        evidence = self.request["context_manifest"][0]
        evidence.update(range_start="bad", range_end=None)
        self.request["recognition"][1]["domain"] = None
        self.forbid_reads(
            canonical(self.request), error("invalid-schema", "/context_manifest/0/range_start")
        )

    def test_frozen_phase3_failures_never_read_artifacts(self):
        cases = (
            "packet-and-missing-snapshot",
            "duplicate-recognition",
            "missing-recognition",
            "p3-l1-qodo-to-expert",
            "p3-swap-complete-valid-tuples",
            "p3-required-reversed",
            "p3-required-later-absent",
            "p3-packet-before-owner",
        )
        for name in cases:
            with self.subTest(case=name):
                case = f"vectors/{name}/"
                self.forbid_reads(self.files[case + "input.json"], self.files[case + "expected.json"])

    def test_missing_policy_wins_over_hash_valid_malformed_snapshot(self):
        malformed = canonical({"not_snapshot": True})
        digest = sha(malformed)
        self.request["candidate"]["snapshot_sha256"] = digest
        self.bundle[digest] = malformed
        self.bundle.pop(self.request["policy"]["sha256"])
        self.assertEqual(self.run_request(), error("artifact-mismatch", "/policy/sha256"))

    def test_earliest_phase4_digest_pointer_wins(self):
        self.request["candidate"]["context_manifest_sha256"] = "0" * 64
        self.bundle.pop(self.request["candidate"]["diff_sha256"])
        self.bundle.pop(self.request["candidate"]["snapshot_sha256"])
        self.assertEqual(
            self.run_request(), error("artifact-mismatch", "/candidate/context_manifest_sha256")
        )

    def test_shared_digest_is_validated_for_each_artifact_role(self):
        self.request["candidate"]["snapshot_sha256"] = self.request["policy"]["sha256"]
        self.assertEqual(self.run_request(), error("artifact-mismatch", "/candidate/snapshot_sha256"))

    def test_repeated_evidence_digest_keeps_per_occurrence_ranges(self):
        extra = copy.deepcopy(self.request["context_manifest"][0])
        extra.update(evidence_id="e2", range_end=2)
        self.request["context_manifest"].append(extra)
        self.rebind_manifest()
        self.assertEqual(
            self.run_request(), error("artifact-mismatch", "/context_manifest/1/content_sha256")
        )

    def test_multiple_bad_ranges_use_numeric_not_lexical_index_order(self):
        original = self.request["context_manifest"][0]
        for index in range(1, 12):
            extra = copy.deepcopy(original)
            extra.update(evidence_id="e%d" % (index + 1), range_end=2 if index in (2, 10) else 1)
            self.request["context_manifest"].append(extra)
        self.rebind_manifest()
        self.assertEqual(
            self.run_request(), error("artifact-mismatch", "/context_manifest/2/content_sha256")
        )

    def test_unreferenced_manifest_and_extras_never_read(self):
        expected = self.files["vectors/mixed/expected.json"]
        forbidden = {self.request["candidate"]["context_manifest_sha256"], "f" * 64}
        reads = []

        def read(digest):
            reads.append(digest)
            self.assertNotIn(digest, forbidden)
            return self.bundle.get(digest)

        self.assertEqual(plan(canonical(self.request), read), expected)
        self.assertTrue(reads)

    def test_host_io_exception_is_not_reclassified_as_invalid_input(self):
        failure = OSError("synthetic offline artifact transport failure")

        def read(_digest):
            raise failure

        with self.assertRaises(OSError) as caught:
            plan(canonical(self.request), read)
        self.assertIs(caught.exception, failure)

    def test_pointer_paths_compare_raw_keys_before_escaping(self):
        self.request["a/b~c"] = None
        self.request["a~b"] = None

        def unexpected_read(_):
            self.fail("Phase 2 accessed the artifact reader")

        error = json.loads(plan(canonical(self.request), unexpected_read))
        self.assertEqual(error["error_code"], "invalid-schema")
        self.assertEqual(error["field_path"], "/a~1b~0c")

    def test_host_exhaustion_and_interrupt_are_not_contract_results(self):
        for failure in (MemoryError("test"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):

                def read(_, failure=failure):
                    raise failure

                with self.assertRaises(type(failure)) as caught:
                    plan(canonical(self.request), read)
                self.assertIs(caught.exception, failure)

    def test_zero_budget_diagnostics_are_generated_not_execution(self):
        self.request["budget"].update(
            candidate_attempt_limit=0, request_input_limit=0, request_output_limit=0
        )
        result = self.run_request()
        output = json.loads(result)
        tuples = [(d["code"], d["packet_id"], d["pass_name"]) for d in output["diagnostics"]]
        expected = [("attempt-limit-below-slots", None, None)]
        for code in ("estimate-unavailable", "zero-input-limit", "zero-output-limit"):
            expected.extend((code, "p1", ps) for ps in ("adversarial", "expert", "qodo"))
        expected.append(("unknown-recognition", "p1", "expert"))
        expected.sort(key=lambda t: tuple("" if v is None else v for v in t))
        self.assertEqual(tuples, expected)
        self.assertTrue(
            all(e["input_tokens"] is None and e["output_allowance"] == 0 for e in output["estimates"])
        )
        claimed = output.pop("plan_digest")
        self.assertEqual(claimed, sha(canonical(output)))
        output["plan_digest"] = claimed
        self.assertEqual(result, canonical(output))


if __name__ == "__main__":
    unittest.main()
