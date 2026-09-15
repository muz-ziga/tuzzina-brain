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

    def test_sources_optional_fallback(self):
        cfg = load_campaign(self._f("brand:\n  name: B\n"))
        self.assertEqual(cfg["sources"], [])

    def test_bad_source_type(self):
        with self.assertRaises(ValueError):
            load_campaign(self._f(GOOD.replace("website", "tiktok")))

    def test_bad_policy(self):
        with self.assertRaises(ValueError):
            load_campaign(self._f(GOOD + "links_policy: everywhere\n"))

    def test_skills_passthrough(self):
        cfg = load_campaign(self._f(
            GOOD + "skills:\n  research: juzzir\n"
            "  text: juzzir\n"))
        self.assertEqual(cfg["skills"], {
            "research": "juzzir",
            "text": "juzzir",
        })

    def test_skills_absent_defaults_empty(self):
        cfg = load_campaign(self._f(GOOD))
        self.assertEqual(cfg["skills"], {})

    def test_roles_default_all_five(self):
        cfg = load_campaign(self._f(GOOD))
        self.assertEqual(cfg["roles"],
                         ["research", "analysis", "text",
                          "image", "video"])

    def test_roles_subset_normalized_canonical(self):
        cfg = load_campaign(self._f(
            GOOD + "roles:\n  - video\n  - text\n  - text\n"))
        self.assertEqual(cfg["roles"], ["text", "video"])

    def test_roles_rejects_bad_shapes(self):
        for roles in ("roles: text\n",
                      "roles: []\n",
                      "roles:\n  - podcast\n",
                      "roles:\n  - text\n  - 7\n"):
            with self.assertRaises(ValueError, msg=roles[:20]):
                load_campaign(self._f(GOOD + roles))

    def test_skills_blank_values_dropped(self):
        cfg = load_campaign(self._f(
            GOOD + "skills:\n  research: '   '\n  text: juzzir\n"))
        self.assertEqual(cfg["skills"], {"text": "juzzir"})

    def test_skills_rejects_bad_shapes(self):
        for skills in ("skills: [1]\n",
                       "skills:\n  podcast: x\n",
                       "skills:\n  text: 7\n",
                       "skills:\n  text: Write short posts.\n",
                       "skills:\n  text: CAMPAIGN-A-SKILL-PROOF\n",
                       "skills:\n  text: ../text\n",
                       "skills:\n  text: '" + "x" * 65 + "'\n"):
            with self.assertRaises(ValueError, msg=skills[:20]):
                load_campaign(self._f(GOOD + skills))


if __name__ == "__main__":
    unittest.main()
