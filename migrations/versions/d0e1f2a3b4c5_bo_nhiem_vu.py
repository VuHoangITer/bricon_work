"""Bộ nhiệm vụ theo giai đoạn: giai đoạn nhân viên, nguồn việc, mục tiêu
tuần, đề xuất nhận việc, tiêu chí đánh giá

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""
from alembic import op
import sqlalchemy as sa

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nguoi_dung", sa.Column("giai_doan", sa.String(20), nullable=False, server_default="chinh_thuc"))
    op.add_column("nguoi_dung", sa.Column("giai_doan_tu", sa.Date()))
    op.add_column("nguoi_dung", sa.Column("mang_phu_trach", sa.String(255)))

    op.add_column("cong_viec", sa.Column("nguon", sa.String(20), nullable=False, server_default="quan_ly_giao"))
    op.create_index("ix_cong_viec_nguon", "cong_viec", ["nguon"])
    op.execute("UPDATE cong_viec SET nguon = 'hang_ngay' WHERE viec_dinh_ky_id IS NOT NULL")

    op.add_column("viec_dinh_ky", sa.Column("giai_doan", sa.String(20)))
    op.create_index("ix_viec_dinh_ky_giai_doan", "viec_dinh_ky", ["giai_doan"])
    op.add_column("viec_dinh_ky", sa.Column("mang", sa.String(60)))
    op.add_column("viec_dinh_ky", sa.Column("ket_qua", sa.String(255)))
    op.add_column("viec_dinh_ky", sa.Column("minh_chung", sa.String(255)))
    op.add_column("viec_dinh_ky", sa.Column("tu_dong", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("viec_dinh_ky", sa.Column("ma", sa.String(10)))

    op.create_table(
        "muc_tieu_tuan",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nguoi_dung_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tuan", sa.Date(), nullable=False),
        sa.Column("ten", sa.String(200), nullable=False),
        sa.Column("chi_tieu", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("da_dat", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("viec_dinh_ky_id", sa.Integer(), sa.ForeignKey("viec_dinh_ky.id", ondelete="SET NULL")),
        sa.Column("tao_boi_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id")),
        sa.Column("tao_luc", sa.DateTime()),
    )
    op.create_index("ix_muc_tieu_tuan_nguoi_dung_id", "muc_tieu_tuan", ["nguoi_dung_id"])
    op.create_index("ix_muc_tieu_tuan_tuan", "muc_tieu_tuan", ["tuan"])

    op.create_table(
        "de_xuat_nhan_viec",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nguoi_dung_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tieu_de", sa.String(255), nullable=False),
        sa.Column("mo_ta", sa.Text()),
        sa.Column("ket_qua", sa.String(255)),
        sa.Column("han", sa.DateTime(), nullable=False),
        sa.Column("trang_thai", sa.String(20), nullable=False, server_default="cho_duyet"),
        sa.Column("nguoi_duyet_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id")),
        sa.Column("y_kien", sa.Text()),
        sa.Column("duyet_luc", sa.DateTime()),
        sa.Column("cong_viec_id", sa.Integer(), sa.ForeignKey("cong_viec.id", ondelete="SET NULL")),
        sa.Column("tao_luc", sa.DateTime()),
    )
    op.create_index("ix_de_xuat_nhan_viec_nguoi_dung_id", "de_xuat_nhan_viec", ["nguoi_dung_id"])
    op.create_index("ix_de_xuat_nhan_viec_trang_thai", "de_xuat_nhan_viec", ["trang_thai"])

    op.create_table(
        "tieu_chi_danh_gia",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chuc_vu_id", sa.Integer(), sa.ForeignKey("chuc_vu.id", ondelete="CASCADE"), nullable=False),
        sa.Column("giai_doan", sa.String(20), nullable=False),
        sa.Column("khoi_luong", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("chat_luong", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("dung_han", sa.Integer(), nullable=False, server_default="20"),
        sa.UniqueConstraint("chuc_vu_id", "giai_doan", name="uq_tieu_chi_cv_gd"),
    )


def downgrade():
    op.drop_table("tieu_chi_danh_gia")
    op.drop_index("ix_de_xuat_nhan_viec_trang_thai", "de_xuat_nhan_viec")
    op.drop_index("ix_de_xuat_nhan_viec_nguoi_dung_id", "de_xuat_nhan_viec")
    op.drop_table("de_xuat_nhan_viec")
    op.drop_index("ix_muc_tieu_tuan_tuan", "muc_tieu_tuan")
    op.drop_index("ix_muc_tieu_tuan_nguoi_dung_id", "muc_tieu_tuan")
    op.drop_table("muc_tieu_tuan")
    for c in ("ma", "tu_dong", "minh_chung", "ket_qua", "mang"):
        op.drop_column("viec_dinh_ky", c)
    op.drop_index("ix_viec_dinh_ky_giai_doan", "viec_dinh_ky")
    op.drop_column("viec_dinh_ky", "giai_doan")
    op.drop_index("ix_cong_viec_nguon", "cong_viec")
    op.drop_column("cong_viec", "nguon")
    for c in ("mang_phu_trach", "giai_doan_tu", "giai_doan"):
        op.drop_column("nguoi_dung", c)
