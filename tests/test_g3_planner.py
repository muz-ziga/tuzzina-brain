import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contracts import ContentPackage
from g3.planner import next_slots, plan


def pkg(**kw):
    d = dict(title="t", content="hello world", media=[],
             platform="facebook",
             settings={"__type": "facebook", "post_type": "post"})
    d.update(kw)
    return ContentPackage(**d)


class G3Test(unittest.TestCase):
    def test_slots_match_days(self):
        now = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)  # a Monday
        slots = next_slots(["monday", "wednesday"], ["10:00"], "UTC", 3,
                           now)
        self.assertEqual(len(slots), 3)
        for s in slots:
            dt = datetime.fromisoformat(s)
            self.assertIn(dt.weekday(), (0, 2))
            self.assertGreater(dt, now)

    def test_slots_default(self):
        now = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
        slots = next_slots([], [], "UTC", 2, now)
        self.assertEqual(len(slots), 2)
        for s in slots:
            self.assertGreater(datetime.fromisoformat(s), now)

    def test_plan_valid(self):
        now = datetime(2026, 9, 7, 8, 0, tzinfo=timezone.utc)
        import g3.planner as P
        orig = P.datetime
        try:
            class FakeDT(datetime):
                @classmethod
                def now(cls, tz=None):
                    return now
            P.datetime = FakeDT
            out = plan([pkg(), pkg()], {"timezone": "UTC"})
        finally:
            P.datetime = orig
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].settings["__type"], "facebook")

    def test_plan_rejects_empty(self):
        with self.assertRaises(ValueError):
            plan([pkg(content="  ")], {"timezone": "UTC"})

    def test_plan_rejects_bad_settings(self):
        with self.assertRaises(ValueError):
            plan([pkg(settings={"__type": "tiktok"})], {"timezone": "UTC"})

    def test_no_side_effects(self):
        p = [pkg()]
        plan(p, {"timezone": "UTC"})
        self.assertEqual(p[0].settings,
                         {"__type": "facebook", "post_type": "post"})


if __name__ == "__main__":
    unittest.main()
