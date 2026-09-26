"""Owner-scoped application schema (AUTH_PLAN.md).

Revision ID: 0001
Revises:
Create Date: 2026-09-24
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
          user_id UUID PRIMARY KEY,
          firebase_uid TEXT UNIQUE NOT NULL,
          email TEXT,
          display_name TEXT,
          created_at TIMESTAMPTZ NOT NULL,
          last_seen_at TIMESTAMPTZ NOT NULL
        );

        CREATE TABLE workspaces (
          workspace_id UUID PRIMARY KEY,
          name TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL
        );

        CREATE TABLE workspace_members (
          workspace_id UUID NOT NULL REFERENCES workspaces,
          user_id UUID NOT NULL REFERENCES users,
          role TEXT NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
          PRIMARY KEY (workspace_id, user_id)
        );

        CREATE TABLE data_sources (
          source_id TEXT PRIMARY KEY,
          owner_user_id UUID NOT NULL REFERENCES users,
          workspace_id UUID REFERENCES workspaces,
          current_version_id TEXT,
          payload JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL
        );
        CREATE INDEX data_sources_owner_idx ON data_sources (owner_user_id, created_at DESC);

        CREATE TABLE source_versions (
          version_id TEXT PRIMARY KEY,
          source_id TEXT NOT NULL REFERENCES data_sources ON DELETE CASCADE,
          owner_user_id UUID NOT NULL REFERENCES users,
          object_uri TEXT,
          sha256 TEXT,
          record_count INTEGER NOT NULL,
          extraction_status TEXT NOT NULL,
          ingested_at TIMESTAMPTZ NOT NULL,
          manifest JSONB NOT NULL
        );
        CREATE INDEX source_versions_source_idx ON source_versions (source_id, ingested_at DESC);

        CREATE TABLE source_records (
          version_id TEXT NOT NULL REFERENCES source_versions ON DELETE CASCADE,
          record_index INTEGER NOT NULL,
          payload JSONB NOT NULL,
          PRIMARY KEY (version_id, record_index)
        );

        CREATE TABLE investigations (
          investigation_id TEXT PRIMARY KEY,
          owner_user_id UUID NOT NULL REFERENCES users,
          workspace_id UUID REFERENCES workspaces,
          payload JSONB NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL
        );
        CREATE INDEX investigations_owner_idx ON investigations (owner_user_id, updated_at DESC);

        CREATE TABLE notebook_entries (
          entry_id TEXT PRIMARY KEY,
          investigation_id TEXT NOT NULL REFERENCES investigations,
          owner_user_id UUID NOT NULL REFERENCES users,
          workspace_id UUID REFERENCES workspaces,
          revision INTEGER NOT NULL,
          saved_at TIMESTAMPTZ NOT NULL,
          payload JSONB NOT NULL,
          UNIQUE (investigation_id, revision)
        );
        CREATE INDEX notebook_entries_owner_idx ON notebook_entries (owner_user_id, saved_at DESC);

        CREATE TABLE usage_ledger (
          id BIGSERIAL PRIMARY KEY,
          investigation_id TEXT NOT NULL,
          owner_user_id UUID REFERENCES users,
          model TEXT NOT NULL,
          input_tokens INTEGER NOT NULL,
          output_tokens INTEGER NOT NULL,
          estimated_cost DOUBLE PRECISION NOT NULL,
          created_at TIMESTAMPTZ NOT NULL
        );
        CREATE INDEX usage_ledger_owner_idx ON usage_ledger (owner_user_id);

        -- Security audit trail. Never stores raw ID tokens.
        CREATE TABLE audit_events (
          id BIGSERIAL PRIMARY KEY,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          event_type TEXT NOT NULL,
          owner_user_id UUID,
          investigation_id TEXT,
          source_id TEXT,
          detail JSONB NOT NULL DEFAULT '{}'::jsonb
        );
        CREATE INDEX audit_events_created_idx ON audit_events (created_at DESC);
        """
    )
    # Dedicated system/demo owner for existing demo data and the shared starter source.
    op.execute(
        f"""
        INSERT INTO users (user_id, firebase_uid, email, display_name, created_at, last_seen_at)
        VALUES ('{SYSTEM_USER_ID}', 'system:demo', NULL, 'SourceLens demo', now(), now())
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE audit_events, usage_ledger, notebook_entries, investigations,
          source_records, source_versions, data_sources, workspace_members, workspaces, users;
        """
    )
