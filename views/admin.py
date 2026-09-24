import os
import secrets
from datetime import date, timedelta
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, send_file, url_for)
from flask_login import current_user, login_required

import dich_vu_ai
import services
from extensions import db
from models import (HoSoNhanVien, LoaiDinhKem, LoaiHoSo, AnhSanPhamAI, BoPhan, BotZalo, ChamCong, ChucVu, CongViec,
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

    # Nhắc nhanh đầu trang: sinh nhật trong tháng + hợp đồng sắp/đã hết hạn
    dang_lam = [n for n in tat_ca if n.dang_hoat_dong]
    sinh_nhat_thang = sorted((n for n in dang_lam if n.sinh_nhat_thang_nay),
                             key=lambda n: n.ngay_sinh.day)
    hd_can_chu_y = []
    for n in dang_lam:
        hd = [h for h in n.ho_so_theo_loai(LoaiHoSo.HOP_DONG) if h.ngay_het_han]
        if hd:
            moi_nhat = max(hd, key=lambda h: h.ngay_het_han)
            if moi_nhat.so_ngay_con_han is not None and moi_nhat.so_ngay_con_han <= 30:
                hd_can_chu_y.append((n, moi_nhat))

    return render_template(
        "admin_users.html",
        sinh_nhat_thang=sinh_nhat_thang, hd_can_chu_y=hd_can_chu_y, hom_nay_vn=ngay_vn_hien_tai(),
        LoaiHoSo=LoaiHoSo,
        nhom_nhan_vien=nhom_nhan_vien, tong_so=len(tat_ca),
        bo_phans=BoPhan.query.order_by(BoPhan.ten).all(),
        bots=BotZalo.query.order_by(BotZalo.ten).all(),
        ds_chuc_vu=ChucVu.query.order_by(ChucVu.ten).all(),
        vai_tros=VaiTro.NHAN,
        f_q=tim,
        f_bo_phan=bo_phan_id,
        f_trang_thai=trang_thai,
    )


@bp.route("/nhan-vien/<int:uid>")
@chi_admin
def chi_tiet_nhan_vien(uid):
    """Trang hồ sơ riêng của 1 nhân viên: thông tin tài khoản + hồ sơ nhân
    sự (ngày vào làm, sinh nhật) + giấy tờ (hợp đồng, bàn giao, cam kết)."""
    nd = db.session.get(NguoiDung, uid) or abort(404)
    return render_template(
        "admin_user_detail.html", n=nd, LoaiHoSo=LoaiHoSo,
        bo_phans=BoPhan.query.order_by(BoPhan.ten).all(),
        bots=BotZalo.query.order_by(BotZalo.ten).all(),
        ds_chuc_vu=ChucVu.query.order_by(ChucVu.ten).all(),
        vai_tros=VaiTro.NHAN,
    )


def _doc_ngay(ten: str):
    raw = (request.form.get(ten) or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


@bp.route("/nhan-vien/<int:uid>/anh-dai-dien", methods=["POST"])
@chi_admin
def doi_anh_dai_dien_nv(uid):
    nd = db.session.get(NguoiDung, uid) or abort(404)
    if request.form.get("xoa") == "1":
        services.dat_anh_dai_dien(nd, None)
        db.session.commit()
        flash("Đã bỏ ảnh đại diện.", "success")
    else:
        f = request.files.get("anh")
        loi = services.dat_anh_dai_dien(nd, f) if f and f.filename else "Chưa chọn ảnh."
        if loi:
            flash(loi, "error")
        else:
            db.session.commit()
            flash(f"Đã cập nhật ảnh đại diện của {nd.ho_ten}.", "success")
    return redirect(url_for("admin.chi_tiet_nhan_vien", uid=uid))


@bp.route("/nhan-vien/<int:uid>/ho-so", methods=["POST"])
@chi_admin
def tai_ho_so(uid):
    nd = db.session.get(NguoiDung, uid) or abort(404)
    loai = request.form.get("loai")
    if loai not in LoaiHoSo.NHAN:
        abort(400)
    files = [f for f in request.files.getlist("tep") if f and f.filename]
    if not files:
        flash("Chưa chọn file nào.", "error")
        return redirect(url_for("admin.chi_tiet_nhan_vien", uid=uid) + f"#ho-so-{loai}")
    ghi_chu = (request.form.get("ghi_chu") or "").strip()[:255] or None
    ngay_ky, ngay_het_han = _doc_ngay("ngay_ky"), _doc_ngay("ngay_het_han")
    for f in files:
        duong_dan, kich_thuoc = services.luu_file(f, "ho-so-nhan-vien")
        db.session.add(HoSoNhanVien(
            nguoi_dung_id=nd.id, loai=loai, duong_dan=duong_dan, ten_goc=f.filename[:255],
            kich_thuoc=kich_thuoc, mime=f.mimetype, ghi_chu=ghi_chu,
            ngay_ky=ngay_ky, ngay_het_han=ngay_het_han, nguoi_tai_len_id=current_user.id,
        ))
    db.session.commit()
    flash(f"Đã thêm {len(files)} file vào \"{LoaiHoSo.NHAN[loai]}\" của {nd.ho_ten}.", "success")
    return redirect(url_for("admin.chi_tiet_nhan_vien", uid=uid) + f"#ho-so-{loai}")


@bp.route("/nhan-vien/ho-so/<int:hid>/xoa", methods=["POST"])
@chi_admin
def xoa_ho_so(hid):
    h = db.session.get(HoSoNhanVien, hid) or abort(404)
    uid, loai = h.nguoi_dung_id, h.loai
    services.xoa_file_ho_so(h)
    db.session.delete(h)
    db.session.commit()
    flash("Đã xoá file.", "success")
    return redirect(url_for("admin.chi_tiet_nhan_vien", uid=uid) + f"#ho-so-{loai}")


@bp.route("/nhan-vien/luu", methods=["POST"])
@chi_admin
def luu_nhan_vien():
    uid = request.form.get("id", type=int)
    nd = db.session.get(NguoiDung, uid) if uid else NguoiDung()

    ma = (request.form.get("ma_dinh_danh") or "").strip()
    if not ma or not (request.form.get("ho_ten") or "").strip():
        flash("Mã nhân viên và họ tên là bắt buộc.", "error")
        return redirect(url_for("admin.chi_tiet_nhan_vien", uid=uid) if uid else url_for("admin.nhan_vien"))

    trung = NguoiDung.query.filter(
        db.func.lower(NguoiDung.ma_dinh_danh) == ma.lower()
    ).first()
    if trung and trung.id != nd.id:
        flash(f"Mã nhân viên {ma} đã tồn tại.", "error")
        return redirect(url_for("admin.chi_tiet_nhan_vien", uid=uid) if uid else url_for("admin.nhan_vien"))

    bot_zalo_id = request.form.get("bot_zalo_id", type=int) or None
    if bot_zalo_id:
        q_dem_bot = NguoiDung.query.filter_by(bot_zalo_id=bot_zalo_id, dang_hoat_dong=True)
        if nd.id:
            q_dem_bot = q_dem_bot.filter(NguoiDung.id != nd.id)
        if q_dem_bot.count() >= 3:
            bot = db.session.get(BotZalo, bot_zalo_id)
            flash(f"Bot {bot.ten if bot else ''} đã có đủ 3 nhân viên sử dụng — "
                  f"chọn bot khác hoặc thêm bot mới ở Thiết lập.", "error")
            return redirect(url_for("admin.chi_tiet_nhan_vien", uid=uid) if uid else url_for("admin.nhan_vien"))

    nd.ma_dinh_danh = ma
    nd.ho_ten = request.form["ho_ten"].strip()
    nd.vai_tro = request.form.get("vai_tro", VaiTro.NHAN_VIEN)
    nd.bo_phan_id = request.form.get("bo_phan_id", type=int) or None
    nd.so_dien_thoai = (request.form.get("so_dien_thoai") or "").strip() or None
    nd.zalo_group_id = (request.form.get("zalo_group_id") or "").strip() or None
    nd.bot_zalo_id = bot_zalo_id
    nd.chuc_vu_id = request.form.get("chuc_vu_id", type=int) or None
    nd.dang_hoat_dong = request.form.get("dang_hoat_dong") == "on"
    if "ngay_vao_lam" in request.form:
        nd.ngay_vao_lam = _doc_ngay("ngay_vao_lam")
    if "ngay_sinh" in request.form:
        nd.ngay_sinh = _doc_ngay("ngay_sinh")

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
    return redirect(url_for("admin.chi_tiet_nhan_vien", uid=nd.id))


@bp.route("/nhan-vien/<int:uid>/reset-mat-khau", methods=["POST"])
@chi_admin
def reset_mat_khau(uid):
    nd = db.session.get(NguoiDung, uid) or abort(404)
    mk = secrets.token_urlsafe(6)
    nd.dat_mat_khau(mk)
    nd.doi_mat_khau = True
    db.session.commit()
    flash(f"Mật khẩu mới của {nd.ho_ten}: {mk}", "success")
    return redirect(url_for("admin.chi_tiet_nhan_vien", uid=uid))


@bp.route("/nhan-vien/<int:uid>/test-zalo", methods=["POST"])
@chi_admin
def test_zalo(uid):
    nd = db.session.get(NguoiDung, uid) or abort(404)
    ok = services.gui_cho_nhan_vien(nd, "🔔 Tin nhắn thử từ hệ thống giao việc BRICON.")
    db.session.commit()
    flash("Gửi thành công." if ok else "Gửi thất bại — xem log Zalo để biết lý do.",
          "success" if ok else "error")
    return redirect(url_for("admin.chi_tiet_nhan_vien", uid=uid))


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
    for h in list(nd.ho_so):
        services.xoa_file_ho_so(h)
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

    return _trang_thiet_lap("bot")


def _trang_thiet_lap(muc: str):
    """Thiết lập tách 3 mục riêng (Bot Zalo / OpenAI & hạn mức / Tin tự
    động), mỗi mục 1 tab — trước đây dồn chung 1 trang rất khó phân biệt."""
    ds_bot = BotZalo.query.order_by(BotZalo.ten).all()
    nv_theo_bot: dict[int, list[NguoiDung]] = {}
    for nd in (NguoiDung.query.filter(NguoiDung.bot_zalo_id.isnot(None),
                                      NguoiDung.dang_hoat_dong.is_(True))
               .order_by(NguoiDung.ho_ten).all()):
        nv_theo_bot.setdefault(nd.bot_zalo_id, []).append(nd)
    ngay_hn = ngay_vn_hien_tai()
    sinh_nhat_sap_toi = []
    for i in range(31):
        d = ngay_hn + timedelta(days=i)
        for n in services.nguoi_sinh_nhat(d):
            sinh_nhat_sap_toi.append((d, n))
    return render_template("admin_thietlap.html", muc=muc,
                           sinh_nhat_bat=services.lay_cai_dat("sinh_nhat_bat", "1") == "1",
                           sinh_nhat_bao_nhom=services.lay_cai_dat("sinh_nhat_bao_nhom", "1") == "1",
                           sinh_nhat_loi_chuc=services.lay_cai_dat("sinh_nhat_loi_chuc", "")
                                              or services.LOI_CHUC_SINH_NHAT_MAC_DINH,
                           sinh_nhat_sap_toi=sinh_nhat_sap_toi[:8],
                           dong_goi_group_id=services.lay_cai_dat("dong_goi_group_id", ""),
                           dong_goi_bot_id=services.lay_cai_dat("dong_goi_bot_id", ""),
                           nhom_ql_mac_dinh=current_app.config["ZALO_GROUP_QL"],
                           ds=ds_bot,
                           nv_theo_bot=nv_theo_bot,
                           openai_api_key=services.lay_cai_dat("openai_api_key"),
                           gh_cau_hoi=services.lay_cai_dat(
                               "tro_ly_gioi_han_cau_hoi_ngay",
                               str(dich_vu_ai._GIOI_HAN_MAC_DINH_CAU_HOI_NGAY)),
                           gh_token=services.lay_cai_dat(
                               "tro_ly_gioi_han_token_ngay",
                               str(dich_vu_ai._GIOI_HAN_MAC_DINH_TOKEN_NGAY)))


@bp.route("/thiet-lap/dong-goi", methods=["POST"])
@chi_admin
def luu_kenh_dong_goi():
    """Chọn nhóm Zalo + bot nhận thông báo đóng gói. Bấm "Gửi thử" thì lưu
    rồi gửi luôn 1 tin thử vào nhóm đó để kiểm tra bot đã vào được nhóm."""
    group_id = (request.form.get("group_id") or "").strip()
    bot_id = (request.form.get("bot_id") or "").strip()
    if bot_id and not (bot_id.isdigit() and db.session.get(BotZalo, int(bot_id))):
        flash("Bot không hợp lệ.", "error")
        return redirect(url_for("admin.thiet_lap"))
    services.dat_cai_dat("dong_goi_group_id", group_id)
    services.dat_cai_dat("dong_goi_bot_id", bot_id)
    db.session.commit()

    if request.form.get("gui_thu") == "1":
        chat_id, token = services.kenh_dong_goi()
        ok = services.gui_zalo(chat_id, "📦 Tin thử — nhóm này sẽ nhận thông báo đóng gói "
                                        "từ BRICON WORK.", token_ghi_de=token)
        db.session.commit()
        if ok:
            flash("Đã lưu và gửi tin thử thành công — kiểm tra nhóm Zalo.", "success")
        else:
            flash("Đã lưu nhưng gửi thử THẤT BẠI — kiểm tra Group ID và chắc chắn bot đã "
                  "được thêm vào nhóm (xem chi tiết ở tab Log Zalo).", "error")
    else:
        flash("Đã lưu cấu hình thông báo đóng gói.", "success")
    return redirect(url_for("admin.thiet_lap"))


@bp.route("/thiet-lap/muc/ai")
@chi_admin
def thiet_lap_ai():
    return _trang_thiet_lap("ai")


@bp.route("/thiet-lap/muc/tin-tu-dong")
@chi_admin
def thiet_lap_tin_tu_dong():
    return _trang_thiet_lap("tin_tu_dong")


@bp.route("/thiet-lap/ai", methods=["POST"])
@chi_admin
def luu_cai_dat_ai():
    key = (request.form.get("openai_api_key") or "").strip()
    services.dat_cai_dat("openai_api_key", key)
    db.session.commit()
    flash("Đã lưu API key AI." if key else "Đã xoá API key AI.", "success")
    return redirect(url_for("admin.thiet_lap_ai"))


@bp.route("/thiet-lap/ai-gioi-han", methods=["POST"])
@chi_admin
def luu_gioi_han_tro_ly():
    """Hạn mức Trợ lý AI/ngày cho Nhân viên + Quản lý bộ phận (Sếp/Admin
    không giới hạn) — để 0 ở ô nào nghĩa là bỏ giới hạn đó."""
    gh_cau_hoi = request.form.get("gh_cau_hoi", type=int)
    gh_token = request.form.get("gh_token", type=int)
    if gh_cau_hoi is None or gh_cau_hoi < 0 or gh_token is None or gh_token < 0:
        flash("Hạn mức phải là số nguyên không âm.", "error")
        return redirect(url_for("admin.thiet_lap_ai"))
    services.dat_cai_dat("tro_ly_gioi_han_cau_hoi_ngay", str(gh_cau_hoi))
    services.dat_cai_dat("tro_ly_gioi_han_token_ngay", str(gh_token))
    db.session.commit()
    flash("Đã lưu hạn mức Trợ lý AI.", "success")
    return redirect(url_for("admin.thiet_lap_ai"))


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
    elif ten_lenh == "sinh-nhat":
        so = services.gui_chuc_mung_sinh_nhat()
        thong_bao = (f"Đã chúc mừng sinh nhật {so} người." if so else
                     "Hôm nay không có ai sinh nhật (hoặc tính năng đang tắt) — dùng nút "
                     "\"Gửi thử lời chúc\" để xem mẫu.")
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
    return redirect(url_for("admin.thiet_lap_tin_tu_dong"))


@bp.route("/thiet-lap/sinh-nhat", methods=["POST"])
@chi_admin
def luu_cai_dat_sinh_nhat():
    services.dat_cai_dat("sinh_nhat_bat", "1" if request.form.get("bat") == "on" else "0")
    services.dat_cai_dat("sinh_nhat_bao_nhom", "1" if request.form.get("bao_nhom") == "on" else "0")
    mau = (request.form.get("loi_chuc") or "").strip()
    services.dat_cai_dat("sinh_nhat_loi_chuc", mau)
    db.session.commit()
    if request.form.get("gui_thu") == "1":
        # Gửi thử lời chúc (dựng theo tên người đang thao tác) vào nhóm QL
        ok = services.gui_nhom_ql("[TIN THỬ — lời chúc sinh nhật]\n\n"
                                  + services.noi_dung_chuc_sinh_nhat(current_user, mau or None))
        db.session.commit()
        flash("Đã lưu và gửi thử lời chúc vào nhóm quản lý." if ok
              else "Đã lưu nhưng gửi thử thất bại — xem tab Log Zalo.", "success" if ok else "error")
    else:
        flash("Đã lưu cài đặt chúc mừng sinh nhật.", "success")
    return redirect(url_for("admin.thiet_lap_tin_tu_dong"))


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


def _trang_info_ai(muc: str):
    """Trang Kiến thức AI tách thành 4 mục riêng (Thông tin chung / Sản phẩm /
    FAQ / Chức vụ), mỗi mục 1 tab — thay vì dồn tất cả form vào 1 trang dài."""
    faq = services.lay_cai_dat("faq_bricon", "") or ""
    return render_template(
        "admin_info_ai.html", muc=muc,
        thong_tin_chung=services.lay_cai_dat("thong_tin_chung_cong_ty", ""),
        thong_tin_san_pham=services.lay_cai_dat("thong_tin_san_pham", ""),
        faq=faq,
        so_cau_faq=len(dich_vu_ai.tach_cau_faq(faq)),
        ds_chuc_vu=ChucVu.query.order_by(ChucVu.ten).all() if muc == "chuc_vu" else [],
        ds_san_pham_ai=SanPhamAI.query.order_by(SanPhamAI.ten).all() if muc == "san_pham" else [],
    )


@bp.route("/info-ai")
@chi_admin
def info_ai():
    return _trang_info_ai("chung")


@bp.route("/info-ai/muc/san-pham")
@chi_admin
def info_ai_san_pham():
    return _trang_info_ai("san_pham")


@bp.route("/info-ai/muc/faq")
@chi_admin
def info_ai_faq():
    return _trang_info_ai("faq")


@bp.route("/info-ai/muc/chuc-vu")
@chi_admin
def info_ai_chuc_vu():
    return _trang_info_ai("chuc_vu")


@bp.route("/info-ai/anh-tro-ly", methods=["POST"])
@chi_admin
def luu_anh_tro_ly():
    """Ảnh đại diện của Trợ lý công việc (hiện ở nút chat nổi + đầu khung
    chat). Tải ảnh mới thì thay ảnh cũ (xoá luôn file cũ trên đĩa); tick
    "dùng mặc định" thì bỏ ảnh, quay về biểu tượng mặc định."""
    cu = services.lay_cai_dat("tro_ly_anh_dai_dien")

    def _xoa_file_cu():
        if cu:
            duong_dan = os.path.join(current_app.config["UPLOAD_ROOT"], *cu.split("/"))
            if os.path.isfile(duong_dan):
                os.remove(duong_dan)

    if request.form.get("dung_mac_dinh") == "1":
        _xoa_file_cu()
        services.dat_cai_dat("tro_ly_anh_dai_dien", "")
        db.session.commit()
        flash("Đã chuyển về ảnh đại diện mặc định.", "success")
        return redirect(url_for("admin.info_ai"))

    f = request.files.get("anh")
    if not f or not f.filename:
        flash("Chưa chọn ảnh.", "error")
        return redirect(url_for("admin.info_ai"))
    if services.phan_loai(f.filename, f.mimetype) != LoaiDinhKem.ANH:
        flash("Tệp vừa chọn không phải ảnh.", "error")
        return redirect(url_for("admin.info_ai"))
    duong_dan, _ = services.luu_file(f, "tro-ly")
    _xoa_file_cu()
    services.dat_cai_dat("tro_ly_anh_dai_dien", duong_dan)
    db.session.commit()
    flash("Đã cập nhật ảnh đại diện Trợ lý công việc.", "success")
    return redirect(url_for("admin.info_ai"))


@bp.route("/info-ai/chung", methods=["POST"])
@chi_admin
def luu_thong_tin_chung():
    noi_dung = (request.form.get("thong_tin_chung") or "").strip()
    services.dat_cai_dat("thong_tin_chung_cong_ty", noi_dung)
    db.session.commit()
    flash("Đã lưu thông tin chung công ty.", "success")
    return redirect(url_for("admin.info_ai"))


@bp.route("/info-ai/faq", methods=["POST"])
@chi_admin
def luu_faq():
    """Lưu bộ Câu hỏi thường gặp (FAQ) cho Trợ lý AI — dán nguyên văn
    dạng Markdown, mỗi câu hỏi bắt đầu bằng dòng "## <câu hỏi>". Trợ lý chỉ
    lấy vài câu LIÊN QUAN NHẤT với câu đang hỏi, không nạp cả bộ."""
    noi_dung = (request.form.get("faq") or "").strip()
    services.dat_cai_dat("faq_bricon", noi_dung)
    db.session.commit()
    so_cau = len(dich_vu_ai.tach_cau_faq(noi_dung))
    if noi_dung and not so_cau:
        flash("Đã lưu nhưng KHÔNG tách được câu hỏi nào — mỗi câu hỏi phải bắt đầu "
              "bằng 1 dòng \"## Câu hỏi...\".", "error")
    else:
        flash(f"Đã lưu FAQ: {so_cau} câu hỏi.", "success")
    return redirect(url_for("admin.info_ai_faq"))


@bp.route("/info-ai/san-pham", methods=["POST"])
@chi_admin
def luu_thong_tin_san_pham():
    noi_dung = (request.form.get("thong_tin_san_pham") or "").strip()
    services.dat_cai_dat("thong_tin_san_pham", noi_dung)
    db.session.commit()
    flash("Đã lưu thông tin sản phẩm.", "success")
    return redirect(url_for("admin.info_ai_san_pham"))


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
        return redirect(url_for("admin.info_ai_san_pham"))

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
    return redirect(url_for("admin.info_ai_san_pham"))


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
    return redirect(url_for("admin.info_ai_san_pham"))


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
    return redirect(url_for("admin.info_ai_san_pham"))


@bp.route("/info-ai/chuc-vu", methods=["POST"])
@chi_admin
def luu_chuc_vu():
    cvid = request.form.get("id", type=int)
    cv = db.session.get(ChucVu, cvid) if cvid else ChucVu()

    ten = (request.form.get("ten") or "").strip()
    if not ten:
        flash("Cần nhập tên chức vụ.", "error")
        return redirect(url_for("admin.info_ai_chuc_vu"))

    trung = ChucVu.query.filter(db.func.lower(ChucVu.ten) == ten.lower()).first()
    if trung and trung.id != cv.id:
        flash(f"Chức vụ {ten} đã tồn tại.", "error")
        return redirect(url_for("admin.info_ai_chuc_vu"))

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
    return redirect(url_for("admin.info_ai_chuc_vu"))


@bp.route("/info-ai/chuc-vu/<int:cvid>/xoa", methods=["POST"])
@chi_admin
def xoa_chuc_vu(cvid):
    cv = db.session.get(ChucVu, cvid) or abort(404)
    so_nv = NguoiDung.query.filter_by(chuc_vu_id=cv.id).count()
    if so_nv:
        flash(f"Không thể xoá {cv.ten} vì đang gán cho {so_nv} nhân viên. "
              f"Đổi chức vụ cho họ trước (ở trang Nhân viên).", "error")
        return redirect(url_for("admin.info_ai_chuc_vu"))
    ten = cv.ten
    db.session.delete(cv)
    db.session.commit()
    flash(f"Đã xoá chức vụ {ten}.", "success")
    return redirect(url_for("admin.info_ai_chuc_vu"))