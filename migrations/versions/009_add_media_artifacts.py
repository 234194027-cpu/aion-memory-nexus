"""Add media artifacts

Revision ID: 009
Revises: 008
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "media_artifacts" in inspector.get_table_names():
        return

    op.create_table(
        "media_artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("raw_event_id", sa.String(), nullable=False),
        sa.Column("source_channel", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("original_name", sa.String(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(), nullable=True),
        sa.Column("storage_path", sa.String(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("wecom_media_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        sa.Column("extractor_name", sa.String(), nullable=True),
        sa.Column("extractor_version", sa.String(), nullable=True),
        sa.Column("extracted_text_path", sa.String(), nullable=True),
        sa.Column("extracted_json_path", sa.String(), nullable=True),
        sa.Column("artifact_metadata", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["raw_event_id"], ["raw_events.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_artifacts_id", "media_artifacts", ["id"])
    op.create_index("ix_media_artifacts_user_id", "media_artifacts", ["user_id"])
    op.create_index("ix_media_artifacts_raw_event_id", "media_artifacts", ["raw_event_id"])
    op.create_index("ix_media_artifacts_source_channel", "media_artifacts", ["source_channel"])
    op.create_index("ix_media_artifacts_message_id", "media_artifacts", ["message_id"])
    op.create_index("ix_media_artifacts_media_type", "media_artifacts", ["media_type"])
    op.create_index("ix_media_artifacts_sha256", "media_artifacts", ["sha256"])
    op.create_index("ix_media_artifacts_wecom_media_id", "media_artifacts", ["wecom_media_id"])
    op.create_index("ix_media_artifacts_status", "media_artifacts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_media_artifacts_status", table_name="media_artifacts")
    op.drop_index("ix_media_artifacts_wecom_media_id", table_name="media_artifacts")
    op.drop_index("ix_media_artifacts_sha256", table_name="media_artifacts")
    op.drop_index("ix_media_artifacts_media_type", table_name="media_artifacts")
    op.drop_index("ix_media_artifacts_message_id", table_name="media_artifacts")
    op.drop_index("ix_media_artifacts_source_channel", table_name="media_artifacts")
    op.drop_index("ix_media_artifacts_raw_event_id", table_name="media_artifacts")
    op.drop_index("ix_media_artifacts_user_id", table_name="media_artifacts")
    op.drop_index("ix_media_artifacts_id", table_name="media_artifacts")
    op.drop_table("media_artifacts")
