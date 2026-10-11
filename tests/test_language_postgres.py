"""Persisted language preferences and localized notification transactions."""

from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from smartlead.config import Settings
from smartlead.db.models import (
    BusinessSettings,
    NotificationOutbox,
    Service,
    StatusHistory,
)
from smartlead.db.session import create_engine, create_session_factory
from smartlead.i18n import SUPPORTED_LANGUAGES, tr
from smartlead.presentation import (
    format_booking_cancelled,
    format_new_request_notification,
    format_request_status_notification,
)
from smartlead.services.core import SmartLeadService
from smartlead.services.errors import ValidationError
from tests.test_bot_fsm import BotHarness

pytestmark = pytest.mark.integration

CUSTOMER_ID = 111_001
ADMIN_IDS = tuple(900_100 + offset for offset in range(len(SUPPORTED_LANGUAGES)))
GROUP_ID = -100_123_456_7890


def _settings(
    database_url: str,
    *,
    admin_ids: tuple[int, ...] = (ADMIN_IDS[0],),
    group_id: int | None = None,
    group_language: str = "en",
) -> Settings:
    return Settings(
        _env_file=None,
        bot_token="123456:i18n-test-token",
        database_url=database_url,
        admin_ids=",".join(map(str, admin_ids)),
        admin_notification_chat_id=group_id,
        admin_notification_language=group_language,
    )


@pytest_asyncio.fixture
async def language_service(clean_pg, migrated_test_database_url):
    settings = _settings(migrated_test_database_url)
    async with clean_pg.begin() as session:
        session.add(
            BusinessSettings(
                id=1,
                name="Language Studio",
                timezone="UTC",
                open_time=time(0),
                close_time=time(23, 59),
                weekdays=list(range(7)),
                slot_step_minutes=30,
                booking_horizon_days=30,
                booking_notice_minutes=0,
            )
        )
        session.add(Service(name="Consultation", description="", duration_minutes=30))
    service = SmartLeadService(clean_pg, settings)
    for telegram_id in (CUSTOMER_ID, *ADMIN_IDS):
        await service.ensure_user(telegram_id, f"Test user {telegram_id}")
    return service


async def _service_id(service: SmartLeadService) -> int:
    return (await service.list_services())[0].id


async def _new_request(service: SmartLeadService):
    return await service.create_request(
        CUSTOMER_ID,
        await _service_id(service),
        "Ada Lovelace",
        "+1 (415) 555-0123",
        "Please contact me about a consultation.",
        uuid4(),
    )


def _tomorrow_at_10_utc() -> datetime:
    return datetime.combine(datetime.now(UTC).date() + timedelta(days=1), time(10), tzinfo=UTC)


@pytest.mark.asyncio
async def test_profile_language_round_trips_all_supported_codes_and_rejects_invalid_values(
    language_service, clean_pg
):
    service = language_service
    assert await service.get_user_language(CUSTOMER_ID) is None
    assert await service.get_user_language(123_456_789) is None

    for code in SUPPORTED_LANGUAGES:
        saved = await service.set_user_language(CUSTOMER_ID, code)
        assert saved.language_code == code
        assert await service.get_user_language(CUSTOMER_ID) == code

    chosen = "fr"
    await service.set_user_language(CUSTOMER_ID, chosen)
    updated_profile = await service.ensure_user(CUSTOMER_ID, "Ada Lovelace Updated", "ada")
    assert updated_profile.language_code == chosen
    assert updated_profile.full_name == "Ada Lovelace Updated"

    with pytest.raises(ValidationError):
        await service.set_user_language(CUSTOMER_ID, "es")
    assert await service.get_user_language(CUSTOMER_ID) == chosen

    with pytest.raises(IntegrityError, match="users_language_valid"):
        async with clean_pg.begin() as session:
            await session.execute(
                text("UPDATE users SET language_code = 'es' WHERE telegram_id = :user_id"),
                {"user_id": CUSTOMER_ID},
            )
    assert await service.get_user_language(CUSTOMER_ID) == chosen


@pytest.mark.asyncio
async def test_request_and_status_notifications_render_for_each_admin_and_customer_locale(
    language_service, clean_pg, migrated_test_database_url
):
    settings = _settings(migrated_test_database_url, admin_ids=ADMIN_IDS)
    service = SmartLeadService(clean_pg, settings)
    for code, telegram_id in zip(SUPPORTED_LANGUAGES, ADMIN_IDS, strict=True):
        await service.set_user_language(telegram_id, code)
    customer_language = "uk"
    await service.set_user_language(CUSTOMER_ID, customer_language)

    request = await _new_request(service)
    async with clean_pg() as session:
        initial_events = list(
            await session.scalars(select(NotificationOutbox).order_by(NotificationOutbox.id))
        )
    assert len(initial_events) == len(SUPPORTED_LANGUAGES)
    assert {event.telegram_id for event in initial_events} == set(ADMIN_IDS)
    assert len({event.dedup_key for event in initial_events}) == len(ADMIN_IDS)

    for code, telegram_id in zip(SUPPORTED_LANGUAGES, ADMIN_IDS, strict=True):
        event = next(row for row in initial_events if row.telegram_id == telegram_id)
        assert event.text == format_new_request_notification(
            request.number,
            "Consultation",
            "Ada Lovelace",
            "+1 (415) 555-0123",
            "Please contact me about a consultation.",
            language=code,
        )
        labels = [button["text"] for row in event.reply_markup["inline_keyboard"] for button in row]
        assert labels == [
            tr("notification_view_request", language=code),
            tr("notification_start_processing", language=code),
            tr("notification_mark_completed", language=code),
        ]

    assert len({event.text for event in initial_events}) == len(SUPPORTED_LANGUAGES)

    last_initial_id = max(event.id for event in initial_events)
    await service.change_request_status(ADMIN_IDS[0], request.id, "in_progress")
    async with clean_pg() as session:
        history_count = await session.scalar(
            select(func.count(StatusHistory.id)).where(StatusHistory.request_id == request.id)
        )
        status_events = list(
            await session.scalars(
                select(NotificationOutbox)
                .where(NotificationOutbox.id > last_initial_id)
                .order_by(NotificationOutbox.id)
            )
        )

    assert history_count == 2
    assert len(status_events) == len(ADMIN_IDS) + 1
    assert {event.telegram_id for event in status_events} == {*ADMIN_IDS, CUSTOMER_ID}
    owner_notice = next(event for event in status_events if event.telegram_id == CUSTOMER_ID)
    assert owner_notice.text == format_request_status_notification(
        request.number, "in_progress", language=customer_language
    )
    for code, telegram_id in zip(SUPPORTED_LANGUAGES, ADMIN_IDS, strict=True):
        event = next(row for row in status_events if row.telegram_id == telegram_id)
        assert event.text == tr(
            "notification_admin_request_status",
            language=code,
            request_id=request.number,
            status=tr("status.in_progress", language=code),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", tuple(SUPPORTED_LANGUAGES))
async def test_customer_status_notifications_are_localized_for_each_supported_profile(
    language_service, clean_pg, code
):
    service = language_service
    await service.set_user_language(CUSTOMER_ID, code)
    request = await _new_request(service)
    await service.change_request_status(ADMIN_IDS[0], request.id, "completed")

    async with clean_pg() as session:
        owner_events = list(
            await session.scalars(
                select(NotificationOutbox)
                .where(NotificationOutbox.telegram_id == CUSTOMER_ID)
                .order_by(NotificationOutbox.id)
            )
        )
    assert len(owner_events) == 1
    assert owner_events[0].text == format_request_status_notification(
        request.number, "completed", language=code
    )


@pytest.mark.asyncio
async def test_group_notifications_use_one_configured_locale_and_one_group_row(
    language_service, clean_pg, migrated_test_database_url
):
    group_language = "de"
    settings = _settings(
        migrated_test_database_url,
        admin_ids=ADMIN_IDS[:2],
        group_id=GROUP_ID,
        group_language=group_language,
    )
    service = SmartLeadService(language_service.sessions, settings)
    for telegram_id in ADMIN_IDS[1:2]:
        await service.ensure_user(telegram_id, f"Test user {telegram_id}")
    await service.set_user_language(ADMIN_IDS[0], "fr")
    await service.set_user_language(ADMIN_IDS[1], "ru")
    await service.set_user_language(CUSTOMER_ID, "ja")

    request = await _new_request(service)
    async with clean_pg() as session:
        events = list(await session.scalars(select(NotificationOutbox)))

    assert len(events) == 1
    assert events[0].telegram_id == GROUP_ID
    assert events[0].text == format_new_request_notification(
        request.number,
        "Consultation",
        "Ada Lovelace",
        "+1 (415) 555-0123",
        "Please contact me about a consultation.",
        language=group_language,
    )
    assert [
        button["text"] for row in events[0].reply_markup["inline_keyboard"] for button in row
    ] == [
        tr("notification_view_request", language=group_language),
        tr("notification_start_processing", language=group_language),
        tr("notification_mark_completed", language=group_language),
    ]


@pytest.mark.asyncio
async def test_real_first_start_language_choice_survives_service_restart(
    language_service, migrated_test_database_url
):
    assert await language_service.get_user_language(CUSTOMER_ID) is None
    first_harness = BotHarness(language_service)
    try:
        await first_harness.message("/start", user_id=CUSTOMER_ID)
        assert first_harness.session.messages[-1].text == tr("language.choose", language="en")
        await first_harness.callback("language:uk", user_id=CUSTOMER_ID)

        assert await language_service.get_user_language(CUSTOMER_ID) == "uk"
        assert first_harness.session.messages[-1].text == tr("menu.choose_option", language="uk")
    finally:
        await first_harness.close()

    restarted_engine = create_engine(migrated_test_database_url)
    restarted_sessions = create_session_factory(restarted_engine)
    try:
        restarted_service = SmartLeadService(restarted_sessions, language_service.settings)
        assert await restarted_service.get_user_language(CUSTOMER_ID) == "uk"

        restarted_harness = BotHarness(restarted_service)
        try:
            await restarted_harness.message("/start", user_id=CUSTOMER_ID)
            assert restarted_harness.session.messages[-1].text == tr(
                "start.welcome", language="uk", business_name="Language Studio"
            )
            assert "language.choose" not in (restarted_harness.session.messages[-1].text or "")
        finally:
            await restarted_harness.close()
    finally:
        await restarted_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("language", tuple(SUPPORTED_LANGUAGES))
async def test_admin_booking_cancellation_uses_each_recipient_language(
    language_service, clean_pg, language
):
    service = language_service
    admin_language = "fr"
    await service.set_user_language(CUSTOMER_ID, language)
    await service.set_user_language(ADMIN_IDS[0], admin_language)
    appointment = await service.create_appointment(
        CUSTOMER_ID,
        await _service_id(service),
        _tomorrow_at_10_utc(),
        "Ada Lovelace",
        "+1 (415) 555-0123",
        uuid4(),
    )
    async with clean_pg() as session:
        last_id = await session.scalar(select(func.max(NotificationOutbox.id))) or 0

    await service.cancel_appointment(ADMIN_IDS[0], appointment.id, admin=True)
    async with clean_pg() as session:
        events = list(
            await session.scalars(
                select(NotificationOutbox)
                .where(NotificationOutbox.id > last_id)
                .order_by(NotificationOutbox.id)
            )
        )

    assert {event.telegram_id for event in events} == {CUSTOMER_ID, ADMIN_IDS[0]}
    owner_event = next(event for event in events if event.telegram_id == CUSTOMER_ID)
    admin_event = next(event for event in events if event.telegram_id == ADMIN_IDS[0])
    assert owner_event.text == format_booking_cancelled(appointment.number, language=language)
    assert admin_event.text == tr(
        "notification_admin_booking_status",
        language=admin_language,
        booking_id=appointment.number,
        status=tr("status.cancelled", language=admin_language),
    )


@pytest.mark.asyncio
async def test_owner_booking_cancellation_notifies_only_other_admins_in_their_language(
    language_service, clean_pg
):
    service = language_service
    admin_language = "de"
    await service.set_user_language(CUSTOMER_ID, "ja")
    await service.set_user_language(ADMIN_IDS[0], admin_language)
    appointment = await service.create_appointment(
        CUSTOMER_ID,
        await _service_id(service),
        _tomorrow_at_10_utc(),
        "Ada Lovelace",
        "+1 (415) 555-0123",
        uuid4(),
    )
    async with clean_pg() as session:
        last_id = await session.scalar(select(func.max(NotificationOutbox.id))) or 0

    await service.cancel_appointment(CUSTOMER_ID, appointment.id)
    async with clean_pg() as session:
        events = list(
            await session.scalars(
                select(NotificationOutbox)
                .where(NotificationOutbox.id > last_id)
                .order_by(NotificationOutbox.id)
            )
        )

    assert len(events) == 1
    assert events[0].telegram_id == ADMIN_IDS[0]
    assert events[0].text == tr(
        "notification_admin_booking_status",
        language=admin_language,
        booking_id=appointment.number,
        status=tr("status.cancelled", language=admin_language),
    )


@pytest.mark.asyncio
async def test_language_and_queued_text_survive_restart_and_booking_instants_stay_utc(
    language_service, clean_pg, migrated_test_database_url
):
    service = language_service
    await service.set_user_language(CUSTOMER_ID, "ru")
    request = await _new_request(service)
    await service.change_request_status(ADMIN_IDS[0], request.id, "in_progress")
    appointment = await service.create_appointment(
        CUSTOMER_ID,
        await _service_id(service),
        _tomorrow_at_10_utc(),
        "Ada Lovelace",
        "+1 (415) 555-0123",
        uuid4(),
    )
    original_start = appointment.starts_at.astimezone(UTC)

    async with clean_pg() as session:
        queued_owner_notice = await session.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.telegram_id == CUSTOMER_ID,
                NotificationOutbox.text
                == format_request_status_notification(request.number, "in_progress", language="ru"),
            )
        )
        assert queued_owner_notice is not None
        queued_text = queued_owner_notice.text

    await service.set_user_language(CUSTOMER_ID, "fr")
    restarted_engine = create_engine(migrated_test_database_url)
    restarted_sessions = create_session_factory(restarted_engine)
    try:
        restarted_service = SmartLeadService(restarted_sessions, service.settings)
        assert await restarted_service.get_user_language(CUSTOMER_ID) == "fr"
        saved_request = await restarted_service.get_request(CUSTOMER_ID, request.id)
        saved_appointment = await restarted_service.get_appointment(CUSTOMER_ID, appointment.id)
        assert saved_request.status == "in_progress"
        assert saved_appointment.starts_at.astimezone(UTC) == original_start

        await restarted_service.change_request_status(ADMIN_IDS[0], request.id, "completed")
        async with restarted_sessions() as session:
            owner_events = list(
                await session.scalars(
                    select(NotificationOutbox)
                    .where(NotificationOutbox.telegram_id == CUSTOMER_ID)
                    .order_by(NotificationOutbox.id)
                )
            )
        assert [event.text for event in owner_events] == [
            queued_text,
            format_request_status_notification(request.number, "completed", language="fr"),
        ]
    finally:
        await restarted_engine.dispose()
