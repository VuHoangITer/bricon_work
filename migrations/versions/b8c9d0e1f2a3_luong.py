"""Lương: vi_tri_luong + luong_nhan_vien

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "vi_tri_luong",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ten", sa.String(120), nullable=False, unique=True),
        sa.Column("phong_ban", sa.String(120)),
        sa.Column("luong_co_ban", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trach_nhiem", sa.JSON(), nullable=False),
        sa.Column("nang_luc", sa.JSON(), nullable=False),
        sa.Column("khau_tru", sa.JSON(), nullable=False),
        sa.Column("tao_luc", sa.DateTime()),
    )
    op.create_table(
        "luong_nhan_vien",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nguoi_dung_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vi_tri_luong_id", sa.Integer(), sa.ForeignKey("vi_tri_luong.id"), nullable=False),
        sa.Column("tu_ngay", sa.Date(), nullable=False),
        sa.Column("ghi_chu", sa.String(255)),
        sa.Column("tao_boi_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id")),
        sa.Column("tao_luc", sa.DateTime()),
        sa.UniqueConstraint("nguoi_dung_id", "tu_ngay", name="uq_luong_nv_tu_ngay"),
    )
    op.create_index("ix_luong_nhan_vien_nguoi_dung_id", "luong_nhan_vien", ["nguoi_dung_id"])
    op.create_index("ix_luong_nhan_vien_vi_tri_luong_id", "luong_nhan_vien", ["vi_tri_luong_id"])


def downgrade():
    op.drop_index("ix_luong_nhan_vien_vi_tri_luong_id", "luong_nhan_vien")
    op.drop_index("ix_luong_nhan_vien_nguoi_dung_id", "luong_nhan_vien")
    op.drop_table("luong_nhan_vien")
    op.drop_table("vi_tri_luong")
