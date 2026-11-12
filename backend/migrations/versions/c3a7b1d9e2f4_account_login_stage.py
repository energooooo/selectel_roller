"""account login progress columns (idempotent)

Добавляет login_stage/login_message/login_at в accounts, если их ещё нет.
Нужна для БД, где ревизия b2f1c3d4e5a6 была применена ДО появления этих колонок
(они дописывались в ту же ревизию). Проверяет наличие колонок и не падает,
если они уже есть (свежая установка получает их сразу из b2f1c3d4e5a6).

Revision ID: c3a7b1d9e2f4
Revises: b2f1c3d4e5a6
Create Date: 2026-11-12 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

import app.db.models


# revision identifiers, used by Alembic.
revision: str = 'c3a7b1d9e2f4'
down_revision: Union[str, Sequence[str], None] = 'b2f1c3d4e5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMNS = {
    'login_stage': sa.Column('login_stage', sa.String(length=24), nullable=False, server_default=''),
    'login_message': sa.Column('login_message', sa.Text(), nullable=False, server_default=''),
    'login_at': sa.Column('login_at', app.db.models.TZDateTime(timezone=True), nullable=True),
}


def _existing_columns() -> set[str]:
    bind = op.get_bind()
    if 'accounts' not in inspect(bind).get_table_names():
        return set()
    return {c['name'] for c in inspect(bind).get_columns('accounts')}


def upgrade() -> None:
    existing = _existing_columns()
    to_add = [col for name, col in COLUMNS.items() if name not in existing]
    if not to_add:
        return
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        for col in to_add:
            batch_op.add_column(col)


def downgrade() -> None:
    existing = _existing_columns()
    to_drop = [name for name in COLUMNS if name in existing]
    if not to_drop:
        return
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        for name in to_drop:
            batch_op.drop_column(name)
