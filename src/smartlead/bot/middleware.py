"""Private-chat boundary, dependency injection and safe error responses."""

import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from smartlead.config import Settings
from smartlead.i18n import current_language, tr, use_language
from smartlead.services.core import SmartLeadService
from smartlead.services.errors import BusinessError

logger = logging.getLogger(__name__)


class UpdateDedupMiddleware(BaseMiddleware):
    """Ignore repeated update IDs in this process; SQL submission keys protect saved writes."""

    def __init__(self, capacity: int = 10_000) -> None:
        self.capacity = capacity
        self.seen: OrderedDict[int, None] = OrderedDict()

    async def __call__(self, handler, event: Update, data):
        if event.update_id in self.seen:
            return None
        self.seen[event.update_id] = None
        if len(self.seen) > self.capacity:
            self.seen.popitem(last=False)
        try:
            return await handler(event, data)
        except BaseException:
            self.seen.pop(event.update_id, None)
            raise


class AdminOnly(BaseFilter):
    async def __call__(self, event: CallbackQuery | Message, settings: Settings) -> bool:
        return event.from_user is not None and event.from_user.id in settings.allowed_admin_ids


class CurrentForm(BaseFilter):
    """Reject a button from a previous screen, even at the same FSM state."""

    async def __call__(self, callback: CallbackQuery, state: FSMContext) -> bool:
        parts = (callback.data or "").split(":")
        data = await state.get_data()
        return len(parts) == 4 and parts[0] == "f" and parts[1] == data.get("form_token")


class BotMiddleware(BaseMiddleware):
    def __init__(self, service: SmartLeadService, settings: Settings) -> None:
        self.service = service
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return None
        try:
            language = await self.service.get_user_language(user.id)
        except Exception as exc:
            logger.error("Language preference lookup failed (%s)", type(exc).__name__)
            await _safe_response(event, tr("error_retry_menu", language="en"))
            return None
        with use_language(language):
            return await self._handle(handler, event, data)

    async def _handle(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message = event.message if isinstance(event, CallbackQuery) else event
        if not isinstance(message, Message):
            if isinstance(event, CallbackQuery):
                await _safe_response(event, tr("error_message_unavailable"))
            return None
        if message.chat.type != "private":
            is_admin_button = isinstance(event, CallbackQuery) and (event.data or "").startswith(
                "admin:"
            )
            is_notification_group = message.chat.id == self.settings.admin_notification_chat_id
            if not (is_admin_button and is_notification_group):
                if isinstance(event, CallbackQuery):
                    await _safe_response(event, tr("error_private_chat"))
                return None
            if event.from_user.id not in self.settings.allowed_admin_ids:
                await _safe_response(event, tr("error_access_denied"))
                return None
        user = event.from_user
        if user is None:
            return None
        data["service"] = self.service
        data["settings"] = self.settings
        try:
            data["profile"] = await self.service.ensure_user(user.id, user.full_name, user.username)
            data["language"] = current_language()
            return await handler(event, data)
        except BusinessError as exc:
            await _safe_response(event, str(exc))
        except Exception as exc:
            if (
                isinstance(exc, TelegramBadRequest)
                and "message is not modified" in str(exc).casefold()
            ):
                # Telegram treats a no-op edit as an error. The current UI is already correct.
                logger.debug("Telegram skipped an unchanged message edit.")
                return None
            if isinstance(event, CallbackQuery) and isinstance(exc, (IndexError, ValueError)):
                if (event.data or "").startswith("admin:"):
                    text = tr("error_admin_button_expired")
                else:
                    text = tr("error_button_expired")
                await _safe_response(event, text)
                return None
            # Do not log event payloads, contact details, connection strings or tokens.
            logger.error("Telegram handler failed (%s)", type(exc).__name__)
            if isinstance(event, CallbackQuery):
                await _safe_response(event, tr("error_retry"))
            else:
                await _safe_response(event, tr("error_retry_menu"))
        return None


async def _safe_response(event: TelegramObject, text: str) -> None:
    """An expired callback or blocked chat must not trigger a second uncaught API error."""
    try:
        if isinstance(event, CallbackQuery):
            await event.answer(text[:180], show_alert=True)
        elif isinstance(event, Message):
            await event.answer(text, parse_mode=None)
    except (TelegramAPIError, OSError, TimeoutError) as exc:
        logger.warning("Telegram error response could not be delivered (%s)", type(exc).__name__)
