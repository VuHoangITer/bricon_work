from urllib.parse import urlparse

from flask import (Blueprint, flash, redirect, render_template, request,
                   session, url_for)
from flask_login import current_user, login_required, login_user, logout_user

from extensions import db
from models import NguoiDung

bp = Blueprint("auth", __name__)


def _next_an_toan(dich: str | None) -> str | None:
    """Chỉ cho phép chuyển hướng nội bộ — chặn open redirect."""
    if not dich:
        return None
    p = urlparse(dich)
    if p.netloc or p.scheme:
        return None
    if not dich.startswith("/"):
        return None
    return dich


@bp.route("/dang-nhap", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("tasks.dashboard"))

    dich = _next_an_toan(request.args.get("next"))

    if request.method == "POST":
        ma = (request.form.get("ma_dinh_danh") or "").strip()
        mk = request.form.get("mat_khau") or ""
        nho = request.form.get("nho_toi") == "on"

        nd = NguoiDung.query.filter(
            db.func.lower(NguoiDung.ma_dinh_danh) == ma.lower()
        ).first()

        if not nd or not nd.kiem_mat_khau(mk):
            flash("Mã nhân viên hoặc mật khẩu không đúng.", "error")
            return render_template("login.html", ma_dinh_danh=ma, next=dich)
        if not nd.dang_hoat_dong:
            flash("Tài khoản đã bị khoá. Liên hệ phòng IT.", "error")
            return render_template("login.html", ma_dinh_danh=ma, next=dich)

        login_user(nd, remember=nho)
        session.permanent = True
        dich = _next_an_toan(request.form.get("next")) or dich
        if nd.doi_mat_khau:
            flash("Đặt mật khẩu mới trước khi dùng tiếp.", "info")
            return redirect(url_for("auth.doi_mat_khau"))
        return redirect(dich or url_for("tasks.dashboard"))

    return render_template("login.html", next=dich)


@bp.route("/dang-xuat")
@login_required
def logout():
    logout_user()
    flash("Đã đăng xuất.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/doi-mat-khau", methods=["GET", "POST"])
@login_required
def doi_mat_khau():
    if request.method == "POST":
        cu = request.form.get("mat_khau_cu") or ""
        moi = request.form.get("mat_khau_moi") or ""
        lai = request.form.get("nhac_lai") or ""

        if not current_user.doi_mat_khau and not current_user.kiem_mat_khau(cu):
            flash("Mật khẩu hiện tại không đúng.", "error")
        elif len(moi) < 6:
            flash("Mật khẩu mới cần ít nhất 6 ký tự.", "error")
        elif moi != lai:
            flash("Hai ô mật khẩu mới không khớp.", "error")
        else:
            current_user.dat_mat_khau(moi)
            current_user.doi_mat_khau = False
            db.session.commit()
            flash("Đã đổi mật khẩu.", "success")
            return redirect(url_for("tasks.dashboard"))

    return render_template("doi_mat_khau.html")


@bp.route("/anh-dai-dien", methods=["GET", "POST"])
@login_required
def anh_dai_dien():
    """Nhân viên tự đổi ảnh đại diện của mình (có khung cắt tròn). Trang
    hiển thị nay gộp vào "Hồ sơ của tôi" — GET chuyển thẳng sang đó."""
    import services
    if request.method == "POST":
        if request.form.get("xoa") == "1":
            services.dat_anh_dai_dien(current_user, None)
            db.session.commit()
            flash("Đã bỏ ảnh đại diện.", "success")
        else:
            f = request.files.get("anh")
            loi = services.dat_anh_dai_dien(current_user, f) if f and f.filename else "Chưa chọn ảnh."
            if loi:
                flash(loi, "error")
            else:
                db.session.commit()
                flash("Đã cập nhật ảnh đại diện.", "success")
    return redirect(url_for("auth.ho_so_cua_toi"))


@bp.route("/ho-so-cua-toi", methods=["GET", "POST"])
@login_required
def ho_so_cua_toi():
    """Nhân viên tự xem hồ sơ của mình: vai trò, bộ phận, chức vụ, ngày vào
    làm (chỉ xem — Quản trị sửa), tự sửa SĐT + sinh nhật, và xem (không
    sửa/xoá) giấy tờ nhân sự của CHÍNH mình."""
    import re
    from datetime import date
    from models import LoaiHoSo, ngay_vn_hien_tai

    if request.method == "POST":
        sdt = re.sub(r"[^\d+]", "", request.form.get("so_dien_thoai") or "")
        if sdt and not re.fullmatch(r"\+?\d{9,15}", sdt):
            flash("Số điện thoại không hợp lệ (9–15 chữ số).", "error")
            return redirect(url_for("auth.ho_so_cua_toi"))

        ngay_sinh = None
        raw = (request.form.get("ngay_sinh") or "").strip()
        if raw:
            try:
                ngay_sinh = date.fromisoformat(raw)
            except ValueError:
                flash("Ngày sinh không hợp lệ.", "error")
                return redirect(url_for("auth.ho_so_cua_toi"))
            hom_nay = ngay_vn_hien_tai()
            if ngay_sinh >= hom_nay or ngay_sinh.year < 1940:
                flash("Ngày sinh không hợp lệ.", "error")
                return redirect(url_for("auth.ho_so_cua_toi"))

        current_user.so_dien_thoai = sdt or None
        current_user.ngay_sinh = ngay_sinh
        db.session.commit()
        flash("Đã lưu thông tin cá nhân.", "success")
        return redirect(url_for("auth.ho_so_cua_toi"))

    return render_template("ho_so_cua_toi.html", n=current_user, LoaiHoSo=LoaiHoSo)