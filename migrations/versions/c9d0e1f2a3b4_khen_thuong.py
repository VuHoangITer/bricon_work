"""Khen thưởng

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""
from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "khen_thuong",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nguoi_dung_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ngay", sa.Date(), nullable=False),
        sa.Column("so_tien", sa.Integer(), nullable=False),
        sa.Column("ly_do", sa.Text(), nullable=False),
        sa.Column("nguoi_tao_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id"), nullable=False),
        sa.Column("tao_luc", sa.DateTime()),
    )
    op.create_index("ix_khen_thuong_nguoi_dung_id", "khen_thuong", ["nguoi_dung_id"])
    op.create_index("ix_khen_thuong_ngay", "khen_thuong", ["ngay"])


def downgrade():
    op.drop_index("ix_khen_thuong_ngay", "khen_thuong")
    op.drop_index("ix_khen_thuong_nguoi_dung_id", "khen_thuong")
    op.drop_table("khen_thuong")
