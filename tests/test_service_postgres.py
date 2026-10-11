"""Service and database guarantees that require a migrated PostgreSQL database."""

import asyncio
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from smartlead.config import Settings
from smartlead.db.models import (
    Appointment,
    BusinessSettings,
    LeadRequest,
    NotificationOutbox,
    Service,
    StatusHistory,
)
from smartlead.db.session import create_engine, create_session_factory
from smartlead.services.core import SmartLeadService
from smartlead.services.errors import (
    AccessDenied,
    InvalidTransition,
    NotFound,
    SlotUnavailable,
    ValidationError,
)
from smartlead.workers.outbox import OutboxWorker

pytestmark = pytest.mark.integration

ADMIN_ID = 900_001
OWNER_ID = 111_001
OTHER_ID = 222_002


@pytest_asyncio.fixture
async def configured_service(clean_pg, migrated_test_database_url):
    settings = Settings(
        _env_file=None,
        bot_token="123456:integration-test-token",
        database_url=migrated_test_database_url,
        admin_ids=str(ADMIN_ID),
    )
    async with clean_pg.begin() as session:
        session.add(
            BusinessSettings(
                id=1,
                name="Test Studio",
                timezone="UTC",
                open_time=time(0),
                close_time=time(23, 59),
                weekdays=list(range(7)),
                slot_step_minutes=30,
                booking_horizon_days=30,
                booking_notice_minutes=0,
            )
        )
        session.add_all(
            [
                Service(name="Consultation", description="", duration_minutes=30),
                Service(name="Long service", description="", duration_minutes=60),
            ]
        )
    service = SmartLeadService(clean_pg, settings)
    await service.ensure_user(OWNER_ID, "Lead Owner")
    await service.ensure_user(OTHER_ID, "Other Customer")
    return service


async def _service_ids(service: SmartLeadService) -> tuple[int, int]:
    rows = await service.list_services()
    return rows[0].id, rows[1].id


def _future_slot() -> datetime:
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(10), tzinfo=UTC)


async def _create_request(service: SmartLeadService, owner: int = OWNER_ID):
    service_id, _ = await _service_ids(service)
    return await service.create_request(
        owner,
        service_id,
        "Ada Lovelace",
        "+1 (415) 555-0123",
        "Please call me about a consultation.",
        uuid4(),
    )


async def _create_appointment(
    service: SmartLeadService,
    *,
    owner: int = OWNER_ID,
    starts_at: datetime | None = None,
    key=None,
):
    service_id, _ = await _service_ids(service)
    return await service.create_appointment(
        owner,
        service_id,
        starts_at or _future_slot(),
        "Ada Lovelace",
        "+1 (415) 555-0123",
        key or uuid4(),
    )


@pytest.mark.asyncio
async def test_migration_installs_shared_calendar_exclusion_constraint(pg_engine):
    async with pg_engine.connect() as connection:
        definition = await connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'appointments_no_overlap'"
            )
        )
    assert definition is not None
    assert "EXCLUDE USING gist" in definition
    assert "tstzrange" in definition


@pytest.mark.asyncio
async def test_same_request_confirmation_racing_twice_creates_one_request_and_event(
    configured_service, clean_pg
):
    service = configured_service
    service_id, _ = await _service_ids(service)
    key = uuid4()

    first, second = await asyncio.gather(
        service.create_request(
            OWNER_ID,
            service_id,
            "Ada Lovelace",
            "+1 (415) 555-0123",
            "Please call me about a consultation.",
            key,
        ),
        service.create_request(
            OWNER_ID,
            service_id,
            "Ada Lovelace",
            "+1 (415) 555-0123",
            "Please call me about a consultation.",
            key,
        ),
    )
    async with clean_pg() as session:
        request_count = await session.scalar(select(func.count(LeadRequest.id)))
        history_count = await session.scalar(select(func.count(StatusHistory.id)))
        outbox_count = await session.scalar(select(func.count(NotificationOutbox.id)))

    assert first.id == second.id
    assert request_count == history_count == outbox_count == 1


@pytest.mark.asyncio
async def test_different_customers_racing_for_one_slot_create_one_booking_and_event(
    configured_service, clean_pg
):
    service = configured_service
    service_id, _ = await _service_ids(service)
    starts_at = _future_slot()

    results = await asyncio.gather(
        service.create_appointment(
            OWNER_ID, service_id, starts_at, "Lead Owner", "+1 (415) 555-0124", uuid4()
        ),
        service.create_appointment(
            OTHER_ID, service_id, starts_at, "Other Customer", "+1 (415) 555-0125", uuid4()
        ),
        return_exceptions=True,
    )

    appointments = [result for result in results if isinstance(result, Appointment)]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(appointments) == 1
    assert len(failures) == 1 and isinstance(failures[0], SlotUnavailable)

    async with clean_pg() as session:
        assert await session.scalar(select(func.count(Appointment.id))) == 1
        assert await session.scalar(select(func.count(StatusHistory.id))) == 1
        events = list(await session.scalars(select(NotificationOutbox)))
    assert len(events) == 1
    assert events[0].telegram_id == ADMIN_ID


@pytest.mark.asyncio
async def test_postgres_rejects_same_and_partial_overlap_but_allows_adjacent_slots(
    configured_service, clean_pg
):
    service = configured_service
    consultation_id, long_service_id = await _service_ids(service)
    owner = await service.ensure_user(OWNER_ID, "Lead Owner")
    other = await service.ensure_user(OTHER_ID, "Other Customer")
    starts_at = _future_slot()
    ends_at = starts_at + timedelta(minutes=30)
    async with clean_pg.begin() as session:
        session.add(
            Appointment(
                user_id=owner.id,
                service_id=consultation_id,
                submission_key=uuid4(),
                starts_at=starts_at,
                ends_at=ends_at,
                customer_name="Ada Lovelace",
                contact="+1 (415) 555-0123",
                service_name="Consultation",
            )
        )

    for conflicting_start in (starts_at, starts_at + timedelta(minutes=15)):
        with pytest.raises(IntegrityError, match="appointments_no_overlap"):
            async with clean_pg.begin() as session:
                session.add(
                    Appointment(
                        user_id=other.id,
                        service_id=long_service_id,
                        submission_key=uuid4(),
                        starts_at=conflicting_start,
                        ends_at=conflicting_start + timedelta(minutes=60),
                        customer_name="Other Customer",
                        contact="+1 (415) 555-0125",
                        service_name="Long service",
                    )
                )

    async with clean_pg.begin() as session:
        session.add(
            Appointment(
                user_id=other.id,
                service_id=long_service_id,
                submission_key=uuid4(),
                starts_at=ends_at,
                ends_at=ends_at + timedelta(minutes=60),
                customer_name="Other Customer",
                contact="+1 (415) 555-0125",
                service_name="Long service",
            )
        )
    async with clean_pg() as session:
        assert await session.scalar(select(func.count(Appointment.id))) == 2


@pytest.mark.asyncio
async def test_request_creation_waits_for_service_deactivation(configured_service, clean_pg):
    service = configured_service
    service_id, _ = await _service_ids(service)
    request_task = None
    async with clean_pg() as deactivation_session:
        async with deactivation_session.begin():
            await deactivation_session.execute(
                update(Service).where(Service.id == service_id).values(is_active=False)
            )
            request_task = asyncio.create_task(
                service.create_request(
                    OWNER_ID,
                    service_id,
                    "Ada Lovelace",
                    "+1 (415) 555-0123",
                    "Please call me about a consultation.",
                    uuid4(),
                )
            )
            done, _ = await asyncio.wait({request_task}, timeout=0.2)
            assert not done, "creation must wait while the service is being deactivated"

    assert request_task is not None
    with pytest.raises(NotFound):
        await request_task
    async with clean_pg() as session:
        assert await session.scalar(select(func.count(LeadRequest.id))) == 0


@pytest.mark.asyncio
async def test_customer_ownership_admin_access_and_status_history_outbox_are_enforced(
    configured_service, clean_pg
):
    service = configured_service
    request = await _create_request(service)
    appointment = await _create_appointment(service)

    with pytest.raises(NotFound):
        await service.get_request(OTHER_ID, request.id)
    with pytest.raises(NotFound):
        await service.cancel_appointment(OTHER_ID, appointment.id)
    with pytest.raises(AccessDenied):
        await service.get_request(OTHER_ID, request.id, admin=True)
    with pytest.raises(AccessDenied):
        await service.statistics(OTHER_ID)

    updated = await service.change_request_status(ADMIN_ID, request.id, "in_progress")
    assert updated.status == "in_progress"
    with pytest.raises(InvalidTransition):
        await service.change_request_status(ADMIN_ID, request.id, "new")

    async with clean_pg() as session:
        history = list(
            await session.scalars(
                select(StatusHistory)
                .where(StatusHistory.request_id == request.id)
                .order_by(StatusHistory.id)
            )
        )
        events = list(
            await session.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id))
        )
        assert [(row.old_status, row.new_status) for row in history] == [
            (None, "new"),
            ("new", "in_progress"),
        ]
        # Requests and bookings each notify the admin; the status change notifies both.
        assert [row.telegram_id for row in events].count(ADMIN_ID) == 3
        assert [row.telegram_id for row in events].count(OWNER_ID) == 1


@pytest.mark.asyncio
async def test_admin_service_changes_and_deactivation_keep_existing_record_snapshots(
    configured_service,
):
    service = configured_service
    service_id, _ = await _service_ids(service)
    request = await _create_request(service)
    appointment = await _create_appointment(service)
    original_end = appointment.ends_at

    with pytest.raises(AccessDenied):
        await service.list_services_admin(OWNER_ID)
    with pytest.raises(AccessDenied):
        await service.create_service(OWNER_ID, "New service", "", 30)

    updated = await service.update_service(
        ADMIN_ID,
        service_id,
        name="Renamed consultation",
        description="Updated service details.",
        duration_minutes=45,
    )
    assert (updated.name, updated.description, updated.duration_minutes) == (
        "Renamed consultation",
        "Updated service details.",
        45,
    )

    inactive = await service.set_service_active(ADMIN_ID, service_id, False)
    assert inactive.is_active is False
    assert [item.id for item in await service.list_services()] == [2]
    admin_catalog = await service.list_services_admin(ADMIN_ID)
    assert [(item.id, item.is_active) for item in admin_catalog] == [(1, False), (2, True)]

    saved_request = await service.get_request(OWNER_ID, request.id)
    saved_appointment = await service.get_appointment(OWNER_ID, appointment.id)
    assert saved_request.service_name == "Consultation"
    assert saved_appointment.service_name == "Consultation"
    assert saved_appointment.ends_at == original_end

    reactivated = await service.set_service_active(ADMIN_ID, service_id, True)
    assert reactivated.is_active is True
    with pytest.raises(ValidationError):
        await service.update_service(ADMIN_ID, service_id, duration_minutes=481)


@pytest.mark.asyncio
async def test_support_contact_can_be_configured_and_cleared_with_admin_acl(configured_service):
    service = configured_service

    with pytest.raises(AccessDenied):
        await service.update_support_contact(OWNER_ID, "@smartlead_help")

    configured = await service.update_support_contact(ADMIN_ID, "@smartlead_help")
    assert configured.support_contact == "https://t.me/smartlead_help"
    long_handle = "a" + ("b" * 31)
    configured = await service.update_support_contact(ADMIN_ID, f"@{long_handle}")
    assert configured.support_contact == f"https://t.me/{long_handle}"
    assert len(configured.support_contact) == 45
    cleared = await service.update_support_contact(ADMIN_ID, None)
    assert cleared.support_contact is None

    with pytest.raises(ValidationError):
        await service.update_support_contact(ADMIN_ID, "https://unsafe.example/support")
    assert (await service.business_settings()).support_contact is None


@pytest.mark.asyncio
async def test_request_status_filter_pagination_and_owner_scoping(configured_service):
    service = configured_service
    requests = [await _create_request(service) for _ in range(7)]
    await service.change_request_status(ADMIN_ID, requests[0].id, "completed")
    await service.change_request_status(ADMIN_ID, requests[1].id, "in_progress")

    first_page = await service.list_requests(OWNER_ID, limit=3)
    second_page = await service.list_requests(OWNER_ID, offset=3, limit=3)
    completed = await service.list_requests(ADMIN_ID, status="completed", admin=True)
    other_customer = await service.list_requests(OTHER_ID)

    assert [item.id for item in first_page] == [item.id for item in reversed(requests[-3:])]
    assert [item.id for item in second_page] == [item.id for item in reversed(requests[-6:-3])]
    assert [item.id for item in completed] == [requests[0].id]
    assert other_customer == []
    with pytest.raises(AccessDenied):
        await service.list_requests(OWNER_ID, admin=True)
    with pytest.raises(ValidationError):
        await service.list_requests(ADMIN_ID, admin=True, status="unknown")


@pytest.mark.asyncio
async def test_group_request_alert_persists_action_markup_and_status_copy(
    configured_service, clean_pg, migrated_test_database_url
):
    settings = Settings(
        _env_file=None,
        bot_token="123456:integration-test-token",
        database_url=migrated_test_database_url,
        admin_ids=str(ADMIN_ID),
        admin_notification_chat_id=-100_123_456_7890,
    )
    service = SmartLeadService(clean_pg, settings)
    await service.ensure_user(OWNER_ID, "Lead Owner")
    await service.ensure_user(OTHER_ID, "Other Customer")
    request = await _create_request(service)

    async with clean_pg() as session:
        rows = list(await session.scalars(select(NotificationOutbox)))
    assert len(rows) == 1
    alert = rows[0]
    assert alert.telegram_id == -100_123_456_7890
    assert "+1 (415) 555-0123" in alert.text
    assert [button[0]["callback_data"] for button in alert.reply_markup["inline_keyboard"]] == [
        f"admin:req:item:{request.id}:0",
        f"admin:req:set:{request.id}:in_progress:0",
        f"admin:req:set:{request.id}:completed:0",
    ]

    await service.change_request_status(ADMIN_ID, request.id, "completed")
    with pytest.raises(InvalidTransition):
        await service.change_request_status(ADMIN_ID, request.id, "in_progress")
    async with clean_pg() as session:
        owner_notice = await session.scalar(
            select(NotificationOutbox).where(NotificationOutbox.telegram_id == OWNER_ID)
        )
        history_count = await session.scalar(
            select(func.count(StatusHistory.id)).where(StatusHistory.request_id == request.id)
        )
    assert owner_notice is not None
    assert "completed" in owner_notice.text.lower()
    assert request.number in owner_notice.text
    assert history_count == 2


@pytest.mark.asyncio
async def test_booking_alert_uses_configured_group_and_replay_does_not_duplicate(
    configured_service, clean_pg, migrated_test_database_url
):
    group_id = -100_123_456_7890
    settings = Settings(
        _env_file=None,
        bot_token="123456:integration-test-token",
        database_url=migrated_test_database_url,
        admin_ids=str(ADMIN_ID),
        admin_notification_chat_id=group_id,
    )
    service = SmartLeadService(clean_pg, settings)
    await service.ensure_user(OWNER_ID, "Lead Owner")

    async with clean_pg.begin() as session:
        await session.execute(
            update(BusinessSettings)
            .where(BusinessSettings.id == 1)
            .values(timezone="Europe/Moscow")
        )

    service_id, _ = await _service_ids(service)
    starts_at = _future_slot()
    key = uuid4()
    appointment = await service.create_appointment(
        OWNER_ID, service_id, starts_at, "Ada Lovelace", "+1 (415) 555-0123", key
    )
    replay = await service.create_appointment(
        OWNER_ID, service_id, starts_at, "Ada Lovelace", "+1 (415) 555-0123", key
    )

    async with clean_pg() as session:
        stored = await session.get(Appointment, appointment.id)
        alerts = list(
            await session.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id))
        )

    assert replay.id == appointment.id
    assert stored is not None
    assert len(alerts) == 1
    alert = alerts[0]
    local_start = stored.starts_at.astimezone(ZoneInfo("Europe/Moscow"))
    assert alert.telegram_id == group_id
    assert "📅 New Booking" in alert.text
    assert f"Booking: #{stored.number}" in alert.text
    assert f"Time: {local_start:%H:%M} (Europe/Moscow, UTC+03:00)" in alert.text
    assert alert.telegram_id not in {ADMIN_ID, OWNER_ID}
    assert alert.reply_markup["inline_keyboard"][0][0]["callback_data"] == (
        f"admin:appt:item:{stored.id}:0"
    )

    bot = SimpleNamespace(send_message=AsyncMock())
    assert await OutboxWorker(clean_pg, bot).deliver_one() is True
    bot.send_message.assert_awaited_once()
    sent = bot.send_message.await_args.kwargs
    assert sent["chat_id"] == group_id
    assert sent["text"] == alert.text
    assert sent["reply_markup"].inline_keyboard[0][0].callback_data == (
        f"admin:appt:item:{stored.id}:0"
    )


@pytest.mark.asyncio
async def test_status_history_and_outbox_failure_rolls_back_status_change(
    configured_service, clean_pg, monkeypatch
):
    service = configured_service
    request = await _create_request(service)
    original_record_event = service._record_event

    async def record_then_fail(*args, **kwargs):
        await original_record_event(*args, **kwargs)
        raise RuntimeError("simulated failure before transaction commit")

    monkeypatch.setattr(service, "_record_event", record_then_fail)
    with pytest.raises(RuntimeError, match="simulated failure"):
        await service.change_request_status(ADMIN_ID, request.id, "in_progress")

    stored = await service.get_request(ADMIN_ID, request.id, admin=True)
    assert stored.status == "new"
    async with clean_pg() as session:
        assert (
            await session.scalar(
                select(func.count(StatusHistory.id)).where(StatusHistory.request_id == request.id)
            )
            == 1
        )
        assert await session.scalar(select(func.count(NotificationOutbox.id))) == 1


@pytest.mark.asyncio
async def test_booking_replay_cancel_releases_slot_and_off_grid_start_is_rejected(
    configured_service, clean_pg
):
    service = configured_service
    service_id, _ = await _service_ids(service)
    starts_at = _future_slot()
    key = uuid4()
    first = await service.create_appointment(
        OWNER_ID, service_id, starts_at, "Ada Lovelace", "+1 (415) 555-0123", key
    )
    replay = await service.create_appointment(
        OWNER_ID, service_id, starts_at, "Ada Lovelace", "+1 (415) 555-0123", key
    )
    assert first.id == replay.id

    cancelled = await service.cancel_appointment(OWNER_ID, first.id)
    assert cancelled.status == "cancelled"
    repeated_cancel = await service.cancel_appointment(OWNER_ID, first.id)
    assert repeated_cancel.status == "cancelled"
    replacement = await service.create_appointment(
        OWNER_ID, service_id, starts_at, "Ada Lovelace", "+1 (415) 555-0123", uuid4()
    )
    assert replacement.id != first.id
    assert replacement.status == "booked"

    with pytest.raises(SlotUnavailable):
        await service.create_appointment(
            OTHER_ID,
            service_id,
            starts_at,
            "Other Customer",
            "+1 (415) 555-0125",
            uuid4(),
        )
    with pytest.raises(ValidationError):
        await service.create_appointment(
            OWNER_ID, service_id, starts_at, "Ada Lovelace", "+1 (415) 555-0123", "not-a-uuid"
        )


@pytest.mark.asyncio
async def test_customer_self_cancel_notifies_admin_without_owner_outbox_and_is_idempotent(
    configured_service, clean_pg
):
    service = configured_service
    appointment = await _create_appointment(service)

    cancelled = await service.cancel_appointment(OWNER_ID, appointment.id)
    assert cancelled.status == "cancelled"
    async with clean_pg() as session:
        first_history = list(
            await session.scalars(
                select(StatusHistory)
                .where(StatusHistory.appointment_id == appointment.id)
                .order_by(StatusHistory.id)
            )
        )
        first_events = list(
            await session.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id))
        )

    assert [(row.old_status, row.new_status) for row in first_history] == [
        (None, "booked"),
        ("booked", "cancelled"),
    ]
    assert OWNER_ID not in {row.telegram_id for row in first_events}
    assert any(
        row.telegram_id == ADMIN_ID
        and appointment.number in row.text
        and "cancelled" in row.text.lower()
        for row in first_events
    )

    repeated = await service.cancel_appointment(OWNER_ID, appointment.id)
    assert repeated.status == "cancelled"
    async with clean_pg() as session:
        repeated_history_count = await session.scalar(
            select(func.count(StatusHistory.id)).where(
                StatusHistory.appointment_id == appointment.id
            )
        )
        repeated_event_count = await session.scalar(select(func.count(NotificationOutbox.id)))
    assert repeated_history_count == len(first_history)
    assert repeated_event_count == len(first_events)


@pytest.mark.asyncio
async def test_admin_owner_self_cancel_does_not_queue_an_extra_private_message(
    configured_service, clean_pg
):
    service = configured_service
    await service.ensure_user(ADMIN_ID, "Admin Customer")
    appointment = await _create_appointment(service, owner=ADMIN_ID)

    # This is the customer self-cancel path, even though this customer is also an admin.
    await service.cancel_appointment(ADMIN_ID, appointment.id)
    async with clean_pg() as session:
        history_after_cancel = await session.scalar(
            select(func.count(StatusHistory.id)).where(
                StatusHistory.appointment_id == appointment.id
            )
        )
        events_after_cancel = list(
            await session.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id))
        )

    assert history_after_cancel == 2
    assert len(events_after_cancel) == 1
    assert events_after_cancel[0].telegram_id == ADMIN_ID
    assert "📅 New Booking" in events_after_cancel[0].text

    await service.cancel_appointment(ADMIN_ID, appointment.id)
    async with clean_pg() as session:
        repeated_history_count = await session.scalar(
            select(func.count(StatusHistory.id)).where(
                StatusHistory.appointment_id == appointment.id
            )
        )
        repeated_event_count = await session.scalar(select(func.count(NotificationOutbox.id)))
    assert repeated_history_count == history_after_cancel
    assert repeated_event_count == len(events_after_cancel)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_id", [OWNER_ID, ADMIN_ID])
async def test_admin_cancel_sends_one_customer_confirmation_and_is_idempotent(
    configured_service, clean_pg, owner_id
):
    service = configured_service
    if owner_id == ADMIN_ID:
        await service.ensure_user(ADMIN_ID, "Admin Customer")
    appointment = await _create_appointment(service, owner=owner_id)
    bot = SimpleNamespace(send_message=AsyncMock())
    worker = OutboxWorker(clean_pg, bot)

    while await worker.deliver_one():
        pass
    bot.send_message.reset_mock()

    cancelled = await service.cancel_appointment(ADMIN_ID, appointment.id, admin=True)
    assert cancelled.status == "cancelled"
    async with clean_pg() as session:
        first_history = list(
            await session.scalars(
                select(StatusHistory)
                .where(StatusHistory.appointment_id == appointment.id)
                .order_by(StatusHistory.id)
            )
        )
        first_events = list(
            await session.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id))
        )

    assert [(row.old_status, row.new_status) for row in first_history] == [
        (None, "booked"),
        ("booked", "cancelled"),
    ]
    owner_notice = next(
        row
        for row in first_events
        if row.telegram_id == owner_id and row.text.startswith("✅ Appointment Cancelled")
    )
    assert appointment.number in owner_notice.text
    assert f"Appointment ID: #{appointment.number}" in owner_notice.text
    assert "Status: 🔴 Cancelled" in owner_notice.text

    while await worker.deliver_one():
        pass
    confirmations = [
        call
        for call in bot.send_message.await_args_list
        if call.kwargs["text"].startswith("✅ Appointment Cancelled")
    ]
    assert len(confirmations) == 1
    assert confirmations[0].kwargs["chat_id"] == owner_id
    assert confirmations[0].kwargs["text"] == owner_notice.text
    delivered_count = bot.send_message.await_count

    repeated = await service.cancel_appointment(ADMIN_ID, appointment.id, admin=True)
    assert repeated.status == "cancelled"
    async with clean_pg() as session:
        repeated_history_count = await session.scalar(
            select(func.count(StatusHistory.id)).where(
                StatusHistory.appointment_id == appointment.id
            )
        )
        repeated_event_count = await session.scalar(select(func.count(NotificationOutbox.id)))
    assert repeated_history_count == len(first_history)
    assert repeated_event_count == len(first_events)
    assert await worker.deliver_one() is False
    assert bot.send_message.await_count == delivered_count


@pytest.mark.asyncio
async def test_upcoming_appointments_are_owner_scoped_and_repeat_cancel_is_idempotent(
    configured_service, clean_pg
):
    service = configured_service
    starts_at = _future_slot()
    owned = await _create_appointment(service, starts_at=starts_at)
    other_owned = await _create_appointment(
        service, owner=OTHER_ID, starts_at=starts_at + timedelta(hours=2)
    )

    assert [row.id for row in await service.list_appointments(OWNER_ID, upcoming=True)] == [
        owned.id
    ]
    assert [row.id for row in await service.list_appointments(OTHER_ID, upcoming=True)] == [
        other_owned.id
    ]
    with pytest.raises(NotFound):
        await service.get_appointment(OTHER_ID, owned.id)

    await service.cancel_appointment(OWNER_ID, owned.id)
    await service.cancel_appointment(OWNER_ID, owned.id)
    assert await service.list_appointments(OWNER_ID, upcoming=True) == []
    saved = await service.get_appointment(OWNER_ID, owned.id)
    assert saved.status == "cancelled"

    async with clean_pg() as session:
        history_count = await session.scalar(
            select(func.count(StatusHistory.id)).where(StatusHistory.appointment_id == owned.id)
        )
    assert history_count == 2


@pytest.mark.asyncio
async def test_committed_records_and_pending_notifications_survive_new_service_instance(
    configured_service,
    clean_pg,
    migrated_test_database_url,
):
    service = configured_service
    request = await _create_request(service)
    appointment = await _create_appointment(service)

    restarted_engine = create_engine(migrated_test_database_url)
    try:
        restarted_service = SmartLeadService(
            create_session_factory(restarted_engine), service.settings
        )
        saved_request = await restarted_service.get_request(OWNER_ID, request.id)
        saved_appointment = await restarted_service.get_appointment(OWNER_ID, appointment.id)
        async with restarted_service.sessions() as session:
            pending = list(
                await session.scalars(
                    select(NotificationOutbox).where(NotificationOutbox.status == "pending")
                )
            )

        assert saved_request.number == request.number
        assert saved_request.contact == "+1 (415) 555-0123"
        assert saved_appointment.number == appointment.number
        assert saved_appointment.status == "booked"
        assert len(pending) == 2
    finally:
        await restarted_engine.dispose()
