"""Notification delivery is retried or finalized from durable PostgreSQL rows."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramServerError
from aiogram.methods import SendMessage

from smartlead.db.models import NotificationOutbox
from smartlead.workers.outbox import OutboxWorker

pytestmark = pytest.mark.integration


async def _queued(
    session_factory,
    *,
    available_at: datetime | None = None,
    reply_markup: dict[str, object] | None = None,
) -> int:
    async with session_factory.begin() as session:
        row = NotificationOutbox(
            telegram_id=123456,
            text="A SmartLead test notification",
            reply_markup=reply_markup,
            dedup_key=f"outbox-test:{datetime.now(UTC).timestamp()}",
            available_at=available_at or datetime.now(UTC) - timedelta(seconds=1),
        )
        session.add(row)
        await session.flush()
        return row.id


async def _row(session_factory, row_id: int) -> NotificationOutbox:
    async with session_factory() as session:
        return await session.get(NotificationOutbox, row_id)


def _bot(side_effect=None):
    return SimpleNamespace(send_message=AsyncMock(side_effect=side_effect))


def _telegram_method() -> SendMessage:
    return SendMessage(chat_id=123456, text="A SmartLead test notification")


@pytest.mark.asyncio
async def test_successful_delivery_marks_row_sent_and_records_attempt(clean_pg):
    row_id = await _queued(clean_pg)
    bot = _bot()

    assert await OutboxWorker(clean_pg, bot).deliver_one() is True

    row = await _row(clean_pg, row_id)
    assert row.status == "sent"
    assert row.attempts == 1
    assert row.sent_at is not None
    assert row.last_error is None
    bot.send_message.assert_awaited_once_with(
        chat_id=123456, text="A SmartLead test notification", parse_mode=None
    )


@pytest.mark.asyncio
async def test_outbox_restores_saved_inline_keyboard_for_new_request_alerts(clean_pg):
    markup = {
        "inline_keyboard": [
            [{"text": "View Request", "callback_data": "admin:req:item:42:0"}],
            [
                {
                    "text": "Mark Completed",
                    "callback_data": "admin:req:set:42:completed:0",
                }
            ],
        ]
    }
    row_id = await _queued(clean_pg, reply_markup=markup)
    bot = _bot()

    assert await OutboxWorker(clean_pg, bot).deliver_one() is True

    assert (await _row(clean_pg, row_id)).reply_markup == markup
    sent = bot.send_message.await_args.kwargs["reply_markup"]
    assert sent.inline_keyboard[0][0].callback_data == "admin:req:item:42:0"
    assert sent.inline_keyboard[1][0].callback_data == "admin:req:set:42:completed:0"


@pytest.mark.asyncio
async def test_outbox_rejects_invalid_saved_markup_without_sending(clean_pg):
    row_id = await _queued(clean_pg, reply_markup={"inline_keyboard": "invalid"})
    bot = _bot()

    assert await OutboxWorker(clean_pg, bot).deliver_one() is True

    row = await _row(clean_pg, row_id)
    assert row.status == "failed"
    assert row.last_error == "InvalidReplyMarkup"
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_forbidden_delivery_is_terminal_and_retry_after_is_scheduled(clean_pg):
    terminal_id = await _queued(clean_pg)
    forbidden_bot = _bot(
        TelegramForbiddenError(_telegram_method(), "bot was blocked by the customer")
    )
    assert await OutboxWorker(clean_pg, forbidden_bot).deliver_one() is True
    terminal = await _row(clean_pg, terminal_id)
    assert terminal.status == "failed"
    assert terminal.attempts == 1
    assert terminal.last_error == "TelegramForbiddenError"

    retry_id = await _queued(clean_pg)
    retry_bot = _bot(TelegramRetryAfter(_telegram_method(), "rate limited", retry_after=7))
    before = datetime.now(UTC)
    assert await OutboxWorker(clean_pg, retry_bot).deliver_one() is True
    retry = await _row(clean_pg, retry_id)
    assert retry.status == "pending"
    assert retry.attempts == 1
    assert retry.last_error == "TelegramRetryAfter"
    assert retry.available_at >= before + timedelta(seconds=6)


@pytest.mark.asyncio
async def test_transient_failure_retries_then_fails_at_attempt_limit(clean_pg):
    row_id = await _queued(clean_pg)
    bot = _bot(TelegramServerError(_telegram_method(), "temporary Telegram failure"))

    assert await OutboxWorker(clean_pg, bot, max_attempts=1).deliver_one() is True

    row = await _row(clean_pg, row_id)
    assert row.status == "failed"
    assert row.attempts == 1
    assert row.last_error == "TelegramServerError"


@pytest.mark.asyncio
async def test_worker_does_not_send_future_rows_and_skip_locked_prevents_double_claim(clean_pg):
    future_id = await _queued(clean_pg, available_at=datetime.now(UTC) + timedelta(minutes=5))
    idle_bot = _bot()
    assert await OutboxWorker(clean_pg, idle_bot).deliver_one() is False
    idle_bot.send_message.assert_not_awaited()
    assert (await _row(clean_pg, future_id)).attempts == 0

    row_id = await _queued(clean_pg)
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold_send(*_args, **_kwargs):
        started.set()
        await release.wait()

    first_bot = _bot(hold_send)
    second_bot = _bot()
    first_worker = OutboxWorker(clean_pg, first_bot)
    second_worker = OutboxWorker(clean_pg, second_bot)
    first_claim = asyncio.create_task(first_worker.deliver_one())
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert await second_worker.deliver_one() is False
        second_bot.send_message.assert_not_awaited()
    finally:
        release.set()
    assert await first_claim is True
    assert (await _row(clean_pg, row_id)).status == "sent"
