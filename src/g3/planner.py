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
            tags=list(pkg.hashtags)))
    return planned
