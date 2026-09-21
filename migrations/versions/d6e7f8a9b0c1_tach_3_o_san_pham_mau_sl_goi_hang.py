"""Bo noi_dung goi_hang, tach thanh 3 o go tay: ten_san_pham, ma_mau, so_luong

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-09-21 15:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd6e7f8a9b0c1'
down_revision = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('goi_hang', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ten_san_pham', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('ma_mau', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('so_luong', sa.String(length=20), nullable=True))

    # Giữ lại dữ liệu nội dung cũ (nhân viên đã gõ tay/tự đọc trước đây)
    # bằng cách chép nguyên vào ten_san_pham thay vì xoá mất — không tự
    # tách được 3 phần vì noi_dung là 1 đoạn văn bản tự do, nhưng còn hơn
    # mất trắng dữ liệu cũ.
    goi_hang = sa.table(
        'goi_hang',
        sa.column('id', sa.Integer),
        sa.column('noi_dung', sa.Text),
        sa.column('ten_san_pham', sa.String),
    )
    conn = op.get_bind()
    conn.execute(
        goi_hang.update()
        .where(goi_hang.c.noi_dung.isnot(None))
        .values(ten_san_pham=goi_hang.c.noi_dung)
    )

    with op.batch_alter_table('goi_hang', schema=None) as batch_op:
        batch_op.drop_column('noi_dung')


def downgrade():
    with op.batch_alter_table('goi_hang', schema=None) as batch_op:
        batch_op.add_column(sa.Column('noi_dung', sa.Text(), nullable=True))

    goi_hang = sa.table(
        'goi_hang',
        sa.column('id', sa.Integer),
        sa.column('noi_dung', sa.Text),
        sa.column('ten_san_pham', sa.String),
    )
    conn = op.get_bind()
    conn.execute(
        goi_hang.update()
        .where(goi_hang.c.ten_san_pham.isnot(None))
        .values(noi_dung=goi_hang.c.ten_san_pham)
    )

    with op.batch_alter_table('goi_hang', schema=None) as batch_op:
        batch_op.drop_column('so_luong')
        batch_op.drop_column('ma_mau')
        batch_op.drop_column('ten_san_pham')
