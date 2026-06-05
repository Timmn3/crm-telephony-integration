"""rename user_role enum values: admin->director, manager->admin, operator->manager

Revision ID: d4b2f7e1c309
Revises: a3f1c8e5d902
Create Date: 2026-06-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd4b2f7e1c309'
down_revision: Union[str, None] = 'a3f1c8e5d902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Порядок важен: сначала освобождаем имя 'admin', затем 'manager'.
    op.execute("ALTER TYPE user_role RENAME VALUE 'admin' TO 'director'")
    op.execute("ALTER TYPE user_role RENAME VALUE 'manager' TO 'admin'")
    op.execute("ALTER TYPE user_role RENAME VALUE 'operator' TO 'manager'")


def downgrade() -> None:
    op.execute("ALTER TYPE user_role RENAME VALUE 'manager' TO 'operator'")
    op.execute("ALTER TYPE user_role RENAME VALUE 'admin' TO 'manager'")
    op.execute("ALTER TYPE user_role RENAME VALUE 'director' TO 'admin'")
