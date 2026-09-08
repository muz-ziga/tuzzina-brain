"""Injection-layer errors. Distinct from TuzzinaError (which comes
from TuzzinaClient and means Tuzzina itself rejected or failed):
these mean the Brain refused to even build/send a payload."""
from __future__ import annotations


class InvalidInjectionIntent(ValueError):
    """The intent is malformed (missing/empty integration_id, empty
    content, bad mode, bad post_kind, malformed media refs)."""


class UnsupportedCapability(ValueError):
    """The intent asks for something the platform adapter reports as
    unsupported (e.g. a post_kind the platform has no API for, or a
    story that needs media it was not given). Raised INSTEAD of
    inventing payload fields Tuzzina does not know."""
