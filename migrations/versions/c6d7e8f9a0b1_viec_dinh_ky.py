"""Việc định kỳ theo chức vụ (tự giao hằng ngày) + cột cong_viec.viec_dinh_ky_id

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
"""
from alembic import op
import sqlalchemy as sa

revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "viec_dinh_ky",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chuc_vu_id", sa.Integer(), sa.ForeignKey("chuc_vu.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tieu_de", sa.String(255), nullable=False),
        sa.Column("mo_ta", sa.Text()),
        sa.Column("cac_thu", sa.String(20), nullable=False, server_default="0,1,2,3,4,5"),
        sa.Column("gio_han", sa.String(5), nullable=False, server_default="17:00"),
        sa.Column("dang_dung", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tao_luc", sa.DateTime()),
    )
    op.create_index("ix_viec_dinh_ky_chuc_vu_id", "viec_dinh_ky", ["chuc_vu_id"])
    with op.batch_alter_table("cong_viec") as b:
        b.add_column(sa.Column("viec_dinh_ky_id", sa.Integer(), nullable=True))
        b.create_index("ix_cong_viec_viec_dinh_ky_id", ["viec_dinh_ky_id"])
        b.create_foreign_key("fk_cong_viec_viec_dinh_ky", "viec_dinh_ky", ["viec_dinh_ky_id"], ["id"],
                             ondelete="SET NULL")


def downgrade():
    with op.batch_alter_table("cong_viec") as b:
        b.drop_constraint("fk_cong_viec_viec_dinh_ky", type_="foreignkey")
        b.drop_index("ix_cong_viec_viec_dinh_ky_id")
        b.drop_column("viec_dinh_ky_id")
    op.drop_index("ix_viec_dinh_ky_chuc_vu_id", table_name="viec_dinh_ky")
    op.drop_table("viec_dinh_ky")
