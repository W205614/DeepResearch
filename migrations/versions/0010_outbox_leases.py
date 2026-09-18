"""Atomic outbox claims, expiring leases and recoverable dead letters.

Revision ID: 0010_outbox_leases
Revises: 0009_java_business_outbox
"""
from alembic import op


revision = "0010_outbox_leases"
down_revision = "0009_java_business_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE business_outbox ADD COLUMN IF NOT EXISTS lease_owner TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE business_outbox ADD COLUMN IF NOT EXISTS lease_until DOUBLE PRECISION NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE business_outbox ADD COLUMN IF NOT EXISTS last_error TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE business_outbox ADD COLUMN IF NOT EXISTS delivered_at TEXT NOT NULL DEFAULT ''")
    op.execute("""
        WITH ranked AS (
            SELECT id,row_number() OVER (
                PARTITION BY aggregate_id,event_type ORDER BY created_at,id
            ) AS duplicate_number
            FROM business_outbox WHERE status='pending'
        )
        UPDATE business_outbox SET status='dead',last_error='duplicate_active_command_before_lease_migration'
        WHERE id IN (SELECT id FROM ranked WHERE duplicate_number>1)
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS business_outbox_active_command
        ON business_outbox(aggregate_id,event_type)
        WHERE status IN ('pending','processing')
    """)
    op.execute("DROP INDEX IF EXISTS business_outbox_pending")
    op.execute("""
        CREATE INDEX business_outbox_pending
        ON business_outbox(status,next_attempt_at,lease_until,created_at)
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE business_outbox ADD CONSTRAINT business_outbox_status
            CHECK (status IN ('pending','processing','delivered','dead'));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS business_outbox_active_command")
    op.execute("UPDATE business_outbox SET status='pending' WHERE status='processing'")
    op.execute("UPDATE business_outbox SET status='delivered' WHERE status='dead'")
    op.execute("ALTER TABLE business_outbox DROP CONSTRAINT IF EXISTS business_outbox_status")
    op.execute("DROP INDEX IF EXISTS business_outbox_pending")
    op.execute("CREATE INDEX business_outbox_pending ON business_outbox(status,next_attempt_at,created_at)")
    op.execute("ALTER TABLE business_outbox DROP COLUMN IF EXISTS delivered_at")
    op.execute("ALTER TABLE business_outbox DROP COLUMN IF EXISTS last_error")
    op.execute("ALTER TABLE business_outbox DROP COLUMN IF EXISTS lease_until")
    op.execute("ALTER TABLE business_outbox DROP COLUMN IF EXISTS lease_owner")
