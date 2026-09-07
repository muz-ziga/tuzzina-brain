import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from campaign import load_campaign

GOOD = """
brand:
  name: B
sources:
  - type: website
    url: https://demo.test
    n: 2
"""


class CampaignTest(unittest.TestCase):
    def _f(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        f.write(text)
        f.close()
        return f.name

    def test_good_defaults(self):
        cfg = load_campaign(self._f(GOOD))
        self.assertEqual(cfg["brand"]["name"], "B")
        self.assertEqual(cfg["links_policy"], "hide")
        self.assertEqual(cfg["language"]["default"], "ar")
        self.assertEqual(cfg["schedule"]["timezone"], "UTC")

    def test_missing_brand(self):
        with self.assertRaises(ValueError):
            load_campaign(self._f("sources:\n - {type: website, url: x}\n"))

    def test_bad_source_type(self):
        with self.assertRaises(ValueError):
            load_campaign(self._f(GOOD.replace("website", "tiktok")))

    def test_bad_policy(self):
        with self.assertRaises(ValueError):
            load_campaign(self._f(GOOD + "links_policy: everywhere\n"))


if __name__ == "__main__":
    unittest.main()
