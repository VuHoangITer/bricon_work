"""Hồ sơ nhân sự: ngày vào làm, sinh nhật + bảng giấy tờ nhân viên

Revision ID: a9b8c7d6e5f4
Revises: f8a9b0c1d2e3
Create Date: 2026-09-24 17:30:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'a9b8c7d6e5f4'
down_revision = 'f8a9b0c1d2e3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('nguoi_dung') as batch_op:
        batch_op.add_column(sa.Column('ngay_vao_lam', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('ngay_sinh', sa.Date(), nullable=True))

    op.create_table(
        'ho_so_nhan_vien',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nguoi_dung_id', sa.Integer(), nullable=False),
        sa.Column('loai', sa.String(length=20), nullable=False),
        sa.Column('duong_dan', sa.String(length=300), nullable=False),
        sa.Column('ten_goc', sa.String(length=255), nullable=True),
        sa.Column('kich_thuoc', sa.Integer(), nullable=True),
        sa.Column('mime', sa.String(length=100), nullable=True),
        sa.Column('ghi_chu', sa.String(length=255), nullable=True),
        sa.Column('ngay_ky', sa.Date(), nullable=True),
        sa.Column('ngay_het_han', sa.Date(), nullable=True),
        sa.Column('nguoi_tai_len_id', sa.Integer(), nullable=True),
        sa.Column('tao_luc', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['nguoi_dung_id'], ['nguoi_dung.id']),
        sa.ForeignKeyConstraint(['nguoi_tai_len_id'], ['nguoi_dung.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('ho_so_nhan_vien') as batch_op:
        batch_op.create_index('ix_ho_so_nhan_vien_nguoi_dung_id', ['nguoi_dung_id'])
        batch_op.create_index('ix_ho_so_nhan_vien_loai', ['loai'])


def downgrade():
    with op.batch_alter_table('ho_so_nhan_vien') as batch_op:
        batch_op.drop_index('ix_ho_so_nhan_vien_loai')
        batch_op.drop_index('ix_ho_so_nhan_vien_nguoi_dung_id')
    op.drop_table('ho_so_nhan_vien')
    with op.batch_alter_table('nguoi_dung') as batch_op:
        batch_op.drop_column('ngay_sinh')
        batch_op.drop_column('ngay_vao_lam')
