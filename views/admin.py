import os
import secrets
from datetime import date
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, send_file, url_for)
from flask_login import current_user, login_required

import dich_vu_ai
import services
from extensions import db
from models import (AnhSanPhamAI, BoPhan, BotZalo, ChamCong, ChucVu, CongViec,
                    DanhGia, DiemChamCong, DinhKem, LogZalo, NguoiDung, SanPhamAI,
                    TroLySuDung, VaiTro, ngay_vn_hien_tai)

bp = Blueprint("admin", __name__, url_prefix="/quan-tri")


def chi_admin(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.vai_tro not in (VaiTro.ADMIN, VaiTro.SEP):
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def chi_admin_thuan(f):
    """Chặt hơn chi_admin — chỉ đúng role Quản trị (Admin), Ban giám đốc
    cũng không qua được. Dùng riêng cho xoá vĩnh viễn nhân viên."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.vai_tro != VaiTro.ADMIN:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


@bp.route("/nhan-vien")
@chi_admin
def nhan_vien():
    tim = (request.args.get("q") or "").strip()
    bo_phan_id = request.args.get("bo_phan", type=int)
    trang_thai = request.args.get("trang_thai", "")

    q = NguoiDung.query
    if tim:
        like = f"%{tim}%"
        q = q.filter(db.or_(NguoiDung.ho_ten.ilike(like), NguoiDung.ma_dinh_danh.ilike(like)))
    if bo_phan_id:
        q = q.filter(NguoiDung.bo_phan_id == bo_phan_id)
    if trang_thai == "1":
        q = q.filter(NguoiDung.dang_hoat_dong.is_(True))
    elif trang_thai == "0":
        q = q.filter(NguoiDung.dang_hoat_dong.is_(False))

    tat_ca = q.order_by(NguoiDung.dang_hoat_dong.desc(), NguoiDung.ho_ten).all()

    # Nhóm theo bộ phận cho dễ lướt mắt tìm đúng người (thay vì 1 danh sách
    # phẳng lẫn lộn mọi phòng ban) — ai chưa gán bộ phận gom vào 1 nhóm
    # riêng, luôn xếp CUỐI CÙNG vì ít quan trọng hơn các phòng ban thật.
    CHUA_GAN = "Chưa gán bộ phận"
    theo_bo_phan: dict[str, list] = {}
    for n in tat_ca:
        ten_bp = n.bo_phan.ten if n.bo_phan else CHUA_GAN
        theo_bo_phan.setdefault(ten_bp, []).append(n)
    nhom_nhan_vien = sorted(theo_bo_phan.items(), key=lambda kv: (kv[0] == CHUA_GAN, kv[0]))

    return render_template(
        "admin_users.html",
        nhom_nhan_vien=nhom_nhan_vien, tong_so=len(tat_ca),
        bo_phans=BoPhan.query.order_by(BoPhan.ten).all(),
        bots=BotZalo.query.order_by(BotZalo.ten).all(),
        ds_chuc_vu=ChucVu.query.order_by(ChucVu.ten).all(),
        vai_tros=VaiTro.NHAN,
        f_q=tim,
        f_bo_phan=bo_phan_id,
        f_trang_thai=trang_thai,
    )


@bp.route("/nhan-vien/luu", methods=["POST"])
@chi_admin
def luu_nhan_vien():
    uid = request.form.get("id", type=int)
    nd = db.session.get(NguoiDung, uid) if uid else NguoiDung()

    ma = (request.form.get("ma_dinh_danh") or "").strip()
    if not ma or not (request.form.get("ho_ten") or "").strip():
        flash("Mã nhân viên và họ tên là bắt buộc.", "error")
        return redirect(url_for("admin.nhan_vien"))

    trung = NguoiDung.query.filter(
        db.func.lower(NguoiDung.ma_dinh_danh) == ma.lower()
    ).first()
    if trung and trung.id != nd.id:
        flash(f"Mã nhân viên {ma} đã tồn tại.", "error")
        return redirect(url_for("admin.nhan_vien"))

    bot_zalo_id = request.form.get("bot_zalo_id", type=int) or None
    if bot_zalo_id:
        q_dem_bot = NguoiDung.query.filter_by(bot_zalo_id=bot_zalo_id, dang_hoat_dong=True)
        if nd.id:
            q_dem_bot = q_dem_bot.filter(NguoiDung.id != nd.id)
        if q_dem_bot.count() >= 3:
            bot = db.session.get(BotZalo, bot_zalo_id)
            flash(f"Bot {bot.ten if bot else ''} đã có đủ 3 nhân viên sử dụng — "
                  f"chọn bot khác hoặc thêm bot mới ở Thiết lập.", "error")
            return redirect(url_for("admin.nhan_vien"))

    nd.ma_dinh_danh = ma
    nd.ho_ten = request.form["ho_ten"].strip()
    nd.vai_tro = request.form.get("vai_tro", VaiTro.NHAN_VIEN)
    nd.bo_phan_id = request.form.get("bo_phan_id", type=int) or None
    nd.so_dien_thoai = (request.form.get("so_dien_thoai") or "").strip() or None
    nd.zalo_group_id = (request.form.get("zalo_group_id") or "").strip() or None
    nd.bot_zalo_id = bot_zalo_id
    nd.chuc_vu_id = request.form.get("chuc_vu_id", type=int) or None
    nd.dang_hoat_dong = request.form.get("dang_hoat_dong") == "on"

    mk_moi = None
    if not uid:
        mk_moi = secrets.token_urlsafe(6)
        nd.dat_mat_khau(mk_moi)
        nd.doi_mat_khau = True
        db.session.add(nd)

    db.session.commit()
    if mk_moi:
        flash(f"Đã tạo {nd.ho_ten}. Mật khẩu tạm: {mk_moi} — gửi cho nhân viên, "
              f"lần đăng nhập đầu sẽ bắt đổi.", "success")
    else:
        flash("Đã lưu.", "success")
    return redirect(url_for("admin.nhan_vien"))


@bp.route("/nhan-vien/<int:uid>/reset-mat-khau", methods=["POST"])
@chi_admin
def reset_mat_khau(uid):
    nd = db.session.get(NguoiDung, uid) or abort(404)
    mk = secrets.token_urlsafe(6)
    nd.dat_mat_khau(mk)
    nd.doi_mat_khau = True
    db.session.commit()
    flash(f"Mật khẩu mới của {nd.ho_ten}: {mk}", "success")
    return redirect(url_for("admin.nhan_vien"))


@bp.route("/nhan-vien/<int:uid>/test-zalo", methods=["POST"])
@chi_admin
def test_zalo(uid):
    nd = db.session.get(NguoiDung, uid) or abort(404)
    ok = services.gui_cho_nhan_vien(nd, "🔔 Tin nhắn thử từ hệ thống giao việc BRICON.")
    db.session.commit()
    flash("Gửi thành công." if ok else "Gửi thất bại — xem log Zalo để biết lý do.",
          "success" if ok else "error")
    return redirect(url_for("admin.nhan_vien"))


@bp.route("/nhan-vien/<int:uid>/xoa", methods=["POST"])
@chi_admin
def xoa_nhan_vien(uid):
    nd = db.session.get(NguoiDung, uid) or abort(404)

    if nd.id == current_user.id:
        flash("Không thể tự xoá tài khoản đang đăng nhập.", "error")
        return redirect(url_for("admin.nhan_vien"))

    co_du_lieu = (
        CongViec.query.filter(db.or_(CongViec.nguoi_giao_id == nd.id,
                                     CongViec.nguoi_nhan_id == nd.id)).first()
        or ChamCong.query.filter_by(nguoi_dung_id=nd.id).first()
        or DinhKem.query.filter_by(nguoi_tai_len_id=nd.id).first()
        or DanhGia.query.filter_by(nguoi_danh_gia_id=nd.id).first()
    )
    if co_du_lieu:
        flash(f"Không thể xoá {nd.ho_ten} vì tài khoản đã có dữ liệu công việc/chấm công/"
              f"đánh giá gắn với nó. Bỏ chọn 'Đang làm việc' để ngừng truy cập thay vì xoá.",
              "error")
        return redirect(url_for("admin.nhan_vien"))

    ten = nd.ho_ten
    db.session.delete(nd)
    db.session.commit()
    flash(f"Đã xoá tài khoản {ten}.", "success")
    return redirect(url_for("admin.nhan_vien"))


@bp.route("/nhan-vien/<int:uid>/xoa-vinh-vien", methods=["POST"])
@chi_admin_thuan
def xoa_vinh_vien_nhan_vien(uid):
    """Xoá vĩnh viễn 1 nhân viên VÀ TOÀN BỘ dữ liệu của họ — không thể
    khôi phục. Chỉ đúng role Quản trị (Admin), Ban giám đốc không có
    quyền này."""
    if uid == current_user.id:
        flash("Không thể tự xoá tài khoản đang đăng nhập.", "error")
        return redirect(url_for("admin.nhan_vien"))

    nd = db.session.get(NguoiDung, uid) or abort(404)
    ten, ma = nd.ho_ten, nd.ma_dinh_danh
    services.xoa_toan_bo_du_lieu_nhan_vien(nd, current_user)
    db.session.commit()
    flash(f"Đã xoá vĩnh viễn {ten} ({ma}) và toàn bộ dữ liệu liên quan.", "success")
    return redirect(url_for("admin.nhan_vien"))


@bp.route("/bo-phan", methods=["GET", "POST"])
@chi_admin
def bo_phan():
    if request.method == "POST":
        ten = (request.form.get("ten") or "").strip()
        if ten and not BoPhan.query.filter_by(ten=ten).first():
            db.session.add(BoPhan(ten=ten))
            db.session.commit()
            flash("Đã thêm bộ phận.", "success")
        else:
            flash("Tên bộ phận trống hoặc đã tồn tại.", "error")
        return redirect(url_for("admin.bo_phan"))
    return render_template("admin_bophan.html",
                           ds=BoPhan.query.order_by(BoPhan.ten).all())


@bp.route("/bo-phan/<int:bo_phan_id>/xoa", methods=["POST"])
@chi_admin
def xoa_bo_phan(bo_phan_id):
    b = db.session.get(BoPhan, bo_phan_id) or abort(404)
    so_nv = NguoiDung.query.filter_by(bo_phan_id=b.id).count()
    if so_nv:
        flash(f"Không thể xoá {b.ten} vì còn {so_nv} nhân viên thuộc bộ phận này. "
              f"Chuyển họ sang bộ phận khác trước.", "error")
        return redirect(url_for("admin.bo_phan"))
    ten = b.ten
    db.session.delete(b)
    db.session.commit()
    flash(f"Đã xoá bộ phận {ten}.", "success")
    return redirect(url_for("admin.bo_phan"))


@bp.route("/diem-cham-cong", methods=["GET", "POST"])
@chi_admin
def diem_cham_cong():
    if request.method == "POST":
        did = request.form.get("id", type=int)
        d = db.session.get(DiemChamCong, did) if did else DiemChamCong()
        try:
            d.lat = float(request.form["lat"])
            d.lng = float(request.form["lng"])
        except (KeyError, ValueError):
            flash("Toạ độ không hợp lệ.", "error")
            return redirect(url_for("admin.diem_cham_cong"))
        d.ten = (request.form.get("ten") or "").strip()
        d.dia_chi = (request.form.get("dia_chi") or "").strip() or None
        d.ban_kinh_m = request.form.get("ban_kinh_m", type=int) or 150
        d.dang_hoat_dong = request.form.get("dang_hoat_dong") == "on"
        if not did:
            db.session.add(d)
        db.session.commit()
        flash("Đã lưu điểm chấm công.", "success")
        return redirect(url_for("admin.diem_cham_cong"))

    return render_template("admin_diem.html",
                           ds=DiemChamCong.query.order_by(DiemChamCong.ten).all())


@bp.route("/ngay-nghi")
@chi_admin
def ngay_nghi():
    from models import NgayNghiLe
    return render_template(
        "admin_ngay_nghi.html",
        ds=NgayNghiLe.query.order_by(NgayNghiLe.ngay).all(),
        nghi_chu_nhat=services.lay_cai_dat("NGHI_CHU_NHAT", "1") == "1",
    )


@bp.route("/ngay-nghi/luu", methods=["POST"])
@chi_admin
def luu_ngay_nghi():
    from models import NgayNghiLe
    ngay_raw = (request.form.get("ngay") or "").strip()
    ten = (request.form.get("ten") or "").strip()
    if not ngay_raw or not ten:
        flash("Cần nhập đủ ngày và tên ngày lễ.", "error")
        return redirect(url_for("admin.ngay_nghi"))
    try:
        ngay = date.fromisoformat(ngay_raw)
    except ValueError:
        flash("Ngày không hợp lệ.", "error")
        return redirect(url_for("admin.ngay_nghi"))

    da_co = NgayNghiLe.query.filter_by(ngay=ngay).first()
    if da_co:
        da_co.ten = ten
        flash(f"Đã cập nhật tên ngày nghỉ {ngay:%d/%m/%Y}.", "success")
    else:
        db.session.add(NgayNghiLe(ngay=ngay, ten=ten))
        flash(f"Đã thêm ngày nghỉ {ngay:%d/%m/%Y} — {ten}.", "success")
    db.session.commit()
    return redirect(url_for("admin.ngay_nghi"))


@bp.route("/ngay-nghi/<int:id>/xoa", methods=["POST"])
@chi_admin
def xoa_ngay_nghi(id):
    from models import NgayNghiLe
    nnl = db.session.get(NgayNghiLe, id) or abort(404)
    ten, ngay = nnl.ten, nnl.ngay
    db.session.delete(nnl)
    db.session.commit()
    flash(f"Đã xoá ngày nghỉ {ten} ({ngay:%d/%m/%Y}).", "success")
    return redirect(url_for("admin.ngay_nghi"))


@bp.route("/ngay-nghi/nghi-chu-nhat", methods=["POST"])
@chi_admin
def luu_nghi_chu_nhat():
    services.dat_cai_dat("NGHI_CHU_NHAT", "1" if request.form.get("nghi_chu_nhat") == "on" else "0")
    db.session.commit()
    flash("Đã cập nhật cài đặt nghỉ Chủ nhật.", "success")
    return redirect(url_for("admin.ngay_nghi"))


@bp.route("/log-zalo")
@chi_admin
def log_zalo():
    ds = LogZalo.query.order_by(LogZalo.id.desc()).limit(200).all()
    return render_template("admin_logzalo.html", ds=ds)


@bp.route("/thiet-lap", methods=["GET", "POST"])
@chi_admin
def thiet_lap():
    if request.method == "POST":
        bid = request.form.get("id", type=int)
        b = db.session.get(BotZalo, bid) if bid else BotZalo()

        ten = (request.form.get("ten") or "").strip()
        token = (request.form.get("token") or "").strip()
        if not ten or not token:
            flash("Cần nhập tên bot và token.", "error")
            return redirect(url_for("admin.thiet_lap"))

        trung = BotZalo.query.filter(db.func.lower(BotZalo.ten) == ten.lower()).first()
        if trung and trung.id != b.id:
            flash(f"Tên bot {ten} đã tồn tại.", "error")
            return redirect(url_for("admin.thiet_lap"))

        b.ten = ten
        b.token = token
        b.link_moi = (request.form.get("link_moi") or "").strip() or None
        b.owner = (request.form.get("owner") or "").strip() or None
        b.dang_hoat_dong = request.form.get("dang_hoat_dong") == "on"
        if not bid:
            b.webhook_secret = secrets.token_urlsafe(24)
            db.session.add(b)
        db.session.commit()
        flash("Đã lưu bot Zalo.", "success")
        return redirect(url_for("admin.thiet_lap"))

    ds_bot = BotZalo.query.order_by(BotZalo.ten).all()
    nv_theo_bot: dict[int, list[NguoiDung]] = {}
    for nd in (NguoiDung.query.filter(NguoiDung.bot_zalo_id.isnot(None),
                                      NguoiDung.dang_hoat_dong.is_(True))
               .order_by(NguoiDung.ho_ten).all()):
        nv_theo_bot.setdefault(nd.bot_zalo_id, []).append(nd)
    return render_template("admin_thietlap.html",
                           ds=ds_bot,
                           nv_theo_bot=nv_theo_bot,
                           openai_api_key=services.lay_cai_dat("openai_api_key"),
                           gh_cau_hoi=services.lay_cai_dat(
                               "tro_ly_gioi_han_cau_hoi_ngay",
                               str(dich_vu_ai._GIOI_HAN_MAC_DINH_CAU_HOI_NGAY)),
                           gh_token=services.lay_cai_dat(
                               "tro_ly_gioi_han_token_ngay",
                               str(dich_vu_ai._GIOI_HAN_MAC_DINH_TOKEN_NGAY)))


@bp.route("/thiet-lap/ai", methods=["POST"])
@chi_admin
def luu_cai_dat_ai():
    key = (request.form.get("openai_api_key") or "").strip()
    services.dat_cai_dat("openai_api_key", key)
    db.session.commit()
    flash("Đã lưu API key AI." if key else "Đã xoá API key AI.", "success")
    return redirect(url_for("admin.thiet_lap"))


@bp.route("/thiet-lap/ai-gioi-han", methods=["POST"])
@chi_admin
def luu_gioi_han_tro_ly():
    """Hạn mức Trợ lý AI/ngày cho Nhân viên + Quản lý bộ phận (Sếp/Admin
    không giới hạn) — để 0 ở ô nào nghĩa là bỏ giới hạn đó."""
    gh_cau_hoi = request.form.get("gh_cau_hoi", type=int)
    gh_token = request.form.get("gh_token", type=int)
    if gh_cau_hoi is None or gh_cau_hoi < 0 or gh_token is None or gh_token < 0:
        flash("Hạn mức phải là số nguyên không âm.", "error")
        return redirect(url_for("admin.thiet_lap"))
    services.dat_cai_dat("tro_ly_gioi_han_cau_hoi_ngay", str(gh_cau_hoi))
    services.dat_cai_dat("tro_ly_gioi_han_token_ngay", str(gh_token))
    db.session.commit()
    flash("Đã lưu hạn mức Trợ lý AI.", "success")
    return redirect(url_for("admin.thiet_lap"))


# ---------------------------------------------------------------------------
# THỐNG KÊ SỬ DỤNG TRỢ LÝ AI — sếp/admin xem ai hỏi bao nhiêu câu, tốn bao
# nhiêu token, theo từng tháng + hôm nay (để biết ai sắp/đã chạm hạn mức).
# ---------------------------------------------------------------------------
def _du_lieu_tro_ly_su_dung(thang: str):
    """Trả về (chi_tiet theo ngày, tổng hợp theo người) trong khoảng thang
    (chuỗi 'YYYY-MM'). Dùng chung cho trang xem và trang xuất Excel."""
    nam, thg = (int(x) for x in thang.split("-"))
    dau = date(nam, thg, 1)
    cuoi = date(nam + (thg == 12), (thg % 12) + 1, 1)

    chi_tiet = (
        TroLySuDung.query
        .join(NguoiDung, TroLySuDung.nguoi_dung_id == NguoiDung.id)
        .filter(TroLySuDung.ngay >= dau, TroLySuDung.ngay < cuoi)
        .order_by(TroLySuDung.ngay.desc(), NguoiDung.ho_ten)
        .all()
    )

    tong: dict[int, dict] = {}
    for su_dung in chi_tiet:
        nd = su_dung.nguoi_dung
        o = tong.setdefault(nd.id, {
            "ho_ten": nd.ho_ten, "ma": nd.ma_dinh_danh,
            "vai_tro": nd.ten_vai_tro, "so_cau_hoi": 0, "so_token": 0,
        })
        o["so_cau_hoi"] += su_dung.so_cau_hoi
        o["so_token"] += su_dung.so_token

    tong_sap_xep = sorted(tong.values(), key=lambda x: x["so_cau_hoi"], reverse=True)
    return chi_tiet, tong_sap_xep


@bp.route("/tro-ly-su-dung")
@chi_admin
def tro_ly_su_dung():
    thang = request.args.get("thang") or ngay_vn_hien_tai().strftime("%Y-%m")
    chi_tiet, tong = _du_lieu_tro_ly_su_dung(thang)

    hom_nay = ngay_vn_hien_tai()
    su_dung_hom_nay = (
        TroLySuDung.query
        .join(NguoiDung, TroLySuDung.nguoi_dung_id == NguoiDung.id)
        .filter(TroLySuDung.ngay == hom_nay)
        .order_by(TroLySuDung.so_cau_hoi.desc())
        .all()
    )
    gh_cau_hoi = int(services.lay_cai_dat(
        "tro_ly_gioi_han_cau_hoi_ngay", str(dich_vu_ai._GIOI_HAN_MAC_DINH_CAU_HOI_NGAY)))
    gh_token = int(services.lay_cai_dat(
        "tro_ly_gioi_han_token_ngay", str(dich_vu_ai._GIOI_HAN_MAC_DINH_TOKEN_NGAY)))

    return render_template(
        "admin_trolysudung.html", thang=thang, chi_tiet=chi_tiet, tong=tong,
        su_dung_hom_nay=su_dung_hom_nay, gh_cau_hoi=gh_cau_hoi, gh_token=gh_token,
        VaiTro=VaiTro)


@bp.route("/tro-ly-su-dung/xuat")
@chi_admin
def xuat_tro_ly_su_dung():
    thang = request.args.get("thang") or ngay_vn_hien_tai().strftime("%Y-%m")
    chi_tiet, tong = _du_lieu_tro_ly_su_dung(thang)
    tep = services.xuat_excel_tro_ly_su_dung(thang, chi_tiet, tong)
    return send_file(
        tep,
        as_attachment=True,
        download_name=f"tro-ly-ai-su-dung-{thang}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/thiet-lap/chay-thu/<ten_lenh>", methods=["POST"])
@chi_admin
def chay_thu_bao_cao(ten_lenh):
    """Bấm để gửi thử ngay 1 trong 5 báo cáo Zalo theo lịch — kiểm tra
    trước khi đặt cron thật trên VPS."""
    if ten_lenh == "cham-cong-sang":
        so = services.nhac_cham_cong_sang()
        thong_bao = f"Đã gửi nhắc chấm công cho {so} người."
    elif ten_lenh == "sap-qua-han":
        so = services.nhac_viec_sap_qua_han(30)
        thong_bao = f"Đã nhắc {so} việc sắp tới hạn."
    elif ten_lenh == "cham-cong-chieu":
        so = services.nhac_cham_cong_chieu()
        thong_bao = f"Đã gửi nhắc chấm công ra cho {so} người."
    elif ten_lenh == "viec-hom-nay":
        so = services.nhac_viec_hom_nay()
        thong_bao = f"Đã gửi nhắc xem việc hôm nay cho {so} người."
    elif ten_lenh == "ban-tin-sang":
        so = services.gui_ban_tin_sang()
        thong_bao = f"Đã gửi bản tin sáng cho {so} người."
    elif ten_lenh == "ban-tin-chieu":
        so = services.gui_ban_tin_chieu()
        thong_bao = f"Đã gửi bản tin chiều cho {so} người."
    elif ten_lenh == "bao-cao-sang":
        services.bao_cao_sang_cho_sep()
        thong_bao = "Đã gửi báo cáo sáng vào nhóm QL."
    elif ten_lenh == "bao-cao-chieu":
        services.bao_cao_chieu_cho_sep()
        thong_bao = "Đã gửi báo cáo chiều vào nhóm QL."
    elif ten_lenh == "thieu-sot":
        services.bao_cao_thieu_sot()
        thong_bao = "Đã gửi báo cáo việc còn thiếu vào nhóm QL."
    else:
        abort(404)
    db.session.commit()
    flash(thong_bao, "success")
    return redirect(url_for("admin.thiet_lap"))


@bp.route("/thiet-lap/<int:bot_id>/xoa", methods=["POST"])
@chi_admin
def xoa_bot(bot_id):
    b = db.session.get(BotZalo, bot_id) or abort(404)
    so_nv = NguoiDung.query.filter_by(bot_zalo_id=b.id).count()
    if so_nv:
        flash(f"Không thể xoá bot {b.ten} vì đang gán cho {so_nv} nhân viên. "
              f"Đổi bot cho họ trước (ở trang Nhân viên).", "error")
        return redirect(url_for("admin.thiet_lap"))
    ten = b.ten
    db.session.delete(b)
    db.session.commit()
    flash(f"Đã xoá bot {ten}.", "success")
    return redirect(url_for("admin.thiet_lap"))


@bp.route("/thiet-lap/<int:bot_id>/lay-group-id")
@chi_admin
def lay_group_id(bot_id):
    b = db.session.get(BotZalo, bot_id) or abort(404)
    ket_qua, loi, tho = services.lay_cac_chat_gan_day(b.token)
    return render_template("admin_group_id.html", bot=b, ket_qua=ket_qua, loi=loi, tho=tho)


@bp.route("/thiet-lap/<int:bot_id>/dat-webhook", methods=["POST"])
@chi_admin
def dat_webhook(bot_id):
    b = db.session.get(BotZalo, bot_id) or abort(404)
    if not b.webhook_secret:
        b.webhook_secret = secrets.token_urlsafe(24)
        db.session.commit()
    ok, phan_hoi = services.dat_webhook(b)
    flash("Đã đặt webhook. Vào nhóm gõ /id để thử." if ok
          else f"Đặt webhook thất bại: {phan_hoi[:300]}", "success" if ok else "error")
    return redirect(url_for("admin.thiet_lap"))


@bp.route("/thiet-lap/<int:bot_id>/xoa-webhook", methods=["POST"])
@chi_admin
def xoa_webhook(bot_id):
    b = db.session.get(BotZalo, bot_id) or abort(404)
    ok, phan_hoi = services.xoa_webhook(b)
    flash("Đã xoá webhook — bot chuyển về chế độ getUpdates." if ok
          else f"Xoá webhook thất bại: {phan_hoi[:300]}", "success" if ok else "error")
    return redirect(url_for("admin.thiet_lap"))


@bp.route("/info-ai")
@chi_admin
def info_ai():
    return render_template(
        "admin_info_ai.html",
        thong_tin_chung=services.lay_cai_dat("thong_tin_chung_cong_ty", ""),
        thong_tin_san_pham=services.lay_cai_dat("thong_tin_san_pham", ""),
        ds_chuc_vu=ChucVu.query.order_by(ChucVu.ten).all(),
        ds_san_pham_ai=SanPhamAI.query.order_by(SanPhamAI.ten).all(),
    )


@bp.route("/info-ai/chung", methods=["POST"])
@chi_admin
def luu_thong_tin_chung():
    noi_dung = (request.form.get("thong_tin_chung") or "").strip()
    services.dat_cai_dat("thong_tin_chung_cong_ty", noi_dung)
    db.session.commit()
    flash("Đã lưu thông tin chung công ty.", "success")
    return redirect(url_for("admin.info_ai"))


@bp.route("/info-ai/san-pham", methods=["POST"])
@chi_admin
def luu_thong_tin_san_pham():
    noi_dung = (request.form.get("thong_tin_san_pham") or "").strip()
    services.dat_cai_dat("thong_tin_san_pham", noi_dung)
    db.session.commit()
    flash("Đã lưu thông tin sản phẩm.", "success")
    return redirect(url_for("admin.info_ai"))


@bp.route("/info-ai/san-pham-ai", methods=["POST"])
@bp.route("/info-ai/san-pham-ai/<int:spid>", methods=["POST"])
@chi_admin
def luu_san_pham_ai(spid=None):
    """Thêm mới (spid=None) hoặc sửa tên/mô tả 1 sản phẩm AI hiện có.
    Có thể đính kèm nhiều ảnh cùng lúc ngay trong lần lưu này — mỗi ảnh
    chọn trong 1 lần lưu dùng chung 1 nhãn (VD: "TDS", "Bảng định mức")."""
    sp = db.session.get(SanPhamAI, spid) if spid else None
    if spid and not sp:
        abort(404)
    if not sp:
        sp = SanPhamAI()

    ten = (request.form.get("ten") or "").strip()
    if not ten:
        flash("Cần nhập tên sản phẩm.", "error")
        return redirect(url_for("admin.info_ai"))

    sp.ten = ten
    sp.mo_ta = (request.form.get("mo_ta") or "").strip()

    nhan_anh = (request.form.get("nhan_anh") or "").strip()
    cac_anh = [f for f in request.files.getlist("anh") if f and f.filename]
    for f in cac_anh:
        duong_dan, _ = services.luu_file(f, "san-pham-ai")
        sp.anh.append(AnhSanPhamAI(duong_dan=duong_dan, nhan=nhan_anh or None))

    if not spid:
        db.session.add(sp)
    db.session.commit()
    flash("Đã lưu sản phẩm.", "success")
    return redirect(url_for("admin.info_ai"))


@bp.route("/info-ai/san-pham-ai/<int:spid>/xoa", methods=["POST"])
@chi_admin
def xoa_san_pham_ai(spid):
    sp = db.session.get(SanPhamAI, spid) or abort(404)
    for a in sp.anh:
        duong_dan_tuyet_doi = os.path.join(current_app.config["UPLOAD_ROOT"], *a.duong_dan.split("/"))
        try:
            os.remove(duong_dan_tuyet_doi)
        except OSError:
            pass
    ten = sp.ten
    db.session.delete(sp)
    db.session.commit()
    flash(f"Đã xoá sản phẩm {ten}.", "success")
    return redirect(url_for("admin.info_ai"))


@bp.route("/info-ai/san-pham-ai/anh/<int:anh_id>/xoa", methods=["POST"])
@chi_admin
def xoa_anh_san_pham_ai(anh_id):
    a = db.session.get(AnhSanPhamAI, anh_id) or abort(404)
    duong_dan_tuyet_doi = os.path.join(current_app.config["UPLOAD_ROOT"], *a.duong_dan.split("/"))
    try:
        os.remove(duong_dan_tuyet_doi)
    except OSError:
        pass
    db.session.delete(a)
    db.session.commit()
    flash("Đã xoá ảnh.", "success")
    return redirect(url_for("admin.info_ai"))


@bp.route("/info-ai/chuc-vu", methods=["POST"])
@chi_admin
def luu_chuc_vu():
    cvid = request.form.get("id", type=int)
    cv = db.session.get(ChucVu, cvid) if cvid else ChucVu()

    ten = (request.form.get("ten") or "").strip()
    if not ten:
        flash("Cần nhập tên chức vụ.", "error")
        return redirect(url_for("admin.info_ai"))

    trung = ChucVu.query.filter(db.func.lower(ChucVu.ten) == ten.lower()).first()
    if trung and trung.id != cv.id:
        flash(f"Chức vụ {ten} đã tồn tại.", "error")
        return redirect(url_for("admin.info_ai"))

    cv.ten = ten
    cv.mo_ta = (request.form.get("mo_ta") or "").strip()

    anh = request.files.get("anh")
    if anh and anh.filename:
        duong_dan, _ = services.luu_file(anh, "chuc-vu")
        cv.anh = duong_dan

    if not cvid:
        db.session.add(cv)
    db.session.commit()
    flash("Đã lưu chức vụ.", "success")
    return redirect(url_for("admin.info_ai"))


@bp.route("/info-ai/chuc-vu/<int:cvid>/xoa", methods=["POST"])
@chi_admin
def xoa_chuc_vu(cvid):
    cv = db.session.get(ChucVu, cvid) or abort(404)
    so_nv = NguoiDung.query.filter_by(chuc_vu_id=cv.id).count()
    if so_nv:
        flash(f"Không thể xoá {cv.ten} vì đang gán cho {so_nv} nhân viên. "
              f"Đổi chức vụ cho họ trước (ở trang Nhân viên).", "error")
        return redirect(url_for("admin.info_ai"))
    ten = cv.ten
    db.session.delete(cv)
    db.session.commit()
    flash(f"Đã xoá chức vụ {ten}.", "success")
    return redirect(url_for("admin.info_ai"))