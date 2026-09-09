"""Phase 8: format vocabulary + allowlist gate. Pure unit tests,
no fixtures, no network."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from research.formats import (FORMATS, allowed_from_distribution,
                              format_for, select_format)


class VocabularyCase(unittest.TestCase):
    def test_closed_vocabulary(self):
        self.assertEqual(FORMATS,
                         ("text", "text+image", "text+video", "link+text"))

    def test_mapping(self):
        self.assertEqual(format_for("none", "hide"), "text")
        self.assertEqual(format_for("none", "attach"), "link+text")
        self.assertEqual(format_for("image", "hide"), "text+image")
        self.assertEqual(format_for("image", "attach"), "text+image")
        self.assertEqual(format_for("video", "hide"), "text+video")
        self.assertEqual(format_for("", ""), "text")

    def test_case_and_blank_tolerance(self):
        self.assertEqual(format_for("IMAGE", "HIDE"), "text+image")
        self.assertEqual(format_for("none", "cta"), "text")


class AllowlistCase(unittest.TestCase):
    def test_unconstrained_when_absent(self):
        for missing in (None, {}, {"other": 1},
                        "junk", {"formats": "junk"}):
            fmt, reason = select_format("image", "hide",
                                        allowed_from_distribution(missing))
            self.assertEqual((fmt, reason), ("text+image", "unconstrained"))

    def test_empty_dict_denies_all(self):
        allowed = allowed_from_distribution({"formats": {}})
        self.assertEqual(allowed, [])
        fmt, reason = select_format("none", "hide", allowed)
        self.assertIsNone(fmt)
        self.assertEqual(reason, "format-not-allowed:text")

    def test_allowed(self):
        fmt, reason = select_format(
            "image", "hide",
            allowed_from_distribution({"formats": {"text+image": 5}}))
        self.assertEqual((fmt, reason), ("text+image", "allowed"))

    def test_zero_count_disallows(self):
        fmt, reason = select_format(
            "image", "hide",
            allowed_from_distribution({"formats": {"text+image": 0,
                                                  "text": 10}}))
        self.assertIsNone(fmt)
        self.assertEqual(reason, "format-not-allowed:text+image")

    def test_unknown_format_keys_ignored(self):
        allowed = allowed_from_distribution(
            {"formats": {"story": 5, "text": 1}})
        self.assertEqual(allowed, ["text"])
        fmt, _ = select_format("none", "hide", allowed)
        self.assertEqual(fmt, "text")

    def test_non_numeric_counts_ignored(self):
        allowed = allowed_from_distribution(
            {"formats": {"text": "many"}})
        self.assertEqual(allowed, [])


if __name__ == "__main__":
    unittest.main()
