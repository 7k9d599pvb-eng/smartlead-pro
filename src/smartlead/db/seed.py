"""Idempotent initial business setup; existing business data is preserved."""

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smartlead.config import Settings
from smartlead.db.models import BusinessSettings, Service


async def seed_database(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    sync_settings: bool = False,
) -> None:
    async with session_factory.begin() as session:
        # Serialize concurrent startup jobs without adding deployment infrastructure.
        await session.execute(text("SELECT pg_advisory_xact_lock(741923058)"))
        values = {
            "id": 1,
            "name": settings.business_name,
            "timezone": settings.business_timezone,
            "open_time": settings.business_open,
            "close_time": settings.business_close,
            "weekdays": sorted({int(day) for day in settings.business_weekdays.split(",")}),
            "slot_step_minutes": settings.slot_step_minutes,
            "booking_horizon_days": settings.booking_horizon_days,
            "booking_notice_minutes": settings.booking_notice_minutes,
            "support_contact": settings.business_support_contact,
        }
        statement = insert(BusinessSettings).values(**values)
        if sync_settings:
            update_values = {key: value for key, value in values.items() if key != "id"}
            if settings.business_support_contact is None:
                # An omitted optional contact should not erase a value set in the admin panel.
                update_values.pop("support_contact")
            statement = statement.on_conflict_do_update(
                index_elements=[BusinessSettings.id],
                set_=update_values,
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=[BusinessSettings.id])
        await session.execute(statement)
        # Seed an empty catalog once. Renaming a service must not recreate demo entries.
        if await session.scalar(select(Service.id).limit(1)) is None:
            for name, description, duration in (
                ("Consultation", "Discuss your needs with our team.", 30),
                ("Service appointment", "A dedicated service session.", 60),
            ):
                session.add(Service(name=name, description=description, duration_minutes=duration))
