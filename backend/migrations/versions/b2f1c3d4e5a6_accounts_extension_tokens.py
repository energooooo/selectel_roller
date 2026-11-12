"""accounts and extension tokens

Revision ID: b2f1c3d4e5a6
Revises: 71aed084ae49
Create Date: 2026-11-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.db.models


# revision identifiers, used by Alembic.
revision: str = 'b2f1c3d4e5a6'
down_revision: Union[str, Sequence[str], None] = '71aed084ae49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'extension_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('admin_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('created_at', app.db.models.TZDateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', app.db.models.TZDateTime(timezone=True), nullable=False),
        sa.Column('expires_at', app.db.models.TZDateTime(timezone=True), nullable=False),
        sa.Column('revoked', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['admin_id'], ['admins.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_table(
        'accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.String(length=32), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('password_enc', sa.Text(), nullable=True),
        sa.Column('note', sa.String(length=200), nullable=False),
        sa.Column('onboarded', sa.Boolean(), nullable=False),
        sa.Column('last_login_at', app.db.models.TZDateTime(timezone=True), nullable=True),
        sa.Column('login_stage', sa.String(length=24), nullable=False),
        sa.Column('login_message', sa.Text(), nullable=False),
        sa.Column('login_at', app.db.models.TZDateTime(timezone=True), nullable=True),
        sa.Column('created_at', app.db.models.TZDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_accounts_account_id'), ['account_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_accounts_account_id'))
    op.drop_table('accounts')
    op.drop_table('extension_tokens')
