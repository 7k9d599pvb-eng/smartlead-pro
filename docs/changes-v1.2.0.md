# SmartLead Pro 1.2.0 — six languages

The existing request, appointment, admin, history, and outbox paths now support
English, French, Russian, Ukrainian, German, and Japanese. New users choose a
language on `/start`; preferences persist in PostgreSQL. The globe button or
`/language` changes the preference and returns to a translated menu.

## Changes

- Migration `0003` adds the user language column and a supported-code constraint.
  Existing profiles retain English; new profiles remain unselected until a choice.
- Six packaged UTF-8 JSON catalogs contain 282 matching keys and placeholders.
  Client forms, list/detail screens, errors, support, admin CRUD, statistics,
  commands, receipts, status labels, and notification buttons use catalog text.
- Babel formats dates by locale. Compact time buttons retain the business timezone
  and UTC offset; review and confirmation screens show full dates. Language
  changes do not alter booking instants or calendar constraints.
- Notifications render each private recipient's saved language inside the existing
  status transaction. Shared group alerts use `ADMIN_NOTIFICATION_LANGUAGE` once
  per event. Queued notices retain their language; preference changes do not replay
  notifications.
- Request transitions, ownership checks, administrator checks, submission keys,
  booking exclusion constraints, cancellation history, and worker retry behavior
  are preserved.

## Validation

Local Python 3.12 and PostgreSQL 17 checks on October 11, 2026:

- Full pytest suite: **189 passed**, no skips, in 9.19 seconds.
- Ruff lint and formatting, Python compilation, and dependency consistency passed.
- Migration `0002 → 0003` applied; Alembic reported no ORM/schema drift.
- All six catalogs parse and pass required-key and placeholder checks.
- A wheel build included all six catalogs, readable through package resources.
- VEXP reported no parse errors, broken imports, or pending dependent changes;
  all 14 affected test modules ran in the full suite.

The Linux Docker test image also passed **189 tests**, with no skips, in 16.14
seconds. It used its own temporary PostgreSQL service and synthetic Telegram
transport; the production database was not used for tests.

The deployed bot reports package version `1.2.0` and schema revision `0003`.
Before the new process started, a protected backup and a seven-table comparison
confirmed that existing data were preserved, excluding only the newly added
language column. The business timezone remains `Europe/Moscow`. Startup logs
confirmed active polling with zero restarts or Telegram network errors at the
post-deployment check. All six installed catalogs passed validation.

The tests use synthetic Telegram updates, an in-memory Bot API transport, and an
isolated PostgreSQL database. They do not claim a human walkthrough with six live
Telegram accounts. Use `/language` for that final device-level check. Keep demo
names, contacts, and screenshots separate from real customer data.
