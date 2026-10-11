"""Private client commands, request intake and appointment self-service."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from smartlead.bot.keyboards import (
    form_callback,
    form_keyboard,
    keyboard,
    main_menu,
    pagination,
    support_menu,
)
from smartlead.bot.middleware import CurrentForm
from smartlead.bot.states import BookingForm, RequestForm
from smartlead.config import Settings
from smartlead.i18n import SUPPORTED_LANGUAGES, tr, use_language
from smartlead.presentation import (
    format_booking_cancelled,
    format_booking_confirmed,
    format_business_date,
    format_business_datetime,
    format_business_time,
    format_calendar_datetime,
    format_calendar_day,
    format_request_submitted,
    status_label,
)
from smartlead.services.core import SmartLeadService
from smartlead.services.errors import SlotUnavailable, ValidationError
from smartlead.services.validation import validate_details, validate_name, validate_phone

router = Router(name="client")

PAGE_SIZE = 5
DATE_PAGE_SIZE = 6
SLOT_PAGE_SIZE = 8


def _contact_prompt() -> str:
    return tr("client.contact_prompt")


def _new_token() -> str:
    # Short enough for Telegram's 64-byte callback_data limit, including a UTC slot key.
    return uuid4().hex[:12]


async def _answer_plain(message: Message, text: str, **kwargs):
    return await message.answer(text, parse_mode=None, **kwargs)


async def _edit_plain(message: Message, text: str, **kwargs):
    return await message.edit_text(text, parse_mode=None, **kwargs)


async def _answer_callback_safely(callback: CallbackQuery, text: str) -> None:
    """A stale callback must not suppress a durable receipt after a DB commit."""
    try:
        await callback.answer(text)
    except TelegramAPIError:
        pass


def _safe_support_url(value: object) -> str | None:
    """Accept only canonical Telegram profile links for an inline URL button."""
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.casefold() != "https"
        or parsed.netloc.casefold() != "t.me"
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/[A-Za-z][A-Za-z0-9_]{4,31}", parsed.path)
    ):
        return None
    return f"https://t.me{parsed.path}"


async def _main_menu(_service: SmartLeadService):
    return main_menu()


def _language_keyboard(*, include_back: bool = False):
    rows = [
        [(native_name, f"language:{code}")] for code, native_name in SUPPORTED_LANGUAGES.items()
    ]
    if include_back:
        rows.append([(tr("button.main_menu_plain"), "nav:menu")])
    return keyboard(rows)


async def _service_name(service: SmartLeadService, item: object) -> str:
    snapshot = getattr(item, "service_name", None)
    if snapshot:
        return str(snapshot)
    record = await service.get_service(int(item.service_id))
    return record.name


async def _service_rows(service: SmartLeadService, token: str) -> list[list[tuple[str, str]]]:
    rows = []
    for item in await service.list_services():
        if item.is_active:
            suffix = tr("client.service.duration_suffix", duration=item.duration_minutes)
            label = f"{item.name[: 64 - len(suffix)]}{suffix}"
            rows.append([(label, form_callback(token, "service", item.id))])
    return rows


async def _render_services(
    event: Message | CallbackQuery,
    state: FSMContext,
    service: SmartLeadService,
    *,
    back: bool,
    notice: str | None = None,
) -> None:
    token = _new_token()
    await state.set_state(
        RequestForm.service
        if (await state.get_data()).get("kind") == "request"
        else BookingForm.service
    )
    await state.update_data(form_token=token)
    rows = await _service_rows(service, token)
    if not rows:
        text = tr("client.services.empty")
        markup = form_keyboard(token, back=back)
    else:
        text = tr("client.services.choose")
        markup = form_keyboard(token, rows, back=back)
    if notice:
        text = f"{notice}\n\n{text}"
    if isinstance(event, CallbackQuery):
        await event.answer()
        if isinstance(event.message, Message):
            await _edit_plain(event.message, text, reply_markup=markup)
    else:
        await _answer_plain(event, text, reply_markup=markup)


async def _prompt_text(
    event: Message | CallbackQuery,
    state: FSMContext,
    next_state: object,
    prompt: str,
) -> None:
    token = _new_token()
    await state.set_state(next_state)
    await state.update_data(form_token=token)
    if isinstance(event, CallbackQuery):
        await event.answer()
        if isinstance(event.message, Message):
            await _edit_plain(event.message, prompt, reply_markup=form_keyboard(token))
    else:
        await _answer_plain(event, prompt, reply_markup=form_keyboard(token))


async def _form_feedback(message: Message, state: FSMContext, text: str) -> None:
    """Show a retry message with fresh Back/Cancel buttons for the same step."""
    token = _new_token()
    await state.update_data(form_token=token)
    await _answer_plain(message, text, reply_markup=form_keyboard(token))


async def _show_dates(
    event: Message | CallbackQuery,
    state: FSMContext,
    service: SmartLeadService,
    page: int = 0,
    notice: str | None = None,
) -> None:
    data = await state.get_data()
    service_id = int(data["service_id"])
    dates = await service.available_dates(service_id)
    pages = max(1, (len(dates) + DATE_PAGE_SIZE - 1) // DATE_PAGE_SIZE)
    page = min(max(0, page), pages - 1)
    visible = dates[page * DATE_PAGE_SIZE : (page + 1) * DATE_PAGE_SIZE]
    token = _new_token()
    await state.set_state(BookingForm.date)
    await state.update_data(form_token=token, date_page=page)
    rows = [
        [(format_calendar_day(day), form_callback(token, "date", day.strftime("%Y%m%d")))]
        for day in visible
    ]
    nav = []
    if page:
        nav.append((tr("button.previous"), form_callback(token, "dates", page - 1)))
    if page + 1 < pages:
        nav.append((tr("button.next"), form_callback(token, "dates", page + 1)))
    if nav:
        rows.append(nav)
    business = await service.business_settings()
    text = (
        tr("client.booking.choose_date", timezone=business.timezone)
        if visible
        else tr("client.booking.no_dates", timezone=business.timezone)
    )
    if notice:
        text = f"{notice}\n\n{text}"
    markup = form_keyboard(token, rows)
    if isinstance(event, CallbackQuery):
        await event.answer()
        if isinstance(event.message, Message):
            await _edit_plain(event.message, text, reply_markup=markup)
    else:
        await _answer_plain(event, text, reply_markup=markup)


def _utc_key(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%d%H%M")


def _from_utc_key(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d%H%M").replace(tzinfo=UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_local_slot(value: datetime, timezone_name: str) -> str:
    return tr(
        "client.booking.slot_label",
        date_time=format_calendar_datetime(value, timezone_name),
    )


async def _show_slots(
    event: Message | CallbackQuery,
    state: FSMContext,
    service: SmartLeadService,
    page: int = 0,
    notice: str | None = None,
) -> None:
    data = await state.get_data()
    service_id = int(data["service_id"])
    chosen_day = date.fromisoformat(data["booking_date"])
    business = await service.business_settings()
    slots = await service.available_slots(service_id, chosen_day)
    pages = max(1, (len(slots) + SLOT_PAGE_SIZE - 1) // SLOT_PAGE_SIZE)
    page = min(max(0, page), pages - 1)
    visible = slots[page * SLOT_PAGE_SIZE : (page + 1) * SLOT_PAGE_SIZE]
    token = _new_token()
    await state.set_state(BookingForm.slot)
    await state.update_data(form_token=token, slot_page=page)
    rows = [
        [
            (
                _format_local_slot(slot, business.timezone),
                form_callback(token, "slot", _utc_key(slot)),
            )
        ]
        for slot in visible
    ]
    nav = []
    if page:
        nav.append((tr("button.previous"), form_callback(token, "slots", page - 1)))
    if page + 1 < pages:
        nav.append((tr("button.next"), form_callback(token, "slots", page + 1)))
    if nav:
        rows.append(nav)
    text = (
        tr(
            "client.booking.available_times",
            date=format_business_date(chosen_day, business.timezone),
        )
        if visible
        else tr("client.booking.no_times", date=format_business_date(chosen_day, business.timezone))
    )
    if notice:
        text = f"{notice}\n\n{text}"
    markup = form_keyboard(token, rows)
    if isinstance(event, CallbackQuery):
        await event.answer()
        if isinstance(event.message, Message):
            await _edit_plain(event.message, text, reply_markup=markup)
    else:
        await _answer_plain(event, text, reply_markup=markup)


async def _show_review(
    event: Message | CallbackQuery,
    state: FSMContext,
    service: SmartLeadService,
    notice: str | None = None,
) -> None:
    data = await state.get_data()
    kind = data.get("kind")
    service_record = await service.get_service(int(data["service_id"]))
    if kind == "request":
        text = tr(
            "client.review.request",
            service_name=service_record.name,
            customer_name=data["customer_name"],
            contact=data["contact"],
            details=data["details"],
        )
        target_state = RequestForm.review
    else:
        business = await service.business_settings()
        starts_at = _from_utc_key(data["starts_at"])
        text = tr(
            "client.review.booking",
            service_name=service_record.name,
            date_time=format_business_datetime(starts_at, business.timezone),
            customer_name=data["customer_name"],
            contact=data["contact"],
        )
        target_state = BookingForm.review
    if notice:
        text = f"{notice}\n\n{text}"
    token = _new_token()
    await state.set_state(target_state)
    await state.update_data(form_token=token)
    markup = form_keyboard(
        token,
        [
            [
                (
                    tr("client.button.submit_request")
                    if kind == "request"
                    else tr("client.button.submit_booking"),
                    form_callback(token, "submit"),
                )
            ]
        ],
        back=True,
    )
    if isinstance(event, CallbackQuery):
        await event.answer()
        if isinstance(event.message, Message):
            await _edit_plain(event.message, text, reply_markup=markup)
    else:
        await _answer_plain(event, text, reply_markup=markup)


async def _form_navigation(
    callback: CallbackQuery,
    state: FSMContext,
    service: SmartLeadService,
) -> None:
    parts = (callback.data or "").split(":")
    action = parts[2]
    if action == "cancel":
        await state.clear()
        await callback.answer(tr("client.form.cancelled"))
        if isinstance(callback.message, Message):
            await _edit_plain(
                callback.message,
                tr("menu.cancelled_choose_option"),
                reply_markup=await _main_menu(service),
            )
        return
    if action == "back":
        current = await state.get_state()
        data = await state.get_data()
        if current == RequestForm.service.state or current == BookingForm.service.state:
            await state.clear()
            await callback.answer()
            if isinstance(callback.message, Message):
                await _edit_plain(
                    callback.message,
                    tr("menu.choose_option"),
                    reply_markup=await _main_menu(service),
                )
        elif current == RequestForm.name.state:
            await _render_services(callback, state, service, back=True)
        elif current == RequestForm.contact.state:
            await _prompt_text(
                callback,
                state,
                RequestForm.name,
                tr(
                    "client.form.current_name",
                    current_name=data.get("customer_name", tr("client.form.not_set")),
                ),
            )
        elif current == RequestForm.details.state:
            await _prompt_text(
                callback,
                state,
                RequestForm.contact,
                tr(
                    "client.form.current_contact",
                    contact_prompt=_contact_prompt(),
                    current_contact=data.get("contact", tr("client.form.not_set")),
                ),
            )
        elif current == RequestForm.review.state:
            await _prompt_text(
                callback,
                state,
                RequestForm.details,
                tr("client.form.current_details", details=data.get("details", "")),
            )
        elif current == BookingForm.date.state:
            await _render_services(callback, state, service, back=True)
        elif current == BookingForm.slot.state:
            await _show_dates(callback, state, service, int(data.get("date_page", 0)))
        elif current == BookingForm.name.state:
            await _show_slots(callback, state, service, int(data.get("slot_page", 0)))
        elif current == BookingForm.contact.state:
            await _prompt_text(
                callback,
                state,
                BookingForm.name,
                tr(
                    "client.form.current_name",
                    current_name=data.get("customer_name", tr("client.form.not_set")),
                ),
            )
        elif current == BookingForm.review.state:
            await _prompt_text(
                callback,
                state,
                BookingForm.contact,
                tr(
                    "client.form.current_contact",
                    contact_prompt=_contact_prompt(),
                    current_contact=data.get("contact", tr("client.form.not_set")),
                ),
            )
        return
    await callback.answer(tr("client.form.stale_button"), show_alert=True)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, service: SmartLeadService, profile) -> None:
    await state.clear()
    if profile.language_code is None:
        await _answer_plain(
            message,
            tr("language.choose"),
            reply_markup=_language_keyboard(),
        )
        return
    business = await service.business_settings()
    await _answer_plain(
        message,
        tr("start.welcome", business_name=business.name),
        reply_markup=main_menu(),
    )


@router.message(Command("menu"))
async def menu(message: Message, state: FSMContext, service: SmartLeadService) -> None:
    await state.clear()
    await _answer_plain(message, tr("menu.choose_option"), reply_markup=await _main_menu(service))


@router.message(Command("language"))
async def language_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _answer_plain(
        message, tr("language.choose"), reply_markup=_language_keyboard(include_back=True)
    )


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext, service: SmartLeadService) -> None:
    await state.clear()
    await _answer_plain(
        message,
        tr("menu.cancelled_choose_option"),
        reply_markup=await _main_menu(service),
    )


@router.message(Command("help"))
async def help_command(message: Message, settings: Settings) -> None:
    lines = [
        tr("client.help.use_buttons"),
        tr("client.help.commands"),
    ]
    if message.from_user and message.from_user.id in settings.allowed_admin_ids:
        lines.append(tr("client.help.admin"))
    await _answer_plain(message, "\n".join(lines))


@router.message(Command("id"))
async def id_command(message: Message) -> None:
    if message.from_user:
        await _answer_plain(message, tr("client.telegram_id", telegram_id=message.from_user.id))


@router.callback_query(F.data == "menu:new")
async def begin_request(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    await state.clear()
    await state.update_data(kind="request", submission_key=str(uuid4()))
    await _render_services(callback, state, service, back=True)


@router.callback_query(F.data == "menu:book")
async def begin_booking(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    await state.clear()
    await state.update_data(kind="booking", submission_key=str(uuid4()))
    await _render_services(callback, state, service, back=True)


@router.callback_query(F.data == "nav:menu")
async def return_to_menu(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    await state.clear()
    await callback.answer()
    if isinstance(callback.message, Message):
        await _edit_plain(
            callback.message,
            tr("menu.choose_option"),
            reply_markup=await _main_menu(service),
        )


@router.callback_query(F.data == "nav:menu:new")
async def send_main_menu(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    """Open a fresh menu from an important receipt without replacing that receipt."""
    await state.clear()
    await callback.answer()
    if isinstance(callback.message, Message):
        await _answer_plain(
            callback.message,
            tr("menu.choose_option"),
            reply_markup=await _main_menu(service),
        )


@router.callback_query(F.data == "menu:support")
async def contact_support(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    await state.clear()
    business = await service.business_settings()
    support_url = _safe_support_url(getattr(business, "support_contact", None))
    text = tr("client.support.screen")
    if not support_url:
        text += "\n\n" + tr("client.support.not_configured")
    await callback.answer()
    if isinstance(callback.message, Message):
        await _edit_plain(
            callback.message,
            text,
            reply_markup=support_menu(support_url),
        )


@router.callback_query(F.data == "menu:language")
async def choose_language(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    if isinstance(callback.message, Message):
        await _edit_plain(
            callback.message,
            tr("language.choose"),
            reply_markup=_language_keyboard(include_back=True),
        )


@router.callback_query(F.data.startswith("language:"))
async def save_language(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    code = (callback.data or "").split(":", 1)[1]
    if code not in SUPPORTED_LANGUAGES:
        await callback.answer(tr("error_invalid_language"), show_alert=True)
        return
    await state.clear()
    await service.set_user_language(callback.from_user.id, code)
    with use_language(code):
        await _answer_callback_safely(callback, tr("language.saved"))
        if isinstance(callback.message, Message):
            await _edit_plain(
                callback.message,
                tr("menu.choose_option"),
                reply_markup=main_menu(),
            )


@router.callback_query(
    CurrentForm(),
    StateFilter(
        RequestForm.service,
        RequestForm.name,
        RequestForm.contact,
        RequestForm.details,
        RequestForm.review,
        BookingForm.service,
        BookingForm.date,
        BookingForm.slot,
        BookingForm.name,
        BookingForm.contact,
        BookingForm.review,
    ),
    F.data.startswith("f:"),
    F.data.split(":")[2].in_({"back", "cancel"}),
)
async def form_back_or_cancel(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    await _form_navigation(callback, state, service)


@router.callback_query(
    CurrentForm(),
    StateFilter(RequestForm.service, BookingForm.service),
    F.data.startswith("f:"),
    F.data.split(":")[2] == "service",
)
async def choose_service(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    service_id = int((callback.data or "").split(":")[3])
    record = await service.get_service(service_id)
    if not record.is_active:
        await callback.answer(tr("client.form.service_unavailable"), show_alert=True)
        return
    data = await state.get_data()
    await state.update_data(service_id=service_id)
    if data.get("kind") == "request":
        await _prompt_text(callback, state, RequestForm.name, tr("client.form.choose_name"))
    else:
        await _show_dates(callback, state, service)


@router.callback_query(
    CurrentForm(),
    StateFilter(BookingForm.date),
    F.data.startswith("f:"),
    F.data.split(":")[2] == "dates",
)
async def change_date_page(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    await _show_dates(callback, state, service, int((callback.data or "").split(":")[3]))


@router.callback_query(
    CurrentForm(),
    StateFilter(BookingForm.date),
    F.data.startswith("f:"),
    F.data.split(":")[2] == "date",
)
async def choose_date(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    value = (callback.data or "").split(":")[3]
    chosen_day = datetime.strptime(value, "%Y%m%d").date()
    data = await state.get_data()
    dates = await service.available_dates(int(data["service_id"]))
    if chosen_day not in dates:
        await _show_dates(callback, state, service, int(data.get("date_page", 0)))
        return
    await state.update_data(booking_date=chosen_day.isoformat(), slot_page=0)
    await _show_slots(callback, state, service)


@router.callback_query(
    CurrentForm(),
    StateFilter(BookingForm.slot),
    F.data.startswith("f:"),
    F.data.split(":")[2] == "slots",
)
async def change_slot_page(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    await _show_slots(callback, state, service, int((callback.data or "").split(":")[3]))


@router.callback_query(
    CurrentForm(),
    StateFilter(BookingForm.slot),
    F.data.startswith("f:"),
    F.data.split(":")[2] == "slot",
)
async def choose_slot(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    key = (callback.data or "").split(":")[3]
    starts_at = _from_utc_key(key)
    data = await state.get_data()
    available = await service.available_slots(
        int(data["service_id"]), date.fromisoformat(data["booking_date"])
    )
    if starts_at not in available:
        await _show_slots(
            callback,
            state,
            service,
            int(data.get("slot_page", 0)),
            notice=tr("client.form.time_unavailable"),
        )
        return
    await state.update_data(starts_at=key)
    if data.get("customer_name") and data.get("contact"):
        await _show_review(callback, state, service)
    else:
        await _prompt_text(callback, state, BookingForm.name, tr("client.form.choose_name"))


@router.message(StateFilter(RequestForm.name, BookingForm.name), F.text, ~F.text.startswith("/"))
async def collect_name(message: Message, state: FSMContext) -> None:
    try:
        value = validate_name(message.text or "")
    except ValidationError as exc:
        await _form_feedback(message, state, str(exc))
        return
    data = await state.get_data()
    await state.update_data(customer_name=value)
    if data.get("kind") == "request":
        await _prompt_text(message, state, RequestForm.contact, _contact_prompt())
    else:
        await _prompt_text(message, state, BookingForm.contact, _contact_prompt())


@router.message(
    StateFilter(RequestForm.contact, BookingForm.contact), F.text, ~F.text.startswith("/")
)
async def collect_contact(message: Message, state: FSMContext, service: SmartLeadService) -> None:
    try:
        value = validate_phone(message.text or "")
    except ValidationError as exc:
        await _form_feedback(message, state, str(exc))
        return
    data = await state.get_data()
    await state.update_data(contact=value)
    if data.get("kind") == "request":
        await _prompt_text(
            message,
            state,
            RequestForm.details,
            tr("client.form.describe_request"),
        )
    else:
        await _show_review(message, state, service)


@router.message(StateFilter(RequestForm.details), F.text, ~F.text.startswith("/"))
async def collect_details(message: Message, state: FSMContext, service: SmartLeadService) -> None:
    try:
        value = validate_details(message.text or "")
    except ValidationError as exc:
        await _form_feedback(message, state, str(exc))
        return
    await state.update_data(details=value)
    await _show_review(message, state, service)


@router.message(StateFilter(RequestForm.service, BookingForm.service))
async def repeat_service_choices(
    message: Message, state: FSMContext, service: SmartLeadService
) -> None:
    await _render_services(
        message,
        state,
        service,
        back=True,
        notice=tr("client.form.repeat_service"),
    )


@router.message(StateFilter(BookingForm.date))
async def repeat_date_choices(
    message: Message, state: FSMContext, service: SmartLeadService
) -> None:
    data = await state.get_data()
    await _show_dates(
        message,
        state,
        service,
        int(data.get("date_page", 0)),
        notice=tr("client.form.repeat_date"),
    )


@router.message(StateFilter(BookingForm.slot))
async def repeat_slot_choices(
    message: Message, state: FSMContext, service: SmartLeadService
) -> None:
    data = await state.get_data()
    await _show_slots(
        message,
        state,
        service,
        int(data.get("slot_page", 0)),
        notice=tr("client.form.repeat_time"),
    )


@router.message(StateFilter(RequestForm.review, BookingForm.review))
async def repeat_review_actions(
    message: Message, state: FSMContext, service: SmartLeadService
) -> None:
    await _show_review(
        message,
        state,
        service,
        notice=tr("client.form.repeat_review"),
    )


@router.message(
    StateFilter(
        RequestForm.name,
        RequestForm.contact,
        RequestForm.details,
        BookingForm.name,
        BookingForm.contact,
    ),
    ~F.text,
)
async def require_text_input(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    field = {
        RequestForm.name.state: tr("client.form.no_text_field_name"),
        RequestForm.contact.state: tr("client.form.no_text_field_phone"),
        RequestForm.details.state: tr("client.form.no_text_field_details"),
        BookingForm.name.state: tr("client.form.no_text_field_name"),
        BookingForm.contact.state: tr("client.form.no_text_field_phone"),
    }.get(current, tr("client.form.no_text_field_generic"))
    await _form_feedback(
        message,
        state,
        tr("client.form.require_text", field=field),
    )


@router.message(
    StateFilter(
        RequestForm.name,
        RequestForm.contact,
        RequestForm.details,
        BookingForm.name,
        BookingForm.contact,
    ),
    F.text.startswith("/"),
)
async def reject_unrecognized_form_command(message: Message, state: FSMContext) -> None:
    await _form_feedback(
        message,
        state,
        tr("client.form.unrecognized_command"),
    )


@router.callback_query(
    CurrentForm(),
    StateFilter(RequestForm.review, BookingForm.review),
    F.data.startswith("f:"),
    F.data.split(":")[2] == "submit",
)
async def submit_form(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    data = await state.get_data()
    key = UUID(data["submission_key"])
    if data.get("kind") == "request":
        record = await service.create_request(
            callback.from_user.id,
            int(data["service_id"]),
            data["customer_name"],
            data["contact"],
            data["details"],
            key,
        )
        service_name = await _service_name(service, record)
        result = format_request_submitted(record.number, service_name, record.status)
        buttons = keyboard(
            [
                [(tr("client.receipt.view_requests"), "nav:requests:0")],
                [(tr("button.main_menu_plain"), "nav:menu:new")],
            ]
        )
    else:
        try:
            record = await service.create_appointment(
                callback.from_user.id,
                int(data["service_id"]),
                _from_utc_key(data["starts_at"]),
                data["customer_name"],
                data["contact"],
                key,
            )
        except SlotUnavailable:
            await _show_slots(
                callback,
                state,
                service,
                int(data.get("slot_page", 0)),
                notice=tr("client.form.slot_taken"),
            )
            return
        business = await service.business_settings()
        service_name = await _service_name(service, record)
        result = format_booking_confirmed(
            record.number,
            service_name,
            record.starts_at,
            business.timezone,
            getattr(record, "status", "booked"),
        )
        buttons = keyboard(
            [
                [(tr("client.receipt.my_appointments"), "nav:appointments:0")],
                [(tr("button.main_menu_plain"), "nav:menu:new")],
            ]
        )
    await state.clear()
    await _answer_callback_safely(
        callback,
        tr("client.receipt.request_button")
        if data.get("kind") == "request"
        else tr("client.receipt.booking_button"),
    )
    if isinstance(callback.message, Message):
        # Deliver the durable receipt first. Editing the review prompt is only
        # cosmetic and must not suppress the confirmation if Telegram rejects it.
        await _answer_plain(callback.message, result, reply_markup=buttons)
        try:
            await _edit_plain(
                callback.message,
                tr("client.receipt.request_notice")
                if data.get("kind") == "request"
                else tr("client.receipt.booking_notice"),
                reply_markup=None,
            )
        except (TelegramAPIError, OSError, TimeoutError):
            pass


async def _show_items(
    callback: CallbackQuery,
    state: FSMContext,
    service: SmartLeadService,
    *,
    kind: str,
    page: int,
    preserve_history: bool = False,
) -> None:
    owner = callback.from_user.id
    offset = page * PAGE_SIZE
    business = await service.business_settings()
    # A history/list callback starts a new context; old form states must not
    # consume the customer's next text message.
    await state.clear()
    if kind == "request":
        items = await service.list_requests(owner, offset=offset, limit=PAGE_SIZE + 1)
        title = tr("client.list.requests_title")
    else:
        items = await service.list_appointments(
            owner, offset=offset, limit=PAGE_SIZE + 1, upcoming=True
        )
        title = tr("client.list.appointments_title")
    has_next = len(items) > PAGE_SIZE
    items = items[:PAGE_SIZE]
    rows: list[list[tuple[str, str]]] = []
    lines = [title]
    for item in items:
        service_name = await _service_name(service, item)
        number = item.number
        if kind == "request":
            created = format_business_date(item.created_at, business.timezone)
            lines.append(
                tr(
                    "client.list.request_row",
                    number=number,
                    service_name=service_name,
                    created_at=created,
                    status=status_label(item.status),
                )
            )
            callback_data = f"view:req:{item.id}:{page}"
        else:
            local_date = format_business_date(item.starts_at, business.timezone)
            local_time = format_business_time(item.starts_at, business.timezone)
            lines.append(
                tr(
                    "client.list.appointment_row",
                    number=number,
                    service_name=service_name,
                    date=local_date,
                    time=local_time,
                    status=status_label(item.status),
                )
            )
            callback_data = f"view:appt:{item.id}:{page}"
        rows.append([(number, callback_data)])
    nav = pagination(f"mine:{'req' if kind == 'request' else 'appt'}", page, has_next)
    if nav:
        rows.append(nav)
    if not items:
        if kind == "request":
            lines.append(tr("client.list.empty_requests"))
            rows.append([(tr("client.list.create_request"), "menu:new")])
        else:
            lines.append(tr("client.list.empty_appointments"))
            rows.append([(tr("client.list.book_service"), "menu:book")])
    rows.append([(tr("button.main_menu_plain"), "nav:menu")])
    await callback.answer()
    if isinstance(callback.message, Message):
        text = "\n".join(lines)
        markup = keyboard(rows)
        if preserve_history:
            await _answer_plain(callback.message, text, reply_markup=markup)
        else:
            await _edit_plain(callback.message, text, reply_markup=markup)


@router.callback_query(F.data.startswith("menu:requests:"))
async def my_requests(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    await state.clear()
    page = int((callback.data or "").rsplit(":", 1)[1])
    await _show_items(callback, state, service, kind="request", page=max(0, page))


@router.callback_query(F.data.startswith("menu:appointments:"))
async def my_appointments(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    await state.clear()
    page = int((callback.data or "").rsplit(":", 1)[1])
    await _show_items(callback, state, service, kind="appointment", page=max(0, page))


@router.callback_query(F.data.startswith("nav:requests:"))
async def show_requests_without_replacing_receipt(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    await state.clear()
    page = int((callback.data or "").rsplit(":", 1)[1])
    await _show_items(
        callback,
        state,
        service,
        kind="request",
        page=max(0, page),
        preserve_history=True,
    )


@router.callback_query(F.data.startswith("nav:appointments:"))
async def show_appointments_without_replacing_receipt(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    await state.clear()
    page = int((callback.data or "").rsplit(":", 1)[1])
    await _show_items(
        callback,
        state,
        service,
        kind="appointment",
        page=max(0, page),
        preserve_history=True,
    )


@router.callback_query(F.data.startswith("mine:req:"))
async def page_my_requests(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    page = int((callback.data or "").rsplit(":", 1)[1])
    await _show_items(callback, state, service, kind="request", page=max(0, page))


@router.callback_query(F.data.startswith("mine:appt:"))
async def page_my_appointments(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    page = int((callback.data or "").rsplit(":", 1)[1])
    await _show_items(callback, state, service, kind="appointment", page=max(0, page))


@router.callback_query(F.data.startswith("view:req:"))
async def view_request(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    _, _, raw_id, raw_page = (callback.data or "").split(":")
    await state.clear()
    item = await service.get_request(callback.from_user.id, int(raw_id))
    service_name = await _service_name(service, item)
    business = await service.business_settings()
    text = tr(
        "client.detail.request_template",
        number=item.number,
        service_name=service_name,
        status=status_label(item.status),
        created_at=format_business_datetime(item.created_at, business.timezone),
        customer_name=item.customer_name,
        contact=item.contact,
        details=item.details,
    )
    await state.update_data(active_cancel_token=None, active_cancel_id=None)
    await callback.answer()
    if isinstance(callback.message, Message):
        await _edit_plain(
            callback.message,
            text[:4000],
            reply_markup=keyboard(
                [
                    [(tr("button.back"), f"menu:requests:{int(raw_page)}")],
                    [(tr("button.main_menu_plain"), "nav:menu")],
                ]
            ),
        )


@router.callback_query(F.data.startswith("view:appt:"))
async def view_appointment(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    _, _, raw_id, raw_page = (callback.data or "").split(":")
    await state.clear()
    item = await service.get_appointment(callback.from_user.id, int(raw_id))
    service_name = await _service_name(service, item)
    business = await service.business_settings()
    token = _new_token()
    await state.update_data(active_cancel_token=token, active_cancel_id=int(item.id))
    text = tr(
        "client.detail.appointment_template",
        number=item.number,
        service_name=service_name,
        date=format_business_date(item.starts_at, business.timezone),
        time=format_business_time(item.starts_at, business.timezone),
        status=status_label(item.status),
        customer_name=item.customer_name,
        contact=item.contact,
    )
    rows: list[list[tuple[str, str]]] = []
    if item.status == "booked" and _as_utc(item.starts_at) > datetime.now(UTC):
        rows.append(
            [
                (
                    tr("client.detail.cancel_appointment"),
                    f"cancel:ask:{token}:{item.id}:{int(raw_page)}",
                )
            ]
        )
    rows.extend(
        [
            [(tr("button.back"), f"menu:appointments:{int(raw_page)}")],
            [(tr("button.main_menu_plain"), "nav:menu")],
        ]
    )
    await callback.answer()
    if isinstance(callback.message, Message):
        await _edit_plain(callback.message, text, reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("cancel:ask:"))
async def ask_cancel_appointment(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer(tr("client.confirm.invalid"), show_alert=True)
        return
    _, _, token, raw_id, raw_page = parts
    data = await state.get_data()
    if data.get("active_cancel_token") != token or str(data.get("active_cancel_id")) != raw_id:
        await callback.answer(
            tr("client.confirm.expired"),
            show_alert=True,
        )
        return
    item = await service.get_appointment(callback.from_user.id, int(raw_id))
    if item.status != "booked" or _as_utc(item.starts_at) <= datetime.now(UTC):
        await state.update_data(active_cancel_token=None, active_cancel_id=None)
        await callback.answer(
            tr("client.confirm.unavailable"),
            show_alert=True,
        )
        return
    await callback.answer()
    if isinstance(callback.message, Message):
        await _edit_plain(
            callback.message,
            tr("client.confirm.cancel_question", number=item.number),
            reply_markup=keyboard(
                [
                    [
                        (
                            tr("client.cancel.confirm_button"),
                            f"cancel:confirm:{token}:{raw_id}:{raw_page}",
                        )
                    ],
                    [(tr("client.cancel.keep_button"), f"view:appt:{raw_id}:{raw_page}")],
                ]
            ),
        )


@router.callback_query(F.data.startswith("cancel:confirm:"))
async def confirm_cancel_appointment(
    callback: CallbackQuery,
    state: FSMContext,
    service: SmartLeadService,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer(tr("client.confirm.invalid"), show_alert=True)
        return
    _, _, token, raw_id, raw_page = parts
    data = await state.get_data()
    if data.get("active_cancel_token") != token or str(data.get("active_cancel_id")) != raw_id:
        await callback.answer(
            tr("client.confirm.expired"),
            show_alert=True,
        )
        return
    await state.update_data(active_cancel_token=None, active_cancel_id=None)
    item = await service.cancel_appointment(callback.from_user.id, int(raw_id))
    if item is None:
        item = await service.get_appointment(callback.from_user.id, int(raw_id))
    await state.clear()
    await _answer_callback_safely(callback, tr("client.cancel.confirmation_button"))
    if isinstance(callback.message, Message):
        await _answer_plain(
            callback.message,
            format_booking_cancelled(item.number),
            reply_markup=keyboard(
                [
                    [(tr("client.button.back_appointments"), f"nav:appointments:{int(raw_page)}")],
                    [(tr("button.main_menu_plain"), "nav:menu:new")],
                ]
            ),
        )
        try:
            await _edit_plain(
                callback.message,
                tr("client.cancel.confirmation_notice"),
                reply_markup=None,
            )
        except (TelegramAPIError, OSError, TimeoutError):
            pass


@router.callback_query(F.data.startswith("f:"))
async def reject_stale_form_button(callback: CallbackQuery) -> None:
    await callback.answer(tr("client.form.stale_button"), show_alert=True)


@router.callback_query()
async def reject_unknown_callback(callback: CallbackQuery) -> None:
    await callback.answer(tr("client.callback.unknown"), show_alert=True)
