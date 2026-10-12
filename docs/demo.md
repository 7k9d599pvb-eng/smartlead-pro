# Human-operated Telegram demo

This live scenario takes about 60–90 seconds after setup. It uses two separate
Telegram accounts controlled by the operator: one customer and one allowlisted
administrator.

## Before the walkthrough

Prepare a demo bot and disposable PostgreSQL database using
[README.md](../README.md). Set `ADMIN_IDS` for the administrator account. For
private alerts, leave `ADMIN_NOTIFICATION_CHAT_ID` unset; for group alerts, add
the bot to an operator-controlled group and configure that group's ID. Group
buttons still require an allowlisted administrator. The administrator must send
`/start` before receiving direct messages.

Use a demo database, not a live business database. Enter only clearly synthetic
names, contact details, and request descriptions. Do not use real customer data.
Before sharing screenshots, crop and hide account names, IDs, contact details,
and any group information.

## Walkthrough (60–90 seconds)

1. In the customer account, open the bot with `/start`. Choose English if the
   first-use language picker appears, then show the main menu.
2. Choose **New request**, select a demo service, enter synthetic details, review
   them, and submit. Show the request receipt and its `SL-R-…` reference.
3. Switch to the separate administrator account. Open the new-request alert in
   the private chat or configured group and choose **Start Processing**.
4. Return to the customer account and show the **In Progress** notification.
5. Return to the menu, choose **Book a service**, select an available future
   date and time, enter synthetic details, and confirm. Show the receipt and its
   `SL-A-…` reference.
6. Use the globe button or `/language`, choose another language, and show the
   translated menu. The saved request and booked time remain unchanged.

After the timed walkthrough, optionally show a completion notification and
**My appointments** → **Cancel appointment**. Confirm cancellation and check that
the slot becomes available again. Administrators choose their interface language
independently; shared group alerts use `ADMIN_NOTIFICATION_LANGUAGE`.

This procedure is a manual demonstration, not a claim that live Telegram checks
have been run. Automated dispatcher tests use synthetic updates and an in-memory
Bot API session. The README screenshots show language selection, the request/status,
booking confirmation, and administrator panel. The customer main menu is
demonstrated live and is not pictured. Remove the demo database and its local
configuration when the walkthrough is complete.
