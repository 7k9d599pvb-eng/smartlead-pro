"""Redact configured secrets even when third-party logs include an exception."""

import logging

from sqlalchemy.engine import make_url

from smartlead.config import Settings


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: list[str]):
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.secrets = [value for value in secrets if value]

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in self.secrets:
            rendered = rendered.replace(secret, "[REDACTED]")
        return rendered


def configure_logging(settings: Settings) -> None:
    url = settings.database_url.get_secret_value()
    password = make_url(url).password
    handler = logging.StreamHandler()
    handler.setFormatter(
        RedactingFormatter([settings.bot_token.get_secret_value(), url, password or ""])
    )
    logging.basicConfig(level=settings.log_level.upper(), handlers=[handler], force=True)
    # SQLAlchemy DEBUG result rows can contain contacts even when parameters are hidden.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
