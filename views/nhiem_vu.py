"""Công việc theo yêu cầu của anh Long:

- Công việc của tôi: lịch tháng (chấm màu theo việc), mục tiêu tuần, việc
  theo ngày chia 3 nguồn (Hằng ngày / Quản lý giao / Đề xuất nhận), điểm tuần.
- Đề xuất nhận việc: nhân viên đề xuất, quản lý duyệt mới thành việc.
- Cấu hình nhiệm vụ: Vị trí (chức vụ) → Giai đoạn → Bộ nhiệm vụ hằng ngày,
  tiêu chí đánh giá, giai đoạn + mảng phụ trách của từng nhân viên, nhập
  file dự thảo 48 mẫu.

Việc hằng ngày vẫn do cron `tao-viec-dinh-ky` tạo mỗi sáng (services)."""
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for  # noqa: F401
from flask_login import current_user, login_required

import services
from extensions import db
from models import (ChucVu, CongViec, DeXuatNhanViec, GiaiDoan, MucTieuTuan, NguoiDung, NguonViec,
                    TieuChiDanhGia, TrangThai, TrangThaiDeXuatNhan, VaiTro, ViecDinhKy, gio_vn_hien_tai,
                    ngay_vn_hien_tai)

bp = Blueprint("nhiem_vu", __name__, url_prefix="/cong-viec")

TEN_THU = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


# ---------------------------------------------------------------------------
# Quyền
# ---------------------------------------------------------------------------
def _duoc_quan_ly(nd: NguoiDung) -> bool:
    """current_user quản lý được nd (xem việc, đặt mục tiêu, duyệt đề xuất)."""
    if nd.id == current_user.id:
        return False
    if current_user.la_admin_sep:
        return True
    return current_user.vai_tro == VaiTro.QUAN_LY and current_user.cung_bo_phan(nd)


def _duoc_xem(nd: NguoiDung) -> bool:
    return nd.id == current_user.id or _duoc_quan_ly(nd)


def _chi_admin_sep():
    if not current_user.la_admin_sep:
        abort(403)


# ---------------------------------------------------------------------------
# Tiện ích ngày / tuần
# ---------------------------------------------------------------------------
def _dau_tuan(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _doc_ngay(s: str | None, mac_dinh: date) -> date:
    try:
        return date.fromisoformat(s) if s else mac_dinh
    except ValueError:
        return mac_dinh


def _tien_do_muc_tieu(mt: MucTieuTuan) -> int:
    """Số đạt: mục tiêu gắn mẫu nhiệm vụ -> đếm việc của mẫu đó đã được
    duyệt hoàn thành trong tuần; không gắn -> số cập nhật tay."""
    if not mt.viec_dinh_ky_id:
        return mt.da_dat or 0
    dau = datetime.combine(mt.tuan, datetime.min.time())
    return CongViec.query.filter(CongViec.nguoi_nhan_id == mt.nguoi_dung_id,
                                 CongViec.viec_dinh_ky_id == mt.viec_dinh_ky_id,
                                 CongViec.trang_thai == TrangThai.HOAN_THANH,
                                 CongViec.han >= dau, CongViec.han < dau + timedelta(days=7)).count()


def tieu_chi_cua(nd: NguoiDung) -> tuple[int, int, int]:
    if nd.chuc_vu_id:
        tc = TieuChiDanhGia.query.filter_by(chuc_vu_id=nd.chuc_vu_id, giai_doan=nd.giai_doan).first()
        if tc:
            return tc.khoi_luong, tc.chat_luong, tc.dung_han
    return TieuChiDanhGia.MAC_DINH


def diem_tuan(nd: NguoiDung, tuan: date) -> dict | None:
    """Điểm tuần theo tiêu chí vị trí + giai đoạn. Chỉ tính kết quả đã được
    xác nhận: khối lượng = việc được duyệt hoàn thành / việc có hạn trong
    tuần; chất lượng = sao trung bình / 5; đúng hạn = việc nộp trước hạn /
    việc đã hoàn thành."""
    dau = datetime.combine(tuan, datetime.min.time())
    ds = CongViec.query.filter(CongViec.nguoi_nhan_id == nd.id, CongViec.trang_thai != TrangThai.HUY,
                               CongViec.han >= dau, CongViec.han < dau + timedelta(days=7)).all()
    if not ds:
        return None
    xong = [v for v in ds if v.trang_thai == TrangThai.HOAN_THANH]
    co_sao = [v.so_sao_cuoi for v in xong if v.so_sao_cuoi is not None]
    dung_han = [v for v in xong if (v.gui_doi_chung_luc or v.hoan_thanh_luc) and v.han
                and (v.gui_doi_chung_luc or v.hoan_thanh_luc) <= v.han]
    kl = len(xong) / len(ds) * 100
    cl = (sum(co_sao) / len(co_sao) / 5 * 100) if co_sao else 0
    dh = (len(dung_han) / len(xong) * 100) if xong else 0
    w = tieu_chi_cua(nd)
    return {"tong": len(ds), "xong": len(xong), "khoi_luong": round(kl), "chat_luong": round(cl),
            "dung_han": round(dh), "trong_so": w,
            "diem": round((kl * w[0] + cl * w[1] + dh * w[2]) / max(sum(w), 1))}


# ---------------------------------------------------------------------------
# Công việc của tôi (hoặc của 1 nhân viên — quản lý xem)
# ---------------------------------------------------------------------------
@bp.route("/")
@login_required
def cua_toi():
    hn = ngay_vn_hien_tai()
    nguoi_id = request.args.get("nguoi", type=int)
    nd = db.session.get(NguoiDung, nguoi_id) if nguoi_id else current_user
    if not nd:
        abort(404)
    if not _duoc_xem(nd):
        abort(403)

    # Tháng của lịch
    try:
        nam, thg = (int(x) for x in (request.args.get("thang") or hn.strftime("%Y-%m")).split("-"))
        dau = date(nam, thg, 1)
    except ValueError:
        dau = hn.replace(day=1)
    cuoi = (dau + timedelta(days=32)).replace(day=1)
    ngay_chon = _doc_ngay(request.args.get("ngay"), hn if dau <= hn < cuoi else dau)

    viec_thang = (CongViec.query.filter(CongViec.nguoi_nhan_id == nd.id, CongViec.trang_thai != TrangThai.HUY,
                                        CongViec.han >= datetime.combine(dau, datetime.min.time()),
                                        CongViec.han < datetime.combine(cuoi, datetime.min.time()))
                  .order_by(CongViec.han).all())
    theo_ngay: dict[date, list] = {}
    for v in viec_thang:
        theo_ngay.setdefault(v.han.date(), []).append(v)

    o_ngay = []
    d = dau
    while d < cuoi:
        ds = theo_ngay.get(d, [])
        cham = []
        if any(v.qua_han for v in ds):
            cham.append("qua_han")
        if any(v.trang_thai in TrangThai.DANG_MO and not v.qua_han for v in ds):
            cham.append("co_viec")
        if ds and all(v.trang_thai == TrangThai.HOAN_THANH for v in ds):
            cham.append("xong")
        o_ngay.append({"ngay": d, "viec": ds, "cham": cham, "nghi": services.la_ngay_nghi(d)})
        d += timedelta(days=1)

    # Mục tiêu tuần
    tuan = _dau_tuan(_doc_ngay(request.args.get("tuan"), ngay_chon))
    muc_tieu = (MucTieuTuan.query.filter_by(nguoi_dung_id=nd.id, tuan=tuan).order_by(MucTieuTuan.id).all())
    for mt in muc_tieu:
        mt.dat = _tien_do_muc_tieu(mt)
        mt.phan_tram = min(100, round(mt.dat / mt.chi_tieu * 100)) if mt.chi_tieu else 0

    de_xuat = (DeXuatNhanViec.query.filter(DeXuatNhanViec.nguoi_dung_id == nd.id,
                                           DeXuatNhanViec.trang_thai == TrangThaiDeXuatNhan.CHO_DUYET)
               .order_by(DeXuatNhanViec.tao_luc.desc()).all())
    mau_nv = []
    if nd.chuc_vu_id:
        mau_nv = [m for m in ViecDinhKy.query.filter_by(chuc_vu_id=nd.chuc_vu_id, dang_dung=True)
                  .order_by(ViecDinhKy.gio_han).all() if services.mau_hop_nhan_vien(m, nd)]
    thang_truoc = (dau - timedelta(days=1)).strftime("%Y-%m")
    return render_template(
        "nhiem_vu_cua_toi.html", nd=nd, la_minh=nd.id == current_user.id, quan_ly=_duoc_quan_ly(nd),
        hom_nay=hn, dau_thang=dau, o_ngay=o_ngay, o_trong_dau=dau.weekday(), ngay_chon=ngay_chon,
        thang_truoc=thang_truoc, thang_sau=cuoi.strftime("%Y-%m"),
        tuan=tuan, tuan_cuoi=tuan + timedelta(days=6), tuan_truoc=(tuan - timedelta(days=7)).isoformat(), tuan_sau=(tuan + timedelta(days=7)).isoformat(),
        muc_tieu=muc_tieu, diem=diem_tuan(nd, tuan), de_xuat=de_xuat, mau_nv=mau_nv,
        so_doi_duyet=CongViec.query.filter_by(nguoi_nhan_id=nd.id, trang_thai=TrangThai.CHO_DUYET).count(),
        so_can_lam=CongViec.query.filter(CongViec.nguoi_nhan_id == nd.id, CongViec.trang_thai.in_(TrangThai.CHUA_XONG)).count(),
        NguonViec=NguonViec, TEN_THU=TEN_THU,
    )


# ---------------------------------------------------------------------------
# Mục tiêu tuần
# ---------------------------------------------------------------------------
def _ve_trang(nd_id, tuan):
    kw = {"tuan": tuan.isoformat()}
    if nd_id != current_user.id:
        kw["nguoi"] = nd_id
    return redirect(url_for("nhiem_vu.cua_toi", **kw) + "#muc-tieu")


@bp.route("/muc-tieu", methods=["POST"])
@login_required
def them_muc_tieu():
    nd = db.session.get(NguoiDung, request.form.get("nguoi_dung_id", type=int) or 0) or abort(404)
    if not _duoc_quan_ly(nd):
        abort(403)
    tuan = _dau_tuan(_doc_ngay(request.form.get("tuan"), ngay_vn_hien_tai()))
    ten = " ".join((request.form.get("ten") or "").split())[:200]
    chi_tieu = request.form.get("chi_tieu", type=int) or 0
    mau = db.session.get(ViecDinhKy, request.form.get("viec_dinh_ky_id", type=int) or 0)
    if mau and not ten:
        ten = mau.tieu_de
    if not ten or chi_tieu <= 0:
        flash("Nhập tên mục tiêu và chỉ tiêu (số lớn hơn 0).", "error")
        return _ve_trang(nd.id, tuan)
    db.session.add(MucTieuTuan(nguoi_dung_id=nd.id, tuan=tuan, ten=ten, chi_tieu=chi_tieu,
                               viec_dinh_ky_id=mau.id if mau else None, tao_boi_id=current_user.id))
    db.session.commit()
    flash(f"Đã thêm mục tiêu tuần {tuan:%d/%m}: {ten}.", "success")
    return _ve_trang(nd.id, tuan)


@bp.route("/muc-tieu/chep-tuan-truoc", methods=["POST"])
@login_required
def chep_muc_tieu():
    nd = db.session.get(NguoiDung, request.form.get("nguoi_dung_id", type=int) or 0) or abort(404)
    if not _duoc_quan_ly(nd):
        abort(403)
    tuan = _dau_tuan(_doc_ngay(request.form.get("tuan"), ngay_vn_hien_tai()))
    co = {m.ten for m in MucTieuTuan.query.filter_by(nguoi_dung_id=nd.id, tuan=tuan)}
    so = 0
    for m in MucTieuTuan.query.filter_by(nguoi_dung_id=nd.id, tuan=tuan - timedelta(days=7)).all():
        if m.ten not in co:
            db.session.add(MucTieuTuan(nguoi_dung_id=nd.id, tuan=tuan, ten=m.ten, chi_tieu=m.chi_tieu,
                                       viec_dinh_ky_id=m.viec_dinh_ky_id, tao_boi_id=current_user.id))
            so += 1
    db.session.commit()
    flash(f"Đã chép {so} mục tiêu từ tuần trước." if so else "Tuần trước không có mục tiêu nào để chép.",
          "success" if so else "info")
    return _ve_trang(nd.id, tuan)


@bp.route("/muc-tieu/<int:id>/cap-nhat", methods=["POST"])
@login_required
def cap_nhat_muc_tieu(id):
    """Cập nhật số đạt (mục tiêu không gắn mẫu) — nhân viên tự báo, quản lý
    sửa được; quản lý còn sửa được tên / chỉ tiêu."""
    mt = db.session.get(MucTieuTuan, id) or abort(404)
    nd = mt.nguoi_dung
    if not (nd.id == current_user.id or _duoc_quan_ly(nd)):
        abort(403)
    if not mt.viec_dinh_ky_id and request.form.get("da_dat") is not None:
        mt.da_dat = max(0, request.form.get("da_dat", type=int) or 0)
    if _duoc_quan_ly(nd) and request.form.get("chi_tieu"):
        mt.chi_tieu = max(1, request.form.get("chi_tieu", type=int) or mt.chi_tieu)
    db.session.commit()
    return _ve_trang(nd.id, mt.tuan)


@bp.route("/muc-tieu/<int:id>/xoa", methods=["POST"])
@login_required
def xoa_muc_tieu(id):
    mt = db.session.get(MucTieuTuan, id) or abort(404)
    if not _duoc_quan_ly(mt.nguoi_dung):
        abort(403)
    nd_id, tuan = mt.nguoi_dung_id, mt.tuan
    db.session.delete(mt)
    db.session.commit()
    flash("Đã xoá mục tiêu.", "success")
    return _ve_trang(nd_id, tuan)


# ---------------------------------------------------------------------------
# Lịch làm việc nhân viên (quản lý / sếp): danh sách người trong phạm vi,
# tình hình việc hôm nay; bấm vào ai thì mở lịch tháng của người đó.
# ---------------------------------------------------------------------------
@bp.route("/nhan-vien")
@login_required
def lich_nhan_vien():
    if not current_user.la_quan_ly:
        return redirect(url_for("nhiem_vu.cua_toi"))
    q = NguoiDung.query.filter(NguoiDung.dang_hoat_dong.is_(True),
                               NguoiDung.vai_tro.notin_((VaiTro.ADMIN, VaiTro.SEP)),
                               NguoiDung.id != current_user.id)
    if not current_user.la_admin_sep:
        q = q.filter(NguoiDung.thuoc_bo_phan(current_user.ds_bo_phan_id or {-1}))
    ds_nv = q.order_by(NguoiDung.ho_ten).all()
    ids = [n.id for n in ds_nv] or [-1]
    hn = ngay_vn_hien_tai()
    bay_gio = gio_vn_hien_tai()
    dau = datetime.combine(hn, datetime.min.time())
    tk = {n.id: {"hom_nay": 0, "xong": 0, "tre": 0, "cho_duyet": 0} for n in ds_nv}
    for v in CongViec.query.filter(CongViec.nguoi_nhan_id.in_(ids), CongViec.trang_thai != TrangThai.HUY,
                                   CongViec.han >= dau, CongViec.han < dau + timedelta(days=1)):
        tk[v.nguoi_nhan_id]["hom_nay"] += 1
        tk[v.nguoi_nhan_id]["xong"] += v.trang_thai == TrangThai.HOAN_THANH
    for nid, so in (db.session.query(CongViec.nguoi_nhan_id, db.func.count(CongViec.id))
                    .filter(CongViec.nguoi_nhan_id.in_(ids), CongViec.trang_thai.in_(TrangThai.CHUA_XONG),
                            CongViec.han < bay_gio).group_by(CongViec.nguoi_nhan_id)):
        tk[nid]["tre"] = so
    for nid, so in (db.session.query(CongViec.nguoi_nhan_id, db.func.count(CongViec.id))
                    .filter(CongViec.nguoi_nhan_id.in_(ids), CongViec.trang_thai == TrangThai.CHO_DUYET)
                    .group_by(CongViec.nguoi_nhan_id)):
        tk[nid]["cho_duyet"] = so
    dong = [{"nv": n, **tk[n.id]} for n in ds_nv]
    # Người đang có việc trễ lên đầu để quản lý thấy ngay
    dong.sort(key=lambda d: (-d["tre"], d["nv"].ho_ten))
    tong = {k: sum(d[k] for d in dong) for k in ("hom_nay", "xong", "tre", "cho_duyet")}
    return render_template("nhiem_vu_lich_nv.html", dong=dong, tong=tong, hom_nay=hn)


# ---------------------------------------------------------------------------
# Đề xuất nhận việc
# ---------------------------------------------------------------------------
def de_xuat_cho_duyet() -> list:
    """Đề xuất nhận việc đang chờ current_user duyệt."""
    if not current_user.la_quan_ly:
        return []
    ds = DeXuatNhanViec.query.filter_by(trang_thai=TrangThaiDeXuatNhan.CHO_DUYET).order_by(DeXuatNhanViec.tao_luc).all()
    return [d for d in ds if _duoc_quan_ly(d.nguoi_dung)]


@bp.route("/de-xuat", methods=["GET", "POST"])
@login_required
def de_xuat():
    if request.method == "POST":
        tieu_de = " ".join((request.form.get("tieu_de") or "").split())[:255]
        try:
            ngay = date.fromisoformat(request.form.get("ngay_han") or "")
            gio = request.form.get("gio_han") or "17:00"
            h, m = (int(x) for x in gio.split(":"))
            han = datetime(ngay.year, ngay.month, ngay.day, h, m)
        except (ValueError, TypeError):
            han = None
        if not tieu_de or not han:
            flash("Nhập tên việc và hạn hoàn thành.", "error")
            return redirect(url_for("nhiem_vu.de_xuat"))
        if han < gio_vn_hien_tai():
            flash("Hạn hoàn thành phải sau thời điểm hiện tại.", "error")
            return redirect(url_for("nhiem_vu.de_xuat"))
        dx = DeXuatNhanViec(nguoi_dung_id=current_user.id, tieu_de=tieu_de, han=han,
                            mo_ta=(request.form.get("mo_ta") or "").strip() or None,
                            ket_qua=(request.form.get("ket_qua") or "").strip()[:255] or None)
        db.session.add(dx)
        db.session.commit()
        services.gui_nhom_ql(f"💡 {current_user.ho_ten} đề xuất nhận việc: {dx.tieu_de}\n"
                             f"Hạn: {dx.han:%H:%M %d/%m}\n"
                             f"Duyệt: {current_app.config['BASE_URL']}/cong-viec/de-xuat")
        flash("Đã gửi đề xuất — quản lý duyệt xong việc sẽ vào danh sách của bạn.", "success")
        return redirect(url_for("nhiem_vu.de_xuat"))

    cua_toi = (DeXuatNhanViec.query.filter_by(nguoi_dung_id=current_user.id)
               .order_by(DeXuatNhanViec.tao_luc.desc()).limit(30).all())
    return render_template("nhiem_vu_de_xuat.html", cua_toi=cua_toi, cho_duyet=de_xuat_cho_duyet(),
                           hom_nay=ngay_vn_hien_tai())


@bp.route("/de-xuat/<int:id>/duyet", methods=["POST"])
@login_required
def duyet_de_xuat(id):
    dx = db.session.get(DeXuatNhanViec, id) or abort(404)
    if not _duoc_quan_ly(dx.nguoi_dung):
        abort(403)
    if dx.trang_thai != TrangThaiDeXuatNhan.CHO_DUYET:
        flash("Đề xuất này đã được xử lý.", "info")
        return redirect(url_for("nhiem_vu.de_xuat"))
    y_kien = (request.form.get("y_kien") or "").strip() or None
    dx.nguoi_duyet_id, dx.duyet_luc, dx.y_kien = current_user.id, gio_vn_hien_tai(), y_kien
    if request.form.get("hanh_dong") == "duyet":
        han = dx.han if dx.han > gio_vn_hien_tai() else gio_vn_hien_tai() + timedelta(days=1)
        mo_ta = "\n".join(x for x in (dx.mo_ta, f"Kết quả cần đạt: {dx.ket_qua}" if dx.ket_qua else None) if x)
        v = CongViec(tieu_de=dx.tieu_de, mo_ta=mo_ta or None, han=han, do_uu_tien="thuong",
                     nguoi_giao_id=current_user.id, nguoi_nhan_id=dx.nguoi_dung_id,
                     trang_thai=TrangThai.MOI, nguon=NguonViec.DE_XUAT)
        db.session.add(v)
        db.session.flush()
        services.gan_ma(v)
        dx.trang_thai, dx.cong_viec_id = TrangThaiDeXuatNhan.DA_DUYET, v.id
        db.session.commit()
        services.gui_cho_nhan_vien(dx.nguoi_dung, f"✅ Đề xuất nhận việc được duyệt: {dx.tieu_de}\n"
                                                  f"Hạn: {han:%H:%M %d/%m}" + (f"\nÝ kiến: {y_kien}" if y_kien else "")
                                                  + f"\n{v.link}", cong_viec=v)
        flash(f"Đã duyệt — tạo việc {v.ma} cho {dx.nguoi_dung.ho_ten}.", "success")
    else:
        dx.trang_thai = TrangThaiDeXuatNhan.TU_CHOI
        db.session.commit()
        services.gui_cho_nhan_vien(dx.nguoi_dung, f"❌ Đề xuất nhận việc chưa được duyệt: {dx.tieu_de}"
                                                  + (f"\nÝ kiến: {y_kien}" if y_kien else ""))
        flash("Đã từ chối đề xuất.", "success")
    tiep = request.form.get("tiep") or ""
    return redirect(tiep if tiep.startswith("/") else url_for("nhiem_vu.de_xuat"))


@bp.route("/de-xuat/<int:id>/huy", methods=["POST"])
@login_required
def huy_de_xuat(id):
    dx = db.session.get(DeXuatNhanViec, id) or abort(404)
    if dx.nguoi_dung_id != current_user.id or dx.trang_thai != TrangThaiDeXuatNhan.CHO_DUYET:
        abort(403)
    dx.trang_thai = TrangThaiDeXuatNhan.DA_HUY
    db.session.commit()
    flash("Đã huỷ đề xuất.", "success")
    return redirect(url_for("nhiem_vu.de_xuat"))


# ---------------------------------------------------------------------------
# Cấu hình nhiệm vụ: Vị trí → Giai đoạn → Bộ nhiệm vụ (Ban giám đốc / Quản trị)
# ---------------------------------------------------------------------------
@bp.route("/nhiem-vu")
@login_required
def cau_hinh():
    _chi_admin_sep()
    ds_cv = ChucVu.query.order_by(ChucVu.ten).all()
    cv = db.session.get(ChucVu, request.args.get("cv", type=int) or 0) or (ds_cv[0] if ds_cv else None)
    gd = request.args.get("gd") if request.args.get("gd") in GiaiDoan.TAT_CA else GiaiDoan.CHINH_THUC
    mau, mau_chung, nhan_vien, tc = [], [], [], None
    if cv:
        tat_ca = ViecDinhKy.query.filter_by(chuc_vu_id=cv.id).order_by(ViecDinhKy.gio_han, ViecDinhKy.id).all()
        mau = [m for m in tat_ca if m.giai_doan == gd]
        mau_chung = [m for m in tat_ca if not m.giai_doan]
        nhan_vien = (NguoiDung.query.filter(NguoiDung.chuc_vu_id == cv.id, NguoiDung.dang_hoat_dong.is_(True))
                     .order_by(NguoiDung.ho_ten).all())
        tc = TieuChiDanhGia.query.filter_by(chuc_vu_id=cv.id, giai_doan=gd).first()
    dem = {}
    for c in ds_cv:
        dem[c.id] = ViecDinhKy.query.filter_by(chuc_vu_id=c.id).count()
    mang_co = sorted({m.mang for m in (mau + mau_chung) if m.mang})
    return render_template(
        "nhiem_vu_cau_hinh.html", ds_cv=ds_cv, cv=cv, gd=gd, mau=mau, mau_chung=mau_chung, nhan_vien=nhan_vien,
        tc=tc, dem=dem, mang_co=mang_co, GiaiDoan=GiaiDoan, TEN_THU=TEN_THU, hom_nay=ngay_vn_hien_tai(),
        trong_so=(tc.khoi_luong, tc.chat_luong, tc.dung_han) if tc else TieuChiDanhGia.MAC_DINH,
    )


def _ve_cau_hinh(cv_id, gd):
    return redirect(url_for("nhiem_vu.cau_hinh", cv=cv_id, gd=gd))


@bp.route("/nhiem-vu/luu", methods=["POST"])
@login_required
def luu_mau():
    _chi_admin_sep()
    mid = request.form.get("id", type=int)
    m = db.session.get(ViecDinhKy, mid) if mid else ViecDinhKy()
    if mid and not m:
        abort(404)
    cv = db.session.get(ChucVu, request.form.get("chuc_vu_id", type=int) or 0)
    gd = request.form.get("giai_doan") or None
    tieu_de = " ".join((request.form.get("tieu_de") or "").split())
    thu = sorted({int(x) for x in request.form.getlist("thu") if x.isdigit() and int(x) < 7})
    gio = (request.form.get("gio_han") or "").strip()
    if not cv or not tieu_de or not thu or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", gio) \
            or (gd and gd not in GiaiDoan.TAT_CA):
        flash("Cần nhập tên nhiệm vụ, ít nhất 1 thứ và giờ hạn hợp lệ.", "error")
        return _ve_cau_hinh(cv.id if cv else None, gd or GiaiDoan.CHINH_THUC)
    m.chuc_vu_id, m.giai_doan, m.tieu_de = cv.id, gd, tieu_de[:255]
    m.mo_ta = (request.form.get("mo_ta") or "").strip() or None
    m.ket_qua = (request.form.get("ket_qua") or "").strip()[:255] or None
    m.minh_chung = (request.form.get("minh_chung") or "").strip()[:255] or None
    m.mang = (request.form.get("mang") or "").strip()[:60] or None
    m.cac_thu, m.gio_han = ",".join(map(str, thu)), gio
    m.tu_dong = request.form.get("tu_dong") == "on"
    m.dang_dung = request.form.get("dang_dung") == "on" if mid else True
    if not mid:
        db.session.add(m)
    db.session.commit()
    flash(f"Đã lưu nhiệm vụ \"{m.tieu_de}\".", "success")
    return _ve_cau_hinh(cv.id, gd or request.form.get("gd_dang_xem") or GiaiDoan.CHINH_THUC)


@bp.route("/nhiem-vu/<int:id>/xoa", methods=["POST"])
@login_required
def xoa_mau(id):
    _chi_admin_sep()
    m = db.session.get(ViecDinhKy, id) or abort(404)
    cv_id, gd = m.chuc_vu_id, m.giai_doan or GiaiDoan.CHINH_THUC
    CongViec.query.filter_by(viec_dinh_ky_id=m.id).update({"viec_dinh_ky_id": None})
    MucTieuTuan.query.filter_by(viec_dinh_ky_id=m.id).update({"viec_dinh_ky_id": None})
    db.session.delete(m)
    db.session.commit()
    flash("Đã xoá nhiệm vụ — việc đã giao trước đó vẫn giữ nguyên.", "success")
    return _ve_cau_hinh(cv_id, gd)


@bp.route("/nhiem-vu/<int:id>/giao", methods=["POST"])
@login_required
def giao_tu_mau(id):
    """Giao ngay 1 nhiệm vụ cho 1 nhân viên (mẫu có điều kiện: khi có lệnh
    pha, dự án… hoặc muốn giao bù hôm nay)."""
    _chi_admin_sep()
    m = db.session.get(ViecDinhKy, id) or abort(404)
    nv = db.session.get(NguoiDung, request.form.get("nguoi_dung_id", type=int) or 0)
    try:
        ngay = date.fromisoformat(request.form.get("ngay") or "")
        h, p = (int(x) for x in m.gio_han.split(":"))
        han = datetime(ngay.year, ngay.month, ngay.day, h, p)
    except ValueError:
        han = None
    if not nv or not han:
        flash("Chọn nhân viên và ngày.", "error")
        return _ve_cau_hinh(m.chuc_vu_id, m.giai_doan or GiaiDoan.CHINH_THUC)
    if han <= gio_vn_hien_tai():
        han = gio_vn_hien_tai().replace(second=0, microsecond=0) + timedelta(hours=2)
    v = services.giao_viec_tu_mau(m, nv, han, current_user)
    db.session.commit()
    services.gui_cho_nhan_vien(nv, f"📋 Việc mới: {v.tieu_de}\nHạn: {v.han:%H:%M %d/%m}\n{v.link}", cong_viec=v)
    flash(f"Đã giao {v.ma} cho {nv.ho_ten}.", "success")
    return _ve_cau_hinh(m.chuc_vu_id, m.giai_doan or GiaiDoan.CHINH_THUC)


@bp.route("/nhiem-vu/tieu-chi", methods=["POST"])
@login_required
def luu_tieu_chi():
    _chi_admin_sep()
    cv = db.session.get(ChucVu, request.form.get("chuc_vu_id", type=int) or 0) or abort(404)
    gd = request.form.get("giai_doan")
    if gd not in GiaiDoan.TAT_CA:
        abort(400)
    kl, cl, dh = (max(0, request.form.get(k, type=int) or 0) for k in ("khoi_luong", "chat_luong", "dung_han"))
    if kl + cl + dh != 100:
        flash(f"Tổng 3 tiêu chí phải bằng 100% (đang là {kl + cl + dh}%).", "error")
        return _ve_cau_hinh(cv.id, gd)
    tc = TieuChiDanhGia.query.filter_by(chuc_vu_id=cv.id, giai_doan=gd).first() \
        or TieuChiDanhGia(chuc_vu_id=cv.id, giai_doan=gd)
    tc.khoi_luong, tc.chat_luong, tc.dung_han = kl, cl, dh
    db.session.add(tc)
    db.session.commit()
    flash("Đã lưu tiêu chí đánh giá.", "success")
    return _ve_cau_hinh(cv.id, gd)


@bp.route("/nhiem-vu/nhan-vien/<int:id>", methods=["POST"])
@login_required
def luu_nhan_vien(id):
    """Đổi giai đoạn + mảng phụ trách của 1 nhân viên. Áp dụng từ hôm nay:
    việc hằng ngày từ lần tạo kế tiếp theo bộ nhiệm vụ mới, việc cũ giữ nguyên."""
    _chi_admin_sep()
    nv = db.session.get(NguoiDung, id) or abort(404)
    gd = request.form.get("giai_doan")
    if gd in GiaiDoan.TAT_CA and gd != nv.giai_doan:
        nv.giai_doan, nv.giai_doan_tu = gd, ngay_vn_hien_tai()
    mang = [x.strip() for x in request.form.getlist("mang") if x.strip()]
    nv.mang_phu_trach = ",".join(mang)[:255] or None
    db.session.commit()
    flash(f"{nv.ho_ten}: {nv.ten_giai_doan}" + (f" · mảng {', '.join(mang)}" if mang else " · mọi mảng"), "success")
    return _ve_cau_hinh(nv.chuc_vu_id, request.form.get("gd_dang_xem") or nv.giai_doan)


@bp.route("/nhiem-vu/nhap-excel", methods=["POST"])
@login_required
def nhap_excel():
    """Nhập file dự thảo "BRICON_Bo_nhiem_vu_du_thao.xlsx" (sheet Nhiem_vu):
    mỗi Nhóm chức năng gán vào 1 chức vụ chọn trên form. Trùng mã mẫu cùng
    chức vụ thì cập nhật, không nhân đôi."""
    _chi_admin_sep()
    from openpyxl import load_workbook
    f = request.files.get("tep")
    if not f or not f.filename.lower().endswith(".xlsx"):
        flash("Chọn file .xlsx bộ nhiệm vụ.", "error")
        return redirect(url_for("nhiem_vu.cau_hinh"))
    try:
        wb = load_workbook(f, read_only=True, data_only=True)
        ws = wb["Nhiem_vu"] if "Nhiem_vu" in wb.sheetnames else wb.worksheets[0]
        hang = list(ws.iter_rows(values_only=True))
    except Exception:
        flash("Không đọc được file Excel.", "error")
        return redirect(url_for("nhiem_vu.cau_hinh"))
    dau = next((i for i, r in enumerate(hang) if r and "Nhiệm vụ" in [str(c or "").strip() for c in r]), None)
    if dau is None:
        flash("Không tìm thấy bảng nhiệm vụ (cần có cột \"Nhiệm vụ\").", "error")
        return redirect(url_for("nhiem_vu.cau_hinh"))
    cot = {str(c or "").strip(): i for i, c in enumerate(hang[dau])}
    can = ("Mã", "Nhóm chức năng", "Giai đoạn", "Mảng phụ trách", "Nhiệm vụ")
    if any(k not in cot for k in can):
        flash("File thiếu cột: " + ", ".join(k for k in can if k not in cot), "error")
        return redirect(url_for("nhiem_vu.cau_hinh"))
    gd_theo_ten = {v.lower(): k for k, v in GiaiDoan.NHAN.items()}
    moi = cap_nhat = bo_qua = 0
    nhom_bo_qua = set()
    for r in hang[dau + 1:]:
        lay = lambda k: str(r[cot[k]] or "").strip() if k in cot and cot[k] < len(r) else ""  # noqa: E731
        if not lay("Nhiệm vụ"):
            continue
        nhom = lay("Nhóm chức năng")
        cv = db.session.get(ChucVu, request.form.get(f"cv_{nhom}", type=int) or 0)
        gd = gd_theo_ten.get(lay("Giai đoạn").lower())
        if not cv or not gd:
            bo_qua += 1
            nhom_bo_qua.add(nhom)
            continue
        ma = lay("Mã")[:10] or None
        m = ViecDinhKy.query.filter_by(chuc_vu_id=cv.id, ma=ma).first() if ma else None
        if m:
            cap_nhat += 1
        else:
            m = ViecDinhKy(chuc_vu_id=cv.id, cac_thu="0,1,2,3,4,5", gio_han="17:00", dang_dung=True, ma=ma)
            db.session.add(m)
            moi += 1
        kich_hoat = lay("Kích hoạt / tần suất")
        m.tieu_de, m.giai_doan, m.mang = lay("Nhiệm vụ")[:255], gd, lay("Mảng phụ trách")[:60] or None
        m.ket_qua = lay("Kết quả đạt")[:255] or None
        m.minh_chung = lay("Minh chứng")[:255] or None
        m.tu_dong = not kich_hoat.lower().startswith("khi có")
        m.mo_ta = f"Kích hoạt: {kich_hoat}" if kich_hoat else None
    db.session.commit()
    flash(f"Đã nhập {moi} nhiệm vụ mới, cập nhật {cap_nhat}."
          + (f" Bỏ qua {bo_qua} dòng chưa chọn chức vụ ({', '.join(sorted(nhom_bo_qua))})." if bo_qua else ""),
          "success" if moi or cap_nhat else "info")
    return redirect(url_for("nhiem_vu.cau_hinh"))
