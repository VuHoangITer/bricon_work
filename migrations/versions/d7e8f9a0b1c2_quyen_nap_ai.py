"""Quyền riêng nạp kiến thức Trợ lý AI (nguoi_dung.nap_kien_thuc_ai)

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
"""
from alembic import op
import sqlalchemy as sa

revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nguoi_dung", sa.Column("nap_kien_thuc_ai", sa.Boolean(), nullable=False,
                                          server_default=sa.false()))


def downgrade():
    op.drop_column("nguoi_dung", "nap_kien_thuc_ai")
