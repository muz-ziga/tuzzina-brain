"""Causal tracing for the injection path. stdlib only (json, sys,
uuid). No framework, no dependency, no network.

Purpose: when a future authorized run executes, these events prove
which frames actually ran, in which order, with which correlation
id. The tracer is observability side-effect only: it never changes
return values, never retries, never stores, never sends.

Security model (fail-closed): emit() accepts ONLY allowlisted
field names. Any other key raises ValueError immediately, so a
future edit that tries to log Authorization/access_token/secret
fails loudly instead of leaking silently. See tests/test_trace.py.
"""
from __future__ import annotations
import json
import sys
import uuid

ALLOWED_FIELDS = frozenset({
    "injection_id",
    "integration_id",
    "identifier",
    "adapter",
    "mode",
    "post_kind",
    "publish_at",
    "has_media",
    "has_link",
    "has_hashtags",
    "content_chars",
    "media_count",
    "error_type",
    "stage",
    "post_ids",
    "response_count",
})

# Event names emitted along the inject() path. Fixed vocabulary so a
# future log consumer can rely on exact strings.
START = "injection.start"
ADAPTER_SELECTED = "injection.adapter_selected"
POST_REQUEST = "injection.post_request"
POST_RESPONSE = "injection.post_response"
ERROR = "injection.error"


def new_injection_id() -> str:
    """One inject() call = one id. uuid4 hex, stdlib only."""
    return uuid.uuid4().hex


class NullTracer:
    """Default: records nothing. Keeps existing callers'
    behavior byte-identical when they don't opt into tracing."""

    def emit(self, event: str, **fields) -> None:
        return None


class MemoryTracer:
    """Test seam: keeps emitted events in memory for assertions."""

    def __init__(self):
        self.events: list = []

    def emit(self, event: str, **fields) -> None:
        _check_fields(event, fields)
        record = {"event": event}
        record.update(fields)
        self.events.append(record)


class StderrTracer:
    """Runtime tracer: one JSON line per event on stderr (never
    stdout, so machine-readable run.py output stays parseable)."""

    def __init__(self, stream=None):
        self._stream = stream if stream is not None else sys.stderr

    def emit(self, event: str, **fields) -> None:
        _check_fields(event, fields)
        record = {"event": event}
        record.update(fields)
        self._stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            self._stream.flush()
        except Exception:
            pass


def _check_fields(event: str, fields: dict) -> None:
    if not isinstance(event, str) or not event:
        raise ValueError("trace event name is required")
    for key in fields:
        if key not in ALLOWED_FIELDS:
            raise ValueError(
                f"trace field {key!r} is not allowlisted "
                f"(event={event}); refusing to emit")
