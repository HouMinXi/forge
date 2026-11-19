# SPDX-License-Identifier: Apache-2.0

import json
import unittest

from tests.offline_planner_replay import canon, inventory, require_equal
from code_forge.offline_planner.lexical import InvalidJSON, NonSchemaNumber, decode_request


class ReplayTests(unittest.TestCase):
    def test_compare_reports_first_byte_and_lengths(self):
        with self.assertRaisesRegex(ValueError, "fixture: result: first differing byte 1"):
            require_equal("fixture", "result", b"ax", b"ab")
        with self.assertRaisesRegex(ValueError, "actual bytes=1.*expected bytes=2"):
            require_equal("fixture", "result", b"a", b"ab")

    def test_archive_lexical_classification_and_encoding(self):
        files = inventory()
        for row in json.loads(files["cases.json"]):
            with self.subTest(row=row["name"]):
                raw = files[f"vectors/{row['name']}/input.json"]
                if row["expected"] == "invalid-json":
                    with self.assertRaises(InvalidJSON):
                        decode_request(raw)
                else:
                    decode_request(raw)
        for name in ("controls-00-1f", "unicode-composed", "unicode-decomposed", "literal-backslash-n"):
            with self.subTest(encoding=name):
                raw = files[f"encoding/{name}.bin"]
                self.assertEqual(canon(decode_request(raw)), raw)

    def test_extra_lexical_controls(self):
        for raw in (
            b"\xef\xbb\xbf{}",
            b'{"a":1,"\\u0061":2}',
            b'"\xff"',
            b'"\\ud800"',
            b'{"\\udfff":1}',
            b"[NaN]",
            b"Infinity",
            b"-Infinity",
        ):
            with self.subTest(raw=raw), self.assertRaises(InvalidJSON):
                decode_request(raw)
        self.assertEqual(decode_request(b'"\\ud83d\\ude00"'), chr(0x1F600))
        self.assertEqual(decode_request(b"-1e-999999"), NonSchemaNumber("-1e-999999"))
        self.assertEqual(decode_request(b"9" * 5000), NonSchemaNumber("9" * 5000))


if __name__ == "__main__":
    unittest.main()
