"""Allowlisted administration inside Telegram, including private edit forms."""

from __future__ import annotations

from datetime import UTC, datetime
from secrets import token_hex

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from smartlead.bot.admin_states import ServiceForm, SupportForm
from smartlead.bot.keyboards import admin_menu, keyboard, pagination
from smartlead.bot.middleware import AdminOnly
from smartlead.config import Settings
from smartlead.i18n import tr
from smartlead.presentation import (
    format_business_date,
    format_business_datetime,
    format_business_time,
    status_label,
)
from smartlead.services.core import SmartLeadService
from smartlead.services.errors import ValidationError

router = Router(name="admin")
PAGE_SIZE = 5


def _panel_title() -> str:
    return tr("admin.panel_title")


@router.message(Command("admin"))
async def admin_start(message: Message, state: FSMContext, settings: Settings) -> None:
    if not message.from_user or message.from_user.id not in settings.allowed_admin_ids:
        await message.answer(tr("error_access_denied"))
        return
    await state.clear()
    await message.answer(_panel_title(), reply_markup=admin_menu())


@router.callback_query(F.data.startswith("admin:form:"), AdminOnly())
async def admin_form_action(
    callback: CallbackQuery, state: FSMContext, service: SmartLeadService
) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    current = await state.get_state()
    if len(parts) != 4 or parts[2] != data.get("admin_form_token") or current is None:
        await callback.answer(tr("admin.form.expired"), show_alert=True)
        return
    action = parts[3]
    if action == "cancel":
        await state.clear()
        await callback.answer(tr("admin.form.cancelled"))
        await _edit(callback, _panel_title(), admin_menu())
        return
    if action == "back":
        if current == SupportForm.contact.state or current == ServiceForm.name.state:
            await state.clear()
            await _show_services(callback, service, int(data.get("service_page", 0)))
            return
        previous = {
            ServiceForm.description.state: ServiceForm.name,
            ServiceForm.duration.state: ServiceForm.description,
            ServiceForm.review.state: ServiceForm.duration,
        }.get(current)
        if previous is not None:
            await state.set_state(previous)
            await callback.answer()
            await _show_service_form(callback, state)
            return
    if action == "keep" and data.get("service_id") is not None:
        if current == ServiceForm.name.state:
            await state.set_state(ServiceForm.description)
        elif current == ServiceForm.description.state:
            await state.set_state(ServiceForm.duration)
        elif current == ServiceForm.duration.state:
            await state.set_state(ServiceForm.review)
        else:
            await callback.answer(tr("admin.form.button_unavailable"), show_alert=True)
            return
        await callback.answer()
        await _show_service_form(callback, state)
        return
    if action == "save" and current == ServiceForm.review.state:
        fields = dict(
            name=data["service_name"],
            description=data["service_description"],
            duration_minutes=data["service_duration"],
        )
        if data.get("service_id") is None:
            item = await service.create_service(callback.from_user.id, **fields)
        else:
            item = await service.update_service(callback.from_user.id, data["service_id"], **fields)
        page = int(data.get("service_page", 0))
        await state.clear()
        await callback.answer(tr("admin.service.saved"))
        await _show_service(callback, service, item.id, page, already_answered=True)
        return
    await callback.answer(tr("admin.form.button_unavailable"), show_alert=True)


@router.message(StateFilter(ServiceForm, SupportForm), AdminOnly(), ~F.text.startswith("/"))
async def admin_form_input(message: Message, state: FSMContext, service: SmartLeadService) -> None:
    try:
        await _collect_admin_input(message, state, service)
    except ValidationError as exc:
        await _admin_form_feedback(message, state, str(exc))


async def _admin_form_feedback(message: Message, state: FSMContext, text: str) -> None:
    data = await state.get_data()
    token = data["admin_form_token"]
    rows = [
        [
            (tr("button.back"), f"admin:form:{token}:back"),
            (tr("button.cancel"), f"admin:form:{token}:cancel"),
        ]
    ]
    if await state.get_state() == ServiceForm.review.state:
        rows.insert(0, [(tr("admin.form.save_button"), f"admin:form:{token}:save")])
    await message.answer(text, reply_markup=keyboard(rows), parse_mode=None)


async def _collect_admin_input(
    message: Message, state: FSMContext, service: SmartLeadService
) -> None:
    current = await state.get_state()
    if not message.text:
        await _admin_form_feedback(message, state, tr("admin.form.prompt_text"))
        return
    value = message.text.strip()
    if current == SupportForm.contact.state:
        await service.update_support_contact(message.from_user.id, None if value == "-" else value)
        await state.clear()
        await message.answer(tr("admin.support.saved"), reply_markup=admin_menu())
        return
    if current == ServiceForm.name.state:
        _validate_text(value, tr("admin.field.service_name"), 2, 100)
        await state.update_data(service_name=value)
        await state.set_state(ServiceForm.description)
    elif current == ServiceForm.description.state:
        value = "" if value == "-" else value
        _validate_text(value, tr("admin.field.description"), 0, 2000, multiline=True)
        await state.update_data(service_description=value)
        await state.set_state(ServiceForm.duration)
    elif current == ServiceForm.duration.state:
        if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 480:
            raise ValidationError("admin.service.duration_error")
        await state.update_data(service_duration=int(value))
        await state.set_state(ServiceForm.review)
    else:
        await _admin_form_feedback(message, state, tr("admin.form.prompt_save"))
        return
    await _show_service_form(message, state)


@router.callback_query(F.data.startswith("admin:"), AdminOnly())
async def admin_action(
    callback: CallbackQuery, service: SmartLeadService, state: FSMContext
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 2:
        await callback.answer(tr("admin.action.unavailable"), show_alert=True)
        return
    action = parts[1]
    admin_id = callback.from_user.id
    await state.clear()
    if action == "stats" and len(parts) == 2:
        stats = await service.statistics(admin_id)
        text = "\n\n".join(
            (
                tr("admin.statistics.title"),
                "\n".join(
                    (
                        tr("admin.statistics.requests_total", count=stats["requests_total"]),
                        tr("admin.statistics.requests_new", count=stats["requests_new"]),
                        tr(
                            "admin.statistics.requests_in_progress",
                            count=stats["requests_in_progress"],
                        ),
                        tr(
                            "admin.statistics.requests_completed", count=stats["requests_completed"]
                        ),
                        tr(
                            "admin.statistics.requests_cancelled", count=stats["requests_cancelled"]
                        ),
                    )
                ),
                "\n".join(
                    (
                        tr(
                            "admin.statistics.appointments_total", count=stats["appointments_total"]
                        ),
                        tr(
                            "admin.statistics.appointments_upcoming",
                            count=stats["appointments_upcoming"],
                        ),
                        tr(
                            "admin.statistics.appointments_completed",
                            count=stats["appointments_completed"],
                        ),
                        tr(
                            "admin.statistics.appointments_cancelled",
                            count=stats["appointments_cancelled"],
                        ),
                    )
                ),
            )
        )
        await callback.answer()
        await _edit(callback, text, [[(tr("button.back"), "admin:home")]])
        return
    if action == "home" and len(parts) == 2:
        await callback.answer()
        await _edit(callback, _panel_title(), admin_menu())
        return
    if action in {"requests", "new_requests"} and len(parts) == 3:
        await _show_admin_list(
            callback,
            service,
            kind="request",
            page=_page(parts[2]),
            new_only=action == "new_requests",
        )
        return
    if action == "appointments" and len(parts) == 3:
        await _show_admin_list(callback, service, kind="appointment", page=_page(parts[2]))
        return
    if action == "services" and len(parts) == 3:
        await _show_services(callback, service, _page(parts[2]))
        return
    if action == "support" and len(parts) == 2:
        if not await _private_form(callback):
            return
        business = await service.business_settings()
        token = token_hex(6)
        await state.set_state(SupportForm.contact)
        await state.update_data(admin_form_token=token)
        await callback.answer()
        await _edit(
            callback,
            tr(
                "admin.support.prompt_message",
                contact=business.support_contact or tr("admin.support.not_configured"),
            ),
            [
                [
                    (tr("button.back"), f"admin:form:{token}:back"),
                    (tr("button.cancel"), f"admin:form:{token}:cancel"),
                ]
            ],
        )
        return
    if action == "svc":
        if len(parts) == 4 and parts[2] == "add":
            if not await _private_form(callback):
                return
            await state.set_state(ServiceForm.name)
            await state.update_data(admin_form_token=token_hex(6), service_page=_page(parts[3]))
            await callback.answer()
            await _show_service_form(callback, state)
            return
        if len(parts) == 5 and parts[2] in {"item", "edit", "toggle"}:
            service_id, page = int(parts[3]), _page(parts[4])
            if parts[2] == "item":
                await _show_service(callback, service, service_id, page)
                return
            item = await service.get_service(service_id)
            if parts[2] == "toggle":
                await service.set_service_active(admin_id, service_id, not item.is_active)
                await callback.answer(tr("admin.service.availability_updated"))
                await _show_service(callback, service, service_id, page, already_answered=True)
                return
            if not await _private_form(callback):
                return
            await state.set_state(ServiceForm.name)
            await state.update_data(
                admin_form_token=token_hex(6),
                service_id=service_id,
                service_page=page,
                service_name=item.name,
                service_description=item.description or "",
                service_duration=item.duration_minutes,
            )
            await callback.answer()
            await _show_service_form(callback, state)
            return
    if action == "req" and len(parts) in {4, 5} and parts[2] == "item":
        await _show_request(callback, service, int(parts[3]), parts[4] if len(parts) == 5 else "0")
        return
    if action == "req" and len(parts) == 6 and parts[2] == "set":
        request_id, new_status, origin = int(parts[3]), parts[4], parts[5]
        _page(origin)
        await service.change_request_status(admin_id, request_id, new_status)
        await callback.answer(tr("admin.request.status_updated"))
        await _show_request(callback, service, request_id, origin, already_answered=True)
        return
    if action == "appt" and len(parts) in {4, 5} and parts[2] == "item":
        await _show_appointment(
            callback, service, int(parts[3]), _page(parts[4]) if len(parts) == 5 else 0
        )
        return
    if action == "appt" and len(parts) == 6 and parts[2] == "set":
        appointment_id, new_status, page = int(parts[3]), parts[4], _page(parts[5])
        await service.change_appointment_status(admin_id, appointment_id, new_status)
        await callback.answer(tr("admin.appointment.status_updated"))
        await _show_appointment(callback, service, appointment_id, page, already_answered=True)
        return
    if action == "appt" and len(parts) == 5 and parts[2] == "cancel-ask":
        appointment_id, page = int(parts[3]), _page(parts[4])
        item = await service.get_appointment(admin_id, appointment_id, admin=True)
        await callback.answer()
        await _edit(
            callback,
            tr("admin.appointment.cancel_prompt", number=item.number),
            [
                [
                    (
                        tr("admin.appointment.cancel_yes"),
                        f"admin:appt:cancel-confirm:{appointment_id}:{page}",
                    )
                ],
                [(tr("admin.appointment.cancel_keep"), f"admin:appt:item:{appointment_id}:{page}")],
            ],
        )
        return
    if action == "appt" and len(parts) == 5 and parts[2] == "cancel-confirm":
        appointment_id, page = int(parts[3]), _page(parts[4])
        await service.change_appointment_status(admin_id, appointment_id, "cancelled")
        await callback.answer(tr("admin.appointment.cancelled"))
        await _show_appointment(callback, service, appointment_id, page, already_answered=True)
        return
    await callback.answer(tr("admin.action.unavailable"), show_alert=True)


@router.callback_query(F.data.startswith("admin:"))
async def deny_admin_callback(callback: CallbackQuery) -> None:
    await callback.answer(tr("error_access_denied"), show_alert=True)


def _page(value: str) -> int:
    page = int(value[1:] if value.startswith("n") else value)
    if page < 0 or page > 1_000_000:
        raise ValueError("invalid page")
    return page


def _validate_text(value: str, label: str, minimum: int, maximum: int, *, multiline=False) -> None:
    if not minimum <= len(value) <= maximum:
        raise ValidationError("error_text_length", field=label, minimum=minimum, maximum=maximum)
    if any((ord(c) < 32 and not (multiline and c in "\n\t")) or ord(c) == 127 for c in value):
        raise ValidationError("error_control_characters", field=label)


async def _private_form(callback: CallbackQuery) -> bool:
    if not isinstance(callback.message, Message) or callback.message.chat.type != "private":
        await callback.answer(tr("admin.private_form_only"), show_alert=True)
        return False
    return True


async def _edit(callback: CallbackQuery, text: str, rows) -> None:
    if isinstance(callback.message, Message):
        markup = rows if hasattr(rows, "inline_keyboard") else keyboard(rows)
        await callback.message.edit_text(text[:4000], reply_markup=markup, parse_mode=None)


async def _show_service_form(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    current = await state.get_state()
    token = token_hex(6)
    await state.update_data(admin_form_token=token)
    editing = data.get("service_id") is not None
    prompts = {
        ServiceForm.name.state: tr("admin.service.prompt_name"),
        ServiceForm.description.state: tr("admin.service.prompt_description"),
        ServiceForm.duration.state: tr("admin.service.prompt_duration"),
    }
    rows = []
    if current == ServiceForm.review.state:
        text = tr(
            "admin.service.review",
            name=data["service_name"],
            duration=data["service_duration"],
            description=data["service_description"] or tr("admin.service.no_description"),
        )
        rows.append([(tr("admin.form.save_button"), f"admin:form:{token}:save")])
    else:
        title = tr("admin.service.form_title_edit" if editing else "admin.service.form_title_new")
        text = f"{title}\n\n{prompts[current]}"
        field = {
            ServiceForm.name.state: "service_name",
            ServiceForm.description.state: "service_description",
            ServiceForm.duration.state: "service_duration",
        }[current]
        if field in data:
            text += "\n\n" + tr(
                "admin.service.current",
                value=data[field] or tr("admin.service.no_description"),
            )
        if editing:
            rows.append([(tr("admin.button.keep_current"), f"admin:form:{token}:keep")])
    rows.append(
        [
            (tr("button.back"), f"admin:form:{token}:back"),
            (tr("button.cancel"), f"admin:form:{token}:cancel"),
        ]
    )
    if isinstance(event, CallbackQuery):
        await _edit(event, text, rows)
    else:
        await event.answer(text, reply_markup=keyboard(rows), parse_mode=None)


async def _show_services(callback: CallbackQuery, service: SmartLeadService, page: int) -> None:
    catalog = await service.list_services_admin(callback.from_user.id)
    items = catalog[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    lines, rows = [tr("admin.services.title")], []
    for item in items:
        lines.append(
            tr(
                "admin.service.item",
                name=item.name,
                duration=item.duration_minutes,
                status=tr("admin.status.active" if item.is_active else "admin.status.inactive"),
            )
        )
        rows.append([(item.name[:50], f"admin:svc:item:{item.id}:{page}")])
    if not items:
        lines.append(tr("admin.services.empty"))
    nav = pagination("admin:services", page, (page + 1) * PAGE_SIZE < len(catalog))
    if nav:
        rows.append(nav)
    rows.extend(
        [
            [(tr("admin.button.add_service"), f"admin:svc:add:{page}")],
            [(tr("admin.button.support_contact"), "admin:support")],
            [(tr("button.admin_menu"), "admin:home")],
        ]
    )
    await callback.answer()
    await _edit(callback, "".join(lines), rows)


async def _show_service(callback, service, service_id, page, *, already_answered=False) -> None:
    item = await service.get_service(service_id)
    text = tr(
        "admin.service.detail_template",
        name=item.name,
        duration=item.duration_minutes,
        status=tr("admin.status.active" if item.is_active else "admin.status.inactive"),
        description=item.description or tr("admin.service.no_description"),
    )
    rows = [
        [(tr("admin.button.edit_service"), f"admin:svc:edit:{item.id}:{page}")],
        [
            (
                tr("admin.button.deactivate" if item.is_active else "admin.button.activate"),
                f"admin:svc:toggle:{item.id}:{page}",
            )
        ],
        [(tr("button.back"), f"admin:services:{page}")],
        [(tr("button.admin_menu"), "admin:home")],
    ]
    if not already_answered:
        await callback.answer()
    await _edit(callback, text, rows)


async def _show_admin_list(callback, service, *, kind, page, new_only=False) -> None:
    offset = page * PAGE_SIZE
    if kind == "request":
        items = await service.list_requests(
            callback.from_user.id,
            offset=offset,
            limit=PAGE_SIZE + 1,
            admin=True,
            status="new" if new_only else None,
        )
        title = tr("admin.list.title_new_requests" if new_only else "admin.list.title_all_requests")
        short = "req"
    else:
        items = await service.list_appointments(
            callback.from_user.id, offset=offset, limit=PAGE_SIZE + 1, admin=True
        )
        title, short = tr("admin.list.title_appointments"), "appt"
    has_next = len(items) > PAGE_SIZE
    items = items[:PAGE_SIZE]
    lines, rows = [title + "\n"], []
    business = await service.business_settings()
    origin = f"n{page}" if new_only else str(page)
    for item in items:
        when = item.created_at if kind == "request" else item.starts_at
        lines.append(
            tr(
                "admin.list.item",
                number=item.number,
                service_name=item.service_name,
                date_time=format_business_datetime(when, business.timezone),
                status=status_label(item.status),
            )
        )
        rows.append(
            [
                (
                    tr("button.view_number", number=item.number),
                    f"admin:{short}:item:{item.id}:{origin}",
                )
            ]
        )
    prefix = "new_requests" if new_only else "requests" if kind == "request" else "appointments"
    nav = pagination(f"admin:{prefix}", page, has_next)
    if nav:
        rows.append(nav)
    rows.append([(tr("button.admin_menu"), "admin:home")])
    if not items:
        lines.append(tr("admin.list.no_new_requests" if new_only else "admin.list.empty"))
    await callback.answer()
    await _edit(callback, "".join(lines), rows)


async def _show_request(callback, service, request_id, origin, *, already_answered=False) -> None:
    page = _page(origin)
    item = await service.get_request(callback.from_user.id, request_id, admin=True)
    business = await service.business_settings()
    text = tr(
        "admin.request.detail_template",
        number=item.number,
        service_name=item.service_name,
        status=status_label(item.status),
        created_at=format_business_datetime(item.created_at, business.timezone),
        customer_name=item.customer_name,
        contact=item.contact,
        details=item.details,
    )
    rows = []
    if item.status == "new":
        rows.append(
            [
                (
                    tr("admin.request.start_processing"),
                    f"admin:req:set:{item.id}:in_progress:{origin}",
                )
            ]
        )
    if item.status in {"new", "in_progress"}:
        rows.extend(
            [
                [
                    (
                        tr("admin.request.mark_completed"),
                        f"admin:req:set:{item.id}:completed:{origin}",
                    )
                ],
                [(tr("admin.request.cancel"), f"admin:req:set:{item.id}:cancelled:{origin}")],
            ]
        )
    prefix = "new_requests" if origin.startswith("n") else "requests"
    rows.extend(
        [
            [(tr("admin.request.back"), f"admin:{prefix}:{page}")],
            [(tr("button.admin_menu"), "admin:home")],
        ]
    )
    if not already_answered:
        await callback.answer()
    await _edit(callback, text, rows)


async def _show_appointment(
    callback, service, appointment_id, page, *, already_answered=False
) -> None:
    item = await service.get_appointment(callback.from_user.id, appointment_id, admin=True)
    business = await service.business_settings()
    text = tr(
        "admin.appointment.detail_template",
        number=item.number,
        service_name=item.service_name,
        date=format_business_date(item.starts_at, business.timezone),
        time=format_business_time(item.starts_at, business.timezone),
        status=status_label(item.status),
        customer_name=item.customer_name,
        contact=item.contact,
    )
    rows = []
    if item.status == "booked":
        if item.ends_at <= datetime.now(UTC):
            rows.append(
                [
                    (
                        tr("admin.appointment.mark_completed"),
                        f"admin:appt:set:{item.id}:completed:{page}",
                    )
                ]
            )
        rows.append([(tr("admin.appointment.cancel"), f"admin:appt:cancel-ask:{item.id}:{page}")])
    rows.extend(
        [
            [(tr("admin.appointment.back"), f"admin:appointments:{page}")],
            [(tr("button.admin_menu"), "admin:home")],
        ]
    )
    if not already_answered:
        await callback.answer()
    await _edit(callback, text, rows)
