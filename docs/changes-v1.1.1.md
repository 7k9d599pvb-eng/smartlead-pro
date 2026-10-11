# SmartLead Pro 1.1.1

## Changes

- New bookings use the same configured administrator group as new requests.
  Without `ADMIN_NOTIFICATION_CHAT_ID`, both still use the administrator allowlist.
  Booking alerts include a protected **View Appointment** button.
- Customer self-cancellation returns one final private confirmation with the
  appointment number and **Cancelled** status. It does not queue another customer
  notice, including when the customer is also an administrator.
- Administrator cancellation queues one customer confirmation in the existing
  transactional outbox. Status history and idempotent repeat cancellation remain
  intact. Booking creation receipts and other customer flows are unchanged.

## Validation performed on October 11, 2026

- Full local suite with isolated PostgreSQL 17: **131 passed in 6.51 seconds**.
- Full Linux Docker suite with a temporary PostgreSQL database and synthetic
  Telegram API sessions: **131 passed in 10.87 seconds**.
- Ruff checks and formatting, Python compilation, dependency checks, and
  Docker Compose configuration validation passed.
- Production availability was checked without creating a reservation:
  **October 12, 2026, 10:30 Europe/Moscow** was available after cancellation.
- In an isolated test database, the same time was booked, cancelled, rebooked,
  and rejected for a different synthetic Telegram user. Concurrent booking and
  PostgreSQL overlap constraint tests also passed.
- Version **1.1.1** was deployed with schema **0002** unchanged. A protected backup
  was created and fingerprints of all seven existing application tables matched
  at cutover. The environment file retained mode `600`.

The first temporary Docker run failed because extracted migration directories
had restrictive permissions. Source directories were normalized before rebuilding
and rerunning the full suite. The production bot continued running during that
repair.

Automated Telegram tests use synthetic updates and mocked API delivery. A manual
test using a second real Telegram account and a new live group booking alert was
not performed. No real customer reservation was created for these checks.
