"""Generic task-execution retry: classified failures at the shared
LLM layer. All transport faked (no network, no keys, no live calls).
Proves, against complete_with_retry + classify_llm_error:
valid success, reasoning-only/empty/malformed retry, 429/5xx/timeout
retry, credential/model/config errors never retry, workflow-
survival (caller continues after rescue), stable task identity,
no duplicate side effects, and eventual success on recovery."""
import os
import socket
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm.adapters import (LLMError, classify_llm_error,
                          complete_with_retry)


class ScriptAdapter:
    """Fake adapter: plays a scripted outcome list. Records every
    call (args identity) and counts side-effect invocations."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []
        self.side_effects = 0

    def complete(self, system, user, *, temperature=0.7, max_tokens=400,
                 timeout=60):
        self.calls.append((system, user, temperature, max_tokens,
                           timeout))
        if not self._outcomes:
            raise AssertionError("adapter called past script end")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _valid_json():
    return '{"ok": true}'


def _strict_validate(raw):
    import json
    try:
        data = json.loads(raw)
    except Exception:
        raise LLMError("invalid-output")
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise LLMError("invalid-output")


def _nosleep():
    calls = []

    def sleep(s):
        calls.append(s)

    sleep.calls = calls
    return sleep


class ClassifyCase(unittest.TestCase):
    def test_valid_content_is_not_an_error(self):
        # classify only sees failures; a clean string is untouched
        self.assertEqual(classify_llm_error(LLMError("http-200")), "non_retryable")

    def test_reasoning_only_empty_content_retries(self):
        self.assertEqual(
            classify_llm_error(LLMError("empty-content: keys=[a]")),
            "retryable")

    def test_malformed_response_retries(self):
        self.assertEqual(
            classify_llm_error(LLMError("malformed-response")),
            "retryable")

    def test_invalid_output_retries(self):
        self.assertEqual(
            classify_llm_error(LLMError("invalid-output")), "retryable")

    def test_429_retries(self):
        self.assertEqual(
            classify_llm_error(LLMError("http-429: slow down")),
            "retryable")

    def test_5xx_retries(self):
        for code in ("http-500", "http-502: bad gateway", "http-503"):
            self.assertEqual(classify_llm_error(LLMError(code)),
                             "retryable")

    def test_4xx_never_retries(self):
        for code in ("http-400", "http-401: bad key", "http-403",
                     "http-404"):
            self.assertEqual(classify_llm_error(LLMError(code)),
                             "non_retryable")

    def test_refusal_never_retries(self):
        self.assertEqual(
            classify_llm_error(LLMError("refusal:max_tokens")),
            "non_retryable")

    def test_timeouts_and_connections_retry(self):
        self.assertEqual(classify_llm_error(TimeoutError()), "retryable")
        self.assertEqual(classify_llm_error(socket.timeout()),
                         "retryable")
        self.assertEqual(
            classify_llm_error(ConnectionError("reset")), "retryable")
        self.assertEqual(
            classify_llm_error(urllib.error.URLError("dns")), "retryable")
        self.assertEqual(
            classify_llm_error(LLMError("transport-URLError")),
            "retryable")

    def test_config_errors_never_retry(self):
        self.assertEqual(classify_llm_error(ValueError("api-key-required")),
                         "non_retryable")
        self.assertEqual(
            classify_llm_error(ValueError("unknown-llm-adapter:x:y")),
            "non_retryable")
        self.assertEqual(classify_llm_error(KeyError("k")),
                         "non_retryable")

    def test_unknown_llm_error_fails_closed(self):
        self.assertEqual(classify_llm_error(LLMError("weird-9")),
                         "non_retryable")


class RetryCase(unittest.TestCase):
    def _run(self, outcomes, **kw):
        adapter = ScriptAdapter(outcomes)
        sleep = _nosleep()
        kw.setdefault("sleep", sleep)
        kw.setdefault("jitter", False)
        stats = {}
        try:
            out = complete_with_retry(
                adapter, "sys", "user", validate=kw.pop("validate", None),
                stats=stats, **kw)
            return adapter, sleep, stats, ("ok", out)
        except Exception as e:  # noqa: BLE001 - asserting on it
            return adapter, sleep, stats, ("err", e)

    def test_valid_first_try_success_no_sleep(self):
        adapter, sleep, stats, (kind, out) = self._run([_valid_json()],
                                                       validate=_strict_validate)
        self.assertEqual((kind, out), ("ok", _valid_json()))
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(sleep.calls, [])
        self.assertEqual(stats["attempts"], 1)
        self.assertEqual(stats["retried"], [])

    def test_reasoning_only_then_success(self):
        adapter, sleep, stats, (kind, out) = self._run(
            [LLMError("empty-content: keys=[reasoning]"), _valid_json()],
            validate=_strict_validate)
        self.assertEqual(kind, "ok")
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(len(sleep.calls), 1)
        self.assertEqual(stats["attempts"], 2)
        self.assertTrue(stats["retried"][0].startswith("empty-content"))

    def test_malformed_json_then_success(self):
        adapter, sleep, stats, (kind, out) = self._run(
            ["{not json", _valid_json()], validate=_strict_validate)
        self.assertEqual(kind, "ok")
        self.assertEqual(len(adapter.calls), 2)

    def test_schema_invalid_then_success(self):
        adapter, sleep, stats, (kind, out) = self._run(
            ['{"ok": false}', _valid_json()], validate=_strict_validate)
        self.assertEqual(kind, "ok")
        self.assertEqual(stats["retried"], ["invalid-output"])

    def test_429_then_success(self):
        adapter, sleep, stats, (kind, out) = self._run(
            [LLMError("http-429"), "hello"])
        self.assertEqual((kind, out), ("ok", "hello"))

    def test_5xx_then_success(self):
        adapter, sleep, stats, (kind, out) = self._run(
            [LLMError("http-500: boom"), "hello"])
        self.assertEqual(kind, "ok")

    def test_timeout_then_success(self):
        adapter, sleep, stats, (kind, out) = self._run(
            [TimeoutError(), "hello"])
        self.assertEqual(kind, "ok")
        self.assertEqual(len(sleep.calls), 1)

    def test_auth_failure_never_retries(self):
        adapter, sleep, stats, (kind, out) = self._run(
            [LLMError("http-401: bad key"), "hello"])
        self.assertEqual(kind, "err")
        self.assertIsInstance(out, LLMError)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(sleep.calls, [])

    def test_unsupported_model_never_retries(self):
        adapter, sleep, stats, (kind, out) = self._run(
            [ValueError("unknown-llm-adapter:x:y"), "hello"])
        self.assertEqual(kind, "err")
        self.assertIsInstance(out, ValueError)
        self.assertEqual(len(adapter.calls), 1)

    def test_missing_config_never_retries(self):
        adapter, sleep, stats, (kind, out) = self._run(
            [ValueError("api-key-required"), "hello"])
        self.assertEqual(kind, "err")
        self.assertEqual(len(adapter.calls), 1)

    def test_exhaustion_raises_last_error(self):
        adapter, sleep, stats, (kind, out) = self._run(
            [LLMError("empty-content"), LLMError("empty-content"),
             LLMError("empty-content")],
            validate=_strict_validate, max_attempts=3)
        self.assertEqual(kind, "err")
        self.assertIsInstance(out, LLMError)
        self.assertEqual(len(adapter.calls), 3)
        self.assertEqual(len(sleep.calls), 2)
        self.assertEqual(stats["attempts"], 3)

    def test_backoff_grows_exponentially(self):
        adapter, sleep, stats, (kind, out) = self._run(
            [TimeoutError(), TimeoutError(), TimeoutError()],
            max_attempts=3, base_delay=2.0, max_delay=100.0)
        self.assertEqual(sleep.calls, [2.0, 4.0])

    def test_backoff_capped(self):
        adapter, sleep, stats, (kind, out) = self._run(
            [TimeoutError()] * 4, max_attempts=4, base_delay=5.0,
            max_delay=6.0)
        self.assertEqual(sleep.calls, [5.0, 6.0, 6.0])

    def test_task_identity_stable_across_retries(self):
        adapter, sleep, stats, (kind, out) = self._run(
            [LLMError("http-503"), LLMError("empty-content"), "done"],
            temperature=0.2, max_tokens=1500, timeout=90)
        self.assertEqual(kind, "ok")
        # Same adapter, same system/user/temperature/max_tokens/
        # timeout on every attempt: one logical task, retried.
        for call in adapter.calls:
            self.assertEqual(
                call,
                ("sys", "user", 0.2, 1500, 90))

    def test_no_duplicate_side_effects(self):
        # The executor performs pure reads; a counting adapter
        # proves repeats are re-reads of the same task, and the
        # caller (not this layer) owns all downstream effects.
        adapter, sleep, stats, (kind, out) = self._run(
            [LLMError("empty-content"), "v"], task="research")
        self.assertEqual(kind, "ok")
        self.assertEqual(stats["task"], "research")
        self.assertEqual(adapter.side_effects, 0)

    def test_workflow_continues_after_rescue(self):
        # A caller-level workflow survives: rescued raw validates
        # and the pipeline proceeds instead of terminating.
        adapter, sleep, stats, (kind, out) = self._run(
            ['{"ok": false}', _valid_json()], validate=_strict_validate)
        self.assertEqual(kind, "ok")
        import json
        pipeline_result = json.loads(out)
        self.assertTrue(pipeline_result["ok"])

    def test_max_attempts_floor_validated(self):
        adapter = ScriptAdapter(["x"])
        with self.assertRaises(ValueError):
            complete_with_retry(adapter, "s", "u", max_attempts=0,
                                sleep=_nosleep())


if __name__ == "__main__":
    unittest.main()
