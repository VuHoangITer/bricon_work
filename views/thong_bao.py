from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import services
from extensions import db
from models import ThongBao

bp = Blueprint("thong_bao", __name__, url_prefix="/thong-bao")


@bp.route("/")
@login_required
def danh_sach():
    ds = ThongBao.query.order_by(ThongBao.tao_luc.desc()).limit(50).all()
    return render_template("thong_bao.html", danh_sach=ds)


@bp.route("/moi", methods=["POST"])
@login_required
def tao_moi():
    if not current_user.la_quan_ly:
        abort(403)

    noi_dung = (request.form.get("noi_dung") or "").strip()
    if not noi_dung:
        flash("Cần nhập nội dung thông báo.", "error")
        return redirect(url_for("thong_bao.danh_sach"))

    tb = ThongBao(noi_dung=noi_dung, nguoi_dang_id=current_user.id)
    db.session.add(tb)
    db.session.flush()  # có tb.id trước khi gửi, phòng khi sau này cần log theo id

    so_nguoi_nhan = services.gui_thong_bao(tb)
    tb.so_nguoi_nhan = so_nguoi_nhan
    db.session.commit()

    flash(f"Đã gửi thông báo tới {so_nguoi_nhan} nhân viên.", "success")
    return redirect(url_for("thong_bao.danh_sach"))
