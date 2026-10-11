# SmartLead Pro v1.1 readiness audit

Audit date: 11 October 2026

## Scope and baseline

This audit covers the existing automated tests, operator documentation, and the
v1.1 changes in this checkout. The application is a single-business Telegram bot
with a shared PostgreSQL calendar. The test harness sends synthetic Telegram
updates through aiogram's real dispatcher using an in-memory Bot API session; it
does not contact Telegram. PostgreSQL integration tests require the explicitly
configured disposable `TEST_DATABASE_URL`. Never run them against working or
production data.

Before the v1.1 changes, the suite already covered request and booking form happy
paths, validation helpers, stale callbacks, Back and Cancel, duplicate request
and booking confirmation, slot conflicts, customer ownership, administrator
status changes, status history and notification outbox transactions, retry and
row-lock behavior, seed idempotence, booking cancellation, and shutdown cleanup.
The baseline also had end-to-end dispatcher checks for denying an unallowlisted
administrator. It did not exercise all of the portfolio-facing workflows below.

## Initial coverage audit

This is the pre-v1.1 test and documentation audit, separated from the new
acceptance tests below. The offline baseline in this checkout was 58 passed and
12 PostgreSQL tests skipped because no `TEST_DATABASE_URL` was supplied. A prior
70-test result included a dedicated PostgreSQL run; it is not used as the
baseline for this audit.

| Area | Initial state | Gap or follow-up |
| --- | --- | --- |
| Booking and requests | Core form completion, input rules, overlapping-time rejection, duplicate confirmation, and customer ownership had coverage. | Customer-facing status visibility and the message-receipt/navigation behavior were only partial. |
| Persistence | Status history, transactional notification outbox, retry behavior, and migration setup were covered in the PostgreSQL suite. | No focused old-schema fixture proved that existing requests, appointments, legacy contact values, service snapshots, and queued payloads survive the new migration. |
| Administrator access | Allowlist checks and administrator status changes were covered. | Group notification callback authorization, request filtering/pagination, service CRUD and support configuration were absent. |
| Customer notifications | Outbox writes and worker delivery paths were covered. | Exact status copy, safe support-link fallback, and protection against leaking exception payloads to logs were not covered. |
| Form lifecycle | Back, Cancel, invalid details, stale form buttons, and duplicate submissions had coverage. | Strict phone-only invalid-input recovery, repeated callback delivery, and concurrent redelivery of the same update ID were missing. |
| Operational behavior | Seed and shutdown behavior had tests and setup instructions. | Restart persistence and a human-operated, non-fabricated portfolio demo procedure were not documented as explicit checks. |

The operator reported one real request-intake flow and its notification. This is
narrow evidence of that path only; it does not verify booking, cancellation,
administrator workflows, group delivery, or a full portfolio demo.

The review also identified two implementation risks that need focused regression
coverage: rejecting malformed optional configuration without crashing startup,
and serializing request/booking creation against administrator service edits or
deactivation. A legacy schema can backfill a snapshot only from the service's
current referenced name; it cannot reconstruct a historical name that was
changed before snapshots existed.

## v1.1 acceptance checks

The v1.1 review adds coverage for these user-visible and data-preservation
guarantees:

- Phone-only request and booking forms reject malformed input while preserving
  the active form; Back and Cancel remain available.
- A saved request confirmation remains readable while the customer returns to
  the menu or opens their request list.
- Customers see their own request status, and cannot read or cancel another
  customer's records. Repeated cancellation is safe and releases the time slot.
- Group request notifications are delivered to the configured negative Telegram
  chat ID with actionable inline buttons. Only allowlisted administrators may
  use those buttons, and group callbacks are accepted only in the configured
  group.
- Status notifications have clear customer-facing text. The initial `new`
  status can move to `completed` for the small-business shortcut; later invalid
  transitions are rejected.
- Administrator request filters and pagination, appointment lists, service
  creation and editing, activation changes, and support-contact setup and clear
  operations behave as intended.
- Editing or deactivating a service does not rewrite service names or time
  snapshots on existing requests and appointments. The migration preserves
  existing records and pending notification payloads.
- Support links are canonical Telegram URLs or are shown as unconfigured.
- Restarting the application preserves committed records and the notification
  outbox. Error logs identify failures without printing phone numbers, bot
  tokens, or database credentials.

## Verification boundaries

Automated tests prove behavior at the dispatcher and service/database boundary.
They do not prove Telegram account setup, actual group privacy and membership
settings, message delivery from a live bot token, or operator experience on a
deployed business. Those checks require a human operator and accounts they
control; the short demo procedure is in [docs/demo.md](demo.md). No customer
results, performance figures, or live-account screenshots are claimed here.

## Completed implementation stages

The checks below are included in the final full suite; they are not separate
claims of live Telegram walkthroughs.

| Stage | Main files | Checks and outcome |
| --- | --- | --- |
| 1. Audit | Existing models, handlers, FSM, services, worker; this report | Baseline: 58 passed, 12 PostgreSQL tests skipped before the test database was enabled. |
| 2. Requests and admin alerts | `bot/client.py`, `services/core.py`, `services/validation.py`, `presentation.py` | Validation, Back/Cancel, repeated updates and confirmations, real PostgreSQL writes, group recipients and notification buttons covered. |
| 3. Admin panel | `bot/admin.py`, `bot/admin_states.py`, `services/core.py` | Admin ACL, new/all request pages, status actions, appointments, service forms, activation, support and statistics covered. |
| 4. Customer notifications | `workers/outbox.py`, `db/models.py`, `presentation.py`, migration `0002` | Atomic history/outbox, distinct customer text, unique events, persisted keyboards, retries and Telegram failures covered. |
| 5. Booking | `services/core.py`, `bot/client.py`; existing PostgreSQL exclusion constraint retained | Past times, timezone/DST, overlap, simultaneous bookings, service deactivation races, cancellation and slot release covered. |
| 6. Customer history | `bot/client.py`, `services/core.py` | Ownership checks, request details, upcoming appointments and cancellation confirmation covered. |
| 7. Navigation and messages | `bot/keyboards.py`, `presentation.py`, `bot/middleware.py`, `bot/app.py` | Receipt retention, stale callbacks, unexpected input, FSM cleanup, update dedup and safe API-error handling covered. |
| 8. QA | `tests/`, `alembic/env.py`, `db/session.py`, `logging.py` | 126 tests passed locally and in Docker; lint, format, compile and dependency checks passed. SQL parameters and debug result rows are protected from logging. |
| 9. Portfolio documentation | `README.md`, `PORTFOLIO.md`, `.env.example`, `docs/demo.md`, `docs/deployment.md` | English setup, honest scope, screenshots section, eight-step demo and safe upgrade instructions prepared; documentation links checked. |

VEXP supplied ranked source context at the start of the audit. Its `verify_done`
reported verification unavailable because this checkout has no Git repository;
the test, lint and compile results below provide the executed verification.

## Final verification record

The final automated run used the dedicated local PostgreSQL test database
(`smartlead_test` on `127.0.0.1:55432`). The integration fixtures require the
database name to contain `test` and truncate application tables between tests.
This is not the business database.

- Full suite, including PostgreSQL integration tests:
  `pytest -q -p no:cacheprovider --tb=short` — **126 passed**, no skips.
- `.venv/Scripts/python.exe -m ruff check src alembic tests` — passed.
- `.venv/Scripts/python.exe -m ruff format --check src alembic tests` — passed
  (40 files already formatted).
- `.venv/Scripts/python.exe -m compileall -q src alembic tests` and
  `.venv/Scripts/python.exe -m pip check` — passed; no broken requirements.
- `docker compose config --quiet` — passed with a temporary placeholder
  environment file. This validates Compose configuration only; it did not start
  the bot or send Telegram messages.
- Production Docker image `smartlead-bot` version `1.1.0` built successfully on
  Linux. The separate Docker Compose test project ran the same full suite:
  **126 passed in 10.82 seconds**, with a temporary PostgreSQL database and a
  synthetic bot token. The production bot remained running during these tests.
- A protected production backup was restored into a separate temporary test
  database and upgraded from `0001` to `0002`. Fingerprints of all seven tables,
  excluding the newly added fields, matched before and after migration.
- Live Telegram remains outside the automated run. The operator reported one
  real request-intake flow and its notification; a read-only check also confirmed
  the configured group and bot administrator membership. No actual new-request
  group notification, inline-button action, booking, or cancellation was sent or
  exercised against Telegram during this audit.
- Production upgrade completed with package version `1.1.0`, schema `0002`, and
  the existing timezone preserved. A fresh backup was created immediately before
  migration; all seven table fingerprints matched before and after the upgrade.
- The running production container started polling, had zero restarts, and had
  no handler or outbox errors at the post-deployment check. The configured bot was
  an administrator in the target group, and pending/failed outbox counts were
  both zero. The environment file retained mode `600`.
- Support contact remains unconfigured; the bot explains this to customers.
  Set a real contact in `/admin` → **Services** → **Support Contact** when ready.
