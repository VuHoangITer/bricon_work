"""Đơn từ chấm công (mục Hành chính nhân sự): 6 loại đơn, nhân viên gửi ->
quản lý duyệt. Riêng Xin nghỉ dùng lại form ký điện tử ở attendance.xin_nghi
(sinh PDF), chỉ khác là giờ phải chờ duyệt mới ghi nhận nghỉ phép."""
from datetime import date, datetime, time

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import services
from extensions import db
from models import (ChamCong, DonTu, LoaiDonTu, NguoiDung, PhanLoaiTreSom, TrangThaiDonTu,
                    VaiTro, gio_vn_hien_tai, ngay_vn_hien_tai)

bp = Blueprint("don_tu", __name__, url_prefix="/don-tu")


# ------------------------------------------------------------------ quyền
def duoc_duyet(nd: NguoiDung, don: DonTu) -> bool:
    """Sếp/Admin duyệt mọi đơn (trừ của chính mình); Quản lý bộ phận duyệt
    đơn của người cùng bộ phận — cùng công thức với duyệt Đề xuất."""
    if don.nguoi_dung_id == nd.id:
        return False
    if nd.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
        return True
    if nd.vai_tro == VaiTro.QUAN_LY and nd.ds_bo_phan_id:
        return nd.cung_bo_phan(don.nguoi_dung)
    return False


def duoc_xem(nd: NguoiDung, don: DonTu) -> bool:
    return don.nguoi_dung_id == nd.id or nd.la_admin_sep or duoc_duyet(nd, don)


def _q_trong_pham_vi():
    """Query đơn của những người current_user quản lý (không gồm chính mình)."""
    q = DonTu.query.filter(DonTu.nguoi_dung_id != current_user.id)
    if current_user.la_admin_sep:
        return q
    if current_user.vai_tro == VaiTro.QUAN_LY and current_user.ds_bo_phan_id:
        return q.join(NguoiDung, DonTu.nguoi_dung_id == NguoiDung.id).filter(
            NguoiDung.thuoc_bo_phan(current_user.ds_bo_phan_id))
    return None


def don_cho_duyet() -> list[DonTu]:
    q = _q_trong_pham_vi()
    if q is None:
        return []
    ds = q.filter(DonTu.trang_thai == TrangThaiDonTu.CHO_DUYET).order_by(DonTu.tao_luc).all()
    return [d for d in ds if duoc_duyet(current_user, d)]


def so_don_cho_duyet() -> int:
    if not current_user.is_authenticated or not current_user.la_quan_ly:
        return 0
    return len(don_cho_duyet())


# ------------------------------------------------------------------ helpers
def _doc_ngay(ten: str) -> date | None:
    try:
        return date.fromisoformat((request.form.get(ten) or "").strip())
    except ValueError:
        return None


def _doc_gio(ten: str) -> time | None:
    raw = (request.form.get(ten) or "").strip()
    if not raw:
        return None
    try:
        h, m = (int(x) for x in raw.split(":")[:2])
        return time(h, m)
    except ValueError:
        return None


def _ca_mac_dinh() -> tuple[str, str]:
    v, r = current_app.config["GIO_VAO"], current_app.config["GIO_RA"]
    return f"{v[0]:02d}:{v[1]:02d}", f"{r[0]:02d}:{r[1]:02d}"


# ------------------------------------------------------------------ trang
@bp.route("/")
@login_required
def danh_sach():
    la_ql = current_user.la_quan_ly
    tab = request.args.get("tab") or ("duyet" if current_user.la_admin_sep else "tao")
    if tab in ("duyet", "da_xu_ly") and not la_ql:
        tab = "tao"
    if tab == "tao" and current_user.la_admin_sep:
        tab = "duyet"

    cua_toi = (DonTu.query.filter_by(nguoi_dung_id=current_user.id)
               .order_by(DonTu.tao_luc.desc()).limit(60).all())
    cho_duyet = don_cho_duyet() if la_ql else []

    da_xu_ly = []
    f_loai = request.args.get("loai", "")
    if tab == "da_xu_ly":
        q = _q_trong_pham_vi()
        if q is not None:
            q = q.filter(DonTu.trang_thai != TrangThaiDonTu.CHO_DUYET)
            if f_loai in LoaiDonTu.NHAN:
                q = q.filter(DonTu.loai == f_loai)
            da_xu_ly = q.order_by(DonTu.tao_luc.desc()).limit(200).all()

    return render_template(
        "don_tu_list.html", tab=tab, cua_toi=cua_toi, cho_duyet=cho_duyet, da_xu_ly=da_xu_ly,
        f_loai=f_loai, LoaiDonTu=LoaiDonTu, TrangThaiDonTu=TrangThaiDonTu,
        duoc_duyet=lambda d: duoc_duyet(current_user, d),
    )


@bp.route("/moi/<loai>", methods=["GET", "POST"])
@login_required
def tao(loai):
    if loai not in LoaiDonTu.NHAN:
        abort(404)
    if current_user.la_admin_sep:
        flash("Sếp/Quản trị không cần gửi đơn từ chấm công.", "info")
        return redirect(url_for("don_tu.danh_sach"))
    if loai == LoaiDonTu.XIN_NGHI:
        return redirect(url_for("attendance.xin_nghi"))

    hom_nay = ngay_vn_hien_tai()
    if request.method == "POST":
        def _loi(msg):
            flash(msg, "error")
            return redirect(url_for("don_tu.tao", loai=loai))

        ngay = _doc_ngay("ngay")
        if not ngay:
            return _loi("Chọn ngày áp dụng.")
        gio_tu, gio_den = _doc_gio("gio_tu"), _doc_gio("gio_den")
        ly_do = (request.form.get("ly_do") or "").strip()
        if not ly_do:
            return _loi("Cần nhập lý do / nội dung.")

        don = DonTu(loai=loai, nguoi_dung_id=current_user.id, ngay=ngay, ly_do=ly_do,
                    gio_tu=gio_tu, gio_den=gio_den)

        if loai == LoaiDonTu.TRE_SOM:
            don.phan_loai = request.form.get("phan_loai")
            if don.phan_loai not in PhanLoaiTreSom.NHAN:
                return _loi("Chọn hình thức: đi trễ hay về sớm.")
            if not gio_tu:
                return _loi("Nhập giờ dự kiến.")
            don.gio_den = None
        elif loai == LoaiDonTu.BO_SUNG_CONG:
            if ngay > hom_nay:
                return _loi("Chỉ bổ sung công cho hôm nay hoặc ngày đã qua.")
            if not (gio_tu or gio_den):
                return _loi("Nhập ít nhất giờ vào hoặc giờ ra cần bổ sung.")
            if gio_tu and gio_den and gio_den <= gio_tu:
                return _loi("Giờ ra phải sau giờ vào.")
            cc = ChamCong.query.filter_by(nguoi_dung_id=current_user.id, ngay=ngay).first()
            if not gio_tu and not (cc and cc.gio_vao):
                return _loi("Ngày này chưa có giờ vào — cần nhập cả giờ vào.")
            don.du_lieu_cu = services.mo_ta_cham_cong(cc)
        elif loai == LoaiDonTu.CONG_TAC:
            don.dia_diem = (request.form.get("dia_diem") or "").strip()[:255]
            if not don.dia_diem:
                return _loi("Nhập địa điểm công tác.")
            if not (gio_tu and gio_den) or gio_den <= gio_tu:
                return _loi("Nhập khung giờ công tác hợp lệ (từ – đến).")
        elif loai == LoaiDonTu.TANG_CA:
            if not (gio_tu and gio_den):
                return _loi("Nhập giờ bắt đầu và kết thúc tăng ca.")
        elif loai == LoaiDonTu.DOI_CA:
            if not (gio_tu and gio_den) or gio_den <= gio_tu:
                return _loi("Nhập ca đề nghị hợp lệ (giờ vào – giờ ra).")
            don.noi_dung = "Ca hiện tại: {} – {}".format(*_ca_mac_dinh())

        f = request.files.get("dinh_kem")
        if f and f.filename:
            don.dinh_kem, _ = services.luu_file(f, "don-tu")
            don.ten_dinh_kem = f.filename[:255]

        db.session.add(don)
        db.session.commit()
        services.bao_don_tu_moi(don)
        db.session.commit()
        flash(f"Đã gửi đơn {don.ten_loai.lower()} — chờ quản lý duyệt.", "success")
        return redirect(url_for("don_tu.chi_tiet", id=don.id))

    ca_vao, ca_ra = _ca_mac_dinh()
    return render_template("don_tu_form.html", loai=loai, LoaiDonTu=LoaiDonTu,
                           PhanLoaiTreSom=PhanLoaiTreSom, hom_nay=hom_nay,
                           ca_vao=ca_vao, ca_ra=ca_ra)


@bp.route("/<int:id>")
@login_required
def chi_tiet(id):
    don = db.session.get(DonTu, id) or abort(404)
    if not duoc_xem(current_user, don):
        abort(403)
    cc = ChamCong.query.filter_by(nguoi_dung_id=don.nguoi_dung_id, ngay=don.ngay).first()
    return render_template("don_tu_detail.html", don=don, cc=cc, TrangThaiDonTu=TrangThaiDonTu,
                           LoaiDonTu=LoaiDonTu, co_the_duyet=duoc_duyet(current_user, don))


@bp.route("/<int:id>/duyet", methods=["POST"])
@login_required
def duyet(id):
    don = db.session.get(DonTu, id) or abort(404)
    if not duoc_duyet(current_user, don):
        abort(403)
    quay_lai = request.form.get("tiep") or url_for("don_tu.chi_tiet", id=don.id)
    if not quay_lai.startswith("/"):
        quay_lai = url_for("don_tu.chi_tiet", id=don.id)
    if don.trang_thai != TrangThaiDonTu.CHO_DUYET:
        flash("Đơn này đã được xử lý rồi.", "info")
        return redirect(quay_lai)

    hanh_dong = request.form.get("hanh_dong")
    if hanh_dong not in ("duyet", "tu_choi"):
        abort(400)
    don.trang_thai = TrangThaiDonTu.DA_DUYET if hanh_dong == "duyet" else TrangThaiDonTu.TU_CHOI
    don.nguoi_duyet_id = current_user.id
    don.nguoi_duyet = current_user
    don.duyet_luc = gio_vn_hien_tai()
    don.y_kien_duyet = (request.form.get("y_kien") or "").strip() or None
    if hanh_dong == "duyet":
        services.ap_dung_don_tu(don)
    db.session.commit()
    services.bao_ket_qua_don_tu(don)
    db.session.commit()
    flash(f"Đã {'duyệt' if hanh_dong == 'duyet' else 'từ chối'} đơn {don.ten_loai.lower()} "
          f"của {don.nguoi_dung.ho_ten}.", "success")
    return redirect(quay_lai)


@bp.route("/<int:id>/huy", methods=["POST"])
@login_required
def huy(id):
    don = db.session.get(DonTu, id) or abort(404)
    if don.nguoi_dung_id != current_user.id:
        abort(403)
    if don.trang_thai != TrangThaiDonTu.CHO_DUYET:
        flash("Chỉ huỷ được đơn đang chờ duyệt.", "error")
    else:
        don.trang_thai = TrangThaiDonTu.DA_HUY
        db.session.commit()
        flash("Đã huỷ đơn.", "success")
    return redirect(url_for("don_tu.chi_tiet", id=don.id))
