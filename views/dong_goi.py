import os
from itertools import zip_longest

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import services
from extensions import db
from models import AnhGoiHang, GoiHang, LoaiDinhKem, NguoiDung, SanPhamGoiHang

bp = Blueprint("dong_goi", __name__, url_prefix="/dong-goi")


@bp.route("/")
@login_required
def danh_sach():
    """Lịch sử đóng gói + ô tra cứu theo mã vận đơn — đây là chỗ thay thế
    việc lướt lại nhóm Zalo: gõ (hoặc dán) mã vận đơn vào là ra ngay ai
    gói, lúc nào, kèm ảnh. Nhân viên thường chỉ thấy phần mình gói; Quản
    lý/Sếp/Admin thấy toàn bộ, lọc thêm được theo người."""
    ma = (request.args.get("ma") or "").strip()
    nguoi = request.args.get("nguoi", type=int)
    trang = request.args.get("trang", 1, type=int)

    q = GoiHang.query
    if not current_user.la_quan_ly:
        q = q.filter(GoiHang.nguoi_goi_id == current_user.id)
    elif nguoi:
        q = q.filter(GoiHang.nguoi_goi_id == nguoi)
    if ma:
        q = q.filter(GoiHang.ma_van_don.ilike(f"%{ma}%"))
    q = q.order_by(GoiHang.tao_luc.desc())

    phan_trang = q.paginate(page=trang, per_page=30, error_out=False)
    nhan_vien = (NguoiDung.query.filter_by(dang_hoat_dong=True).order_by(NguoiDung.ho_ten).all()
                if current_user.la_quan_ly else [])
    return render_template(
        "dong_goi_list.html", phan_trang=phan_trang, ban_ghi=phan_trang.items,
        f_ma=ma, f_nguoi=nguoi, nhan_vien=nhan_vien,
    )


@bp.route("/moi")
@login_required
def moi():
    return render_template("dong_goi_form.html")


@bp.route("/luu", methods=["POST"])
@login_required
def luu():
    ma_van_don = (request.form.get("ma_van_don") or "").strip()
    cac_anh = [f for f in request.files.getlist("anh") if f and f.filename]

    # 1 đơn có thể gồm nhiều sản phẩm/màu khác nhau — form gửi lên 3 mảng
    # cùng độ dài, ghép theo vị trí (dòng thứ i trên form = phần tử thứ i
    # của cả 3 mảng); zip_longest để không rớt dữ liệu nếu lỡ có mảng
    # ngắn/dài hơn 1 chút do JS. Bỏ qua dòng nào cả 3 ô đều trống (dòng
    # thừa chưa xoá, hoặc dòng đầu tiên nếu nhân viên không ghi gì).
    ds_ten = request.form.getlist("ten_san_pham[]")
    ds_mau = request.form.getlist("ma_mau[]")
    ds_sl = request.form.getlist("so_luong[]")
    dong_san_pham = []
    for ten, mau, sl in zip_longest(ds_ten, ds_mau, ds_sl, fillvalue=""):
        ten = (ten or "").strip()
        mau = (mau or "").strip()
        sl = (sl or "").strip()
        if ten or mau or sl:
            dong_san_pham.append((ten[:200] or None, mau[:50] or None, sl[:20] or None))

    if not ma_van_don:
        flash("Chưa có mã vận đơn — quét lại hoặc gõ tay vào ô mã.", "error")
        return redirect(url_for("dong_goi.moi"))
    if not cac_anh:
        flash("Chưa chụp ảnh kiện hàng đã gói.", "error")
        return redirect(url_for("dong_goi.moi"))
    for f in cac_anh:
        if services.phan_loai(f.filename, f.mimetype) != LoaiDinhKem.ANH:
            flash("Có file gửi lên không phải ảnh — chụp lại bằng camera.", "error")
            return redirect(url_for("dong_goi.moi"))

    gh = GoiHang(ma_van_don=ma_van_don[:50], nguoi_goi_id=current_user.id)
    db.session.add(gh)
    db.session.flush()
    for f in cac_anh:
        duong_dan, _ = services.luu_file(f, "goi-hang")
        db.session.add(AnhGoiHang(goi_hang_id=gh.id, duong_dan=duong_dan))
    for ten, mau, sl in dong_san_pham:
        db.session.add(SanPhamGoiHang(goi_hang_id=gh.id, ten_san_pham=ten, ma_mau=mau, so_luong=sl))
    db.session.commit()

    services.bao_goi_hang(gh)
    db.session.commit()  # commit luôn LogZalo mà bao_goi_hang vừa add vào session

    flash(f"Đã ghi nhận: {ma_van_don} — {current_user.ho_ten} ({len(cac_anh)} ảnh).", "success")
    return redirect(url_for("dong_goi.moi"))


@bp.route("/<int:id>/xoa", methods=["POST"])
@login_required
def xoa(id):
    """Xoá 1 bản ghi đóng gói nhầm/test — chỉ Quản lý trở lên, và chỉ để
    dọn rác, không phải nghiệp vụ chính. Xoá LUÔN file ảnh thật trên đĩa
    (không chỉ xoá dòng DB) — nếu không sẽ để lại rác trong thư mục
    upload mãi mãi vì không ai dọn lại."""
    if not current_user.la_quan_ly:
        abort(403)
    gh = db.session.get(GoiHang, id) or abort(404)

    for a in gh.anh:
        duong_dan_tuyet_doi = os.path.join(current_app.config["UPLOAD_ROOT"], *a.duong_dan.split("/"))
        if os.path.isfile(duong_dan_tuyet_doi):
            os.remove(duong_dan_tuyet_doi)

    db.session.delete(gh)
    db.session.commit()

    flash("Đã xoá bản ghi và ảnh liên quan.", "success")
    return redirect(url_for("dong_goi.danh_sach"))