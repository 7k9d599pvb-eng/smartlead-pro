# Localization

SmartLead Pro supports English (`en`), French (`fr`), Russian (`ru`), Ukrainian
(`uk`), German (`de`), and Japanese (`ja`). Translations ship with the Python
package, so the Docker image and a normal wheel install use the same catalogs.

## Preferences and migration

Migration `0003` adds the nullable `users.language_code` column and a check
constraint limiting it to the six supported codes. Existing profiles are set to
English to preserve their current interface. New profiles have no selected
language until the user chooses one on their first `/start`.

The main menu's globe button and `/language` open the picker again. A choice is
saved against the Telegram user who pressed the button; callbacks cannot edit
another profile. Changing language clears an unfinished FSM form and presents
the main menu in the new language. Ordinary profile updates never overwrite the
saved preference. Telegram's device language does not override an explicit choice.

## Messages and dates

`smartlead.i18n.tr()` reads the catalog key, selects the active language, falls
back to English if the translation is missing, and interpolates named variables.
The middleware sets a ContextVar for each update and restores it afterward,
keeping concurrent users' languages separate. Backend notifications pass their
recipient's language explicitly.

Catalogs contain plain Telegram text. Customer names, contacts, request details,
and service names are inserted as data; they are not treated as HTML or format
strings. Identifiers such as `SL-R-000001`, callback data, and database status
codes retain their original form.

Babel uses the selected locale for full dates and abbreviated calendar buttons.
Times use a 24-hour business clock with the IANA timezone and UTC offset. The
locale changes presentation only: appointment timestamps, slot calculations,
daylight-saving rules, and PostgreSQL overlap checks remain unchanged.

## Notifications

Private customer and administrator notices use the language saved in the
recipient's profile, with English for an unselected preference. A shared
administrator group uses `ADMIN_NOTIFICATION_LANGUAGE` instead because a single
group message cannot have a different language for each reader. This setting
does not affect private messages or administrator access checks.

Status history and the localized text and buttons enter the existing outbox in
one transaction. The worker retries the stored message; changing language does
not create or replay events. Previously queued notices retain their original
language. The existing delivery guarantees and deduplication keys are unchanged.

## Editing translations

1. Add or edit a named key in `src/smartlead/locales/en/messages.json`.
2. Add its translation to the other five catalogs, preserving variable names such
   as `{request_number}` or `{customer_name}`. Save the files as UTF-8.
3. Use `tr("your.key", customer_name=value)` in the handler or presentation layer.
   Keep user-facing sentences outside the business logic.
4. Run catalog checks and the regular suite:

```sh
python -c "from smartlead.i18n import validate_catalogs; validate_catalogs()"
python -m pytest -q -p no:cacheprovider
```

The tests check catalog completeness, placeholder parity, English fallback,
localized calendar dates, and isolation between concurrent language contexts.
PostgreSQL and dispatcher tests exercise saved choices, notifications, and the
request and appointment paths. Run integration tests against a dedicated test
database as described in the README.
