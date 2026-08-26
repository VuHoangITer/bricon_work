"""Lớp dịch vụ: gửi Zalo, lưu file, tính toạ độ, tính KPI, xuất Excel."""
import json
import math
import os
import uuid
from datetime import datetime, date, timedelta
from io import BytesIO

import requests
from flask import current_app, url_for
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from werkzeug.utils import secure_filename

from extensions import db
from models import (BuoiNghi, ChamCong, CongViec, DanhGia, DiemChamCong, LogZalo,
                    LoaiDinhKem, NguoiDung, TrangThai, VaiTro, gio_vn_hien_tai, ngay_vn_hien_tai)

# ---------------------------------------------------------------------------
# GỬI ZALO
# ---------------------------------------------------------------------------

def _bot_token(nguoi_dung: NguoiDung | None) -> str:
    if nguoi_dung and nguoi_dung.bot_zalo and nguoi_dung.bot_zalo.dang_hoat_dong:
        return nguoi_dung.bot_zalo.token
    return current_app.config["ZALO_DEFAULT_BOT_TOKEN"]


def gui_zalo(chat_id: str, noi_dung: str, nguoi_dung: NguoiDung | None = None,
             cong_viec: CongViec | None = None, token_ghi_de: str | None = None) -> bool:
    """Gửi 1 tin nhắn text tới 1 chat Zalo. Luôn ghi log, không bao giờ raise.

    Không để lỗi Zalo làm hỏng giao dịch chính (giao việc, duyệt việc...).
    token_ghi_de dùng khi gửi không gắn với 1 nhân viên cụ thể (VD: nhóm QL).
    """
    log = LogZalo(
        nguoi_dung_id=nguoi_dung.id if nguoi_dung else None,
        cong_viec_id=cong_viec.id if cong_viec else None,
        chat_id=chat_id,
        noi_dung=noi_dung,
    )
    token = token_ghi_de or _bot_token(nguoi_dung)
    if not chat_id or not token:
        log.phan_hoi = "Thiếu chat_id hoặc bot token"
        db.session.add(log)
        return False

    url = f"{current_app.config['ZALO_API_BASE']}{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": noi_dung}, timeout=10)
        log.thanh_cong = r.ok
        log.phan_hoi = r.text[:1000]
    except Exception as e:  # noqa: BLE001
        log.thanh_cong = False
        log.phan_hoi = f"{type(e).__name__}: {e}"[:1000]
    db.session.add(log)
    return log.thanh_cong


def gui_cho_nhan_vien(nv: NguoiDung, noi_dung: str, cong_viec: CongViec | None = None) -> bool:
    return gui_zalo(nv.zalo_group_id, noi_dung, nguoi_dung=nv, cong_viec=cong_viec)


def gui_nhom_ql(noi_dung: str, cong_viec: CongViec | None = None) -> bool:
    """Gửi vào nhóm Quản Lý. Dùng token riêng của bot phụ trách nhóm QL."""
    token = current_app.config["ZALO_BOT_TOKEN_QL"] or current_app.config["ZALO_DEFAULT_BOT_TOKEN"]
    return gui_zalo(current_app.config["ZALO_GROUP_QL"], noi_dung,
                    cong_viec=cong_viec, token_ghi_de=token)


# ---------------------------------------------------------------------------
# NGHỈ PHÉP
# ---------------------------------------------------------------------------

def co_nghi_phep_buoi(nguoi_dung_id: int, ngay, buoi: str) -> bool:
    """True nếu nhân viên có nghỉ phép đã ghi nhận cho đúng buổi này (hoặc
    cả ngày) — dùng để bỏ qua tính đi trễ/về sớm cho đúng buổi đã xin nghỉ."""
    from models import XinNghi
    return XinNghi.query.filter(
        XinNghi.nguoi_dung_id == nguoi_dung_id,
        XinNghi.ngay == ngay,
        XinNghi.buoi.in_((buoi, BuoiNghi.CA_NGAY)),
    ).first() is not None


def bao_xin_nghi(nguoi_dung: NguoiDung, ngay_dau, ngay_cuoi, buoi: str,
                  ghi_chu: str | None, so_ngay_cong: float):
    """Báo 1 tin gộp vào nhóm QL khi có người xin nghỉ (đã tự động duyệt qua
    ảnh minh chứng) — không cần ai duyệt tay, nhưng vẫn cho sếp biết."""
    if ngay_dau == ngay_cuoi:
        thoi_gian = f"Ngày {ngay_dau:%d/%m/%Y} — {BuoiNghi.NHAN.get(buoi, buoi)}"
    else:
        thoi_gian = f"Từ ngày {ngay_dau:%d/%m/%Y} đến ngày {ngay_cuoi:%d/%m/%Y} (cả ngày)"
    nd = (
        f"🏖️ Đã ghi nhận nghỉ phép — tự động duyệt qua ảnh minh chứng\n\n"
        f"{nguoi_dung.ho_ten} ({nguoi_dung.ma_dinh_danh})\n"
        f"{thoi_gian}\n"
        f"Tính {so_ngay_cong:g} ngày công."
    )
    if ghi_chu:
        nd += f"\nGhi chú: {ghi_chu}"
    gui_nhom_ql(nd)


def lay_cac_chat_gan_day(token: str) -> tuple[list[dict], str | None, str]:
    """Gọi getUpdates để tìm các nhóm/chat bot vừa nhận được tin nhắn — dùng
    để dò chat_id của 1 nhóm mới thêm bot vào.

    Trả về (danh sách chat đã nhận diện được, thông báo lỗi nếu có, JSON thô
    để đối chiếu tay nếu cách đọc tự động ở đây thiếu do khác định dạng).
    """
    url = f"{current_app.config['ZALO_API_BASE']}{token}/getUpdates"
    try:
        r = requests.get(url, timeout=10)
        tho = r.text
        du_lieu = r.json()
    except Exception as e:  # noqa: BLE001
        return [], f"Không gọi được API: {type(e).__name__}: {e}", ""

    if du_lieu.get("ok") is False:
        return [], du_lieu.get("description") or "Zalo báo lỗi nhưng không rõ nguyên nhân.", tho

    ds = du_lieu.get("result")
    if ds is None:
        ds = du_lieu.get("updates") or []
    if isinstance(ds, dict):
        ds = [ds]  # Zalo trả 1 object đơn (không phải mảng) khi chỉ có 1 cập nhật
    if not isinstance(ds, list):
        return [], "Zalo trả về dữ liệu không đúng định dạng mong đợi — xem JSON thô bên dưới.", tho

    thay: dict = {}
    for cap_nhat in ds:
        tin = cap_nhat.get("message") or {}
        chat = tin.get("chat") or {}
        nguoi_gui = tin.get("from") or {}
        chat_id = chat.get("id")
        if not chat_id:
            continue
        thay[chat_id] = {
            "chat_id": chat_id,
            "loai": chat.get("chat_type") or chat.get("type") or "—",
            "ten": (chat.get("title") or chat.get("name")
                   or f"(Zalo không trả tên nhóm — tin gần nhất từ {nguoi_gui.get('display_name', '?')})"),
        }
    return list(thay.values()), None, tho


# ---------------------------------------------------------------------------
# WEBHOOK ZALO — thay thế n8n cho các lệnh chat trực tiếp trong nhóm
# ---------------------------------------------------------------------------

def dat_webhook(bot: "BotZalo") -> tuple[bool, str]:
    """Đăng ký webhook với Zalo cho 1 bot — Zalo sẽ tự POST tin nhắn mới về
    /webhook/zalo/<bot_id> thay vì phải gọi getUpdates để hỏi."""
    base = current_app.config["BASE_URL"]
    if not base.startswith("https://"):
        return False, ("BASE_URL hiện không phải https — Zalo sẽ từ chối webhook. "
                       "Cần deploy với domain thật + SSL trước khi đặt webhook.")

    url_webhook = f"{base}/webhook/zalo/{bot.id}"
    api_url = f"{current_app.config['ZALO_API_BASE']}{bot.token}/setWebhook"
    try:
        r = requests.post(api_url, json={
            "url": url_webhook,
            "secret_token": bot.webhook_secret,
        }, timeout=10)
        return r.ok, r.text
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def xoa_webhook(bot: "BotZalo") -> tuple[bool, str]:
    api_url = f"{current_app.config['ZALO_API_BASE']}{bot.token}/deleteWebhook"
    try:
        r = requests.post(api_url, timeout=10)
        return r.ok, r.text
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def xu_ly_webhook_zalo(bot: "BotZalo", du_lieu: dict):
    """Xử lý 1 sự kiện webhook Zalo gửi tới.
    - "/id": trả zalo_group_id + zalo_user_id (dò nhóm khi thiết lập).
    - Tag bot + có chữ "giao việc": trả link trang Giao việc mới.
    - Tag bot + có chữ "kpi": trả link trang KPI.
    - Tag bot, còn lại: trả link trang chủ hệ thống.

    Zalo không công bố rõ trường "đã tag bot" trong webhook, nên nhận diện
    bằng nội dung tin nhắn: có tên bot hoặc dấu "@" thì coi là đã tag —
    tránh trả lời tràn lan mọi tin nhắn thường trong nhóm. Muốn thêm lệnh
    khác thì thêm nhánh if bên dưới, cùng một nơi.
    """
    tin = du_lieu.get("message") or {}
    chat = tin.get("chat") or {}
    nguoi_gui = tin.get("from") or {}
    text = (tin.get("text") or "").strip()

    chat_id = chat.get("id")
    if not chat_id or not text:
        return

    text_thuong = text.lower()

    if text_thuong.startswith("/id"):
        nd = (
            f"🆔 Thông tin nhóm/chat này\n\n"
            f"zalo_group_id: {chat_id}\n"
            f"zalo_user_id: {nguoi_gui.get('id', '—')}"
        )
        gui_zalo(chat_id, nd, token_ghi_de=bot.token)
        return

    da_tag = "@" in text or (bot.ten and bot.ten.lower() in text_thuong)
    if not da_tag:
        return

    base = current_app.config["BASE_URL"]
    if "giao việc" in text_thuong or "giao viec" in text_thuong:
        nd = f"📌 Vào đây để giao việc mới:\n{base}/viec/moi"
    elif "kpi" in text_thuong:
        nd = f"📊 Vào đây để xem KPI:\n{base}/kpi"
    else:
        nd = f"👋 Vào hệ thống BRICON WORK tại đây:\n{base}/"
    gui_zalo(chat_id, nd, token_ghi_de=bot.token)



# ---- Nội dung tin nhắn ------------------------------------------------------

def _han_str(viec: CongViec) -> str:
    return viec.han.strftime("%H:%M %d/%m/%Y") if viec.han else "không đặt hạn"


def bao_giao_viec(viec: CongViec):
    nd = (
        f"📌 Bạn có 1 công việc mới: {viec.ten_uu_tien}\n\n"
        f"[{viec.ma}] {viec.tieu_de}\n"
        f"Người giao: {viec.nguoi_giao.ho_ten}\n"
        f"Hạn: {_han_str(viec)}\n\n"
        f"Bấm vào xem chi tiết và gửi đối chứng:\n{viec.link}"
    )
    gui_cho_nhan_vien(viec.nguoi_nhan, nd, viec)


def bao_giao_viec_hang_ngay(nguoi_nhan: NguoiDung, tieu_de: str, ngay_dau, ngay_cuoi,
                             gio_han: str, so_luong: int):
    """Báo gộp 1 tin duy nhất khi giao 1 loạt việc Hằng ngày cho 1 nhân viên,
    thay vì báo riêng từng ngày (mỗi ngày là 1 công việc riêng trong hệ thống,
    nhưng chỉ cần 1 tin Zalo tóm tắt)."""
    if ngay_dau == ngay_cuoi:
        thoi_gian = f"Vào ngày {ngay_dau:%d/%m/%Y}, hạn lúc {gio_han}"
    else:
        thoi_gian = f"Từ ngày {ngay_dau:%d/%m/%Y} tới ngày {ngay_cuoi:%d/%m/%Y}, hạn lúc {gio_han} mỗi ngày"
    nd = (
        f"📅 Bạn có việc hằng ngày mới: {tieu_de}\n\n"
        f"{thoi_gian}.\n"
        f"Tổng cộng {so_luong} công việc đã được tạo.\n\n"
        f"Xem danh sách công việc:\n{current_app.config['BASE_URL']}/viec"
    )
    gui_cho_nhan_vien(nguoi_nhan, nd)


def bao_doi_uu_tien(viec: CongViec, uu_tien_cu_ten: str, nguoi_doi: NguoiDung):
    """Báo cho nhân viên khi Sếp/Admin kéo thả đổi mức độ công việc trên
    trang Công việc."""
    nd = (
        f"🔄 Mức độ công việc đã được đổi\n\n"
        f"[{viec.ma}] {viec.tieu_de}\n"
        f"Từ: {uu_tien_cu_ten} → Sang: {viec.ten_uu_tien}\n"
        f"Người đổi: {nguoi_doi.ho_ten}\n\n"
        f"Xem chi tiết:\n{viec.link}"
    )
    gui_cho_nhan_vien(viec.nguoi_nhan, nd, viec)


# ---------------------------------------------------------------------------
# BÁO CÁO ZALO THEO LỊCH TRONG NGÀY (chạy bằng cron, xem app.py)
# ---------------------------------------------------------------------------

def _nhan_vien_khong_phai_admin_sep() -> list[NguoiDung]:
    return NguoiDung.query.filter_by(dang_hoat_dong=True).filter(
        NguoiDung.vai_tro.notin_((VaiTro.ADMIN, VaiTro.SEP))
    ).all()


def nhac_viec_sap_qua_han(phut_truoc: int = 30) -> int:
    """Nhắc việc sắp tới hạn (mặc định trong vòng 30 phút tới), CHỈ gửi cho
    đúng nhân viên đang nhận việc đó — không báo nhóm QL. Nên chạy bằng
    cron thường xuyên (VD mỗi 5 phút) để bắt kịp mốc giờ; dùng cột
    da_nhac_sap_qua_han để mỗi việc chỉ nhắc đúng 1 lần, không bị nhắc lặp
    lại liên tục trong suốt khung 30 phút đó. Chỉ nhắc việc CHƯA nộp gì
    (mới/đang làm/phải làm lại) — việc đang chờ duyệt thì đã ngoài tầm tay
    nhân viên, không cần nhắc."""
    bay_gio = gio_vn_hien_tai()
    moc_xa = bay_gio + timedelta(minutes=phut_truoc)
    viecs = CongViec.query.filter(
        CongViec.trang_thai.in_(TrangThai.CHUA_XONG),
        CongViec.han.isnot(None),
        CongViec.han > bay_gio,
        CongViec.han <= moc_xa,
        CongViec.da_nhac_sap_qua_han.is_(False),
    ).all()
    for v in viecs:
        nd = (
            f"⏳ Việc sắp tới hạn (còn dưới {phut_truoc} phút)\n\n"
            f"[{v.ma}] {v.tieu_de}\n"
            f"Hạn: {v.han:%H:%M %d/%m/%Y}\n\n"
            f"Xem chi tiết và gửi đối chứng ngay:\n{v.link}"
        )
        gui_cho_nhan_vien(v.nguoi_nhan, nd, v)
        v.da_nhac_sap_qua_han = True
    return len(viecs)


def nhac_cham_cong_sang() -> int:
    """7h45: gửi link chấm công cho từng nhân viên/quản lý (trừ Sếp/Admin —
    2 role này không tự chấm công). Trả về số người đã gửi."""
    base = current_app.config["BASE_URL"]
    nd = f"⏰ Nhớ chấm công vào nhé!\n\n{base}/cham-cong"
    ds = _nhan_vien_khong_phai_admin_sep()
    for nv in ds:
        gui_cho_nhan_vien(nv, nd)
    return len(ds)


def nhac_cham_cong_chieu() -> int:
    """17h32: nhắc chấm công RA — chỉ nhắc đúng người đã chấm công VÀO hôm
    nay nhưng CHƯA chấm ra (không làm phiền người đã ra rồi hoặc chưa từng
    chấm vào). Trả về số người đã gửi."""
    base = current_app.config["BASE_URL"]
    hom_nay = ngay_vn_hien_tai()
    ds = (ChamCong.query.filter_by(ngay=hom_nay)
          .filter(ChamCong.gio_vao.isnot(None), ChamCong.gio_ra.is_(None))
          .all())
    nd = f"⏰ Nhớ chấm công ra trước khi về nhé!\n\n{base}/cham-cong"
    so_luong = 0
    for cc in ds:
        nv = cc.nguoi_dung
        if not nv or not nv.dang_hoat_dong or nv.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
            continue
        gui_cho_nhan_vien(nv, nd)
        so_luong += 1
    return so_luong


def nhac_viec_hom_nay() -> int:
    """8h00: gửi link xem việc hôm nay cho từng nhân viên/quản lý (trừ
    Sếp/Admin). Trả về số người đã gửi."""
    base = current_app.config["BASE_URL"]
    nd = f"📋 Xem công việc hôm nay của bạn:\n\n{base}/viec"
    ds = _nhan_vien_khong_phai_admin_sep()
    for nv in ds:
        gui_cho_nhan_vien(nv, nd)
    return len(ds)


def bao_cao_sang_cho_sep():
    """8h10: báo cáo nhanh đầu ngày cho Sếp/Quản lý — gửi vào nhóm QL."""
    from models import XinNghi
    hom_nay = ngay_vn_hien_tai()
    dau_ngay = datetime.combine(hom_nay, datetime.min.time())
    cuoi_ngay = datetime.combine(hom_nay, datetime.max.time())

    co_mat = ChamCong.query.filter_by(ngay=hom_nay).filter(ChamCong.gio_vao.isnot(None)).count()
    di_tre = ChamCong.query.filter_by(ngay=hom_nay, di_tre=True).count()
    xin_nghi_hom_nay = XinNghi.query.filter_by(ngay=hom_nay).count()
    viec_hom_nay = CongViec.query.filter(CongViec.han >= dau_ngay, CongViec.han <= cuoi_ngay).count()
    qua_han = CongViec.query.filter(
        CongViec.han < gio_vn_hien_tai(), CongViec.trang_thai.in_(TrangThai.CHUA_XONG)).count()
    cho_duyet = CongViec.query.filter_by(trang_thai=TrangThai.CHO_DUYET).count()

    nd = (
        f"📋 BRICON – {hom_nay:%d/%m/%Y}\n\n"
        f"🧑‍💼 {co_mat} nhân viên có mặt\n"
        f"🕐 {di_tre} người đi trễ\n"
        f"📝 {xin_nghi_hom_nay} đơn xin nghỉ hôm nay\n"
        f"📅 {viec_hom_nay} công việc hôm nay\n"
        f"🔴 {qua_han} việc quá hạn\n"
        f"🟡 {cho_duyet} việc chờ đánh giá\n\n"
        f"Chúc anh một ngày làm việc hiệu quả! 💪"
    )
    gui_nhom_ql(nd)


def bao_cao_chieu_cho_sep():
    """17h30: báo cáo tóm tắt cuối ngày cho Sếp/Quản lý — gửi vào nhóm QL."""
    from models import XinNghi
    hom_nay = ngay_vn_hien_tai()
    dau_ngay = datetime.combine(hom_nay, datetime.min.time())
    cuoi_ngay = datetime.combine(hom_nay, datetime.max.time())

    co_mat = ChamCong.query.filter_by(ngay=hom_nay).filter(ChamCong.gio_vao.isnot(None)).count()
    di_tre = ChamCong.query.filter_by(ngay=hom_nay, di_tre=True).count()
    ve_som = ChamCong.query.filter_by(ngay=hom_nay, ve_som=True).count()
    xin_nghi_hom_nay = XinNghi.query.filter_by(ngay=hom_nay).count()
    viec_hom_nay = CongViec.query.filter(CongViec.han >= dau_ngay, CongViec.han <= cuoi_ngay).count()
    hoan_thanh_hom_nay = CongViec.query.filter(
        CongViec.trang_thai == TrangThai.HOAN_THANH,
        CongViec.hoan_thanh_luc >= dau_ngay, CongViec.hoan_thanh_luc <= cuoi_ngay,
    ).count()
    qua_han = CongViec.query.filter(
        CongViec.han < gio_vn_hien_tai(), CongViec.trang_thai.in_(TrangThai.CHUA_XONG)).count()
    cho_duyet = CongViec.query.filter_by(trang_thai=TrangThai.CHO_DUYET).count()

    nd = (
        f"📊 Tóm tắt cuối ngày – BRICON {hom_nay:%d/%m/%Y}\n\n"
        f"🧑‍💼 {co_mat} nhân viên có mặt hôm nay\n"
        f"🕐 {di_tre} người đi trễ · 🏃 {ve_som} người về sớm\n"
        f"📝 {xin_nghi_hom_nay} đơn xin nghỉ hôm nay\n"
        f"📅 {viec_hom_nay} công việc hôm nay · ✅ {hoan_thanh_hom_nay} đã hoàn thành\n"
        f"🔴 {qua_han} việc quá hạn\n"
        f"🟡 {cho_duyet} việc chờ đánh giá\n\n"
        f"Một ngày làm việc nữa đã hoàn tất!"
    )
    gui_nhom_ql(nd)


def bao_cao_thieu_sot():
    """18h00: báo cáo nhân viên nào còn việc chưa nộp/còn thiếu trong ngày
    — gửi vào nhóm QL, liệt kê theo từng người."""
    hom_nay = ngay_vn_hien_tai()
    dau_ngay = datetime.combine(hom_nay, datetime.min.time())
    cuoi_ngay = datetime.combine(hom_nay, datetime.max.time())

    viec_con_thieu = CongViec.query.filter(
        CongViec.han >= dau_ngay, CongViec.han <= cuoi_ngay,
        CongViec.trang_thai.in_(TrangThai.CHUA_XONG),
    ).order_by(CongViec.nguoi_nhan_id).all()

    if not viec_con_thieu:
        gui_nhom_ql(
            f"✅ Hết ngày {hom_nay:%d/%m/%Y}, không còn việc nào tồn đọng "
            f"chưa nộp. Cả đội làm tốt lắm!"
        )
        return

    theo_nguoi: dict[str, list[str]] = {}
    for v in viec_con_thieu:
        theo_nguoi.setdefault(v.nguoi_nhan.ho_ten, []).append(
            f"[{v.ma}] {v.tieu_de} ({v.ten_trang_thai})")

    dong = "\n".join(f"• {ten}: " + "; ".join(vs) for ten, vs in theo_nguoi.items())
    nd = (
        f"📝 Việc còn thiếu cuối ngày {hom_nay:%d/%m/%Y}\n\n"
        f"{dong}\n\n"
        f"Nhắc các bạn hoàn thành và gửi đối chứng sớm nhé."
    )
    gui_nhom_ql(nd)


def bao_gui_doi_chung(viec: CongViec, so_file: int):
    lan = f" (lần {viec.lan_gui})" if viec.lan_gui > 1 else ""
    nd = (
        f"✅ {viec.nguoi_nhan.ho_ten} đã gửi đối chứng{lan}\n\n"
        f"[{viec.ma}] {viec.tieu_de}\n"
        f"Số tệp đính kèm: {so_file}\n\n"
        f"Vào xem và đánh giá:\n{viec.link}"
    )
    gui_cho_nhan_vien(viec.nguoi_giao, nd, viec)


def bao_da_duyet(viec: CongViec, dg: DanhGia):
    sao = "⭐" * (dg.so_sao or 0)
    nd = (
        f"🎉 Sếp đã xem và đánh giá công việc của bạn\n\n"
        f"[{viec.ma}] {viec.tieu_de}\n"
        f"Đánh giá: {sao} ({dg.so_sao}/5)\n"
    )
    if dg.ghi_chu:
        nd += f"Nhận xét: {dg.ghi_chu}\n"
    nd += f"\nXem lại:\n{viec.link}"
    gui_cho_nhan_vien(viec.nguoi_nhan, nd, viec)


def bao_lam_lai(viec: CongViec, dg: DanhGia):
    nd = (
        f"🔁 Công việc chưa đạt, cần làm lại\n\n"
        f"[{viec.ma}] {viec.tieu_de}\n"
        f"Người duyệt: {dg.nguoi_danh_gia.ho_ten}\n"
        f"Lý do: {dg.ghi_chu}\n\n"
        f"Làm lại rồi gửi đối chứng tại đây:\n{viec.link}"
    )
    gui_cho_nhan_vien(viec.nguoi_nhan, nd, viec)


# ---------------------------------------------------------------------------
# LƯU FILE
# ---------------------------------------------------------------------------
DUOI_ANH = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
DUOI_VIDEO = {".mp4", ".mov", ".webm", ".m4v", ".3gp"}
DUOI_AM = {".mp3", ".m4a", ".aac", ".ogg", ".wav", ".webm", ".amr"}
DUOI_FILE = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".zip"}


def phan_loai(ten_file: str, mime: str = "") -> str | None:
    duoi = os.path.splitext(ten_file)[1].lower()
    mime = (mime or "").lower()
    if mime.startswith("image/") or duoi in DUOI_ANH:
        return LoaiDinhKem.ANH
    if mime.startswith("video/") or duoi in DUOI_VIDEO:
        return LoaiDinhKem.VIDEO
    if mime.startswith("audio/") or duoi in DUOI_AM:
        return LoaiDinhKem.GHI_AM
    if duoi in DUOI_FILE:
        return LoaiDinhKem.FILE
    return None


def _duong_dan_moi(ten_goc: str, thu_muc: str = "doi-chung") -> tuple[str, str]:
    """Trả về (đường dẫn tương đối — luôn dùng '/', đường dẫn tuyệt đối)."""
    hom_nay = ngay_vn_hien_tai()
    duoi = os.path.splitext(secure_filename(ten_goc))[1].lower() or ".bin"
    tuong_doi = f"{thu_muc}/{hom_nay:%Y/%m}/{uuid.uuid4().hex}{duoi}"
    tuyet_doi = os.path.join(current_app.config["UPLOAD_ROOT"], *tuong_doi.split("/"))
    os.makedirs(os.path.dirname(tuyet_doi), exist_ok=True)
    return tuong_doi, tuyet_doi


def luu_file(file_storage, thu_muc: str = "doi-chung") -> tuple[str, int]:
    tuong_doi, tuyet_doi = _duong_dan_moi(file_storage.filename, thu_muc)
    file_storage.save(tuyet_doi)
    return tuong_doi, os.path.getsize(tuyet_doi)


# ---------------------------------------------------------------------------
# ĐỊNH VỊ
# ---------------------------------------------------------------------------

def khoang_cach_m(lat1, lng1, lat2, lng2) -> float:
    """Haversine, trả về mét."""
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def diem_gan_nhat(lat: float, lng: float):
    """Trả về (DiemChamCong|None, khoảng cách mét, có trong phạm vi không)."""
    diems = DiemChamCong.query.filter_by(dang_hoat_dong=True).all()
    if not diems:
        return None, None, True  # chưa cấu hình điểm nào thì không chặn
    gan_nhat, kc_min = None, None
    for d in diems:
        kc = khoang_cach_m(lat, lng, d.lat, d.lng)
        if kc_min is None or kc < kc_min:
            gan_nhat, kc_min = d, kc
    return gan_nhat, kc_min, kc_min <= gan_nhat.ban_kinh_m


def trung_toa_do_dang_ngo(nguoi_dung_id: int, lat: float, lng: float, so_ngay: int = 5) -> bool:
    """Cờ nghi ngờ: toạ độ trùng tới 6 chữ số thập phân nhiều ngày liên tiếp.

    GPS thật gần như không bao giờ ra kết quả giống hệt nhau; trùng khít
    thường là toạ độ do app fake GPS cắm cứng.
    """
    truoc = (
        ChamCong.query.filter(
            ChamCong.nguoi_dung_id == nguoi_dung_id,
            ChamCong.lat_vao.isnot(None),
            ChamCong.ngay >= ngay_vn_hien_tai() - timedelta(days=so_ngay * 2),
        )
        .order_by(ChamCong.ngay.desc())
        .limit(so_ngay)
        .all()
    )
    if len(truoc) < 2:
        return False
    return all(
        round(c.lat_vao, 6) == round(lat, 6) and round(c.lng_vao, 6) == round(lng, 6)
        for c in truoc[:2]
    )


# ---------------------------------------------------------------------------
# GIỜ LÀM VIỆC
# ---------------------------------------------------------------------------

def tinh_di_tre(gio: datetime, gio_chuan: tuple[int, int] | None = None,
                phut_tre_cho_phep: int | None = None) -> tuple[bool, int]:
    h, m = gio_chuan or current_app.config["GIO_VAO"]
    chuan = gio.replace(hour=h, minute=m, second=0, microsecond=0)
    phut = (phut_tre_cho_phep if phut_tre_cho_phep is not None
           else current_app.config["PHUT_TRE_CHO_PHEP"])
    han_mem = chuan + timedelta(minutes=phut)
    if gio <= han_mem:
        return False, 0
    return True, int((gio - chuan).total_seconds() // 60)


def tinh_ve_som(gio: datetime, gio_chuan: tuple[int, int] | None = None) -> tuple[bool, int]:
    h, m = gio_chuan or current_app.config["GIO_RA"]
    chuan = gio.replace(hour=h, minute=m, second=0, microsecond=0)
    if gio >= chuan:
        return False, 0
    return True, int((chuan - gio).total_seconds() // 60)


def tinh_lai_tre_som(cc: "ChamCong"):
    """Tính lại đi trễ/về sớm cho 1 bản ghi chấm công ĐÃ CÓ, theo đúng quy
    tắc hiện hành (kể cả nghỉ phép nửa ngày) — dùng để vá dữ liệu cũ chấm
    công trước khi có/đổi quy tắc này. Sửa thẳng lên object, không tự
    commit."""
    if cc.gio_vao:
        if co_nghi_phep_buoi(cc.nguoi_dung_id, cc.ngay, BuoiNghi.SANG):
            cc.di_tre, cc.so_phut_tre = tinh_di_tre(
                cc.gio_vao, current_app.config["GIO_BAT_DAU_CHIEU"],
                current_app.config["PHUT_TRE_CHO_PHEP_NUA_NGAY"])
        elif co_nghi_phep_buoi(cc.nguoi_dung_id, cc.ngay, BuoiNghi.CHIEU):
            cc.di_tre, cc.so_phut_tre = tinh_di_tre(
                cc.gio_vao, current_app.config["GIO_VAO"],
                current_app.config["PHUT_TRE_CHO_PHEP_NUA_NGAY"])
        else:
            cc.di_tre, cc.so_phut_tre = tinh_di_tre(cc.gio_vao)

    if cc.gio_ra:
        if co_nghi_phep_buoi(cc.nguoi_dung_id, cc.ngay, BuoiNghi.CHIEU):
            cc.ve_som, cc.so_phut_som = tinh_ve_som(
                cc.gio_ra, current_app.config["GIO_KET_THUC_SANG"])
        else:
            cc.ve_som, cc.so_phut_som = tinh_ve_som(cc.gio_ra)


def gio_cong_trong_ngay(cc: "ChamCong") -> float:
    """Tính số công (0 / 0.5 / 1) của 1 lần chấm công — CHỈ dựa theo giờ
    vào/ra thực tế cắt theo ranh giới trưa, không liên quan gì tới nghỉ
    phép (nghỉ phép không được cộng thêm công, đúng theo yêu cầu "công chỉ
    tính khi chấm công tại địa điểm").

    Có mặt buổi sáng: chấm vào lúc hoặc trước giờ bắt đầu buổi chiều.
    Có mặt buổi chiều: đã chấm ra, lúc hoặc sau giờ kết thúc buổi sáng.
    """
    if not cc.gio_vao:
        return 0.0
    h_sang, m_sang = current_app.config["GIO_KET_THUC_SANG"]
    h_chieu, m_chieu = current_app.config["GIO_BAT_DAU_CHIEU"]
    moc_ket_thuc_sang = cc.gio_vao.replace(hour=h_sang, minute=m_sang, second=0, microsecond=0)
    moc_bat_dau_chieu = cc.gio_vao.replace(hour=h_chieu, minute=m_chieu, second=0, microsecond=0)

    co_lam_sang = cc.gio_vao <= moc_bat_dau_chieu
    co_lam_chieu = bool(cc.gio_ra) and cc.gio_ra >= moc_ket_thuc_sang
    return (0.5 if co_lam_sang else 0.0) + (0.5 if co_lam_chieu else 0.0)


# ---------------------------------------------------------------------------
# MÃ CÔNG VIỆC
# ---------------------------------------------------------------------------

def gan_ma(viec: CongViec):
    """Mã sinh từ khoá chính Postgres -> không bao giờ trùng, không cần đếm."""
    viec.ma = f"V{viec.id + current_app.config['TASK_CODE_OFFSET']}"


# ---------------------------------------------------------------------------
# KPI — thang 0-5 sao theo tiêu chí thái độ làm việc
# ---------------------------------------------------------------------------
NHAN_SAO = {
    0: "Không làm",
    1: "Làm cho có",
    2: "Có làm nhưng cần nhắc nhở",
    3: "Làm đầy đủ, có trách nhiệm",
    4: "Có tâm, nhiệt huyết",
    5: "Chủ động, sáng tạo, vượt mong đợi",
}

GIA_HAN_LAM_LAI = timedelta(hours=3)  # cộng thêm kể từ lúc sếp bấm "Yêu cầu làm lại"


def _han_hieu_luc(v: CongViec) -> datetime | None:
    """Hạn dùng để xét quá hạn khi tính KPI.

    Mặc định là hạn gốc (v.han). Mỗi lần sếp yêu cầu làm lại trong lúc việc
    đã quá hạn gốc (đã nộp, đang chờ duyệt, nhưng trễ), nhân viên được cộng
    thêm GIA_HAN_LAM_LAI kể từ đúng lúc sếp bấm "Yêu cầu làm lại" — nếu vẫn
    tính theo hạn cũ thì coi như họ không còn chút thời gian nào để sửa lại.
    Nhiều lần làm lại liên tiếp thì cộng dồn theo từng lần.
    """
    if not v.han:
        return None
    han = v.han
    for dg in v.danh_gia:
        if dg.ket_qua == "lam_lai" and dg.tao_luc > han:
            han = dg.tao_luc + GIA_HAN_LAM_LAI
    return han


def nhan_theo_sao(sao_tb: float | None) -> str:
    if sao_tb is None:
        return "— Chưa có dữ liệu"
    muc = min(5, max(0, math.floor(sao_tb + 0.5)))
    return f"{muc}★ {NHAN_SAO[muc]}"


def tinh_kpi(tu_ngay: date, den_ngay: date, nguoi_dung_id: int | None = None) -> list[dict]:
    """Tính KPI trong khoảng ngày [tu_ngay, den_ngay].

    - Việc đã Hoàn thành: tính theo ngày hoàn thành (hoan_thanh_luc) nằm
      trong khoảng, dùng đúng số sao sếp đã chấm.
    - Việc chưa xong (mới/đang làm/làm lại/chờ duyệt): tính theo hạn (han)
      nằm trong khoảng — để bắt được cả việc quá hạn mà chưa nộp gì.
    - Quá hạn hiệu lực mà chưa nộp gì lại (moi/dang_lam/lam_lai) -> tự động 0★.
    - Đang Chờ duyệt (đã nộp, sếp chưa xem) -> không tự chấm dù có quá hạn
      hay không, để sếp vào đánh giá tay.
    """
    dau = datetime.combine(tu_ngay, datetime.min.time())
    cuoi = datetime.combine(den_ngay, datetime.max.time())
    bay_gio = gio_vn_hien_tai()

    q_xong = CongViec.query.join(
        NguoiDung, CongViec.nguoi_nhan_id == NguoiDung.id
    ).filter(
        NguoiDung.vai_tro.notin_((VaiTro.ADMIN, VaiTro.SEP)),
        CongViec.trang_thai == TrangThai.HOAN_THANH,
        CongViec.hoan_thanh_luc.isnot(None),
        CongViec.hoan_thanh_luc >= dau,
        CongViec.hoan_thanh_luc <= cuoi,
    )
    q_con_lai = CongViec.query.join(
        NguoiDung, CongViec.nguoi_nhan_id == NguoiDung.id
    ).filter(
        NguoiDung.vai_tro.notin_((VaiTro.ADMIN, VaiTro.SEP)),
        CongViec.han.isnot(None),
        CongViec.han >= dau,
        CongViec.han <= cuoi,
        CongViec.trang_thai.notin_((TrangThai.HOAN_THANH, TrangThai.HUY)),
    )
    if nguoi_dung_id:
        q_xong = q_xong.filter(CongViec.nguoi_nhan_id == nguoi_dung_id)
        q_con_lai = q_con_lai.filter(CongViec.nguoi_nhan_id == nguoi_dung_id)

    viecs = {v.id: v for v in q_xong.all()}
    for v in q_con_lai.all():
        viecs.setdefault(v.id, v)

    bang: dict[int, dict] = {}
    for v in viecs.values():
        o = bang.setdefault(
            v.nguoi_nhan_id,
            {
                "nguoi_dung_id": v.nguoi_nhan_id,
                "ho_ten": v.nguoi_nhan.ho_ten,
                "ma_dinh_danh": v.nguoi_nhan.ma_dinh_danh,
                "tong_viec": 0, "da_danh_gia": 0, "cho_duyet": 0, "dang_lam": 0,
                "tong_sao": 0,
            },
        )
        o["tong_viec"] += 1

        if v.trang_thai == TrangThai.HOAN_THANH:
            if v.so_sao_cuoi is not None:
                o["da_danh_gia"] += 1
                o["tong_sao"] += v.so_sao_cuoi
            continue

        if v.trang_thai == TrangThai.CHO_DUYET:
            o["cho_duyet"] += 1
            continue

        # còn lại: moi / dang_lam / lam_lai
        han = _han_hieu_luc(v)
        if han and bay_gio > han:
            o["da_danh_gia"] += 1
            o["tong_sao"] += 0
        else:
            o["dang_lam"] += 1

    ds = []
    for o in bang.values():
        o["sao_tb"] = round(o["tong_sao"] / o["da_danh_gia"], 2) if o["da_danh_gia"] else None
        o["xep_loai"] = nhan_theo_sao(o["sao_tb"])
        ds.append(o)
    ds.sort(key=lambda x: (x["sao_tb"] is None, -(x["sao_tb"] or 0)))
    for i, o in enumerate(ds, 1):
        o["hang"] = i
    return ds


# ---------------------------------------------------------------------------
# TỰ ĐỘNG ĐÓNG VIỆC QUÁ HẠN
# ---------------------------------------------------------------------------

def bao_tu_dong_dong(viec: CongViec):
    nd_nv = (
        f"🔒 Công việc đã tự động đóng do quá hạn không nộp đối chứng\n\n"
        f"[{viec.ma}] {viec.tieu_de}\n"
        f"Kết quả: 0★ — Không làm\n\n"
        f"Việc đã bị khoá lại, chỉ Admin hoặc Ban giám đốc mới mở lại được.\n{viec.link}"
    )
    gui_cho_nhan_vien(viec.nguoi_nhan, nd_nv, viec)

    nd_ql = (
        f"🔴 Việc tự động đóng 0★ — quá hạn không làm\n\n"
        f"[{viec.ma}] {viec.tieu_de}\n"
        f"Người thực hiện: {viec.nguoi_nhan.ho_ten} ({viec.nguoi_nhan.ma_dinh_danh})\n"
        f"Người giao: {viec.nguoi_giao.ho_ten}\n"
        f"Hạn: {_han_str(viec)}\n\n"
        f"{viec.link}"
    )
    gui_nhom_ql(nd_ql, viec)


def dong_cac_viec_qua_han() -> list[CongViec]:
    """Tự động đóng + chấm 0★ các việc đã quá hạn hiệu lực mà chưa nộp đối
    chứng (mới/đang làm/làm lại — không đụng tới việc đang Chờ duyệt, luôn
    để sếp tự chấm). Gọi hàm này ở nhiều điểm (mỗi lần tải trang liên quan
    tới công việc, và cả qua cron) để việc được đóng gần như ngay khi quá hạn.
    """
    bay_gio = gio_vn_hien_tai()
    ung_vien = CongViec.query.filter(
        CongViec.trang_thai.in_(TrangThai.CHUA_XONG),
        CongViec.han.isnot(None),
        CongViec.han < bay_gio,
    ).all()

    da_dong = []
    for v in ung_vien:
        han = _han_hieu_luc(v)
        if han and bay_gio > han:
            v.trang_thai = TrangThai.HOAN_THANH
            v.so_sao_cuoi = 0
            v.hoan_thanh_luc = bay_gio
            db.session.add(DanhGia(
                cong_viec_id=v.id, nguoi_danh_gia_id=v.nguoi_giao_id,
                lan_gui=v.lan_gui, ket_qua="dat", so_sao=0,
                ghi_chu="Hệ thống tự động đóng: quá hạn mà không nộp đối chứng.",
            ))
            da_dong.append(v)

    if da_dong:
        db.session.commit()
        for v in da_dong:
            bao_tu_dong_dong(v)
        db.session.commit()  # lưu log zalo

    return da_dong


def go_lien_ket_log_zalo_cho_viec(viec: CongViec):
    """Gỡ liên kết log Zalo khỏi 1 công việc trước khi xoá vĩnh viễn công
    việc đó — Postgres chặn xoá nếu còn log tham chiếu qua khoá ngoại
    cong_viec_id. Giữ nguyên log để tra cứu, chỉ gỡ liên kết."""
    for lz in LogZalo.query.filter_by(cong_viec_id=viec.id).all():
        lz.cong_viec_id = None


def xoa_file_dinh_kem(viec: CongViec):
    """Xoá vật lý các tệp đối chứng của 1 công việc trên ổ đĩa, gọi trước khi
    xoá bản ghi CongViec để không để lại file mồ côi."""
    for d in viec.dinh_kem:
        duong_dan = os.path.join(current_app.config["UPLOAD_ROOT"], *d.duong_dan.split("/"))
        try:
            os.remove(duong_dan)
        except OSError:
            pass


def xoa_toan_bo_du_lieu_nhan_vien(nd: NguoiDung, admin_thuc_hien: NguoiDung):
    """Xoá vĩnh viễn 1 nhân viên và TOÀN BỘ dữ liệu gắn với chính họ —
    không thể khôi phục. Chỉ gọi từ route đã tự kiểm tra quyền Admin thuần.

    - Việc họ NHẬN: xoá hẳn, kèm file đính kèm vật lý và đánh giá đi theo
      (đã cascade sẵn qua quan hệ CongViec.dinh_kem / CongViec.danh_gia).
    - Việc họ từng GIAO cho người khác: KHÔNG xoá (sẽ mất lịch sử của
      người nhận việc, không liên quan gì tới người bị xoá) — chỉ đổi
      người giao thành admin đang thực hiện xoá, kèm ghi chú.
    - Đánh giá họ từng chấm cho việc người khác: giữ lại, đổi người
      đánh giá thành admin đang thực hiện xoá.
    - Chấm công, xin nghỉ (kèm ảnh minh chứng vật lý): xoá hẳn — dữ liệu
      của riêng họ.
    - Log Zalo gắn với họ: gỡ liên kết (giữ log để tra cứu, không xoá).
    """
    from models import XinNghi

    for v in CongViec.query.filter_by(nguoi_nhan_id=nd.id).all():
        xoa_file_dinh_kem(v)
        go_lien_ket_log_zalo_cho_viec(v)
        db.session.delete(v)

    for v in CongViec.query.filter_by(nguoi_giao_id=nd.id).all():
        v.nguoi_giao_id = admin_thuc_hien.id
        v.mo_ta = (v.mo_ta or "") + (
            f"\n\n[Người giao gốc {nd.ho_ten} ({nd.ma_dinh_danh}) đã bị xoá tài "
            f"khoản — chuyển người giao sang {admin_thuc_hien.ho_ten}]"
        )

    for dg in DanhGia.query.filter_by(nguoi_danh_gia_id=nd.id).all():
        dg.nguoi_danh_gia_id = admin_thuc_hien.id

    for cc in ChamCong.query.filter_by(nguoi_dung_id=nd.id).all():
        db.session.delete(cc)

    for x in XinNghi.query.filter_by(nguoi_dung_id=nd.id).all():
        duong_dan = os.path.join(current_app.config["UPLOAD_ROOT"], *x.anh_minh_chung.split("/"))
        try:
            os.remove(duong_dan)
        except OSError:
            pass
        db.session.delete(x)

    for lz in LogZalo.query.filter_by(nguoi_dung_id=nd.id).all():
        lz.nguoi_dung_id = None

    db.session.delete(nd)


# ---------------------------------------------------------------------------
# XUẤT EXCEL
# ---------------------------------------------------------------------------
_NEN_TIEU_DE = PatternFill("solid", fgColor="111111")
_CHU_TIEU_DE = Font(bold=True, color="FFFFFF")
_GIUA = Alignment(horizontal="center")


def _ke_tieu_de(ws, do_rong: list[int]):
    for cell in ws[1]:
        cell.font = _CHU_TIEU_DE
        cell.fill = _NEN_TIEU_DE
        cell.alignment = _GIUA
    for i, w in enumerate(do_rong, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w


def xuat_excel_bang_cong(thang: str, ban_ghi: list, don_nghi: list, tong: list) -> BytesIO:
    """Xuất bảng công ra file Excel — 3 sheet: Tổng hợp, Chi tiết chấm công,
    và Nghỉ phép trong tháng."""
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "Tổng hợp"
    ws1.append(["Mã NV", "Họ tên", "Ngày công", "Trễ", "Về sớm", "Nghỉ phép", "Không phép"])
    for o in tong:
        ws1.append([o["ma"], o["ho_ten"], o["so_ngay"], o["tre"], o["som"], o["nghi_phep"], o["khong_phep"]])
    _ke_tieu_de(ws1, [10, 26, 11, 8, 10, 11, 11])

    ws2 = wb.create_sheet("Chi tiết chấm công")
    ws2.append(["Ngày", "Mã NV", "Họ tên", "Giờ vào", "Giờ ra", "Điểm chấm công",
               "Đi trễ (phút)", "Về sớm (phút)", "Nghỉ không phép", "Nghi fake GPS"])
    for c in ban_ghi:
        ws2.append([
            c.ngay.strftime("%d/%m/%Y"),
            c.nguoi_dung.ma_dinh_danh,
            c.nguoi_dung.ho_ten,
            c.gio_vao.strftime("%H:%M") if c.gio_vao else "",
            c.gio_ra.strftime("%H:%M") if c.gio_ra else "",
            c.diem_vao.ten if c.diem_vao else "",
            c.so_phut_tre if c.di_tre else "",
            c.so_phut_som if c.ve_som else "",
            "Có" if c.nghi_khong_phep else "",
            "Có" if c.nghi_ngo else "",
        ])
    _ke_tieu_de(ws2, [12, 10, 26, 10, 10, 22, 14, 14, 15, 14])

    ws3 = wb.create_sheet("Nghỉ phép")
    ws3.append(["Ngày", "Mã NV", "Họ tên", "Buổi", "Ghi chú"])
    for x in don_nghi:
        ws3.append([
            x.ngay.strftime("%d/%m/%Y"),
            x.nguoi_dung.ma_dinh_danh,
            x.nguoi_dung.ho_ten,
            x.ten_buoi,
            x.ghi_chu or "",
        ])
    _ke_tieu_de(ws3, [12, 10, 26, 12, 30])

    dem = BytesIO()
    wb.save(dem)
    dem.seek(0)
    return dem


# ---------------------------------------------------------------------------
# CẤU HÌNH HỆ THỐNG (key-value đơn giản)
# ---------------------------------------------------------------------------

def lay_cai_dat(khoa: str, mac_dinh: str | None = None) -> str | None:
    from models import CaiDat
    cd = db.session.get(CaiDat, khoa)
    return cd.gia_tri if cd and cd.gia_tri else mac_dinh


def dat_cai_dat(khoa: str, gia_tri: str):
    from models import CaiDat
    cd = db.session.get(CaiDat, khoa)
    if not cd:
        cd = CaiDat(khoa=khoa)
        db.session.add(cd)
    cd.gia_tri = gia_tri


# ---------------------------------------------------------------------------
# TÍCH HỢP CHATGPT — gợi ý / tóm tắt yêu cầu chi tiết khi giao việc
# ---------------------------------------------------------------------------
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _goi_chatgpt_tin_nhan(messages: list[dict], dang_json: bool = False) -> tuple[str | None, str | None]:
    """Gọi OpenAI với danh sách messages đầy đủ — hỗ trợ nhiều lượt hội
    thoại (dùng cho trợ lý AI), không chỉ 1 cặp system/user đơn.
    dang_json=True bắt OpenAI trả về đúng 1 object JSON hợp lệ."""
    key = lay_cai_dat("openai_api_key")
    if not key:
        return None, "Chưa cấu hình OpenAI API key ở trang Thiết lập."
    model = current_app.config.get("OPENAI_MODEL", "gpt-4o-mini")
    goi_tin = {"model": model, "messages": messages, "temperature": 0.4}
    if dang_json:
        goi_tin["response_format"] = {"type": "json_object"}
    try:
        r = requests.post(
            _OPENAI_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=goi_tin,
            timeout=25,
        )
        du_lieu = r.json()
    except Exception as e:  # noqa: BLE001
        return None, f"Không gọi được OpenAI: {type(e).__name__}: {e}"

    if "error" in du_lieu:
        return None, du_lieu["error"].get("message", "OpenAI báo lỗi không rõ nguyên nhân.")
    try:
        return du_lieu["choices"][0]["message"]["content"].strip(), None
    except (KeyError, IndexError):
        return None, "Phản hồi từ OpenAI không đúng định dạng mong đợi."


def _goi_chatgpt(he_thong: str, nguoi_dung_hoi: str) -> tuple[str | None, str | None]:
    """Gọi 1 lượt chat đơn giản tới OpenAI. Trả về (nội dung trả lời, lỗi)."""
    return _goi_chatgpt_tin_nhan([
        {"role": "system", "content": he_thong},
        {"role": "user", "content": nguoi_dung_hoi},
    ])


def ai_goi_y_mo_ta(tieu_de: str) -> tuple[str | None, str | None]:
    """Gợi ý mô tả chi tiết dựa theo tên công việc — trả lời dạng gạch đầu
    dòng ngắn gọn để nhân viên dễ hiểu."""
    he_thong = (
        "Bạn giúp soạn mô tả công việc ngắn gọn, rõ ràng bằng tiếng Việt cho "
        "nhân viên một công ty vật liệu xây dựng (keo dán gạch, chống thấm...). "
        "Trả lời dạng các gạch đầu dòng bắt đầu bằng dấu \"-\", mỗi dòng 1 ý cụ "
        "thể (làm gì, ở đâu nếu đoán được, cần nộp lại gì làm bằng chứng). "
        "Không dài dòng, không thêm lời chào hay giải thích ngoài lề."
    )
    return _goi_chatgpt(he_thong, f"Tên công việc: {tieu_de}")


def ai_tom_tat_mo_ta(noi_dung_tho: str) -> tuple[str | None, str | None]:
    """Tóm tắt mô tả dài dòng thành các gạch đầu dòng chính, giữ nguyên ý."""
    he_thong = (
        "Bạn tóm tắt yêu cầu công việc do sếp viết (thường lan man, nhiều ý "
        "trộn lẫn) thành các gạch đầu dòng bắt đầu bằng dấu \"-\", ngắn gọn, "
        "đúng trọng tâm, giữ nguyên đầy đủ ý bằng tiếng Việt, sắp xếp lại cho "
        "nhân viên dễ hiểu, dễ làm theo. Không thêm ý ngoài nội dung gốc, "
        "không thêm lời chào hay giải thích ngoài lề."
    )
    return _goi_chatgpt(he_thong, noi_dung_tho)


# ---------------------------------------------------------------------------
# TRỢ LÝ AI — hỏi đáp trong hệ thống, trả lời theo đúng dữ liệu thật
# ---------------------------------------------------------------------------
_HUONG_DAN_HE_THONG_TRO_LY = (
    "Bạn là trợ lý ảo của BRICON WORK — phần mềm nội bộ quản lý giao việc, "
    "chấm công, KPI và xin nghỉ phép của công ty BRICON. Trả lời bằng tiếng "
    "Việt, ngắn gọn, thân thiện, đúng trọng tâm câu hỏi.\n\n"
    "Hướng dẫn sử dụng hệ thống (dùng để trả lời các câu hỏi \"làm sao để...\"):\n"
    "- Xin nghỉ phép: vào menu Chấm công → bấm \"Xin nghỉ phép\" → chọn từ "
    "ngày/đến ngày (nếu nghỉ đúng 1 ngày thì chọn thêm buổi sáng/chiều/cả "
    "ngày) → bắt buộc đính kèm ảnh giấy phép đã được duyệt → bấm Gửi là "
    "được ghi nhận và tính công ngay, không cần ai duyệt thêm.\n"
    "- Chấm công: vào menu Chấm công, bấm nút chấm công vào/ra, trình duyệt "
    "sẽ xin quyền vị trí — phải đứng trong bán kính cho phép mới chấm được, "
    "ngoài phạm vi thì bị chặn không chấm được.\n"
    "- Xem việc được giao: vào menu Công việc, hoặc xem mục \"Việc hằng "
    "ngày\" ngay trên trang Hôm nay (Dashboard), có lọc theo mức Cần gấp/"
    "Nhiệm vụ chính/Hằng ngày.\n"
    "- Nộp kết quả công việc: vào chi tiết công việc, mục \"Gửi đối chứng\", "
    "đính kèm ảnh/video/tệp hoặc ghi âm tại chỗ rồi gửi. Việc quá hạn mà "
    "chưa nộp gì sẽ tự động đóng 0 sao, khoá lại, chỉ Admin mở lại được.\n"
    "- Xem KPI: vào menu KPI, chọn khoảng ngày muốn xem, thang điểm 0-5 sao.\n\n"
    "CHỈ dùng dữ liệu thật được cung cấp bên dưới cho các CON SỐ, TÊN VIỆC, "
    "LỊCH SỬ cụ thể — không bịa ra công việc, số liệu, hay sự kiện nào không "
    "có trong dữ liệu này. Nếu thiếu dữ liệu để trả lời 1 câu hỏi TRA CỨU "
    "(hỏi có việc gì, số liệu bao nhiêu, ai đã làm gì...), nói rõ là chưa có "
    "dữ liệu, đừng đoán số liệu.\n"
    "QUAN TRỌNG — không được LẪN chủ sở hữu dữ liệu: dữ liệu bên dưới có thể "
    "liệt kê việc/chấm công/KPI của NHIỀU người khác nhau (mỗi người 1 dòng "
    "riêng, có ghi rõ tên). Khi người hỏi dùng từ \"tôi\"/\"của tôi\", CHỈ được "
    "dùng đúng phần dữ liệu đã ghi rõ là \"của chính người đang hỏi\" (thường "
    "nằm ngay đầu ngữ cảnh) — TUYỆT ĐỐI không lấy việc/chấm công của người "
    "khác trong danh sách rồi trả lời như thể đó là của người đang hỏi. Nếu "
    "phần \"của chính người đang hỏi\" trống hoặc không có, phải nói rõ họ "
    "không có dữ liệu đó, không được mượn tạm dữ liệu của người khác.\n"
    "Nhưng nếu được hỏi Ý KIẾN, ĐỀ XUẤT, PHÂN TÍCH, NHẬN XÉT dựa trên dữ liệu "
    "đã có (VD: \"đề xuất phương án cho nhân viên này\" khi đã biết KPI của "
    "họ), hãy CHỦ ĐỘNG đưa ra góc nhìn, gợi ý hợp lý dựa trên đúng số liệu đã "
    "được cung cấp — đây là suy luận trên dữ liệu thật, không phải bịa đặt, "
    "đừng từ chối chỉ vì không có thêm dữ liệu nào khác ngoài những gì đã "
    "cho.\n\n"
    "Bạn (trợ lý AI) không có khả năng TỰ THỰC HIỆN bất kỳ thao tác ghi dữ "
    "liệu nào trong hệ thống cho bất kỳ ai — không tự tạo việc, không tự "
    "xin nghỉ, không tự chấm công hộ người dùng. Đây là giới hạn của CHÍNH "
    "BẠN, không phải giới hạn quyền hạn của người đang hỏi. Khi người dùng "
    "muốn thực hiện 1 thao tác (VD: \"tôi muốn giao việc\", \"đăng ký nghỉ "
    "phép\"), hướng dẫn họ tự bấm trong hệ thống — KHÔNG được suy diễn rằng "
    "vai trò của họ \"không thể\" làm việc đó, trừ khi dữ liệu bên dưới nói "
    "rõ vai trò đó thực sự bị cấm (chỉ Sếp/Quản trị không tự NHẬN việc "
    "được giao cho mình — còn việc họ GIAO việc cho người khác thì vẫn "
    "làm bình thường qua trang Giao việc mới, hai việc này khác nhau, "
    "đừng nhầm).\n\n"
    "Về lương, mô tả công việc, chế độ theo vị trí: CHỈ được dùng đúng phần "
    "\"Thông tin riêng cho chức vụ\" của chính người đang hỏi (nếu có nạp bên "
    "dưới). Không được suy đoán, ước lượng, hay bịa ra thông tin của chức vụ "
    "khác — vì bạn không hề được cung cấp dữ liệu của chức vụ khác, nên nếu "
    "người hỏi tò mò về vị trí khác, trả lời rằng bạn không có thông tin đó.\n\n"
    "LUÔN LUÔN trả lời bằng đúng 1 object JSON, không thêm chữ nào ngoài "
    "JSON đó, theo đúng khuôn dạng:\n"
    '{"tra_loi": "<câu trả lời tự nhiên bằng tiếng Việt>", '
    '"duong_dan": "<đường dẫn gợi ý bấm vào nếu phù hợp, hoặc null>", '
    '"nhan_nut": "<nhãn ngắn cho nút bấm đó, hoặc null>", '
    '"media": "<đường dẫn ảnh/video/ghi âm để hiện kèm câu trả lời, hoặc null>"}\n\n'
    "CHỈ được dùng đúng các đường dẫn sau cho duong_dan, không bịa đường "
    "dẫn khác:\n"
    "- \"/\" — trang Hôm nay (Dashboard)\n"
    "- \"/viec\" — danh sách công việc\n"
    "- \"/viec?trang_thai=cho_duyet\" — các việc đang chờ duyệt đối chứng\n"
    "- \"/viec?trang_thai=dang_mo\" — các việc đang mở\n"
    "- \"/viec/moi\" — giao việc mới (chỉ gợi ý nếu người hỏi là quản lý/sếp/admin)\n"
    "- \"/cham-cong\" — trang chấm công cá nhân\n"
    "- \"/cham-cong/xin-nghi\" — trang xin nghỉ phép\n"
    "- \"/cham-cong/bang-cong\" — bảng công cả công ty (chỉ quản lý/sếp/admin)\n"
    "- \"/kpi\" — trang KPI\n"
    "Nếu câu hỏi không cần gợi ý bấm đi đâu (VD: chỉ hỏi thông tin chung, "
    "chào hỏi), để duong_dan và nhan_nut là null.\n\n"
    "Về media (ảnh/video/ghi âm): dữ liệu bên dưới có thể kèm theo các đoạn "
    "dạng [media:đường-dẫn] ngay sau 1 việc hoặc 1 chức vụ có đối chứng/ảnh "
    "minh hoạ. Nếu người hỏi muốn XEM/HIỆN 1 ảnh/video/ghi âm cụ thể và có "
    "đúng 1 đoạn [media:...] liên quan trong dữ liệu, hãy COPY Y NGUYÊN "
    "chuỗi đường dẫn đó (không kèm chữ \"media:\" hay dấu ngoặc) vào trường "
    "media. TUYỆT ĐỐI không tự bịa đường dẫn media — nếu không có đoạn "
    "[media:...] nào phù hợp, để media là null."
)


def _dong_media_cho_viec(viec: "CongViec") -> str:
    """Liệt kê đối chứng ảnh/video/ghi âm mới nhất của 1 việc, kèm đường
    dẫn thật trong ngoặc [media:...] — AI chỉ được copy y nguyên chuỗi này
    khi trả lời, không được tự bịa đường dẫn khác."""
    from models import DinhKem, LoaiDinhKem
    tep = (DinhKem.query.filter_by(cong_viec_id=viec.id)
          .filter(DinhKem.loai.in_((LoaiDinhKem.ANH, LoaiDinhKem.VIDEO, LoaiDinhKem.GHI_AM)))
          .order_by(DinhKem.tao_luc.desc()).limit(3).all())
    if not tep:
        return ""
    return " | Đối chứng: " + "; ".join(
        f"{LoaiDinhKem.NHAN[d.loai]} [media:{d.duong_dan}]" for d in tep)


def _ngu_canh_toan_doi(nd: NguoiDung) -> str:
    """Với Sếp/Admin: TOÀN BỘ nhân viên công ty. Với Quản lý: nhân viên
    trong bộ phận mình. Liệt kê chấm công hôm nay + việc hạn hôm nay/ngày
    mai của TỪNG người, để trợ lý tra cứu được theo tên bất kỳ ai trong
    phạm vi (không chỉ của riêng người đang hỏi)."""
    from models import ChamCong, CongViec, TrangThai

    q = NguoiDung.query.filter_by(dang_hoat_dong=True).filter(
        NguoiDung.vai_tro.notin_((VaiTro.ADMIN, VaiTro.SEP)))
    if nd.vai_tro == VaiTro.QUAN_LY and nd.bo_phan_id:
        q = q.filter(NguoiDung.bo_phan_id == nd.bo_phan_id)
    nhan_su = q.order_by(NguoiDung.ho_ten).all()
    if not nhan_su:
        return ""

    hom_nay = ngay_vn_hien_tai()
    ngay_mai = hom_nay + timedelta(days=1)
    dong = []
    for nv in nhan_su:
        cc = ChamCong.query.filter_by(nguoi_dung_id=nv.id, ngay=hom_nay).first()
        if cc and cc.gio_vao:
            tt = f"đã chấm vào {cc.gio_vao:%H:%M}"
            tt += f", ra {cc.gio_ra:%H:%M}" if cc.gio_ra else ", chưa chấm ra"
            if cc.di_tre:
                tt += f" (trễ {cc.so_phut_tre}p)"
            if cc.ve_som:
                tt += f" (sớm {cc.so_phut_som}p)"
        elif cc and cc.nghi_khong_phep:
            tt = "nghỉ không phép hôm nay"
        else:
            tt = "chưa chấm công"

        viec_hn = CongViec.query.filter(
            CongViec.nguoi_nhan_id == nv.id, CongViec.trang_thai.in_(TrangThai.DANG_MO),
            CongViec.han >= datetime.combine(hom_nay, datetime.min.time()),
            CongViec.han <= datetime.combine(hom_nay, datetime.max.time()),
        ).all()
        viec_nm = CongViec.query.filter(
            CongViec.nguoi_nhan_id == nv.id, CongViec.trang_thai.in_(TrangThai.DANG_MO),
            CongViec.han >= datetime.combine(ngay_mai, datetime.min.time()),
            CongViec.han <= datetime.combine(ngay_mai, datetime.max.time()),
        ).all()

        dong.append(f"* {nv.ho_ten} ({nv.ma_dinh_danh}) — chấm công hôm nay: {tt}.")
        if viec_hn:
            dong.append("  Việc hạn hôm nay: " + "; ".join(
                f"[{v.ma}] {v.tieu_de} ({v.ten_trang_thai}){_dong_media_cho_viec(v)}"
                for v in viec_hn))
        if viec_nm:
            dong.append("  Việc hạn ngày mai: " + "; ".join(
                f"[{v.ma}] {v.tieu_de} ({v.ten_trang_thai}){_dong_media_cho_viec(v)}"
                for v in viec_nm))

    return "\n".join(dong)


def _kiem_tra_media_hop_le(nd: NguoiDung, duong_dan: str) -> str | None:
    """Chỉ cho qua nếu đường dẫn AI trả về khớp ĐÚNG 1 đối chứng mà nd có
    quyền xem việc đó, hoặc khớp ảnh minh hoạ 1 Chức vụ (ai xem cũng được).
    Không bao giờ tin thẳng đường dẫn do AI đưa ra — luôn xác minh lại ở
    đây trước khi trả về cho trình duyệt."""
    from models import ChucVu, DinhKem
    dk = DinhKem.query.filter_by(duong_dan=duong_dan).first()
    if dk:
        return duong_dan if nd.duoc_xem_viec(dk.cong_viec) else None
    cv = ChucVu.query.filter_by(anh=duong_dan).first()
    if cv:
        return duong_dan
    return None


def _boi_canh_tro_ly(nd: NguoiDung) -> str:
    """Dựng đoạn ngữ cảnh dữ liệu thật của người đang hỏi, nhét vào system
    prompt để trợ lý AI trả lời đúng, không bịa.

    Có 2 lớp thông tin tổ chức nạp thêm ngoài dữ liệu việc/chấm công:
    - Thông tin chung công ty (chế độ, chính sách...) — áp dụng cho mọi
      người, quản lý ở trang Info AI.
    - Thông tin riêng theo Chức vụ của đúng người đang hỏi (mô tả công
      việc, lương, chế độ riêng vị trí) — CHỈ nạp đúng 1 chức vụ của họ,
      không nạp chức vụ khác, nên AI không có gì để lẫn lộn giữa các
      chức vụ dù có bị hỏi khéo. Riêng Admin/Sếp được nạp TOÀN BỘ chức vụ
      (vì họ vốn đã quản lý phần này ở Info AI).

    Với Quản lý/Sếp/Admin, còn nạp thêm dữ liệu chấm công + việc hôm nay/
    ngày mai của TỪNG nhân viên trong phạm vi (bộ phận với Quản lý, toàn
    công ty với Sếp/Admin) — để tra cứu được theo tên bất kỳ ai, không chỉ
    của riêng người đang hỏi.
    """
    from models import BoPhan, ChamCong, CongViec, TrangThai, XinNghi

    hom_nay = ngay_vn_hien_tai()
    ngay_mai = hom_nay + timedelta(days=1)
    dong = [f"Hôm nay là {hom_nay:%d/%m/%Y}. Người đang hỏi: {nd.ho_ten} ({nd.ten_vai_tro})."]

    thong_tin_chung = lay_cai_dat("thong_tin_chung_cong_ty")
    if thong_tin_chung:
        dong.append("--- Thông tin chung công ty (áp dụng cho mọi người) ---\n"
                    + thong_tin_chung[:4000])

    if nd.la_admin_sep:
        from models import ChucVu
        tat_ca_cv = ChucVu.query.order_by(ChucVu.ten).all()
        if tat_ca_cv:
            dong.append("--- Toàn bộ chức vụ trong hệ thống (Admin/Sếp được xem hết, "
                        "không giới hạn 1 chức vụ như nhân viên thường) ---\n" +
                        "\n".join(
                            f"{cv.ten}: {(cv.mo_ta or '(chưa có mô tả)').strip()[:500]}"
                            + (f" [media:{cv.anh}]" if cv.anh else "")
                            for cv in tat_ca_cv))
    elif nd.chuc_vu:
        dong.append(
            f"--- Thông tin riêng cho chức vụ \"{nd.chuc_vu.ten}\" của người đang hỏi "
            f"(CHỈ dùng đúng phần này cho câu hỏi về vị trí công việc của họ, không có "
            f"dữ liệu chức vụ khác nên đừng suy đoán) ---\n"
            + ((nd.chuc_vu.mo_ta or "").strip()[:3000] or "(chưa có mô tả cho chức vụ này)")
            + (f" [media:{nd.chuc_vu.anh}]" if nd.chuc_vu.anh else "")
        )
    else:
        dong.append("Người này chưa được gán chức vụ cụ thể — không có thông tin riêng "
                    "theo vị trí công việc, nếu họ hỏi thì nói rõ là chưa có dữ liệu.")

    if not nd.la_admin_sep:
        viec_hom_nay = CongViec.query.filter(
            CongViec.nguoi_nhan_id == nd.id,
            CongViec.trang_thai.in_(TrangThai.DANG_MO),
            CongViec.han >= datetime.combine(hom_nay, datetime.min.time()),
            CongViec.han <= datetime.combine(hom_nay, datetime.max.time()),
        ).all()
        viec_ngay_mai = CongViec.query.filter(
            CongViec.nguoi_nhan_id == nd.id,
            CongViec.trang_thai.in_(TrangThai.DANG_MO),
            CongViec.han >= datetime.combine(ngay_mai, datetime.min.time()),
            CongViec.han <= datetime.combine(ngay_mai, datetime.max.time()),
        ).all()

        if viec_hom_nay:
            dong.append("Việc có hạn HÔM NAY (của chính người đang hỏi): " + "; ".join(
                f"[{v.ma}] {v.tieu_de} (hạn {v.han:%H:%M}, mức {v.ten_uu_tien})"
                f"{_dong_media_cho_viec(v)}" for v in viec_hom_nay))
        else:
            dong.append("Hôm nay không có việc nào tới hạn.")

        if viec_ngay_mai:
            dong.append("Việc có hạn NGÀY MAI (của chính người đang hỏi): " + "; ".join(
                f"[{v.ma}] {v.tieu_de} (hạn {v.han:%H:%M}, mức {v.ten_uu_tien})"
                f"{_dong_media_cho_viec(v)}" for v in viec_ngay_mai))
        else:
            dong.append("Ngày mai chưa có việc nào tới hạn (theo dữ liệu hiện có).")

        cc = ChamCong.query.filter_by(nguoi_dung_id=nd.id, ngay=hom_nay).first()
        if cc and cc.gio_vao:
            trang_thai_cc = f"đã chấm vào lúc {cc.gio_vao:%H:%M}"
            trang_thai_cc += f", đã chấm ra lúc {cc.gio_ra:%H:%M}" if cc.gio_ra else ", chưa chấm ra"
            if cc.di_tre:
                trang_thai_cc += f" (đi trễ {cc.so_phut_tre} phút)"
            if cc.ve_som:
                trang_thai_cc += f" (về sớm {cc.so_phut_som} phút)"
        elif cc and cc.nghi_khong_phep:
            trang_thai_cc = "nghỉ không phép hôm nay"
        else:
            trang_thai_cc = "chưa chấm công vào hôm nay"
        dong.append(f"Chấm công hôm nay (của chính người đang hỏi): {trang_thai_cc}.")

        nghi_sap_toi = (XinNghi.query.filter(
            XinNghi.nguoi_dung_id == nd.id, XinNghi.ngay >= hom_nay)
            .order_by(XinNghi.ngay).limit(5).all())
        if nghi_sap_toi:
            dong.append("Nghỉ phép sắp tới đã đăng ký: " + "; ".join(
                f"{x.ngay:%d/%m} ({x.ten_buoi})" for x in nghi_sap_toi))
        else:
            dong.append("Chưa đăng ký nghỉ phép nào sắp tới.")

        if nd.la_quan_ly and nd.bo_phan_id:
            tong_bo_phan = NguoiDung.query.filter_by(
                bo_phan_id=nd.bo_phan_id, dang_hoat_dong=True).count()
            dong.append(f"Bộ phận mình quản lý hiện có {tong_bo_phan} nhân viên đang hoạt động.")
            bang_kpi = _bang_kpi_thang_nay_cho_ngu_canh(bo_phan_id=nd.bo_phan_id)
            if bang_kpi:
                dong.append("--- KPI tháng này của nhân viên trong bộ phận mình quản lý "
                            "(dùng để trả lời khi được hỏi đánh giá/kết quả làm việc của "
                            "1 nhân viên cụ thể) ---\n" + bang_kpi)
            ngu_canh_doi = _ngu_canh_toan_doi(nd)
            if ngu_canh_doi:
                dong.append("--- Chấm công hôm nay + việc hạn hôm nay/ngày mai của TỪNG "
                            "nhân viên trong bộ phận mình quản lý (dùng để trả lời khi "
                            "được hỏi về 1 người cụ thể theo tên, không chỉ về chính "
                            "người đang hỏi) ---\n" + ngu_canh_doi)
    else:
        cho_duyet = CongViec.query.filter_by(trang_thai=TrangThai.CHO_DUYET).count()
        qua_han = CongViec.query.filter(
            CongViec.han < gio_vn_hien_tai(), CongViec.trang_thai.in_(TrangThai.CHUA_XONG)).count()
        tong_nhan_vien = NguoiDung.query.filter_by(dang_hoat_dong=True).count()
        theo_bo_phan = (
            db.session.query(BoPhan.ten, db.func.count(NguoiDung.id))
            .join(NguoiDung, NguoiDung.bo_phan_id == BoPhan.id)
            .filter(NguoiDung.dang_hoat_dong.is_(True))
            .group_by(BoPhan.ten).all()
        )
        dong.append(
            f"Số liệu công ty hiện tại: {tong_nhan_vien} nhân viên đang hoạt động "
            f"(gồm mọi vai trò), {cho_duyet} việc đang chờ duyệt đối chứng, "
            f"{qua_han} việc đang quá hạn chưa nộp."
            + (" Theo bộ phận: " + "; ".join(f"{ten}: {sl}" for ten, sl in theo_bo_phan)
               if theo_bo_phan else "")
        )
        dong.append(
            "QUAN TRỌNG: Vai trò Sếp/Quản trị KHÔNG tự chấm công, không tự xin "
            "nghỉ, và KHÔNG BAO GIỜ tự NHẬN việc nào trong hệ thống này (không "
            "ai giao việc được cho Sếp/Admin). Nếu người này hỏi về việc/chấm "
            "công của CHÍNH HỌ (VD: \"tôi có việc gì hôm nay\", \"tôi đã chấm "
            "công chưa\"), PHẢI trả lời rằng vai trò Sếp/Quản trị không có "
            "việc/chấm công riêng — TUYỆT ĐỐI không được lấy việc hay chấm "
            "công của bất kỳ nhân viên nào khác (trong danh sách bên dưới) rồi "
            "gán nhầm thành của người đang hỏi.\n"
            "NGƯỢC LẠI: Sếp/Quản trị VẪN CÓ ĐẦY ĐỦ QUYỀN GIAO việc cho người "
            "khác qua trang Giao việc mới — đây KHÔNG phải là hạn chế của họ. "
            "Nếu người này nói muốn giao việc, trả lời bình thường và gợi ý "
            "đường dẫn /viec/moi, đừng nói rằng vai trò của họ không giao "
            "việc được — chỉ có NHẬN việc mới bị cấm, GIAO việc thì không."
        )

        bang_kpi = _bang_kpi_thang_nay_cho_ngu_canh()
        if bang_kpi:
            dong.append("--- KPI tháng này của TẤT CẢ nhân viên toàn công ty (dùng để trả "
                        "lời khi được hỏi đánh giá/kết quả làm việc của 1 nhân viên cụ thể) "
                        "---\n" + bang_kpi)

        ngu_canh_doi = _ngu_canh_toan_doi(nd)
        if ngu_canh_doi:
            dong.append("--- Chấm công hôm nay + việc hạn hôm nay/ngày mai của TỪNG nhân "
                        "viên TOÀN CÔNG TY (dùng để trả lời khi được hỏi về 1 người cụ "
                        "thể theo tên) ---\n" + ngu_canh_doi)

    return "\n\n".join(dong)


def _bang_kpi_thang_nay_cho_ngu_canh(bo_phan_id: int | None = None) -> str:
    """Tóm tắt KPI từ đầu tháng tới hôm nay, dạng text gọn để nhét vào ngữ
    cảnh trợ lý AI — lọc theo bộ phận nếu có (dùng cho quản lý)."""
    hom_nay = ngay_vn_hien_tai()
    bang = tinh_kpi(hom_nay.replace(day=1), hom_nay)
    if bo_phan_id:
        ids = {n.id for n in NguoiDung.query.filter_by(
            bo_phan_id=bo_phan_id, dang_hoat_dong=True).all()}
        bang = [b for b in bang if b["nguoi_dung_id"] in ids]
    if not bang:
        return ""
    dong = []
    for b in bang:
        sao = f"{b['sao_tb']:.1f}★ ({b['xep_loai']})" if b["sao_tb"] is not None else "chưa có đánh giá nào"
        dong.append(
            f"{b['ho_ten']}: {sao} — đã đánh giá {b['da_danh_gia']}, "
            f"chờ duyệt {b['cho_duyet']}, đang làm {b['dang_lam']}"
        )
    return "\n".join(dong)


def tro_ly_tra_loi(nd: NguoiDung, tin_nhan: str, lich_su: list[dict]) -> tuple[dict | None, str | None]:
    """Trợ lý AI hỏi-đáp — trả lời dựa trên dữ liệu thật của đúng người
    đang hỏi (lấy theo nd, không lấy theo dữ liệu client gửi lên) + hướng
    dẫn sử dụng hệ thống. lich_su là vài lượt hỏi-đáp gần nhất do trình
    duyệt gửi lên để giữ mạch hội thoại, chỉ dùng tối đa 8 lượt gần nhất.

    Trả về (dict {tra_loi, duong_dan, nhan_nut, media}, lỗi) — các trường
    phụ có thể None nếu câu hỏi không cần gợi ý đi đâu / không có media.
    """
    boi_canh = _boi_canh_tro_ly(nd)
    messages = [{"role": "system",
                "content": _HUONG_DAN_HE_THONG_TRO_LY + "\n\nDữ liệu hiện tại:\n" + boi_canh}]
    for m in lich_su[-8:]:
        if isinstance(m, dict) and m.get("vai_tro") in ("user", "assistant") and m.get("noi_dung"):
            messages.append({"role": m["vai_tro"], "content": str(m["noi_dung"])[:2000]})
    messages.append({"role": "user", "content": tin_nhan[:2000]})

    noi_dung, loi = _goi_chatgpt_tin_nhan(messages, dang_json=True)
    if loi:
        return None, loi

    van_ban = (noi_dung or "").strip()
    if van_ban.startswith("```"):
        van_ban = van_ban.strip("`").removeprefix("json").strip()
    try:
        ket_qua = json.loads(van_ban)
    except (json.JSONDecodeError, TypeError):
        # OpenAI lỡ không trả đúng JSON -> vẫn hiện được câu trả lời thô,
        # chỉ là không có nút bấm gợi ý.
        return {"tra_loi": noi_dung, "duong_dan": None, "nhan_nut": None, "media": None}, None

    return {
        "tra_loi": ket_qua.get("tra_loi") or noi_dung,
        "duong_dan": ket_qua.get("duong_dan") or None,
        "nhan_nut": ket_qua.get("nhan_nut") or None,
        "media": _duong_dan_media_da_xac_minh(nd, ket_qua.get("media")),
    }, None


def _duong_dan_media_da_xac_minh(nd: NguoiDung, duong_dan: str | None) -> str | None:
    """Xác minh đường dẫn media AI trả về là thật + nd có quyền xem, rồi
    đổi thành URL /media/... cho trình duyệt. Không hợp lệ -> trả None,
    im lặng bỏ qua (không hiện ảnh) thay vì tin liều AI."""
    if not duong_dan:
        return None
    hop_le = _kiem_tra_media_hop_le(nd, duong_dan)
    if not hop_le:
        return None
    return url_for("media", duong_dan=hop_le)