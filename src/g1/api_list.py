"""ApiListAdapter: extract many items from a paginated JSON endpoint.

The JSON contract is conventional, not domain-specific:

    {"pagination": {"data": [row, ...], "next_page": 2, ...}}

Rows are mapped to SourceItem with the page URL as identity, the
row's `model_type` (or the source's declared `class`) as the content
class, its image as the official artwork, and a short flattened text
of the conventional fields. A response without `pagination.data`
behaves like one single row, so the adapter never loses the old
single-object shape.

The only URLs fetched are the configured endpoint and its `page`
parameter; every request goes through the shared g1.fetch boundary.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar

from contracts import ExtractionResult, Identity, SourceItem
from g1.base import SourceAdapter
from g1.fetch import _HopCheckedRedirect, safe_get

UA = "tuzzina-brain/0.1 (+api-list)"
TIMEOUT = 25
MAX_BYTES = 1_000_000

_DEFAULT_TEXT_KEYS = ("name", "title", "description", "bio", "summary")
_IMAGE_KEYS = ("image", "image_small", "cover")


def _parse_dt(value: str):
    try:
        parsed = datetime.fromisoformat(
            str(value or "").strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _older(value: str, cursor: str) -> bool:
    if not cursor:
        return False
    left, right = _parse_dt(value), _parse_dt(cursor)
    if left is None or right is None:
        return False
    return left < right


def _page_url(url: str, page) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(
        parsed.query, keep_blank_values=True) if k != "page"]
    query.append(("page", str(page)))
    return urllib.parse.urlunsplit((
        parsed.scheme, parsed.netloc, parsed.path,
        urllib.parse.urlencode(query), parsed.fragment))


def _fetch_json(url: str, config: dict):
    """One JSON GET through the shared security boundary. CSRF is the
    same optional bootstrap the single-object adapter uses."""
    headers = {"Accept": "application/json"}
    opener = None
    if config.get("csrf"):
        jar = CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar),
            _HopCheckedRedirect)
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
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
        except Exception:
            pass
        headers["Referer"] = url
        headers["Origin"] = origin
    body, final, _ = safe_get(
        url, timeout=TIMEOUT, max_bytes=MAX_BYTES,
        allowed_content_types=("json", "text"),
        user_agent=UA, headers=headers, opener=opener)
    return json.loads(body.decode("utf-8", "replace")), final or url


def _rows(data) -> list:
    if isinstance(data, dict):
        pagination = data.get("pagination")
        if isinstance(pagination, dict) and isinstance(
                pagination.get("data"), list):
            return pagination["data"]
        if isinstance(data.get("data"), list):
            return data["data"]
    return [data]


def _next_page(data):
    if not isinstance(data, dict):
        return None
    pagination = data.get("pagination")
    if not isinstance(pagination, dict):
        return None
    return pagination.get("next_page")


def _row_class(row: dict, config: dict) -> str:
    declared = str(config.get("class") or "").strip()
    if declared:
        return declared
    return str(row.get("type") or row.get("kind")
               or row.get("model_type") or "").strip()


def _row_page(row: dict, config: dict, cls: str) -> str:
    template = str(config.get("page_template") or "").strip()
    row_id = row.get("id")
    if template and row_id not in (None, ""):
        return (template.replace("{class}", cls)
                .replace("{id}", str(row_id)))
    return str(config.get("page") or "").strip()


def _row_text(row: dict, page: str, config: dict) -> str:
    parts = [f"source_id: {page}"]
    keys = config.get("text_keys")
    if not isinstance(keys, list) or not keys:
        keys = list(_DEFAULT_TEXT_KEYS)
    for key in keys:
        if not isinstance(key, str) or not key.strip():
            continue
        value = row.get(key.strip())
        if isinstance(value, str) and value.strip():
            parts.append(f"{key.strip()}: {value.strip()}")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parts.append(f"{key.strip()}: {value}")
    nested = config.get("nested_text_keys")
    if isinstance(nested, list):
        for key in nested:
            if not isinstance(key, str) or not key.strip():
                continue
            value = row.get(key.strip())
            names = []
            if isinstance(value, dict):
                for name_key in ("name", "title", "display_name"):
                    if isinstance(value.get(name_key), str) \
                            and value[name_key].strip():
                        names.append(value[name_key].strip())
                        break
            elif isinstance(value, list):
                for entry in value[:5]:
                    if not isinstance(entry, dict):
                        continue
                    for name_key in ("name", "title", "display_name"):
                        if isinstance(entry.get(name_key), str) \
                                and entry[name_key].strip():
                            names.append(entry[name_key].strip())
                            break
            if names:
                parts.append(f"{key.strip()}: " + ", ".join(names))
    return " | ".join(parts)[:1000]


def _row_images(row: dict) -> list:
    out = []
    for key in _IMAGE_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip() and value not in out:
            out.append(value.strip())
    for artist in (row.get("artists") or [])[:1]:
        if isinstance(artist, dict):
            value = artist.get("image_small")
            if isinstance(value, str) and value.strip() and value not in out:
                out.append(value.strip())
    return out


def _row_author(row: dict) -> str:
    artists = row.get("artists") or []
    if artists and isinstance(artists[0], dict):
        return str(artists[0].get("name") or "").strip()
    return ""


def _to_item(row, config: dict) -> SourceItem | None:
    if not isinstance(row, dict):
        return None
    if row.get("disabled") is True:
        return None
    cls = _row_class(row, config)
    page = _row_page(row, config, cls)
    title = str(row.get("name") or row.get("title") or "").strip()
    if not page or not title:
        return None
    return SourceItem(
        source_id=page, source_type="api-list", source_url=page,
        title=title[:200], text=_row_text(row, page, config),
        images=_row_images(row), links=[], hashtags=[],
        published_at=str(row.get("created_at")
                         or row.get("release_date") or ""),
        platform="api-list", item_id=page,
        external_id=("%s:%s" % (cls, row.get("id")))
        if row.get("id") is not None else "",
        author=_row_author(row), content_class=cls)


class ApiListAdapter(SourceAdapter):
    source_type = "api-list"

    def extract(self, url: str, n: int, config: dict | None = None,
                known_ids=None, cursor: str = ""):
        """Fetch a newest-first list incrementally.

        `known_ids` and `cursor` are optional scan boundaries: after a
        page containing a known item or an item older than the cursor,
        the adapter stops. The whole page is still returned so a new
        item never gets skipped because an old one shares its page.
        `meta.scan_complete` is False only when n/max_pages cut the
        scan before a boundary."""
        config = config or {}
        limit = max(1, int(n or 1))
        max_pages = max(1, int(config.get("max_pages") or 1))
        known = {str(x) for x in (known_ids or []) if str(x).strip()}
        items = []
        seen = set()
        page_url = url
        partial = False
        reason = ""
        scan_complete = False
        for page_index in range(max_pages):
            try:
                data, final = _fetch_json(page_url, config)
            except Exception:
                partial = True
                reason = "api-list-extract-failed"
                break
            rows = _rows(data)
            stop = False
            for row in rows:
                item = _to_item(row, config)
                if item is None or item.item_id in seen:
                    continue
                seen.add(item.item_id)
                items.append(item)
                if item.item_id in known:
                    stop = True
                if _older(item.published_at, cursor):
                    stop = True
                if len(items) >= limit:
                    break
            if stop:
                scan_complete = True
                break
            if len(items) >= limit:
                break
            next_page = _next_page(data)
            if next_page in (None, "", 0):
                scan_complete = True
                break
            page_url = _page_url(url, next_page)
        return ExtractionResult(
            items, Identity(),
            {"requested": limit, "returned": len(items),
             "partial": partial, "reason": reason,
             "scan_complete": scan_complete})
