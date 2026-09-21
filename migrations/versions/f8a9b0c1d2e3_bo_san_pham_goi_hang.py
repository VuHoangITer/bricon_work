"""Bo bang san_pham_goi_hang - sep doi y, goi hang chi can ma van don + anh

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-09-21 16:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f8a9b0c1d2e3'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('san_pham_goi_hang', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_san_pham_goi_hang_goi_hang_id'))

    op.drop_table('san_pham_goi_hang')


def downgrade():
    op.create_table(
        'san_pham_goi_hang',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('goi_hang_id', sa.Integer(), nullable=False),
        sa.Column('ten_san_pham', sa.String(length=200), nullable=True),
        sa.Column('ma_mau', sa.String(length=50), nullable=True),
        sa.Column('so_luong', sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(['goi_hang_id'], ['goi_hang.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('san_pham_goi_hang', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_san_pham_goi_hang_goi_hang_id'), ['goi_hang_id'], unique=False)
