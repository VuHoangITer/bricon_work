import base64
import binascii
import os

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import services
from extensions import db
from models import (DeXuat, DinhKemDeXuat, GiaiDoanDinhKemDeXuat, LoaiDeXuat, LoaiDinhKem,
                    NguoiDung, TrangThaiDeXuat, VaiTro)

bp = Blueprint("de_xuat", __name__, url_prefix="/de-xuat")


def _de_xuat_can_duyet():
    """Đề xuất đang chờ duyệt mà current_user có quyền duyệt — dùng đúng
    công thức NguoiDung.duoc_duyet_de_xuat, không lặp lại logic ở đây."""
    if not current_user.la_quan_ly:
        return []
    ds = DeXuat.query.filter_by(trang_thai=TrangThaiDeXuat.CHO_DUYET).order_by(DeXuat.tao_luc).all()
    return [dx for dx in ds if current_user.duoc_duyet_de_xuat(dx)]


def _de_xuat_giam_sat():
    """Đề xuất ĐÃ XỬ LÝ (đã duyệt/từ chối) trong phạm vi current_user được
    xem — Admin/Sếp: toàn công ty; Quản lý bộ phận: của nhân viên trong bộ
    phận mình. Tách riêng khỏi _de_xuat_can_duyet (chỉ đang chờ) để Sếp còn
    xem lại lịch sử đã duyệt, không chỉ việc cần hành động."""
    if current_user.la_admin_sep:
        q = DeXuat.query
    elif current_user.vai_tro == VaiTro.QUAN_LY and current_user.bo_phan_id:
        q = DeXuat.query.join(NguoiDung, DeXuat.nguoi_de_xuat_id == NguoiDung.id).filter(
            NguoiDung.bo_phan_id == current_user.bo_phan_id
        )
    else:
        return []
    return q.filter(DeXuat.trang_thai != TrangThaiDeXuat.CHO_DUYET) \
        .order_by(DeXuat.tao_luc.desc()).limit(500).all()


def _duoc_xem(dx: DeXuat) -> bool:
    return (
        current_user.id == dx.nguoi_de_xuat_id
        or current_user.la_admin_sep
        or current_user.duoc_duyet_de_xuat(dx)
    )


def _doc_chu_ky(ten_truong: str) -> bytes | None:
    """Đọc + giải mã 1 chữ ký canvas gửi lên qua form (data URL base64).
    Trả None nếu trống/không hợp lệ/canvas chưa ký (khớp ngưỡng đã dùng ở
    xin_nghi: canvas trắng vẫn xuất ra vài trăm byte PNG rỗng)."""
    raw = request.form.get(ten_truong) or ""
    if "," not in raw:
        return None
    try:
        du_lieu = base64.b64decode(raw.split(",", 1)[1])
    except (ValueError, binascii.Error):
        return None
    if len(du_lieu) < 200:
        return None
    return du_lieu


def _luu_dinh_kem_de_xuat(dx: DeXuat, giai_doan: str, ten_truong: str = "dinh_kem") -> int:
    """Lưu mọi ảnh/tệp (bất kỳ định dạng nào) người dùng chọn ở form vào
    bảng DinhKemDeXuat, gắn theo giai_doan (de_xuat/duyet) — dùng chung cho
    cả lúc gửi lẫn lúc duyệt vì logic lưu giống hệt nhau, chỉ khác giai
    đoạn. Không loại bỏ định dạng lạ như DinhKem (đối chứng nộp việc) vẫn
    làm: mặc định về LoaiDinhKem.FILE nếu không nhận diện được, để đúng
    yêu cầu "đính kèm bất kì thứ gì"."""
    files = [f for f in request.files.getlist(ten_truong) if f and f.filename]
    so_luu = 0
    for f in files:
        loai = services.phan_loai(f.filename, f.mimetype) or LoaiDinhKem.FILE
        duong_dan, kich_thuoc = services.luu_file(f, "de-xuat")
        db.session.add(DinhKemDeXuat(
            de_xuat_id=dx.id, giai_doan=giai_doan, nguoi_tai_len_id=current_user.id,
            loai=loai, duong_dan=duong_dan, ten_goc=f.filename[:255],
            kich_thuoc=kich_thuoc, mime=f.mimetype,
        ))
        so_luu += 1
    return so_luu


def _loc_de_xuat(ds: list[DeXuat], loai: str, nguoi: int | None, trang_thai: str,
                 thang: str) -> list[DeXuat]:
    """Lọc 1 danh sách đề xuất theo bộ lọc trên trang (loại / nhân viên /
    trạng thái / tháng 'YYYY-MM'). Lọc trong Python vì danh sách vốn đã giới
    hạn theo phạm vi quyền của người xem và không lớn."""
    if loai in LoaiDeXuat.NHAN:
        ds = [d for d in ds if d.loai == loai]
    if nguoi:
        ds = [d for d in ds if d.nguoi_de_xuat_id == nguoi]
    if trang_thai in TrangThaiDeXuat.NHAN:
        ds = [d for d in ds if d.trang_thai == trang_thai]
    if thang:
        ds = [d for d in ds if d.tao_luc and d.tao_luc.strftime("%Y-%m") == thang]
    return ds


def _tong_tien(ds: list[DeXuat]) -> int:
    return int(sum(d.chi_phi_du_kien or 0 for d in ds))


@bp.route("/")
@login_required
def danh_sach():
    f_loai = request.args.get("loai", "")
    f_nguoi = request.args.get("nguoi", type=int)
    f_trang_thai = request.args.get("trang_thai", "")
    f_thang = (request.args.get("thang") or "").strip()
    if f_thang and not (len(f_thang) == 7 and f_thang[4] == "-"):
        f_thang = ""

    cua_toi = DeXuat.query.filter_by(nguoi_de_xuat_id=current_user.id).order_by(DeXuat.tao_luc.desc()).all()
    can_duyet = _de_xuat_can_duyet()
    giam_sat = _de_xuat_giam_sat()

    # Danh sách nhân viên cho ô lọc = những người có đề xuất trong phạm vi
    # current_user được xem (không lộ tên ngoài phạm vi quyền).
    nhan_vien = []
    if current_user.la_quan_ly:
        thay = {d.nguoi_de_xuat_id: d.nguoi_de_xuat for d in can_duyet + giam_sat if d.nguoi_de_xuat}
        nhan_vien = sorted(thay.values(), key=lambda n: n.ho_ten)

    dang_loc = bool(f_loai or f_nguoi or f_trang_thai or f_thang)
    cua_toi = _loc_de_xuat(cua_toi, f_loai, None, f_trang_thai, f_thang)
    can_duyet = _loc_de_xuat(can_duyet, f_loai, f_nguoi, "", f_thang)
    if f_trang_thai and f_trang_thai != TrangThaiDeXuat.CHO_DUYET:
        can_duyet = []  # đang lọc Đã duyệt/Từ chối -> ẩn mục "Cần tôi duyệt"
    giam_sat = _loc_de_xuat(giam_sat, f_loai, f_nguoi, f_trang_thai, f_thang)

    # Tổng tiền tạm ứng ĐÃ DUYỆT theo đúng bộ lọc hiện tại (Sếp hay cần biết
    # "tháng này đã duyệt tạm ứng bao nhiêu tiền, cho ai").
    tam_ung_da_duyet = [d for d in giam_sat
                        if d.loai == LoaiDeXuat.TAM_UNG and d.trang_thai == TrangThaiDeXuat.DA_DUYET]
    return render_template(
        "de_xuat_list.html", cua_toi=cua_toi, can_duyet=can_duyet, giam_sat=giam_sat,
        nhan_vien=nhan_vien, f_loai=f_loai, f_nguoi=f_nguoi, f_trang_thai=f_trang_thai,
        f_thang=f_thang, dang_loc=dang_loc,
        tong_tam_ung_da_duyet=_tong_tien(tam_ung_da_duyet), so_tam_ung_da_duyet=len(tam_ung_da_duyet),
        LoaiDeXuat=LoaiDeXuat, TrangThaiDeXuat=TrangThaiDeXuat,
    )


@bp.route("/moi/<loai>", methods=["GET", "POST"])
@login_required
def moi(loai):
    if loai not in LoaiDeXuat.NHAN:
        abort(404)
    if current_user.la_admin_sep:
        flash("Sếp/Quản trị không cần gửi đề xuất.", "info")
        return redirect(url_for("de_xuat.danh_sach"))

    if request.method == "POST":
        noi_dung = (request.form.get("noi_dung") or "").strip()
        if not noi_dung:
            flash("Cần nhập nội dung đề xuất.", "error")
            return redirect(url_for("de_xuat.moi", loai=loai))

        chi_phi = None
        chi_phi_raw = (request.form.get("chi_phi_du_kien") or "").strip()
        if chi_phi_raw:
            try:
                chi_phi = int(chi_phi_raw.replace(".", "").replace(",", ""))
            except ValueError:
                flash("Chi phí dự kiến không hợp lệ.", "error")
                return redirect(url_for("de_xuat.moi", loai=loai))

        chu_ky_png = _doc_chu_ky("chu_ky")
        if not chu_ky_png:
            flash("Cần ký tên trước khi gửi đề xuất.", "error")
            return redirect(url_for("de_xuat.moi", loai=loai))
        anh_ky_da_cat = services.cat_anh_chu_ky(chu_ky_png)
        duong_dan_ky, _ = services.luu_bytes(anh_ky_da_cat, "chu-ky-de-xuat.png", "de-xuat")

        dx = DeXuat(
            loai=loai, nguoi_de_xuat_id=current_user.id, noi_dung=noi_dung,
            chi_phi_du_kien=chi_phi, duong_dan_chu_ky_de_xuat=duong_dan_ky,
            duong_dan_pdf="",  # điền ngay bên dưới, cần dx.id trước để sinh PDF nhất quán với các nơi khác
        )
        db.session.add(dx)
        db.session.flush()

        _luu_dinh_kem_de_xuat(dx, GiaiDoanDinhKemDeXuat.DE_XUAT)

        pdf_bytes = services.tao_pdf_de_xuat(dx)
        dx.duong_dan_pdf = services.luu_pdf_de_xuat(pdf_bytes)
        db.session.commit()

        services.bao_de_xuat_moi(dx)

        flash("Đã gửi đề xuất, chờ duyệt.", "success")
        return redirect(url_for("de_xuat.chi_tiet", id=dx.id))

    return render_template("de_xuat_form.html", loai=loai, ten_loai=LoaiDeXuat.NHAN[loai])


@bp.route("/<int:id>")
@login_required
def chi_tiet(id):
    dx = db.session.get(DeXuat, id) or abort(404)
    if not _duoc_xem(dx):
        abort(403)
    duoc_duyet = current_user.duoc_duyet_de_xuat(dx) and dx.trang_thai == TrangThaiDeXuat.CHO_DUYET
    duoc_xoa = current_user.vai_tro == VaiTro.ADMIN
    return render_template("de_xuat_detail.html", dx=dx, duoc_duyet=duoc_duyet, duoc_xoa=duoc_xoa)


@bp.route("/<int:id>/duyet", methods=["POST"])
@login_required
def duyet(id):
    dx = db.session.get(DeXuat, id) or abort(404)
    if not current_user.duoc_duyet_de_xuat(dx):
        abort(403)
    if dx.trang_thai != TrangThaiDeXuat.CHO_DUYET:
        flash("Đề xuất này đã được xử lý rồi.", "error")
        return redirect(url_for("de_xuat.chi_tiet", id=dx.id))

    ket_qua = request.form.get("ket_qua")
    if ket_qua not in (TrangThaiDeXuat.DA_DUYET, TrangThaiDeXuat.TU_CHOI):
        flash("Cần chọn Đồng ý hoặc Từ chối.", "error")
        return redirect(url_for("de_xuat.chi_tiet", id=dx.id))

    chu_ky_png = _doc_chu_ky("chu_ky")
    if not chu_ky_png:
        flash("Cần ký tên trước khi duyệt.", "error")
        return redirect(url_for("de_xuat.chi_tiet", id=dx.id))
    anh_ky_da_cat = services.cat_anh_chu_ky(chu_ky_png)
    duong_dan_ky, _ = services.luu_bytes(anh_ky_da_cat, "chu-ky-duyet-de-xuat.png", "de-xuat")

    dx.trang_thai = ket_qua
    dx.nguoi_duyet_id = current_user.id
    dx.y_kien_duyet = (request.form.get("y_kien_duyet") or "").strip() or None
    dx.duong_dan_chu_ky_duyet = duong_dan_ky
    dx.duyet_luc = services.gio_vn_hien_tai()

    _luu_dinh_kem_de_xuat(dx, GiaiDoanDinhKemDeXuat.DUYET)

    pdf_bytes = services.tao_pdf_de_xuat(dx)
    dx.duong_dan_pdf = services.luu_pdf_de_xuat(pdf_bytes)
    db.session.commit()

    services.bao_duyet_de_xuat(dx)

    flash("Đã lưu kết quả duyệt.", "success")
    return redirect(url_for("de_xuat.chi_tiet", id=dx.id))


@bp.route("/<int:id>/xoa", methods=["POST"])
@login_required
def xoa(id):
    """Xoá vĩnh viễn 1 đề xuất — CHỈ Admin (không phải Sếp/Quản lý bộ
    phận, dù họ vẫn có quyền duyệt). Xoá cả 3 file liên quan trên đĩa
    (PDF + 2 ảnh chữ ký) để không để lại rác, không chỉ xoá dòng DB."""
    if current_user.vai_tro != VaiTro.ADMIN:
        abort(403)
    dx = db.session.get(DeXuat, id) or abort(404)

    duong_dan_can_xoa = [dx.duong_dan_pdf, dx.duong_dan_chu_ky_de_xuat, dx.duong_dan_chu_ky_duyet]
    duong_dan_can_xoa += [dk.duong_dan for dk in dx.dinh_kem]
    for duong_dan in duong_dan_can_xoa:
        if not duong_dan:
            continue
        duong_dan_tuyet_doi = os.path.join(current_app.config["UPLOAD_ROOT"], *duong_dan.split("/"))
        if os.path.isfile(duong_dan_tuyet_doi):
            os.remove(duong_dan_tuyet_doi)

    db.session.delete(dx)
    db.session.commit()

    flash("Đã xoá vĩnh viễn đề xuất.", "success")
    return redirect(url_for("de_xuat.danh_sach"))