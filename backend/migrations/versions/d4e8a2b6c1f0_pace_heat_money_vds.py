"""регулятор темпа, рейтинг подсетей, учёт IP, доходы, балансы, VDS

Revision ID: d4e8a2b6c1f0
Revises: c3a7b1d9e2f4
Create Date: 2026-11-12 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

import app.db.models

revision: str = 'd4e8a2b6c1f0'
down_revision: Union[str, Sequence[str], None] = 'c3a7b1d9e2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = app.db.models.TZDateTime(timezone=True)

NEW_COLUMNS = {
    'selectel_keys': [
        sa.Column('pace', sa.Float(), nullable=False, server_default='1'),
        sa.Column('pace_reason', sa.String(length=200), nullable=False, server_default=''),
    ],
    'subnets': [
        sa.Column('attempts_total', sa.Integer(), nullable=False, server_default='0'),
    ],
    'caught_ips': [
        sa.Column('client', sa.String(length=120), nullable=False, server_default=''),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('expires_at', TZ, nullable=True),
        sa.Column('expiry_notified_at', TZ, nullable=True),
    ],
}


def upgrade() -> None:
    insp = inspect(op.get_bind())
    for table, cols in NEW_COLUMNS.items():
        existing = {c['name'] for c in insp.get_columns(table)}
        todo = [c for c in cols if c.name not in existing]
        if todo:
            with op.batch_alter_table(table) as batch:
                for col in todo:
                    batch.add_column(col)
    tables = set(insp.get_table_names())
    if 'sales' not in tables:
        op.create_table(
            'sales',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('ts', TZ, nullable=False),
            sa.Column('kind', sa.String(length=16), nullable=False),
            sa.Column('amount', sa.Float(), nullable=False),
            sa.Column('buyer', sa.String(length=120), nullable=False),
            sa.Column('caught_id', sa.Integer(), sa.ForeignKey('caught_ips.id', ondelete='SET NULL'), nullable=True),
            sa.Column('ip', sa.String(length=64), nullable=False),
            sa.Column('note', sa.String(length=300), nullable=False),
            sa.Column('created_at', TZ, nullable=False),
        )
        op.create_index('ix_sales_ts', 'sales', ['ts'])
    if 'account_balances' not in tables:
        op.create_table(
            'account_balances',
            sa.Column('account_id', sa.String(length=32), primary_key=True),
            sa.Column('balance', sa.Float(), nullable=True),
            sa.Column('hours_left', sa.Integer(), nullable=True),
            sa.Column('error', sa.String(length=300), nullable=False),
            sa.Column('checked_at', TZ, nullable=True),
            sa.Column('warned_at', TZ, nullable=True),
        )
    if 'vds' not in tables:
        op.create_table(
            'vds',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('caught_id', sa.Integer(), sa.ForeignKey('caught_ips.id', ondelete='SET NULL'), nullable=True),
            sa.Column('key_id', sa.Integer(), sa.ForeignKey('selectel_keys.id', ondelete='SET NULL'), nullable=True),
            sa.Column('account_id', sa.String(length=32), nullable=False),
            sa.Column('ip', sa.String(length=64), nullable=False),
            sa.Column('region', sa.String(length=16), nullable=False),
            sa.Column('zone', sa.String(length=16), nullable=False),
            sa.Column('name', sa.String(length=64), nullable=False),
            sa.Column('flavor', sa.String(length=64), nullable=False),
            sa.Column('server_id', sa.String(length=64), nullable=False),
            sa.Column('port_id', sa.String(length=64), nullable=False),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('step', sa.String(length=200), nullable=False),
            sa.Column('error', sa.Text(), nullable=False),
            sa.Column('password_enc', sa.Text(), nullable=True),
            sa.Column('month_cost', sa.Float(), nullable=True),
            sa.Column('created_at', TZ, nullable=False),
        )
        op.create_index('ix_vds_caught_id', 'vds', ['caught_id'])


def downgrade() -> None:
    op.drop_table('vds')
    op.drop_table('account_balances')
    op.drop_index('ix_sales_ts', 'sales')
    op.drop_table('sales')
    for table, cols in NEW_COLUMNS.items():
        with op.batch_alter_table(table) as batch:
            for col in cols:
                batch.drop_column(col.name)
