"""CLI: migrations are explicit; startup never silently changes the schema."""

import argparse
import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import BotCommand
from aiogram.utils.token import TokenValidationError
from pydantic import ValidationError
from sqlalchemy import text

from smartlead.config import Settings
from smartlead.db.seed import seed_database
from smartlead.db.session import create_engine, create_session_factory
from smartlead.i18n import SUPPORTED_LANGUAGES, tr
from smartlead.logging import configure_logging
from smartlead.services.core import SmartLeadService
from smartlead.workers.outbox import OutboxWorker

logger = logging.getLogger(__name__)


async def check_database(engine) -> None:
    async with engine.connect() as connection:
        version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        constraint = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_constraint WHERE conname = 'appointments_no_overlap' "
                "AND conrelid = 'appointments'::regclass AND contype = 'x'"
            )
        )
    if version != "0003" or constraint != 1:
        raise RuntimeError("Apply migrations with alembic upgrade head before starting the bot.")


async def run_bot(service: SmartLeadService, settings: Settings) -> None:
    from smartlead.bot.app import build_dispatcher

    bot = Bot(settings.bot_token.get_secret_value())
    dispatcher = build_dispatcher(service, settings)
    stop = asyncio.Event()
    worker_task = None
    polling_task = None
    try:
        await bot.get_me()  # Fail early for invalid credentials; do not drop pending updates.
        for language in (None, *SUPPORTED_LANGUAGES):
            await bot.set_my_commands(
                [
                    BotCommand(
                        command=command,
                        description=tr(f"command_{command}", language=language or "en"),
                    )
                    for command in ("start", "menu", "language", "cancel", "help", "id", "admin")
                ],
                language_code=language,
            )
        worker = OutboxWorker(service.sessions, bot, max_attempts=settings.outbox_max_attempts)
        worker_task = asyncio.create_task(worker.run(stop, settings.outbox_poll_seconds))
        polling_task = asyncio.create_task(
            dispatcher.start_polling(
                bot,
                close_bot_session=False,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
        )
        finished, _ = await asyncio.wait(
            {worker_task, polling_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in finished:
            task.result()
    finally:
        stop.set()
        tasks = [task for task in (polling_task, worker_task) if task is not None]
        if polling_task is not None and not polling_task.done():
            polling_task.cancel()
        if worker_task is not None and not worker_task.done():
            # Finish and commit an in-flight send within Compose's shutdown grace period.
            # A Telegram send is bounded to 15 seconds in OutboxWorker.
            _, pending = await asyncio.wait({worker_task}, timeout=20)
            for task in pending:
                task.cancel()
        # Collect faulted tasks too, so one failure cannot prevent resource cleanup.
        await asyncio.gather(*tasks, return_exceptions=True)
        await dispatcher.storage.close()
        await dispatcher.fsm.events_isolation.close()
        await bot.session.close()


async def run(args: argparse.Namespace, settings: Settings) -> None:
    engine = create_engine(settings.database_url.get_secret_value())
    sessions = create_session_factory(engine)
    try:
        await check_database(engine)
        if args.command == "seed":
            await seed_database(sessions, settings, sync_settings=args.sync_settings)
            logger.info("Business settings and sample services initialized.")
        elif args.command == "check":
            logger.info("PostgreSQL connection, schema revision and booking constraint are valid.")
        else:
            await seed_database(sessions, settings)
            await run_bot(SmartLeadService(sessions, settings), settings)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartLead Telegram Bot")
    subparsers = parser.add_subparsers(dest="command")
    seed_parser = subparsers.add_parser(
        "seed", help="Initialize sample services and business settings"
    )
    seed_parser.add_argument(
        "--sync-settings",
        action="store_true",
        help="Update saved business settings from current environment",
    )
    subparsers.add_parser("check", help="Check PostgreSQL schema without connecting to Telegram")
    args = parser.parse_args()
    try:
        settings = Settings()
    except ValidationError as exc:
        fields = sorted(
            {str(error["loc"][0]) if error["loc"] else "business hours" for error in exc.errors()}
        )
        parser.exit(2, f"Invalid configuration: {', '.join(fields)}. See .env.example.\n")
    configure_logging(settings)
    try:
        asyncio.run(run(args, settings))
    except KeyboardInterrupt:
        pass
    except (TokenValidationError, TelegramUnauthorizedError):
        parser.exit(
            1, "Invalid BOT_TOKEN. Configure it locally in .env or environment variables.\n"
        )
    except Exception as exc:
        # A PostgreSQL exception may include connection credentials or bound customer input.
        logger.error(
            "Startup or runtime failed (%s). Check configuration and migrations.",
            type(exc).__name__,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
