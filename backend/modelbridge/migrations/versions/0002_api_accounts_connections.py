"""Account, scoped key, connection and schema APIs."""

import sqlalchemy as sa
from alembic import op

revision = "0002_api_product"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_table(
        "memberships",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("role", sa.String(24), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner','administrator','migration_engineer','auditor')",
            name="valid_membership_role",
        ),
    )
    op.add_column(
        "access_credentials", sa.Column("user_id", sa.String(36), sa.ForeignKey("accounts.id"))
    )
    op.add_column("access_credentials", sa.Column("name", sa.String(100)))
    op.add_column("access_credentials", sa.Column("scopes", sa.JSON))
    op.add_column("access_credentials", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("access_credentials", sa.Column("last_used_at", sa.DateTime(timezone=True)))
    op.create_table(
        "api_rate_buckets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("window", sa.Integer, nullable=False),
        sa.Column("count", sa.Integer, nullable=False),
    )
    op.create_table(
        "database_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer, nullable=False),
        sa.Column("database", sa.String(100), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("password_envelope", sa.Text, nullable=False),
        sa.Column("sslmode", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_database_connections_workspace_id", "database_connections", ["workspace_id"]
    )
    op.create_table(
        "schema_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "connection_id", sa.String(36), sa.ForeignKey("database_connections.id"), nullable=False
        ),
        sa.Column("schema_name", sa.String(63), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("objects", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_schema_snapshots_workspace_id", "schema_snapshots", ["workspace_id"])
    op.create_index("ix_schema_snapshots_connection_id", "schema_snapshots", ["connection_id"])


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore an operator-verified backup")
