import base64
import binascii
import os
from datetime import date, datetime, timedelta

from flask import (Blueprint, Response, abort, current_app, flash, redirect,
                   render_template, request, send_file, url_for)
from flask_login import current_user, login_required

import services
from extensions import db
from models import BuoiNghi, ChamCong, DiemChamCong, NguoiDung, VaiTro, XinNghi, gio_vn_hien_tai, ngay_vn_hien_tai

bp = Blueprint("attendance", __name__, url_prefix="/cham-cong")


def _toa_do_tu_form():
    try:
        lat = float(request.form["lat"])
        lng = float(request.form["lng"])
    except (KeyError, ValueError, TypeError):
        return None, None, None
    do_chinh_xac = request.form.get("do_chinh_xac", type=float) or 9999.0
    return lat, lng, do_chinh_xac


def _thong_bao_ngoai_pham_vi(diem, kc) -> str:
    o_dau = f" — {diem.ten}" if diem else ""
    return (f"Vị trí ngoài phạm vi cho phép (cách {kc:.0f}m điểm gần nhất{o_dau}). "
            f"Ra đúng khu vực rồi chấm công lại.")


@bp.route("/")
@login_required
def trang_cham_cong():
    if current_user.la_admin_sep:
        # Sếp/Quản trị không tự chấm công — bấm vào đây thấy luôn bảng công.
        return redirect(url_for("attendance.bang_cong"))

    hom_nay = ChamCong.query.filter_by(
        nguoi_dung_id=current_user.id, ngay=ngay_vn_hien_tai()
    ).first()
    lich_su = (
        ChamCong.query.filter(
            ChamCong.nguoi_dung_id == current_user.id,
            ChamCong.ngay >= ngay_vn_hien_tai() - timedelta(days=30),
        )
        .order_by(ChamCong.ngay.desc())
        .all()
    )
    nghi_gan_day = (
        XinNghi.query.filter(
            XinNghi.nguoi_dung_id == current_user.id,
            XinNghi.ngay >= ngay_vn_hien_tai() - timedelta(days=30),
        )
        .order_by(XinNghi.ngay.desc())
        .all()
    )
    nghi_theo_ngay = {x.ngay: x for x in nghi_gan_day}
    return render_template(
        "attendance.html",
        hom_nay=hom_nay,
        lich_su=lich_su,
        nghi_theo_ngay=nghi_theo_ngay,
        diems=DiemChamCong.query.filter_by(dang_hoat_dong=True).all(),
        la_hom_nay_nghi=services.la_hom_nay_nghi(),
    )


@bp.route("/vao", methods=["POST"])
@login_required
def cham_cong_vao():
    if current_user.la_admin_sep:
        flash("Sếp/Quản trị không cần chấm công.", "info")
        return redirect(url_for("attendance.bang_cong"))

    lat, lng, do_chinh_xac = _toa_do_tu_form()
    if lat is None:
        flash("Không lấy được vị trí. Bật GPS rồi thử lại.", "error")
        return redirect(url_for("attendance.trang_cham_cong"))
    if do_chinh_xac > current_app.config["GPS_ACCURACY_MAX"]:
        flash(f"Sai số GPS quá lớn ({do_chinh_xac:.0f}m). Ra chỗ thoáng rồi thử lại.", "error")
        return redirect(url_for("attendance.trang_cham_cong"))

    if ChamCong.query.filter_by(nguoi_dung_id=current_user.id, ngay=ngay_vn_hien_tai()).first():
        flash("Hôm nay bạn đã chấm công vào rồi.", "info")
        return redirect(url_for("attendance.trang_cham_cong"))

    diem, kc, trong_pham_vi = services.diem_gan_nhat(lat, lng)
    if not trong_pham_vi:
        flash(_thong_bao_ngoai_pham_vi(diem, kc), "error")
        return redirect(url_for("attendance.trang_cham_cong"))

    bay_gio = gio_vn_hien_tai()
    # Có nghỉ phép buổi sáng (hoặc cả ngày) hôm nay -> so trễ với giờ bắt
    # đầu buổi chiều, dùng mức trễ cho phép riêng của nửa ngày (5 phút).
    # Có nghỉ phép buổi chiều -> vẫn so với giờ vào chuẩn buổi sáng như
    # thường, nhưng cũng chỉ được trễ 5 phút thay vì 10 phút ngày thường.
    if services.co_nghi_phep_buoi(current_user.id, ngay_vn_hien_tai(), BuoiNghi.SANG):
        tre, phut_tre = services.tinh_di_tre(
            bay_gio, current_app.config["GIO_BAT_DAU_CHIEU"],
            current_app.config["PHUT_TRE_CHO_PHEP_NUA_NGAY"],
        )
    elif services.co_nghi_phep_buoi(current_user.id, ngay_vn_hien_tai(), BuoiNghi.CHIEU):
        tre, phut_tre = services.tinh_di_tre(
            bay_gio, current_app.config["GIO_VAO"],
            current_app.config["PHUT_TRE_CHO_PHEP_NUA_NGAY"],
        )
    else:
        tre, phut_tre = services.tinh_di_tre(bay_gio)

    cc = ChamCong(
        nguoi_dung_id=current_user.id, ngay=ngay_vn_hien_tai(), gio_vao=bay_gio,
        lat_vao=lat, lng_vao=lng, do_chinh_xac_vao=do_chinh_xac,
        diem_vao_id=diem.id if diem else None,
        khoang_cach_vao=int(kc) if kc is not None else None,
        di_tre=tre, so_phut_tre=phut_tre,
        ip=request.headers.get("X-Forwarded-For", request.remote_addr),
        user_agent=(request.user_agent.string or "")[:300],
        nghi_ngo=services.trung_toa_do_dang_ngo(current_user.id, lat, lng),
        ghi_chu=(request.form.get("ghi_chu") or "").strip() or None,
    )
    db.session.add(cc)
    db.session.commit()

    msg = f"Đã chấm công vào lúc {bay_gio:%H:%M}."
    if tre:
        msg += f" Ghi nhận đi trễ {phut_tre} phút."
    flash(msg, "warning" if tre else "success")

    if cc.nghi_ngo:
        services.gui_nhom_ql(
            f"⚠️ Chấm công bất thường\n{current_user.ho_ten} ({current_user.ma_dinh_danh}) "
            f"lúc {bay_gio:%H:%M %d/%m}\nLý do: toạ độ trùng khít nhiều ngày (nghi fake GPS)"
        )
        db.session.commit()

    return redirect(url_for("attendance.trang_cham_cong"))


@bp.route("/ra", methods=["POST"])
@login_required
def cham_cong_ra():
    if current_user.la_admin_sep:
        flash("Sếp/Quản trị không cần chấm công.", "info")
        return redirect(url_for("attendance.bang_cong"))

    cc = ChamCong.query.filter_by(nguoi_dung_id=current_user.id, ngay=ngay_vn_hien_tai()).first()
    if not cc or not cc.gio_vao:
        flash("Chưa có chấm công vào cho hôm nay.", "error")
        return redirect(url_for("attendance.trang_cham_cong"))
    if cc.gio_ra:
        flash("Hôm nay bạn đã chấm công ra rồi.", "info")
        return redirect(url_for("attendance.trang_cham_cong"))

    lat, lng, do_chinh_xac = _toa_do_tu_form()
    if lat is None:
        flash("Không lấy được vị trí. Bật GPS rồi thử lại.", "error")
        return redirect(url_for("attendance.trang_cham_cong"))

    diem, kc, trong_pham_vi = services.diem_gan_nhat(lat, lng)
    if not trong_pham_vi:
        flash(_thong_bao_ngoai_pham_vi(diem, kc), "error")
        return redirect(url_for("attendance.trang_cham_cong"))

    bay_gio = gio_vn_hien_tai()
    # Có nghỉ phép buổi chiều (hoặc cả ngày) hôm nay -> so về sớm với giờ
    # kết thúc buổi sáng (11h30 mặc định) thay vì giờ ra chuẩn cả ngày, vì
    # buổi chiều đã được duyệt nghỉ, chỉ cần làm đủ buổi sáng thôi.
    if services.co_nghi_phep_buoi(current_user.id, ngay_vn_hien_tai(), BuoiNghi.CHIEU):
        som, phut_som = services.tinh_ve_som(bay_gio, current_app.config["GIO_KET_THUC_SANG"])
    else:
        som, phut_som = services.tinh_ve_som(bay_gio)

    cc.gio_ra = bay_gio
    cc.lat_ra, cc.lng_ra, cc.do_chinh_xac_ra = lat, lng, do_chinh_xac
    cc.diem_ra_id = diem.id if diem else None
    cc.khoang_cach_ra = int(kc) if kc is not None else None
    cc.ve_som, cc.so_phut_som = som, phut_som
    db.session.commit()

    msg = f"Đã chấm công ra lúc {bay_gio:%H:%M}."
    if som:
        msg += f" Ghi nhận về sớm {phut_som} phút."
    flash(msg, "warning" if som else "success")
    return redirect(url_for("attendance.trang_cham_cong"))


# --------------------------------------------------------------- xin nghỉ
@bp.route("/xin-nghi", methods=["GET", "POST"])
@login_required
def xin_nghi():
    if current_user.la_admin_sep:
        flash("Sếp/Quản trị không cần xin nghỉ phép.", "info")
        return redirect(url_for("attendance.bang_cong"))

    if request.method == "POST":
        try:
            ngay_dau = date.fromisoformat(request.form.get("ngay_dau", ""))
        except ValueError:
            flash("Ngày bắt đầu không hợp lệ.", "error")
            return redirect(url_for("attendance.xin_nghi"))
        try:
            ngay_cuoi = date.fromisoformat(request.form.get("ngay_cuoi") or request.form.get("ngay_dau"))
        except ValueError:
            flash("Ngày kết thúc không hợp lệ.", "error")
            return redirect(url_for("attendance.xin_nghi"))
        if ngay_cuoi < ngay_dau:
            flash("Ngày kết thúc phải từ ngày bắt đầu trở đi.", "error")
            return redirect(url_for("attendance.xin_nghi"))

        buoi = request.form.get("buoi") or BuoiNghi.CA_NGAY
        if buoi not in BuoiNghi.NHAN:
            buoi = BuoiNghi.CA_NGAY
        if ngay_dau != ngay_cuoi:
            buoi = BuoiNghi.CA_NGAY  # nghỉ nhiều ngày thì luôn tính cả ngày mỗi ngày

        ly_do = (request.form.get("ly_do") or "").strip()
        if not ly_do:
            flash("Cần nhập lý do nghỉ.", "error")
            return redirect(url_for("attendance.xin_nghi"))

        so_dien_thoai = (request.form.get("so_dien_thoai") or "").strip()
        if not so_dien_thoai:
            flash("Cần nhập số điện thoại liên hệ để ghi vào đơn xin nghỉ.", "error")
            return redirect(url_for("attendance.xin_nghi"))
        if current_user.so_dien_thoai != so_dien_thoai:
            current_user.so_dien_thoai = so_dien_thoai  # đồng bộ luôn vào hồ sơ cho lần sau

        ban_giao_cho = None
        ban_giao_cho_id_raw = (request.form.get("ban_giao_cho_id") or "").strip()
        if ban_giao_cho_id_raw:
            ban_giao_cho = db.session.get(NguoiDung, int(ban_giao_cho_id_raw))
            if not ban_giao_cho or ban_giao_cho.id == current_user.id or not ban_giao_cho.dang_hoat_dong:
                flash("Người bàn giao công việc không hợp lệ.", "error")
                return redirect(url_for("attendance.xin_nghi"))

        chu_ky_raw = request.form.get("chu_ky") or ""
        if "," not in chu_ky_raw:
            flash("Cần ký tên trước khi gửi đơn xin nghỉ.", "error")
            return redirect(url_for("attendance.xin_nghi"))
        try:
            chu_ky_png = base64.b64decode(chu_ky_raw.split(",", 1)[1])
        except (ValueError, binascii.Error):
            flash("Chữ ký không hợp lệ, thử ký lại.", "error")
            return redirect(url_for("attendance.xin_nghi"))
        if len(chu_ky_png) < 200:  # canvas trắng xuất ra vẫn vài trăm byte PNG rỗng
            flash("Cần ký tên trước khi gửi đơn xin nghỉ.", "error")
            return redirect(url_for("attendance.xin_nghi"))

        pdf_bytes = services.tao_pdf_don_xin_nghi(
            current_user, ngay_dau, ngay_cuoi, buoi, ly_do, ban_giao_cho, chu_ky_png,
        )
        duong_dan = services.luu_pdf_don_xin_nghi(pdf_bytes)

        so_ngay_tao = (ngay_cuoi - ngay_dau).days + 1
        da_tao = 0
        for i in range(so_ngay_tao):
            ngay = ngay_dau + timedelta(days=i)
            if XinNghi.query.filter_by(
                nguoi_dung_id=current_user.id, ngay=ngay, buoi=buoi
            ).first():
                continue  # đã xin đúng ngày + buổi này rồi, khỏi tạo trùng
            db.session.add(XinNghi(
                nguoi_dung_id=current_user.id, ngay=ngay, buoi=buoi,
                anh_minh_chung=duong_dan, ghi_chu=ly_do,
                ban_giao_cho_id=ban_giao_cho.id if ban_giao_cho else None,
            ))
            da_tao += 1
        db.session.commit()

        if da_tao:
            services.bao_xin_nghi(
                current_user, ngay_dau, ngay_cuoi, buoi, ly_do,
                da_tao * BuoiNghi.SO_NGAY[buoi], ban_giao_cho, duong_dan,
            )
            db.session.commit()
            flash(f"Đã ghi nhận nghỉ phép ({da_tao} ngày) — tự động duyệt qua đơn ký điện tử.", "success")
        else:
            flash("Những ngày này đã xin nghỉ đúng buổi này trước đó rồi.", "info")
        return redirect(url_for("attendance.xin_nghi"))

    dong_nghiep = (
        NguoiDung.query.filter(
            NguoiDung.id != current_user.id,
            NguoiDung.dang_hoat_dong.is_(True),
            NguoiDung.vai_tro != VaiTro.ADMIN,
        )
        .order_by(NguoiDung.ho_ten)
        .all()
    )
    lich_su = (
        XinNghi.query.filter_by(nguoi_dung_id=current_user.id)
        .order_by(XinNghi.ngay.desc())
        .limit(30)
        .all()
    )
    return render_template("xin_nghi.html", lich_su=lich_su, dong_nghiep=dong_nghiep)


@bp.route("/xin-nghi/xem-truoc-don", methods=["POST"])
@login_required
def xem_truoc_don():
    """AJAX — sinh thử NGUYÊN TỜ đơn xin nghỉ (đúng hàm dùng khi gửi thật:
    services.tao_pdf_don_xin_nghi) từ dữ liệu đang điền dở trên form + chữ
    ký vừa vẽ, để nhân viên xem trước đầy đủ cả đơn trước khi bấm gửi hẳn.
    KHÔNG lưu bất kỳ thay đổi nào xuống DB (luôn rollback ở cuối)."""
    if current_user.la_admin_sep:
        abort(403)

    try:
        ngay_dau = date.fromisoformat(request.form.get("ngay_dau", ""))
        ngay_cuoi = date.fromisoformat(request.form.get("ngay_cuoi") or request.form.get("ngay_dau"))
    except ValueError:
        abort(400)
    if ngay_cuoi < ngay_dau:
        abort(400)

    buoi = request.form.get("buoi") or BuoiNghi.CA_NGAY
    if buoi not in BuoiNghi.NHAN:
        buoi = BuoiNghi.CA_NGAY
    if ngay_dau != ngay_cuoi:
        buoi = BuoiNghi.CA_NGAY

    ly_do = (request.form.get("ly_do") or "").strip() or "(chưa nhập lý do)"
    so_dien_thoai = (request.form.get("so_dien_thoai") or "").strip() or "(chưa nhập số điện thoại)"

    ban_giao_cho = None
    ban_giao_cho_id_raw = (request.form.get("ban_giao_cho_id") or "").strip()
    if ban_giao_cho_id_raw:
        ban_giao_cho = db.session.get(NguoiDung, int(ban_giao_cho_id_raw))

    chu_ky_raw = request.form.get("chu_ky") or ""
    if "," not in chu_ky_raw:
        abort(400)
    try:
        chu_ky_png = base64.b64decode(chu_ky_raw.split(",", 1)[1])
    except (ValueError, binascii.Error):
        abort(400)

    # Đổi tạm SĐT trên object đang có trong session để đơn xem trước hiện
    # đúng số vừa gõ (kể cả khi khác số đã lưu) — không commit, rollback
    # ngay sau khi sinh xong để chắc chắn không lỡ lưu xuống DB.
    so_dien_thoai_cu = current_user.so_dien_thoai
    current_user.so_dien_thoai = so_dien_thoai
    try:
        pdf_bytes = services.tao_pdf_don_xin_nghi(
            current_user, ngay_dau, ngay_cuoi, buoi, ly_do, ban_giao_cho, chu_ky_png,
        )
    finally:
        current_user.so_dien_thoai = so_dien_thoai_cu
        db.session.rollback()

    return Response(pdf_bytes, mimetype="application/pdf")


@bp.route("/xin-nghi/<int:id>/xoa", methods=["POST"])
@login_required
def xoa_xin_nghi(id):
    if not current_user.la_admin_sep:
        abort(403)
    x = db.session.get(XinNghi, id) or abort(404)
    ten, ngay, duong_dan_pdf = x.nguoi_dung.ho_ten, x.ngay, x.anh_minh_chung

    services.bao_tu_choi_xin_nghi(x, current_user)
    db.session.delete(x)
    db.session.commit()

    # Xoá vĩnh viễn file PDF trên đĩa — CHỈ khi không còn dòng XinNghi nào
    # khác (của cùng đợt nghỉ nhiều ngày) đang dùng chung đúng file này,
    # tránh làm hỏng link "Xem đơn" của các ngày còn lại chưa bị xoá.
    con_dung_chung = XinNghi.query.filter_by(anh_minh_chung=duong_dan_pdf).first()
    if not con_dung_chung:
        duong_dan_tuyet_doi = os.path.join(
            current_app.config["UPLOAD_ROOT"], *duong_dan_pdf.split("/"))
        try:
            os.remove(duong_dan_tuyet_doi)
        except OSError:
            pass

    flash(f"Đã xoá vĩnh viễn nghỉ phép của {ten} ngày {ngay:%d/%m/%Y}, đã báo Zalo cho nhân viên.", "success")
    return redirect(request.referrer or url_for("attendance.bang_cong"))


# ---------------------------------------------------------------------------
def _du_lieu_bang_cong(thang: str):
    """Query dùng chung cho trang xem và trang xuất Excel.

    Ngày công CHỈ tính khi có chấm công thật tại địa điểm — nghỉ phép (có
    phép hay không phép đều vậy) không cộng vào Ngày công, chỉ hiện riêng
    ở cột Nghỉ phép để biết, không ảnh hưởng số ngày công/lương.
    """
    nam, thg = (int(x) for x in thang.split("-"))
    dau = date(nam, thg, 1)
    cuoi = date(nam + (thg == 12), (thg % 12) + 1, 1)

    q_cc = ChamCong.query.filter(ChamCong.ngay >= dau, ChamCong.ngay < cuoi)
    q_xn = XinNghi.query.filter(XinNghi.ngay >= dau, XinNghi.ngay < cuoi)
    if current_user.vai_tro == VaiTro.QUAN_LY and current_user.bo_phan_id:
        q_cc = q_cc.join(NguoiDung).filter(NguoiDung.bo_phan_id == current_user.bo_phan_id)
        q_xn = q_xn.join(NguoiDung, XinNghi.nguoi_dung_id == NguoiDung.id).filter(
            NguoiDung.bo_phan_id == current_user.bo_phan_id)

    ban_ghi = q_cc.order_by(ChamCong.ngay.desc()).all()
    don_nghi = q_xn.order_by(XinNghi.ngay.desc()).all()

    tong: dict[int, dict] = {}

    def _o(nguoi_dung: NguoiDung) -> dict:
        return tong.setdefault(nguoi_dung.id, {
            "ho_ten": nguoi_dung.ho_ten, "ma": nguoi_dung.ma_dinh_danh,
            "so_ngay": 0.0, "tre": 0, "som": 0, "khong_phep": 0, "nghi_phep": 0,
        })

    for c in ban_ghi:
        o = _o(c.nguoi_dung)
        o["so_ngay"] += services.gio_cong_trong_ngay(c)
        o["tre"] += int(bool(c.di_tre))
        o["som"] += int(bool(c.ve_som))
        o["khong_phep"] += int(bool(c.nghi_khong_phep))

    for x in don_nghi:
        o = _o(x.nguoi_dung)
        o["nghi_phep"] += 1

    return ban_ghi, don_nghi, sorted(tong.values(), key=lambda x: x["ho_ten"])


@bp.route("/bang-cong")
@login_required
def bang_cong():
    if not current_user.la_quan_ly:
        abort(403)
    thang = request.args.get("thang") or ngay_vn_hien_tai().strftime("%Y-%m")
    ban_ghi, don_nghi, tong = _du_lieu_bang_cong(thang)
    return render_template("bang_cong.html", ban_ghi=ban_ghi, don_nghi=don_nghi,
                           tong=tong, thang=thang)


@bp.route("/bang-cong/xuat")
@login_required
def xuat_bang_cong():
    if not current_user.la_quan_ly:
        abort(403)
    thang = request.args.get("thang") or ngay_vn_hien_tai().strftime("%Y-%m")
    ban_ghi, don_nghi, tong = _du_lieu_bang_cong(thang)
    tep = services.xuat_excel_bang_cong(thang, ban_ghi, don_nghi, tong)
    return send_file(
        tep,
        as_attachment=True,
        download_name=f"bang-cong-{thang}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )