"""Cấu trúc menu chính, chia 2 khối rõ ràng:

- CÔNG VIỆC: những gì liên quan đến công việc (việc, KPI, đề xuất công
  việc, thư viện, đóng gói, công nợ).
- HÀNH CHÍNH NHÂN SỰ: những gì liên quan đến tiền lương (chấm công, đơn
  từ, tạm ứng, biên bản phạt, bảng công, giấy tờ).

Dùng chung cho sidebar trên máy tính (base.html) và các trang "hub" mở từ
thanh menu dưới cùng trên điện thoại."""
from flask import Blueprint, render_template, request, url_for
from flask_login import current_user, login_required

from icons import bi

bp = Blueprint("menu", __name__, url_prefix="/muc")


def _muc(endpoint, nhan, icon, mo_ta="", dem=0, kw=None, khop=None):
    kw = kw or {}
    return {"url": url_for(endpoint, **kw), "endpoint": endpoint, "kw": kw, "nhan": nhan,
            "icon": icon, "mo_ta": mo_ta, "dem": dem, "khop": khop}


def _dang_mo(m) -> bool:
    if m["khop"]:
        return m["khop"]()
    return request.endpoint == m["endpoint"]


def _loai_de_xuat_dang_xem() -> str | None:
    if request.blueprint != "de_xuat":
        return None
    loai = request.args.get("loai") or (request.view_args or {}).get("loai")
    if not loai and request.view_args and request.view_args.get("id"):
        from extensions import db
        from models import DeXuat
        dx = db.session.get(DeXuat, request.view_args["id"])
        loai = dx.loai if dx else None
    return loai


def _so_viec_can_lam() -> int:
    """Việc giao cho tôi, chưa xong (mới giao / đang làm / phải làm lại)."""
    from models import CongViec, TrangThai
    return CongViec.query.filter(CongViec.nguoi_nhan_id == current_user.id,
                                 CongViec.trang_thai.in_(TrangThai.CHUA_XONG)).count()


def _so_viec_doi_duyet() -> int:
    from models import CongViec, TrangThai
    return CongViec.query.filter_by(nguoi_nhan_id=current_user.id, trang_thai=TrangThai.CHO_DUYET).count()


def _so_viec_can_duyet() -> int:
    """Việc nhân viên đã gửi đối chứng, đang chờ tôi duyệt."""
    from models import CongViec, TrangThai
    from views.tasks import _viec_lien_quan
    return sum(1 for v in _viec_lien_quan().filter(CongViec.trang_thai == TrangThai.CHO_DUYET)
               if current_user.duoc_duyet_viec(v))


def _xem_viec(ma):
    """Mục menu việc nào đang mở: theo ?xem= trên trang danh sách việc."""
    return lambda: request.endpoint == "tasks.danh_sach" and request.args.get("xem", "") == ma


def menu_chinh() -> list[dict]:
    """[{ma, nhan, muc: [...]}] theo quyền của người đang đăng nhập."""
    if not current_user.is_authenticated:
        return []
    from models import BienBanPhat, TrangThaiBienBan
    from views.cong_no import duoc_vao as duoc_vao_cong_no
    from models import LoaiDeXuat
    from views.de_xuat import _de_xuat_can_duyet
    from views.don_tu import so_don_cho_duyet

    dx_cho = _de_xuat_can_duyet()  # [] với nhân viên thường
    dx_dem = {loai: sum(1 for d in dx_cho if d.loai == loai) for loai in (LoaiDeXuat.CONG_VIEC, LoaiDeXuat.TAM_UNG)}

    cong_viec = []
    if not current_user.la_admin_sep:
        cong_viec.append(_muc("tasks.danh_sach", "Việc cần làm", bi("clipboard-list"),
                              "Việc được giao, chưa hoàn thành", dem=_so_viec_can_lam(),
                              kw={"xem": "can_lam"}, khop=_xem_viec("can_lam")))
    if current_user.la_quan_ly:
        cong_viec.append(_muc("tasks.danh_sach", "Việc cần duyệt", bi("check-circle"),
                              "Nhân viên đã nộp, chờ bạn đánh giá", dem=_so_viec_can_duyet(),
                              kw={"xem": "can_duyet"}, khop=_xem_viec("can_duyet")))
    if not current_user.la_admin_sep:
        n = _so_viec_doi_duyet()
        cong_viec.append(_muc("tasks.danh_sach", "Việc đợi duyệt", bi("hourglass"),
                              f"{n} việc đã nộp, chờ quản lý duyệt" if n else "Việc đã nộp, chờ quản lý duyệt",
                              kw={"xem": "doi_duyet"}, khop=_xem_viec("doi_duyet")))
    if current_user.la_quan_ly:
        cong_viec.append(_muc("tasks.danh_sach", "Tất cả công việc", bi("briefcase"), "Giao việc, theo dõi cả bộ phận",
                              khop=lambda: request.endpoint in ("tasks.chi_tiet", "tasks.giao_viec")
                              or (request.endpoint == "tasks.danh_sach" and not request.args.get("xem"))))
    cong_viec += [
        _muc("tasks.kpi", "KPI", bi("target"), "Điểm sao, xếp loại"),
        _muc("de_xuat.danh_sach", "Đề xuất công việc", bi("lightbulb"), "Mua sắm, đề xuất cải tiến",
             dem=dx_dem[LoaiDeXuat.CONG_VIEC], kw={"loai": "cong_viec"}, khop=lambda: _loai_de_xuat_dang_xem() == "cong_viec"),
        _muc("tasks.thu_vien", "Thư viện", bi("images"), "Ảnh, tệp đối chứng"),
        _muc("dong_goi.moi", "Đóng gói", bi("package"), "Chụp ảnh gói hàng",
             khop=lambda: request.blueprint == "dong_goi"),
    ]
    if duoc_vao_cong_no(current_user):
        cong_viec.append(_muc("cong_no.danh_sach", "Công nợ", bi("credit-card"), "Thanh toán công nợ",
                              khop=lambda: request.blueprint == "cong_no"))

    nhan_su = [
        _muc("attendance.trang_cham_cong", "Chấm công", bi("clock"), "Vào/ra ca, lịch công tháng",
             khop=lambda: request.endpoint in ("attendance.trang_cham_cong", "attendance.xin_nghi")),
        _muc("don_tu.danh_sach", "Đơn từ", bi("file-text"), "Xin nghỉ, đi trễ, bổ sung công…",
             dem=so_don_cho_duyet(), khop=lambda: request.blueprint == "don_tu"),
        _muc("de_xuat.danh_sach", "Tạm ứng", bi("wallet"), "Đề xuất tạm ứng lương",
             dem=dx_dem[LoaiDeXuat.TAM_UNG], kw={"loai": "tam_ung"}, khop=lambda: _loai_de_xuat_dang_xem() == "tam_ung"),
        _muc("bien_ban.danh_sach", "Biên bản phạt", bi("alert-triangle"), "Vi phạm, phạt",
             dem=BienBanPhat.query.filter_by(nguoi_vi_pham_id=current_user.id,
                                             trang_thai=TrangThaiBienBan.CHO_KY).count(),
             khop=lambda: request.blueprint == "bien_ban"),
    ]
    if current_user.la_quan_ly:
        nhan_su.append(_muc("attendance.bang_cong", "Bảng công", bi("bar-chart"), "Công cả bộ phận, xuất Excel",
                            khop=lambda: request.endpoint in ("attendance.bang_cong", "attendance.sua_cham_cong")))
    if current_user.la_admin_sep:
        nhan_su.append(_muc("luong.bang_luong", "Bảng lương", bi("credit-card"), "Tính lương tự động, phiếu lương",
                            khop=lambda: request.blueprint == "luong"))
    if current_user.duoc_quan_ly_giay_to:
        nhan_su.append(_muc("admin.giay_to", "Giấy tờ nhân sự", bi("folder"), "Hợp đồng, cam kết",
                            khop=lambda: request.endpoint in ("admin.giay_to", "admin.giay_to_nhan_vien")))

    nhom = [
        {"ma": "cong_viec", "nhan": "Công việc", "muc": cong_viec},
        {"ma": "nhan_su", "nhan": "Hành chính nhân sự", "muc": nhan_su},
    ]
    for n in nhom:
        for m in n["muc"]:
            m["active"] = _dang_mo(m)
        n["active"] = any(m["active"] for m in n["muc"])
        n["dem"] = sum(m["dem"] for m in n["muc"])
    return nhom


@bp.route("/cong-viec")
@login_required
def cong_viec():
    return render_template("menu_hub.html", nhom=menu_chinh()[0], tieu_de="Công việc",
                           mo_ta="Việc được giao, KPI, đề xuất công việc")


@bp.route("/nhan-su")
@login_required
def nhan_su():
    return render_template("menu_hub.html", nhom=menu_chinh()[1], tieu_de="Hành chính nhân sự",
                           mo_ta="Chấm công, đơn từ, tạm ứng, biên bản phạt, lương")


@bp.route("/ca-nhan")
@login_required
def ca_nhan():
    return render_template("menu_ca_nhan.html")
