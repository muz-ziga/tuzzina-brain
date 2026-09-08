"""YouTube public channel collector (G1 source type "youtube").

Source: the public channel Atom feed (live-proven R2):
  https://www.youtube.com/feeds/videos.xml?channel_id=<CHANNEL_ID>
No key, no OAuth, no Google API. Stdlib only; all HTTP safety,
parsing, identity, and monitoring reuse R1 (g1/rss.py +
research/monitor.py) unchanged.

KNOWN LIMIT (documented, no workaround in this step): the feed
carries at most ~15 recent entries with no pagination. Videos
published beyond the returned window between polls can be missed;
the monitor surfaces exactly what the feed returned.

Channel identification: explicit channel_id ONLY
(^UC[A-Za-z0-9_-]{22}$). No @handle resolution, no search, no
lookup. The caller (operator config today, UI tomorrow) supplies
the id; anything else is refused before any network happens, so
no arbitrary URL can reach the fetcher through this adapter.
"""
from __future__ import annotations
import re

from contracts import ExtractionResult, Identity
from g1.base import SourceAdapter
from g1.rss import FeedError, fetch_feed, to_source_item

CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

FEED_TEMPLATE = ("https://www.youtube.com/feeds/videos.xml"
                 "?channel_id={cid}")


def youtube_feed_url(channel_id: str) -> str:
    """Validated feed URL for a channel. Raises ValueError on any
    other input (fail-fast at configuration time, before network)."""
    cid = (channel_id or "").strip()
    if not CHANNEL_ID_RE.match(cid):
        raise ValueError("invalid-channel-id")
    return FEED_TEMPLATE.format(cid=cid)


class YouTubeFeedAdapter(SourceAdapter):
    """Known-channel monitoring. Stateless like the other G1
    adapters: fetch + map only. Dedup/cursors live in
    research/monitor.py over the same RssItem contract
    (callers use youtube_feed_url + source_id f"youtube:{cid}").
    Failure -> partial ExtractionResult with reason, never raises.
    """
    source_type = "youtube"

    def extract(self, channel_id: str, n: int) -> ExtractionResult:
        """`channel_id` (not a URL): validated, then mapped to the
        single fixed feed template. Arbitrary URLs cannot pass."""
        n = max(1, int(n))
        try:
            url = youtube_feed_url(channel_id)
        except ValueError:
            return ExtractionResult([], Identity(), {
                "requested": n, "returned": 0, "partial": True,
                "reason": "invalid-channel-id"})
        try:
            items, title, _ = fetch_feed(url)
        except FeedError as e:
            return ExtractionResult([], Identity(), {
                "requested": n, "returned": 0, "partial": True,
                "reason": str(e)})
        except Exception:
            return ExtractionResult([], Identity(), {
                "requested": n, "returned": 0, "partial": True,
                "reason": "fetch-failed"})
        out = [to_source_item(it, "youtube", "youtube", url)
               for it in items[:n]]
        return ExtractionResult(out, Identity(
            name=(title or channel_id).strip()), {
                "requested": n, "returned": len(out),
                "partial": len(out) < n,
                "reason": "" if len(out) >= n else "fewer-items-found"})
