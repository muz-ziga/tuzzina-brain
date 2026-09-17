"""Search discovery adapter (Stage E). Query-driven candidate
discovery through the GENERIC MCP capability layer: this module
names only semantic capabilities ("web.search",
"news.search") and never a vendor, endpoint, or credential.
Resolution (registry) + transport (McpClient) are shared
infrastructure; swapping the backing search provider is a
registry edit, never code.

Source types produced: "web" (capability web.search),
"news" (capability news.search), "social.facebook",
"social.instagram" and "social.x" (capabilities of the
same name, resolved through the same registry to a
Tuzzina-owned or self-hosted MCP read tool). Results
normalize into the
existing SourceItem contract and reuse stable_identity, so
cross-query / cross-capability / cross-engine duplicates
collapse exactly like feed duplicates. URLs are required
(no URL means no stable identity under the existing
contract: discarded, never invented).

Freshness: publishedDate rides through verbatim when
present; dateless results are kept (never dated by fiat)
and freshness preference stays a downstream policy
(max_age_days filter here, skill guidance downstream).

Fail-closed: unresolvable capability, MCP error, empty or
malformed results all yield zero candidates (never
fabricated, never silent success). Search content is
UNTRUSTED data: it passes through byte-identical and is
quoted at the model boundary by the existing editorial
gate; nothing here grants tool authority.

Zero-cost: this adapter holds no keys, no accounts, no
purchase path, and no paid fallback. The backing MCP server
operator-configured (self-hosted SearXNG in the reference
deployment); a paid provider can never enter through this
path because the code has no such branch.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

from contracts import ExtractionResult, Identity, SourceItem
from g1.base import SourceAdapter
from g1.rss import canonical_url, content_hash
from mcp.client import McpClient, McpError
from mcp.registry import RegistryError, read_auth, resolve

CAPABILITY_FOR_TYPE = {
    "web": "web.search",
    "news": "news.search",
    "social.facebook": "social.facebook",
    "social.instagram": "social.instagram",
    "social.x": "social.x",
}


class SearchError(Exception):
    """Discovery failure. str() is a short machine reason
    (mirrors FeedError vocabulary where it overlaps):
    no-provider, ambiguous-capability, mcp-unavailable,
    tool-error:*, malformed-result, empty-query. Safe to
    store in monitoring state."""


def _excerpt(value) -> str:
    try:
        import json
        text = value if isinstance(value, str) else json.dumps(value)
    except Exception:
        return ""
    return (text or "")[:300]


def _domain(url: str) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower()
    except Exception:
        return ""


def _parse_dt(value: str):
    try:
        text = (value or "").strip()
        if not text:
            return None
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def discover(query: str, *, capability: str = "web.search",
             max_results: int = 10, servers=None,
             timeout: int = 25) -> tuple:
    """capability -> resolve -> McpClient -> raw result list.
    Returns (results, tool_name, server_id) for evidence.
    Raises SearchError on every failure mode (resolution,
    transport, tool error, malformed envelope). Zero results
    is a SUCCESS with an empty list (callers no-op)."""
    if not isinstance(query, str) or not query.strip():
        raise SearchError("empty-query")
    try:
        count = max(1, min(int(max_results or 10), 20))
    except (TypeError, ValueError):
        count = 10
    try:
        route = resolve(capability, servers=servers)
    except RegistryError as e:
        raise SearchError(
            "no-provider" if "no-provider" in str(e)
            else "mcp-unavailable")
    except Exception:
        raise SearchError("mcp-unavailable")
    try:
        client = McpClient(route["endpoint"],
                           auth_header=read_auth(
                               route.get("auth_env") or ""),
                           timeout=timeout)
        envelope = client.call_tool(
            route["tool"], {"query": query.strip(),
                            "max_results": count})
    except Exception as e:
        if isinstance(e, SearchError):
            raise
        if isinstance(e, McpError):
            raise SearchError(
                "tool-error:" + _excerpt(str(e))[:200])
        raise SearchError("mcp-unavailable")
    results = None
    try:
        if isinstance(envelope, dict):
            for item in envelope.get("content") or []:
                if isinstance(item, dict) and isinstance(
                        item.get("text"), str):
                    results = json.loads(item["text"])
                    break
    except Exception:
        results = None
    if results is None:
        raise SearchError("malformed-result")
    if not isinstance(results, list):
        raise SearchError("malformed-result")
    return results, route["tool"], route["server_id"]


def to_source_items(results: list, *, source_type: str,
                    capability: str, query: str,
                    discovered_at: str = "") -> list:
    """Raw results -> SourceItems. Drops anything without a
    usable http(s) URL, non-mapping rows, and rows whose text
    fields are all empty. Identity follows the shared rule
    (canonical URL, else content hash); provenance rides the
    Stage B fields. When a row carries an explicit stable
    platform id it is preserved as external_id (so the shared
    stable_identity() prefers platform:id); author prefers an
    explicit account/page field and falls back to the domain.
    Pure: no network, no store, no clock."""
    if source_type not in CAPABILITY_FOR_TYPE:
        raise SearchError(f"unknown-search-type:{source_type}")
    items: list = []
    for row in results or []:
        if not isinstance(row, dict):
            continue
        url = row.get("url") if isinstance(
            row.get("url"), str) else ""
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        title = row.get("title") if isinstance(
            row.get("title"), str) else ""
        snippet = row.get("content") if isinstance(
            row.get("content"), str) else ""
        if not title.strip() and not snippet.strip():
            continue
        author = row.get("author") if isinstance(
            row.get("author"), str) else ""
        author = author.strip() or _domain(url)
        external_id = row.get("id") if isinstance(
            row.get("id"), str) else ""
        external_id = external_id.strip()
        try:
            item_id = canonical_url(url) or ""
        except Exception:
            item_id = ""
        if not item_id:
            continue
        items.append(SourceItem(
            source_id=item_id, source_type=source_type,
            source_url=url, title=title.strip()[:200],
            text=snippet.strip()[:4000], images=[], links=[],
            published_at=row.get("publishedDate") if isinstance(
                row.get("publishedDate"), str) else "",
            platform=source_type, item_id=item_id,
            content_hash=content_hash(title, snippet),
            external_id=external_id, discovered_at=discovered_at or "",
            capability=capability, author=author))
    return items


def filter_fresh(items: list, max_age_days=None,
                 now: str = "") -> list:
    """Drop items with a parseable published_at older than
    max_age_days. Dateless items are KEPT (never dated by
    fiat); unparseable now or absent cutoff keeps everything.
    Pure."""
    try:
        cutoff = None
        if max_age_days is not None:
            days = int(max_age_days)
            if days >= 0:
                base = _parse_dt(now)
                if base is not None:
                    from datetime import timedelta
                    cutoff = base - timedelta(days=days)
        if cutoff is None:
            return list(items or [])
    except (TypeError, ValueError):
        return list(items or [])
    kept = []
    for item in items or []:
        pub = _parse_dt(getattr(item, "published_at", ""))
        if pub is not None and pub < cutoff:
            continue
        kept.append(item)
    return kept


class SearchAdapter(SourceAdapter):
    """Query-driven source type ("web"/"news"). Stateless like
    the other G1 adapters: fetch + map only (discover, not
    extract-by-URL). Dedup/cursors live in research/monitor.py
    over the same SourceItem contract. Failure -> partial
    ExtractionResult with reason, never raises."""

    def __init__(self, source_type: str = "web"):
        if source_type not in CAPABILITY_FOR_TYPE:
            raise SearchError(
                f"unknown-search-type:{source_type}")
        self.source_type = source_type

    def discover_items(self, query: str, n: int,
                       *, max_age_days=None, now: str = "",
                       servers=None) -> tuple:
        """Full Stage E path for one query: discover ->
        normalize -> freshness. Returns (items, meta) where
        meta carries capability/tool/server/query for evidence.
        Raises SearchError only on discovery failure (empty is
        success with zero items)."""
        capability = CAPABILITY_FOR_TYPE[self.source_type]
        results, tool, server_id = discover(
            query, capability=capability,
            max_results=max(1, int(n or 3)), servers=servers)
        items = to_source_items(
            results, source_type=self.source_type,
            capability=capability, query=query,
            discovered_at=now or "")
        fresh = filter_fresh(items, max_age_days, now)
        return fresh, {"capability": capability, "tool": tool,
                       "server": server_id, "query": query,
                       "returned": len(results),
                       "kept": len(fresh)}

    def extract(self, url: str, n: int) -> ExtractionResult:
        """SourceAdapter interface parity: search is queried,
        not extracted by URL. Always partial with a reason so
        URL-driven callers fail visibly instead of silently."""
        _ = (url, n)
        return ExtractionResult([], Identity(), {
            "requested": n, "returned": 0, "partial": True,
            "reason": "search-needs-query"})
