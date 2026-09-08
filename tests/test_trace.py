"""Trace tests: prove the injection path emits causal evidence.
All Tuzzina contact goes through the in-memory FakeClient; no
network, no production, no R2, no DB."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapters.base import UnsupportedPlatform
from injection.errors import InvalidInjectionIntent, UnsupportedCapability
from injection.intent import build_intent
from injection.service import InjectionService
from injection.trace import (ADAPTER_SELECTED, ERROR, POST_REQUEST,
                             POST_RESPONSE, START, MemoryTracer,
                             NullTracer, StderrTracer)


FB_RECORD = {"id": "int-fb-1", "name": "Juzzir",
             "identifier": "facebook", "picture": "p"}


class FakeClient:
    def __init__(self, records):
        self._records = {r["id"]: r for r in records}
        self.posts_calls = []

    def get_integration(self, integration_id):
        try:
            return self._records[integration_id]
        except KeyError:
            from tuzzina.client import TuzzinaError
            raise TuzzinaError(
                f"integration {integration_id!r} not found in Tuzzina")

    def create_post(self, posts, date, post_type="draft"):
        self.posts_calls.append({"posts": posts, "date": date,
                                 "type": post_type})
        return [{"postId": "p1"}]


def fb_intent(**kw):
    d = dict(integration_id="int-fb-1", content="Hello world",
             media=[{"id": "m1", "path": "https://cdn.test/a.jpg"}],
             publish_at="2026-09-08T09:00:00+00:00", mode="draft")
    d.update(kw)
    return build_intent(**d)


def inject_with_trace(intent, client):
    tracer = MemoryTracer()
    out = InjectionService(tracer=tracer).inject(intent, client)
    return out, tracer.events


class CorrelationTest(unittest.TestCase):
    def test_one_correlation_id_per_inject(self):
        _, e1 = inject_with_trace(fb_intent(), FakeClient([FB_RECORD]))
        _, e2 = inject_with_trace(fb_intent(), FakeClient([FB_RECORD]))
        ids1 = {e["injection_id"] for e in e1}
        ids2 = {e["injection_id"] for e in e2}
        self.assertEqual(len(ids1), 1)
        self.assertEqual(len(ids2), 1)
        self.assertNotEqual(next(iter(ids1)), next(iter(ids2)))

    def test_events_preserve_same_injection_id(self):
        _, events = inject_with_trace(fb_intent(), FakeClient([FB_RECORD]))
        self.assertGreater(len(events), 0)
        ids = {e["injection_id"] for e in events}
        self.assertEqual(len(ids), 1)


class EventOrderTest(unittest.TestCase):
    def test_start_emitted(self):
        _, events = inject_with_trace(fb_intent(), FakeClient([FB_RECORD]))
        starts = [e for e in events if e["event"] == START]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["integration_id"], "int-fb-1")
        self.assertEqual(starts[0]["mode"], "draft")

    def test_adapter_selected_emitted(self):
        _, events = inject_with_trace(fb_intent(), FakeClient([FB_RECORD]))
        sel = [e for e in events if e["event"] == ADAPTER_SELECTED]
        self.assertEqual(len(sel), 1)
        self.assertEqual(sel[0]["identifier"], "facebook")
        self.assertEqual(sel[0]["adapter"], "FacebookAdapter")

    def test_post_request_emitted(self):
        _, events = inject_with_trace(fb_intent(), FakeClient([FB_RECORD]))
        req = [e for e in events if e["event"] == POST_REQUEST]
        self.assertEqual(len(req), 1)
        self.assertTrue(req[0]["has_media"])
        self.assertEqual(req[0]["mode"], "draft")

    def test_post_response_emitted(self):
        _, events = inject_with_trace(fb_intent(), FakeClient([FB_RECORD]))
        resp = [e for e in events if e["event"] == POST_RESPONSE]
        self.assertEqual(len(resp), 1)
        self.assertEqual(resp[0]["post_ids"], ["p1"])

    def test_event_order(self):
        _, events = inject_with_trace(fb_intent(), FakeClient([FB_RECORD]))
        names = [e["event"] for e in events]
        self.assertEqual(names, [START, ADAPTER_SELECTED, POST_REQUEST,
                                 POST_RESPONSE])

    def test_failure_event_emitted(self):
        tracer = MemoryTracer()
        with self.assertRaises(UnsupportedCapability):
            InjectionService(tracer=tracer).inject(
                fb_intent(post_kind="story", media=[]),
                FakeClient([FB_RECORD]))
        errs = [e for e in tracer.events if e["event"] == ERROR]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["error_type"], "UnsupportedCapability")
        self.assertIn("injection_id", errs[0])

    def test_failure_reraises_unchanged(self):
        tracer = MemoryTracer()
        with self.assertRaises(UnsupportedCapability) as ctx:
            InjectionService(tracer=tracer).inject(
                fb_intent(post_kind="story", media=[]),
                FakeClient([FB_RECORD]))
        self.assertIn("story", str(ctx.exception))

    def test_null_tracer_default_is_silent(self):
        # Existing callers that pass no tracer keep byte-identical
        # behavior: same return, no crash, no output required.
        out = InjectionService().inject(fb_intent(), FakeClient([FB_RECORD]))
        self.assertEqual(out["identifier"], "facebook")


class SecretsTest(unittest.TestCase):
    def test_secrets_never_emitted(self):
        _, events = inject_with_trace(
            fb_intent(link="https://src.test/x", links_policy="attach",
                      settings={"post_type": "post", "url": "x"}),
            FakeClient([FB_RECORD]))
        blob = str(events).lower()
        for bad in ("authorization", "access_token", "refresh_token",
                    "client_secret", "api_key", "apikey", "cookie",
                    "secret", "password", "bearer"):
            self.assertNotIn(bad, blob)

    def test_emit_rejects_disallowed_fields(self):
        tracer = MemoryTracer()
        with self.assertRaises(ValueError):
            tracer.emit("injection.start", injection_id="x",
                        access_token="EVIL")
        with self.assertRaises(ValueError):
            tracer.emit("injection.start", injection_id="x",
                        Authorization="EVIL")
        with self.assertRaises(ValueError):
            tracer.emit("injection.start", injection_id="x",
                        api_key="EVIL")
        self.assertEqual(tracer.events, [])

    def test_stderr_tracer_writes_json_lines(self):
        import io
        buf = io.StringIO()
        t = StderrTracer(stream=buf)
        t.emit(START, injection_id="abc", integration_id="i1")
        import json
        line = json.loads(buf.getvalue().strip())
        self.assertEqual(line["event"], START)
        self.assertEqual(line["injection_id"], "abc")


class BehaviorUnchangedTest(unittest.TestCase):
    def test_return_value_identical_with_and_without_tracer(self):
        c1, c2 = FakeClient([FB_RECORD]), FakeClient([FB_RECORD])
        out_plain = InjectionService().inject(fb_intent(), c1)
        out_traced, _ = inject_with_trace(fb_intent(), c2)
        self.assertEqual(out_plain, out_traced)


if __name__ == "__main__":
    unittest.main()
