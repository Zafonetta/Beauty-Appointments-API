"""Allow guest users without an email address.

Revision ID: a6c12e8f4b31
Revises: 702f3a8a8633
Create Date: 2026-11-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a6c12e8f4b31"
down_revision: Union[str, Sequence[str], None] = "702f3a8a8633"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=120),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=120),
        nullable=False,
    )
