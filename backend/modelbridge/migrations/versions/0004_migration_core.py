"""Durable approved plans, jobs, outcomes, crosswalks, reconciliation and webhook outbox."""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0004_migration_core"
down_revision = "0003_verified_registration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    def identifier() -> sa.Column[str]:
        return sa.Column("id", sa.String(36), primary_key=True)

    def workspace(index: bool = True) -> sa.Column[str]:
        return sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
            index=index,
        )

    def created() -> sa.Column[datetime]:
        return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)

    op.create_table(
        "migration_plans",
        identifier(),
        workspace(),
        sa.Column("definition", sa.JSON, nullable=False),
        sa.Column("connection_bindings", sa.JSON, nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        created(),
    )
    op.create_table(
        "migration_jobs",
        identifier(),
        workspace(),
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("migration_plans.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, index=True),
        sa.Column("batch_size", sa.Integer, nullable=False),
        sa.Column("sample_size", sa.Integer, nullable=False),
        sa.Column("checkpoint", sa.JSON, nullable=False),
        sa.Column("counters", sa.JSON, nullable=False),
        sa.Column("cancel_requested", sa.Boolean, nullable=False),
        sa.Column("retries", sa.Integer, nullable=False),
        sa.Column("retry_rounds", sa.Integer, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(64)),
        created(),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workspace_id", "idempotency_key"),
    )
    op.create_table(
        "record_outcomes",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("migration_jobs.id"), primary_key=True),
        sa.Column("table_name", sa.String(63), primary_key=True),
        sa.Column("source_hash", sa.String(64), primary_key=True),
        sa.Column("source_key", sa.Text, nullable=False),
        sa.Column("destination_key", sa.Text),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("unique_hashes", sa.JSON, nullable=False),
    )
    op.create_table(
        "identifier_crosswalks",
        sa.Column("plan_id", sa.String(36), sa.ForeignKey("migration_plans.id"), primary_key=True),
        sa.Column("table_name", sa.String(63), primary_key=True),
        sa.Column("source_hash", sa.String(64), primary_key=True),
        sa.Column("source_key", sa.Text, nullable=False),
        sa.Column("destination_hash", sa.String(64), nullable=False, index=True),
        sa.Column("destination_key", sa.Text, nullable=False),
        sa.UniqueConstraint("plan_id", "table_name", "destination_hash"),
    )
    op.create_table(
        "reconciliation_reports",
        identifier(),
        workspace(),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("migration_jobs.id"), nullable=False),
        sa.Column("result", sa.JSON, nullable=False),
        created(),
    )
    op.create_table(
        "webhooks",
        identifier(),
        workspace(),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("subscriptions", sa.JSON, nullable=False),
        sa.Column("secret_envelope", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        created(),
    )
    op.create_table(
        "lifecycle_events",
        identifier(),
        workspace(False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("migration_jobs.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        created(),
    )
    op.create_table(
        "webhook_deliveries",
        identifier(),
        sa.Column(
            "webhook_id", sa.String(36), sa.ForeignKey("webhooks.id"), nullable=False, index=True
        ),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("lifecycle_events.id"), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_code", sa.Integer),
        sa.Column("error_code", sa.String(64)),
        sa.UniqueConstraint("webhook_id", "event_id"),
    )
    op.create_index(
        "ix_migration_jobs_due", "migration_jobs", ["state", "next_attempt_at", "created_at"]
    )
    op.create_index("ix_webhook_deliveries_due", "webhook_deliveries", ["state", "next_attempt_at"])
    op.create_table(
        "webhook_attempts",
        identifier(),
        sa.Column(
            "delivery_id",
            sa.String(36),
            sa.ForeignKey("webhook_deliveries.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status_code", sa.Integer),
        sa.Column("error_code", sa.String(64)),
        sa.UniqueConstraint("delivery_id", "number"),
    )


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is disabled; restore a verified backup instead")
