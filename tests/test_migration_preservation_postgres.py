"""The v1.1 migration backfills snapshots and preserves legacy rows and outbox work."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import select, text

from alembic import command
from smartlead.db.models import Appointment, LeadRequest, NotificationOutbox, StatusHistory, User

pytestmark = pytest.mark.integration


def _set_revision(database_url: str, revision: str) -> None:
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(config, revision) if revision == "head" else command.downgrade(
            config, revision
        )
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


@pytest.mark.asyncio
async def test_0001_to_head_preserves_legacy_records_and_pending_outbox(
    clean_pg,
    pg_engine,
    migrated_test_database_url,
):
    await asyncio.to_thread(_set_revision, migrated_test_database_url, "0001")
    legacy_key = str(uuid4())
    try:
        async with pg_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, telegram_id, full_name, username) "
                    "VALUES (1, 111001, 'Legacy customer', NULL)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO services (id, name, description, duration_minutes) "
                    "VALUES (1, 'Legacy consultation', 'Original service copy', 30)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO business_settings "
                    "(id, name, timezone, open_time, close_time, weekdays, "
                    "slot_step_minutes, booking_horizon_days, booking_notice_minutes) "
                    "VALUES (1, 'Legacy studio', 'UTC', '09:00', '18:00', '[0,1,2,3,4]', "
                    "30, 14, 60)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO requests "
                    "(id, user_id, service_id, submission_key, customer_name, contact, details) "
                    "VALUES (1, 1, 1, CAST(:key AS UUID), 'Legacy customer', "
                    "'legacy@example.org', 'Existing request details')"
                ),
                {"key": legacy_key},
            )
            await connection.execute(
                text(
                    "INSERT INTO appointments "
                    "(id, user_id, service_id, submission_key, starts_at, ends_at, "
                    "customer_name, contact) VALUES (1, 1, 1, CAST(:key AS UUID), "
                    "'2030-01-02 10:00:00+00', '2030-01-02 10:30:00+00', "
                    "'Legacy customer', 'legacy@example.org')"
                ),
                {"key": str(uuid4())},
            )
            await connection.execute(
                text(
                    "INSERT INTO status_history "
                    "(id, request_id, appointment_id, old_status, new_status, actor_telegram_id) "
                    "VALUES (1, 1, NULL, NULL, 'new', 111001)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO notification_outbox "
                    "(id, telegram_id, text, dedup_key) "
                    "VALUES (1, 900001, 'Legacy pending notification', 'legacy:outbox:1')"
                )
            )

        await asyncio.to_thread(_set_revision, migrated_test_database_url, "head")

        async with pg_engine.begin() as connection:
            await connection.execute(
                text("UPDATE services SET name = 'Renamed consultation' WHERE id = 1")
            )
        async with clean_pg() as session:
            legacy_user = await session.get(User, 1)
            request = await session.scalar(select(LeadRequest).where(LeadRequest.id == 1))
            appointment = await session.scalar(select(Appointment).where(Appointment.id == 1))
            outbox = await session.scalar(
                select(NotificationOutbox).where(NotificationOutbox.id == 1)
            )
            history_count = await session.scalar(
                select(StatusHistory.id).where(StatusHistory.id == 1)
            )

        assert request is not None
        assert legacy_user is not None
        assert legacy_user.telegram_id == 111001
        assert legacy_user.language_code == "en"
        assert request.contact == "legacy@example.org"
        assert request.service_name == "Legacy consultation"
        assert appointment is not None
        assert appointment.contact == "legacy@example.org"
        assert appointment.service_name == "Legacy consultation"
        assert outbox is not None
        assert outbox.text == "Legacy pending notification"
        assert outbox.status == "pending"
        assert outbox.reply_markup is None
        assert history_count == 1
    finally:
        await asyncio.to_thread(_set_revision, migrated_test_database_url, "head")
        async with pg_engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE notification_outbox, status_history, appointments, requests, "
                    "business_settings, services, users RESTART IDENTITY CASCADE"
                )
            )
