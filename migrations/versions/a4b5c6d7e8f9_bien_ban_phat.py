"""Biên bản phạt: danh mục lỗi, biên bản, bằng chứng đính kèm

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
"""
from alembic import op
import sqlalchemy as sa

revision = "a4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "loi_vi_pham",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ten", sa.String(200), nullable=False, unique=True),
        sa.Column("muc_phat", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mo_ta", sa.Text()),
        sa.Column("dang_dung", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tao_luc", sa.DateTime()),
    )
    op.create_table(
        "bien_ban_phat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nguoi_vi_pham_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id"), nullable=False),
        sa.Column("nguoi_lap_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id"), nullable=False),
        sa.Column("loi_id", sa.Integer(), sa.ForeignKey("loi_vi_pham.id", ondelete="SET NULL")),
        sa.Column("ten_loi", sa.String(200), nullable=False),
        sa.Column("ngay_vi_pham", sa.Date(), nullable=False),
        sa.Column("noi_dung", sa.Text(), nullable=False),
        sa.Column("so_tien", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trang_thai", sa.String(20), nullable=False, server_default="cho_ky"),
        sa.Column("chu_ky_lap", sa.String(300)),
        sa.Column("chu_ky_nv", sa.String(300)),
        sa.Column("y_kien_nv", sa.Text()),
        sa.Column("ky_luc", sa.DateTime()),
        sa.Column("xu_ly_boi_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id")),
        sa.Column("xu_ly_luc", sa.DateTime()),
        sa.Column("ly_do_xu_ly", sa.Text()),
        sa.Column("duong_dan_pdf", sa.String(300)),
        sa.Column("tao_luc", sa.DateTime()),
    )
    for cot in ("nguoi_vi_pham_id", "nguoi_lap_id", "ngay_vi_pham", "trang_thai", "tao_luc"):
        op.create_index(f"ix_bien_ban_phat_{cot}", "bien_ban_phat", [cot])
    op.create_table(
        "dinh_kem_bien_ban",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bien_ban_id", sa.Integer(), sa.ForeignKey("bien_ban_phat.id"), nullable=False),
        sa.Column("loai", sa.String(20), nullable=False),
        sa.Column("duong_dan", sa.String(300), nullable=False),
        sa.Column("ten_goc", sa.String(255)),
        sa.Column("kich_thuoc", sa.Integer()),
        sa.Column("mime", sa.String(100)),
        sa.Column("tao_luc", sa.DateTime()),
    )
    op.create_index("ix_dinh_kem_bien_ban_bien_ban_id", "dinh_kem_bien_ban", ["bien_ban_id"])


def downgrade():
    op.drop_index("ix_dinh_kem_bien_ban_bien_ban_id", table_name="dinh_kem_bien_ban")
    op.drop_table("dinh_kem_bien_ban")
    for cot in ("nguoi_vi_pham_id", "nguoi_lap_id", "ngay_vi_pham", "trang_thai", "tao_luc"):
        op.drop_index(f"ix_bien_ban_phat_{cot}", table_name="bien_ban_phat")
    op.drop_table("bien_ban_phat")
    op.drop_table("loi_vi_pham")
