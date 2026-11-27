"""Deterministic content-wheel planning.

Pure functions only: the caller supplies the published-topic ledger
and the campaign's wheel declaration. No I/O, no clock, no model.

The wheel is campaign data:

    wheel:
      pattern: [identity, identity, content, ...]
      content_classes: [artist, track, album]
      start_slot: 0

`content` is a bucket, not a class: the next content class comes from
`content_classes` in order. `identity` is a bucket whose class is the
literal string "identity". Nothing here knows a campaign, an artist or
a platform.
"""
from __future__ import annotations

import re

IDENTITY = "identity"
CONTENT = "content"

# The identity bucket name is the wheel's own vocabulary; a campaign
# may rename it through identity_classes. Content classes are always
# campaign data (no built-in artist/album/track list).
DEFAULT_IDENTITY_CLASSES = ("identity",)


def is_published(entry) -> bool:
    """A ledger entry counts for the wheel only when it reached a
    post. Missing state means the historical published format."""
    if not isinstance(entry, dict):
        return False
    state = str(entry.get("state") or "published").strip().lower()
    return state not in ("draft", "scheduled", "failed")


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _id_of(item) -> str:
    return (getattr(item, "item_id", "") or
            getattr(item, "source_id", "") or
            getattr(item, "source_url", ""))


def _pattern(wheel: dict) -> list:
    raw = wheel.get("pattern")
    if not isinstance(raw, list) or not raw:
        raise ValueError("wheel.pattern must be a non-empty list")
    out = [str(x).strip().lower() for x in raw]
    if any(x not in (IDENTITY, CONTENT) for x in out):
        raise ValueError("wheel.pattern entries must be identity|content")
    return out


def _content_classes(wheel: dict) -> list:
    raw = wheel.get("content_classes")
    if not isinstance(raw, list):
        raise ValueError("wheel.content_classes must be a list")
    out = [str(x).strip() for x in raw if str(x).strip()]
    if not out:
        raise ValueError("wheel.content_classes must be non-empty")
    return out


def _identity_classes(wheel: dict) -> list:
    raw = wheel.get("identity_classes")
    if raw is None:
        return list(DEFAULT_IDENTITY_CLASSES)
    if not isinstance(raw, list):
        raise ValueError("wheel.identity_classes must be a list")
    out = [str(x).strip() for x in raw if str(x).strip()]
    if not out:
        raise ValueError("wheel.identity_classes must be non-empty")
    return out


def plan_next(recent, wheel: dict):
    """Return the next wheel slot.

    `recent` is newest-first, exactly as the published-topics ledger
    returns it. The slot advances only from a PUBLISHED entry that
    carries `wheel_slot`; drafts and historical entries without a slot
    never move the wheel. A campaign that has never recorded a slot
    starts at `start_slot` (default 0).
    """
    if not isinstance(wheel, dict):
        return None
    pattern = _pattern(wheel)
    content_classes = _content_classes(wheel)
    identity_classes = _identity_classes(wheel)
    window = len(pattern)
    published = [e for e in (recent or []) if is_published(e)]

    last_slot = None
    for entry in published:
        slot = _int(entry.get("wheel_slot"))
        if slot is not None and 0 <= slot < window:
            last_slot = slot
            break
    if last_slot is None:
        start = _int(wheel.get("start_slot"))
        slot = (start if start is not None else 0) % window
    else:
        slot = (last_slot + 1) % window

    bucket = pattern[slot]
    if bucket == IDENTITY:
        content_class = identity_classes[0]
        accept = list(identity_classes)
    else:
        content_posts = 0
        for entry in published:
            if _int(entry.get("wheel_slot")) is None:
                continue
            if str(entry.get("wheel_bucket") or "").strip().lower() \
                    == CONTENT:
                content_posts += 1
        content_class = content_classes[content_posts % len(content_classes)]
        accept = [content_class]

    return {
        "slot": slot,
        "bucket": bucket,
        "class": content_class,
        "accept": accept,
        "window": window,
        "pattern": list(pattern),
        "content_classes": list(content_classes),
        "identity_classes": list(identity_classes),
    }


def fallback_class(plan: dict, wheel: dict) -> str:
    """The other bucket's class, used when the planned class has no
    selectable item. Content falls back to identity; identity falls
    back to the first content class."""
    if not isinstance(plan, dict):
        return IDENTITY
    if str(plan.get("bucket") or "").lower() == CONTENT:
        classes = _identity_classes(wheel or {})
        return classes[0]
    classes = _content_classes(wheel or {})
    return classes[0]


def select_lru(pool, recent, required_class: str):
    """Least-recently-featured item of one class.

    Items never recorded in the ledger are preferred in pool order;
    otherwise the oldest recorded `at` wins (ties keep pool order).
    Only published entries count; drafts never consume an item.
    """
    wanted = str(required_class or "").strip()
    if not wanted:
        return None
    unique = []
    seen = set()
    for item in pool or []:
        if getattr(item, "content_class", "") != wanted:
            continue
        item_id = _id_of(item)
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        unique.append(item)
    if not unique:
        return None
    last_at = {}
    for entry in recent or []:
        if not is_published(entry):
            continue
        at = str(entry.get("at") or "")
        for raw in (entry.get("item_ids") or []):
            item_id = str(raw or "").strip()
            if item_id and item_id not in last_at:
                last_at[item_id] = at
    # Historical rows may predate item_ids. Their topic/angle still
    # names the work, so the item's own title is a safe fallback
    # signal that it was already featured. This never invents an id;
    # it only marks an item the ledger already described.
    def _norm(value):
        return " ".join(
            part for part in
            re.sub(r"[^\w\u0600-\u06FF]+", " ",
                   str(value or "")).lower().split()
            if part)
    for entry in recent or []:
        if not is_published(entry):
            continue
        at = str(entry.get("at") or "")
        blob = _norm("%s %s" % (entry.get("topic") or "",
                                entry.get("angle") or ""))
        if not blob:
            continue
        for item in unique:
            title = _norm(getattr(item, "title", ""))
            if len(title) < 3:
                continue
            if title in blob and _id_of(item) not in last_at:
                last_at[_id_of(item)] = at
    unused = [it for it in unique if _id_of(it) not in last_at]
    if unused:
        return unused[0]
    return min(unique, key=lambda it: last_at.get(_id_of(it), ""))
