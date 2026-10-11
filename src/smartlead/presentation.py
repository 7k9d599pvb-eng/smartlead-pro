"""Localized customer and administrator messages shared by the bot and outbox."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from babel.dates import format_date, format_time

from smartlead.i18n import LOCALE_BY_LANGUAGE, current_language, normalize_language, tr


def _language_code(language: str | None) -> str:
    return current_language() if language is None else normalize_language(language)


def status_label(status: object, *, language: str | None = None) -> str:
    """Return a translated badge for a persisted request or appointment status."""
    value = str(getattr(status, "value", status) or "unknown").strip().casefold()
    key = {
        "new": "status.new",
        "in_progress": "status.in_progress",
        "completed": "status.completed",
        "cancelled": "status.cancelled",
        "booked": "status.booked",
    }.get(value)
    if key is not None:
        return tr(key, language=language)
    return tr("status.unknown", language=language)


def _as_local(value: datetime, timezone_name: str) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(timezone_name))


def format_calendar_day(value: date, *, language: str | None = None) -> str:
    """Format a compact calendar button date in the active language."""
    code = _language_code(language)
    locale = LOCALE_BY_LANGUAGE[code]
    pattern = {
        "en": "EEE, dd MMM",
        "fr": "EEE d MMM",
        "ru": "EEE, d MMM",
        "uk": "EEE, d MMM",
        "de": "EEE, d. MMM",
        "ja": "M月d日 (EEE)",
    }[code]
    return format_date(value, format=pattern, locale=locale)


def format_business_date(
    value: date | datetime,
    timezone_name: str,
    *,
    language: str | None = None,
) -> str:
    """Format a full calendar date in the business timezone and active language."""
    if isinstance(value, datetime):
        value = _as_local(value, timezone_name).date()
    locale = LOCALE_BY_LANGUAGE[_language_code(language)]
    return format_date(value, format="full", locale=locale)


def format_business_time(
    value: datetime,
    timezone_name: str,
    *,
    language: str | None = None,
) -> str:
    """Format local 24-hour time with its IANA timezone and current UTC offset."""
    local = _as_local(value, timezone_name)
    offset = local.utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset is not None else 0
    sign = "+" if minutes >= 0 else "-"
    hours, remainder = divmod(abs(minutes), 60)
    locale = LOCALE_BY_LANGUAGE[_language_code(language)]
    local_time = format_time(local, format="HH:mm", locale=locale)
    return f"{local_time} ({timezone_name}, UTC{sign}{hours:02d}:{remainder:02d})"


def format_business_datetime(
    value: datetime,
    timezone_name: str,
    *,
    language: str | None = None,
) -> str:
    return tr(
        "datetime.join",
        language=language,
        date=format_business_date(value, timezone_name, language=language),
        time=format_business_time(value, timezone_name, language=language),
    )


def format_calendar_datetime(
    value: datetime,
    timezone_name: str,
    *,
    language: str | None = None,
) -> str:
    """Keep slot buttons compact while retaining the business clock and DST offset."""
    local = _as_local(value, timezone_name)
    return (
        f"{format_calendar_day(local.date(), language=language)} "
        f"{format_business_time(value, timezone_name, language=language)}"
    )


def format_request_submitted(
    request_number: str,
    service_name: str,
    status: str = "new",
    *,
    language: str | None = None,
) -> str:
    return "\n".join(
        (
            tr("request.submitted.title", language=language),
            "",
            tr("request.submitted.reference", language=language, request_number=request_number),
            tr("label.service", language=language, service_name=service_name),
            tr("label.status", language=language, status=status_label(status, language=language)),
            "",
            tr("request.submitted.thanks", language=language),
        )
    )


def format_booking_confirmed(
    booking_number: str,
    service_name: str,
    starts_at: datetime,
    timezone_name: str,
    status: str = "booked",
    *,
    language: str | None = None,
) -> str:
    return "\n".join(
        (
            tr("booking.confirmed.title", language=language),
            "",
            tr("booking.confirmed.reference", language=language, booking_number=booking_number),
            tr("label.service", language=language, service_name=service_name),
            tr(
                "label.date",
                language=language,
                date=format_business_date(starts_at, timezone_name, language=language),
            ),
            tr(
                "label.time",
                language=language,
                time=format_business_time(starts_at, timezone_name, language=language),
            ),
            tr("label.status", language=language, status=status_label(status, language=language)),
            "",
            tr("booking.confirmed.thanks", language=language),
        )
    )


def format_new_request_notification(
    request_number: str,
    service_name: str,
    customer_name: str,
    contact: str,
    details: str,
    *,
    language: str | None = None,
) -> str:
    """Render a localized plain-text new-request notice for the admin outbox."""
    return "\n".join(
        (
            tr("request.notification.title", language=language),
            "",
            tr("request.notification.reference", language=language, request_number=request_number),
            tr("label.customer", language=language, customer_name=customer_name),
            tr("label.phone", language=language, contact=contact),
            tr("label.service", language=language, service_name=service_name),
            tr("request.notification.description", language=language, details=details),
            tr("label.status", language=language, status=status_label("new", language=language)),
        )
    )


def format_request_status_notification(
    request_number: str,
    status: str,
    *,
    language: str | None = None,
) -> str:
    """Render a localized customer update when an administrator changes status."""
    normalized = str(getattr(status, "value", status) or "unknown").strip().casefold()
    if normalized == "in_progress":
        key = "request.status.in_progress"
    elif normalized == "completed":
        key = "request.status.completed"
    elif normalized == "cancelled":
        key = "request.status.cancelled"
    else:
        return tr(
            "request.status.generic",
            language=language,
            request_number=request_number,
            status=status_label(status, language=language),
        )
    return tr(key, language=language, request_number=request_number)


def format_customer_request_update(
    request_number: str,
    status: str,
    *,
    language: str | None = None,
) -> str:
    """Alias with an explicit customer-facing name for downstream outbox callers."""
    return format_request_status_notification(request_number, status, language=language)


def format_booking_cancelled(
    booking_number: str,
    *,
    language: str | None = None,
) -> str:
    return "\n".join(
        (
            tr("booking.cancelled.title", language=language),
            "",
            tr("booking.cancelled.reference", language=language, booking_number=booking_number),
            tr(
                "label.status",
                language=language,
                status=status_label("cancelled", language=language),
            ),
        )
    )
