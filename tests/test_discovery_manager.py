"""Stage C discovery-core tests: pure due-source policy. No
network, no clock (now is explicit), no state, no LLM."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from discovery.manager import (DEFAULT_POLL_MINUTES,
                               FALLBACK_POLL_MINUTES, DueSource,
                               due_sources, poll_minutes_for)

NOW = "2026-09-08T12:00:00+00:00"


def _rss(url="http://f.test/rss", **over):
    d = {"type": "rss", "url": url, "n": 1}
    d.update(over)
    return d


class CadenceCase(unittest.TestCase):
    def test_type_defaults(self):
        self.assertEqual(
            poll_minutes_for({"type": "rss"}),
            DEFAULT_POLL_MINUTES["rss"])
        self.assertEqual(
            poll_minutes_for({"type": "website"}),
            DEFAULT_POLL_MINUTES["website"])
        self.assertEqual(
            poll_minutes_for({"type": "youtube"}),
            DEFAULT_POLL_MINUTES["youtube"])

    def test_explicit_wins(self):
        self.assertEqual(
            poll_minutes_for({"type": "rss", "poll_minutes": 1}),
            1)

    def test_garbage_falls_back(self):
        # Bad explicit values degrade to the per-type default
        # (validation rejects them upstream; the manager never
        # breaks the cycle on config garbage).
        for bad, want in (
                ({"type": "rss", "poll_minutes": 0}, 5),
                ({"type": "rss", "poll_minutes": -3}, 5),
                ({"type": "rss", "poll_minutes": "often"}, 5),
                ({"type": "rss", "poll_minutes": True}, 5),
                ({"type": "nope"}, FALLBACK_POLL_MINUTES),
                ({}, FALLBACK_POLL_MINUTES)):
            self.assertEqual(poll_minutes_for(bad), want, bad)


class DueCase(unittest.TestCase):
    def test_never_checked_is_due(self):
        due = due_sources([_rss()], {}, NOW)
        self.assertEqual(len(due), 1)
        self.assertIsInstance(due[0], DueSource)
        self.assertEqual(due[0].last_checked_at, "")

    def test_fresh_check_not_due(self):
        due = due_sources(
            [_rss()], {"http://f.test/rss": NOW}, NOW)
        self.assertEqual(due, [])

    def test_stale_check_is_due(self):
        due = due_sources(
            [_rss()],
            {"http://f.test/rss": "2026-09-08T11:00:00+00:00"},
            NOW)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].poll_minutes,
                         DEFAULT_POLL_MINUTES["rss"])

    def test_explicit_cadence_respected(self):
        checked = {"http://f.test/rss":
                   "2026-09-08T11:55:00+00:00"}
        self.assertEqual(
            due_sources([_rss(poll_minutes=1)], checked, NOW),
            [due_sources([_rss(poll_minutes=1)], checked,
                         NOW)[0]])
        self.assertEqual(
            due_sources([_rss(poll_minutes=60)], checked, NOW),
            [])

    def test_disabled_never_due(self):
        due = due_sources([_rss(enabled=False)], {}, NOW)
        self.assertEqual(due, [])

    def test_unparseable_last_check_is_due(self):
        due = due_sources(
            [_rss()], {"http://f.test/rss": "not-a-date"}, NOW)
        self.assertEqual(len(due), 1)

    def test_unparseable_now_fails_closed(self):
        with self.assertRaises(ValueError):
            due_sources([_rss()], {}, "someday")

    def test_source_id_key_used(self):
        src = _rss()
        src["source_id"] = "feed-main"
        due = due_sources([src], {"feed-main": NOW}, NOW)
        self.assertEqual(due, [])

    def test_inputs_untouched(self):
        src = _rss()
        before = dict(src)
        due_sources([src], {}, NOW)
        self.assertEqual(src, before)

    def test_mixed_batch(self):
        sources = [_rss("http://f.test/a"),
                   _rss("http://f.test/b", enabled=False),
                   {"type": "website",
                    "url": "http://w.test/"}]
        checked = {"http://f.test/a": NOW}
        due = due_sources(sources, checked, NOW)
        self.assertEqual(
            sorted(d["source"].get("url", "") for d in
                   ({"source": d.source} for d in due)),
            ["http://w.test/"])


class StrategyCarryCase(unittest.TestCase):
    def test_poll_minutes_survives_store(self):
        # Exercise through the module loader path: construct
        # via load() so validation + carry are both proven.
        import tempfile
        import yaml
        doc = {"integration_id": "int-1",
               "strategy": {"sources": [
                   {"type": "rss",
                    "url": "https://f.test/rss",
                    "poll_minutes": 2}]}}
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + "/int-1.yaml"
            with open(path, "w",
                      encoding="utf-8") as f:
                yaml.safe_dump(doc, f)
            from channels.strategy_store import load
            loaded = load("int-1", base_dir=tmp)
        self.assertEqual(loaded.sources[0]["poll_minutes"], 2)

    def test_bad_poll_minutes_rejected(self):
        import tempfile
        import yaml
        doc = {"integration_id": "int-1",
               "strategy": {"sources": [
                   {"type": "rss",
                    "url": "https://f.test/rss",
                    "poll_minutes": 0}]}}
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + "/int-1.yaml"
            with open(path, "w",
                      encoding="utf-8") as f:
                yaml.safe_dump(doc, f)
            from channels.strategy_store import load
            with self.assertRaises(ValueError):
                load("int-1", base_dir=tmp)


if __name__ == "__main__":
    unittest.main()
