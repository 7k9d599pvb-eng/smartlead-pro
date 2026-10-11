# SmartLead Pro

SmartLead Pro is a Telegram bot for service businesses. Customers can submit a
service request, book an available appointment, check request status, and manage
upcoming appointments. Allowlisted administrators manage requests, appointments,
services, and business settings in Telegram.

## Features

- English, French, Russian, Ukrainian, German, and Japanese interfaces, with a
  saved language preference for each customer and administrator.
- Guided request and booking forms with input checks, review, Back, and Cancel.
- Unique request and booking references, such as `SL-R-000001` and `SL-A-000001`.
- Availability based on business hours and one shared calendar. PostgreSQL
  prevents overlapping bookings across services.
- Customer views for their own requests and upcoming appointments, with status
  updates and appointment cancellation.
- An administrator panel for request filters and status changes, appointments,
  service creation and editing, activation, support contact, and statistics.
- Optional group alerts for new requests and bookings, with buttons to open the
  record. Every administrator action checks the allowlist.
- Transactional status history and a PostgreSQL notification outbox with retry
  handling for transient delivery failures.
- Migrations that preserve existing requests, appointments, service-name
  snapshots, and queued notifications.

SmartLead supports one business and one shared calendar. Employee schedules,
payments, a web dashboard, and AI features are outside its scope.

## Stack

Python 3.12 or later, aiogram 3, PostgreSQL 17, SQLAlchemy 2, Alembic, Babel, Docker
Compose, pytest, and Ruff. Runtime and development dependencies are pinned in
`requirements.lock` and `requirements-dev.lock`.

## Start with Docker Compose

Install Docker Engine and Docker Compose v2.20 or later. Copy the example
configuration and edit it locally:

```sh
git clone https://github.com/7k9d599pvb-eng/smartlead-pro.git
cd smartlead-pro
cp .env.example .env
```

Set `BOT_TOKEN`, `ADMIN_IDS`, and a strong `POSTGRES_PASSWORD`. The password in
`DATABASE_URL` must match. Use a URL-safe password, or percent-encode reserved
characters in the database URL. Keep `.env` private: do not commit it, share it,
or paste its contents into chat.

Start PostgreSQL, apply migrations, seed the sample services, and start the bot:

```sh
docker compose up --build -d
docker compose logs -f bot
```

Compose waits for PostgreSQL to become healthy, then runs the migration and seed
services before starting the bot. To stop the stack, run `docker compose down`.
The named PostgreSQL volume keeps data between container restarts.

## Run with Python

Use Python 3.12 or later and a PostgreSQL database reachable from your machine.
Set `DATABASE_URL` in `.env` to a `postgresql+asyncpg://` URL for that database.
For a local database, use `127.0.0.1` as the host instead of Compose's `db`.

```sh
python -m venv .venv
```

Activate the virtual environment. Use the command for your shell:

```sh
# macOS or Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Then install the development dependencies and the package:

```sh
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
```

Copy `.env.example` to `.env` and set the required values. Apply the schema and
seed the database before starting the bot; application startup does not run
migrations automatically:

```sh
alembic upgrade head
python -m smartlead check
python -m smartlead seed
python -m smartlead
```

`python -m smartlead check` verifies the database connection, migration version,
and appointment constraint without contacting Telegram. Normal seeding creates
sample services and fills missing business settings while preserving edits made
by an administrator. Use `python -m smartlead seed --sync-settings` only when you
want environment values for the business name, support contact, hours, and
timezone copied to the database again. The installed `smartlead` command can be
used in place of `python -m smartlead`.

## Telegram and administrator setup

Create a bot with [@BotFather](https://t.me/BotFather) and set its token as
`BOT_TOKEN`. Add the numeric Telegram IDs of administrators to `ADMIN_IDS`, as a
comma-separated list. Use `/id` in a private chat with the bot to find your ID.
Administrators open the admin panel with `/admin`. Each administrator must send
the bot `/start` once before it can send them a private message.

To send new request and booking alerts to a group, set
`ADMIN_NOTIFICATION_CHAT_ID` to that group's negative numeric Telegram ID. Add
the bot to the group and allow it to send messages. The notification buttons
still require an allowlisted administrator, and group callbacks are accepted
only in the configured group. Without this setting, new alerts go to the
allowlisted administrators' private chats.

Set `ADMIN_NOTIFICATION_LANGUAGE` to `en`, `fr`, `ru`, `uk`, `de`, or `ja` for
shared group alerts. It defaults to `en`. A group receives one alert for an
action; private administrator alerts use each administrator's saved language.

`BUSINESS_SUPPORT_CONTACT` is optional. Set it to a Telegram username or `t.me`
profile link. Administrators can also set or clear the customer support contact
under `/admin` → **Services** → **Support Contact**.

See `.env.example` for all supported configuration options, including business
hours, timezone, booking notice, and booking horizon.

## Languages

New users choose a language on their first `/start`. The choice is saved in
PostgreSQL and used on subsequent visits. Use the globe button in the main menu
or `/language` to change it. Switching language closes an unfinished form;
confirmed requests and appointments stay intact. Users already registered before
the language migration keep English until they choose another language.

Menus, form prompts, validation errors, receipts, status labels, support screens,
the admin panel, and notification buttons use the selected language. Babel
formats calendar dates; booking times remain in the business timezone with a
24-hour clock and UTC offset. Changing language does not change a booked time.
Service names, descriptions, and customer-entered information remain as entered.

Notifications use the recipient's saved language when the status event is
committed. The existing outbox stores that text and keyboard for retries. A
language change affects future events and does not resend or rewrite old notices.

Translations live in `src/smartlead/locales/<language>/messages.json`. Missing
translations fall back to English. Automated checks require all six shipped
catalogs to contain the same keys and named placeholders. See
[the localization guide](docs/localization.md) for maintenance details.

## PostgreSQL and booking behavior

The database stores users, services, business settings, requests, appointments,
status history, and pending notifications. A booked appointment blocks its time
across every service. Cancelling an upcoming booking releases the slot. Services
are deactivated instead of deleted, and changes to a service do not rewrite
existing request or appointment snapshots.

Set business hours with `BUSINESS_OPEN`, `BUSINESS_CLOSE`,
`BUSINESS_TIMEZONE`, and `BUSINESS_WEEKDAYS`. Timezones use IANA names such as
`Europe/London` or `Europe/Moscow`; weekdays use Monday `0` through Sunday `6`.
Opening and closing times must be on the same day. Overnight hours are not
supported. The defaults are weekdays from 09:00 to 18:00, 30-minute slots, a
60-minute booking notice, and a 14-day booking horizon.

Appointments are stored as UTC instants and shown in the business timezone with
the UTC offset. The two occurrences of a repeated clock time during a fall-back
transition remain distinct; times in a spring-forward clock gap cannot be
booked.

Run one long-polling bot process per token. Unfinished forms use in-memory
storage and are cleared when the bot restarts. Confirmed records, status history,
and queued notifications remain in PostgreSQL. Notification delivery is at least
once: if Telegram accepts a message but the worker stops before recording
success, a retry may send it again.

## Tests and scenarios

Install the development dependencies and run the offline suite:

```sh
python -m pytest -q -p no:cacheprovider
```

Automated tests cover request and booking forms, validation and navigation,
duplicate submissions, administrator access, booking conflicts and cancellation,
database migrations, status history, and notification retries. They use
synthetic Telegram updates and do not require a Telegram token or live Telegram
access.

Language tests also cover the initial picker, preference changes and persistence,
request and booking flows in all six languages, recipient-specific notices,
localized dates, English fallback, and catalog integrity.

PostgreSQL integration tests run only when `TEST_DATABASE_URL` points to an async
PostgreSQL URL with `test` in its database name. They apply real Alembic
migrations and clear application tables before each test. Use a dedicated,
disposable test database; never point the suite at working or production data.

The Compose test profile uses a temporary PostgreSQL database. With a URL-safe
`POSTGRES_PASSWORD`, Compose builds the test URL automatically. If your password
needs URL encoding, set `TEST_DATABASE_URL` in `.env` explicitly, using the
`db_test` host and the same password:

```dotenv
TEST_DATABASE_URL=postgresql+asyncpg://smartlead_test:URL_SAFE_PASSWORD@db_test:5432/smartlead_test
```

Then run:

```sh
docker compose --profile test run --build --rm test
docker compose --profile test stop db_test
```

For a Telegram walkthrough with accounts and a database you control, follow the
[human-operated demo](docs/demo.md).

Run Ruff with:

```sh
python -m ruff check src alembic tests
```

## Screenshots

No live-account screenshots are included. Add screenshots only from a
human-operated demo, and remove names, phone numbers, request details, group IDs,
and private chat information before sharing them.

## Further reading

- [Deployment runbook](docs/deployment.md)
- [Human-operated Telegram demo](docs/demo.md)
- [v1.1 readiness audit](docs/audit-v1.1.md)
- [v1.2 localization changes and validation](docs/changes-v1.2.0.md)
