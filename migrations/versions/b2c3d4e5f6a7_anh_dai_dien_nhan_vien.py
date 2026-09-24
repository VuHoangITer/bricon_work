"""Ảnh đại diện nhân viên

Revision ID: b2c3d4e5f6a7
Revises: a9b8c7d6e5f4
Create Date: 2026-09-24 17:40:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = 'a9b8c7d6e5f4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('nguoi_dung') as batch_op:
        batch_op.add_column(sa.Column('anh_dai_dien', sa.String(length=300), nullable=True))


def downgrade():
    with op.batch_alter_table('nguoi_dung') as batch_op:
        batch_op.drop_column('anh_dai_dien')
