"""Capability status: a lightweight read-only lens over an
adapter's capabilities() dict. NOT a registry, NOT a source of
truth -- Tuzzina remains both. Answers only:

  "supported"   the adapter reports this feature as available
  "unsupported" the adapter reports it as unavailable
  "unknown"     the adapter says nothing about it

Anything "unknown" or "unsupported" must surface as
UnsupportedCapability at inject time, never as invented payload.
"""
from __future__ import annotations


def status(caps: dict, feature: str) -> str:
    """Resolve a dotted feature path (e.g. "media.carousel_max")
    against a capabilities() dict."""
    if not isinstance(caps, dict):
        return "unknown"
    node: object = caps
    for part in feature.split("."):
        if not isinstance(node, dict) or part not in node:
            return "unknown"
        node = node[part]
    if node is None:
        return "unknown"
    if node is False:
        return "unsupported"
    if isinstance(node, str) and node.strip().lower() == "unsupported":
        return "unsupported"
    if isinstance(node, (list, tuple, set, dict)) and len(node) == 0:
        return "unsupported"
    return "supported"
