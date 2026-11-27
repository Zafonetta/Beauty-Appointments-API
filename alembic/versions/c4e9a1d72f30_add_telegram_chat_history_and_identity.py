"""Add Telegram identity and persistent chat history.

Revision ID: c4e9a1d72f30
Revises: 024c14f03157
Create Date: 2026-11-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4e9a1d72f30"
down_revision: Union[str, Sequence[str], None] = "024c14f03157"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("telegram_user_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_users_telegram_user_id",
        "users",
        ["telegram_user_id"],
        unique=True,
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_id", "chat_messages", ["id"], unique=False)
    op.create_index(
        "ix_chat_messages_telegram_user_id",
        "chat_messages",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_messages_created_at",
        "chat_messages",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
    op.drop_index(
        "ix_chat_messages_telegram_user_id",
        table_name="chat_messages",
    )
    op.drop_index("ix_chat_messages_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_column("users", "telegram_user_id")
