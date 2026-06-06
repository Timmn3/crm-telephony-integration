"""add mango_extension to users

Revision ID: e5c3a1b8f076
Revises: d4b2f7e1c309
Create Date: 2026-06-06 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e5c3a1b8f076'
down_revision: Union[str, None] = 'd4b2f7e1c309'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('mango_extension', sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'mango_extension')
