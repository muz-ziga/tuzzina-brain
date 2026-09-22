"""Capability registry for the Research Agent (Phase 2).

Ownership (red lines held):
- Brain owns this INTERFACE only. Executors are thin wrappers over
  the EXISTING G1 code (g1.search, g1.website). No new provider,
  no OAuth, no account management, no vendor branch, no provider
  manager, and nothing duplicated from Tuzzina.
- An Agent addresses capabilities by NAME + typed arguments; it
  never sees an endpoint, credential, or vendor detail.

Tool results are TYPED, BOUNDED structures (schema-validated,
truncated, capped) — never raw provider text and never a channel
through which source content could inject instructions: the loop
appends them as data records, and every field is a plain string
or number with a fixed maximum length.
"""
from __future__ import annotations

# Closed vocabulary. Adding a capability means adding an entry
# here and its executor below; nothing else in runtime changes.
TOOL_NAMES = ("discovery.search", "discovery.fetch")

MAX_TOOL_ITEMS = 10
MAX_TOOL_TEXT = 2000
MAX_TOOL_TITLE = 200
MAX_TOOL_URL = 500
MAX_QUERY_CHARS = 300

# Campaign-side capability config keys (allowlisted shapes only).
CAPABILITY_KEYS = ("discovery",)
_DISCOVERY_KEYS = ("max_calls", "n", "max_age_days")

MAX_CAPABILITY_CALLS = 40  # runtime ceiling over any campaign value
MAX_CAPABILITY_N = 20


class ToolError(Exception):
    """Capability misuse or executor failure. Callers translate it
    into a typed tool-result error (never a stage crash): the agent
    sees the failure and may adapt."""


def enabled_tools(capabilities) -> dict:
    """Resolve the campaign capability block into an allowlist of
    tool names plus their per-tool limits. Unknown keys fail
    closed. Absent/empty means NO tools (pre-Phase-2 behavior:
    the agent has nothing to call and the stage behaves as
    today). Returns {name: {"n": int, "max_age_days": int|None}}.
    """
    if capabilities is None:
        return {}
    if not isinstance(capabilities, dict):
        raise ToolError("capabilities-not-mapping")
    for key in capabilities:
        if key not in CAPABILITY_KEYS:
            raise ToolError("unknown-capability:" + str(key))
    block = capabilities.get("discovery")
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ToolError("discovery-capability-not-mapping")
    for key in block:
        if key not in _DISCOVERY_KEYS:
            raise ToolError("unknown-discovery-key:" + str(key))
    n = _clamp(block.get("n"), 1, MAX_CAPABILITY_N, 3)
    horizon = _clamp(block.get("max_age_days"), 0, 3650, None)
    return {
        "discovery.search": {"n": n, "max_age_days": horizon},
        "discovery.fetch": {"n": n, "max_age_days": horizon},
    }


def _clamp(value, low, high, default):
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, num))


def validate_arguments(name: str, arguments) -> dict:
    """Structural validation of one tool call's arguments. Returns
    a clean argument dict or raises ToolError (the loop turns that
    into a typed rejection; the agent may retry with fixed args)."""
    if name not in TOOL_NAMES:
        raise ToolError("unknown-tool:" + str(name))
    if not isinstance(arguments, dict):
        raise ToolError("arguments-not-mapping")
    if name == "discovery.search":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolError("search-query-required")
        if len(query) > MAX_QUERY_CHARS:
            raise ToolError("search-query-too-long")
        return {"query": query.strip()}
    url = arguments.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ToolError("fetch-url-required")
    if len(url) > MAX_TOOL_URL:
        raise ToolError("fetch-url-too-long")
    if not url.strip().lower().startswith(("http://", "https://")):
        raise ToolError("fetch-url-not-http")
    return {"url": url.strip()}


def _f(item, name: str) -> str:
    """Field read that accepts both SourceItem objects (real G1)
    and plain mappings (test doubles)."""
    if isinstance(item, dict):
        return item.get(name) or ""
    return getattr(item, name, "") or ""


def _shape_item(it) -> dict:
    """One SourceItem -> a bounded typed record. Every field is a
    string with a fixed maximum: source content can never grow
    into an unbounded blob inside the agent's context."""
    return {
        "item_id": str(_f(it, "item_id") or _f(it, "source_id"))[:MAX_TOOL_URL],
        "title": str(_f(it, "title"))[:MAX_TOOL_TITLE],
        "text": str(_f(it, "text"))[:MAX_TOOL_TEXT],
        "source_url": str(_f(it, "source_url"))[:MAX_TOOL_URL],
        "published_at": str(_f(it, "published_at"))[:64],
        "platform": str(_f(it, "platform"))[:32],
    }


def execute(name: str, arguments, limits: dict,
            *, now: str = "", search_fn=None, fetch_fn=None) -> dict:
    """Run ONE capability call and return a typed, bounded result.

    Executors reuse G1 verbatim (search_fn/fetch_fn are the
    injection seams used by tests; production passes the real G1
    functions). A failure is returned as {ok: False, error: code}
    — never raised into the stage — so the agent can adapt.
    """
    try:
        args = validate_arguments(name, arguments)
    except ToolError as e:
        return {"ok": False, "error": str(e)}
    cap = limits.get(name) or {}
    n = _clamp(cap.get("n"), 1, MAX_CAPABILITY_N, 3)
    horizon = cap.get("max_age_days")
    try:
        if name == "discovery.search":
            items = _run_search(args["query"], n, horizon, now,
                                search_fn)
        else:
            items = _run_fetch(args["url"], n, fetch_fn)
    except ToolError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False,
                "error": "tool-failed:" + type(e).__name__}
    records = [_shape_item(it) for it in (items or [])][:MAX_TOOL_ITEMS]
    return {"ok": True, "tool": name,
            "count": len(records), "items": records}


def _run_search(query: str, n: int, horizon, now: str,
                search_fn):
    if search_fn is None:
        from g1.search import SearchError, discover, to_source_items
        try:
            results, _tool, _server = discover(
                query, capability="web.search", max_results=n)
        except SearchError as e:
            raise ToolError("search-failed:" + str(e)[:80])
        return to_source_items(results, source_type="search",
                               capability="web.search", query=query,
                               discovered_at=now)
    return search_fn(query, n, horizon)


def _run_fetch(url: str, n: int, fetch_fn):
    if fetch_fn is None:
        from g1.website import WebsiteAdapter
        res = WebsiteAdapter().extract(url, max(1, n))
        return list(getattr(res, "items", []) or [])
    return fetch_fn(url, n)
