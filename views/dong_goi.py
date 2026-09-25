import os

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import services
from extensions import db
from models import AnhGoiHang, DonShopee, GoiHang, LoaiDinhKem, NguoiDung, TrangThaiDonShopee, gio_vn_hien_tai

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
        f_ma=ma, f_nguoi=nguoi, nhan_vien=nhan_vien, so_cho_dong=_so_cho_dong(),
    )


@bp.context_processor
def _tram_cho_moi_trang():
    """Trạng thái Trạm Shopee cho tab/biểu ngữ ở mọi trang Đóng gói."""
    return {"tram_shopee": services.trang_thai_tram_shopee()}


@bp.route("/tram")
@login_required
def tram():
    """Trạng thái Trạm Shopee + hướng dẫn từng bước lấy lại cookie khi hết hạn."""
    return render_template("dong_goi_tram.html", so_cho_dong=_so_cho_dong())


def _so_cho_dong() -> int:
    return DonShopee.query.filter_by(trang_thai=TrangThaiDonShopee.CHO_DONG).count()


@bp.route("/cho-dong")
@login_required
def cho_dong():
    """Danh sách đơn Shopee do Trạm Shopee đẩy về — tab Chờ đóng (mặc định),
    Đã đóng, Bỏ qua. Bấm 1 đơn để vào quét mã vận đơn + chụp ảnh."""
    tt = request.args.get("tt") or TrangThaiDonShopee.CHO_DONG
    if tt not in TrangThaiDonShopee.NHAN:
        tt = TrangThaiDonShopee.CHO_DONG
    q = DonShopee.query.filter_by(trang_thai=tt)
    if tt == TrangThaiDonShopee.CHO_DONG:
        q = q.order_by(DonShopee.han_giao.is_(None), DonShopee.han_giao, DonShopee.tao_luc)
        ds = q.all()
    else:
        ds = q.order_by(DonShopee.dong_luc.desc().nullslast() if tt == TrangThaiDonShopee.DA_DONG
                        else DonShopee.tao_luc.desc()).limit(100).all()
    dem = {k: DonShopee.query.filter_by(trang_thai=k).count() for k in TrangThaiDonShopee.NHAN}
    return render_template("dong_goi_cho.html", ds=ds, tt=tt, dem=dem,
                           TrangThaiDonShopee=TrangThaiDonShopee,
                           so_cho_dong=dem[TrangThaiDonShopee.CHO_DONG])


@bp.route("/don/<int:id>")
@login_required
def don(id):
    """Mở 1 đơn Shopee: xem sản phẩm cần gói, quét mã vận đơn + chụp ảnh."""
    d = db.session.get(DonShopee, id) or abort(404)
    return render_template("dong_goi_form.html", don=d, TrangThaiDonShopee=TrangThaiDonShopee)


@bp.route("/don/<int:id>/bo-qua", methods=["POST"])
@login_required
def bo_qua_don(id):
    """Quản lý đánh dấu Bỏ qua (đơn huỷ / đóng ngoài hệ thống) — thôi nhắc."""
    if not current_user.la_quan_ly:
        abort(403)
    d = db.session.get(DonShopee, id) or abort(404)
    if d.trang_thai == TrangThaiDonShopee.CHO_DONG:
        d.trang_thai = TrangThaiDonShopee.BO_QUA
        d.dong_luc = gio_vn_hien_tai()
        db.session.commit()
        flash(f"Đã bỏ qua đơn {d.ma_don} — không nhắc nữa.", "success")
    return redirect(url_for("dong_goi.cho_dong"))


@bp.route("/don/<int:id>/mo-lai", methods=["POST"])
@login_required
def mo_lai_don(id):
    if not current_user.la_quan_ly:
        abort(403)
    d = db.session.get(DonShopee, id) or abort(404)
    if d.trang_thai == TrangThaiDonShopee.BO_QUA:
        d.trang_thai = TrangThaiDonShopee.CHO_DONG
        d.dong_luc = None
        db.session.commit()
        flash(f"Đã đưa đơn {d.ma_don} về Chờ đóng.", "success")
    return redirect(url_for("dong_goi.cho_dong", tt=TrangThaiDonShopee.BO_QUA))


@bp.route("/moi")
@login_required
def moi():
    return render_template("dong_goi_form.html")


@bp.route("/luu", methods=["POST"])
@login_required
def luu():
    ma_van_don = (request.form.get("ma_van_don") or "").strip()
    cac_anh = [f for f in request.files.getlist("anh") if f and f.filename]
    don = None
    don_id = request.form.get("don_id", type=int)
    quay_lai = url_for("dong_goi.moi")
    if don_id:
        don = db.session.get(DonShopee, don_id) or abort(404)
        quay_lai = url_for("dong_goi.don", id=don.id)
        if don.trang_thai != TrangThaiDonShopee.CHO_DONG:
            flash(f"Đơn {don.ma_don} đang ở trạng thái \"{don.ten_trang_thai}\" — không đóng lại được.", "error")
            return redirect(quay_lai)

    if not ma_van_don:
        flash("Chưa có mã vận đơn — quét lại hoặc gõ tay vào ô mã.", "error")
        return redirect(quay_lai)
    if don and don.ma_van_don and ma_van_don not in (don.ma_van_don, don.ma_don):
        flash(f"Mã vừa quét ({ma_van_don}) KHÔNG khớp mã vận đơn của đơn này ({don.ma_van_don}) "
              "— kiểm tra lại, có thể đang cầm nhầm phiếu của đơn khác.", "error")
        return redirect(quay_lai)
    if not cac_anh:
        flash("Chưa chụp ảnh kiện hàng đã gói.", "error")
        return redirect(quay_lai)
    for f in cac_anh:
        if services.phan_loai(f.filename, f.mimetype) != LoaiDinhKem.ANH:
            flash("Có file gửi lên không phải ảnh — chụp lại bằng camera.", "error")
            return redirect(quay_lai)

    gh = GoiHang(ma_van_don=ma_van_don[:50], nguoi_goi_id=current_user.id)
    db.session.add(gh)
    db.session.flush()
    for f in cac_anh:
        duong_dan, _ = services.luu_file(f, "goi-hang")
        db.session.add(AnhGoiHang(goi_hang_id=gh.id, duong_dan=duong_dan))
    don = services.gan_don_shopee_cho_goi_hang(gh, don)
    db.session.commit()

    services.bao_goi_hang(gh)
    db.session.commit()  # commit luôn LogZalo mà bao_goi_hang vừa add vào session

    flash(f"Đã ghi nhận: {ma_van_don} — {current_user.ho_ten} ({len(cac_anh)} ảnh)"
          + (f" · đơn Shopee {don.ma_don} chuyển Đã đóng." if don else "."), "success")
    if don_id:
        return redirect(url_for("dong_goi.cho_dong"))
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

    services.mo_lai_don_shopee_cua_goi_hang([gh.id])
    db.session.delete(gh)
    db.session.commit()

    flash("Đã xoá bản ghi và ảnh liên quan.", "success")
    return redirect(url_for("dong_goi.danh_sach"))