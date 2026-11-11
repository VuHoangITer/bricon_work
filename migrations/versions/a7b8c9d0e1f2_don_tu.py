"""Đơn từ chấm công (6 loại, có duyệt) + cờ trễ/sớm có phép trên chấm công

Revision ID: a7b8c9d0e1f2
Revises: f9a0b1c2d3e4
"""
from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "don_tu",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("loai", sa.String(20), nullable=False),
        sa.Column("nguoi_dung_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id"), nullable=False),
        sa.Column("trang_thai", sa.String(20), nullable=False, server_default="cho_duyet"),
        sa.Column("ngay", sa.Date(), nullable=False),
        sa.Column("ngay_den", sa.Date()),
        sa.Column("buoi", sa.String(10)),
        sa.Column("phan_loai", sa.String(20)),
        sa.Column("gio_tu", sa.Time()),
        sa.Column("gio_den", sa.Time()),
        sa.Column("dia_diem", sa.String(255)),
        sa.Column("noi_dung", sa.Text()),
        sa.Column("ly_do", sa.Text(), nullable=False),
        sa.Column("ban_giao_cho_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id")),
        sa.Column("duong_dan_pdf", sa.String(300)),
        sa.Column("dinh_kem", sa.String(300)),
        sa.Column("ten_dinh_kem", sa.String(255)),
        sa.Column("du_lieu_cu", sa.String(255)),
        sa.Column("nguoi_duyet_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id")),
        sa.Column("y_kien_duyet", sa.Text()),
        sa.Column("duyet_luc", sa.DateTime()),
        sa.Column("tao_luc", sa.DateTime()),
    )
    for cot in ("loai", "nguoi_dung_id", "trang_thai", "ngay", "tao_luc"):
        op.create_index(f"ix_don_tu_{cot}", "don_tu", [cot])

    op.add_column("cham_cong", sa.Column("tre_co_phep", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("cham_cong", sa.Column("som_co_phep", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.add_column("xin_nghi", sa.Column("don_tu_id", sa.Integer(), sa.ForeignKey("don_tu.id")))
    op.create_index("ix_xin_nghi_don_tu_id", "xin_nghi", ["don_tu_id"])


def downgrade():
    op.drop_index("ix_xin_nghi_don_tu_id", table_name="xin_nghi")
    op.drop_column("xin_nghi", "don_tu_id")
    op.drop_column("cham_cong", "som_co_phep")
    op.drop_column("cham_cong", "tre_co_phep")
    for cot in ("loai", "nguoi_dung_id", "trang_thai", "ngay", "tao_luc"):
        op.drop_index(f"ix_don_tu_{cot}", table_name="don_tu")
    op.drop_table("don_tu")
