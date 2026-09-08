"""RSS/Atom collector (G1). Stdlib only: urllib + xml.etree +
socket/ipaddress for SSRF guards. No feed library dependency.

What this is: fetch -> parse (RSS 2.x + Atom) -> normalize ->
stable identity + content hash. Collection only: no LLM, no
relevance decisions, no publishing.

Identity precedence (documented, stable across fetches):
  1. explicit feed GUID / Atom id (verbatim, stripped)
  2. canonical item link (scheme/host lowered, fragment dropped,
     trailing slash dropped, utm_* params dropped)
  3. content fallback: "hash:" + sha256(normalized title + text)

Failure model: fetch/parse problems raise FeedError(reason);
callers (RssAdapter, research monitor) convert to structured
meta instead of crashing. No secrets are logged (URLs only).
"""
from __future__ import annotations
import hashlib
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from contracts import ExtractionResult, Identity, SourceItem
from g1.base import SourceAdapter

UA = "tuzzina-brain/0.1 (+rss-discovery)"
TIMEOUT = 25
MAX_BYTES = 1_000_000
MAX_REDIRECTS = 5


class FeedError(Exception):
    """Fetch/parse failure. str() is a short machine reason
    (e.g. "timeout", "http-404", "malformed-xml", "oversized",
    "unsafe-url") — safe to store in monitoring state."""


@dataclass
class RssItem:
    item_id: str
    id_kind: str  # "guid" | "link" | "content"
    url: str
    title: str
    text: str
    published_at: str = ""
    updated_at: str = ""
    source_url: str = ""
    source_title: str = ""
    content_hash: str = ""
    thumbnail: str = ""


def _deny(reason: str) -> FeedError:
    return FeedError(reason)


def _check_url(url: str) -> urllib.parse.ParseResult:
    try:
        p = urllib.parse.urlparse((url or "").strip())
    except Exception:
        raise _deny("unsafe-url")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise _deny("unsafe-url")
    if "@" in (p.netloc or ""):
        raise _deny("unsafe-url")
    return p


def _resolve_host(host: str) -> None:
    """Refuse non-public targets. Literals are checked without DNS;
    names resolve (patched in tests) and every address must be
    globally routable. Raises FeedError on any refusal."""
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            raise _deny("dns-failed")
        ips = []
        for info in infos:
            try:
                ips.append(ipaddress.ip_address(info[4][0]))
            except Exception:
                continue
    if not ips:
        raise _deny("dns-failed")
    for ip in ips:
        if not ip.is_global:
            raise _deny("non-public-target")


class _CappedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        n = getattr(req, "_brain_redirects", 0) + 1
        if n > MAX_REDIRECTS:
            raise FeedError("too-many-redirects")
        new_req = super().redirect_request(req, fp, code, msg, headers,
                                           newurl)
        if new_req is None:
            return None
        new_req._brain_redirects = n  # noqa: SLF001 (stdlib req attr)
        return new_req


def _opener():
    """Opener with capped redirects. Module-level indirection so
    tests can substitute a deterministic double."""
    return urllib.request.build_opener(_CappedRedirect)


def _http_get(url: str) -> tuple[bytes, str]:
    p = _check_url(url)
    _resolve_host(p.hostname or "")
    opener = _opener()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            ctype = r.headers.get("Content-Type", "")
            if ctype and not any(
                    k in ctype.lower()
                    for k in ("xml", "rss", "atom", "text", "json")):
                raise _deny("unsupported-content-type")
            body = r.read(MAX_BYTES + 1)
    except FeedError:
        raise
    except urllib.error.HTTPError as e:
        raise _deny(f"http-{e.code}")
    except (urllib.error.URLError, TimeoutError, socket.timeout,
            ConnectionError, OSError) as e:
        name = type(e).__name__
        raise _deny("timeout" if "imeout" in name else "fetch-failed")
    if len(body) > MAX_BYTES:
        raise _deny("oversized")
    return body, ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(el) -> str:
    if el is None:
        return ""
    return "".join(el.itertext())


def _clean(s: str, limit: int) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def _rss_date(s: str) -> str:
    try:
        d = parsedate_to_datetime((s or "").strip())
        if d is None:
            return ""
        return d.isoformat()
    except Exception:
        return ""


def _atom_date(s: str) -> str:
    try:
        from datetime import datetime, timezone
        d = datetime.fromisoformat((s or "").strip().replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.isoformat()
    except Exception:
        return ""


def _child_text(el, *names: str) -> str:
    for child in el:
        if _local(child.tag) in names and not list(child):
            return (child.text or "").strip()
    for child in el:
        if _local(child.tag) in names:
            return _text(child).strip()
    return ""


def _atom_link(entry) -> str:
    fallback = ""
    for child in entry:
        if _local(child.tag) != "link":
            continue
        href = (child.get("href") or "").strip()
        if not href:
            continue
        if not fallback:
            fallback = href
        if (child.get("rel") or "alternate") == "alternate":
            return href
    return fallback


def _parse_rss(root) -> tuple[str, str, list]:
    channel = None
    for child in root:
        if _local(child.tag) == "channel":
            channel = child
            break
    if channel is None:
        raise _deny("malformed-xml")
    title = _child_text(channel, "title")
    link = _child_text(channel, "link")
    raws = []
    for item in channel:
        if _local(item.tag) != "item":
            continue
        summary = ""
        for child in item:
            if _local(child.tag) in ("encoded",):
                summary = _text(child)
                break
        if not summary:
            summary = _child_text(item, "description", "summary")
        raws.append({
            "title": _child_text(item, "title"),
            "link": _child_text(item, "link"),
            "guid": _child_text(item, "guid", "id"),
            "published": _rss_date(_child_text(item, "pubDate",
                                               "published", "updated",
                                               "date")),
            "updated": "",
            "summary": summary,
        })
    return title, link, raws


def _parse_atom(root) -> tuple[str, str, list]:
    title = _child_text(root, "title")
    feed_link = ""
    for child in root:
        if _local(child.tag) == "link" and \
                (child.get("rel") or "alternate") == "alternate":
            feed_link = (child.get("href") or "").strip()
            break
    raws = []
    for entry in root:
        if _local(entry.tag) != "entry":
            continue
        content = ""
        videoid = ""
        description = ""
        thumbnail = ""
        for child in entry:
            local = _local(child.tag)
            if local == "content" and not content:
                content = _text(child)
            elif local == "videoId" and not videoid:
                # yt:videoId: preferred stable identity when present
                videoid = (child.text or "").strip()
            elif local == "group":
                # media:group (Media RSS): description + thumbnail
                for g in child:
                    glocal = _local(g.tag)
                    if glocal == "description" and not description:
                        description = _text(g).strip()
                    elif glocal == "thumbnail" and not thumbnail:
                        thumbnail = (g.get("url") or "").strip()
        raws.append({
            "title": _child_text(entry, "title"),
            "link": _atom_link(entry),
            "guid": videoid or _child_text(entry, "id"),
            "published": _atom_date(_child_text(entry, "published")),
            "updated": _atom_date(_child_text(entry, "updated")),
            "summary": content or _child_text(entry, "summary") or
            description,
            "thumbnail": thumbnail,
        })
    return title, feed_link, raws


def parse_feed(data: bytes) -> tuple[str, str, list]:
    """Parse raw feed bytes. Returns (feed_title, feed_link, raw
    item dicts). Raises FeedError("malformed-xml" | "empty-feed")."""
    try:
        root = ET.fromstring(data)
    except Exception:
        raise _deny("malformed-xml")
    kind = _local(root.tag).lower()
    if kind == "rss":
        title, link, raws = _parse_rss(root)
    elif kind == "feed":
        title, link, raws = _parse_atom(root)
    else:
        raise _deny("malformed-xml")
    if not raws:
        raise _deny("empty-feed")
    return title.strip(), link.strip(), raws


def canonical_url(url: str) -> str:
    """Lower scheme/host, drop fragment, drop trailing slash, drop
    utm_* params. Non-destructive otherwise; unparseable -> ""."""
    try:
        p = urllib.parse.urlparse((url or "").strip())
        if p.scheme not in ("http", "https") or not p.hostname:
            return ""
        netloc = p.hostname.lower()
        if p.port and ((p.scheme == "http" and p.port != 80) or
                       (p.scheme == "https" and p.port != 443)):
            netloc = f"{netloc}:{p.port}"
        path = (p.path or "").rstrip("/") or ""
        keep = [(k, v) for k, v in
                urllib.parse.parse_qsl(p.query, keep_blank_values=True)
                if not k.lower().startswith("utm_")]
        query = urllib.parse.urlencode(keep)
        return urllib.parse.urlunparse(
            (p.scheme.lower(), netloc, path, "", query, ""))
    except Exception:
        return ""


def content_hash(title: str, text: str) -> str:
    return hashlib.sha256(
        f"{title}\n{text}".encode("utf-8")).hexdigest()


def normalize_item(raw: dict, source_url: str,
                   source_title: str = "") -> RssItem:
    """Raw parsed dict -> normalized RssItem with stable identity."""
    title = _clean(raw.get("title") or "", 300)
    text = _clean(raw.get("summary") or "", 5000)
    guid = (raw.get("guid") or "").strip()
    link = canonical_url(raw.get("link") or "")
    if guid:
        item_id, kind = guid, "guid"
    elif link:
        item_id, kind = link, "link"
    else:
        item_id = "hash:" + content_hash(title, text)
        kind = "content"
    return RssItem(
        item_id=item_id, id_kind=kind, url=link,
        title=title, text=text,
        published_at=raw.get("published") or "",
        updated_at=raw.get("updated") or "",
        source_url=source_url, source_title=source_title,
        content_hash=content_hash(title, text),
        thumbnail=(raw.get("thumbnail") or "").strip())


def fetch_feed(url: str) -> tuple[list, str, str]:
    """Fetch + parse + normalize. Returns (RssItems, feed_title,
    feed_link). Raises FeedError on any failure."""
    body, _ = _http_get(url.strip())
    title, link, raws = parse_feed(body)
    return ([normalize_item(r, url.strip(), title) for r in raws],
            title, link)


def to_source_item(it: RssItem, source_type: str, platform: str,
                   url_fallback: str) -> SourceItem:
    """Shared RssItem -> SourceItem mapping. Thumbnails (when the
    feed provides them, e.g. YouTube media:thumbnail) ride the
    existing images list; no new media model."""
    images = [it.thumbnail] if it.thumbnail else []
    return SourceItem(
        source_id=it.item_id, source_type=source_type,
        source_url=it.url or url_fallback,
        title=it.title[:200], text=it.text[:4000],
        images=images, links=[],
        published_at=it.published_at, platform=platform,
        item_id=it.item_id, content_hash=it.content_hash)


class RssAdapter(SourceAdapter):
    """RSS/Atom as a G1 source type. Stateless like WebsiteAdapter:
    no dedup, no cursors (monitoring lives in research/monitor.py).
    Failure -> partial ExtractionResult with reason, never raises."""
    source_type = "rss"

    def extract(self, url: str, n: int) -> ExtractionResult:
        n = max(1, int(n))
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
        out: list[SourceItem] = []
        for it in items[:n]:
            out.append(to_source_item(it, "rss", "rss", url.strip()))
        host = urllib.parse.urlparse(url.strip()).netloc
        return ExtractionResult(out, Identity(
            name=(title or host).strip()), {
                "requested": n, "returned": len(out),
                "partial": len(out) < n,
                "reason": "" if len(out) >= n else "fewer-items-found"})
