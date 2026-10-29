"""Quy trình nội bộ cho Trợ lý AI (nội dung + nhiều ảnh có nhãn)

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
"""
from alembic import op
import sqlalchemy as sa

revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "quy_trinh",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ten", sa.String(200), nullable=False, unique=True),
        sa.Column("noi_dung", sa.Text()),
        sa.Column("tu_khoa", sa.String(500)),
        sa.Column("tao_luc", sa.DateTime()),
    )
    op.create_table(
        "anh_quy_trinh",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quy_trinh_id", sa.Integer(), sa.ForeignKey("quy_trinh.id"), nullable=False),
        sa.Column("duong_dan", sa.String(500), nullable=False),
        sa.Column("nhan", sa.String(255)),
    )
    op.create_index("ix_anh_quy_trinh_quy_trinh_id", "anh_quy_trinh", ["quy_trinh_id"])


def downgrade():
    op.drop_index("ix_anh_quy_trinh_quy_trinh_id", table_name="anh_quy_trinh")
    op.drop_table("anh_quy_trinh")
    op.drop_table("quy_trinh")
