"""Nhân viên thuộc nhiều bộ phận: bảng bộ phận phụ (kiêm nhiệm)

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
"""
from alembic import op
import sqlalchemy as sa

revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nguoi_dung_bo_phan",
        sa.Column("nguoi_dung_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("bo_phan_id", sa.Integer(), sa.ForeignKey("bo_phan.id", ondelete="CASCADE"),
                  primary_key=True),
    )
    op.create_index("ix_nguoi_dung_bo_phan_bo_phan_id", "nguoi_dung_bo_phan", ["bo_phan_id"])


def downgrade():
    op.drop_index("ix_nguoi_dung_bo_phan_bo_phan_id", table_name="nguoi_dung_bo_phan")
    op.drop_table("nguoi_dung_bo_phan")
