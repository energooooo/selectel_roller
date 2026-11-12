"""пользователи с заявками и кабинеты: роль/статус у админов, владелец у записей

Существующие админы становятся владельцами (role=owner, status=active), все текущие
записи переходят в кабинет первого из них. Прокси и подсети теперь уникальны внутри
кабинета, а не на всю панель.

Revision ID: e5f1a9c3b7d2
Revises: d4e8a2b6c1f0
Create Date: 2026-11-13 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

import app.db.models

revision: str = 'e5f1a9c3b7d2'
down_revision: Union[str, Sequence[str], None] = 'd4e8a2b6c1f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = app.db.models.TZDateTime(timezone=True)
NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
OWNED = ["accounts", "proxies", "selectel_keys", "subnets", "caught_ips", "sales", "vds"]

ADMIN_COLUMNS = [
    sa.Column('role', sa.String(16), nullable=False, server_default='owner'),
    sa.Column('status', sa.String(16), nullable=False, server_default='active'),
    sa.Column('description', sa.String(500), nullable=False, server_default=''),
    sa.Column('reg_ip', sa.String(64), nullable=False, server_default=''),
    sa.Column('reg_user_agent', sa.String(300), nullable=False, server_default=''),
    sa.Column('decided_at', TZ, nullable=True),
    sa.Column('decided_by', sa.String(64), nullable=False, server_default=''),
    sa.Column('tg_message_id', sa.Integer(), nullable=True),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    existing = {c['name'] for c in insp.get_columns('admins')}
    todo = [c for c in ADMIN_COLUMNS if c.name not in existing]
    if todo:
        with op.batch_alter_table('admins') as b:
            for c in todo:
                b.add_column(c)

    for table in OWNED:
        cols = {c['name'] for c in inspect(bind).get_columns(table)}
        if 'owner_id' in cols:
            continue
        with op.batch_alter_table(table, naming_convention=NAMING, recreate="always") as b:
            b.add_column(sa.Column('owner_id', sa.Integer(), nullable=True))
            b.create_foreign_key(f'fk_{table}_owner_id_admins', 'admins', ['owner_id'], ['id'], ondelete='SET NULL')
            b.create_index(f'ix_{table}_owner_id', ['owner_id'])
            if table == 'subnets':
                b.drop_constraint('uq_subnets_cidr', type_='unique')
                b.create_unique_constraint('uq_subnets_owner_cidr', ['owner_id', 'cidr'])
            if table == 'proxies':
                b.drop_constraint('uq_proxies_fingerprint', type_='unique')
                b.create_unique_constraint('uq_proxies_owner_fingerprint', ['owner_id', 'fingerprint'])

    first = bind.execute(sa.text("SELECT min(id) FROM admins")).scalar()
    if first is not None:
        for table in OWNED:
            bind.execute(sa.text(f"UPDATE {table} SET owner_id = :o WHERE owner_id IS NULL"), {"o": first})


def downgrade() -> None:
    for table in OWNED:
        with op.batch_alter_table(table, recreate="always") as b:
            if table == 'subnets':
                b.drop_constraint('uq_subnets_owner_cidr', type_='unique')
                b.create_unique_constraint('uq_subnets_cidr', ['cidr'])
            if table == 'proxies':
                b.drop_constraint('uq_proxies_owner_fingerprint', type_='unique')
                b.create_unique_constraint('uq_proxies_fingerprint', ['fingerprint'])
            b.drop_index(f'ix_{table}_owner_id')
            b.drop_column('owner_id')
    with op.batch_alter_table('admins') as b:
        for c in ADMIN_COLUMNS:
            b.drop_column(c.name)
