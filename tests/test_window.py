import sys
sys.path.insert(0, "src")
import unittest
from slots import window_publish_ats

class WindowCase(unittest.TestCase):
    def test_finite_bounded(self):
        sched={"timezone":"UTC","times":["11:00","12:00","13:00"],"days":[]}
        ats=window_publish_ats(sched,"2026-09-18T10:00:00+00:00","2026-09-18T13:00:00+00:00",limit=10)
        self.assertEqual(len(ats),3)
    def test_recurring_next_window(self):
        sched={"timezone":"UTC","times":["11:00"],"days":[]}
        w1=window_publish_ats(sched,"2026-09-18T10:00:00+00:00","2026-09-18T12:00:00+00:00")
        w2=window_publish_ats(sched,"2026-09-18T12:00:00+01:00","2026-09-18T14:00:00+00:00")
        self.assertNotEqual(w1,w2)
        self.assertEqual(len(set(w1+w2)), len(w1)+len(w2))
    def test_deterministic(self):
        sched={"timezone":"UTC","times":["09:00"]}
        a=window_publish_ats(sched,"2026-09-18T00:00:00+00:00","2026-09-19T00:00:00+00:00")
        b=window_publish_ats(sched,"2026-09-18T00:00:00+00:00","2026-09-19T00:00:00+00:00")
        self.assertEqual(a,b)
    def test_no_side_effects(self):
        import pathlib
        src=pathlib.Path("src/slots.py").read_text().split("def window_publish_ats")[1].split("\ndef ")[0]
        for bad in ["urllib","socket","create_post"]:
            self.assertNotIn(bad, src)
        self.assertNotIn("claim(", src)
if __name__=="__main__": unittest.main()
