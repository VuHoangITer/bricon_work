"""Quyền riêng quản lý giấy tờ nhân sự (nguoi_dung.quan_ly_giay_to)

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
"""
from alembic import op
import sqlalchemy as sa

revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nguoi_dung", sa.Column("quan_ly_giay_to", sa.Boolean(), nullable=False,
                                          server_default=sa.false()))


def downgrade():
    op.drop_column("nguoi_dung", "quan_ly_giay_to")
