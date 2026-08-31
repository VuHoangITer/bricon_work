import math
import os
import re
from datetime import datetime, date, timedelta

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)
from flask_login import current_user, login_required
from sqlalchemy import case

import dich_vu_ai
import services
from extensions import db
from models import (AnhDanhGia, AnhSanPhamAI, AnhYeuCau, ChucVu, CongViec, DanhGia, DinhKem,
                    DoUuTien, GhiChuNop, LoaiDinhKem, NgayNghiLe, NguoiDung, TrangThai, VaiTro,
                    XinNghi, gio_vn_hien_tai, ngay_vn_hien_tai)

bp = Blueprint("tasks", __name__)

KICH_THUOC_TRANG = 9  # 3 thẻ / hàng x 3 hàng

_THU_TRONG_TUAN = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def _loi_chao_theo_gio() -> tuple[str, str, str]:
    """Trả về (lời chào, câu phụ, mã icon trang trí — mat_troi/hoang_hon/mat_trang)."""
    gio = gio_vn_hien_tai().hour
    if gio < 11:
        return ("Chào buổi sáng", "Chúc bạn một ngày mới tràn đầy năng lượng và nhiều việc suôn sẻ.",
                "mat_troi")
    if gio < 13:
        return ("Chào buổi trưa", "Nghỉ trưa một chút rồi tiếp tục chinh phục công việc nhé.",
                "mat_troi")
    if gio < 18:
        return ("Chào buổi chiều", "Buổi chiều hiệu quả đang chờ bạn phía trước.",
                "hoang_hon")
    return ("Chào buổi tối", "Chúc bạn một buổi tối hiệu quả và tràn đầy năng lượng.",
            "mat_trang")


@bp.before_request
def _tu_dong_dong_viec_qua_han():
    """Chạy trước MỌI trang trong khu vực công việc — để việc quá hạn chưa
    nộp được tự đóng + chấm 0★ gần như ngay khi vừa mở app, không cần đợi
    cron. Chỉ chạy khi đã đăng nhập, tránh khách vãng lai kích hoạt ghi DB."""
    if current_user.is_authenticated:
        services.dong_cac_viec_qua_han()


# ---------------------------------------------------------------------------
def _nhan_vien_duoc_giao() -> list[NguoiDung]:
    # Sếp/Quản trị không thể bị giao việc — loại khỏi danh sách chọn.
    q = NguoiDung.query.filter_by(dang_hoat_dong=True).filter(
        NguoiDung.vai_tro.notin_((VaiTro.ADMIN, VaiTro.SEP))
    )
    if current_user.vai_tro == VaiTro.QUAN_LY and current_user.bo_phan_id:
        q = q.filter(NguoiDung.bo_phan_id == current_user.bo_phan_id)
    return q.order_by(NguoiDung.ho_ten).all()


def _viec_lien_quan():
    """Query các việc mà người đang đăng nhập được phép thấy."""
    q = CongViec.query
    if current_user.xem_toan_cong_ty:
        return q
    if current_user.vai_tro == VaiTro.QUAN_LY and current_user.bo_phan_id:
        return q.join(NguoiDung, CongViec.nguoi_nhan_id == NguoiDung.id).filter(
            db.or_(
                NguoiDung.bo_phan_id == current_user.bo_phan_id,
                CongViec.nguoi_giao_id == current_user.id,
                CongViec.nguoi_nhan_id == current_user.id,
            )
        )
    return q.filter(
        db.or_(
            CongViec.nguoi_nhan_id == current_user.id,
            CongViec.nguoi_giao_id == current_user.id,
        )
    )


# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
def dashboard():
    hom_nay = ngay_vn_hien_tai()

    # "Hôm nay có việc gì": việc không đặt ngày bắt đầu thì chỉ hiện đúng
    # ngày hạn (như cũ). Việc CÓ đặt ngày bắt đầu thì hiện xuyên suốt từ
    # ngày bắt đầu tới ngày của hạn, không chỉ đúng ngày hạn.
    dieu_kien_hom_nay = services.dieu_kien_viec_trong_ngay(hom_nay)

    cua_toi = (
        CongViec.query.filter(
            CongViec.nguoi_nhan_id == current_user.id,
            CongViec.trang_thai.in_(TrangThai.DANG_MO),
            dieu_kien_hom_nay,
        )
        .order_by(CongViec.han)
        .all()
    )
    cua_toi_theo_cot = [
        (ma, DoUuTien.NHAN[ma], [v for v in cua_toi if v.do_uu_tien == ma])
        for ma in (DoUuTien.CAO, DoUuTien.THUONG, DoUuTien.THAP)
    ]

    cho_toi_duyet = []
    if current_user.la_quan_ly:
        cho_toi_duyet = (
            _viec_lien_quan()
            .filter(CongViec.trang_thai == TrangThai.CHO_DUYET)
            .order_by(CongViec.gui_doi_chung_luc)
            .all()
        )
        cho_toi_duyet = [v for v in cho_toi_duyet if current_user.duoc_duyet_viec(v)]

    from models import ChamCong
    cc_hom_nay = ChamCong.query.filter_by(
        nguoi_dung_id=current_user.id, ngay=ngay_vn_hien_tai()
    ).first()

    # Việc của CHÍNH nhân viên bị hệ thống tự động đóng + chấm 0★ trong
    # ngày hôm nay (quá hạn không nộp đối chứng) — hiện ngay trên dashboard
    # kèm người giao để họ chủ động liên hệ xin mở lại nếu thấy chưa hợp lý,
    # không phải tự đi lục trong danh sách việc mới biết.
    viec_0_sao_hom_nay = []
    if not current_user.la_admin_sep:
        dau_ngay = datetime.combine(hom_nay, datetime.min.time())
        cuoi_ngay = datetime.combine(hom_nay, datetime.max.time())
        viec_0_sao_hom_nay = (
            CongViec.query.filter(
                CongViec.nguoi_nhan_id == current_user.id,
                CongViec.so_sao_cuoi == 0,
                CongViec.hoan_thanh_luc >= dau_ngay,
                CongViec.hoan_thanh_luc <= cuoi_ngay,
            )
            .order_by(CongViec.hoan_thanh_luc.desc())
            .all()
        )

    viec_hom_nay_cong_ty = 0
    viec_hom_nay_cong_ty_theo_cot = []
    if current_user.la_admin_sep:
        # Không tính việc đang "Chờ duyệt" — nhân viên đã nộp xong phần
        # việc của họ rồi, đưa vào đây dễ gây tưởng nhầm là còn ai đó
        # chưa làm gì trong ngày.
        viec_hom_nay_cong_ty_ds = (
            CongViec.query.filter(
                CongViec.trang_thai.in_(TrangThai.CHUA_XONG),
                dieu_kien_hom_nay,
            )
            .order_by(CongViec.han.is_(None), CongViec.han)
            .all()
        )
        viec_hom_nay_cong_ty = len(viec_hom_nay_cong_ty_ds)
        viec_hom_nay_cong_ty_theo_cot = [
            (ma, DoUuTien.NHAN[ma], [v for v in viec_hom_nay_cong_ty_ds if v.do_uu_tien == ma])
            for ma in (DoUuTien.CAO, DoUuTien.THUONG, DoUuTien.THAP)
        ]

    loi_chao, loi_chao_phu, chao_mung_icon = _loi_chao_theo_gio()
    return render_template(
        "dashboard.html",
        cua_toi=cua_toi,
        cua_toi_theo_cot=cua_toi_theo_cot,
        cho_toi_duyet=cho_toi_duyet,
        cc_hom_nay=cc_hom_nay,
        viec_0_sao_hom_nay=viec_0_sao_hom_nay,
        viec_hom_nay_cong_ty=viec_hom_nay_cong_ty,
        viec_hom_nay_cong_ty_theo_cot=viec_hom_nay_cong_ty_theo_cot,
        loi_chao=loi_chao,
        loi_chao_phu=loi_chao_phu,
        chao_mung_icon=chao_mung_icon,
        hom_nay_hien_thi=f"{_THU_TRONG_TUAN[hom_nay.weekday()]}, {hom_nay:%d/%m/%Y}",
        gioi_han_tro_ly=dich_vu_ai.trang_thai_gioi_han_tro_ly(current_user),
    )


@bp.route("/viec")
@login_required
def danh_sach():
    trang_thai = request.args.get("trang_thai", "")  # "" = mặc định "Chưa hoàn thành" -> chia 3 cột
    nguoi = request.args.get("nguoi", type=int)
    tim = (request.args.get("q") or "").strip()
    trang = request.args.get("trang", 1, type=int)
    sao = request.args.get("sao", type=int)
    tu_ngay_raw = request.args.get("tu_ngay") or ""
    den_ngay_raw = request.args.get("den_ngay") or ""

    q = _viec_lien_quan()
    if nguoi:
        q = q.filter(CongViec.nguoi_nhan_id == nguoi)
    if tim:
        like = f"%{tim}%"
        q = q.filter(db.or_(CongViec.tieu_de.ilike(like), CongViec.ma.ilike(like)))
    if sao is not None:  # 0 sao là giá trị lọc hợp lệ, không được coi là "chưa chọn"
        q = q.filter(CongViec.so_sao_cuoi == sao)

    if tu_ngay_raw:
        try:
            q = q.filter(CongViec.han >= datetime.combine(date.fromisoformat(tu_ngay_raw), datetime.min.time()))
        except ValueError:
            tu_ngay_raw = ""
    if den_ngay_raw:
        try:
            q = q.filter(CongViec.han <= datetime.combine(date.fromisoformat(den_ngay_raw), datetime.max.time()))
        except ValueError:
            den_ngay_raw = ""

    che_do_3_cot = trang_thai in ("", "dang_mo")
    ngu_canh_chung = dict(
        nhan_vien=_nhan_vien_duoc_giao() if current_user.la_quan_ly else [],
        f_trang_thai=trang_thai, f_nguoi=nguoi, f_q=tim, f_sao=sao,
        f_tu_ngay=tu_ngay_raw, f_den_ngay=den_ngay_raw,
    )

    if che_do_3_cot:
        # Chưa hoàn thành: CHỈ Mới giao/Đang làm/Phải làm lại — Chờ duyệt
        # tách hẳn ra thành 1 trạng thái lọc riêng (không lẫn vào đây), vì
        # về mặt xử lý nó là việc nhân viên ĐÃ xong phần của mình, đang chờ
        # sếp xem, không còn là việc "chưa hoàn thành" theo nghĩa cần làm.
        q = q.filter(CongViec.trang_thai.in_(TrangThai.CHUA_XONG))
        q = q.order_by(CongViec.han.is_(None), CongViec.han)
        viecs = q.all()
        theo_cot = [
            (ma, DoUuTien.NHAN[ma], [v for v in viecs if v.do_uu_tien == ma])
            for ma in (DoUuTien.CAO, DoUuTien.THUONG, DoUuTien.THAP)
        ]
        return render_template(
            "task_list.html", che_do_3_cot=True, theo_cot=theo_cot,
            tong_so=len(viecs), **ngu_canh_chung,
        )

    # Hoàn thành / Đã huỷ / hoặc 1 trạng thái cụ thể: giữ kiểu lưới + phân
    # trang như cũ, lọc riêng khỏi chế độ 3 cột bên trên.
    q = q.filter(CongViec.trang_thai == trang_thai)
    thu_tu_uu_tien = case(
        (CongViec.do_uu_tien == DoUuTien.CAO, 0),
        (CongViec.do_uu_tien == DoUuTien.THUONG, 1),
        (CongViec.do_uu_tien == DoUuTien.THAP, 2),
        else_=1,
    )
    q = q.order_by(thu_tu_uu_tien, CongViec.han.is_(None), CongViec.han)
    phan_trang = q.paginate(page=trang, per_page=KICH_THUOC_TRANG, error_out=False)
    return render_template(
        "task_list.html", che_do_3_cot=False, phan_trang=phan_trang,
        viecs=phan_trang.items, **ngu_canh_chung,
    )


@bp.route("/viec/ai-goi-y", methods=["POST"])
@login_required
def ai_goi_y():
    """AJAX — gợi ý mô tả chi tiết bằng ChatGPT dựa theo tên công việc."""
    if not current_user.la_quan_ly:
        abort(403)
    tieu_de = (request.form.get("tieu_de") or "").strip()
    if not tieu_de:
        return jsonify({"ok": False, "loi": "Chưa nhập tên công việc."})
    mo_ta, loi = dich_vu_ai.ai_goi_y_mo_ta(tieu_de)
    if loi:
        return jsonify({"ok": False, "loi": loi})
    return jsonify({"ok": True, "mo_ta": mo_ta})


@bp.route("/viec/ai-tom-tat", methods=["POST"])
@login_required
def ai_tom_tat():
    """AJAX — tóm tắt mô tả lan man thành gạch đầu dòng bằng ChatGPT."""
    if not current_user.la_quan_ly:
        abort(403)
    noi_dung = (request.form.get("mo_ta") or "").strip()
    if not noi_dung:
        return jsonify({"ok": False, "loi": "Chưa có nội dung để tóm tắt."})
    mo_ta, loi = dich_vu_ai.ai_tom_tat_mo_ta(noi_dung)
    if loi:
        return jsonify({"ok": False, "loi": loi})
    return jsonify({"ok": True, "mo_ta": mo_ta})


def _ds_ngay_nghi_json(so_ngay_toi: int = 730) -> list[str]:
    """Danh sách ISO date của ngày nghỉ (Chủ nhật nếu bật cài đặt + ngày lễ
    đã khai báo) từ hôm nay tới so_ngay_toi ngày tới — để JS tự vô hiệu hoá
    đúng các ô ngày đó trên lịch chọn "Hằng ngày", không cần đợi submit
    mới báo lỗi."""
    hom_nay = ngay_vn_hien_tai()
    ket_qua = set()
    if services.lay_cai_dat("NGHI_CHU_NHAT", "1") == "1":
        for i in range(so_ngay_toi):
            d = hom_nay + timedelta(days=i)
            if d.weekday() == 6:
                ket_qua.add(d.isoformat())
    for nnl in NgayNghiLe.query.filter(
        NgayNghiLe.ngay >= hom_nay, NgayNghiLe.ngay <= hom_nay + timedelta(days=so_ngay_toi)
    ).all():
        ket_qua.add(nnl.ngay.isoformat())
    return sorted(ket_qua)


@bp.route("/viec/moi", methods=["GET", "POST"])
@login_required
def giao_viec():
    if not current_user.la_quan_ly:
        abort(403)
    nhan_vien = _nhan_vien_duoc_giao()
    thang_hien_tai = ngay_vn_hien_tai().strftime("%Y-%m")
    ds_ngay_nghi = _ds_ngay_nghi_json()

    if request.method == "POST":
        nguoi_nhan_ids = request.form.getlist("nguoi_nhan_id")
        tieu_de = (request.form.get("tieu_de") or "").strip()
        mo_ta = (request.form.get("mo_ta") or "").strip()
        uu_tien = request.form.get("do_uu_tien") or DoUuTien.THUONG
        han_raw = request.form.get("han") or ""
        ngay_bat_dau_raw = (request.form.get("ngay_bat_dau") or "").strip()
        cac_ngay_raw = request.form.getlist("ngay_hang_ngay")
        gio_hang_ngay = request.form.get("gio_hang_ngay") or "17:25"
        thang_hang_ngay = request.form.get("thang_hang_ngay") or thang_hien_tai

        def loi(thong_bao):
            flash(thong_bao, "error")
            return render_template(
                "task_form.html", nhan_vien=nhan_vien, tieu_de=tieu_de, mo_ta=mo_ta,
                han=han_raw, uu_tien=uu_tien, ngay_da_chon=cac_ngay_raw,
                ngay_bat_dau=ngay_bat_dau_raw,
                gio_hang_ngay=gio_hang_ngay, thang_hang_ngay=thang_hang_ngay,
                ds_ngay_nghi=ds_ngay_nghi,
            )

        if not tieu_de or not nguoi_nhan_ids:
            return loi("Cần nhập tên công việc và chọn ít nhất 1 người nhận.")

        # Hằng ngày: nhiều ngày rời rạc, dùng chung 1 giờ hạn mỗi ngày, không
        # áp dụng "Ngày bắt đầu" (mỗi ngày đã là 1 việc riêng biệt rồi).
        # Còn lại: 1 hạn duy nhất (datetime-local) như cũ, có thể kèm ngày
        # bắt đầu để việc hiện xuyên suốt trong "Việc hằng ngày" từ ngày đó
        # tới ngày hạn, không chỉ đúng ngày hạn.
        ngay_bat_dau = None
        if uu_tien == DoUuTien.THAP:
            if not cac_ngay_raw:
                return loi("Chọn ít nhất 1 ngày cho công việc hằng ngày.")
            try:
                gio_h, gio_m = (int(x) for x in gio_hang_ngay.split(":"))
            except ValueError:
                return loi("Giờ áp dụng không hợp lệ.")
            danh_sach_han = []
            for ng in cac_ngay_raw:
                try:
                    d = date.fromisoformat(ng)
                except ValueError:
                    return loi("Có ngày không hợp lệ trong danh sách đã chọn.")
                danh_sach_han.append(datetime(d.year, d.month, d.day, gio_h, gio_m))
            if any(h <= gio_vn_hien_tai() for h in danh_sach_han):
                return loi("Có ngày/giờ áp dụng đã ở trong quá khứ so với thời điểm hiện tại "
                           f"({gio_vn_hien_tai():%H:%M %d/%m}) — chọn lại ngày hoặc giờ cho phù hợp.")
            ngay_nghi_trung = [h.date() for h in danh_sach_han if services.la_ngay_nghi(h.date())]
            if ngay_nghi_trung:
                cac_ngay_hien = ", ".join(f"{d:%d/%m}" for d in sorted(set(ngay_nghi_trung)))
                return loi(f"Không giao được việc vào ngày nghỉ (Chủ nhật/lễ): {cac_ngay_hien} "
                           f"— bỏ các ngày này ra khỏi lựa chọn.")
        else:
            han = None
            if han_raw:
                try:
                    han = datetime.strptime(han_raw, "%Y-%m-%dT%H:%M")
                except ValueError:
                    return loi("Hạn hoàn thành không đúng định dạng.")
                if han <= gio_vn_hien_tai():
                    return loi("Hạn hoàn thành phải ở trong tương lai so với thời điểm hiện tại "
                               f"({gio_vn_hien_tai():%H:%M %d/%m}) — không thể giao việc với hạn đã qua.")
                if services.la_ngay_nghi(han.date()):
                    return loi(f"Ngày {han:%d/%m/%Y} là ngày nghỉ (Chủ nhật/lễ) — "
                               f"chọn hạn vào ngày làm việc khác.")
            if ngay_bat_dau_raw:
                try:
                    ngay_bat_dau = date.fromisoformat(ngay_bat_dau_raw)
                except ValueError:
                    return loi("Ngày bắt đầu không đúng định dạng.")
                if han and ngay_bat_dau > han.date():
                    return loi("Ngày bắt đầu phải trước hoặc bằng ngày của hạn hoàn thành.")
            danh_sach_han = [han]

        # Ảnh minh hoạ đính kèm "Yêu cầu chi tiết" (không bắt buộc) — chỉ áp
        # dụng cho việc CHÍNH ở khối trên cùng, đọc bytes 1 lần ở đây vì
        # FileStorage chỉ đọc/lưu được 1 lần, trong khi việc chính có thể bị
        # nhân bản thành nhiều CongViec (nhiều người nhận, hoặc nhiều ngày
        # nếu là Hằng ngày) — mỗi việc cần 1 bản lưu file riêng.
        anh_yeu_cau_bytes: list[tuple[str, bytes]] = []
        for f in request.files.getlist("anh_yeu_cau"):
            if not f or not f.filename:
                continue
            if services.phan_loai(f.filename, f.mimetype) != LoaiDinhKem.ANH:
                flash(f"Bỏ qua tệp không phải ảnh: {f.filename}", "error")
                continue
            anh_yeu_cau_bytes.append((f.filename, f.read()))

        tao = []
        for nid in nguoi_nhan_ids:
            nv = db.session.get(NguoiDung, int(nid))
            if not nv or not nv.dang_hoat_dong or nv.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
                continue
            for h in danh_sach_han:
                v = CongViec(
                    tieu_de=tieu_de, mo_ta=mo_ta, han=h, ngay_bat_dau=ngay_bat_dau,
                    do_uu_tien=uu_tien,
                    nguoi_giao_id=current_user.id, nguoi_nhan_id=nv.id,
                    trang_thai=TrangThai.MOI,
                )
                db.session.add(v)
                tao.append(v)

        # Việc THÊM (khác tên/nội dung với việc chính ở trên) trong cùng 1
        # lần giao — mỗi khối 1 hạn đơn, có thể kèm ngày bắt đầu riêng,
        # không hỗ trợ lịch nhiều ngày kiểu Hằng ngày (giữ đơn giản). Khối
        # nào để trống tên/chưa chọn người nhận thì bỏ qua, không chặn cả
        # form. Dò theo tên field tieu_de_N thay vì đếm số khối, nên xoá
        # khối giữa chừng ở form vẫn an toàn.
        chi_so_them = sorted({
            int(m.group(1)) for k in request.form
            if (m := re.fullmatch(r"tieu_de_(\d+)", k))
        })
        tao_them_theo_khoi = []
        for idx in chi_so_them:
            tieu_de_i = (request.form.get(f"tieu_de_{idx}") or "").strip()
            nguoi_nhan_ids_i = request.form.getlist(f"nguoi_nhan_id_{idx}")
            if not tieu_de_i or not nguoi_nhan_ids_i:
                continue
            mo_ta_i = (request.form.get(f"mo_ta_{idx}") or "").strip()
            uu_tien_i = request.form.get(f"do_uu_tien_{idx}") or DoUuTien.THUONG
            if uu_tien_i not in (DoUuTien.THUONG, DoUuTien.CAO):
                uu_tien_i = DoUuTien.THUONG
            han_i = None
            han_i_raw = request.form.get(f"han_{idx}") or ""
            if han_i_raw:
                try:
                    han_i = datetime.strptime(han_i_raw, "%Y-%m-%dT%H:%M")
                except ValueError:
                    han_i = None
                if han_i and han_i <= gio_vn_hien_tai():
                    han_i = None  # hạn đã ở quá khứ — bỏ qua thay vì khoá cả form,
                                  # coi như việc thêm này không đặt hạn (giống cách
                                  # ngày bắt đầu vô lý cũng chỉ bị bỏ qua bên dưới)
            ngay_bat_dau_i = None
            ngay_bat_dau_i_raw = (request.form.get(f"ngay_bat_dau_{idx}") or "").strip()
            if ngay_bat_dau_i_raw:
                try:
                    ngay_bat_dau_i = date.fromisoformat(ngay_bat_dau_i_raw)
                except ValueError:
                    ngay_bat_dau_i = None
                if ngay_bat_dau_i and han_i and ngay_bat_dau_i > han_i.date():
                    ngay_bat_dau_i = None  # bỏ qua giá trị vô lý, không chặn cả form

            tao_i = []
            for nid in nguoi_nhan_ids_i:
                nv = db.session.get(NguoiDung, int(nid))
                if not nv or not nv.dang_hoat_dong or nv.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
                    continue
                v = CongViec(
                    tieu_de=tieu_de_i, mo_ta=mo_ta_i, han=han_i,
                    ngay_bat_dau=ngay_bat_dau_i, do_uu_tien=uu_tien_i,
                    nguoi_giao_id=current_user.id, nguoi_nhan_id=nv.id,
                    trang_thai=TrangThai.MOI,
                )
                db.session.add(v)
                tao_i.append(v)
            if tao_i:
                tao_them_theo_khoi.append(tao_i)

        tat_ca = tao + [v for nhom in tao_them_theo_khoi for v in nhom]
        if not tat_ca:
            return loi("Không tạo được công việc nào — kiểm tra lại người nhận đã chọn.")

        db.session.flush()          # lấy id
        for v in tat_ca:
            services.gan_ma(v)

        if anh_yeu_cau_bytes:
            for v in tao:  # chỉ việc CHÍNH, không áp dụng cho các khối "Việc thêm"
                for ten_goc, du_lieu in anh_yeu_cau_bytes:
                    duong_dan, _ = services.luu_bytes(du_lieu, ten_goc, "yeu-cau")
                    v.anh_yeu_cau.append(AnhYeuCau(duong_dan=duong_dan, ten_goc=ten_goc[:255]))

        db.session.commit()

        # Hằng ngày (của khối chính): gộp thành 1 tin Zalo mỗi nhân viên.
        # Nhiệm vụ chính / Cần gấp (khối chính và mọi khối thêm): báo riêng
        # từng việc như cũ.
        theo_nhan_vien: dict[int, int] = {}
        if uu_tien == DoUuTien.THAP:
            ngay_dau = min(danh_sach_han).date()
            ngay_cuoi = max(danh_sach_han).date()
            gio_han = danh_sach_han[0].strftime("%H:%M")
            for v in tao:
                theo_nhan_vien[v.nguoi_nhan_id] = theo_nhan_vien.get(v.nguoi_nhan_id, 0) + 1
            for nid, so_luong in theo_nhan_vien.items():
                nv = db.session.get(NguoiDung, nid)
                services.bao_giao_viec_hang_ngay(nv, tieu_de, ngay_dau, ngay_cuoi, gio_han, so_luong)
        else:
            for v in tao:
                services.bao_giao_viec(v)

        for nhom in tao_them_theo_khoi:
            for v in nhom:
                services.bao_giao_viec(v)

        db.session.commit()         # lưu log zalo

        if tao_them_theo_khoi:
            flash(f"Đã giao tổng cộng {len(tat_ca)} việc thuộc "
                  f"{1 + len(tao_them_theo_khoi)} loại công việc khác nhau, "
                  f"đã gửi thông báo Zalo.", "success")
        elif uu_tien == DoUuTien.THAP:
            flash(f"Đã giao {len(tao)} việc hằng ngày, gửi {len(theo_nhan_vien)} tin Zalo gộp.", "success")
        else:
            flash(f"Đã giao {len(tao)} việc và gửi thông báo Zalo.", "success")

        return redirect(url_for("tasks.chi_tiet", viec_id=tat_ca[0].id) if len(tat_ca) == 1
                        else url_for("tasks.danh_sach"))

    return render_template("task_form.html", nhan_vien=nhan_vien, thang_hang_ngay=thang_hien_tai,
                           ds_ngay_nghi=ds_ngay_nghi)


@bp.route("/viec/<int:viec_id>")
@login_required
def chi_tiet(viec_id):
    viec = db.session.get(CongViec, viec_id) or abort(404)
    if not current_user.duoc_xem_viec(viec):
        abort(403)

    # Nhân viên mở lần đầu -> chuyển sang Đang làm
    if (viec.nguoi_nhan_id == current_user.id
            and viec.trang_thai == TrangThai.MOI):
        viec.trang_thai = TrangThai.DANG_LAM
        viec.mo_lan_dau_luc = gio_vn_hien_tai()
        db.session.commit()

    return render_template(
        "task_detail.html",
        viec=viec,
        duoc_duyet=current_user.duoc_duyet_viec(viec),
        la_nguoi_nhan=viec.nguoi_nhan_id == current_user.id,
    )


@bp.route("/viec/<int:viec_id>/doi-chung", methods=["POST"])
@login_required
def gui_doi_chung(viec_id):
    viec = db.session.get(CongViec, viec_id) or abort(404)
    if viec.nguoi_nhan_id != current_user.id:
        abort(403)
    if viec.trang_thai not in TrangThai.CHUA_XONG:
        flash("Công việc này không ở trạng thái cho phép gửi đối chứng.", "error")
        return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))

    ghi_chu = (request.form.get("ghi_chu") or "").strip()
    files = [f for f in request.files.getlist("tep") if f and f.filename]

    if not files and not ghi_chu:
        flash("Gửi ít nhất 1 tệp đối chứng hoặc ghi chú kết quả.", "error")
        return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))

    so_luu = 0
    for f in files:
        loai = services.phan_loai(f.filename, f.mimetype)
        if not loai:
            flash(f"Bỏ qua tệp không hỗ trợ: {f.filename}", "error")
            continue
        duong_dan, kich_thuoc = services.luu_file(f)
        db.session.add(DinhKem(
            cong_viec_id=viec.id, nguoi_tai_len_id=current_user.id,
            lan_gui=viec.lan_gui, loai=loai, duong_dan=duong_dan,
            ten_goc=f.filename[:255], kich_thuoc=kich_thuoc, mime=f.mimetype,
        ))
        so_luu += 1

    viec.trang_thai = TrangThai.CHO_DUYET
    viec.gui_doi_chung_luc = gio_vn_hien_tai()
    if ghi_chu:
        db.session.add(GhiChuNop(
            cong_viec_id=viec.id, lan_gui=viec.lan_gui, noi_dung=ghi_chu,
        ))
    db.session.commit()

    services.bao_gui_doi_chung(viec, so_luu)
    db.session.commit()

    flash("Đã gửi đối chứng. Sếp sẽ nhận được thông báo Zalo.", "success")
    return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))


@bp.route("/viec/<int:viec_id>/danh-gia", methods=["POST"])
@login_required
def danh_gia(viec_id):
    viec = db.session.get(CongViec, viec_id) or abort(404)
    if not current_user.duoc_duyet_viec(viec):
        abort(403)
    if viec.trang_thai != TrangThai.CHO_DUYET:
        flash("Công việc này chưa gửi đối chứng.", "error")
        return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))

    ket_qua = request.form.get("ket_qua")
    ghi_chu = (request.form.get("ghi_chu") or "").strip()
    anh_files = [f for f in request.files.getlist("anh_danh_gia") if f and f.filename]

    def _luu_anh_danh_gia(dg):
        """Lưu các ảnh sếp đính kèm khi đánh giá (nếu có), gắn vào đúng
        đánh giá dg vừa tạo. Bỏ qua và báo lỗi cho tệp không phải ảnh."""
        for f in anh_files:
            if services.phan_loai(f.filename, f.mimetype) != LoaiDinhKem.ANH:
                flash(f"Bỏ qua tệp không phải ảnh: {f.filename}", "error")
                continue
            duong_dan, _ = services.luu_file(f, "danh-gia")
            db.session.add(AnhDanhGia(
                danh_gia_id=dg.id, duong_dan=duong_dan, ten_goc=f.filename[:255],
            ))

    if ket_qua == "dat":
        so_sao = request.form.get("so_sao", type=int)
        if not so_sao or not 1 <= so_sao <= 5:
            flash("Chọn số sao từ 1 đến 5.", "error")
            return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))
        dg = DanhGia(cong_viec_id=viec.id, nguoi_danh_gia_id=current_user.id,
                     lan_gui=viec.lan_gui, ket_qua="dat", so_sao=so_sao,
                     ghi_chu=ghi_chu)
        db.session.add(dg)
        db.session.flush()  # lấy dg.id để gắn ảnh đánh giá
        _luu_anh_danh_gia(dg)
        viec.trang_thai = TrangThai.HOAN_THANH
        viec.hoan_thanh_luc = gio_vn_hien_tai()
        viec.so_sao_cuoi = so_sao
        db.session.commit()
        services.bao_da_duyet(viec, dg)
        flash("Đã duyệt và gửi kết quả về Zalo cho nhân viên.", "success")

    elif ket_qua == "lam_lai":
        if not ghi_chu:
            flash("Nhập lý do để nhân viên biết phải sửa gì.", "error")
            return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))
        dg = DanhGia(cong_viec_id=viec.id, nguoi_danh_gia_id=current_user.id,
                     lan_gui=viec.lan_gui, ket_qua="lam_lai", ghi_chu=ghi_chu)
        db.session.add(dg)
        db.session.flush()  # lấy dg.id để gắn ảnh đánh giá
        _luu_anh_danh_gia(dg)
        viec.trang_thai = TrangThai.LAM_LAI
        viec.lan_gui += 1
        viec.gui_doi_chung_luc = None
        db.session.commit()
        services.bao_lam_lai(viec, dg)
        flash("Đã gửi yêu cầu làm lại về Zalo cho nhân viên.", "success")
    else:
        flash("Chọn Đạt hoặc Yêu cầu làm lại.", "error")

    db.session.commit()
    return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))


@bp.route("/viec/<int:viec_id>/danh-gia-lai", methods=["POST"])
@login_required
def danh_gia_lai(viec_id):
    """Admin/Sếp/Quản lý bộ phận sửa lại số sao của 1 việc ĐÃ Hoàn thành —
    dùng cho trường hợp lúc chấm ban đầu chưa phát hiện vấn đề (VD phát
    hiện lúc bàn giao thực tế), không giới hạn thời gian. KPI tự cập nhật
    theo vì tính trực tiếp từ so_sao_cuoi, không lưu số liệu KPI riêng."""
    viec = db.session.get(CongViec, viec_id) or abort(404)
    if not current_user.duoc_duyet_viec(viec):
        abort(403)
    if viec.trang_thai != TrangThai.HOAN_THANH:
        flash("Chỉ đánh giá lại được việc đã Hoàn thành.", "error")
        return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))

    so_sao_moi = request.form.get("so_sao_moi", type=int)
    if so_sao_moi is None or not (0 <= so_sao_moi <= 5):
        flash("Chọn số sao hợp lệ (0-5).", "error")
        return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))

    ly_do = (request.form.get("ly_do") or "").strip()
    if not ly_do:
        flash("Cần nêu rõ lý do đánh giá lại, để nhân viên hiểu vì sao sao bị đổi.", "error")
        return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))

    sao_cu = viec.so_sao_cuoi
    if so_sao_moi == sao_cu:
        flash("Số sao mới trùng với số sao hiện tại, không có gì để đổi.", "info")
        return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))

    viec.so_sao_cuoi = so_sao_moi
    dgl = DanhGia(
        cong_viec_id=viec.id, nguoi_danh_gia_id=current_user.id,
        lan_gui=viec.lan_gui, ket_qua="danh_gia_lai", so_sao=so_sao_moi,
        ghi_chu=ly_do,
    )
    db.session.add(dgl)
    db.session.flush()  # lấy dgl.id để gắn ảnh

    for f in request.files.getlist("anh_danh_gia_lai"):
        if not f or not f.filename:
            continue
        if services.phan_loai(f.filename, f.mimetype) != LoaiDinhKem.ANH:
            flash(f"Bỏ qua tệp không phải ảnh: {f.filename}", "error")
            continue
        duong_dan, _ = services.luu_file(f, "danh-gia")
        db.session.add(AnhDanhGia(danh_gia_id=dgl.id, duong_dan=duong_dan, ten_goc=f.filename[:255]))

    db.session.commit()

    services.bao_danh_gia_lai(viec, current_user, sao_cu, so_sao_moi, ly_do)
    db.session.commit()

    sao_cu_hien = f"{sao_cu}★" if sao_cu is not None else "chưa có sao"
    flash(f"Đã cập nhật đánh giá: {sao_cu_hien} → {so_sao_moi}★. KPI sẽ tự tính lại theo số sao mới.", "success")
    return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))


@bp.route("/viec/<int:viec_id>/huy", methods=["POST"])
@login_required
def huy_viec(viec_id):
    viec = db.session.get(CongViec, viec_id) or abort(404)
    if not current_user.duoc_duyet_viec(viec):
        abort(403)
    viec.trang_thai = TrangThai.HUY
    db.session.commit()
    services.gui_cho_nhan_vien(
        viec.nguoi_nhan,
        f"🚫 Công việc [{viec.ma}] {viec.tieu_de} đã được huỷ bởi "
        f"{current_user.ho_ten}. Bạn không cần làm nữa.",
        viec,
    )
    db.session.commit()
    flash("Đã huỷ công việc.", "info")
    return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))


@bp.route("/viec/<int:viec_id>/mo-lai", methods=["POST"])
@login_required
def mo_lai_viec(viec_id):
    """Admin/Ban giám đốc, hoặc Quản lý bộ phận của nhân viên nhận việc —
    mở lại việc đã bị hệ thống tự động đóng (nhận biết qua so_sao_cuoi == 0,
    vì chấm tay không bao giờ cho ra 0 sao). Mở lại đưa việc về Làm lại, tự
    động được cộng 3 giờ gia hạn theo đúng quy tắc tính KPI đã có."""
    viec = db.session.get(CongViec, viec_id) or abort(404)
    if not current_user.duoc_duyet_viec(viec):
        abort(403)
    if not (viec.trang_thai == TrangThai.HOAN_THANH and viec.so_sao_cuoi == 0):
        flash("Chỉ mở lại được việc đã bị hệ thống tự động đóng (0 sao).", "error")
        return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))

    dg = DanhGia(
        cong_viec_id=viec.id, nguoi_danh_gia_id=current_user.id,
        lan_gui=viec.lan_gui, ket_qua="lam_lai",
        ghi_chu=f"{current_user.ho_ten} đã mở lại việc bị hệ thống tự động đóng.",
    )
    db.session.add(dg)
    viec.trang_thai = TrangThai.LAM_LAI
    viec.lan_gui += 1
    viec.so_sao_cuoi = None
    viec.hoan_thanh_luc = None
    viec.gui_doi_chung_luc = None
    db.session.commit()
    services.bao_lam_lai(viec, dg)
    db.session.commit()
    flash("Đã mở lại việc, nhân viên có thể nộp đối chứng lại.", "success")
    return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))


@bp.route("/viec/<int:viec_id>/xoa", methods=["POST"])
@login_required
def xoa_viec(viec_id):
    """Chỉ Admin/Ban giám đốc — xoá vĩnh viễn 1 công việc, kể cả tệp đính
    kèm trên ổ đĩa. Không thể khôi phục, khác với Huỷ (chỉ đổi trạng thái).
    Vẫn báo Zalo cho nhân viên như khi Huỷ — với họ, việc biến mất hay bị
    đổi trạng thái Huỷ thì thực tế cũng là không cần làm nữa, cần được báo
    giống nhau."""
    if not current_user.la_admin_sep:
        abort(403)
    viec = db.session.get(CongViec, viec_id) or abort(404)
    ma, ten = viec.ma, viec.tieu_de
    services.gui_cho_nhan_vien(
        viec.nguoi_nhan,
        f"🚫 Công việc [{ma}] {ten} đã được huỷ bởi {current_user.ho_ten}. "
        f"Bạn không cần làm nữa.",
        viec,
    )
    services.xoa_file_dinh_kem(viec)
    services.go_lien_ket_log_zalo_cho_viec(viec)
    db.session.delete(viec)
    db.session.commit()
    flash(f"Đã xoá vĩnh viễn công việc {ma} — {ten}.", "success")
    return redirect(url_for("tasks.danh_sach"))


@bp.route("/kpi")
@login_required
def kpi():
    hom_nay = ngay_vn_hien_tai()
    dau_tuan = hom_nay - timedelta(days=hom_nay.weekday())  # Thứ 2 tuần này

    try:
        tu_ngay = date.fromisoformat(request.args["tu_ngay"]) if request.args.get("tu_ngay") else dau_tuan
    except ValueError:
        tu_ngay = dau_tuan
    try:
        den_ngay = date.fromisoformat(request.args["den_ngay"]) if request.args.get("den_ngay") else hom_nay
    except ValueError:
        den_ngay = hom_nay

    if current_user.la_quan_ly:
        bang = services.tinh_kpi(tu_ngay, den_ngay)
        if current_user.vai_tro == VaiTro.QUAN_LY and current_user.bo_phan_id:
            ids = {n.id for n in _nhan_vien_duoc_giao()}
            bang = [b for b in bang if b["nguoi_dung_id"] in ids]
    else:
        bang = services.tinh_kpi(tu_ngay, den_ngay, current_user.id)
    return render_template("kpi.html", bang=bang, tu_ngay=tu_ngay, den_ngay=den_ngay)


NGUON_THU_VIEN = {
    "doi_chung": "Đối chứng",
    "danh_gia": "Ảnh đánh giá",
    "yeu_cau": "Ảnh yêu cầu",
    "xin_nghi": "Đơn xin nghỉ",
    "chuc_vu": "Ảnh chức vụ",
    "san_pham": "Ảnh sản phẩm",
    "mo_coi": "Không rõ nguồn gốc",
}


class _PhanTrangDon:
    """Giả lập tối thiểu Flask-SQLAlchemy Pagination cho danh sách Python
    thường (không phải 1 query đơn) — để dùng lại nguyên khối HTML phân
    trang đã có trong template, không cần viết thêm 1 kiểu phân trang khác."""

    def __init__(self, items, page, per_page, total):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = total
        self.pages = max(1, math.ceil(total / per_page))
        self.has_prev = page > 1
        self.has_next = page < self.pages
        self.prev_num = page - 1
        self.next_num = page + 1


def _khop_tim(tim: str, *chuoi) -> bool:
    if not tim:
        return True
    tim_thuong = tim.lower()
    return any(tim_thuong in (c or "").lower() for c in chuoi)


def _trong_khoang_ngay(tao_luc, tu_ngay, den_ngay) -> bool:
    if tu_ngay and tao_luc.date() < tu_ngay:
        return False
    if den_ngay and tao_luc.date() > den_ngay:
        return False
    return True


def _muc_doi_chung(nguoi, tim, tu_ngay, den_ngay):
    """Đối chứng — phạm vi theo đúng quyền xem việc (như trước giờ)."""
    viec_ids = _viec_lien_quan().with_entities(CongViec.id)
    q = DinhKem.query.join(CongViec, DinhKem.cong_viec_id == CongViec.id).filter(
        DinhKem.cong_viec_id.in_(viec_ids)
    )
    if nguoi:
        q = q.filter(DinhKem.nguoi_tai_len_id == nguoi)
    ds = []
    for dk in q.all():
        if not _trong_khoang_ngay(dk.tao_luc, tu_ngay, den_ngay):
            continue
        if not _khop_tim(tim, dk.cong_viec.ma, dk.cong_viec.tieu_de, dk.ten_goc):
            continue
        ds.append({
            "loai_nguon": "doi_chung", "ten_loai_nguon": NGUON_THU_VIEN["doi_chung"],
            "duong_dan": dk.duong_dan, "loai": dk.loai,
            "ten_file": dk.ten_goc or dk.duong_dan,
            "dong_1": f"[{dk.cong_viec.ma}] {dk.cong_viec.tieu_de}",
            "dong_1_link": url_for("tasks.chi_tiet", viec_id=dk.cong_viec_id),
            "nguoi_ten": dk.nguoi_tai_len.ho_ten if dk.nguoi_tai_len else None,
            "phu": f"lần {dk.lan_gui}" if dk.lan_gui > 1 else None,
            "tao_luc": dk.tao_luc,
            "co_the_xoa": current_user.la_admin_sep,
            "xoa_action": url_for("tasks.xoa_dinh_kem", id=dk.id),
            "xoa_xac_nhan": (f"Xoá vĩnh viễn đối chứng này? Không ảnh hưởng đánh giá đã "
                            f"chấm cho việc [{dk.cong_viec.ma}], chỉ mất file."),
        })
    return ds


def _muc_anh_danh_gia(nguoi, tim, tu_ngay, den_ngay):
    q = AnhDanhGia.query.join(DanhGia, AnhDanhGia.danh_gia_id == DanhGia.id).join(
        CongViec, DanhGia.cong_viec_id == CongViec.id)
    if nguoi:
        q = q.filter(DanhGia.nguoi_danh_gia_id == nguoi)
    ds = []
    for adg in q.all():
        if not _trong_khoang_ngay(adg.tao_luc, tu_ngay, den_ngay):
            continue
        viec = adg.danh_gia.cong_viec
        if not _khop_tim(tim, viec.ma, viec.tieu_de, adg.ten_goc):
            continue
        ds.append({
            "loai_nguon": "danh_gia", "ten_loai_nguon": NGUON_THU_VIEN["danh_gia"],
            "duong_dan": adg.duong_dan, "loai": LoaiDinhKem.ANH,
            "ten_file": adg.ten_goc or adg.duong_dan,
            "dong_1": f"[{viec.ma}] {viec.tieu_de}",
            "dong_1_link": url_for("tasks.chi_tiet", viec_id=viec.id),
            "nguoi_ten": adg.danh_gia.nguoi_danh_gia.ho_ten if adg.danh_gia.nguoi_danh_gia else None,
            "phu": None, "tao_luc": adg.tao_luc,
            "co_the_xoa": current_user.la_admin_sep,
            "xoa_action": url_for("tasks.xoa_anh_danh_gia", id=adg.id),
            "xoa_xac_nhan": f"Xoá vĩnh viễn ảnh đánh giá này của việc [{viec.ma}]?",
        })
    return ds


def _muc_anh_yeu_cau(nguoi, tim, tu_ngay, den_ngay):
    q = AnhYeuCau.query.join(CongViec, AnhYeuCau.cong_viec_id == CongViec.id)
    if nguoi:
        q = q.filter(CongViec.nguoi_giao_id == nguoi)
    ds = []
    for ayc in q.all():
        if not _trong_khoang_ngay(ayc.tao_luc, tu_ngay, den_ngay):
            continue
        viec = ayc.cong_viec
        if not _khop_tim(tim, viec.ma, viec.tieu_de, ayc.ten_goc):
            continue
        ds.append({
            "loai_nguon": "yeu_cau", "ten_loai_nguon": NGUON_THU_VIEN["yeu_cau"],
            "duong_dan": ayc.duong_dan, "loai": LoaiDinhKem.ANH,
            "ten_file": ayc.ten_goc or ayc.duong_dan,
            "dong_1": f"[{viec.ma}] {viec.tieu_de}",
            "dong_1_link": url_for("tasks.chi_tiet", viec_id=viec.id),
            "nguoi_ten": viec.nguoi_giao.ho_ten if viec.nguoi_giao else None,
            "phu": None, "tao_luc": ayc.tao_luc,
            "co_the_xoa": current_user.la_admin_sep,
            "xoa_action": url_for("tasks.xoa_anh_yeu_cau", id=ayc.id),
            "xoa_xac_nhan": f"Xoá vĩnh viễn ảnh yêu cầu này của việc [{viec.ma}]?",
        })
    return ds


def _muc_xin_nghi(nguoi, tim, tu_ngay, den_ngay):
    """1 đơn nghỉ nhiều ngày tạo nhiều dòng XinNghi nhưng dùng CHUNG 1 file
    — gộp lại thành 1 mục duy nhất theo đường dẫn, tránh hiện trùng lặp."""
    q = XinNghi.query
    if nguoi:
        q = q.filter(XinNghi.nguoi_dung_id == nguoi)
    theo_file: dict[str, list] = {}
    for xn in q.order_by(XinNghi.ngay).all():
        theo_file.setdefault(xn.anh_minh_chung, []).append(xn)

    ds = []
    for duong_dan, nhom in theo_file.items():
        dau, cuoi = nhom[0], nhom[-1]
        tao_luc = dau.tao_luc or gio_vn_hien_tai()
        if not _trong_khoang_ngay(tao_luc, tu_ngay, den_ngay):
            continue
        ten_nv = dau.nguoi_dung.ho_ten if dau.nguoi_dung else "—"
        khoang_ngay = (f"ngày {dau.ngay:%d/%m/%Y}" if dau.ngay == cuoi.ngay
                      else f"{dau.ngay:%d/%m}–{cuoi.ngay:%d/%m/%Y}")
        if not _khop_tim(tim, ten_nv, khoang_ngay):
            continue
        ds.append({
            "loai_nguon": "xin_nghi", "ten_loai_nguon": NGUON_THU_VIEN["xin_nghi"],
            "duong_dan": duong_dan, "loai": LoaiDinhKem.FILE,
            "ten_file": f"Đơn nghỉ {khoang_ngay}",
            "dong_1": f"Đơn nghỉ phép — {khoang_ngay}",
            "dong_1_link": None,
            "nguoi_ten": ten_nv, "phu": None, "tao_luc": tao_luc,
            "co_the_xoa": False,  # xoá tại trang Bảng công (có xử lý dùng-chung-file riêng)
            "xoa_action": None, "xoa_xac_nhan": None,
        })
    return ds


def _muc_chuc_vu(nguoi, tim, tu_ngay, den_ngay):
    ds = []
    for cv in ChucVu.query.filter(ChucVu.anh.isnot(None)).all():
        if not _trong_khoang_ngay(cv.tao_luc or gio_vn_hien_tai(), tu_ngay, den_ngay):
            continue
        if not _khop_tim(tim, cv.ten):
            continue
        ds.append({
            "loai_nguon": "chuc_vu", "ten_loai_nguon": NGUON_THU_VIEN["chuc_vu"],
            "duong_dan": cv.anh, "loai": LoaiDinhKem.ANH,
            "ten_file": cv.ten,
            "dong_1": f"Chức vụ: {cv.ten}", "dong_1_link": url_for("admin.info_ai"),
            "nguoi_ten": None, "phu": None, "tao_luc": cv.tao_luc or gio_vn_hien_tai(),
            "co_the_xoa": False,  # xoá tại trang Quản trị > Chức vụ
            "xoa_action": None, "xoa_xac_nhan": None,
        })
    return ds


def _muc_san_pham(nguoi, tim, tu_ngay, den_ngay):
    from models import SanPhamAI
    ds = []
    q = AnhSanPhamAI.query.join(SanPhamAI, AnhSanPhamAI.san_pham_id == SanPhamAI.id)
    for asp in q.all():
        tao_luc = asp.san_pham.tao_luc or gio_vn_hien_tai()
        if not _trong_khoang_ngay(tao_luc, tu_ngay, den_ngay):
            continue
        if not _khop_tim(tim, asp.san_pham.ten, asp.nhan):
            continue
        ds.append({
            "loai_nguon": "san_pham", "ten_loai_nguon": NGUON_THU_VIEN["san_pham"],
            "duong_dan": asp.duong_dan, "loai": LoaiDinhKem.ANH,
            "ten_file": asp.nhan or asp.san_pham.ten,
            "dong_1": f"Sản phẩm: {asp.san_pham.ten}" + (f" · {asp.nhan}" if asp.nhan else ""),
            "dong_1_link": url_for("admin.info_ai"),
            "nguoi_ten": None, "phu": None, "tao_luc": tao_luc,
            "co_the_xoa": False,  # xoá tại trang Quản trị > Info AI
            "xoa_action": None, "xoa_xac_nhan": None,
        })
    return ds


def _muc_mo_coi(tim, tu_ngay, den_ngay):
    """Quét toàn bộ thư mục upload, tìm file KHÔNG có bản ghi nào (ở bất kỳ
    bảng nào) trỏ tới — dấu vết của lỗi cũ hoặc xoá dữ liệu chưa dọn hết.
    Chỉ Admin/Sếp mới quét được vì phải duyệt qua toàn bộ ổ đĩa upload."""
    goc = current_app.config["UPLOAD_ROOT"]
    tren_dia = set()
    for thu_muc_goc, _, cac_file in os.walk(goc):
        for ten in cac_file:
            duong_dan_tuyet_doi = os.path.join(thu_muc_goc, ten)
            tren_dia.add(os.path.relpath(duong_dan_tuyet_doi, goc).replace(os.sep, "/"))

    da_biet = set()
    da_biet.update(r[0] for r in db.session.query(DinhKem.duong_dan).all())
    da_biet.update(r[0] for r in db.session.query(AnhDanhGia.duong_dan).all())
    da_biet.update(r[0] for r in db.session.query(AnhYeuCau.duong_dan).all())
    da_biet.update(r[0] for r in db.session.query(XinNghi.anh_minh_chung).all())
    da_biet.update(r[0] for r in db.session.query(ChucVu.anh).filter(ChucVu.anh.isnot(None)).all())
    da_biet.update(r[0] for r in db.session.query(AnhSanPhamAI.duong_dan).all())

    ds = []
    for duong_dan in sorted(tren_dia - da_biet):
        duong_dan_tuyet_doi = os.path.join(goc, *duong_dan.split("/"))
        try:
            thong_ke = os.stat(duong_dan_tuyet_doi)
            tao_luc = datetime.fromtimestamp(thong_ke.st_mtime)
        except OSError:
            tao_luc = gio_vn_hien_tai()
        if not _trong_khoang_ngay(tao_luc, tu_ngay, den_ngay):
            continue
        ten_file = os.path.basename(duong_dan)
        if not _khop_tim(tim, ten_file, duong_dan):
            continue
        ds.append({
            "loai_nguon": "mo_coi", "ten_loai_nguon": NGUON_THU_VIEN["mo_coi"],
            "duong_dan": duong_dan, "loai": services.phan_loai(ten_file) or LoaiDinhKem.FILE,
            "ten_file": ten_file,
            "dong_1": f"⚠️ {ten_file}", "dong_1_link": None,
            "nguoi_ten": None, "phu": "không rõ nguồn gốc — có thể xoá an toàn",
            "tao_luc": tao_luc,
            "co_the_xoa": True,
            "xoa_action": url_for("tasks.xoa_file_mo_coi"),
            "xoa_xac_nhan": f"Xoá vĩnh viễn file mồ côi \"{ten_file}\"? Không có gì tham chiếu tới file này.",
            "xoa_duong_dan": duong_dan,  # form ẩn cần gửi kèm để backend biết xoá file nào
        })
    return ds


@bp.route("/thu-vien")
@login_required
def thu_vien():
    """Thư viện file. Mặc định (nhân viên/quản lý): chỉ đối chứng, đúng
    phạm vi quyền xem việc như trước giờ. Admin/Sếp có thêm bộ lọc "Nguồn"
    để xem MỌI loại file từng lưu trong thư mục upload — ảnh đánh giá, ảnh
    yêu cầu, đơn xin nghỉ, ảnh chức vụ, ảnh sản phẩm, và cả file mồ côi."""
    trang = request.args.get("trang", 1, type=int)
    nguoi = request.args.get("nguoi", type=int)
    tim = (request.args.get("q") or "").strip()
    tu_ngay_raw = request.args.get("tu_ngay") or ""
    den_ngay_raw = request.args.get("den_ngay") or ""
    nguon = request.args.get("nguon") or "doi_chung"
    if not current_user.la_admin_sep:
        nguon = "doi_chung"  # nhân viên/quản lý luôn chỉ thấy đối chứng, bất kể query có gì

    tu_ngay = den_ngay = None
    if tu_ngay_raw:
        try:
            tu_ngay = date.fromisoformat(tu_ngay_raw)
        except ValueError:
            tu_ngay_raw = ""
    if den_ngay_raw:
        try:
            den_ngay = date.fromisoformat(den_ngay_raw)
        except ValueError:
            den_ngay_raw = ""

    ham_theo_nguon = {
        "doi_chung": lambda: _muc_doi_chung(nguoi, tim, tu_ngay, den_ngay),
        "danh_gia": lambda: _muc_anh_danh_gia(nguoi, tim, tu_ngay, den_ngay),
        "yeu_cau": lambda: _muc_anh_yeu_cau(nguoi, tim, tu_ngay, den_ngay),
        "xin_nghi": lambda: _muc_xin_nghi(nguoi, tim, tu_ngay, den_ngay),
        "chuc_vu": lambda: _muc_chuc_vu(nguoi, tim, tu_ngay, den_ngay),
        "san_pham": lambda: _muc_san_pham(nguoi, tim, tu_ngay, den_ngay),
        "mo_coi": lambda: _muc_mo_coi(tim, tu_ngay, den_ngay),
    }
    if nguon == "tat_ca":
        ds = [m for key in ham_theo_nguon if key != "mo_coi" for m in ham_theo_nguon[key]()]
    else:
        ds = ham_theo_nguon.get(nguon, ham_theo_nguon["doi_chung"])()

    ds.sort(key=lambda d: d["tao_luc"], reverse=True)

    MOI_TRANG = 24
    tong = len(ds)
    tong_trang = max(1, math.ceil(tong / MOI_TRANG))
    trang = min(max(1, trang), tong_trang)
    ds_trang = ds[(trang - 1) * MOI_TRANG: trang * MOI_TRANG]
    phan_trang = _PhanTrangDon(ds_trang, trang, MOI_TRANG, tong)

    return render_template(
        "thu_vien.html",
        phan_trang=phan_trang, ds=ds_trang,
        nhan_vien=_nhan_vien_duoc_giao() if current_user.la_quan_ly else [],
        f_nguoi=nguoi, f_q=tim, f_tu_ngay=tu_ngay_raw, f_den_ngay=den_ngay_raw,
        f_nguon=nguon, NGUON_THU_VIEN=NGUON_THU_VIEN,
    )


@bp.route("/thu-vien/xem-mo-coi/<path:duong_dan>")
@login_required
def xem_file_mo_coi(duong_dan):
    """Chỉ Admin/Sếp — xem trước 1 file mồ côi trước khi quyết định xoá.
    Route /media thường không nhận diện được file loại này (đúng nghĩa mồ
    côi là không bảng nào tham chiếu tới), nên cần route riêng — chỉ phục
    vụ đúng file thực sự nằm trong UPLOAD_ROOT, chặn path traversal."""
    if not current_user.la_admin_sep:
        abort(403)
    goc_that = os.path.realpath(current_app.config["UPLOAD_ROOT"])
    duong_dan_that = os.path.realpath(os.path.join(goc_that, *duong_dan.split("/")))
    if not (duong_dan_that == goc_that or duong_dan_that.startswith(goc_that + os.sep)):
        abort(400)
    if not os.path.isfile(duong_dan_that):
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_ROOT"], duong_dan)


@bp.route("/thu-vien/xoa-mo-coi", methods=["POST"])
@login_required
def xoa_file_mo_coi():
    """Chỉ Admin/Sếp — xoá 1 file mồ côi. Kiểm tra lại NGAY LÚC XOÁ là thực
    sự không còn bảng nào tham chiếu (không tin dữ liệu cũ lúc hiện trang),
    và chặn path traversal — chỉ xoá file thực sự nằm trong UPLOAD_ROOT."""
    if not current_user.la_admin_sep:
        abort(403)
    duong_dan = (request.form.get("duong_dan") or "").strip()
    if not duong_dan:
        abort(400)

    con_dung = (
        DinhKem.query.filter_by(duong_dan=duong_dan).first()
        or AnhDanhGia.query.filter_by(duong_dan=duong_dan).first()
        or AnhYeuCau.query.filter_by(duong_dan=duong_dan).first()
        or XinNghi.query.filter_by(anh_minh_chung=duong_dan).first()
        or ChucVu.query.filter_by(anh=duong_dan).first()
        or AnhSanPhamAI.query.filter_by(duong_dan=duong_dan).first()
    )
    if con_dung:
        flash("File này thực ra vẫn đang được dùng, không xoá.", "error")
        return redirect(url_for("tasks.thu_vien", nguon="mo_coi"))

    goc_that = os.path.realpath(current_app.config["UPLOAD_ROOT"])
    duong_dan_that = os.path.realpath(os.path.join(goc_that, *duong_dan.split("/")))
    if not (duong_dan_that == goc_that or duong_dan_that.startswith(goc_that + os.sep)):
        abort(400)
    try:
        os.remove(duong_dan_that)
        flash("Đã xoá vĩnh viễn file mồ côi.", "success")
    except OSError:
        flash("Không tìm thấy file (có thể đã bị xoá trước đó).", "info")
    return redirect(url_for("tasks.thu_vien", nguon="mo_coi"))


@bp.route("/thu-vien/xoa-anh-danh-gia/<int:id>", methods=["POST"])
@login_required
def xoa_anh_danh_gia(id):
    """Chỉ Admin/Sếp — xoá 1 ảnh đánh giá, kể cả file vật lý. Không ảnh
    hưởng tới nội dung/số sao đã chấm, chỉ mất ảnh minh hoạ đi kèm."""
    if not current_user.la_admin_sep:
        abort(403)
    adg = db.session.get(AnhDanhGia, id) or abort(404)
    duong_dan = os.path.join(current_app.config["UPLOAD_ROOT"], *adg.duong_dan.split("/"))
    try:
        os.remove(duong_dan)
    except OSError:
        pass
    ma_viec = adg.danh_gia.cong_viec.ma
    db.session.delete(adg)
    db.session.commit()
    flash(f"Đã xoá ảnh đánh giá của việc {ma_viec}.", "success")
    return redirect(request.referrer or url_for("tasks.thu_vien", nguon="danh_gia"))


@bp.route("/thu-vien/xoa-anh-yeu-cau/<int:id>", methods=["POST"])
@login_required
def xoa_anh_yeu_cau(id):
    """Chỉ Admin/Sếp — xoá 1 ảnh yêu cầu, kể cả file vật lý."""
    if not current_user.la_admin_sep:
        abort(403)
    ayc = db.session.get(AnhYeuCau, id) or abort(404)
    duong_dan = os.path.join(current_app.config["UPLOAD_ROOT"], *ayc.duong_dan.split("/"))
    try:
        os.remove(duong_dan)
    except OSError:
        pass
    ma_viec = ayc.cong_viec.ma
    db.session.delete(ayc)
    db.session.commit()
    flash(f"Đã xoá ảnh yêu cầu của việc {ma_viec}.", "success")
    return redirect(request.referrer or url_for("tasks.thu_vien", nguon="yeu_cau"))


@bp.route("/thu-vien/<int:id>/xoa", methods=["POST"])
@login_required
def xoa_dinh_kem(id):
    """Chỉ Admin/Sếp — xoá 1 đối chứng, kể cả file vật lý. Không thể khôi
    phục, và không ảnh hưởng gì tới việc/lịch sử đánh giá của việc đó."""
    if not current_user.la_admin_sep:
        abort(403)
    dk = db.session.get(DinhKem, id) or abort(404)
    duong_dan = os.path.join(current_app.config["UPLOAD_ROOT"], *dk.duong_dan.split("/"))
    try:
        os.remove(duong_dan)
    except OSError:
        pass
    ma_viec = dk.cong_viec.ma
    db.session.delete(dk)
    db.session.commit()
    flash(f"Đã xoá đối chứng của việc {ma_viec}.", "success")
    return redirect(request.referrer or url_for("tasks.thu_vien"))


@bp.route("/viec/<int:viec_id>/dat-lai-han", methods=["POST"])
@login_required
def dat_lai_han(viec_id):
    """Chỉ Admin/Sếp — đặt lại (hoặc đặt lần đầu) hạn hoàn thành + ngày bắt
    đầu cho 1 công việc, kể cả khi giao việc trước đó lỡ quên đặt. Báo
    Zalo cho nhân viên ngay sau khi đổi."""
    if not current_user.la_admin_sep:
        abort(403)
    viec = db.session.get(CongViec, viec_id) or abort(404)

    han_raw = (request.form.get("han") or "").strip()
    han_moi = None
    if han_raw:
        try:
            han_moi = datetime.strptime(han_raw, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Hạn hoàn thành không đúng định dạng.", "error")
            return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))
        if han_moi <= gio_vn_hien_tai():
            flash("Hạn hoàn thành phải ở trong tương lai — nếu đặt hạn đã qua, "
                  "việc sẽ bị hệ thống tự động đóng và chấm 0 sao ngay lập tức.", "error")
            return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))
        if services.la_ngay_nghi(han_moi.date()):
            flash(f"Ngày {han_moi:%d/%m/%Y} là ngày nghỉ (Chủ nhật/lễ) — chọn ngày làm việc khác.", "error")
            return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))

    ngay_bat_dau_raw = (request.form.get("ngay_bat_dau") or "").strip()
    ngay_bat_dau_moi = None
    if ngay_bat_dau_raw:
        try:
            ngay_bat_dau_moi = date.fromisoformat(ngay_bat_dau_raw)
        except ValueError:
            flash("Ngày bắt đầu không đúng định dạng.", "error")
            return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))
        if han_moi and ngay_bat_dau_moi > han_moi.date():
            flash("Ngày bắt đầu phải trước hoặc bằng ngày của hạn hoàn thành.", "error")
            return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))

    han_cu = viec.han
    if han_cu == han_moi and viec.ngay_bat_dau == ngay_bat_dau_moi:
        flash("Không có gì thay đổi.", "info")
        return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))

    viec.han = han_moi
    viec.ngay_bat_dau = ngay_bat_dau_moi
    viec.da_nhac_sap_qua_han = False  # đặt hạn mới -> cho nhắc lại từ đầu
    db.session.commit()

    services.bao_dat_lai_han(viec, han_cu, current_user)
    db.session.commit()

    flash("Đã cập nhật hạn hoàn thành, đã báo Zalo cho nhân viên.", "success")
    return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))


@bp.route("/viec/<int:viec_id>/doi-uu-tien", methods=["POST"])
@login_required
def doi_uu_tien(viec_id):
    """AJAX — kéo thả đổi mức độ ngay trên lưới "Chưa hoàn thành". Chỉ
    Admin/Sếp, chỉ áp dụng cho việc chưa hoàn thành, báo Zalo cho nhân
    viên ngay sau khi đổi."""
    if not current_user.la_admin_sep:
        return jsonify({"ok": False, "loi": "Không có quyền."}), 403

    viec = db.session.get(CongViec, viec_id)
    if not viec:
        return jsonify({"ok": False, "loi": "Không tìm thấy công việc."}), 404
    if viec.trang_thai not in TrangThai.DANG_MO:
        return jsonify({"ok": False, "loi": "Chỉ đổi được mức độ cho việc chưa hoàn thành."})

    uu_tien_moi = request.form.get("do_uu_tien") or ""
    if uu_tien_moi not in DoUuTien.NHAN:
        return jsonify({"ok": False, "loi": "Mức độ không hợp lệ."})

    if uu_tien_moi == viec.do_uu_tien:
        return jsonify({"ok": True})

    uu_tien_cu_ten = viec.ten_uu_tien
    viec.do_uu_tien = uu_tien_moi
    db.session.commit()

    services.bao_doi_uu_tien(viec, uu_tien_cu_ten, current_user)
    db.session.commit()

    return jsonify({"ok": True})