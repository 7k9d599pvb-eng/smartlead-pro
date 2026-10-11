"""Validation shared by FSM input and the service transaction boundary."""

import re

from smartlead.i18n import tr
from smartlead.services.errors import ValidationError


def _text(value: str, label: str, minimum: int, maximum: int, *, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError("error_enter_text", field=tr(label).lower())
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise ValidationError(
            "error_text_length", field=tr(label), minimum=minimum, maximum=maximum
        )
    if any(
        (ord(char) < 32 and not (multiline and char in "\n\t")) or ord(char) == 127
        for char in value
    ):
        raise ValidationError("error_control_characters", field=tr(label))
    return value


def validate_name(value: str) -> str:
    return _text(value, "field_name", 2, 100)


def validate_details(value: str) -> str:
    return _text(value, "field_details", 10, 2000, multiline=True)


def validate_contact(value: str) -> str:
    value = _text(value, "field_contact", 5, 200)
    email = re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", value)
    username = re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{4,31}", value)
    phone = re.fullmatch(r"\+?[0-9 ()-]+", value)
    if phone and 7 <= len(re.sub(r"[^0-9]", "", value)) <= 15:
        return value
    if email or username:
        return value
    raise ValidationError("error_invalid_contact")


def validate_phone(value: str) -> str:
    """Validate a new form's phone number without changing legacy contact handling."""
    value = _text(value, "field_phone", 7, 40)
    if not re.fullmatch(r"\+?[0-9](?:[0-9 ().-]*[0-9])?", value):
        raise ValidationError("error_phone_format")
    digits = re.sub(r"\D", "", value)
    if not 7 <= len(digits) <= 15:
        raise ValidationError("error_phone_digits")
    if value.count("(") != value.count(")") or value.count("(") > 1:
        raise ValidationError("error_invalid_phone")
    if "(" in value and value.index("(") > value.rindex(")"):
        raise ValidationError("error_invalid_phone")
    return value


def validate_support_contact(value: str | None) -> str | None:
    """Return a canonical Telegram profile URL, or None when support is disabled."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("error_invalid_support")
    value = value.strip()
    if not value:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value) or len(value) > 200:
        raise ValidationError("error_invalid_support")

    username_pattern = r"[A-Za-z][A-Za-z0-9_]{4,31}"
    username = re.fullmatch(rf"@?({username_pattern})", value)
    link = re.fullmatch(rf"(?:https://)?t\.me/({username_pattern})/?", value, re.IGNORECASE)
    match = username or link
    if match is None:
        raise ValidationError("error_invalid_support")
    return f"https://t.me/{match.group(1)}"


def validate_service_name(value: str) -> str:
    return _text(value, "field_service_name", 2, 100)


def validate_service_description(value: str) -> str:
    return _text(value, "field_service_description", 0, 2000, multiline=True)


def validate_service_duration(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 480:
        raise ValidationError("error_duration_range")
    return value
