"""Dispatcher flows are exercised with fake updates and an in-memory Bot API session."""

import asyncio
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramServerError
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Sticker, Update, User

from smartlead.bot.app import build_dispatcher
from smartlead.bot.keyboards import InlineKeyboardMarkup
from smartlead.config import Settings
from smartlead.db.models import BusinessSettings, Service
from smartlead.services.core import SmartLeadService
from smartlead.services.errors import SlotUnavailable
from tests.fake_telegram import RecordingSession

USER_ID = 101_010


def _settings(admin_ids: str = "", admin_notification_chat_id: int | None = None) -> Settings:
    return Settings(
        _env_file=None,
        bot_token="123456:telegram-test-token",
        database_url="postgresql+asyncpg://test@127.0.0.1/smartlead_test",
        admin_ids=admin_ids,
        admin_notification_chat_id=admin_notification_chat_id,
    )


def _service():
    configured_service = SimpleNamespace(spec=SimpleNamespace())
    # Existing English flow tests exercise an explicitly selected preference.
    # First-start and language-switch tests use an unselected profile separately.
    profiles = {}

    async def ensure_user(telegram_id, full_name, username=None):
        profile = profiles.setdefault(
            telegram_id, SimpleNamespace(telegram_id=telegram_id, language_code="en")
        )
        profile.full_name = full_name
        profile.username = username
        return profile

    async def get_user_language(telegram_id):
        return profiles.get(telegram_id, SimpleNamespace(language_code="en")).language_code

    async def set_user_language(telegram_id, language_code):
        profile = await ensure_user(telegram_id, "Test Customer")
        profile.language_code = language_code
        return profile

    configured_service.ensure_user = AsyncMock(side_effect=ensure_user)
    configured_service.get_user_language = AsyncMock(side_effect=get_user_language)
    configured_service.set_user_language = AsyncMock(side_effect=set_user_language)
    configured_service.business_settings = AsyncMock(
        return_value=SimpleNamespace(name="Test Studio", timezone="UTC", support_contact=None)
    )
    service_record = SimpleNamespace(id=1, name="Consultation", duration_minutes=30, is_active=True)
    configured_service.list_services = AsyncMock(return_value=[service_record])
    configured_service.get_service = AsyncMock(return_value=service_record)
    configured_service.available_dates = AsyncMock(
        return_value=[datetime.now(UTC).date() + timedelta(days=1)]
    )
    configured_service.available_slots = AsyncMock(
        return_value=[
            datetime.combine(
                datetime.now(UTC).date() + timedelta(days=1),
                datetime.min.time(),
                tzinfo=UTC,
            ).replace(hour=10)
        ]
    )
    configured_service.create_request = AsyncMock(
        return_value=SimpleNamespace(
            number="SL-R-000001",
            service_id=1,
            service_name="Consultation",
            status="new",
        )
    )
    configured_service.create_appointment = AsyncMock(
        return_value=SimpleNamespace(
            number="SL-A-000001",
            service_id=1,
            service_name="Consultation",
            starts_at=configured_service.available_slots.return_value[0],
            status="booked",
        )
    )
    return configured_service


class BotHarness:
    def __init__(
        self,
        service=None,
        *,
        admin_ids: str = "",
        admin_notification_chat_id: int | None = None,
    ) -> None:
        self.service = service or _service()
        self.session = RecordingSession()
        self.bot = Bot("123456:telegram-test-token", session=self.session)
        self.dispatcher = build_dispatcher(
            self.service, _settings(admin_ids, admin_notification_chat_id)
        )
        self.update_id = 0
        self.message_id = 0

    def _next_update(self) -> int:
        self.update_id += 1
        return self.update_id

    async def message(
        self,
        text: str,
        *,
        user_id: int = USER_ID,
        chat_id: int | None = None,
        chat_type: str = "private",
    ) -> None:
        self.message_id += 1
        user = User(id=user_id, is_bot=False, first_name="Test Customer")
        message = Message(
            message_id=self.message_id,
            date=datetime.now(UTC),
            chat=Chat(id=user_id if chat_id is None else chat_id, type=chat_type),
            from_user=user,
            text=text,
        )
        await self.dispatcher.feed_update(
            self.bot, Update(update_id=self._next_update(), message=message)
        )

    async def callback(
        self,
        data: str,
        *,
        user_id: int = USER_ID,
        message_index: int = -1,
        chat_id: int | None = None,
        chat_type: str = "private",
    ) -> None:
        message = self.session.messages[message_index]
        if chat_id is not None:
            message = message.model_copy(update={"chat": Chat(id=chat_id, type=chat_type)})
        callback = CallbackQuery(
            id=f"callback-{self._next_update()}",
            from_user=User(id=user_id, is_bot=False, first_name="Test Customer"),
            chat_instance="test-chat",
            message=message,
            data=data,
        )
        await self.dispatcher.feed_update(
            self.bot, Update(update_id=self._next_update(), callback_query=callback)
        )

    def data_for(self, label: str, *, message_index: int = -1) -> str:
        message = self.session.messages[message_index]
        markup = message.reply_markup
        assert isinstance(markup, InlineKeyboardMarkup)
        for row in markup.inline_keyboard:
            for button in row:
                if button.text == label:
                    assert button.callback_data is not None
                    return button.callback_data
        raise AssertionError(
            f"No inline button labeled {label!r}; last text={message.text!r}; markup={markup!r}"
        )

    def data_starting_with(self, prefix: str) -> str:
        message = self.session.messages[-1]
        markup = message.reply_markup
        assert isinstance(markup, InlineKeyboardMarkup)
        for row in markup.inline_keyboard:
            for button in row:
                if button.text and button.text.startswith(prefix):
                    assert button.callback_data is not None
                    return button.callback_data
        raise AssertionError(
            f"No inline button starting with {prefix!r}; last text={message.text!r}; "
            f"markup={markup!r}"
        )

    async def click(
        self,
        label: str,
        *,
        user_id: int = USER_ID,
        message_index: int = -1,
        chat_id: int | None = None,
        chat_type: str = "private",
    ) -> None:
        await self.callback(
            self.data_for(label, message_index=message_index),
            user_id=user_id,
            message_index=message_index,
            chat_id=chat_id,
            chat_type=chat_type,
        )

    async def state(self, user_id: int = USER_ID) -> str | None:
        key = StorageKey(bot_id=int(self.bot.id), chat_id=user_id, user_id=user_id)
        return await self.dispatcher.storage.get_state(key)

    async def data(self, user_id: int = USER_ID) -> dict:
        key = StorageKey(bot_id=int(self.bot.id), chat_id=user_id, user_id=user_id)
        return await self.dispatcher.storage.get_data(key)

    async def sticker(self, *, user_id: int = USER_ID) -> None:
        self.message_id += 1
        sticker = Sticker(
            file_id="test-file",
            file_unique_id="test-unique-file",
            type="regular",
            width=1,
            height=1,
            is_animated=False,
            is_video=False,
        )
        message = Message(
            message_id=self.message_id,
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="Test Customer"),
            sticker=sticker,
        )
        await self.dispatcher.feed_update(
            self.bot, Update(update_id=self._next_update(), message=message)
        )

    async def close(self) -> None:
        await self.dispatcher.storage.close()
        await self.bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent", [False, True], ids=["sequential", "concurrent"])
async def test_duplicate_update_id_is_processed_once(concurrent: bool):
    harness = BotHarness()
    try:
        await harness.message("/start")
        await harness.click("New request")
        await harness.click("Consultation · 30 min")
        assert await harness.state() == "RequestForm:name"

        previous_ensure_calls = harness.service.ensure_user.await_count
        previous_messages = len(harness.session.messages)
        harness.message_id += 1
        update = Update(
            update_id=harness._next_update(),
            message=Message(
                message_id=harness.message_id,
                date=datetime.now(UTC),
                chat=Chat(id=USER_ID, type="private"),
                from_user=User(id=USER_ID, is_bot=False, first_name="Test Customer"),
                text="Ada Lovelace",
            ),
        )

        if concurrent:
            await asyncio.gather(
                harness.dispatcher.feed_update(harness.bot, update),
                harness.dispatcher.feed_update(harness.bot, update),
            )
        else:
            await harness.dispatcher.feed_update(harness.bot, update)
            await harness.dispatcher.feed_update(harness.bot, update)

        assert await harness.state() == "RequestForm:contact"
        assert harness.service.ensure_user.await_count == previous_ensure_calls + 1
        assert len(harness.session.messages) == previous_messages + 1
        assert "phone number" in (harness.session.messages[-1].text or "").casefold()
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_request_commit_and_receipt_survive_expired_callback_ack(
    clean_pg, migrated_test_database_url
):
    settings = Settings(
        _env_file=None,
        bot_token="123456:integration-test-token",
        database_url=migrated_test_database_url,
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
        session.add(Service(name="Consultation", description="", duration_minutes=30))
    service = SmartLeadService(clean_pg, settings)
    await service.ensure_user(USER_ID, "Test Customer")
    await service.set_user_language(USER_ID, "en")
    harness = BotHarness(service)
    try:
        await harness.message("/start")
        await harness.click("New request")
        await harness.click("Consultation · 30 min")
        await harness.message("Ada Lovelace")
        await harness.message("+1 (415) 555-0123")
        await harness.message("Please call me about a consultation.")
        harness.session.fail_method_once = "AnswerCallbackQuery"

        await harness.click("Send request")

        stored = await service.list_requests(USER_ID, offset=0, limit=6)
        assert len(stored) == 1
        assert stored[0].contact == "+1 (415) 555-0123"
        assert "Request Submitted" in (harness.session.messages[-1].text or "")
        assert any(
            type(method).__name__ == "AnswerCallbackQuery" for method in harness.session.methods
        )
        assert harness.session.fail_method_once is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_request_form_completes_through_real_dispatcher_handlers():
    harness = BotHarness()
    try:
        await harness.message("/start")
        assert "Welcome to Test Studio" in (harness.session.messages[-1].text or "")
        await harness.click("New request")
        await harness.click("Consultation · 30 min")
        await harness.message("Ada Lovelace")
        await harness.message("Call me please")
        assert await harness.state() == "RequestForm:contact"
        assert "Enter a phone number" in (harness.session.messages[-1].text or "")
        assert (await harness.data())["customer_name"] == "Ada Lovelace"
        harness.data_for("Back")
        harness.data_for("Cancel")
        await harness.message("+1 (415) 555-0123")
        await harness.message("too short")
        assert await harness.state() == "RequestForm:details"
        assert "10–2000 characters" in (harness.session.messages[-1].text or "")
        await harness.message("I need help improving our lead follow-up process.")
        assert "Review your request" in (harness.session.messages[-1].text or "")
        await harness.click("Send request")

        harness.service.create_request.assert_awaited_once()
        args = harness.service.create_request.await_args.args
        assert args[:5] == (
            USER_ID,
            1,
            "Ada Lovelace",
            "+1 (415) 555-0123",
            "I need help improving our lead follow-up process.",
        )
        assert "SL-R-000001" in (harness.session.messages[-1].text or "")
        assert await harness.state() is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_request_receipt_survives_menu_navigation_and_lists_own_status():
    request = SimpleNamespace(
        id=87,
        number="SL-R-000087",
        service_id=1,
        service_name="Consultation",
        status="in_progress",
        created_at=datetime.now(UTC),
    )
    service = _service()
    service.create_request.return_value = request
    service.list_requests = AsyncMock(return_value=[request])
    harness = BotHarness(service)
    try:
        await harness.message("/start")
        await harness.click("New request")
        await harness.click("Consultation · 30 min")
        await harness.message("Ada Lovelace")
        await harness.message("+1 (415) 555-0123")
        await harness.message("I need help improving our lead follow-up process.")
        await harness.click("Send request")

        receipt_index = len(harness.session.messages) - 1
        receipt = harness.session.messages[receipt_index]
        receipt_text = receipt.text
        assert "Request Submitted" in (receipt_text or "")
        assert "Status: 🔵 In Progress" in (receipt_text or "")

        def assert_receipt_was_not_edited() -> None:
            assert not any(
                isinstance(method, EditMessageText)
                and int(method.chat_id) == receipt.chat.id
                and int(method.message_id) == receipt.message_id
                for method in harness.session.methods
            )

        await harness.click("Main Menu", message_index=receipt_index)
        assert "Choose an option" in (harness.session.messages[-1].text or "")
        assert receipt.text == receipt_text
        assert_receipt_was_not_edited()

        await harness.click("View My Requests", message_index=receipt_index)
        assert receipt.text == receipt_text
        assert_receipt_was_not_edited()
        assert "Your Requests" in (harness.session.messages[-1].text or "")
        assert "SL-R-000087" in (harness.session.messages[-1].text or "")
        assert "Status: 🔵 In Progress" in (harness.session.messages[-1].text or "")
        service.list_requests.assert_awaited_once_with(USER_ID, offset=0, limit=6)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_request_phone_error_keeps_back_and_cancel_actions_available():
    harness = BotHarness()
    try:
        await harness.message("/start")
        await harness.click("New request")
        await harness.click("Consultation · 30 min")
        await harness.message("Ada Lovelace")
        await harness.message("not a phone")

        assert await harness.state() == "RequestForm:contact"
        assert (await harness.data())["customer_name"] == "Ada Lovelace"
        await harness.click("Back")
        assert await harness.state() == "RequestForm:name"
        await harness.click("Back")
        assert await harness.state() == "RequestForm:service"
        await harness.click("Cancel")
        assert await harness.state() is None
        harness.service.create_request.assert_not_awaited()
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_booking_phone_error_can_be_cancelled_without_creating_appointment():
    harness = BotHarness()
    try:
        await harness.message("/start")
        await harness.click("Book a service")
        await harness.click("Consultation · 30 min")
        chosen_day = harness.service.available_dates.return_value[0]
        await harness.click(chosen_day.strftime("%a, %d %b"))
        selected_slot = harness.service.available_slots.return_value[0]
        await harness.callback(
            harness.data_starting_with(selected_slot.strftime("%a, %d %b 10:00"))
        )
        await harness.message("Ada Lovelace")
        await harness.message("not a phone")

        assert await harness.state() == "BookingForm:contact"
        assert (await harness.data())["customer_name"] == "Ada Lovelace"
        await harness.click("Cancel")
        assert await harness.state() is None
        harness.service.create_appointment.assert_not_awaited()
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_booking_form_presents_local_slots_and_confirms_selected_utc_instant():
    harness = BotHarness()
    try:
        await harness.message("/start")
        await harness.click("Book a service")
        await harness.click("Consultation · 30 min")
        chosen_day = harness.service.available_dates.return_value[0]
        await harness.click(chosen_day.strftime("%a, %d %b"))
        selected_slot = harness.service.available_slots.return_value[0]
        await harness.callback(
            harness.data_starting_with(selected_slot.strftime("%a, %d %b 10:00"))
        )
        await harness.message("Ada Lovelace")
        await harness.message("Call me please")
        assert await harness.state() == "BookingForm:contact"
        assert "Enter a phone number" in (harness.session.messages[-1].text or "")
        harness.data_for("Back")
        harness.data_for("Cancel")
        await harness.message("+1 (415) 555-0123")
        assert "Review your booking" in (harness.session.messages[-1].text or "")
        await harness.click("Book appointment")

        harness.service.create_appointment.assert_awaited_once()
        args = harness.service.create_appointment.await_args.args
        assert args[:5] == (USER_ID, 1, selected_slot, "Ada Lovelace", "+1 (415) 555-0123")
        assert "SL-A-000001" in (harness.session.messages[-1].text or "")
        assert await harness.state() is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_support_menu_uses_only_safe_urls_and_explains_unconfigured_state():
    for contact, expected_url in (
        ("https://t.me/smartlead_help", "https://t.me/smartlead_help"),
        (None, None),
        ("https://outside.example/support", None),
    ):
        service = _service()
        service.business_settings.return_value.support_contact = contact
        harness = BotHarness(service)
        try:
            await harness.message("/start")
            main_markup = harness.session.messages[-1].reply_markup
            main_support_button = next(
                button
                for row in main_markup.inline_keyboard
                for button in row
                if button.text == "Contact Support"
            )
            assert main_support_button.callback_data == "menu:support"
            await harness.click("Contact Support")
            support_markup = harness.session.messages[-1].reply_markup
            support_button = next(
                (
                    button
                    for row in support_markup.inline_keyboard
                    for button in row
                    if button.text == "Message Support"
                ),
                None,
            )
            if expected_url:
                assert support_button is not None
                assert support_button.url == expected_url
                assert support_button.callback_data is None
            else:
                assert support_button is None
                assert "not configured yet" in (harness.session.messages[-1].text or "")
        finally:
            await harness.close()


@pytest.mark.asyncio
async def test_booking_race_returns_to_slots_and_reuses_customer_data_and_submission_key():
    service = _service()
    chosen_day = service.available_dates.return_value[0]
    first_slot = datetime.combine(chosen_day, datetime.min.time(), tzinfo=UTC).replace(hour=10)
    replacement_slot = first_slot + timedelta(minutes=30)
    slot_calls = 0

    async def available_slots(*_args, **_kwargs):
        nonlocal slot_calls
        slot_calls += 1
        return [first_slot, replacement_slot] if slot_calls <= 2 else [replacement_slot]

    service.available_slots = AsyncMock(side_effect=available_slots)
    service.create_appointment = AsyncMock(
        side_effect=[
            SlotUnavailable("That time was just booked."),
            SimpleNamespace(
                number="SL-A-000002",
                service_id=1,
                service_name="Consultation",
                starts_at=replacement_slot,
                status="booked",
            ),
        ]
    )
    harness = BotHarness(service)
    try:
        await harness.message("/start")
        await harness.click("Book a service")
        await harness.click("Consultation · 30 min")
        await harness.click(chosen_day.strftime("%a, %d %b"))
        await harness.callback(harness.data_starting_with(first_slot.strftime("%a, %d %b 10:00")))
        await harness.message("Ada Lovelace")
        await harness.message("+1 (415) 555-0123")
        before = await harness.data()
        submission_key = before["submission_key"]
        assert await harness.state() == "BookingForm:review"

        await harness.click("Book appointment")
        assert await harness.state() == "BookingForm:slot"
        retry_text = harness.session.messages[-1].text or ""
        assert "That time was just booked" in retry_text
        assert "Your name and contact details are saved" in retry_text
        await harness.callback(
            harness.data_starting_with(replacement_slot.strftime("%a, %d %b 10:30"))
        )
        assert await harness.state() == "BookingForm:review"
        after = await harness.data()
        assert after["customer_name"] == "Ada Lovelace"
        assert after["contact"] == "+1 (415) 555-0123"
        assert after["submission_key"] == submission_key
        await harness.click("Book appointment")

        assert service.create_appointment.await_count == 2
        assert [call.args[-1] for call in service.create_appointment.await_args_list] == [
            UUID(submission_key),
            UUID(submission_key),
        ]
        assert service.create_appointment.await_args_list[1].args[2] == replacement_slot
        assert "SL-A-000002" in (harness.session.messages[-1].text or "")
        assert await harness.state() is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_back_cancel_and_old_form_buttons_do_not_submit_or_advance_form():
    harness = BotHarness()
    try:
        await harness.message("/start")
        await harness.click("New request")
        stale_service_button = harness.data_for("Consultation · 30 min")
        await harness.click("Consultation · 30 min")
        assert await harness.state() == "RequestForm:name"
        await harness.click("Back")
        assert await harness.state() == "RequestForm:service"

        get_service_calls = harness.service.get_service.await_count
        await harness.callback(stale_service_button)
        assert harness.service.get_service.await_count == get_service_calls
        assert await harness.state() == "RequestForm:service"

        await harness.click("Cancel")
        assert await harness.state() is None
        assert "Cancelled" in (harness.session.messages[-1].text or "")
        harness.service.create_request.assert_not_awaited()
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_nontext_update_does_not_advance_or_clear_a_text_input_state():
    harness = BotHarness()
    try:
        await harness.message("/start")
        await harness.click("New request")
        await harness.click("Consultation · 30 min")
        message_count = len(harness.session.messages)

        await harness.sticker()

        assert await harness.state() == "RequestForm:name"
        assert len(harness.session.messages) == message_count + 1
        assert "Please send a text message for your name" in (
            harness.session.messages[-1].text or ""
        )
        harness.data_for("Back")
        harness.data_for("Cancel")
        await harness.message("Ada Lovelace")
        assert await harness.state() == "RequestForm:contact"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_customer_can_cancel_their_own_upcoming_appointment_from_dispatcher():
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
    service = _service()
    service.list_appointments = AsyncMock(return_value=[appointment])
    service.get_appointment = AsyncMock(return_value=appointment)

    async def cancel(owner_id: int, appointment_id: int):
        assert owner_id == USER_ID
        assert appointment_id == appointment.id
        appointment.status = "cancelled"
        return appointment

    service.cancel_appointment = AsyncMock(side_effect=cancel)
    harness = BotHarness(service)
    try:
        await harness.message("/start")
        await harness.click("My appointments")
        await harness.click("SL-A-000087")
        await harness.click("Cancel appointment")
        assert "Cancel this appointment?" in (harness.session.messages[-1].text or "")
        confirmation_index = len(harness.session.messages) - 1
        stale_confirmation = harness.data_for("Yes, cancel")
        await harness.click("Yes, cancel")

        service.cancel_appointment.assert_awaited_once_with(USER_ID, appointment.id)
        receipt_index = len(harness.session.messages) - 1
        receipt = harness.session.messages[receipt_index]
        assert "Appointment Cancelled" in (receipt.text or "")
        assert f"Appointment ID: #{appointment.number}" in receipt.text
        assert "Status: 🔴 Cancelled" in receipt.text
        assert await harness.state() is None
        await harness.callback(stale_confirmation, message_index=confirmation_index)
        service.cancel_appointment.assert_awaited_once_with(USER_ID, appointment.id)
        assert receipt.text and "Appointment Cancelled" in receipt.text
        confirmations = [
            method
            for method in harness.session.methods
            if isinstance(method, SendMessage) and "Appointment Cancelled" in method.text
        ]
        assert len(confirmations) == 1
        assert not any(
            isinstance(method, EditMessageText)
            and int(method.chat_id) == receipt.chat.id
            and int(method.message_id) == receipt.message_id
            for method in harness.session.methods
        )
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_allowlisted_admin_changes_request_status_through_real_callbacks():
    admin_id = 900_001
    request = SimpleNamespace(
        id=42,
        number="SL-R-000042",
        service_id=1,
        service_name="Consultation",
        status="new",
        created_at=datetime.now(UTC),
        customer_name="Ada Lovelace",
        contact="+1 (415) 555-0123",
        details="Please contact me about a consultation.",
    )
    service = _service()
    service.list_requests = AsyncMock(return_value=[request])
    service.get_request = AsyncMock(return_value=request)

    async def change_status(actor_id: int, request_id: int, status: str):
        assert actor_id == admin_id
        assert request_id == request.id
        request.status = status
        return request

    service.change_request_status = AsyncMock(side_effect=change_status)
    harness = BotHarness(service, admin_ids=str(admin_id))
    try:
        await harness.message("/admin", user_id=admin_id)
        await harness.click("📋 All Requests", user_id=admin_id)
        await harness.click("View #SL-R-000042", user_id=admin_id)
        assert "Status: 🟡 New" in (harness.session.messages[-1].text or "")
        await harness.click("Start Processing", user_id=admin_id)
        assert "Status: 🔵 In Progress" in (harness.session.messages[-1].text or "")
        await harness.click("Mark Completed", user_id=admin_id)

        assert request.status == "completed"
        assert service.change_request_status.await_args_list[0].args == (
            admin_id,
            request.id,
            "in_progress",
        )
        assert service.change_request_status.await_args_list[1].args == (
            admin_id,
            request.id,
            "completed",
        )
        assert "Status: 🟢 Completed" in (harness.session.messages[-1].text or "")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_admin_new_request_filter_paginates_through_authorized_callbacks():
    admin_id = 900_001
    requests = [
        SimpleNamespace(
            id=50 + index,
            number=f"SL-R-0000{50 + index}",
            service_id=1,
            service_name="Consultation",
            status="new",
            created_at=datetime.now(UTC),
            customer_name=f"Demo Customer {index}",
            contact=f"+1 (415) 555-01{index:02d}",
            details="Please contact me about a consultation.",
        )
        for index in range(6)
    ]
    service = _service()
    service.list_requests = AsyncMock(return_value=requests)
    harness = BotHarness(service, admin_ids=str(admin_id))
    try:
        await harness.message("/admin", user_id=admin_id)
        await harness.click("📥 New Requests", user_id=admin_id)

        assert "New Requests" in (harness.session.messages[-1].text or "")
        service.list_requests.assert_awaited_once_with(
            admin_id,
            offset=0,
            limit=6,
            admin=True,
            status="new",
        )
        await harness.click("Next", user_id=admin_id)
        assert service.list_requests.await_args_list[1].kwargs == {
            "offset": 5,
            "limit": 6,
            "admin": True,
            "status": "new",
        }
        assert service.list_requests.await_args_list[1].args == (admin_id,)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_admin_appointment_list_and_details_show_saved_service_and_status():
    admin_id = 900_001
    appointment = SimpleNamespace(
        id=87,
        number="SL-A-000087",
        service_id=1,
        service_name="Historic consultation name",
        starts_at=datetime.now(UTC) + timedelta(days=1),
        ends_at=datetime.now(UTC) + timedelta(days=1, minutes=30),
        status="booked",
        customer_name="Demo Customer",
        contact="+1 (415) 555-0123",
    )
    service = _service()
    service.list_appointments = AsyncMock(return_value=[appointment])
    service.get_appointment = AsyncMock(return_value=appointment)
    harness = BotHarness(service, admin_ids=str(admin_id))
    try:
        await harness.message("/admin", user_id=admin_id)
        await harness.click("📅 Appointments", user_id=admin_id)

        service.list_appointments.assert_awaited_once_with(admin_id, offset=0, limit=6, admin=True)
        assert "Historic consultation name" in (harness.session.messages[-1].text or "")
        await harness.click("View #SL-A-000087", user_id=admin_id)
        assert "Historic consultation name" in (harness.session.messages[-1].text or "")
        assert "Status: 🟢 Booked" in (harness.session.messages[-1].text or "")
        assert service.get_appointment.await_args.kwargs == {"admin": True}
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_group_request_status_buttons_require_allowlisted_admin_and_configured_group():
    admin_id = 900_001
    group_id = -100_123_456_7890
    request = SimpleNamespace(
        id=42,
        number="SL-R-000042",
        service_id=1,
        service_name="Consultation",
        status="new",
        created_at=datetime.now(UTC),
        customer_name="Demo Customer",
        contact="+1 (415) 555-0123",
        details="Please contact me about a consultation.",
    )
    service = _service()
    service.get_request = AsyncMock(return_value=request)

    async def change_status(actor_id: int, request_id: int, status: str):
        assert actor_id == admin_id
        assert request_id == request.id
        request.status = status
        return request

    service.change_request_status = AsyncMock(side_effect=change_status)
    harness = BotHarness(
        service,
        admin_ids=str(admin_id),
        admin_notification_chat_id=group_id,
    )
    try:
        await harness.message("/admin", user_id=admin_id)
        admin_message = len(harness.session.messages) - 1
        action = f"admin:req:set:{request.id}:completed:0"

        await harness.callback(
            action,
            user_id=admin_id,
            message_index=admin_message,
            chat_id=group_id,
            chat_type="supergroup",
        )
        service.change_request_status.assert_awaited_once_with(admin_id, request.id, "completed")
        assert request.status == "completed"

        # A repeated Telegram delivery of the same button stays safe.
        await harness.callback(
            action,
            user_id=admin_id,
            message_index=admin_message,
            chat_id=group_id,
            chat_type="supergroup",
        )
        assert service.change_request_status.await_count == 2

        await harness.callback(
            action,
            user_id=USER_ID,
            message_index=admin_message,
            chat_id=group_id,
            chat_type="supergroup",
        )
        assert service.change_request_status.await_count == 2
        assert any(
            getattr(method, "text", None) == "Access denied." for method in harness.session.methods
        )

        await harness.callback(
            action,
            user_id=admin_id,
            message_index=admin_message,
            chat_id=group_id - 1,
            chat_type="supergroup",
        )
        assert service.change_request_status.await_count == 2

        methods_before_group_message = len(harness.session.methods)
        await harness.message(
            "/admin",
            user_id=admin_id,
            chat_id=group_id,
            chat_type="supergroup",
        )
        assert len(harness.session.methods) == methods_before_group_message
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_admin_service_wizard_saves_once_and_changes_availability():
    admin_id = 900_001
    service_record = SimpleNamespace(
        id=1,
        name="Consultation",
        description="",
        duration_minutes=30,
        is_active=True,
    )
    created = SimpleNamespace(id=22)
    service = _service()
    service.list_services_admin = AsyncMock(return_value=[service_record])
    service.create_service = AsyncMock(return_value=created)
    service.get_service = AsyncMock(return_value=service_record)
    service.set_service_active = AsyncMock(return_value=service_record)
    harness = BotHarness(service, admin_ids=str(admin_id))
    try:
        await harness.message("/admin", user_id=admin_id)
        await harness.click("⚙️ Services", user_id=admin_id)
        await harness.click("Add Service", user_id=admin_id)
        assert await harness.state(admin_id) == "ServiceForm:name"

        await harness.message("Portfolio consultation", user_id=admin_id)
        await harness.message("A short planning session.", user_id=admin_id)
        await harness.message("481", user_id=admin_id)
        assert await harness.state(admin_id) == "ServiceForm:duration"
        assert "1 to 480 minutes" in (harness.session.messages[-1].text or "")
        await harness.message("60", user_id=admin_id)
        assert await harness.state(admin_id) == "ServiceForm:review"
        review_index = len(harness.session.messages) - 1
        stale_save = harness.data_for("Save Service", message_index=review_index)
        await harness.click("Save Service", user_id=admin_id)

        service.create_service.assert_awaited_once_with(
            admin_id,
            name="Portfolio consultation",
            description="A short planning session.",
            duration_minutes=60,
        )
        assert await harness.state(admin_id) is None

        await harness.callback(stale_save, user_id=admin_id, message_index=review_index)
        assert service.create_service.await_count == 1

        await harness.click("Deactivate", user_id=admin_id)
        service.set_service_active.assert_awaited_once_with(admin_id, 1, False)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_admin_support_contact_can_be_set_and_cleared_from_private_form():
    admin_id = 900_001
    service = _service()
    service.update_support_contact = AsyncMock()
    service.business_settings.return_value.support_contact = None
    service.list_services_admin = AsyncMock(return_value=[])
    harness = BotHarness(service, admin_ids=str(admin_id))
    try:
        await harness.message("/admin", user_id=admin_id)
        await harness.click("⚙️ Services", user_id=admin_id)
        await harness.click("Support Contact", user_id=admin_id)
        await harness.message("@smartlead_help", user_id=admin_id)
        service.update_support_contact.assert_awaited_once_with(admin_id, "@smartlead_help")

        await harness.message("/admin", user_id=admin_id)
        await harness.click("⚙️ Services", user_id=admin_id)
        await harness.click("Support Contact", user_id=admin_id)
        await harness.message("-", user_id=admin_id)
        assert service.update_support_contact.await_args_list[1].args == (admin_id, None)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_unallowlisted_admin_cannot_open_admin_menu_or_call_admin_actions():
    harness = BotHarness()
    try:
        await harness.message("/admin")
        assert harness.session.messages[-1].text == "Access denied."
        await harness.callback("admin:stats", user_id=USER_ID)
        assert not hasattr(harness.service, "statistics")
        answers = [
            method
            for method in harness.session.methods
            if type(method).__name__ == "AnswerCallbackQuery"
        ]
        assert answers[-1].text == "Access denied."
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_api_exception_logs_do_not_echo_phone_or_credentials(caplog):
    harness = BotHarness()
    phone = "+1 (415) 555-0199"
    token = "123456:telegram-test-token"
    database_url = "postgresql+asyncpg://test:private-password@localhost/smartlead_test"
    try:
        await harness.message("/start")
        harness.service.business_settings.side_effect = TelegramServerError(
            SendMessage(chat_id=USER_ID, text="test"),
            f"API failed for {phone}; bot={token}; database={database_url}",
        )
        with caplog.at_level("ERROR", logger="smartlead.bot.middleware"):
            await harness.message("/start")

        logged = caplog.text
        assert "Telegram handler failed (TelegramServerError)" in logged
        assert phone not in logged
        assert token not in logged
        assert "private-password" not in logged
        assert "Something went wrong" in (harness.session.messages[-1].text or "")
        assert phone not in (harness.session.messages[-1].text or "")
    finally:
        await harness.close()
