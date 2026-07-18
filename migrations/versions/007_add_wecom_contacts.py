"""Add WeCom contacts

Revision ID: 007
Revises: 006
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa


revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "wecom_contacts" in inspector.get_table_names():
        return

    op.create_table(
        "wecom_contacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("wecom_user_id", sa.String(), nullable=True),
        sa.Column("chat_id", sa.String(), nullable=True),
        sa.Column("chat_type", sa.String(), nullable=True),
        sa.Column("aibot_id", sa.String(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_message_id", sa.String(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("contact_metadata", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wecom_contacts_id", "wecom_contacts", ["id"])
    op.create_index("ix_wecom_contacts_user_id", "wecom_contacts", ["user_id"])
    op.create_index("ix_wecom_contacts_wecom_user_id", "wecom_contacts", ["wecom_user_id"])
    op.create_index("ix_wecom_contacts_chat_id", "wecom_contacts", ["chat_id"])
    op.create_index("ix_wecom_contacts_is_default", "wecom_contacts", ["is_default"])
    op.create_index("ix_wecom_contacts_user_default", "wecom_contacts", ["user_id", "is_default"])
    op.create_index("ix_wecom_contacts_user_wecom", "wecom_contacts", ["user_id", "wecom_user_id"])
    op.create_index("ix_wecom_contacts_user_chat", "wecom_contacts", ["user_id", "chat_id"])


def downgrade() -> None:
    op.drop_index("ix_wecom_contacts_user_chat", table_name="wecom_contacts")
    op.drop_index("ix_wecom_contacts_user_wecom", table_name="wecom_contacts")
    op.drop_index("ix_wecom_contacts_user_default", table_name="wecom_contacts")
    op.drop_index("ix_wecom_contacts_is_default", table_name="wecom_contacts")
    op.drop_index("ix_wecom_contacts_chat_id", table_name="wecom_contacts")
    op.drop_index("ix_wecom_contacts_wecom_user_id", table_name="wecom_contacts")
    op.drop_index("ix_wecom_contacts_user_id", table_name="wecom_contacts")
    op.drop_index("ix_wecom_contacts_id", table_name="wecom_contacts")
    op.drop_table("wecom_contacts")
