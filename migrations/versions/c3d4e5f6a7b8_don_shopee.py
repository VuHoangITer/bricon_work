"""Bảng don_shopee — đơn Shopee do Trạm Shopee đẩy sang

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "don_shopee",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ma_don", sa.String(length=40), nullable=False),
        sa.Column("ma_goi", sa.String(length=40), nullable=True),
        sa.Column("ma_van_don", sa.String(length=60), nullable=True),
        sa.Column("khach", sa.String(length=120), nullable=True),
        sa.Column("san_pham", sa.Text(), nullable=True),
        sa.Column("tong_tien", sa.Numeric(14, 0), nullable=True),
        sa.Column("han_giao", sa.DateTime(), nullable=True),
        sa.Column("trang_thai_shopee", sa.String(length=60), nullable=True),
        sa.Column("trang_thai", sa.String(length=20), nullable=False, server_default="cho_dong"),
        sa.Column("goi_hang_id", sa.Integer(), nullable=True),
        sa.Column("dong_luc", sa.DateTime(), nullable=True),
        sa.Column("tao_luc", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_don_shopee_ma_don", "don_shopee", ["ma_don"], unique=True)
    op.create_index("ix_don_shopee_ma_van_don", "don_shopee", ["ma_van_don"])
    op.create_index("ix_don_shopee_trang_thai", "don_shopee", ["trang_thai"])
    op.create_index("ix_don_shopee_goi_hang_id", "don_shopee", ["goi_hang_id"])
    op.create_index("ix_don_shopee_tao_luc", "don_shopee", ["tao_luc"])


def downgrade():
    op.drop_table("don_shopee")
