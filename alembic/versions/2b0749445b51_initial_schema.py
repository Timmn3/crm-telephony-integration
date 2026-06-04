"""initial schema (v2: groups + amo_tokens)

Revision ID: 2b0749445b51
Revises:
Create Date: 2026-05-30 23:25:04.726652

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2b0749445b51'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('city', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tg_id', sa.BigInteger(), nullable=False),
        sa.Column('tg_username', sa.String(length=255), nullable=True),
        sa.Column('full_name', sa.String(length=255), nullable=False),
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column('role', sa.Enum('admin', 'operator', 'manager', name='user_role'), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_tg_id'), 'users', ['tg_id'], unique=True)
    op.create_table(
        'orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('amo_lead_id', sa.BigInteger(), nullable=False),
        sa.Column('client_name', sa.String(length=255), nullable=False),
        sa.Column('client_phone', sa.String(length=32), nullable=False),
        sa.Column('client_address', sa.String(length=512), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('operator_tg_id', sa.BigInteger(), nullable=False),
        sa.Column('manager_tg_id', sa.BigInteger(), nullable=True),
        sa.Column(
            'status',
            sa.Enum(
                'sent', 'call_requested', 'call_approved',
                'call_in_progress', 'completed', 'cancelled',
                name='order_status',
            ),
            nullable=False,
        ),
        sa.Column('tg_message_id', sa.BigInteger(), nullable=True),
        sa.Column('tg_chat_id', sa.BigInteger(), nullable=True),
        sa.Column('call_requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('call_approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_orders_amo_lead_id'), 'orders', ['amo_lead_id'], unique=False)
    op.create_index(op.f('ix_orders_status'), 'orders', ['status'], unique=False)
    op.create_table(
        'call_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('manager_tg_id', sa.BigInteger(), nullable=False),
        sa.Column('mango_command_id', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('duration', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_call_log_order_id'), 'call_log', ['order_id'], unique=False)
    op.create_table(
        'amo_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('access_token', sa.Text(), nullable=False),
        sa.Column('refresh_token', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('amo_tokens')
    op.drop_index(op.f('ix_call_log_order_id'), table_name='call_log')
    op.drop_table('call_log')
    op.drop_index(op.f('ix_orders_status'), table_name='orders')
    op.drop_index(op.f('ix_orders_amo_lead_id'), table_name='orders')
    op.drop_table('orders')
    op.drop_index(op.f('ix_users_tg_id'), table_name='users')
    op.drop_table('users')
    op.drop_table('groups')
    # Удаляем native enum-типы PostgreSQL, иначе повторный upgrade упадёт.
    sa.Enum(name='order_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='user_role').drop(op.get_bind(), checkfirst=True)
