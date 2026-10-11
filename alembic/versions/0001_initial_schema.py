"""Create SmartLead tables with idempotency and PostgreSQL booking protection.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint(
            "duration_minutes > 0 AND duration_minutes <= 480", name="services_duration_valid"
        ),
    )
    op.create_table(
        "business_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("open_time", sa.Time(), nullable=False),
        sa.Column("close_time", sa.Time(), nullable=False),
        sa.Column("weekdays", sa.JSON(), nullable=False),
        sa.Column("slot_step_minutes", sa.Integer(), nullable=False),
        sa.Column("booking_horizon_days", sa.Integer(), nullable=False),
        sa.Column("booking_notice_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="business_settings_singleton"),
        sa.CheckConstraint("open_time < close_time", name="business_settings_hours_valid"),
        sa.CheckConstraint(
            "slot_step_minutes >= 5 AND slot_step_minutes <= 240",
            name="business_settings_step_valid",
        ),
        sa.CheckConstraint(
            "booking_horizon_days >= 1 AND booking_horizon_days <= 60",
            name="business_settings_horizon_valid",
        ),
        sa.CheckConstraint(
            "booking_notice_minutes >= 0 AND booking_notice_minutes <= 10080",
            name="business_settings_notice_valid",
        ),
    )
    op.create_table(
        "requests",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("services.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("submission_key", sa.Uuid(), nullable=False),
        sa.Column("customer_name", sa.String(100), nullable=False),
        sa.Column("contact", sa.String(200), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), server_default="new", nullable=False),
        *timestamps(),
        sa.UniqueConstraint("user_id", "submission_key", name="requests_user_submission_unique"),
        sa.CheckConstraint(
            "status IN ('new', 'in_progress', 'completed', 'cancelled')",
            name="requests_status_valid",
        ),
        sa.CheckConstraint("length(details) <= 2000", name="requests_details_length_valid"),
    )
    op.create_index("requests_user_created_idx", "requests", ["user_id", "created_at"])
    op.create_index("requests_status_created_idx", "requests", ["status", "created_at"])
    op.create_table(
        "appointments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("services.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("submission_key", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default="booked", nullable=False),
        sa.Column("customer_name", sa.String(100), nullable=False),
        sa.Column("contact", sa.String(200), nullable=False),
        *timestamps(),
        sa.UniqueConstraint(
            "user_id", "submission_key", name="appointments_user_submission_unique"
        ),
        sa.CheckConstraint(
            "status IN ('booked', 'completed', 'cancelled')", name="appointments_status_valid"
        ),
        sa.CheckConstraint("ends_at > starts_at", name="appointments_interval_valid"),
    )
    # The shared calendar protects every service, including simultaneous bookings.
    # [) allows an appointment to start exactly when another appointment ends.
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT appointments_no_overlap "
        "EXCLUDE USING gist (tstzrange(starts_at, ends_at, '[)') WITH &&) "
        "WHERE (status = 'booked')"
    )
    op.create_index("appointments_user_created_idx", "appointments", ["user_id", "created_at"])
    op.create_index("appointments_status_starts_idx", "appointments", ["status", "starts_at"])
    op.create_table(
        "status_history",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "request_id",
            sa.BigInteger(),
            sa.ForeignKey("requests.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "appointment_id",
            sa.BigInteger(),
            sa.ForeignKey("appointments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("old_status", sa.String(20), nullable=True),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(request_id IS NOT NULL AND appointment_id IS NULL) OR "
            "(request_id IS NULL AND appointment_id IS NOT NULL)",
            name="status_history_exactly_one_target",
        ),
    )
    op.create_index("status_history_request_idx", "status_history", ["request_id", "created_at"])
    op.create_index(
        "status_history_appointment_idx", "status_history", ["appointment_id", "created_at"]
    )
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("dedup_key", sa.String(200), unique=True, nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="outbox_status_valid"),
        sa.CheckConstraint("attempts >= 0", name="outbox_attempts_nonnegative"),
    )
    op.create_index(
        "notification_outbox_status_available_idx",
        "notification_outbox",
        ["status", "available_at"],
    )


def downgrade() -> None:
    op.drop_table("notification_outbox")
    op.drop_table("status_history")
    op.drop_table("appointments")
    op.drop_table("requests")
    op.drop_table("business_settings")
    op.drop_table("services")
    op.drop_table("users")
