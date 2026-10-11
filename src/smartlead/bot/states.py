"""Conversation states; persistent business data belongs in PostgreSQL."""

from aiogram.fsm.state import State, StatesGroup


class RequestForm(StatesGroup):
    service = State()
    name = State()
    contact = State()
    details = State()
    review = State()


class BookingForm(StatesGroup):
    service = State()
    date = State()
    slot = State()
    name = State()
    contact = State()
    review = State()
