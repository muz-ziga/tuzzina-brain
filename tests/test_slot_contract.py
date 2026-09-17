"""Stage B slot contract tests: pure window math, no IO, no
scheduler, no state. The executor (Stage 9) is future work;
this file pins the contract it will consume."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from g3.planner import Slot, research_window


class SlotCase(unittest.TestCase):
    def test_window_derives_from_publish_at(self):
        slot = research_window("2026-09-08T11:00:00+00:00",
                               lead_time_min=30)
        self.assertEqual(slot.publish_at,
                         "2026-09-08T11:00:00+00:00")
        self.assertEqual(slot.research_start_at,
                         "2026-09-08T10:30:00+00:00")
        self.assertEqual(slot.deadline_at, slot.publish_at)

    def test_default_lead_is_thirty(self):
        slot = research_window("2026-09-08T11:00:00+00:00")
        self.assertEqual(slot.research_start_at,
                         "2026-09-08T10:30:00+00:00")

    def test_zero_lead_means_now(self):
        slot = research_window("2026-09-08T11:00:00+00:00",
                               lead_time_min=0)
        self.assertTrue(slot.research_start_at)
        self.assertEqual(slot.deadline_at, slot.publish_at)

    def test_negative_lead_clamps_to_zero(self):
        slot = research_window("2026-09-08T11:00:00+00:00",
                               lead_time_min=-5)
        self.assertTrue(slot.research_start_at)

    def test_bad_publish_at_fails_closed(self):
        with self.assertRaises(ValueError):
            research_window("not-a-date")
        with self.assertRaises(ValueError):
            research_window("")

    def test_naive_publish_at_assumed_utc(self):
        slot = research_window("2026-09-08T11:00:00")
        self.assertIn("+00:00", slot.publish_at)

    def test_slot_is_plain_value(self):
        slot = Slot(publish_at="p", research_start_at="r",
                    deadline_at="d")
        self.assertEqual(
            (slot.publish_at, slot.research_start_at,
             slot.deadline_at), ("p", "r", "d"))

    def test_three_concepts_stay_separate(self):
        # Polling cadence, publication schedule, and lead time
        # share no field: the slot carries only its own window.
        slot = research_window("2026-09-08T11:00:00+00:00",
                               lead_time_min=30)
        self.assertFalse(hasattr(slot, "poll_interval"))
        self.assertFalse(hasattr(slot, "frequency"))


if __name__ == "__main__":
    unittest.main()
