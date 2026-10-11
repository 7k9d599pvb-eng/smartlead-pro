"""Customer and administrator notifications stay plain, readable text."""

from datetime import UTC, datetime

from smartlead.presentation import (
    format_booking_cancelled,
    format_booking_confirmed,
    format_new_request_notification,
    format_request_status_notification,
    format_request_submitted,
    status_label,
)


def test_status_labels_and_customer_request_notifications_are_clear():
    assert status_label("new") == "🟡 New"
    assert status_label("in_progress") == "🔵 In Progress"
    assert status_label("completed") == "🟢 Completed"

    receipt = format_request_submitted("SL-R-000042", "Consultation")
    assert "Request ID: #SL-R-000042" in receipt
    assert "Service: Consultation" in receipt
    assert "Status: 🟡 New" in receipt

    update = format_request_status_notification("SL-R-000042", "completed")
    assert "Request Completed!" in update
    assert "request #SL-R-000042 has been completed" in update


def test_booking_receipts_and_admin_alerts_include_actionable_details():
    starts_at = datetime(2026, 10, 12, 10, tzinfo=UTC)
    receipt = format_booking_confirmed("SL-A-000087", "Consultation", starts_at, "UTC")
    assert "Booking ID: #SL-A-000087" in receipt
    assert "Monday, October 12, 2026" in receipt
    assert "10:00 (UTC, UTC+00:00)" in receipt
    assert "Status: 🟢 Booked" in receipt

    cancelled = format_booking_cancelled("SL-A-000087")
    assert "Appointment Cancelled" in cancelled
    assert "Status: 🔴 Cancelled" in cancelled

    alert = format_new_request_notification(
        "SL-R-000042",
        "Consultation",
        "Demo Customer",
        "+1 (415) 555-0123",
        "Please call about a consultation.",
    )
    assert "🔔 New Customer Request" in alert
    assert "Request: #SL-R-000042" in alert
    assert "Phone: +1 (415) 555-0123" in alert
    assert "Description: Please call about a consultation." in alert
