"""add pending_users

Revision ID: c7a1e9f4d2b8
Revises: 2b0749445b51
Create Date: 2026-06-04 11:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a1e9f4d2b8'
down_revision: Union[str, None] = '2b0749445b51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pending_users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tg_id', sa.BigInteger(), nullable=False),
        sa.Column('tg_username', sa.String(length=255), nullable=True),
        sa.Column('full_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pending_users_tg_id'), 'pending_users', ['tg_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_pending_users_tg_id'), table_name='pending_users')
    op.drop_table('pending_users')
