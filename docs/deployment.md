# Linux server deployment

This runbook deploys SmartLead in `/opt/smartlead` as the isolated Compose project
`smartlead-portfolio`. It assumes Docker Engine and Docker Compose v2.20 or later
are installed and that the deployment account can run Docker commands. SmartLead
uses Telegram long polling, so it needs outbound DNS and HTTPS access to Telegram;
it does not need an inbound server port. PostgreSQL is private to the Compose
network and has no published port.

## Prepare the server

Place the project files in `/opt/smartlead`, then open a shell in that directory:

```sh
cd /opt/smartlead
docker compose version
docker info
docker compose ls
```

Check that Compose is v2.20 or newer and that Docker Engine is reachable. Use one
polling bot process per Telegram bot token. On a host that already runs containers,
confirm `smartlead-portfolio` is not already in use; the project name below keeps
SmartLead's containers, network, and named database volume separate from other
Compose projects.

Use this helper for the commands below. It clears the shell variables that could
override Compose settings, while preserving the Docker context and CLI
configuration for the current account:

```sh
dc() {
  env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u TEST_DATABASE_URL \
    -u TELEGRAM_API_IP -u COMPOSE_PROJECT_NAME -u COMPOSE_PROFILES -u COMPOSE_FILE \
    docker compose --env-file .env -p smartlead-portfolio -f compose.yaml "$@"
}
```

Create `.env` only for the first deployment. Edit it in a private terminal on the
server; never paste tokens, passwords, or the file contents into chat or commit
the file:

```sh
cp .env.example .env
chmod 600 .env
${EDITOR:-vi} .env
```

Set `BOT_TOKEN` from BotFather and put each administrator's numeric Telegram ID in
`ADMIN_IDS`. Set a strong URL-safe `POSTGRES_PASSWORD`, and use the same value in
`DATABASE_URL`. For the Compose deployment, the database URL must use host `db`,
for example:

```dotenv
POSTGRES_DB=smartlead
POSTGRES_USER=smartlead
POSTGRES_PASSWORD=<your-url-safe-password>
DATABASE_URL=postgresql+asyncpg://smartlead:<your-url-safe-password>@db:5432/smartlead
```

Keep the database name and user in the URL aligned with `POSTGRES_DB` and
`POSTGRES_USER`. A URL-safe password avoids URL-encoding errors. If you change the
password for a database volume that already exists, changing `.env` alone does
not change the PostgreSQL role password; rotate the role in PostgreSQL and update
the URL together. Compose passes every `.env` entry to the `bot`, `seed`, and
`migrate` containers, so protect the file and remove test-only entries such as
`TEST_DATABASE_URL` before starting the production bot.

Validate the resolved Compose configuration without printing its secret values,
then build and run the dedicated test profile before using the production
database:

```sh
dc config --quiet
dc --profile test run --build --rm test
dc --profile test stop db_test
```

The test profile uses its own temporary PostgreSQL service. Compose derives its
URL from `POSTGRES_PASSWORD` by default. If that password contains reserved URL
characters, set the optional `TEST_DATABASE_URL` with a percent-encoded password
and host `db_test`. Remove that test-only entry before starting production
services because Compose passes all `.env` entries into the app containers.
Do not point the integration suite at the production database.

The v1.1.1 Linux Docker test image passed 131 tests, including PostgreSQL
integration tests. That verifies the application and database paths; it does not
test access to Telegram from this server. See [the validation record](changes-v1.1.1.md).

The v1.2.0 language release passed 189 tests in the Linux Docker test image. Its
migration, data-preservation, and polling checks are recorded in
[the language-release validation](changes-v1.2.0.md).

## If the Telegram API times out

First test the server's ordinary route:

```sh
curl --connect-timeout 5 --max-time 15 --silent --show-error --output /dev/null \
  --write-out 'HTTP %{http_code}\n' https://api.telegram.org/
```

If that request times out, test a candidate address with `--resolve`. Use an
address from a trusted network source and leave curl's default TLS verification
enabled; do not add `-k` or change global DNS, proxy, or TLS settings. An HTTP
response (for example, `302`) with a successful curl exit confirms that the
connection and certificate check succeeded for that address:

```sh
candidate_ip='REPLACE_WITH_CANDIDATE_IP'
curl --connect-timeout 5 --max-time 15 --silent --show-error --output /dev/null \
  --write-out 'HTTP %{http_code}\n' \
  --resolve "api.telegram.org:443:$candidate_ip" https://api.telegram.org/
```

Only after that check succeeds, set `TELEGRAM_API_IP` to the verified address in
the private `.env` file. The optional `compose.telegram.yaml` adds a hosts entry
for `api.telegram.org` inside the bot container; TLS still verifies the
`api.telegram.org` hostname. This address override may need updating if
Telegram's API address changes.

When the override is enabled, unset any shell value and replace the `dc` helper
above with this two-file version. Use it for every Compose command, including
configuration checks, tests, start/stop, logs, and backups, so the override is
consistently applied:

```sh
unset TELEGRAM_API_IP
dc() {
  env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u TEST_DATABASE_URL \
    -u TELEGRAM_API_IP -u COMPOSE_PROJECT_NAME -u COMPOSE_PROFILES -u COMPOSE_FILE \
    docker compose --env-file .env -p smartlead-portfolio \
      -f compose.yaml -f compose.telegram.yaml "$@"
}
dc config --quiet
```

## Start and verify SmartLead

Start the stack from `/opt/smartlead`:

```sh
dc up --build -d
dc exec bot python -m smartlead check
dc ps
dc logs --tail=100 migrate seed bot
```

Compose waits for PostgreSQL to become healthy, applies the Alembic migrations,
runs the idempotent seed step, and then starts the bot. The `check` command
confirms the database connection, schema revision, and booking constraint without
contacting Telegram. If a service failed, inspect its logs before retrying.

Complete a manual Telegram check with a test customer chat: send `/start`, submit
a request, and make and cancel a booking. Each administrator must send `/start`
to the bot once before Telegram will allow the bot to message that administrator.
Then use `/admin` to change a request status and confirm the customer receives the
notification. These manual actions create normal records in the deployed
database.

The bot has no Compose healthcheck, so `dc ps` alone does not prove Telegram
polling is active. Check the bot logs after startup and after an administrator or
customer action:

```sh
dc logs -f --tail=100 bot
```

The application writes logs to stdout/stderr. Compose rotates the bot and database
logs at 10 MB per file and retains three files per container.

## Back up and verify a restore

The named PostgreSQL volume survives container recreation, but it is not a backup.
Create regular backups and copy them off the server. The following command writes
a PostgreSQL custom archive with owner-only permissions in the current account's
home directory. The archive itself is not encrypted; encrypt it before storing or
transferring it off the server.

```sh
mkdir -p "$HOME/backups/smartlead"
chmod 700 "$HOME/backups/smartlead"
umask 077
backup="$HOME/backups/smartlead/smartlead-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump"
dc exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup"
dc exec -T db pg_restore --list < "$backup" > /dev/null
```

Periodically verify a backup by restoring it into a newly created database in the
isolated, temporary `db_test` service. If the retained archive is encrypted,
decrypt it to protected local storage first and set `backup` to that file. The
service uses tmpfs and is separate from the production `db`; these commands do
not drop or overwrite the production database:

```sh
restore_db="smartlead_restore_$(date -u +%Y%m%d%H%M%S)"
dc --profile test up -d --wait db_test
dc --profile test exec -T db_test sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$restore_db"
dc --profile test exec -T db_test sh -c 'pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d "$1"' sh "$restore_db" < "$backup"
dc --profile test exec -T db_test sh -c 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -c "SELECT version_num FROM alembic_version"' sh "$restore_db"
dc --profile test stop db_test
```

Keep the archive until the restore check succeeds. Preserve backups off-host, and
never use a production database name for `restore_db`.

## Stop and upgrade

To stop the stack, run `dc down`. This removes the containers and project network
but preserves the `smartlead-portfolio` PostgreSQL volume and the `.env` file. Do
not add `-v` unless you intend to delete the database volume and all stored
requests, appointments, history, and queued notifications.

Before an application upgrade, create and verify a backup. Update the project
files in `/opt/smartlead` without replacing `.env`. Build while the current bot
is still running, then stop polling before applying migrations. This prevents
the previous application version from writing to a newly changed schema:

```sh
dc build migrate seed bot
dc stop bot
dc run --rm --no-deps migrate
dc run --rm --no-deps seed
dc up --no-build -d
dc exec bot python -m smartlead check
dc ps
dc logs --tail=100 migrate seed bot
```

The migration and seed services run before the bot starts. Keep one active
long-polling process per Telegram token; starting another copy of the same bot
token can interrupt polling.
