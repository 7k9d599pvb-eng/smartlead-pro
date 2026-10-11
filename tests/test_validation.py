"""The same input rules protect both Telegram forms and service calls."""

import pytest

from smartlead.services.errors import ValidationError
from smartlead.services.validation import (
    validate_contact,
    validate_details,
    validate_name,
    validate_phone,
    validate_service_description,
    validate_service_duration,
    validate_service_name,
    validate_support_contact,
)


@pytest.mark.parametrize(
    ("validator", "value", "expected"),
    [
        (validate_name, "  Ada Lovelace ", "Ada Lovelace"),
        (validate_contact, " ada@example.org ", "ada@example.org"),
        (validate_contact, "+1 (415) 555-0123", "+1 (415) 555-0123"),
        (validate_contact, "@ada_lovelace", "@ada_lovelace"),
        (
            validate_details,
            "  Need a consultation.\nAvailable next week.  ",
            "Need a consultation.\nAvailable next week.",
        ),
    ],
)
def test_valid_input_is_trimmed_and_preserved(validator, value, expected):
    assert validator(value) == expected


@pytest.mark.parametrize("value", ["", " A ", "x" * 101, "Ada\x00Lovelace"])
def test_invalid_name_is_rejected(value):
    with pytest.raises(ValidationError):
        validate_name(value)


@pytest.mark.parametrize("value", ["", "short", "x" * 2001, "need\x01help now"])
def test_invalid_details_are_rejected(value):
    with pytest.raises(ValidationError):
        validate_details(value)


@pytest.mark.parametrize(
    "value",
    ["x@y", "no-at-sign.example", "123456", "+1 (23)", "@ab", "@1invalid", "bad\n@example.org"],
)
def test_invalid_contact_is_rejected(value):
    with pytest.raises(ValidationError):
        validate_contact(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" +1 (415) 555-0123 ", "+1 (415) 555-0123"),
        ("0044 20 7946 0958", "0044 20 7946 0958"),
        ("1234567", "1234567"),
    ],
)
def test_phone_form_input_is_trimmed_and_preserved(value, expected):
    assert validate_phone(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "123456",
        "1234567890123456",
        "call me at 1234567",
        "+",
        "123(4567",
        "123)4567",
        "123-4567 ext. 4",
    ],
)
def test_invalid_phone_form_input_is_rejected(value):
    with pytest.raises(ValidationError):
        validate_phone(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("@smartlead_help", "https://t.me/smartlead_help"),
        ("t.me/smartlead_help", "https://t.me/smartlead_help"),
        ("https://t.me/smartlead_help/", "https://t.me/smartlead_help"),
        ("@" + "a" + "b" * 31, "https://t.me/" + "a" + "b" * 31),
        ("  ", None),
        (None, None),
    ],
)
def test_support_contact_is_canonical_or_cleared(value, expected):
    assert validate_support_contact(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://t.me/smartlead_help",
        "https://evil.example/smartlead_help",
        "https://t.me/smartlead_help?start=unsafe",
        "t.me/smartlead_help@evil.example",
        "@12345",
        "@ab",
    ],
)
def test_unsafe_or_invalid_support_contacts_are_rejected(value):
    with pytest.raises(ValidationError):
        validate_support_contact(value)


def test_service_form_values_are_validated_at_the_service_boundary():
    assert validate_service_name("  Initial consultation  ") == "Initial consultation"
    assert validate_service_description("  Meet to discuss the project.  ") == (
        "Meet to discuss the project."
    )
    assert validate_service_description("") == ""
    assert validate_service_duration(30) == 30

    with pytest.raises(ValidationError):
        validate_service_name("x")
    with pytest.raises(ValidationError):
        validate_service_description("bad\x01text")
    for value in (0, 481, True, "30"):
        with pytest.raises(ValidationError):
            validate_service_duration(value)
