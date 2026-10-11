"""Configuration mistakes fail before the bot connects to Telegram or PostgreSQL."""

from datetime import time, timedelta, timezone

import pytest
from pydantic import ValidationError

from smartlead.config import Settings


def settings(**overrides):
    return Settings(
        _env_file=None,
        bot_token="123456:unit-test-token",
        database_url="postgresql+asyncpg://test:private-password@localhost/smartlead_test",
        **overrides,
    )


def test_secret_values_are_hidden_from_settings_representation():
    configured = settings(admin_ids=" 11,22,11 ")
    assert configured.allowed_admin_ids == frozenset({11, 22})
    rendered = repr(configured)
    assert "private-password" not in rendered
    assert "unit-test-token" not in rendered


@pytest.mark.parametrize("value", ["0", "-42", "hello", "11,", "11;22"])
def test_invalid_admin_allowlist_is_rejected(value):
    with pytest.raises(ValidationError):
        settings(admin_ids=value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"business_timezone": "Mars/Olympus"},
        {"business_open": "18:00", "business_close": "09:00"},
        {"business_open": "09:00", "business_close": "09:00"},
        {"business_open": time(9, 0, 1)},
        {"business_close": time(18, 0, 0, 1)},
        {"business_open": time(9, tzinfo=timezone(timedelta(hours=1)))},
        {"business_weekdays": "7"},
        {"business_weekdays": ""},
        {"booking_horizon_days": 0},
        {"booking_notice_minutes": -1},
        {"slot_step_minutes": 0},
        {"outbox_max_attempts": 0},
        {"log_level": "VERBOSE"},
    ],
)
def test_invalid_business_configuration_is_rejected(overrides):
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_log_level_is_normalized_to_uppercase():
    assert settings(log_level="warning").log_level == "WARNING"


@pytest.mark.parametrize("language", ["en", "fr", "ru", "uk", "de", "ja"])
def test_shared_notification_language_accepts_supported_codes(language):
    assert settings(admin_notification_language=language).admin_notification_language == language


@pytest.mark.parametrize("language", ["es", "", "RU", "ru-RU"])
def test_shared_notification_language_rejects_unsupported_codes(language):
    with pytest.raises(ValidationError):
        settings(admin_notification_language=language)


def test_admin_notification_group_id_must_be_negative_and_support_is_canonicalized():
    configured = settings(
        admin_notification_chat_id=-100_123_456_7890,
        business_support_contact="@smartlead_help",
    )

    assert configured.admin_notification_chat_id == -100_123_456_7890
    assert configured.business_support_contact == "https://t.me/smartlead_help"


@pytest.mark.parametrize("chat_id", [0, 123456, "not-a-chat"])
def test_admin_notification_chat_id_rejects_nonnegative_or_malformed_values(chat_id):
    with pytest.raises(ValidationError):
        settings(admin_notification_chat_id=chat_id)


@pytest.mark.parametrize(
    "contact",
    ["http://t.me/smartlead_help", "https://example.org/support", "t.me/user?x=1"],
)
def test_business_support_contact_rejects_unsafe_urls(contact):
    with pytest.raises(ValidationError):
        settings(business_support_contact=contact)


@pytest.mark.parametrize("url", ["sqlite:///data.db", "postgresql://localhost/test", "invalid"])
def test_only_async_postgresql_urls_are_accepted(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, bot_token="123:test", database_url=url)
