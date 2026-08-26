import secrets
from functools import wraps

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

import services
from extensions import db
from models import (BoPhan, BotZalo, ChamCong, ChucVu, CongViec, DanhGia,
                    DiemChamCong, DinhKem, LogZalo, NguoiDung, VaiTro)

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

    return render_template(
        "admin_users.html",
        ds=q.order_by(NguoiDung.dang_hoat_dong.desc(), NguoiDung.ho_ten).all(),
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
        b.dang_hoat_dong = request.form.get("dang_hoat_dong") == "on"
        if not bid:
            b.webhook_secret = secrets.token_urlsafe(24)
            db.session.add(b)
        db.session.commit()
        flash("Đã lưu bot Zalo.", "success")
        return redirect(url_for("admin.thiet_lap"))

    ds_bot = BotZalo.query.order_by(BotZalo.ten).all()
    so_dung_theo_bot = {
        bid: sl for bid, sl in (
            db.session.query(NguoiDung.bot_zalo_id, db.func.count(NguoiDung.id))
            .filter(NguoiDung.bot_zalo_id.isnot(None), NguoiDung.dang_hoat_dong.is_(True))
            .group_by(NguoiDung.bot_zalo_id).all()
        )
    }
    return render_template("admin_thietlap.html",
                           ds=ds_bot,
                           so_dung_theo_bot=so_dung_theo_bot,
                           openai_api_key=services.lay_cai_dat("openai_api_key"))


@bp.route("/thiet-lap/ai", methods=["POST"])
@chi_admin
def luu_cai_dat_ai():
    key = (request.form.get("openai_api_key") or "").strip()
    services.dat_cai_dat("openai_api_key", key)
    db.session.commit()
    flash("Đã lưu API key AI." if key else "Đã xoá API key AI.", "success")
    return redirect(url_for("admin.thiet_lap"))


@bp.route("/thiet-lap/chay-thu/<ten_lenh>", methods=["POST"])
@chi_admin
def chay_thu_bao_cao(ten_lenh):
    """Bấm để gửi thử ngay 1 trong 5 báo cáo Zalo theo lịch — kiểm tra
    trước khi đặt cron thật trên VPS."""
    if ten_lenh == "cham-cong-sang":
        so = services.nhac_cham_cong_sang()
        thong_bao = f"Đã gửi nhắc chấm công cho {so} người."
    elif ten_lenh == "viec-hom-nay":
        so = services.nhac_viec_hom_nay()
        thong_bao = f"Đã gửi nhắc xem việc hôm nay cho {so} người."
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
        ds_chuc_vu=ChucVu.query.order_by(ChucVu.ten).all(),
    )


@bp.route("/info-ai/chung", methods=["POST"])
@chi_admin
def luu_thong_tin_chung():
    noi_dung = (request.form.get("thong_tin_chung") or "").strip()
    services.dat_cai_dat("thong_tin_chung_cong_ty", noi_dung)
    db.session.commit()
    flash("Đã lưu thông tin chung công ty.", "success")
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