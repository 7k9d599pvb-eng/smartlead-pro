"""Persistence model for one business and one shared appointment calendar."""

from datetime import datetime, time
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# The variant keeps pure service tests possible on SQLite without weakening the
# PostgreSQL schema or the production-only exclusion constraint.
BIG_ID = BigInteger().with_variant(Integer, "sqlite")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "language_code IS NULL OR language_code IN ('en', 'fr', 'ru', 'uk', 'de', 'ja')",
            name="users_language_valid",
        ),
    )

    id: Mapped[int] = mapped_column(BIG_ID, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    full_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(64))
    language_code: Mapped[str | None] = mapped_column(String(2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint(
            "duration_minutes > 0 AND duration_minutes <= 480",
            name="services_duration_valid",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    duration_minutes: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class BusinessSettings(Base):
    __tablename__ = "business_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="business_settings_singleton"),
        CheckConstraint("open_time < close_time", name="business_settings_hours_valid"),
        CheckConstraint(
            "slot_step_minutes >= 5 AND slot_step_minutes <= 240",
            name="business_settings_step_valid",
        ),
        CheckConstraint(
            "booking_horizon_days >= 1 AND booking_horizon_days <= 60",
            name="business_settings_horizon_valid",
        ),
        CheckConstraint(
            "booking_notice_minutes >= 0 AND booking_notice_minutes <= 10080",
            name="business_settings_notice_valid",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(100))
    open_time: Mapped[time] = mapped_column(Time)
    close_time: Mapped[time] = mapped_column(Time)
    weekdays: Mapped[list[int]] = mapped_column(JSON)
    slot_step_minutes: Mapped[int] = mapped_column(Integer)
    booking_horizon_days: Mapped[int] = mapped_column(Integer)
    booking_notice_minutes: Mapped[int] = mapped_column(Integer)
    support_contact: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LeadRequest(Base):
    __tablename__ = "requests"
    __table_args__ = (
        UniqueConstraint("user_id", "submission_key", name="requests_user_submission_unique"),
        CheckConstraint(
            "status IN ('new', 'in_progress', 'completed', 'cancelled')",
            name="requests_status_valid",
        ),
        CheckConstraint("length(details) <= 2000", name="requests_details_length_valid"),
        Index("requests_user_created_idx", "user_id", "created_at"),
        Index("requests_status_created_idx", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BIG_ID, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    service_name: Mapped[str] = mapped_column(String(100))
    submission_key: Mapped[UUID] = mapped_column(Uuid)
    customer_name: Mapped[str] = mapped_column(String(100))
    contact: Mapped[str] = mapped_column(String(200))
    details: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="new", server_default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def number(self) -> str:
        return f"SL-R-{self.id:06d}"


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("user_id", "submission_key", name="appointments_user_submission_unique"),
        CheckConstraint(
            "status IN ('booked', 'completed', 'cancelled')", name="appointments_status_valid"
        ),
        CheckConstraint("ends_at > starts_at", name="appointments_interval_valid"),
        ExcludeConstraint(
            (func.tstzrange(text("starts_at"), text("ends_at"), "[)"), "&&"),
            where=text("status = 'booked'"),
            name="appointments_no_overlap",
            using="gist",
        ).ddl_if(dialect="postgresql"),
        Index("appointments_user_created_idx", "user_id", "created_at"),
        Index("appointments_status_starts_idx", "status", "starts_at"),
    )

    id: Mapped[int] = mapped_column(BIG_ID, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    service_name: Mapped[str] = mapped_column(String(100))
    submission_key: Mapped[UUID] = mapped_column(Uuid)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="booked", server_default="booked")
    customer_name: Mapped[str] = mapped_column(String(100))
    contact: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def number(self) -> str:
        return f"SL-A-{self.id:06d}"


class StatusHistory(Base):
    __tablename__ = "status_history"
    __table_args__ = (
        CheckConstraint(
            "(request_id IS NOT NULL AND appointment_id IS NULL) OR "
            "(request_id IS NULL AND appointment_id IS NOT NULL)",
            name="status_history_exactly_one_target",
        ),
        Index("status_history_request_idx", "request_id", "created_at"),
        Index("status_history_appointment_idx", "appointment_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BIG_ID, primary_key=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("requests.id", ondelete="RESTRICT"))
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="RESTRICT")
    )
    old_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20))
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="outbox_status_valid"),
        CheckConstraint("attempts >= 0", name="outbox_attempts_nonnegative"),
        Index("notification_outbox_status_available_idx", "status", "available_at"),
    )

    id: Mapped[int] = mapped_column(BIG_ID, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    reply_markup: Mapped[dict[str, object] | None] = mapped_column(JSON)
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
