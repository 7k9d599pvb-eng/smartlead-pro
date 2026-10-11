"""At-least-once notification delivery with durable retries and row locking."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import InlineKeyboardMarkup
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smartlead.db.models import NotificationOutbox

logger = logging.getLogger(__name__)


class OutboxWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        bot: Bot,
        *,
        max_attempts: int = 8,
    ) -> None:
        self.sessions = session_factory
        self.bot = bot
        self.max_attempts = max_attempts

    async def deliver_one(self) -> bool:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(NotificationOutbox)
                .where(
                    NotificationOutbox.status == "pending",
                    NotificationOutbox.available_at <= datetime.now(UTC),
                )
                .order_by(NotificationOutbox.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return False
            row.attempts += 1
            send_args = {
                "chat_id": row.telegram_id,
                "text": row.text,
                "parse_mode": None,
            }
            if row.reply_markup is not None:
                try:
                    send_args["reply_markup"] = InlineKeyboardMarkup.model_validate(
                        row.reply_markup
                    )
                except (PydanticValidationError, TypeError, ValueError):
                    row.status = "failed"
                    row.last_error = "InvalidReplyMarkup"
                    return True
            try:
                await asyncio.wait_for(
                    self.bot.send_message(**send_args),
                    timeout=15,
                )
            except TelegramUnauthorizedError:
                # Invalid bot credentials affect the entire process; keep this row for recovery.
                raise
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                row.status = "failed"
                row.last_error = type(exc).__name__
            except TelegramRetryAfter as exc:
                self._retry(row, type(exc).__name__, max(1, exc.retry_after))
            except (TelegramAPIError, TimeoutError, OSError) as exc:
                self._retry(row, type(exc).__name__, min(3600, 2 ** min(row.attempts, 12)))
            else:
                row.status = "sent"
                row.sent_at = datetime.now(UTC)
                row.last_error = None
            return True

    def _retry(self, row: NotificationOutbox, reason: str, delay_seconds: int) -> None:
        row.last_error = reason
        if row.attempts >= self.max_attempts:
            row.status = "failed"
        else:
            row.available_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)

    async def run(self, stop: asyncio.Event, poll_seconds: float = 2) -> None:
        while not stop.is_set():
            try:
                delivered = await self.deliver_one()
            except TelegramUnauthorizedError:
                raise
            except Exception as exc:
                # No message bodies, credentials or database parameters in worker logs.
                logger.error("Outbox processing failed (%s)", type(exc).__name__)
                delivered = False
            if delivered:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
