"""Transactional business operations with authorization at every public boundary."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from smartlead.config import Settings
from smartlead.db.models import (
    Appointment,
    BusinessSettings,
    LeadRequest,
    NotificationOutbox,
    Service,
    StatusHistory,
    User,
)
from smartlead.i18n import SUPPORTED_LANGUAGES, normalize_language, tr
from smartlead.presentation import (
    format_booking_cancelled,
    format_business_date,
    format_business_time,
    format_new_request_notification,
    format_request_status_notification,
    status_label,
)
from smartlead.services.errors import (
    AccessDenied,
    InvalidTransition,
    NotFound,
    SlotUnavailable,
    ValidationError,
)
from smartlead.services.slots import slots_for_day
from smartlead.services.validation import (
    validate_details,
    validate_name,
    validate_phone,
    validate_service_description,
    validate_service_duration,
    validate_service_name,
    validate_support_contact,
)

REQUEST_TRANSITIONS = {
    "new": {"in_progress", "completed", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


class SmartLeadService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], settings: Settings):
        self.sessions = session_factory
        self.settings = settings

    def _require_admin(self, telegram_id: int) -> None:
        if telegram_id not in self.settings.allowed_admin_ids:
            raise AccessDenied("error_admin_required")

    def _new_submission_admin_targets(self) -> set[int]:
        group_id = self.settings.admin_notification_chat_id
        return {group_id} if group_id is not None else set(self.settings.allowed_admin_ids)

    @staticmethod
    async def _user(session: AsyncSession, telegram_id: int, *, lock: bool = False) -> User:
        query = select(User).where(User.telegram_id == telegram_id)
        if lock:
            query = query.with_for_update()
        user = await session.scalar(query)
        if user is None:
            raise NotFound("error_start_first")
        return user

    @staticmethod
    async def _service(
        session: AsyncSession,
        service_id: int,
        *,
        active: bool = False,
        lock: bool = False,
    ) -> Service:
        if lock:
            # Share locks allow concurrent submissions but serialize against CRUD's FOR UPDATE.
            service = await session.scalar(
                select(Service).where(Service.id == service_id).with_for_update(read=True)
            )
        else:
            service = await session.get(Service, service_id)
        if service is None or (active and not service.is_active):
            raise NotFound("error_service_unavailable")
        return service

    @staticmethod
    async def _business(session: AsyncSession) -> BusinessSettings:
        business = await session.get(BusinessSettings, 1)
        if business is None:
            raise NotFound("error_setup_incomplete")
        return business

    async def ensure_user(
        self, telegram_id: int, full_name: str, username: str | None = None
    ) -> User:
        if telegram_id <= 0:
            raise ValidationError("error_invalid_telegram_user")
        async with self.sessions.begin() as session:
            statement = insert(User).values(
                telegram_id=telegram_id, full_name=full_name[:255], username=username
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[User.telegram_id],
                    set_={
                        "full_name": statement.excluded.full_name,
                        "username": statement.excluded.username,
                    },
                )
            )
            return await self._user(session, telegram_id)

    async def get_user_language(self, telegram_id: int) -> str | None:
        """Read preferences without creating users or overwriting their explicit choice."""
        async with self.sessions() as session:
            return await session.scalar(
                select(User.language_code).where(User.telegram_id == telegram_id)
            )

    async def set_user_language(self, telegram_id: int, language_code: str) -> User:
        if not isinstance(language_code, str) or language_code not in SUPPORTED_LANGUAGES:
            raise ValidationError("error_invalid_language")
        async with self.sessions.begin() as session:
            user = await self._user(session, telegram_id, lock=True)
            user.language_code = language_code
            await session.flush()
            return user

    async def business_settings(self) -> BusinessSettings:
        async with self.sessions() as session:
            return await self._business(session)

    async def list_services(self) -> list[Service]:
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(Service).where(Service.is_active.is_(True)).order_by(Service.id)
                )
            )

    async def list_services_admin(self, admin_telegram_id: int) -> list[Service]:
        self._require_admin(admin_telegram_id)
        async with self.sessions() as session:
            return list(await session.scalars(select(Service).order_by(Service.id)))

    async def create_service(
        self,
        admin_telegram_id: int,
        name: str,
        description: str,
        duration_minutes: int,
    ) -> Service:
        self._require_admin(admin_telegram_id)
        service = Service(
            name=validate_service_name(name),
            description=validate_service_description(description),
            duration_minutes=validate_service_duration(duration_minutes),
            is_active=True,
        )
        async with self.sessions.begin() as session:
            session.add(service)
            await session.flush()
            return service

    async def update_service(
        self,
        admin_telegram_id: int,
        service_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        duration_minutes: int | None = None,
    ) -> Service:
        self._require_admin(admin_telegram_id)
        if name is None and description is None and duration_minutes is None:
            raise ValidationError("error_service_update_empty")
        async with self.sessions.begin() as session:
            service = await session.scalar(
                select(Service).where(Service.id == service_id).with_for_update()
            )
            if service is None:
                raise NotFound("error_service_not_found")
            if name is not None:
                service.name = validate_service_name(name)
            if description is not None:
                service.description = validate_service_description(description)
            if duration_minutes is not None:
                service.duration_minutes = validate_service_duration(duration_minutes)
            await session.flush()
            return service

    async def set_service_active(
        self,
        admin_telegram_id: int,
        service_id: int,
        is_active: bool,
    ) -> Service:
        self._require_admin(admin_telegram_id)
        if type(is_active) is not bool:
            raise ValidationError("error_service_active_choice")
        async with self.sessions.begin() as session:
            service = await session.scalar(
                select(Service).where(Service.id == service_id).with_for_update()
            )
            if service is None:
                raise NotFound("error_service_not_found")
            service.is_active = is_active
            await session.flush()
            return service

    async def update_support_contact(
        self,
        admin_telegram_id: int,
        support_contact: str | None,
    ) -> BusinessSettings:
        self._require_admin(admin_telegram_id)
        canonical = validate_support_contact(support_contact)
        async with self.sessions.begin() as session:
            business = await self._business(session)
            business.support_contact = canonical
            await session.flush()
            return business

    async def get_service(self, service_id: int) -> Service:
        async with self.sessions() as session:
            return await self._service(session, service_id)

    async def _record_event(
        self,
        session: AsyncSession,
        record: LeadRequest | Appointment,
        actor: int,
        old_status: str | None,
        text: Callable[[str], str],
        *,
        owner: int | None = None,
        owner_text: Callable[[str], str] | None = None,
        admin_targets: set[int] | None = None,
        reply_markup: Callable[[str], dict[str, object]] | None = None,
    ) -> None:
        is_request = isinstance(record, LeadRequest)
        history = StatusHistory(
            request_id=record.id if is_request else None,
            appointment_id=None if is_request else record.id,
            old_status=old_status,
            new_status=record.status,
            actor_telegram_id=actor,
        )
        session.add(history)
        await session.flush()
        recipients = set(
            self.settings.allowed_admin_ids if admin_targets is None else admin_targets
        )
        if owner is not None:
            recipients.add(owner)
        preferences = dict(
            (
                await session.execute(
                    select(User.telegram_id, User.language_code).where(
                        User.telegram_id.in_(recipients)
                    )
                )
            ).all()
        )
        for recipient in recipients:
            language = (
                self.settings.admin_notification_language
                if recipient == self.settings.admin_notification_chat_id
                else normalize_language(preferences.get(recipient))
            )
            render = owner_text if recipient == owner and owner_text is not None else text
            session.add(
                NotificationOutbox(
                    telegram_id=recipient,
                    text=render(language),
                    reply_markup=reply_markup(language) if reply_markup is not None else None,
                    dedup_key=f"history:{history.id}:telegram:{recipient}",
                )
            )

    @staticmethod
    def _check_key(submission_key: UUID) -> None:
        if not isinstance(submission_key, UUID):
            raise ValidationError("error_invalid_confirmation")

    @staticmethod
    def _check_replay(record: LeadRequest | Appointment, **values: object) -> None:
        if any(getattr(record, key) != value for key, value in values.items()):
            raise ValidationError("error_confirmation_used")

    async def create_request(
        self,
        telegram_id: int,
        service_id: int,
        customer_name: str,
        contact: str,
        details: str,
        submission_key: UUID,
    ) -> LeadRequest:
        customer_name = validate_name(customer_name)
        details = validate_details(details)
        self._check_key(submission_key)
        async with self.sessions.begin() as session:
            # Serialize this user's confirmations. Unique constraint remains a DB backstop.
            user = await self._user(session, telegram_id, lock=True)
            existing = await session.scalar(
                select(LeadRequest).where(
                    LeadRequest.user_id == user.id,
                    LeadRequest.submission_key == submission_key,
                )
            )
            values = dict(
                service_id=service_id,
                customer_name=customer_name,
                contact=contact.strip() if isinstance(contact, str) else contact,
                details=details,
            )
            if existing is not None:
                self._check_replay(existing, **values)
                return existing
            contact = validate_phone(contact)
            values["contact"] = contact
            service = await self._service(session, service_id, active=True, lock=True)
            request = LeadRequest(
                user_id=user.id,
                submission_key=submission_key,
                service_name=service.name,
                **values,
            )
            session.add(request)
            await session.flush()
            await self._record_event(
                session,
                request,
                telegram_id,
                None,
                lambda language: format_new_request_notification(
                    request.number,
                    service.name,
                    customer_name,
                    contact,
                    details,
                    language=language,
                ),
                admin_targets=self._new_submission_admin_targets(),
                reply_markup=lambda language: {
                    "inline_keyboard": [
                        [
                            {
                                "text": tr("notification_view_request", language=language),
                                "callback_data": f"admin:req:item:{request.id}:0",
                            }
                        ],
                        [
                            {
                                "text": tr("notification_start_processing", language=language),
                                "callback_data": f"admin:req:set:{request.id}:in_progress:0",
                            }
                        ],
                        [
                            {
                                "text": tr("notification_mark_completed", language=language),
                                "callback_data": f"admin:req:set:{request.id}:completed:0",
                            }
                        ],
                    ]
                },
            )
            return request

    async def _occupied(self, session: AsyncSession, *, now: datetime) -> list[tuple]:
        return list(
            (
                await session.execute(
                    select(Appointment.starts_at, Appointment.ends_at).where(
                        Appointment.status == "booked", Appointment.ends_at > now
                    )
                )
            ).all()
        )

    async def available_dates(self, service_id: int) -> list[date]:
        async with self.sessions() as session:
            service = await self._service(session, service_id, active=True)
            business = await self._business(session)
            now = datetime.now(UTC)
            occupied = await self._occupied(session, now=now)
            today = now.astimezone(ZoneInfo(business.timezone)).date()
            return [
                day
                for offset in range(business.booking_horizon_days)
                if slots_for_day(
                    business,
                    service.duration_minutes,
                    day := today + timedelta(days=offset),
                    occupied,
                    now=now,
                )
            ]

    async def available_slots(self, service_id: int, day: date) -> list[datetime]:
        async with self.sessions() as session:
            service = await self._service(session, service_id, active=True)
            business = await self._business(session)
            now = datetime.now(UTC)
            occupied = await self._occupied(session, now=now)
            return slots_for_day(business, service.duration_minutes, day, occupied, now=now)

    async def create_appointment(
        self,
        telegram_id: int,
        service_id: int,
        starts_at: datetime,
        customer_name: str,
        contact: str,
        submission_key: UUID,
    ) -> Appointment:
        customer_name = validate_name(customer_name)
        self._check_key(submission_key)
        if not isinstance(starts_at, datetime) or starts_at.tzinfo is None:
            raise ValidationError("error_choose_zoned_time")
        if starts_at.utcoffset() is None:
            raise ValidationError("error_choose_zoned_time")
        starts_at = starts_at.astimezone(UTC)
        try:
            async with self.sessions.begin() as session:
                user = await self._user(session, telegram_id, lock=True)
                existing = await session.scalar(
                    select(Appointment).where(
                        Appointment.user_id == user.id,
                        Appointment.submission_key == submission_key,
                    )
                )
                values = dict(
                    service_id=service_id,
                    starts_at=starts_at,
                    customer_name=customer_name,
                    contact=contact.strip() if isinstance(contact, str) else contact,
                )
                if existing is not None:
                    self._check_replay(existing, **values)
                    return existing
                contact = validate_phone(contact)
                values["contact"] = contact
                service = await self._service(session, service_id, active=True, lock=True)
                business = await self._business(session)
                now = datetime.now(UTC)
                day = starts_at.astimezone(ZoneInfo(business.timezone)).date()
                slots = slots_for_day(
                    business,
                    service.duration_minutes,
                    day,
                    await self._occupied(session, now=now),
                    now=now,
                )
                if starts_at not in slots:
                    raise SlotUnavailable("error_slot_unavailable")
                appointment = Appointment(
                    user_id=user.id,
                    submission_key=submission_key,
                    service_name=service.name,
                    ends_at=starts_at + timedelta(minutes=service.duration_minutes),
                    **values,
                )
                session.add(appointment)
                await session.flush()  # PostgreSQL excludes overlap, including other services.
                await self._record_event(
                    session,
                    appointment,
                    telegram_id,
                    None,
                    lambda language: tr(
                        "notification_admin_booking",
                        language=language,
                        booking_id=appointment.number,
                        service_name=service.name,
                        appointment_date=format_business_date(
                            starts_at, business.timezone, language=language
                        ),
                        appointment_time=format_business_time(
                            starts_at, business.timezone, language=language
                        ),
                        customer_name=customer_name,
                        contact=contact,
                        status=status_label("booked", language=language),
                    ),
                    admin_targets=self._new_submission_admin_targets(),
                    reply_markup=lambda language: {
                        "inline_keyboard": [
                            [
                                {
                                    "text": tr("notification_view_appointment", language=language),
                                    "callback_data": f"admin:appt:item:{appointment.id}:0",
                                }
                            ]
                        ]
                    },
                )
                return appointment
        except IntegrityError as exc:
            if "appointments_no_overlap" in str(exc.orig):
                raise SlotUnavailable("error_slot_just_booked") from exc
            raise

    async def _get_record(
        self,
        session: AsyncSession,
        model: type[LeadRequest] | type[Appointment],
        telegram_id: int,
        record_id: int,
        *,
        admin: bool,
        lock: bool = False,
    ) -> LeadRequest | Appointment:
        if admin:
            self._require_admin(telegram_id)
        query = select(model).where(model.id == record_id)
        if not admin:
            query = query.join(User, User.id == model.user_id).where(
                User.telegram_id == telegram_id
            )
        if lock:
            query = query.with_for_update(of=model)
        record = await session.scalar(query)
        if record is None:
            raise NotFound("error_item_not_found")
        return record

    async def _list_records(
        self,
        model: type[LeadRequest] | type[Appointment],
        telegram_id: int,
        *,
        offset: int,
        limit: int,
        admin: bool,
        status: str | None = None,
        upcoming: bool = False,
    ) -> list:
        if admin:
            self._require_admin(telegram_id)
        if offset < 0 or not 1 <= limit <= 50:
            raise ValidationError("error_invalid_page")
        query = select(model)
        if status is not None:
            if model is not LeadRequest or status not in REQUEST_TRANSITIONS:
                raise ValidationError("error_invalid_status_filter")
            query = query.where(model.status == status)
        if upcoming:
            if model is not Appointment:
                raise ValidationError("error_upcoming_filter")
            query = query.where(
                Appointment.status == "booked", Appointment.starts_at > datetime.now(UTC)
            ).order_by(Appointment.starts_at, Appointment.id)
        else:
            query = query.order_by(model.id.desc())
        query = query.offset(offset).limit(limit)
        if not admin:
            query = query.join(User, User.id == model.user_id).where(
                User.telegram_id == telegram_id
            )
        async with self.sessions() as session:
            return list(await session.scalars(query))

    async def list_requests(
        self,
        telegram_id: int,
        *,
        offset: int = 0,
        limit: int = 5,
        admin: bool = False,
        status: str | None = None,
    ) -> list[LeadRequest]:
        return await self._list_records(
            LeadRequest, telegram_id, offset=offset, limit=limit, admin=admin, status=status
        )

    async def list_appointments(
        self,
        telegram_id: int,
        *,
        offset: int = 0,
        limit: int = 5,
        admin: bool = False,
        upcoming: bool = False,
    ) -> list[Appointment]:
        return await self._list_records(
            Appointment, telegram_id, offset=offset, limit=limit, admin=admin, upcoming=upcoming
        )

    async def get_request(
        self,
        telegram_id: int,
        request_id: int,
        *,
        admin: bool = False,
    ) -> LeadRequest:
        async with self.sessions() as session:
            return await self._get_record(
                session, LeadRequest, telegram_id, request_id, admin=admin
            )

    async def get_appointment(
        self,
        telegram_id: int,
        appointment_id: int,
        *,
        admin: bool = False,
    ) -> Appointment:
        async with self.sessions() as session:
            return await self._get_record(
                session, Appointment, telegram_id, appointment_id, admin=admin
            )

    async def change_request_status(
        self,
        admin_telegram_id: int,
        request_id: int,
        new_status: str,
    ) -> LeadRequest:
        self._require_admin(admin_telegram_id)
        async with self.sessions.begin() as session:
            request = await self._get_record(
                session, LeadRequest, admin_telegram_id, request_id, admin=True, lock=True
            )
            if request.status == new_status:
                return request
            if new_status not in REQUEST_TRANSITIONS[request.status]:
                raise InvalidTransition("error_request_transition")
            old_status = request.status
            request.status = new_status
            owner = await session.get(User, request.user_id)
            await self._record_event(
                session,
                request,
                admin_telegram_id,
                old_status,
                lambda language: tr(
                    "notification_admin_request_status",
                    language=language,
                    request_id=request.number,
                    status=status_label(new_status, language=language),
                ),
                owner=owner.telegram_id,
                owner_text=lambda language: format_request_status_notification(
                    request.number, new_status, language=language
                ),
            )
            return request

    async def cancel_appointment(
        self,
        telegram_id: int,
        appointment_id: int,
        *,
        admin: bool = False,
    ) -> Appointment:
        if admin:
            self._require_admin(telegram_id)
        return await self._change_appointment(telegram_id, appointment_id, "cancelled", admin=admin)

    async def change_appointment_status(
        self,
        admin_telegram_id: int,
        appointment_id: int,
        new_status: str,
    ) -> Appointment:
        self._require_admin(admin_telegram_id)
        return await self._change_appointment(
            admin_telegram_id,
            appointment_id,
            new_status,
            admin=True,
        )

    async def _change_appointment(
        self,
        actor: int,
        appointment_id: int,
        new_status: str,
        *,
        admin: bool,
    ) -> Appointment:
        async with self.sessions.begin() as session:
            appointment = await self._get_record(
                session, Appointment, actor, appointment_id, admin=admin, lock=True
            )
            if appointment.status == new_status:
                return appointment
            if appointment.status != "booked" or new_status not in {"cancelled", "completed"}:
                raise InvalidTransition("error_booking_transition")
            now = datetime.now(UTC)
            if not admin and (new_status != "cancelled" or appointment.starts_at <= now):
                raise InvalidTransition("error_upcoming_cancel_only")
            if new_status == "completed" and appointment.ends_at > now:
                raise InvalidTransition("error_booking_not_ended")
            old_status = appointment.status
            appointment.status = new_status
            owner_telegram_id = None
            owner_text = None
            admin_targets = None
            if admin:
                owner = await session.get(User, appointment.user_id)
                owner_telegram_id = owner.telegram_id

                def owner_text(language: str) -> str:
                    if new_status == "cancelled":
                        return format_booking_cancelled(appointment.number, language=language)
                    return tr(
                        "notification_customer_booking_status",
                        language=language,
                        booking_id=appointment.number,
                        status=status_label(new_status, language=language),
                    )
            else:
                # The client handler sends the final cancellation receipt after this commit.
                admin_targets = set(self.settings.allowed_admin_ids) - {actor}
            await self._record_event(
                session,
                appointment,
                actor,
                old_status,
                lambda language: tr(
                    "notification_admin_booking_status",
                    language=language,
                    booking_id=appointment.number,
                    status=status_label(new_status, language=language),
                ),
                owner=owner_telegram_id,
                owner_text=owner_text,
                admin_targets=admin_targets,
            )
            return appointment

    async def statistics(self, admin_telegram_id: int) -> dict[str, int]:
        self._require_admin(admin_telegram_id)
        async with self.sessions() as session:
            request_row = (
                await session.execute(
                    select(
                        func.count(LeadRequest.id),
                        *(
                            func.count(LeadRequest.id).filter(LeadRequest.status == status)
                            for status in ("new", "in_progress", "completed", "cancelled")
                        ),
                    )
                )
            ).one()
            appointment_row = (
                await session.execute(
                    select(
                        func.count(Appointment.id),
                        func.count(Appointment.id).filter(
                            Appointment.status == "booked",
                            Appointment.starts_at > datetime.now(UTC),
                        ),
                        func.count(Appointment.id).filter(Appointment.status == "completed"),
                        func.count(Appointment.id).filter(Appointment.status == "cancelled"),
                    )
                )
            ).one()
            keys = (
                "requests_total",
                "requests_new",
                "requests_in_progress",
                "requests_completed",
                "requests_cancelled",
                "appointments_total",
                "appointments_upcoming",
                "appointments_completed",
                "appointments_cancelled",
            )
            return dict(zip(keys, (*request_row, *appointment_row), strict=True))
