"""Quy trình: phạm vi xem (mọi người / chỉ quản lý) + tệp đính kèm (Word, PDF…)

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
"""
from alembic import op
import sqlalchemy as sa

revision = "f9a0b1c2d3e4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quy_trinh", sa.Column("pham_vi", sa.String(20), nullable=False, server_default="tat_ca"))
    op.create_table(
        "tep_quy_trinh",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quy_trinh_id", sa.Integer(), sa.ForeignKey("quy_trinh.id"), nullable=False),
        sa.Column("duong_dan", sa.String(500), nullable=False),
        sa.Column("ten_goc", sa.String(255), nullable=False),
        sa.Column("kich_thuoc", sa.Integer()),
        sa.Column("tao_luc", sa.DateTime()),
    )
    op.create_index("ix_tep_quy_trinh_quy_trinh_id", "tep_quy_trinh", ["quy_trinh_id"])


def downgrade():
    op.drop_index("ix_tep_quy_trinh_quy_trinh_id", table_name="tep_quy_trinh")
    op.drop_table("tep_quy_trinh")
    with op.batch_alter_table("quy_trinh") as b:
        b.drop_column("pham_vi")
