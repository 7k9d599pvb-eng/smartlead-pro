# SmartLead Pro

[![CI](https://github.com/7k9d599pvb-eng/smartlead-pro/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/7k9d599pvb-eng/smartlead-pro/actions/workflows/ci.yml)

## Project Overview

SmartLead Pro is a Telegram bot for small service businesses. Customers submit
requests, book appointments, and follow their progress. Administrators manage
requests, bookings, services, and the support contact inside Telegram.

This repository is a portfolio project. It demonstrates a working single-business
workflow without a web dashboard, payments, or an external CRM.

The [60–90 second walkthrough](docs/demo.md) follows a customer request, an
administrator's status update, a booking, and the language picker.

## Features

- Guided request and booking forms with validation, review, Back, and Cancel.
- Request and booking references such as `SL-R-000001` and `SL-A-000001`.
- Customer views restricted to their own requests and upcoming appointments.
- Booking availability based on business hours, notice, and a shared calendar.
  PostgreSQL prevents overlapping active appointments, including concurrent
  bookings across different services.
- Customer and administrator appointment cancellation, with the slot released
  for another booking.
- An administrator panel with request filters, pagination, status changes,
  appointment management, service editing, support settings, and statistics.
- Optional group alerts for new requests and bookings. Every administrative
  callback checks the Telegram ID allowlist on the server.
- Transactional status history and a PostgreSQL notification outbox with retries.
- Six interface languages, saved separately for each user.

## Supported Languages

| Language | Code |
| --- | --- |
| English | `en` |
| Français | `fr` |
| Русский | `ru` |
| Українська | `uk` |
| Deutsch | `de` |
| 日本語 | `ja` |

New users choose a language on their first `/start`. PostgreSQL stores the
preference for subsequent visits. The globe button in the main menu and the
`/language` command open the picker again. A language change closes an unfinished
form but leaves confirmed requests and appointments intact. Profiles created
before the language migration default to English.

Menus, forms, validation messages, receipts, statuses, support screens, the admin
panel, and notification buttons are localized. Babel formats calendar dates.
Times use a 24-hour clock in the business timezone, with its UTC offset. Changing
language does not change a booked time. Service names, service descriptions, and
customer input remain as entered.

Customer notifications and private administrator alerts use the recipient's
saved language when the event is committed. Shared group alerts use
`ADMIN_NOTIFICATION_LANGUAGE`. The outbox keeps the rendered text and keyboard
for retries; changing language does not rewrite or resend earlier notifications.

Translations are stored in `src/smartlead/locales/<language>/messages.json`.
Missing translations fall back to English. Tests check matching keys and named
placeholders in all six catalogs. See the [localization guide](docs/localization.md).

## Tech Stack

Python 3.12+, aiogram 3, PostgreSQL 17, SQLAlchemy 2, Alembic, Babel, Docker Compose,
pytest, and Ruff. Runtime and development dependencies are pinned in
`requirements.lock` and `requirements-dev.lock`. CI uses Python 3.12 and
PostgreSQL 17.

The Python package lives in `src/smartlead/`. Telegram handlers are in `bot/`,
business rules in `services/`, persistence in `db/`, and notification delivery in
`workers/`. Schema changes are in `alembic/versions/`; automated tests are in
`tests/`. The Compose configuration is named `compose.yaml`.

## Installation

Clone the repository:

```bash
git clone https://github.com/7k9d599pvb-eng/smartlead-pro.git
cd smartlead-pro
```

### Docker

Install Docker Engine with Docker Compose 2.20 or later, or Docker Desktop on
Windows or macOS. This route installs Python dependencies inside the image;
a local Python installation is not needed.

### Local Python

Install Python 3.12 or later and PostgreSQL 17. Check `python3 --version` on Linux
or macOS, or `python --version` on Windows, before creating the environment.
The interpreter must report Python 3.12 or later.

Linux and macOS, using Bash or Zsh:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` in place of
`python` in the commands below. Activation is a convenience, not a requirement.

Install the development dependencies and the package:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
```

## Configuration

Create a private `.env` file from the template.

Linux and macOS:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Edit it locally. Do not commit real credentials or paste them into chat.

| Setting | Purpose |
| --- | --- |
| `BOT_TOKEN` | Token issued by [@BotFather](https://t.me/BotFather); required to start the bot. |
| `ADMIN_IDS` | Comma-separated numeric Telegram IDs allowed to use `/admin`. An empty list disables administrator access. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Database credentials used by the Compose PostgreSQL service. Set a strong password. |
| `DATABASE_URL` | Async SQLAlchemy URL for the same database, user, and password. Use host `db` in Compose or `127.0.0.1` for local PostgreSQL. |
| `ADMIN_NOTIFICATION_CHAT_ID` | Optional negative group ID for new request and booking alerts. |
| `ADMIN_NOTIFICATION_LANGUAGE` | Language for shared group alerts; defaults to `en`. |
| `BUSINESS_TIMEZONE` | IANA timezone such as `Europe/London` or `Europe/Moscow`. |
| `BUSINESS_SUPPORT_CONTACT` | Optional Telegram username or `t.me` profile link. |

Keep the database name, user, and password in `DATABASE_URL` consistent with the
database you create. Use a URL-safe password or percent-encode reserved characters
in the URL. `.env.example` contains placeholders and the remaining settings.

### Telegram setup

Create the bot with BotFather and enter its token in `.env`. Start a private chat
with the bot and use `/id` to find your numeric ID. Add administrator IDs to
`ADMIN_IDS`, then use `/admin`. Each administrator must send `/start` before the
bot can send them private notifications.

For group alerts, add the bot to your group, allow it to send messages, and set
`ADMIN_NOTIFICATION_CHAT_ID`. New requests and bookings then go to that group
instead of administrators' private chats. Group buttons accept only allowlisted
administrators and only in the configured group.

The support contact can also be set or cleared under `/admin` → **Services** →
**Support Contact**. If no contact is configured, the customer screen says so.

### Local PostgreSQL

For a new local installation, connect with a PostgreSQL administrator account:

```bash
psql -h 127.0.0.1 -U postgres -d postgres
```

Create a dedicated role and database in the interactive `psql` session. The
`\password` command prompts for a password rather than putting it in SQL history:

```sql
CREATE ROLE smartlead LOGIN;
\password smartlead
CREATE DATABASE smartlead OWNER smartlead;
```

Skip creation if you already have a dedicated database. Set `DATABASE_URL` to
`postgresql+asyncpg://smartlead:URL_ENCODED_PASSWORD@127.0.0.1:5432/smartlead`,
replacing the password placeholder locally. The booking constraint uses
PostgreSQL's built-in GiST support for timestamp ranges; no extra extension is
required.

## Running the Bot

### Docker Compose

From the repository root, after configuring `.env`:

```bash
docker compose config --quiet
docker compose up --build -d
docker compose logs -f bot
```

Compose waits for PostgreSQL, applies Alembic migrations, seeds missing defaults,
and starts the bot. The migration and seed services exit after completing their
work. Stop the stack with `docker compose down`. Data is kept in the named
PostgreSQL volume; do not remove that volume when you want to keep existing data.

### Local Python

Apply the schema before starting the application:

```bash
python -m alembic upgrade head
python -m smartlead check
python -m smartlead seed
python -m smartlead
```

Application startup checks the schema but does not apply migrations.
`python -m smartlead check` verifies the database connection, migration version,
and booking constraint without contacting Telegram. The installed `smartlead`
command is equivalent to `python -m smartlead`.

Normal seeding creates sample services and fills missing business settings while
preserving administrator edits. Use `python -m smartlead seed --sync-settings`
only when you intend to copy business settings from the environment again.

### Scheduling and persistence

Set hours, weekdays, notice, and booking horizon in `.env` before initial seeding.
Defaults are Monday–Friday, 09:00–18:00, 30-minute slots, 60 minutes' notice, and a
14-day horizon. Weekdays use Monday `0` through Sunday `6`. Opening and closing
must be on the same day; overnight hours are not supported.

Appointments are stored as UTC instants and displayed in the business timezone.
Repeated clock times during daylight-saving transitions remain distinct; times
in a clock gap cannot be booked. Cancelling an active upcoming appointment
releases its slot across all services.

Migrations preserve existing records. Requests and appointments store service-name
snapshots, so later service edits do not change their displayed service names.
Deactivating a service keeps its existing records.

Run one polling process per token. Unfinished forms use in-memory FSM storage and
are cleared on restart. Confirmed records, history, and queued notifications stay
in PostgreSQL. Outbox delivery is at least once: a crash after Telegram accepts a
message but before delivery is recorded can cause a retry to send it again.

## Testing

Install the development dependencies, then run:

```bash
python -m ruff check src alembic tests
python -m ruff format --check src alembic tests
python -m pip check
python -m pytest -q -p no:cacheprovider
```

Without `TEST_DATABASE_URL`, pytest runs the non-database tests and skips the
PostgreSQL integration tests. Tests use synthetic Telegram updates; no live
Telegram access or real bot token is needed.

### Full suite with local PostgreSQL

Create a separate database and login for testing, for example `smartlead_test`,
using the same interactive procedure as above. Integration tests require an
async PostgreSQL URL with `test` in the database name. They apply real migrations
and clear application tables before each test. Use a disposable test database.

Linux and macOS:

```bash
export TEST_DATABASE_URL='postgresql+asyncpg://smartlead_test:URL_ENCODED_PASSWORD@127.0.0.1:5432/smartlead_test'
python -m pytest -q -p no:cacheprovider
```

Windows PowerShell:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+asyncpg://smartlead_test:URL_ENCODED_PASSWORD@127.0.0.1:5432/smartlead_test'
python -m pytest -q -p no:cacheprovider
```

Replace the password placeholder with the test database password. Local pytest
reads `TEST_DATABASE_URL` from the process environment, not from `.env`.

### Full suite with Docker Compose

Prepare `.env` and set `POSTGRES_PASSWORD` first; Compose requires it even for the
test profile. The profile uses a separate temporary PostgreSQL database. For a
URL-safe password, Compose builds the test URL automatically. Otherwise set
`TEST_DATABASE_URL` in `.env` using the `db_test` host and the percent-encoded
value of `POSTGRES_PASSWORD`:

```dotenv
TEST_DATABASE_URL=postgresql+asyncpg://smartlead_test:URL_ENCODED_PASSWORD@db_test:5432/smartlead_test
```

```bash
docker compose --profile test run --build --rm test
docker compose --profile test stop db_test
```

Coverage includes validation, Back and Cancel, duplicate submissions, ownership
and administrator access, concurrent booking conflicts, cancellation, migration
preservation, status history, and notification retries. Localization tests cover
all six languages, preference persistence, dates, fallback, template variables,
and recipient-specific notifications.

The [demo guide](docs/demo.md) describes a separate human-operated Telegram
walkthrough. Automated tests do not replace that check of the live interface.

## Screenshots

These owner-provided captures show recorded demo sessions. They are
cropped to the bot messages, resized, and stripped of image metadata. Personal
names, phone numbers, private chat identifiers, and unrelated messages are excluded.
Request references, booking dates, and statuses are shown as captured.

**Language selection**

![Language picker offering English, French, Russian, Ukrainian, German and Japanese](docs/screenshots/language-menu.jpg)

**Request confirmation and status update**

![Request submitted with its reference, followed by an In Progress notification](docs/screenshots/request-status.jpg)

**Appointment confirmation**

![Confirmed appointment with its reference, service, date and business-local time](docs/screenshots/appointment-confirmed.jpg)

**Administrator panel**

![Telegram administrator menu for requests, appointments, services and statistics](docs/screenshots/admin-panel.jpg)

The request, booking, and administrator screens use English. The language picker
retains its Russian prompt and native language names. See
[Supported Languages](#supported-languages) for how preferences are stored.

## Project Status

Version 1.2 includes requests, booking, administration, notifications, and the
six-language interface. The project is intended for portfolio demonstrations and
adaptation to a single business. It does not include employee calendars, payments,
a web admin panel, AI chat, or multiple tenants.

GitHub Actions runs Python checks and a Docker build with PostgreSQL integration
tests. Review the [workflow results](https://github.com/7k9d599pvb-eng/smartlead-pro/actions)
for the commit you plan to deploy. Configure your own credentials, business hours,
timezone, and support contact, then complete the Telegram walkthrough before
using it for a business.

- [Deployment runbook](docs/deployment.md)
- [Human-operated Telegram demo](docs/demo.md)
- [v1.1 readiness audit](docs/audit-v1.1.md)
- [v1.2 localization changes and validation](docs/changes-v1.2.0.md)
- [Portfolio notes](PORTFOLIO.md)
