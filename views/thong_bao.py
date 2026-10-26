from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import services
from extensions import db
from models import NguoiDung, ThongBao, VaiTro

bp = Blueprint("thong_bao", __name__, url_prefix="/thong-bao")


def _nguoi_nhan_hop_le():
    """Người được phép gửi thông báo tới: Admin/Ban giám đốc = toàn công ty;
    Quản lý bộ phận = chỉ nhân viên trong bộ phận mình."""
    ds = services.danh_sach_nhan_vien_nhan_thong_bao()
    if current_user.la_admin_sep:
        return ds
    return [n for n in ds if n.id != current_user.id and current_user.trong_pham_vi(n)]


def _duoc_xem(tb: ThongBao) -> bool:
    """Admin/Ban giám đốc xem hết. Người khác chỉ xem thông báo mình gửi,
    mình nhận; Quản lý bộ phận xem thêm thông báo gửi tới người trong bộ
    phận mình (thông báo riêng như biên bản vi phạm không lộ ra ngoài)."""
    if current_user.la_admin_sep or tb.nguoi_dang_id == current_user.id:
        return True
    ids = {n.get("id") for n in tb.nguoi_nhan}
    if current_user.id in ids:
        return True
    if current_user.vai_tro == VaiTro.QUAN_LY and current_user.ds_bo_phan_id and ids:
        return NguoiDung.query.filter(NguoiDung.id.in_(ids),
                                      NguoiDung.thuoc_bo_phan(current_user.ds_bo_phan_id)).first() is not None
    return False


@bp.route("/")
@login_required
def danh_sach():
    ds = [tb for tb in ThongBao.query.order_by(ThongBao.tao_luc.desc()).limit(300).all()
          if _duoc_xem(tb)][:50]
    nhan_vien = _nguoi_nhan_hop_le() if current_user.la_quan_ly else []
    return render_template("thong_bao.html", danh_sach=ds, nhan_vien=nhan_vien)


@bp.route("/moi", methods=["POST"])
@login_required
def tao_moi():
    if not current_user.la_quan_ly:
        abort(403)

    noi_dung = (request.form.get("noi_dung") or "").strip()
    if not noi_dung:
        flash("Cần nhập nội dung thông báo.", "error")
        return redirect(url_for("thong_bao.danh_sach"))

    hop_le = {nv.id: nv for nv in _nguoi_nhan_hop_le()}
    if request.form.get("gui_tat_ca") == "1":
        nguoi_nhan = list(hop_le.values())
    else:
        ids = []
        for i in request.form.getlist("nguoi_nhan_id"):
            try:
                ids.append(int(i))
            except ValueError:
                pass
        nguoi_nhan = [hop_le[i] for i in ids if i in hop_le]

    if not nguoi_nhan:
        flash("Cần chọn ít nhất 1 người nhận.", "error")
        return redirect(url_for("thong_bao.danh_sach"))

    tb = ThongBao(noi_dung=noi_dung, nguoi_dang_id=current_user.id,
                  gui_tat_ca=request.form.get("gui_tat_ca") == "1")
    db.session.add(tb)
    db.session.flush()  # có tb.id trước khi gửi, phòng khi sau này cần log theo id

    so_nguoi_nhan = services.gui_thong_bao(tb, nguoi_nhan)
    tb.so_nguoi_nhan = so_nguoi_nhan
    db.session.commit()

    loi = tb.nguoi_nhan_loi
    if loi:
        flash(f"Đã gửi thông báo tới {so_nguoi_nhan - len(loi)}/{so_nguoi_nhan} nhân viên. "
              f"Chưa gửi được cho: {', '.join(n['ten'] for n in loi)} (chưa có Zalo hoặc lỗi bot).", "error")
    else:
        flash(f"Đã gửi thông báo tới {so_nguoi_nhan} nhân viên.", "success")
    return redirect(url_for("thong_bao.danh_sach"))