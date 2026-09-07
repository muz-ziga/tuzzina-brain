"""WebsiteAdapter: extract up to N content items from a website.
Stdlib only (urllib + html.parser). No JS rendering: static HTML only.
"""
from __future__ import annotations
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from contracts import ExtractionResult, Identity, SourceItem
from g1.base import SourceAdapter

UA = "tuzzina-brain/0.1 (+discovery)"
TIMEOUT = 25
MAX_BYTES = 3_000_000


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.metas = {}
        self.links = []      # (href, text)
        self._a_href = None
        self._a_text = ""
        self.imgs = []       # (src, alt)
        self.text_parts = []
        self._skip = 0       # script/style depth

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name and a.get("content"):
                self.metas[name] = a["content"]
        elif tag == "a" and a.get("href"):
            self._a_href = a["href"]
            self._a_text = ""
        elif tag == "img" and a.get("src"):
            self.imgs.append((a["src"], a.get("alt", "")))
        elif tag == "link" and "icon" in (a.get("rel", "")):
            self.metas["__icon__"] = a.get("href", "")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._a_href is not None:
            t = self._a_text.strip()
            if len(t) > 15:
                self.links.append((self._a_href, t))
            self._a_href = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        else:
            s = data.strip()
            if s:
                self.text_parts.append(s)
            if self._a_href is not None:
                self._a_text += data + " "


def _fetch(url: str) -> tuple[str, str]:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=TIMEOUT) as r:
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "text" not in ctype and ctype:
            return "", ""
        body = r.read(MAX_BYTES)
    enc = "utf-8"
    m = re.search(rb"charset=([\w-]+)", body[:2000])
    if m:
        try:
            enc = m.group(1).decode("ascii")
        except Exception:
            pass
    try:
        return body.decode(enc, errors="replace"), r.url
    except Exception:
        return "", ""


def _parse(html: str) -> _Page:
    p = _Page()
    try:
        p.feed(html)
    except Exception:
        pass
    return p


def _abs(base: str, ref: str) -> str:
    try:
        u = urljoin(base, ref)
        return u if u.startswith(("http://", "https://")) else ""
    except Exception:
        return ""


def _is_article_link(page_url: str, href: str, text: str) -> bool:
    try:
        if urlparse(href).netloc != urlparse(page_url).netloc:
            return False
    except Exception:
        return False
    low = href.lower()
    if any(x in low for x in ("/tag/", "/category/", "/author/", "/page/",
                              "#", "?", ".css", ".js", ".ico", ".png",
                              ".jpg", "login", "signup", "privacy", "terms")):
        return False
    return len(text) > 25


class WebsiteAdapter(SourceAdapter):
    source_type = "website"

    def extract(self, url: str, n: int) -> ExtractionResult:
        n = max(1, int(n))
        html, final_url = _fetch(url)
        if not html:
            return ExtractionResult([], Identity(), {
                "requested": n, "returned": 0, "partial": True,
                "reason": "fetch-failed-or-not-html"})
        page = _parse(html)
        host = urlparse(final_url or url).netloc
        identity = Identity(
            name=(page.title or host).strip(),
            description=(page.metas.get("description")
                         or page.metas.get("og:description", ""))[:500],
            language=(page.metas.get("content-language")
                      or page.metas.get("og:locale", ""))[:16],
            logo_url=_abs(final_url or url, page.metas.get("__icon__", "")),
            colors=[c for c in [page.metas.get("theme-color")] if c],
        )
        seen, cands = set(), []
        for href, text in page.links:
            full = _abs(final_url or url, href)
            if full and full not in seen and _is_article_link(
                    final_url or url, full, text):
                seen.add(full)
                cands.append((full, text))
            if len(cands) >= n:
                break
        items: list[SourceItem] = []
        if not cands:  # single page: the page itself is the item
            body = " ".join(page.text_parts)
            imgs = [u for u, _ in
                    ((_abs(final_url or url, s), a) for s, a in page.imgs)
                    if u][:5]
            items.append(SourceItem(
                source_id=final_url or url, source_type="website",
                source_url=final_url or url, title=page.title.strip()[:200],
                text=re.sub(r"\s+", " ", body)[:4000], images=imgs,
                links=[], platform="website",
                published_at=page.metas.get("article:published_time", "")))
        else:
            for link, anchor in cands[:n]:
                try:
                    ahtml, afinal = _fetch(link)
                    if not ahtml:
                        continue
                    ap = _parse(ahtml)
                    body = " ".join(ap.text_parts)
                    imgs = [u for u, _ in
                            ((_abs(afinal or link, s), a)
                             for s, a in ap.imgs) if u][:5]
                    items.append(SourceItem(
                        source_id=afinal or link, source_type="website",
                        source_url=afinal or link,
                        title=(ap.title.strip() or anchor)[:200],
                        text=re.sub(r"\s+", " ", body)[:4000], images=imgs,
                        links=[], platform="website",
                        published_at=ap.metas.get(
                            "article:published_time", "")))
                except Exception:
                    continue
        return ExtractionResult(items, identity, {
            "requested": n, "returned": len(items),
            "partial": len(items) < n,
            "reason": "" if len(items) >= n else "fewer-articles-found"})
