"""Lưu danh sách người nhận của Thông báo (trước đây chỉ lưu số lượng)

Thông báo cũ: cố gắng dựng lại danh sách từ Log Zalo (log nào còn thì dựng
được, log đã bị dọn thì thôi — trang vẫn hiện số người như cũ).

Revision ID: d1e2f3a4b5c6
Revises: b2c3d4e5f6a7
"""
import json
from datetime import datetime, timedelta

from alembic import op
import sqlalchemy as sa

revision = "d1e2f3a4b5c6"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

TIEU_DE = "THÔNG BÁO NỘI BỘ – CÔNG TY BRICON\n\n"


def upgrade():
    op.add_column("thong_bao", sa.Column("ds_nguoi_nhan", sa.Text(), nullable=True))
    op.add_column("thong_bao", sa.Column("gui_tat_ca", sa.Boolean(), nullable=True, server_default=sa.false()))

    bind = op.get_bind()
    ds_tb = bind.execute(sa.text("SELECT id, noi_dung, tao_luc FROM thong_bao")).fetchall()
    for tb_id, noi_dung, tao_luc in ds_tb:
        if not tao_luc:
            continue
        if isinstance(tao_luc, str):  # SQLite trả chuỗi, PostgreSQL trả datetime
            tao_luc = datetime.fromisoformat(tao_luc)
        rows = bind.execute(sa.text(
            "SELECT DISTINCT n.id, n.ho_ten, n.ma_dinh_danh, l.thanh_cong "
            "FROM log_zalo l JOIN nguoi_dung n ON n.id = l.nguoi_dung_id "
            "WHERE l.noi_dung = :nd AND l.tao_luc BETWEEN :tu AND :den"),
            {"nd": TIEU_DE + (noi_dung or ""), "tu": tao_luc - timedelta(minutes=1),
             "den": tao_luc + timedelta(minutes=15)}).fetchall()
        if not rows:
            continue
        theo_id = {}
        for nid, ten, ma, ok in rows:
            theo_id[nid] = {"id": nid, "ten": ten, "ma": ma, "ok": bool(ok) or theo_id.get(nid, {}).get("ok", False)}
        bind.execute(sa.text("UPDATE thong_bao SET ds_nguoi_nhan = :ds WHERE id = :id"),
                     {"ds": json.dumps(list(theo_id.values()), ensure_ascii=False), "id": tb_id})


def downgrade():
    op.drop_column("thong_bao", "gui_tat_ca")
    op.drop_column("thong_bao", "ds_nguoi_nhan")
