"""Persist notification keyboards, support details, and historical service names.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("business_settings", sa.Column("support_contact", sa.String(50), nullable=True))
    op.add_column("notification_outbox", sa.Column("reply_markup", sa.JSON(), nullable=True))
    op.add_column("requests", sa.Column("service_name", sa.String(100), nullable=True))
    op.add_column("appointments", sa.Column("service_name", sa.String(100), nullable=True))

    # Legacy rows have no stored historical label, so use their currently referenced catalog name.
    # New rows then retain this snapshot when the service is renamed later.
    op.execute(
        "UPDATE requests SET service_name = services.name "
        "FROM services WHERE requests.service_id = services.id"
    )
    op.execute(
        "UPDATE appointments SET service_name = services.name "
        "FROM services WHERE appointments.service_id = services.id"
    )
    op.alter_column("requests", "service_name", existing_type=sa.String(100), nullable=False)
    op.alter_column("appointments", "service_name", existing_type=sa.String(100), nullable=False)


def downgrade() -> None:
    op.drop_column("appointments", "service_name")
    op.drop_column("requests", "service_name")
    op.drop_column("notification_outbox", "reply_markup")
    op.drop_column("business_settings", "support_contact")
