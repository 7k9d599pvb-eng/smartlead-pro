"""Environment-only configuration; secret values are never logged."""

from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from smartlead.i18n import SUPPORTED_LANGUAGES
from smartlead.services.errors import ValidationError as BusinessValidationError
from smartlead.services.validation import validate_support_contact


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: SecretStr
    database_url: SecretStr
    admin_ids: str = ""
    admin_notification_chat_id: int | None = None
    admin_notification_language: str = "en"
    business_name: str = Field(default="SmartLead Studio", min_length=1, max_length=100)
    business_support_contact: str | None = None
    business_timezone: str = "Europe/London"
    business_open: time = time(9, 0)
    business_close: time = time(18, 0)
    business_weekdays: str = "0,1,2,3,4"
    booking_horizon_days: int = Field(default=14, ge=1, le=60)
    booking_notice_minutes: int = Field(default=60, ge=0, le=10080)
    slot_step_minutes: int = Field(default=30, ge=5, le=240)
    outbox_poll_seconds: float = Field(default=2, ge=0.1, le=60)
    outbox_max_attempts: int = Field(default=8, ge=1, le=50)
    log_level: str = "INFO"

    @field_validator("business_open", "business_close")
    @classmethod
    def valid_wall_time(cls, value: time) -> time:
        if value.tzinfo is not None or value.second or value.microsecond:
            raise ValueError("Business hours must be local wall times in HH:MM format")
        return value

    @field_validator("log_level")
    @classmethod
    def valid_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        return value

    @field_validator("business_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use a valid IANA timezone, e.g. Europe/London") from exc
        return value

    @field_validator("database_url")
    @classmethod
    def valid_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except Exception as exc:
            raise ValueError("DATABASE_URL must be a PostgreSQL asyncpg URL") from exc
        if url.drivername != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must start with postgresql+asyncpg://")
        return value

    @field_validator("admin_ids")
    @classmethod
    def valid_admin_ids(cls, value: str) -> str:
        if value.strip():
            try:
                numbers = [int(item.strip()) for item in value.split(",")]
            except ValueError as exc:
                raise ValueError("ADMIN_IDS must be comma-separated positive Telegram IDs") from exc
            if any(number <= 0 for number in numbers):
                raise ValueError("ADMIN_IDS must contain positive Telegram IDs")
        return value

    @field_validator("admin_notification_chat_id")
    @classmethod
    def valid_admin_notification_chat_id(cls, value: int | None) -> int | None:
        if value is not None and value >= 0:
            raise ValueError("ADMIN_NOTIFICATION_CHAT_ID must be a negative Telegram group ID")
        return value

    @field_validator("admin_notification_language")
    @classmethod
    def valid_notification_language(cls, value: str) -> str:
        if value not in SUPPORTED_LANGUAGES:
            raise ValueError("ADMIN_NOTIFICATION_LANGUAGE must be en, fr, ru, uk, de or ja")
        return value

    @field_validator("business_support_contact")
    @classmethod
    def valid_business_support_contact(cls, value: str | None) -> str | None:
        try:
            return validate_support_contact(value)
        except BusinessValidationError as exc:
            raise ValueError(
                "BUSINESS_SUPPORT_CONTACT must be a Telegram username or t.me profile link"
            ) from exc

    @field_validator("business_weekdays")
    @classmethod
    def valid_weekdays(cls, value: str) -> str:
        try:
            days = [int(day.strip()) for day in value.split(",")]
        except ValueError as exc:
            raise ValueError("BUSINESS_WEEKDAYS must contain numbers 0..6") from exc
        if not days or any(day not in range(7) for day in days):
            raise ValueError("BUSINESS_WEEKDAYS must contain numbers 0..6")
        return value

    @model_validator(mode="after")
    def valid_hours(self) -> "Settings":
        if self.business_open >= self.business_close:
            raise ValueError("BUSINESS_CLOSE must be later than BUSINESS_OPEN on the same day")
        return self

    @property
    def allowed_admin_ids(self) -> frozenset[int]:
        return frozenset(int(item.strip()) for item in self.admin_ids.split(",") if item.strip())
