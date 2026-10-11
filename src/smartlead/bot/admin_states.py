"""Private-chat forms for allowlisted business administrators."""

from aiogram.fsm.state import State, StatesGroup


class ServiceForm(StatesGroup):
    name = State()
    description = State()
    duration = State()
    review = State()


class SupportForm(StatesGroup):
    contact = State()
