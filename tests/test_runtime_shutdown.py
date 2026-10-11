"""The bot drains an in-flight outbox send and closes resources on startup failure."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.methods import GetMe

import smartlead.__main__ as app_main
import smartlead.bot.app as bot_app
from smartlead.config import Settings


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        bot_token="123456:runtime-test-token",
        database_url="postgresql+asyncpg://test@127.0.0.1/smartlead_test",
        outbox_poll_seconds=0.1,
    )


class FakeBot:
    instances = []
    startup_error: BaseException | None = None

    def __init__(self, token: str) -> None:
        self.token = token
        self.session = SimpleNamespace(close=AsyncMock())
        self.get_me = AsyncMock(side_effect=self.startup_error)
        if self.startup_error is None:
            self.get_me.return_value = SimpleNamespace(id=123456, is_bot=True)
        self.set_my_commands = AsyncMock()
        self.instances.append(self)


class FakeDispatcher:
    def __init__(self, polling_started: asyncio.Event, polling_finished: asyncio.Event) -> None:
        self.polling_started = polling_started
        self.polling_finished = polling_finished
        self.storage = SimpleNamespace(close=AsyncMock())
        self.fsm = SimpleNamespace(events_isolation=SimpleNamespace(close=AsyncMock()))

    def resolve_used_update_types(self) -> list[str]:
        return ["message", "callback_query"]

    async def start_polling(self, _bot, **_kwargs) -> None:
        self.polling_started.set()
        await self.polling_finished.wait()


@pytest.mark.asyncio
async def test_shutdown_waits_for_inflight_worker_then_closes_bot_and_dispatcher(
    monkeypatch,
):
    polling_started = asyncio.Event()
    polling_finished = asyncio.Event()
    send_release = asyncio.Event()
    dispatcher = FakeDispatcher(polling_started, polling_finished)
    worker_holder = []

    class InFlightWorker:
        def __init__(self, _sessions, _bot, *, max_attempts: int) -> None:
            assert max_attempts == 8
            self.started = asyncio.Event()
            self.stop_seen = asyncio.Event()
            self.completed = asyncio.Event()
            self.cancelled = False
            worker_holder.append(self)

        async def run(self, stop: asyncio.Event, _poll_seconds: float) -> None:
            self.started.set()
            stop_observer = asyncio.create_task(stop.wait())
            try:
                await stop_observer
                self.stop_seen.set()
                await send_release.wait()
                self.completed.set()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            finally:
                if not stop_observer.done():
                    stop_observer.cancel()
                    await asyncio.gather(stop_observer, return_exceptions=True)

    FakeBot.instances.clear()
    FakeBot.startup_error = None
    monkeypatch.setattr(app_main, "Bot", FakeBot)
    monkeypatch.setattr(app_main, "OutboxWorker", InFlightWorker)
    monkeypatch.setattr(bot_app, "build_dispatcher", lambda _service, _settings: dispatcher)

    run = asyncio.create_task(app_main.run_bot(SimpleNamespace(sessions=object()), _settings()))
    try:
        await asyncio.wait_for(polling_started.wait(), timeout=2)
        worker = worker_holder[0]
        await asyncio.wait_for(worker.started.wait(), timeout=2)

        polling_finished.set()
        await asyncio.wait_for(worker.stop_seen.wait(), timeout=2)
        assert not worker.completed.is_set()
        assert not worker.cancelled
        assert not run.done()
        dispatcher.storage.close.assert_not_awaited()

        send_release.set()
        await asyncio.wait_for(run, timeout=2)

        assert worker.completed.is_set()
        assert not worker.cancelled
        dispatcher.storage.close.assert_awaited_once()
        dispatcher.fsm.events_isolation.close.assert_awaited_once()
        FakeBot.instances[0].session.close.assert_awaited_once()
        command_calls = FakeBot.instances[0].set_my_commands.await_args_list
        assert {call.kwargs["language_code"] for call in command_calls} == {
            None,
            "en",
            "fr",
            "ru",
            "uk",
            "de",
            "ja",
        }
        russian = next(
            call.args[0] for call in command_calls if call.kwargs["language_code"] == "ru"
        )
        assert next(
            command.description for command in russian if command.command == "language"
        ) != ("Choose language")
    finally:
        polling_finished.set()
        send_release.set()
        if not run.done():
            await asyncio.wait_for(run, timeout=2)


@pytest.mark.asyncio
async def test_unauthorized_startup_still_closes_bot_and_dispatcher(monkeypatch):
    dispatcher = FakeDispatcher(asyncio.Event(), asyncio.Event())
    unauthorized = TelegramUnauthorizedError(GetMe(), "invalid token")
    FakeBot.instances.clear()
    FakeBot.startup_error = unauthorized
    monkeypatch.setattr(app_main, "Bot", FakeBot)
    monkeypatch.setattr(bot_app, "build_dispatcher", lambda _service, _settings: dispatcher)

    with pytest.raises(TelegramUnauthorizedError):
        await app_main.run_bot(SimpleNamespace(sessions=object()), _settings())

    FakeBot.instances[0].get_me.assert_awaited_once()
    FakeBot.instances[0].set_my_commands.assert_not_awaited()
    FakeBot.instances[0].session.close.assert_awaited_once()
    dispatcher.storage.close.assert_awaited_once()
    dispatcher.fsm.events_isolation.close.assert_awaited_once()

    FakeBot.startup_error = None
