"""Monitoring state for collectors. Domain contract only: no
database, no network, no clock. The persistence backend stays
replaceable behind StateStore (tests use MemoryStateStore).

Lifecycle (explicit, loss-free):
  CHECK (load state, fetch feed)
  -> COLLECT (normalize all candidates)
  -> CLASSIFY (NEW | UNCHANGED | UPDATED against staged state)
  -> RETURN (CollectionResult; store NOT yet touched)
  -> COMMIT (result.commit(store) persists cursor + errors)

Cursor advances only on commit. A crash between RETURN and COMMIT
re-delivers the same items as NEW/UPDATED on the next run: at-least-
once, never silent loss. UPDATED is classified distinctly and is
never auto-treated as a new publication.

Kinds: NEW (identity never seen), UNCHANGED (seen + same hash),
UPDATED (seen + changed hash; hash refreshed on commit).
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from g1.rss import FeedError, fetch_feed

NEW = "NEW"
UNCHANGED = "UNCHANGED"
UPDATED = "UPDATED"


@dataclass
class SourceState:
    """Per-source monitoring cursor. Plain data (dicts/strings) so
    any future store (file, DB) can serialize it without changes.
    `seen` maps item_id -> {"hash": content_hash,
    "first_seen": iso}. `last_seen_*` is an advisory high-water
    mark; the seen-map is the dedup authority."""
    source_id: str = ""
    last_seen_item_id: str = ""
    last_seen_published_at: str = ""
    seen: dict = field(default_factory=dict)
    first_seen_at: str = ""
    last_checked_at: str = ""
    last_error: str = ""


class StateStore(ABC):
    @abstractmethod
    def load(self, source_id: str) -> SourceState:
        """Return stored state; unknown source -> fresh state."""

    @abstractmethod
    def save(self, state: SourceState) -> None:
        """Persist state (called only via CollectionResult.commit)."""


class MemoryStateStore(StateStore):
    """In-process store. Tests and single-run demos; holds deep
    copies so callers can never mutate stored state by accident."""

    def __init__(self):
        self._data: dict[str, SourceState] = {}

    def load(self, source_id: str) -> SourceState:
        st = self._data.get(source_id)
        if st is None:
            return SourceState(source_id=source_id)
        return deepcopy(st)

    def save(self, state: SourceState) -> None:
        self._data[state.source_id] = deepcopy(state)


@dataclass
class ClassifiedItem:
    item: object  # RssItem
    kind: str


@dataclass
class CollectionResult:
    source_id: str
    items: list = field(default_factory=list)
    checked_at: str = ""
    error: str = ""
    _pending: object = None  # SourceState staged for commit

    def new_items(self) -> list:
        return [c.item for c in self.items if c.kind == NEW]

    def commit(self, store: StateStore) -> None:
        """Persist the staged cursor. The ONLY write path."""
        if self._pending is None:
            raise ValueError("nothing staged to commit")
        store.save(self._pending)


def _parse_dt(s: str):
    try:
        d = datetime.fromisoformat((s or "").strip().replace(
            "Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def collect(source: dict, *, store: StateStore, now: str,
            horizon_days=None, max_items: int = 50) -> CollectionResult:
    """One polling step. Pure apart from fetch (network) and the
    returned commit closure. Never touches `store` itself."""
    url = (source.get("url") or "").strip()
    source_id = (source.get("source_id") or "").strip() or url
    state = store.load(source_id)
    pending = deepcopy(state)
    pending.last_checked_at = now
    if not pending.first_seen_at:
        pending.first_seen_at = now
    try:
        items, _title, _link = fetch_feed(url)
    except FeedError as e:
        pending.last_error = str(e)
        return CollectionResult(source_id, [], now, str(e), pending)
    except Exception:
        pending.last_error = "fetch-failed"
        return CollectionResult(source_id, [], now, "fetch-failed",
                                pending)
    cutoff = None
    if horizon_days is not None:
        try:
            base = _parse_dt(now)
            if base is not None and int(horizon_days) >= 0:
                cutoff = base - timedelta(days=int(horizon_days))
        except Exception:
            cutoff = None
    out: list[ClassifiedItem] = []
    for it in items[:max(0, int(max_items))]:
        if cutoff is not None and it.published_at:
            dt = _parse_dt(it.published_at)
            if dt is not None and dt < cutoff:
                continue  # outside the polling window: not new,
                # not marked (window is fixed, so it stays excluded)
        prev = pending.seen.get(it.item_id)
        if prev is None:
            pending.seen[it.item_id] = {"hash": it.content_hash,
                                        "first_seen": now}
            if it.published_at and (
                    not pending.last_seen_published_at or
                    it.published_at > pending.last_seen_published_at):
                pending.last_seen_published_at = it.published_at
                pending.last_seen_item_id = it.item_id
            out.append(ClassifiedItem(it, NEW))
        elif not isinstance(prev, dict) or \
                prev.get("hash") != it.content_hash:
            pending.seen[it.item_id] = {"hash": it.content_hash,
                                        "first_seen": (prev.get(
                                            "first_seen", now)
                                            if isinstance(
                                                prev, dict) else now)}
            out.append(ClassifiedItem(it, UPDATED))
        else:
            out.append(ClassifiedItem(it, UNCHANGED))
    pending.last_error = ""
    return CollectionResult(source_id, out, now, "", pending)
