"""Availability is calculated from local business hours and persisted UTC instants."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from smartlead.services.slots import local_instants, slots_for_day


@dataclass
class Business:
    timezone: str = "Europe/London"
    open_time: time = time(9)
    close_time: time = time(12)
    weekdays: list[int] | None = None
    slot_step_minutes: int = 30
    booking_horizon_days: int = 14
    booking_notice_minutes: int = 0

    def __post_init__(self):
        if self.weekdays is None:
            self.weekdays = list(range(7))


def test_local_instants_skips_spring_forward_gap_and_returns_both_fall_back_instants():
    london = ZoneInfo("Europe/London")

    assert local_instants(datetime(2026, 3, 29, 1, 30), london) == []
    fold = local_instants(datetime(2026, 10, 25, 1, 30), london)
    assert fold == [
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 1, 30, tzinfo=UTC),
    ]


def test_local_instants_rejects_aware_wall_time():
    with pytest.raises(ValueError, match="naive"):
        local_instants(datetime(2026, 1, 5, 9, tzinfo=UTC), ZoneInfo("Europe/London"))


def test_slots_use_business_timezone_notice_and_half_open_overlap():
    business = Business(booking_notice_minutes=30)
    day = date(2026, 1, 5)  # Monday, London is UTC in January.
    now = datetime(2026, 1, 5, 8, 45, tzinfo=UTC)
    occupied = [(datetime(2026, 1, 5, 10, tzinfo=UTC), datetime(2026, 1, 5, 10, 30, tzinfo=UTC))]

    slots = slots_for_day(business, 30, day, occupied, now=now)

    assert slots == [
        datetime(2026, 1, 5, 9, 30, tzinfo=UTC),
        datetime(2026, 1, 5, 10, 30, tzinfo=UTC),
        datetime(2026, 1, 5, 11, tzinfo=UTC),
        datetime(2026, 1, 5, 11, 30, tzinfo=UTC),
    ]


def test_slots_reject_closed_days_outside_horizon_and_naive_now():
    business = Business(weekdays=[0], booking_horizon_days=2)
    now = datetime(2026, 1, 5, 8, tzinfo=UTC)

    assert slots_for_day(business, 30, date(2026, 1, 6), [], now=now) == []
    assert slots_for_day(business, 30, date(2026, 1, 7), [], now=now) == []
    with pytest.raises(ValueError, match="timezone-aware"):
        slots_for_day(business, 30, date(2026, 1, 5), [], now=datetime(2026, 1, 5, 8))


def test_slot_end_must_fit_inside_closing_time():
    business = Business(open_time=time(11), close_time=time(12), slot_step_minutes=30)
    day = date(2026, 1, 5)

    assert slots_for_day(business, 75, day, [], now=datetime(2026, 1, 5, 8, tzinfo=UTC)) == []
