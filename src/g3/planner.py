"""G3 Content Planner: decisions only. Pure function, no IO, no state.
Input: content packages + schedule config. Output: PlannedPost list.

Schedule config keys (all optional; documented defaults apply):
  days: [monday..sunday] | timezone | times: ["HH:MM"]
  daily_count | weekly_count | monthly_count (caps on slots produced)
  start_time / end_time ("HH:MM" daily window, slots inside only)
  spacing_minutes (min gap between consecutive slots)

G3 computes slots. Postiz executes scheduling/state/publishing.
No cron, no queue, no runtime here.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from contracts import ContentPackage, PlannedPost

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


def _tz(name: str):
    if ZoneInfo and name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone.utc


def _parse_time(s: str) -> tuple[int, int]:
    h, _, m = str(s).partition(":")
    try:
        return max(0, min(23, int(h or 9))), max(0, min(59, int(m or 0)))
    except ValueError:
        return 9, 0


def _as_int(v, default=None):
    try:
        i = int(v)
        return i if i >= 0 else default
    except (TypeError, ValueError):
        return default


def next_slots(days: list, times: list, tzname: str, count: int,
               now: datetime | None = None, start_time: str = "",
               end_time: str = "",
               spacing_minutes=None) -> list[str]:
    """Upcoming datetimes matching days/times inside the daily
    [start_time, end_time] window with >= spacing_minutes between
    consecutive slots. Empty days/times -> one slot per day at 09:00
    starting tomorrow (documented default)."""
    tz = _tz(tzname or "UTC")
    now = now or datetime.now(tz)
    days = [d.lower() for d in (days or [])]
    times = times or ["09:00"]
    spacing = _as_int(spacing_minutes, 0) or 0
    wd = ["monday", "tuesday", "wednesday", "thursday", "friday",
          "saturday", "sunday"]
    ws, we = None, None
    if start_time:
        ws = _parse_time(start_time)
    if end_time:
        we = _parse_time(end_time)
    out: list[str] = []
    last: datetime | None = None
    cursor = now
    guard = 0
    while len(out) < count and guard < 370:
        guard += 1
        day = cursor + timedelta(days=1) if guard > 1 else cursor
        cursor = day
        if days and wd[day.weekday()] not in days:
            continue
        for t in times:
            h, mi = _parse_time(t)
            slot = day.replace(hour=h, minute=mi, second=0,
                               microsecond=0)
            if slot <= now:
                continue
            if ws and (slot.hour, slot.minute) < ws:
                continue
            if we and (slot.hour, slot.minute) > we:
                continue
            if last is not None and spacing > 0 and \
                    (slot - last) < timedelta(minutes=spacing):
                continue
            out.append(slot.isoformat())
            last = slot
            if len(out) >= count:
                break
    return out


def plan(packages: list[ContentPackage], schedule: dict) -> list[PlannedPost]:
    sched = schedule or {}
    want = len(packages)
    for cap_key in ("daily_count", "weekly_count", "monthly_count"):
        cap = _as_int(sched.get(cap_key), None)
        if cap is not None:
            want = min(want, cap)
    slots = next_slots(sched.get("days", []), sched.get("times", []),
                       sched.get("timezone", "UTC"), want,
                       start_time=str(sched.get("start_time") or ""),
                       end_time=str(sched.get("end_time") or ""),
                       spacing_minutes=sched.get("spacing_minutes"))
    planned: list[PlannedPost] = []
    for pkg, slot in zip(packages, slots):
        if not pkg.content or not pkg.content.strip():
            raise ValueError("empty content refused")
        if not isinstance(pkg.settings, dict) or \
                pkg.settings.get("__type") != pkg.platform:
            raise ValueError("invalid platform settings")
        planned.append(PlannedPost(
            platform=pkg.platform, content=pkg.content, media=pkg.media,
            planned_at=slot, settings=dict(pkg.settings),
            tags=list(pkg.hashtags), source_ref=pkg.source_ref))
    return planned


@dataclass
class Slot:
    """One publication slot (Stage B contract, Stage 9 executor).

    Pure value, no IO, no state: publish_at is when Tuzzina
    publishes; research_start_at opens the discovery window;
    deadline_at is when the intent must be handed off.
    Insertion point: the campaign scheduler expands due slots
    (publish_at - lead_time <= now), runs one research cycle
    per slot, and hands each intent its slot's publish_at.
    Source polling cadence, publication schedule, and research
    lead time stay three separate fields and must never share
    one overloaded value."""
    publish_at: str = ""
    research_start_at: str = ""
    deadline_at: str = ""


def research_window(publish_at: str, lead_time_min: int = 30,
                    now: datetime | None = None,
                    tzname: str = "UTC") -> Slot:
    """Derive a slot's research window from its publish time.
    lead_time_min<=0 means research starts immediately (window
    opens now); the deadline is publish_at itself. Overdue
    slots (publish_at in the past) still return a Slot with
    the same fields so the caller, not this function, decides
    (skip vs catch-up is scheduler policy, Stage 9). Raises
    ValueError on unparseable publish_at (fail-closed)."""
    tz = _tz(tzname or "UTC")
    try:
        pub = datetime.fromisoformat(
            (publish_at or "").strip().replace("Z", "+00:00"))
    except Exception:
        raise ValueError("slot publish_at is not ISO datetime")
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    pub = pub.astimezone(tz)
    try:
        lead = int(lead_time_min)
    except (TypeError, ValueError):
        lead = 30
    if lead < 0:
        lead = 0
    start = datetime.now(tz) if lead <= 0 else pub - timedelta(
        minutes=lead)
    return Slot(publish_at=pub.isoformat(),
                research_start_at=start.isoformat(),
                deadline_at=pub.isoformat())
