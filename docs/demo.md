# Human-operated Telegram demo

This eight-step walkthrough is for an operator testing an isolated demo bot. Use
only Telegram accounts and a group you control. Use a disposable demo database;
do not point the walkthrough at a live business database or enter a customer's
personal information. Never publish bot tokens, phone numbers, or private chat
screenshots.

Prepare a demo bot, `ADMIN_IDS`, and a disposable PostgreSQL database using
[README.md](../README.md). For group alerts, add the bot to an operator-controlled
private group and configure `ADMIN_NOTIFICATION_CHAT_ID`. Administrators must
send `/start` before receiving direct messages. Use clearly labelled synthetic
names, phone numbers, and descriptions; never use real customer data. Hide
account names and IDs in screenshots.

1. The customer opens the bot and sends `/start`, then selects **English** if
   prompted. The labels below assume English; `/language` opens the six-language
   picker at any time.
2. The customer chooses **New request**, selects a service, and enters synthetic
   demo contact details and a short description.
3. The customer reviews the details and presses **Send request**. The bot returns
   a unique reference such as `#SL-R-000001`.
4. The administrator receives **New Customer Request** in the configured group
   or, when no group is configured, in their private bot chat.
5. The administrator presses **Start Processing** on the notification.
6. The customer receives **Request Update** with the new **In Progress** status.
7. The customer chooses **Book a service**, selects a future date and free time,
   enters synthetic contact details, and confirms.
8. The bot returns **Booking Confirmed!** with the service, business-local date
   and time, and unique booking reference.

An optional follow-up demonstrates **My requests**, completion notifications,
and **My appointments** → **Cancel appointment**. Confirm cancellation and check
that the time becomes available again.

To demonstrate localization, use the globe button or `/language`, choose another
language, and reopen the saved request or appointment. The text and date format
change; reference numbers, statuses in the database, and booked instants stay the
same. Administrators can choose their language independently. Shared group alerts
use `ADMIN_NOTIFICATION_LANGUAGE` rather than a reader's personal preference.

This procedure is a manual demonstration, not a claim that live Telegram checks
have been run. Automated dispatcher tests use synthetic updates and an in-memory
Bot API session. Remove the demo database and its local configuration when the
walkthrough is complete.
