"""Persist language preferences without changing requests, bookings or queued notices.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("language_code", sa.String(2), nullable=True))
    # Existing users keep the interface they used before this upgrade. New users
    # have NULL until they explicitly choose a language on their first /start.
    op.execute("UPDATE users SET language_code = 'en'")
    op.create_check_constraint(
        "users_language_valid",
        "users",
        "language_code IS NULL OR language_code IN ('en', 'fr', 'ru', 'uk', 'de', 'ja')",
    )


def downgrade() -> None:
    op.drop_constraint("users_language_valid", "users", type_="check")
    op.drop_column("users", "language_code")
