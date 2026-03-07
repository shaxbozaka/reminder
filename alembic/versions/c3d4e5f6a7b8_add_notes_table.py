"""add notes table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('notes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('source', sa.Enum('text', 'voice', 'forward', name='notesource'), nullable=False),
        sa.Column('status', sa.Enum('open', 'done', 'ignored', name='notestatus'), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_notes_telegram_id'), 'notes', ['telegram_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_notes_telegram_id'), table_name='notes')
    op.drop_table('notes')
    op.execute("DROP TYPE IF EXISTS notesource")
    op.execute("DROP TYPE IF EXISTS notestatus")
