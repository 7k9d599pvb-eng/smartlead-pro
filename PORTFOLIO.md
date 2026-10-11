# SmartLead Pro

SmartLead Pro is a Telegram intake and appointment tool for a small service
business. Customers can send a request, follow its status, book an available
appointment, and cancel an upcoming booking. Allowlisted administrators manage
the service catalog and request status from Telegram. New request and booking alerts can be
routed to a configured administrator group.

## What the project demonstrates

- Python 3.12, aiogram 3, SQLAlchemy 2, PostgreSQL 17, and Alembic migrations.
- A focused Telegram workflow with inline menus and input validation.
- Six-language customer and administrator interfaces, persisted preferences,
  localized dates, and notifications rendered for each recipient.
- Database-enforced protection against overlapping appointments on one shared
  calendar.
- Durable status history and an outbox for retryable notifications.
- Record ownership checks, administrator allowlisting, and migration-safe
  snapshots of service names and booked time intervals.
- Docker Compose setup for local operation and isolated database testing.

## Product scope

The application models one business and one shared calendar. It does not include
employee schedules, payments, a web dashboard, analytics claims, or AI features.
Telegram messages and screenshots in a portfolio walkthrough should come from a
human-operated demo deployment. Clearly label synthetic demo records. Do not
claim paying customers, testimonials, commercial results, or use real personal
data in the demonstration.

## Demo

The human-operated walkthrough is documented in [docs/demo.md](docs/demo.md).
It uses Telegram accounts controlled by the operator and an isolated demo
database. Automated coverage and its limits are recorded in
[docs/audit-v1.1.md](docs/audit-v1.1.md).
