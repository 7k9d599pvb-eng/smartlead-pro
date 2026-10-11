"""Compact, bounded inline keyboards and callback payloads."""

from collections.abc import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from smartlead.i18n import tr

Button = tuple[str, str]


def keyboard(rows: Iterable[Iterable[Button]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def main_menu(support_url: str | None = None) -> InlineKeyboardMarkup:
    # Keep the optional argument for callers from the v1.0 API. Support details
    # belong on their own screen so the main menu has a stable callback flow.
    del support_url
    return keyboard(
        [
            [(tr("menu.new_request"), "menu:new")],
            [(tr("menu.book_service"), "menu:book")],
            [(tr("menu.my_requests"), "menu:requests:0")],
            [(tr("menu.my_appointments"), "menu:appointments:0")],
            [(tr("menu.contact_support"), "menu:support")],
            [(tr("menu.language"), "menu:language")],
        ]
    )


def support_menu(support_url: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if support_url:
        rows.append([InlineKeyboardButton(text=tr("menu.message_support"), url=support_url)])
    rows.append([InlineKeyboardButton(text=tr("button.main_menu_plain"), callback_data="nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_menu() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [(tr("admin.menu.new_requests"), "admin:new_requests:0")],
            [(tr("admin.menu.all_requests"), "admin:requests:0")],
            [(tr("admin.menu.appointments"), "admin:appointments:0")],
            [(tr("admin.menu.services"), "admin:services:0")],
            [(tr("admin.menu.statistics"), "admin:stats")],
            [(tr("button.main_menu"), "nav:menu")],
        ]
    )


def form_callback(token: str, action: str, value: object = "-") -> str:
    return f"f:{token}:{action}:{value}"


def form_keyboard(
    token: str, rows: Iterable[Iterable[Button]] = (), *, back: bool = True
) -> InlineKeyboardMarkup:
    result = [list(row) for row in rows]
    navigation = []
    if back:
        navigation.append((tr("button.back"), form_callback(token, "back")))
    navigation.append((tr("button.cancel"), form_callback(token, "cancel")))
    result.append(navigation)
    return keyboard(result)


def pagination(prefix: str, page: int, has_next: bool) -> list[Button]:
    buttons = []
    if page:
        buttons.append((tr("button.previous"), f"{prefix}:{page - 1}"))
    if has_next:
        buttons.append((tr("button.next"), f"{prefix}:{page + 1}"))
    return buttons
