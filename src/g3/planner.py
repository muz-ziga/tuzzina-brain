"""G3 Content Planner: decisions only. Pure function, no IO, no state.
Input: content packages + schedule config. Output: PlannedPost list."""
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
    return int(h or 9), int(m or 0)


def next_slots(days: list, times: list, tzname: str, count: int,
               now: datetime | None = None) -> list[str]:
    """Upcoming datetimes matching days/times. Empty days/times ->
    one slot per day at 09:00 starting tomorrow (documented default)."""
    tz = _tz(tzname or "UTC")
    now = now or datetime.now(tz)
    days = [d.lower() for d in (days or [])]
    times = times or ["09:00"]
    wd = ["monday", "tuesday", "wednesday", "thursday", "friday",
          "saturday", "sunday"]
    out: list[str] = []
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
            if slot > now:
                out.append(slot.isoformat())
                if len(out) >= count:
                    break
    return out


def plan(packages: list[ContentPackage], schedule: dict) -> list[PlannedPost]:
    sched = schedule or {}
    slots = next_slots(sched.get("days", []), sched.get("times", []),
                       sched.get("timezone", "UTC"), len(packages))
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
