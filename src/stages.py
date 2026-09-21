"""Shared current-stage retry (Level 2 task/stage execution).

Level 1 (provider attempts) is complete_with_retry in
llm/adapters.py: same call repeated with second-scale backoff.
This module is Level 2: when Level 1 exhausts but the failure
remains retryable, the SAME logical stage waits minutes and runs
again — the campaign stays at the current stage instead of
restarting from stage 1, and downstream stages never unlock
until the current one returns validated SUCCESS.

STAGES names the retryable pre-execution stages. "package"
covers text + image-prompt assembly (both live inside
build_package; splitting it would duplicate agent logic, so the
shared helper covers them together while each role keeps its
own Level-1 retry). "execute" is deliberately ABSENT: media
resolution, injection, and publishing have side effects and stay
under the existing idempotent execution paths — never wrapped
here.

Cross-process durability (surviving a dead runner) belongs to
the Temporal workflow/activity layer, which re-invokes runs;
this helper covers waits within one runner lifetime. It creates
no state, no scheduler, no per-agent managers: one function,
one classifier, one envelope shape.
"""
from __future__ import annotations
import random
import socket
import sys
import time
import urllib.error

from llm.adapters import LLMError, classify_llm_error

STAGES = ("research", "analysis", "package")

SUCCESS = "SUCCESS"
RETRYABLE_STAGE_FAILURE = "RETRYABLE_STAGE_FAILURE"
TERMINAL_FAILURE = "TERMINAL_FAILURE"


def stage_ok(stage: str, payload=None) -> dict:
    """Stage result envelope, success shape."""
    return {"stage": stage, "status": SUCCESS, "payload": payload,
            "error": ""}


def stage_fail(stage: str, error: object, *, retryable: bool) -> dict:
    """Stage result envelope, failure shape. Terminal failures
    never unlock the next stage; retryable ones keep the
    campaign waiting at the current stage."""
    return {
        "stage": stage,
        "status": RETRYABLE_STAGE_FAILURE if retryable else
        TERMINAL_FAILURE,
        "payload": None,
        "error": f"{type(error).__name__}: {error}",
    }


def classify_stage_error(err: object) -> str:
    """"retryable" or "terminal" for ANY stage-call failure.

    LLMError reuses the shared provider classifier. Domain
    wrappers ("model-error: <inner>", "model-timeout") unwrap
    to the inner provider code so a reasoning-only reply stays
    retryable while a 401 stays terminal. Everything else
    (config, structural, programming errors) is terminal:
    repeating it identically cannot help. Pure; never raises.
    """
    if isinstance(err, LLMError):
        return ("retryable"
                if classify_llm_error(err) == "retryable"
                else "terminal")
    if isinstance(err, (TimeoutError, socket.timeout, ConnectionError,
                        urllib.error.URLError)):
        return "retryable"
    text = str(err)
    if text == "model-timeout":
        return "retryable"
    if text.startswith("model-error:"):
        inner = text.split(":", 1)[1].strip() or "unknown"
        return ("retryable"
                if classify_llm_error(LLMError(inner)) == "retryable"
                else "terminal")
    return "terminal"


def run_stage(stage: str, fn, *, attempts: int = 3,
              base_delay: float = 45.0, max_delay: float = 180.0,
              jitter: bool = True, sleep=None,
              stats: dict | None = None):
    """Run one logical stage fn() with slow shared retries.

    The SAME closure runs every attempt (same inputs: stage
    identity preserved). Retryable failures sleep with
    exponential backoff then repeat; terminal failures and
    exhaustion re-raise the last error unchanged, so existing
    callers (cycle error envelope, slot terminal states) behave
    byte-identically apart from waiting longer first.

    Only wrap side-effect-free pre-execution stages. Retrying
    repeats fn(): callers must pass closures whose repetition
    cannot duplicate posts, images, rows, or publications
    (research/analysis/package assembly are pure reads).
    stats, when given, receives {"stage", "attempts", "retried"}.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown-stage:{stage}")
    if attempts < 1:
        raise ValueError("stage attempts must be >= 1")
    # Resolved at call time (not def time) so tests can substitute
    # a fake clock without touching production defaults.
    _sleep = sleep if sleep is not None else time.sleep
    retried: list = []
    last: BaseException | None = None
    for n in range(1, attempts + 1):
        try:
            value = fn()
            if stats is not None:
                stats.update({"stage": stage, "attempts": n,
                              "retried": retried})
            return value
        except Exception as e:
            last = e
            if classify_stage_error(e) == "terminal" or n >= attempts:
                if stats is not None:
                    stats.update({"stage": stage, "attempts": n,
                                  "retried": retried})
                raise
            if isinstance(e, LLMError):
                code = str(e.args[0]) if e.args else "LLMError"
            else:
                code = f"{type(e).__name__}: {str(e)[:80]}"
            retried.append(code)
            delay = min(max_delay, base_delay * (2 ** (n - 1)))
            if jitter:
                delay += random.uniform(0, base_delay)
            # Single stderr line per stage retry (existing run
            # trace convention): makes a rescued stage visible
            # in orchestrator logs without touching the result
            # envelope. No secrets: code only, never prompts.
            print(f"stage={stage} attempt={n} retryable ({code}): "
                  f"waiting {delay:.0f}s, retrying same stage",
                  file=sys.stderr)
            _sleep(delay)
    assert last is not None  # attempts >= 1 guarantees a pass
    raise last
