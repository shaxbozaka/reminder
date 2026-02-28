"""add optional prayer names to enum

Revision ID: a1b2c3d4e5f6
Revises: 53eba8db08bf
Create Date: 2026-02-28 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '53eba8db08bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE prayername ADD VALUE IF NOT EXISTS 'tahajjud'")
    op.execute("ALTER TYPE prayername ADD VALUE IF NOT EXISTS 'duha'")
    op.execute("ALTER TYPE prayername ADD VALUE IF NOT EXISTS 'witr'")
    op.execute("ALTER TYPE prayername ADD VALUE IF NOT EXISTS 'tarawih'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values
    pass
