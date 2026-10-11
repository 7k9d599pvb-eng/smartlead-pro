"""Catalog loading, language context, placeholders, and localized dates."""

import asyncio
from datetime import UTC, date, datetime

import pytest

import smartlead.i18n as i18n
from smartlead.i18n import (
    SUPPORTED_LANGUAGES,
    current_language,
    get_catalog,
    normalize_language,
    tr,
    use_language,
    validate_catalogs,
)
from smartlead.presentation import (
    format_business_date,
    format_business_datetime,
    format_business_time,
    format_calendar_day,
    format_request_submitted,
)


def test_supported_language_names_and_normalization():
    assert SUPPORTED_LANGUAGES == {
        "en": "English",
        "fr": "Français",
        "ru": "Русский",
        "uk": "Українська",
        "de": "Deutsch",
        "ja": "日本語",
    }
    assert normalize_language(None) == "en"
    assert normalize_language("FR") == "fr"
    assert normalize_language("fr-CA") == "fr"
    assert normalize_language("uk_UA") == "uk"
    assert normalize_language("xx") == "en"


def test_catalogs_are_complete_and_have_matching_placeholders():
    validate_catalogs()
    english = get_catalog("en")
    assert set(english) == set(get_catalog("fr"))
    assert "notification_admin_booking" in english
    for language in SUPPORTED_LANGUAGES:
        assert all(
            value.strip() and "\ufffd" not in value for value in get_catalog(language).values()
        )


def test_translation_falls_back_to_english_and_exposes_missing_variables(monkeypatch):
    assert tr("request.submitted.title", language="fr") == "✅ Demande envoyée !"
    assert tr("key.that.does.not.exist", language="fr") == "key.that.does.not.exist"

    original_get_catalog = i18n.get_catalog
    monkeypatch.setattr(
        i18n,
        "get_catalog",
        lambda language=None: (
            {
                key: value
                for key, value in original_get_catalog("fr").items()
                if key != "request.submitted.title"
            }
            if language == "fr"
            else original_get_catalog(language)
        ),
    )
    assert tr("request.submitted.title", language="fr") == "✅ Request Submitted!"

    with pytest.raises(KeyError, match="request_number"):
        tr("request.submitted.reference", language="en")
    with pytest.raises(TypeError, match="Unused translation variables"):
        tr("request.submitted.title", language="en", unused="value")


def test_language_context_restores_after_nested_blocks_and_exceptions():
    assert current_language() == "en"
    with use_language("fr"):
        assert current_language() == "fr"
        with use_language("de"):
            assert current_language() == "de"
        assert current_language() == "fr"
    assert current_language() == "en"

    with pytest.raises(RuntimeError):
        with use_language("ja"):
            assert current_language() == "ja"
            raise RuntimeError("leave the language block")
    assert current_language() == "en"


@pytest.mark.asyncio
async def test_concurrent_language_contexts_do_not_leak_between_tasks():
    expected = {
        "en": "New request",
        "fr": "Nouvelle demande",
        "ru": "Новая заявка",
        "uk": "Нова заявка",
        "de": "Neue Anfrage",
        "ja": "新しいお問い合わせ",
    }

    async def render(language: str) -> tuple[str, str]:
        with use_language(language):
            await asyncio.sleep(0)
            return current_language(), tr("menu.new_request")

    rendered = await asyncio.gather(*(render(language) for language in SUPPORTED_LANGUAGES))
    assert [language for language, _ in rendered] == list(SUPPORTED_LANGUAGES)
    assert [value for _, value in rendered] == [
        expected[language] for language in SUPPORTED_LANGUAGES
    ]
    assert current_language() == "en"


def test_default_english_presentation_copy_is_preserved():
    receipt = format_request_submitted("SL-R-000042", "Consultation")
    assert receipt == (
        "✅ Request Submitted!\n\n"
        "Request ID: #SL-R-000042\n"
        "Service: Consultation\n"
        "Status: 🟡 New\n\n"
        "Thank you! We have received your request and will contact you soon."
    )


def test_business_dates_calendar_labels_and_time_join_are_localized():
    starts_at = datetime(2026, 10, 12, 7, 30, tzinfo=UTC)
    assert format_calendar_day(date(2026, 10, 12)) == "Mon, 12 Oct"
    assert format_calendar_day(date(2026, 10, 12), language="fr").startswith("lun. 12 oct.")
    assert format_calendar_day(date(2026, 10, 12), language="de").startswith("Mo., 12. Okt.")
    assert format_calendar_day(date(2026, 10, 12), language="ja").startswith("10月12日")
    assert format_business_date(starts_at, "UTC") == "Monday, October 12, 2026"
    assert format_business_date(starts_at, "Europe/Moscow", language="fr").startswith(
        "lundi 12 octobre 2026"
    )
    assert format_business_date(starts_at, "Europe/Moscow", language="ru").startswith(
        "понедельник, 12 октября 2026"
    )
    assert format_business_date(starts_at, "Europe/Moscow", language="uk").startswith(
        "понеділок, 12 жовтня 2026"
    )
    assert format_business_date(starts_at, "Europe/Moscow", language="de").startswith(
        "Montag, 12. Oktober 2026"
    )
    assert format_business_date(starts_at, "Europe/Moscow", language="ja").startswith(
        "2026年10月12日"
    )

    assert format_business_time(starts_at, "Europe/Moscow") == ("10:30 (Europe/Moscow, UTC+03:00)")
    assert format_business_datetime(starts_at, "UTC", language="en").endswith(
        " at 07:30 (UTC, UTC+00:00)"
    )
    assert " à 10:30 (Europe/Moscow, UTC+03:00)" in format_business_datetime(
        starts_at, "Europe/Moscow", language="fr"
    )
    assert " в 10:30 (Europe/Moscow, UTC+03:00)" in format_business_datetime(
        starts_at, "Europe/Moscow", language="ru"
    )
    assert " о 10:30 (Europe/Moscow, UTC+03:00)" in format_business_datetime(
        starts_at, "Europe/Moscow", language="uk"
    )
    assert " um 10:30 (Europe/Moscow, UTC+03:00)" in format_business_datetime(
        starts_at, "Europe/Moscow", language="de"
    )
    assert format_business_datetime(starts_at, "Europe/Moscow", language="ja").endswith(
        "10:30 (Europe/Moscow, UTC+03:00)"
    )
