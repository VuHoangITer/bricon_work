"""Cấu trúc menu chính, chia 2 khối rõ ràng:

- CÔNG VIỆC: những gì liên quan đến công việc (việc, KPI, đề xuất công
  việc, thư viện, đóng gói, công nợ).
- HÀNH CHÍNH NHÂN SỰ: những gì liên quan đến tiền lương (chấm công, đơn
  từ, tạm ứng, biên bản phạt, bảng công, giấy tờ).

Dùng chung cho sidebar trên máy tính (base.html) và các trang "hub" mở từ
thanh menu dưới cùng trên điện thoại."""
from flask import Blueprint, redirect, render_template, request, url_for
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


def danh_sach_can_duyet() -> dict:
    """Mọi thứ đang chờ current_user duyệt, gom về 1 chỗ (trang Cần duyệt)."""
    from models import CongViec, TrangThai
    from views.de_xuat import _de_xuat_can_duyet
    from views.don_tu import don_cho_duyet
    from views.nhiem_vu import de_xuat_cho_duyet
    from views.tasks import _viec_lien_quan
    if not current_user.la_quan_ly:
        return {"don": [], "viec": [], "de_xuat": [], "nhan_viec": [], "tong": 0}
    viec = [v for v in _viec_lien_quan().filter(CongViec.trang_thai == TrangThai.CHO_DUYET)
            .order_by(CongViec.gui_doi_chung_luc).all() if current_user.duoc_duyet_viec(v)]
    kq = {"don": don_cho_duyet(), "viec": viec, "de_xuat": _de_xuat_can_duyet(), "nhan_viec": de_xuat_cho_duyet()}
    kq["tong"] = sum(len(x) for x in kq.values())
    return kq


def so_can_duyet() -> int:
    if not current_user.is_authenticated or not current_user.la_quan_ly:
        return 0
    return danh_sach_can_duyet()["tong"]


def menu_chinh() -> list[dict]:
    """[{ma, nhan, muc: [...]}] theo quyền của người đang đăng nhập.
    Gọn theo vai trò: nhân viên chỉ thấy việc của mình; mọi thứ chờ duyệt của
    quản lý gom vào 1 ô "Cần duyệt"; mục ít dùng (thư viện, cấu hình nhiệm
    vụ…) để ở Cá nhân → Công cụ."""
    if not current_user.is_authenticated:
        return []
    from models import BienBanPhat, TrangThaiBienBan
    from views.cong_no import duoc_vao as duoc_vao_cong_no

    la_ql, la_sep = current_user.la_quan_ly, current_user.la_admin_sep

    cong_viec = []
    if not la_sep:
        cong_viec.append(_muc("nhiem_vu.cua_toi", "Công việc của tôi", bi("calendar"),
                              "Lịch việc, mục tiêu tuần, đề xuất nhận việc", dem=_so_viec_can_lam(),
                              khop=lambda: request.endpoint in ("nhiem_vu.cua_toi", "nhiem_vu.de_xuat")
                              and not request.args.get("nguoi")
                              or (request.endpoint == "tasks.danh_sach" and request.args.get("xem") in ("can_lam", "doi_duyet"))))
    if la_ql:
        cong_viec.append(_muc("menu.can_duyet", "Cần duyệt", bi("check-circle"),
                              "Việc, đơn từ, tạm ứng, đề xuất chờ bạn", dem=so_can_duyet(),
                              khop=lambda: request.endpoint == "menu.can_duyet"
                              or (request.endpoint == "tasks.danh_sach" and request.args.get("xem") == "can_duyet")))
        cong_viec.append(_muc("tasks.danh_sach", "Tất cả công việc", bi("briefcase"), "Giao việc, theo dõi cả bộ phận",
                              khop=lambda: request.endpoint in ("tasks.chi_tiet", "tasks.giao_viec")
                              or (request.endpoint == "tasks.danh_sach" and not request.args.get("xem"))
                              or (request.endpoint == "nhiem_vu.cua_toi" and bool(request.args.get("nguoi")))))
    cong_viec += [
        _muc("tasks.kpi", "KPI", bi("target"), "Điểm sao, xếp loại"),
        _muc("de_xuat.danh_sach", "Đề xuất mua sắm", bi("lightbulb"), "Mua sắm, cải tiến",
             kw={"loai": "cong_viec"}, khop=lambda: _loai_de_xuat_dang_xem() == "cong_viec"),
        _muc("dong_goi.moi", "Đóng gói", bi("package"), "Chụp ảnh gói hàng",
             khop=lambda: request.blueprint == "dong_goi"),
    ]
    if duoc_vao_cong_no(current_user):
        cong_viec.append(_muc("cong_no.danh_sach", "Công nợ", bi("credit-card"), "Thanh toán công nợ",
                              khop=lambda: request.blueprint == "cong_no"))

    nhan_su = []
    if not la_sep:
        nhan_su.append(_muc("attendance.trang_cham_cong", "Chấm công", bi("clock"), "Vào/ra ca, lịch công tháng",
                            khop=lambda: request.endpoint in ("attendance.trang_cham_cong", "attendance.xin_nghi")))
    nhan_su.append(_muc("don_tu.danh_sach", "Đơn từ", bi("file-text"), "Nghỉ, đi trễ, bổ sung công, tạm ứng…",
                        khop=lambda: request.blueprint == "don_tu" or _loai_de_xuat_dang_xem() == "tam_ung"))
    if not la_sep:
        nhan_su.append(_muc("luong.cua_toi", "Lương của tôi", bi("credit-card"), "Lương tạm tính đến hôm nay"))
    so_bb = BienBanPhat.query.filter_by(nguoi_vi_pham_id=current_user.id, trang_thai=TrangThaiBienBan.CHO_KY).count()
    nhan_su.append(_muc("bien_ban.danh_sach", "Biên bản", bi("alert-triangle"), "Vi phạm, phạt", dem=so_bb,
                        khop=lambda: request.blueprint == "bien_ban"))
    if la_ql:
        nhan_su.append(_muc("attendance.bang_cong", "Bảng công", bi("bar-chart"), "Công cả bộ phận, xuất Excel",
                            khop=lambda: request.endpoint in ("attendance.bang_cong", "attendance.sua_cham_cong")
                            or request.endpoint == "attendance.cham_cong_nhan_vien"))
    if la_sep:
        nhan_su.append(_muc("luong.bang_luong", "Lương & thưởng", bi("wallet"), "Bảng lương, khen thưởng, mẫu lương",
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


@bp.route("/can-duyet")
@login_required
def can_duyet():
    if not current_user.la_quan_ly:
        return redirect(url_for("nhiem_vu.cua_toi"))
    from models import LoaiDeXuat
    ds = danh_sach_can_duyet()
    # Đề xuất có 2 loại: đề xuất công việc (mua sắm, cải tiến) và tạm ứng —
    # tách 2 tab riêng; nhân viên xin nhận việc cũng là đề xuất công việc.
    ds["de_xuat_cv"] = [d for d in ds["de_xuat"] if d.loai == LoaiDeXuat.CONG_VIEC]
    ds["tam_ung"] = [d for d in ds["de_xuat"] if d.loai == LoaiDeXuat.TAM_UNG]
    dem = {"don": len(ds["don"]), "viec": len(ds["viec"]),
           "de_xuat": len(ds["de_xuat_cv"]) + len(ds["nhan_viec"]), "tam_ung": len(ds["tam_ung"])}
    tab = request.args.get("tab")
    if tab not in dem:
        # Mặc định mở tab đầu tiên đang có mục chờ
        tab = next((k for k in ("don", "viec", "de_xuat", "tam_ung") if dem[k]), "don")
    return render_template("can_duyet.html", ds=ds, dem=dem, tab=tab)


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
