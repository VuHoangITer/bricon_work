"""Tach san pham goi_hang ra bang rieng (1 don gom nhieu san pham/mau)

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-09-21 16:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7f8a9b0c1d2'
down_revision = 'd6e7f8a9b0c1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'san_pham_goi_hang',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('goi_hang_id', sa.Integer(), nullable=False),
        sa.Column('ten_san_pham', sa.String(length=200), nullable=True),
        sa.Column('ma_mau', sa.String(length=50), nullable=True),
        sa.Column('so_luong', sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(['goi_hang_id'], ['goi_hang.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('san_pham_goi_hang', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_san_pham_goi_hang_goi_hang_id'), ['goi_hang_id'], unique=False)

    # Chuyển dữ liệu: nếu goi_hang đã có bản ghi thật (bản 3-o-rieng chạy
    # trước khi đổi model lần này), copy đúng 1 dòng sản phẩm sang bảng
    # mới cho mỗi bản ghi CÓ điền ít nhất 1 trong 3 ô — không mất dữ liệu
    # các lần gói trước, chỉ là giờ mỗi lần gói có thể thêm dòng khác.
    conn = op.get_bind()
    goi_hang = sa.table(
        'goi_hang',
        sa.column('id', sa.Integer),
        sa.column('ten_san_pham', sa.String),
        sa.column('ma_mau', sa.String),
        sa.column('so_luong', sa.String),
    )
    san_pham_goi_hang = sa.table(
        'san_pham_goi_hang',
        sa.column('goi_hang_id', sa.Integer),
        sa.column('ten_san_pham', sa.String),
        sa.column('ma_mau', sa.String),
        sa.column('so_luong', sa.String),
    )
    for hang in conn.execute(sa.select(goi_hang.c.id, goi_hang.c.ten_san_pham, goi_hang.c.ma_mau, goi_hang.c.so_luong)):
        if hang.ten_san_pham or hang.ma_mau or hang.so_luong:
            conn.execute(san_pham_goi_hang.insert().values(
                goi_hang_id=hang.id, ten_san_pham=hang.ten_san_pham,
                ma_mau=hang.ma_mau, so_luong=hang.so_luong,
            ))

    with op.batch_alter_table('goi_hang', schema=None) as batch_op:
        batch_op.drop_column('so_luong')
        batch_op.drop_column('ma_mau')
        batch_op.drop_column('ten_san_pham')


def downgrade():
    with op.batch_alter_table('goi_hang', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ten_san_pham', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('ma_mau', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('so_luong', sa.String(length=20), nullable=True))

    # Downgrade chỉ giữ lại DÒNG SẢN PHẨM ĐẦU TIÊN của mỗi lần gói (cột cũ
    # chỉ chứa được 1 dòng) — các dòng phụ sẽ mất khi hạ cấp, chấp nhận
    # được vì downgrade chỉ dùng khi cần lùi khẩn cấp, không phải luồng
    # bình thường.
    conn = op.get_bind()
    goi_hang = sa.table(
        'goi_hang', sa.column('id', sa.Integer),
        sa.column('ten_san_pham', sa.String), sa.column('ma_mau', sa.String), sa.column('so_luong', sa.String),
    )
    san_pham_goi_hang = sa.table(
        'san_pham_goi_hang', sa.column('id', sa.Integer), sa.column('goi_hang_id', sa.Integer),
        sa.column('ten_san_pham', sa.String), sa.column('ma_mau', sa.String), sa.column('so_luong', sa.String),
    )
    da_gan = set()
    rows = conn.execute(
        sa.select(
            san_pham_goi_hang.c.goi_hang_id, san_pham_goi_hang.c.ten_san_pham,
            san_pham_goi_hang.c.ma_mau, san_pham_goi_hang.c.so_luong,
        ).order_by(san_pham_goi_hang.c.id)
    )
    for sp in rows:
        if sp.goi_hang_id in da_gan:
            continue
        da_gan.add(sp.goi_hang_id)
        conn.execute(
            goi_hang.update().where(goi_hang.c.id == sp.goi_hang_id)
            .values(ten_san_pham=sp.ten_san_pham, ma_mau=sp.ma_mau, so_luong=sp.so_luong)
        )

    with op.batch_alter_table('san_pham_goi_hang', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_san_pham_goi_hang_goi_hang_id'))

    op.drop_table('san_pham_goi_hang')
