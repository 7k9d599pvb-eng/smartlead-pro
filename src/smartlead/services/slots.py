"""Build slots in business wall time, compare reservations as UTC instants."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class CalendarSettings(Protocol):
    timezone: str
    open_time: time
    close_time: time
    weekdays: list[int]
    slot_step_minutes: int
    booking_horizon_days: int
    booking_notice_minutes: int


def local_instants(wall_time: datetime, timezone: ZoneInfo) -> list[datetime]:
    """Return 0 instants for a DST gap, 2 for a fold, 1 for ordinary wall time."""
    if wall_time.tzinfo is not None:
        raise ValueError("wall_time must be naive")
    instants = set()
    for fold in (0, 1):
        utc = wall_time.replace(tzinfo=timezone, fold=fold).astimezone(UTC)
        if utc.astimezone(timezone).replace(tzinfo=None) == wall_time:
            instants.add(utc)
    return sorted(instants)


def slots_for_day(
    business: CalendarSettings,
    duration_minutes: int,
    day: date,
    occupied: list[tuple[datetime, datetime]],
    *,
    now: datetime | None = None,
) -> list[datetime]:
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    timezone = ZoneInfo(business.timezone)
    today = now.astimezone(timezone).date()
    if not today <= day < today + timedelta(days=business.booking_horizon_days):
        return []
    if day.weekday() not in business.weekdays:
        return []
    opening = datetime.combine(day, business.open_time)
    closing = datetime.combine(day, business.close_time)
    open_instants = local_instants(opening, timezone)
    close_instants = local_instants(closing, timezone)
    if not open_instants or not close_instants:
        return []
    earliest = now.astimezone(UTC) + timedelta(minutes=business.booking_notice_minutes)
    duration = timedelta(minutes=duration_minutes)
    slots: list[datetime] = []
    wall = opening
    while wall < closing:
        for start in local_instants(wall, timezone):
            end = start + duration
            if start < earliest or end > max(close_instants):
                continue
            if any(start < busy_end and end > busy_start for busy_start, busy_end in occupied):
                continue
            slots.append(start)
        wall += timedelta(minutes=business.slot_step_minutes)
    return sorted(set(slots))
