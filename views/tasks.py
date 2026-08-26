import os
import re
from datetime import datetime, date, timedelta

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import case

import services
from extensions import db
from models import (CongViec, DanhGia, DinhKem, DoUuTien, LoaiDinhKem, NguoiDung,
                    TrangThai, VaiTro, gio_vn_hien_tai, ngay_vn_hien_tai)

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
    dau_ngay = datetime.combine(hom_nay, datetime.min.time())
    cuoi_ngay = datetime.combine(hom_nay, datetime.max.time())

    cua_toi = (
        CongViec.query.filter(
            CongViec.nguoi_nhan_id == current_user.id,
            CongViec.trang_thai.in_(TrangThai.DANG_MO),
            CongViec.han >= dau_ngay,
            CongViec.han <= cuoi_ngay,
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

    viec_hom_nay_cong_ty = 0
    if current_user.la_admin_sep:
        viec_hom_nay_cong_ty = CongViec.query.filter(
            CongViec.trang_thai.in_(TrangThai.DANG_MO),
            CongViec.han >= dau_ngay,
            CongViec.han <= cuoi_ngay,
        ).count()

    loi_chao, loi_chao_phu, chao_mung_icon = _loi_chao_theo_gio()
    return render_template(
        "dashboard.html",
        cua_toi=cua_toi,
        cua_toi_theo_cot=cua_toi_theo_cot,
        cho_toi_duyet=cho_toi_duyet,
        cc_hom_nay=cc_hom_nay,
        viec_hom_nay_cong_ty=viec_hom_nay_cong_ty,
        loi_chao=loi_chao,
        loi_chao_phu=loi_chao_phu,
        chao_mung_icon=chao_mung_icon,
        hom_nay_hien_thi=f"{_THU_TRONG_TUAN[hom_nay.weekday()]}, {hom_nay:%d/%m/%Y}",
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
        # Chưa hoàn thành: chia 3 cột theo mức độ, mỗi cột xếp hạn gần nhất
        # lên đầu — không phân trang, xem hết trong 1 trang cho dễ bao quát.
        q = q.filter(CongViec.trang_thai.in_(TrangThai.DANG_MO))
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
    mo_ta, loi = services.ai_goi_y_mo_ta(tieu_de)
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
    mo_ta, loi = services.ai_tom_tat_mo_ta(noi_dung)
    if loi:
        return jsonify({"ok": False, "loi": loi})
    return jsonify({"ok": True, "mo_ta": mo_ta})


@bp.route("/viec/moi", methods=["GET", "POST"])
@login_required
def giao_viec():
    if not current_user.la_quan_ly:
        abort(403)
    nhan_vien = _nhan_vien_duoc_giao()
    thang_hien_tai = ngay_vn_hien_tai().strftime("%Y-%m")

    if request.method == "POST":
        nguoi_nhan_ids = request.form.getlist("nguoi_nhan_id")
        tieu_de = (request.form.get("tieu_de") or "").strip()
        mo_ta = (request.form.get("mo_ta") or "").strip()
        uu_tien = request.form.get("do_uu_tien") or DoUuTien.THUONG
        han_raw = request.form.get("han") or ""
        cac_ngay_raw = request.form.getlist("ngay_hang_ngay")
        gio_hang_ngay = request.form.get("gio_hang_ngay") or "08:00"
        thang_hang_ngay = request.form.get("thang_hang_ngay") or thang_hien_tai

        def loi(thong_bao):
            flash(thong_bao, "error")
            return render_template(
                "task_form.html", nhan_vien=nhan_vien, tieu_de=tieu_de, mo_ta=mo_ta,
                han=han_raw, uu_tien=uu_tien, ngay_da_chon=cac_ngay_raw,
                gio_hang_ngay=gio_hang_ngay, thang_hang_ngay=thang_hang_ngay,
            )

        if not tieu_de or not nguoi_nhan_ids:
            return loi("Cần nhập tên công việc và chọn ít nhất 1 người nhận.")

        # Hằng ngày: nhiều ngày rời rạc, dùng chung 1 giờ hạn mỗi ngày.
        # Còn lại: 1 hạn duy nhất (datetime-local) như cũ.
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
        else:
            han = None
            if han_raw:
                try:
                    han = datetime.strptime(han_raw, "%Y-%m-%dT%H:%M")
                except ValueError:
                    return loi("Hạn hoàn thành không đúng định dạng.")
            danh_sach_han = [han]

        tao = []
        for nid in nguoi_nhan_ids:
            nv = db.session.get(NguoiDung, int(nid))
            if not nv or not nv.dang_hoat_dong or nv.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
                continue
            for h in danh_sach_han:
                v = CongViec(
                    tieu_de=tieu_de, mo_ta=mo_ta, han=h, do_uu_tien=uu_tien,
                    nguoi_giao_id=current_user.id, nguoi_nhan_id=nv.id,
                    trang_thai=TrangThai.MOI,
                )
                db.session.add(v)
                tao.append(v)

        # Việc THÊM (khác tên/nội dung với việc chính ở trên) trong cùng 1
        # lần giao — mỗi khối 1 hạn đơn, không hỗ trợ lịch nhiều ngày kiểu
        # Hằng ngày (giữ đơn giản). Khối nào để trống tên/chưa chọn người
        # nhận thì bỏ qua, không chặn cả form. Dò theo tên field tieu_de_N
        # thay vì đếm số khối, nên xoá khối giữa chừng ở form vẫn an toàn.
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

            tao_i = []
            for nid in nguoi_nhan_ids_i:
                nv = db.session.get(NguoiDung, int(nid))
                if not nv or not nv.dang_hoat_dong or nv.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
                    continue
                v = CongViec(
                    tieu_de=tieu_de_i, mo_ta=mo_ta_i, han=han_i, do_uu_tien=uu_tien_i,
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

    return render_template("task_form.html", nhan_vien=nhan_vien, thang_hang_ngay=thang_hien_tai)


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
        viec.mo_ta = (viec.mo_ta or "") + f"\n\n--- Kết quả lần {viec.lan_gui} ---\n{ghi_chu}"
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

    if ket_qua == "dat":
        so_sao = request.form.get("so_sao", type=int)
        if not so_sao or not 1 <= so_sao <= 5:
            flash("Chọn số sao từ 1 đến 5.", "error")
            return redirect(url_for("tasks.chi_tiet", viec_id=viec.id))
        dg = DanhGia(cong_viec_id=viec.id, nguoi_danh_gia_id=current_user.id,
                     lan_gui=viec.lan_gui, ket_qua="dat", so_sao=so_sao,
                     ghi_chu=ghi_chu)
        viec.trang_thai = TrangThai.HOAN_THANH
        viec.hoan_thanh_luc = gio_vn_hien_tai()
        viec.so_sao_cuoi = so_sao
        db.session.add(dg)
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
    """Chỉ Admin/Ban giám đốc — mở lại việc đã bị hệ thống tự động đóng
    (nhận biết qua so_sao_cuoi == 0, vì chấm tay không bao giờ cho ra 0 sao).
    Mở lại đưa việc về Làm lại, tự động được cộng 3 giờ gia hạn theo đúng
    quy tắc tính KPI đã có."""
    if not current_user.la_admin_sep:
        abort(403)
    viec = db.session.get(CongViec, viec_id) or abort(404)
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
    kèm trên ổ đĩa. Không thể khôi phục, khác với Huỷ (chỉ đổi trạng thái)."""
    if not current_user.la_admin_sep:
        abort(403)
    viec = db.session.get(CongViec, viec_id) or abort(404)
    ma, ten = viec.ma, viec.tieu_de
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


@bp.route("/thu-vien")
@login_required
def thu_vien():
    """Xem toàn bộ đối chứng đã nộp — phạm vi theo đúng quyền xem việc sẵn
    có (nhân viên chỉ thấy việc liên quan tới mình, quản lý thấy trong bộ
    phận, admin/sếp thấy toàn công ty)."""
    trang = request.args.get("trang", 1, type=int)
    nguoi = request.args.get("nguoi", type=int)
    loai = request.args.get("loai", "")
    tim = (request.args.get("q") or "").strip()
    tu_ngay_raw = request.args.get("tu_ngay") or ""
    den_ngay_raw = request.args.get("den_ngay") or ""

    viec_ids = _viec_lien_quan().with_entities(CongViec.id)
    q = DinhKem.query.join(CongViec, DinhKem.cong_viec_id == CongViec.id).filter(
        DinhKem.cong_viec_id.in_(viec_ids)
    )
    if nguoi:
        q = q.filter(DinhKem.nguoi_tai_len_id == nguoi)
    if loai:
        q = q.filter(DinhKem.loai == loai)
    if tim:
        like = f"%{tim}%"
        q = q.filter(db.or_(CongViec.tieu_de.ilike(like), CongViec.ma.ilike(like)))
    if tu_ngay_raw:
        try:
            q = q.filter(DinhKem.tao_luc >= datetime.combine(date.fromisoformat(tu_ngay_raw), datetime.min.time()))
        except ValueError:
            tu_ngay_raw = ""
    if den_ngay_raw:
        try:
            q = q.filter(DinhKem.tao_luc <= datetime.combine(date.fromisoformat(den_ngay_raw), datetime.max.time()))
        except ValueError:
            den_ngay_raw = ""

    q = q.order_by(DinhKem.tao_luc.desc())
    phan_trang = q.paginate(page=trang, per_page=24, error_out=False)

    return render_template(
        "thu_vien.html",
        phan_trang=phan_trang, ds=phan_trang.items,
        nhan_vien=_nhan_vien_duoc_giao() if current_user.la_quan_ly else [],
        f_nguoi=nguoi, f_loai=loai, f_q=tim, f_tu_ngay=tu_ngay_raw, f_den_ngay=den_ngay_raw,
    )


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