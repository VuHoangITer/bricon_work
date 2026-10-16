"""Tạo bảng Thanh toán công nợ + ảnh lệnh chuyển tiền

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
"""
from alembic import op
import sqlalchemy as sa

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "thanh_toan_cong_no",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ten_kh", sa.String(200), nullable=False),
        sa.Column("ma_don", sa.String(50), nullable=False),
        sa.Column("ghi_chu", sa.String(255), nullable=False),
        sa.Column("ghi_chu_chuan", sa.String(255), nullable=False),
        sa.Column("tim_kiem", sa.String(500), nullable=False),
        sa.Column("nguoi_tao_id", sa.Integer(), sa.ForeignKey("nguoi_dung.id"), nullable=False),
        sa.Column("tao_luc", sa.DateTime()),
        sa.Column("sua_luc", sa.DateTime()),
        sa.Column("lich_su", sa.Text()),
    )
    for cot in ("ten_kh", "ma_don", "ghi_chu_chuan", "tim_kiem", "nguoi_tao_id", "tao_luc"):
        op.create_index(f"ix_thanh_toan_cong_no_{cot}", "thanh_toan_cong_no", [cot])

    op.create_table(
        "anh_cong_no",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cong_no_id", sa.Integer(), sa.ForeignKey("thanh_toan_cong_no.id"), nullable=False),
        sa.Column("duong_dan", sa.String(300), nullable=False),
        sa.Column("tao_luc", sa.DateTime()),
    )
    op.create_index("ix_anh_cong_no_cong_no_id", "anh_cong_no", ["cong_no_id"])


def downgrade():
    op.drop_index("ix_anh_cong_no_cong_no_id", table_name="anh_cong_no")
    op.drop_table("anh_cong_no")
    for cot in ("ten_kh", "ma_don", "ghi_chu_chuan", "tim_kiem", "nguoi_tao_id", "tao_luc"):
        op.drop_index(f"ix_thanh_toan_cong_no_{cot}", table_name="thanh_toan_cong_no")
    op.drop_table("thanh_toan_cong_no")
