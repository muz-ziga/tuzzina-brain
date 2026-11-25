"""ApiAdapter: extract items from a JSON endpoint on one origin.

Generic: no domain-specific paths or field names beyond a small
set of conventional JSON keys. Optional same-origin CSRF bootstrap
uses /csrf-token and a cookie jar; only public, validated hosts
are contacted (shared g1.fetch boundary).
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

from contracts import ExtractionResult, Identity, SourceItem
from g1.base import SourceAdapter
from g1.fetch import FetchError, _HopCheckedRedirect, safe_get

UA = "tuzzina-brain/0.1 (+api)"
TIMEOUT = 25
MAX_BYTES = 1_000_000

_IMG = re.compile(r"https?://[^\s\"'<>]+?\.(?:png|jpe?g|webp|gif)(?:\?[^\s\"'<>]*)?", re.I)
_TITLE_KEYS = ("title", "name", "album", "track", "artist", "heading")
_TEXT_KEYS = ("text", "description", "summary", "body", "content", "bio")
_DATE_KEYS = ("published_at", "release_date", "created_at", "updated_at", "date")


def _flatten_selective(value, out: list, keys: tuple,
                     depth: int = 0) -> None:
    if depth > 8 or len(out) > 60:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if k not in keys:
                continue
            if isinstance(v, (dict, list)):
                _flatten_selective(v, out, keys, depth + 1)
            elif v not in (None, "", False, True):
                out.append(f"{k}: {v}")
    elif isinstance(value, list):
        for v in value[:5]:
            _flatten_selective(v, out, keys, depth + 1)


def _flatten(value, out: list, depth: int = 0) -> None:
    if depth > 12 or len(out) > 400:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                _flatten(v, out, depth + 1)
            elif v not in (None, "", False, True):
                out.append(f"{k}: {v}")
    elif isinstance(value, list):
        for v in value[:60]:
            if isinstance(v, (dict, list)):
                _flatten(v, out, depth + 1)
            elif v not in (None, "", False, True):
                out.append(str(v))


def _find_key(value, keys, found: list, depth: int = 0) -> None:
    if depth > 12 or len(found) >= 8:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if k in keys and isinstance(v, str) and v.strip():
                found.append(v.strip())
            else:
                _find_key(v, keys, found, depth + 1)
    elif isinstance(value, list):
        for v in value[:40]:
            _find_key(v, keys, found, depth + 1)


class ApiAdapter(SourceAdapter):
    source_type = "api"

    def extract(self, url: str, n: int, config: dict | None = None) -> ExtractionResult:
        config = config or {}
        headers = {"Accept": "application/json"}
        opener = None
        if config.get("csrf"):
            jar = CookieJar()
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(jar),
                _HopCheckedRedirect)
            p = urllib.parse.urlparse(url)
            origin = f"{p.scheme}://{p.netloc}"
            try:
                token_body, _, _ = safe_get(
                    origin + "/csrf-token", timeout=TIMEOUT,
                    max_bytes=MAX_BYTES,
                    allowed_content_types=("json", "text"),
                    user_agent=UA, opener=opener)
                token = json.loads(token_body.decode("utf-8", "replace")).get(
                    "csrf_token", "")
                if token:
                    headers["X-XSRF-TOKEN"] = str(token)
            except (FetchError, Exception):
                pass
            headers["Referer"] = url
            headers["Origin"] = origin
        try:
            body, final, _ = safe_get(
                url, timeout=TIMEOUT, max_bytes=MAX_BYTES,
                allowed_content_types=("json", "text"),
                user_agent=UA, headers=headers, opener=opener)
            data = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            return ExtractionResult([], Identity(), {
                "requested": n, "returned": 0, "partial": True,
                "reason": "api-extract-failed"})

        page = str(config.get("page") or "").strip()
        source_url = page or final or url
        source_id = page or url

        # Essential fields only: the research model cannot carry
        # 4000 chars of metadata. Every field here is a conventional
        # key any platform JSON may use; absent keys simply omit.
        essential_keys = ("name", "title", "album", "track", "artist",
                          "artists", "release_date", "description", "bio",
                          "summary", "id", "tracks_count")
        # The page URL is the item identity the research model must
        # quote in item_ids. Put it first so the model can see it.
        essential: list = [f"source_id: {source_url}"]
        _flatten_selective(data, essential, essential_keys)
        text = " | ".join(essential)[:250]
        title = ""
        published = ""
        found_titles = []
        _find_key(data, _TITLE_KEYS, found_titles)
        if found_titles:
            title = found_titles[0][:200]
        found_dates = []
        _find_key(data, _DATE_KEYS, found_dates)
        if found_dates:
            published = found_dates[0]
        imgs = []
        decoded = body.decode("utf-8", "replace").replace("\\/", "/")
        for m in _IMG.finditer(decoded):
            u = m.group(0)
            if u not in imgs:
                imgs.append(u)
            if len(imgs) >= 5:
                break
        item = SourceItem(
            source_id=source_id, source_type="api",
            source_url=source_url, title=title or source_id,
            text=text, images=imgs, links=[], platform="api",
            published_at=published)
        return ExtractionResult([item], Identity(), {
            "requested": n, "returned": 1, "partial": False,
            "reason": ""})
