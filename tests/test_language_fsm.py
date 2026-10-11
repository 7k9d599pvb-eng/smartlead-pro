"""Localized first-start, profile, client and administrator dispatcher flows."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from smartlead.i18n import SUPPORTED_LANGUAGES, tr, validate_catalogs
from smartlead.presentation import (
    format_booking_cancelled,
    format_booking_confirmed,
    format_business_date,
    format_business_datetime,
    format_calendar_datetime,
    format_calendar_day,
    format_request_submitted,
)
from tests.test_bot_fsm import USER_ID, BotHarness, _service


def _localized_harness(initial_language: str | None = None, *, admin: bool = False):
    service = _service()
    profiles: dict[int, str | None] = {USER_ID: initial_language}

    async def ensure_user(telegram_id: int, full_name: str, username: str | None = None):
        profiles.setdefault(telegram_id, None)
        return SimpleNamespace(
            telegram_id=telegram_id,
            full_name=full_name,
            username=username,
            language_code=profiles[telegram_id],
        )

    async def get_user_language(telegram_id: int) -> str | None:
        return profiles.get(telegram_id)

    async def set_user_language(telegram_id: int, language_code: str):
        profiles[telegram_id] = language_code
        return await ensure_user(telegram_id, "Test Customer")

    service.ensure_user = AsyncMock(side_effect=ensure_user)
    service.get_user_language = AsyncMock(side_effect=get_user_language)
    service.set_user_language = AsyncMock(side_effect=set_user_language)
    harness = BotHarness(service, admin_ids=str(USER_ID) if admin else "")
    return harness, profiles


def _all_labels(harness: BotHarness, message_index: int = -1) -> list[str]:
    markup = harness.session.messages[message_index].reply_markup
    assert markup is not None
    return [button.text for row in markup.inline_keyboard for button in row]


def _callback_for_action(harness: BotHarness, action: str, *, message_index: int = -1) -> str:
    markup = harness.session.messages[message_index].reply_markup
    assert markup is not None
    for row in markup.inline_keyboard:
        for button in row:
            data = button.callback_data
            if data and data.startswith("f:") and data.split(":")[2] == action:
                return data
    raise AssertionError(
        f"No form callback for {action!r}; text={harness.session.messages[message_index].text!r}"
    )


def _callback_with_prefix(harness: BotHarness, prefix: str, *, message_index: int = -1) -> str:
    markup = harness.session.messages[message_index].reply_markup
    assert markup is not None
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data and button.callback_data.startswith(prefix):
                return button.callback_data
    raise AssertionError(
        f"No callback beginning with {prefix!r}; "
        f"text={harness.session.messages[message_index].text!r}"
    )


@pytest.mark.asyncio
async def test_all_six_translation_catalogs_have_matching_keys_and_placeholders():
    validate_catalogs()
    assert set(SUPPORTED_LANGUAGES) == {"en", "fr", "ru", "uk", "de", "ja"}


@pytest.mark.asyncio
@pytest.mark.parametrize("language", tuple(SUPPORTED_LANGUAGES))
async def test_first_start_language_picker_saves_and_renders_every_supported_locale(language):
    harness, profiles = _localized_harness(initial_language=None)
    try:
        await harness.message("/start")

        assert harness.session.messages[-1].text == tr("language.choose", language="en")
        picker_markup = harness.session.messages[-1].reply_markup
        assert picker_markup is not None
        choices = {
            button.callback_data: button.text
            for row in picker_markup.inline_keyboard
            for button in row
        }
        assert choices == {
            f"language:{code}": native_name for code, native_name in SUPPORTED_LANGUAGES.items()
        }

        await harness.callback(f"language:{language}")

        assert profiles[USER_ID] == language
        harness.service.set_user_language.assert_awaited_once_with(USER_ID, language)
        assert harness.session.messages[-1].text == tr("menu.choose_option", language=language)
        assert _all_labels(harness) == [
            tr(key, language=language)
            for key in (
                "menu.new_request",
                "menu.book_service",
                "menu.my_requests",
                "menu.my_appointments",
                "menu.contact_support",
                "menu.language",
            )
        ]
        assert await harness.state() is None

        # Re-entering /start must honor the saved choice rather than show onboarding again.
        await harness.message("/start")
        assert harness.session.messages[-1].text == tr(
            "start.welcome", language=language, business_name="Test Studio"
        )
        assert "menu.choose_option" not in (harness.session.messages[-1].text or "")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_language_menu_switch_clears_an_open_form_and_updates_the_profile():
    harness, profiles = _localized_harness(initial_language="ru")
    try:
        await harness.message("/start")
        main_menu_index = len(harness.session.messages) - 1
        assert harness.session.messages[-1].text == tr(
            "start.welcome", language="ru", business_name="Test Studio"
        )

        await harness.callback("menu:new")
        assert await harness.state() == "RequestForm:service"
        form_data_before = await harness.data()
        assert form_data_before["kind"] == "request"

        # A language button on an already-rendered menu can safely interrupt a form.
        await harness.callback(
            "menu:language",
            message_index=main_menu_index,
        )
        assert harness.session.messages[main_menu_index].text == tr(
            "language.choose", language="ru"
        )
        await harness.callback("language:de", message_index=main_menu_index)

        assert profiles[USER_ID] == "de"
        assert await harness.state() is None
        assert await harness.data() == {}
        assert harness.session.messages[main_menu_index].text == tr(
            "menu.choose_option", language="de"
        )

        await harness.message("/language")
        assert harness.session.messages[-1].text == tr("language.choose", language="de")
        await harness.callback("language:fr")
        assert profiles[USER_ID] == "fr"
        assert harness.session.messages[-1].text == tr("menu.choose_option", language="fr")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_language_change_survives_expired_callback_ack_and_clears_active_form():
    harness, profiles = _localized_harness(initial_language="ru")
    try:
        await harness.message("/start")
        await harness.callback("menu:new")
        assert await harness.state() == "RequestForm:service"
        await harness.callback("menu:language")
        assert await harness.state() is None

        harness.session.fail_method_once = "AnswerCallbackQuery"
        await harness.callback("language:ja")

        assert profiles[USER_ID] == "ja"
        harness.service.set_user_language.assert_awaited_with(USER_ID, "ja")
        assert harness.session.fail_method_once is None
        assert any(
            type(method).__name__ == "AnswerCallbackQuery" for method in harness.session.methods
        )
        assert harness.session.messages[-1].text == tr("menu.choose_option", language="ja")
        assert _all_labels(harness) == [
            tr(key, language="ja")
            for key in (
                "menu.new_request",
                "menu.book_service",
                "menu.my_requests",
                "menu.my_appointments",
                "menu.contact_support",
                "menu.language",
            )
        ]
        assert await harness.state() is None
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("language", tuple(SUPPORTED_LANGUAGES))
async def test_own_appointment_cancellation_receipt_uses_profile_language(language):
    appointment = SimpleNamespace(
        id=87,
        number="SL-A-000087",
        service_id=1,
        service_name="Consultation",
        starts_at=datetime.now(UTC) + timedelta(days=1),
        status="booked",
        customer_name="Ada Lovelace",
        contact="+1 (415) 555-0123",
    )
    harness, _profiles = _localized_harness(initial_language=language)
    service = harness.service
    service.list_appointments = AsyncMock(return_value=[appointment])
    service.get_appointment = AsyncMock(return_value=appointment)
    service.cancel_appointment = AsyncMock(return_value=appointment)
    try:
        await harness.message("/start")
        await harness.callback("menu:appointments:0")
        await harness.callback(_callback_with_prefix(harness, "view:appt:"))
        await harness.callback(_callback_with_prefix(harness, "cancel:ask:"))
        await harness.callback(_callback_with_prefix(harness, "cancel:confirm:"))

        service.cancel_appointment.assert_awaited_once_with(USER_ID, appointment.id)
        assert harness.session.messages[-1].text == format_booking_cancelled(
            appointment.number, language=language
        )
        assert await harness.state() is None
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("language", tuple(SUPPORTED_LANGUAGES))
async def test_request_booking_errors_support_and_admin_panel_are_localized(language):
    harness, _profiles = _localized_harness(initial_language=language, admin=True)
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    starts_at = datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC).replace(hour=10)
    try:
        await harness.message("/start")
        assert harness.session.messages[-1].text == tr(
            "start.welcome", language=language, business_name="Test Studio"
        )

        # Request flow: translations cover prompts, validation feedback, review,
        # cancellation, Back/Cancel actions and the durable receipt.
        await harness.callback("menu:new")
        assert harness.session.messages[-1].text == tr("client.services.choose", language=language)
        await harness.callback(_callback_for_action(harness, "service"))
        assert await harness.state() == "RequestForm:name"
        assert harness.session.messages[-1].text == tr("client.form.choose_name", language=language)
        await harness.message("A")
        assert await harness.state() == "RequestForm:name"
        assert harness.session.messages[-1].text == tr(
            "error_text_length",
            language=language,
            field=tr("field_name", language=language),
            minimum=2,
            maximum=100,
        )
        await harness.callback(_callback_for_action(harness, "back"))
        assert await harness.state() == "RequestForm:service"
        assert tr("button.cancel", language=language) in _all_labels(harness)
        await harness.callback(_callback_for_action(harness, "cancel"))
        assert await harness.state() is None
        assert harness.session.messages[-1].text == tr(
            "menu.cancelled_choose_option", language=language
        )
        harness.service.create_request.assert_not_awaited()

        await harness.callback("menu:new")
        await harness.callback(_callback_for_action(harness, "service"))
        await harness.message("Ada Lovelace")
        assert harness.session.messages[-1].text == tr("client.contact_prompt", language=language)
        await harness.message("not a phone")
        assert await harness.state() == "RequestForm:contact"
        assert harness.session.messages[-1].text == tr("error_phone_format", language=language)
        await harness.message("+1 (415) 555-0123")
        await harness.message("short")
        assert await harness.state() == "RequestForm:details"
        assert harness.session.messages[-1].text == tr(
            "error_text_length",
            language=language,
            field=tr("field_details", language=language),
            minimum=10,
            maximum=2000,
        )
        details = "Please contact me about a consultation."
        await harness.message(details)
        assert await harness.state() == "RequestForm:review"
        assert harness.session.messages[-1].text == tr(
            "client.review.request",
            language=language,
            service_name="Consultation",
            customer_name="Ada Lovelace",
            contact="+1 (415) 555-0123",
            details=details,
        )
        await harness.callback(_callback_for_action(harness, "submit"))
        harness.service.create_request.assert_awaited_once()
        assert harness.session.messages[-1].text == format_request_submitted(
            "SL-R-000001", "Consultation", language=language
        )
        assert await harness.state() is None

        # Support and the primary admin panel follow the same persisted profile locale.
        await harness.message("/start")
        await harness.callback("menu:support")
        assert harness.session.messages[-1].text == (
            tr("client.support.screen", language=language)
            + "\n\n"
            + tr("client.support.not_configured", language=language)
        )
        await harness.message("/admin")
        assert harness.session.messages[-1].text == tr("admin.panel_title", language=language)
        assert _all_labels(harness) == [
            tr(key, language=language)
            for key in (
                "admin.menu.new_requests",
                "admin.menu.all_requests",
                "admin.menu.appointments",
                "admin.menu.services",
                "admin.menu.statistics",
                "button.main_menu",
            )
        ]

        # Booking flow also exercises date/time labels and a real UTC slot callback.
        await harness.message("/start")
        await harness.callback("menu:book")
        assert harness.session.messages[-1].text == tr("client.services.choose", language=language)
        await harness.callback(_callback_for_action(harness, "service"))
        assert harness.session.messages[-1].text == tr(
            "client.booking.choose_date", language=language, timezone="UTC"
        )
        date_button = next(
            button
            for row in harness.session.messages[-1].reply_markup.inline_keyboard
            for button in row
            if button.callback_data and button.callback_data.split(":")[2] == "date"
        )
        assert date_button.text == format_calendar_day(tomorrow, language=language)
        await harness.callback(date_button.callback_data)
        assert harness.session.messages[-1].text == tr(
            "client.booking.available_times",
            language=language,
            date=format_business_date(tomorrow, "UTC", language=language),
        )
        slot_button = next(
            button
            for row in harness.session.messages[-1].reply_markup.inline_keyboard
            for button in row
            if button.callback_data and button.callback_data.split(":")[2] == "slot"
        )
        expected_slot = tr(
            "client.booking.slot_label",
            language=language,
            date_time=format_calendar_datetime(starts_at, "UTC", language=language),
        )
        expected_review_datetime = format_business_datetime(starts_at, "UTC", language=language)
        assert slot_button.text == expected_slot
        await harness.callback(slot_button.callback_data)
        assert await harness.state() == "BookingForm:name"

        # Back and Cancel remain translated and functional on the booking form too.
        await harness.callback(_callback_for_action(harness, "back"))
        assert await harness.state() == "BookingForm:slot"
        await harness.callback(_callback_for_action(harness, "cancel"))
        assert await harness.state() is None
        assert harness.session.messages[-1].text == tr(
            "menu.cancelled_choose_option", language=language
        )
        harness.service.create_appointment.assert_not_awaited()

        # Restart booking, enter invalid contact, then confirm successfully.
        await harness.message("/start")
        await harness.callback("menu:book")
        await harness.callback(_callback_for_action(harness, "service"))
        await harness.callback(
            next(
                button.callback_data
                for row in harness.session.messages[-1].reply_markup.inline_keyboard
                for button in row
                if button.callback_data and button.callback_data.split(":")[2] == "date"
            )
        )
        await harness.callback(
            next(
                button.callback_data
                for row in harness.session.messages[-1].reply_markup.inline_keyboard
                for button in row
                if button.callback_data and button.callback_data.split(":")[2] == "slot"
            )
        )
        await harness.message("Ada Lovelace")
        await harness.message("not a phone")
        assert await harness.state() == "BookingForm:contact"
        assert harness.session.messages[-1].text == tr("error_phone_format", language=language)
        await harness.message("+1 (415) 555-0123")
        assert await harness.state() == "BookingForm:review"
        assert harness.session.messages[-1].text == tr(
            "client.review.booking",
            language=language,
            service_name="Consultation",
            date_time=expected_review_datetime,
            customer_name="Ada Lovelace",
            contact="+1 (415) 555-0123",
        )
        await harness.callback(_callback_for_action(harness, "submit"))
        harness.service.create_appointment.assert_awaited_once()
        assert harness.session.messages[-1].text == format_booking_confirmed(
            "SL-A-000001",
            "Consultation",
            starts_at,
            "UTC",
            language=language,
        )
        assert await harness.state() is None
    finally:
        await harness.close()
