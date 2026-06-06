"""add telegram runtime tables

Revision ID: 20260606_0001
Revises: 20260101_0000
Create Date: 2026-06-06 00:01:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260606_0001"
down_revision: Union[str, None] = "20260101_0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_installations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.String(length=100), nullable=False),
        sa.Column("external_bot_id", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False, server_default="workspace_bot"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("team_id", "alias", name="uq_telegram_installations_team_alias"),
        sa.UniqueConstraint(
            "external_bot_id",
            name="uq_telegram_installations_external_bot_id",
        ),
    )
    op.create_index(
        "idx_telegram_installations_team_id",
        "telegram_installations",
        ["team_id"],
    )

    op.create_table(
        "telegram_chats",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_chat_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("ingest_mode", sa.String(length=32), nullable=False, server_default="disabled"),
        sa.Column(
            "access_mode",
            sa.String(length=32),
            nullable=False,
            server_default="workspace_bot",
        ),
        sa.Column(
            "send_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["telegram_installations.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "installation_id",
            "external_chat_id",
            name="uq_telegram_chats_installation_chat",
        ),
    )
    op.create_index(
        "idx_telegram_chats_installation_id",
        "telegram_chats",
        ["installation_id"],
    )

    op.create_table(
        "telegram_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("external_user_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("language_code", sa.String(length=32), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("external_user_id", name="uq_telegram_users_external_user_id"),
    )
    op.create_index(
        "idx_telegram_users_external_user_id",
        "telegram_users",
        ["external_user_id"],
    )

    op.create_table(
        "telegram_user_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("telegram_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["telegram_installations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["telegram_user_id"], ["telegram_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "team_id",
            "telegram_user_id",
            name="uq_telegram_user_links_team_telegram_user",
        ),
    )
    op.create_index(
        "idx_telegram_user_links_team_id",
        "telegram_user_links",
        ["team_id"],
    )

    op.create_table(
        "telegram_business_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_connection_id", sa.String(length=128), nullable=False),
        sa.Column("can_reply", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "selected_chat_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["telegram_installations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["telegram_user_id"], ["telegram_users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "business_connection_id",
            name="uq_telegram_business_connections_external_id",
        ),
    )
    op.create_index(
        "idx_telegram_business_connections_team_id",
        "telegram_business_connections",
        ["team_id"],
    )

    op.create_table(
        "telegram_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("payload_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["telegram_installations.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "installation_id",
            "update_id",
            name="uq_telegram_updates_installation_update_id",
        ),
    )
    op.create_index(
        "idx_telegram_updates_installation_id",
        "telegram_updates",
        ["installation_id"],
    )

    op.create_table(
        "telegram_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("telegram_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("business_connection_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_update_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False, server_default="inbound"),
        sa.Column(
            "access_mode",
            sa.String(length=32),
            nullable=False,
            server_default="workspace_bot",
        ),
        sa.Column("external_chat_id", sa.String(length=64), nullable=False),
        sa.Column("external_message_id", sa.String(length=64), nullable=False),
        sa.Column("external_thread_id", sa.String(length=64), nullable=True),
        sa.Column("reply_to_external_message_id", sa.String(length=64), nullable=True),
        sa.Column("message_kind", sa.String(length=32), nullable=False, server_default="text"),
        sa.Column("import_source", sa.String(length=64), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("media_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["telegram_installations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["telegram_chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["business_connection_ref_id"],
            ["telegram_business_connections.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["raw_update_id"], ["telegram_updates.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "installation_id",
            "external_chat_id",
            "external_message_id",
            name="uq_telegram_messages_installation_chat_message",
        ),
    )
    op.create_index("idx_telegram_messages_team_id", "telegram_messages", ["team_id"])
    op.create_index(
        "idx_telegram_messages_chat_sent_at",
        "telegram_messages",
        ["external_chat_id", "sent_at"],
    )
    op.create_index(
        "idx_telegram_messages_thread_id",
        "telegram_messages",
        ["external_thread_id"],
    )

    op.create_table(
        "telegram_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chat_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("business_connection_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="agent_reply"),
        sa.Column("target_chat_id", sa.String(length=64), nullable=True),
        sa.Column("target_user_id", sa.String(length=64), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["telegram_installations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["telegram_chats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["business_connection_ref_id"],
            ["telegram_business_connections.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("team_id", "dedupe_key", name="uq_telegram_outbox_team_dedupe_key"),
    )
    op.create_index(
        "idx_telegram_outbox_status_next_attempt",
        "telegram_outbox",
        ["status", "next_attempt_at"],
    )
    op.create_index("idx_telegram_outbox_team_id", "telegram_outbox", ["team_id"])

    op.create_table(
        "telegram_callback_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirm_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("target_chat_id", sa.String(length=64), nullable=True),
        sa.Column("target_user_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["telegram_installations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["confirm_id"], ["confirms.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_telegram_callback_tokens_token_hash",
        ),
    )
    op.create_index(
        "idx_telegram_callback_tokens_confirm_id",
        "telegram_callback_tokens",
        ["confirm_id"],
    )

    op.create_table(
        "telegram_notification_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("quiet_hours", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "digest_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["telegram_users.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "team_id",
            "telegram_user_id",
            "category",
            name="uq_telegram_notification_preferences_team_user_category",
        ),
    )
    op.create_index(
        "idx_telegram_notification_preferences_team_id",
        "telegram_notification_preferences",
        ["team_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_telegram_notification_preferences_team_id",
        table_name="telegram_notification_preferences",
    )
    op.drop_table("telegram_notification_preferences")

    op.drop_index("idx_telegram_callback_tokens_confirm_id", table_name="telegram_callback_tokens")
    op.drop_table("telegram_callback_tokens")

    op.drop_index("idx_telegram_outbox_team_id", table_name="telegram_outbox")
    op.drop_index(
        "idx_telegram_outbox_status_next_attempt",
        table_name="telegram_outbox",
    )
    op.drop_table("telegram_outbox")

    op.drop_index("idx_telegram_messages_thread_id", table_name="telegram_messages")
    op.drop_index("idx_telegram_messages_chat_sent_at", table_name="telegram_messages")
    op.drop_index("idx_telegram_messages_team_id", table_name="telegram_messages")
    op.drop_table("telegram_messages")

    op.drop_index("idx_telegram_updates_installation_id", table_name="telegram_updates")
    op.drop_table("telegram_updates")

    op.drop_index(
        "idx_telegram_business_connections_team_id",
        table_name="telegram_business_connections",
    )
    op.drop_table("telegram_business_connections")

    op.drop_index("idx_telegram_user_links_team_id", table_name="telegram_user_links")
    op.drop_table("telegram_user_links")

    op.drop_index("idx_telegram_users_external_user_id", table_name="telegram_users")
    op.drop_table("telegram_users")

    op.drop_index("idx_telegram_chats_installation_id", table_name="telegram_chats")
    op.drop_table("telegram_chats")

    op.drop_index("idx_telegram_installations_team_id", table_name="telegram_installations")
    op.drop_table("telegram_installations")
