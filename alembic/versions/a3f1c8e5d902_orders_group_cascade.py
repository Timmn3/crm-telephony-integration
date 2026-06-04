"""orders.group_id FK: RESTRICT -> CASCADE (для удаления группы)

Revision ID: a3f1c8e5d902
Revises: c7a1e9f4d2b8
Create Date: 2026-06-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3f1c8e5d902'
down_revision: Union[str, None] = 'c7a1e9f4d2b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('orders_group_id_fkey', 'orders', type_='foreignkey')
    op.create_foreign_key(
        'orders_group_id_fkey', 'orders', 'groups',
        ['group_id'], ['id'], ondelete='CASCADE',
    )


def downgrade() -> None:
    op.drop_constraint('orders_group_id_fkey', 'orders', type_='foreignkey')
    op.create_foreign_key(
        'orders_group_id_fkey', 'orders', 'groups',
        ['group_id'], ['id'], ondelete='RESTRICT',
    )
