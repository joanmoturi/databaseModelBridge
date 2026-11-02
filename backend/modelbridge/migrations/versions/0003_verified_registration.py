"""Fail closed on unverified email identities; operator-issued registration proof."""

import sqlalchemy as sa
from alembic import op

revision = "0003_verified_registration"
down_revision = "0002_api_product"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("email_verified", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "registration_permits",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a verified backup instead")
