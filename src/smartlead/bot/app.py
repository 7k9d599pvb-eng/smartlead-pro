"""Construct one dispatcher for one polling process."""

from copy import deepcopy

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from smartlead.bot.admin import router as admin_router
from smartlead.bot.client import router as client_router
from smartlead.bot.middleware import BotMiddleware, UpdateDedupMiddleware
from smartlead.config import Settings
from smartlead.services.core import SmartLeadService


def build_dispatcher(service: SmartLeadService, settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    dispatcher.update.outer_middleware(UpdateDedupMiddleware())
    middleware = BotMiddleware(service, settings)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(middleware)
    # Router instances acquire a parent when included. Clone the handler trees so
    # callers can build fresh dispatchers in tests or across polling lifecycles.
    dispatcher.include_router(deepcopy(admin_router))
    dispatcher.include_router(deepcopy(client_router))
    return dispatcher
