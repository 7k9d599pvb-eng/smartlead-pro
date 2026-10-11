"""Small in-memory Bot API session for dispatcher tests; it never opens a socket."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Chat, Message, User


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.methods: list[Any] = []
        self.messages: list[Message] = []
        self.fail_method_once: str | None = None

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: Any,
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        self.methods.append(method)
        if type(method).__name__ == self.fail_method_once:
            self.fail_method_once = None
            raise TelegramAPIError(method, "synthetic Telegram API failure")
        if isinstance(method, SendMessage):
            message = Message(
                message_id=max((item.message_id for item in self.messages), default=0) + 1,
                date=datetime.now(UTC),
                chat=Chat(
                    id=int(method.chat_id),
                    type="supergroup" if int(method.chat_id) < 0 else "private",
                ),
                from_user=User(
                    id=int(bot.id),
                    is_bot=True,
                    first_name="SmartLead test bot",
                ),
                text=method.text,
                reply_markup=method.reply_markup,
            )
            self.messages.append(message)
            return message
        if isinstance(method, EditMessageText):
            chat_id = int(method.chat_id)
            message_id = int(method.message_id)
            index = next(
                (
                    index
                    for index, item in enumerate(self.messages)
                    if item.chat.id == chat_id and item.message_id == message_id
                ),
                None,
            )
            if index is None:
                # Group callback tests may edit an incoming message that was not sent by
                # this fake bot session. Model the external message at its actual key.
                message = Message(
                    message_id=message_id,
                    date=datetime.now(UTC),
                    chat=Chat(id=chat_id, type="supergroup" if chat_id < 0 else "private"),
                    from_user=User(
                        id=int(bot.id),
                        is_bot=True,
                        first_name="SmartLead test bot",
                    ),
                    text=method.text,
                    reply_markup=method.reply_markup,
                )
                self.messages.append(message)
            else:
                message = self.messages[index].model_copy(
                    update={"text": method.text, "reply_markup": method.reply_markup}
                )
                self.messages[index] = message
            return message
        return True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        if False:  # Keep this an async generator without producing network data.
            yield b""
