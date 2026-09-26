"""Store revocable SourceLens sessions independently of the identity provider.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE auth_sessions (
          token_hash TEXT PRIMARY KEY,
          owner_user_id UUID NOT NULL REFERENCES users ON DELETE CASCADE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          expires_at TIMESTAMPTZ NOT NULL,
          revoked_at TIMESTAMPTZ
        );
        CREATE INDEX auth_sessions_owner_idx ON auth_sessions (owner_user_id);
        CREATE INDEX auth_sessions_expiry_idx ON auth_sessions (expires_at);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE auth_sessions")
