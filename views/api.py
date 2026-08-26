"""API cho n8n (Police Bricon) đọc/ghi dữ liệu.

Xác thực bằng header:  X-API-Key: <API_KEY trong .env>
"""
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Blueprint, current_app, jsonify, request

import services
from extensions import db
from models import ChamCong, CongViec, NguoiDung, TrangThai, VaiTro

bp = Blueprint("api", __name__, url_prefix="/api/v1")


def can_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-API-Key") or request.args.get("api_key")
        cau_hinh = current_app.config["API_KEY"]
        if not cau_hinh or key != cau_hinh:
            return jsonify({"ok": False, "loi": "Sai hoặc thiếu API key"}), 401
        return f(*args, **kwargs)
    return wrapper


def _viec_json(v: CongViec) -> dict:
    return {
        "id": v.id,
        "ma": v.ma,
        "tieu_de": v.tieu_de,
        "mo_ta": v.mo_ta,
        "nguoi_giao": v.nguoi_giao.ho_ten,
        "nguoi_nhan": v.nguoi_nhan.ho_ten,
        "ma_dinh_danh": v.nguoi_nhan.ma_dinh_danh,
        "zalo_group_id": v.nguoi_nhan.zalo_group_id,
        "han": v.han.isoformat() if v.han else None,
        "trang_thai": v.trang_thai,
        "ten_trang_thai": v.ten_trang_thai,
        "qua_han": v.qua_han,
        "lan_gui": v.lan_gui,
        "so_sao": v.so_sao_cuoi,
        "so_dinh_kem": len(v.dinh_kem),
        "tao_luc": v.tao_luc.isoformat() if v.tao_luc else None,
        "hoan_thanh_luc": v.hoan_thanh_luc.isoformat() if v.hoan_thanh_luc else None,
        "link": v.link,
    }


# ---------------------------------------------------------------- nhân viên
@bp.get("/nhan-vien")
@can_api_key
def api_nhan_vien():
    q = NguoiDung.query
    if request.args.get("dang_hoat_dong", "1") == "1":
        q = q.filter_by(dang_hoat_dong=True)
    return jsonify({"ok": True, "data": [
        {
            "id": n.id, "ma_dinh_danh": n.ma_dinh_danh, "ho_ten": n.ho_ten,
            "vai_tro": n.vai_tro, "bo_phan": n.bo_phan.ten if n.bo_phan else None,
            "zalo_group_id": n.zalo_group_id, "dang_hoat_dong": n.dang_hoat_dong,
        } for n in q.order_by(NguoiDung.ho_ten).all()
    ]})


# ---------------------------------------------------------------- công việc
@bp.get("/cong-viec")
@can_api_key
def api_cong_viec():
    q = CongViec.query
    if ma_nv := request.args.get("ma_dinh_danh"):
        q = q.join(NguoiDung, CongViec.nguoi_nhan_id == NguoiDung.id).filter(
            db.func.lower(NguoiDung.ma_dinh_danh) == ma_nv.lower()
        )
    tt = request.args.get("trang_thai")
    if tt == "dang_mo":
        q = q.filter(CongViec.trang_thai.in_(TrangThai.DANG_MO))
    elif tt:
        q = q.filter(CongViec.trang_thai == tt)
    if request.args.get("qua_han") == "1":
        q = q.filter(CongViec.han < datetime.now(),
                     CongViec.trang_thai.in_(TrangThai.CHUA_XONG))
    if han_tu := request.args.get("han_tu"):
        q = q.filter(CongViec.han >= datetime.fromisoformat(han_tu))
    if han_den := request.args.get("han_den"):
        q = q.filter(CongViec.han <= datetime.fromisoformat(han_den))

    gh = min(request.args.get("gioi_han", 200, type=int), 1000)
    viecs = q.order_by(CongViec.han.is_(None), CongViec.han).limit(gh).all()
    return jsonify({"ok": True, "tong": len(viecs),
                    "data": [_viec_json(v) for v in viecs]})


@bp.get("/cong-viec/<ma>")
@can_api_key
def api_cong_viec_chi_tiet(ma):
    v = CongViec.query.filter(db.func.upper(CongViec.ma) == ma.upper()).first()
    if not v:
        return jsonify({"ok": False, "loi": f"Không tìm thấy {ma}"}), 404
    data = _viec_json(v)
    data["danh_gia"] = [
        {"lan_gui": d.lan_gui, "ket_qua": d.ket_qua, "so_sao": d.so_sao,
         "ghi_chu": d.ghi_chu, "nguoi_danh_gia": d.nguoi_danh_gia.ho_ten,
         "tao_luc": d.tao_luc.isoformat()}
        for d in v.danh_gia
    ]
    return jsonify({"ok": True, "data": data})


@bp.post("/cong-viec")
@can_api_key
def api_tao_cong_viec():
    """Cho phép bot Zalo giao việc bằng câu lệnh, việc vẫn hiện trên web."""
    d = request.get_json(silent=True) or {}
    nguoi_giao = NguoiDung.query.filter(
        db.func.lower(NguoiDung.ma_dinh_danh) == str(d.get("nguoi_giao", "")).lower()
    ).first()
    nguoi_nhan = NguoiDung.query.filter(
        db.func.lower(NguoiDung.ma_dinh_danh) == str(d.get("nguoi_nhan", "")).lower()
    ).first()
    if not nguoi_giao or not nguoi_nhan:
        return jsonify({"ok": False, "loi": "Sai ma_dinh_danh nguoi_giao/nguoi_nhan"}), 400
    if nguoi_nhan.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
        return jsonify({"ok": False, "loi": "Sếp/Quản trị không thể bị giao việc"}), 400
    if not (d.get("tieu_de") or "").strip():
        return jsonify({"ok": False, "loi": "Thiếu tieu_de"}), 400

    han = None
    if d.get("han"):
        try:
            han = datetime.fromisoformat(d["han"])
        except ValueError:
            return jsonify({"ok": False, "loi": "han phải theo ISO 8601"}), 400

    v = CongViec(tieu_de=d["tieu_de"].strip(), mo_ta=d.get("mo_ta"), han=han,
                 nguoi_giao_id=nguoi_giao.id, nguoi_nhan_id=nguoi_nhan.id,
                 trang_thai=TrangThai.MOI)
    db.session.add(v)
    db.session.flush()
    services.gan_ma(v)
    db.session.commit()

    if d.get("gui_zalo", True):
        services.bao_giao_viec(v)
        db.session.commit()
    return jsonify({"ok": True, "data": _viec_json(v)}), 201


# ---------------------------------------------------------------- chấm công
@bp.get("/cham-cong")
@can_api_key
def api_cham_cong():
    thang = request.args.get("thang") or date.today().strftime("%Y-%m")
    nam, thg = (int(x) for x in thang.split("-"))
    dau = date(nam, thg, 1)
    cuoi = date(nam + (thg == 12), (thg % 12) + 1, 1)

    q = ChamCong.query.filter(ChamCong.ngay >= dau, ChamCong.ngay < cuoi)
    if ma_nv := request.args.get("ma_dinh_danh"):
        q = q.join(NguoiDung).filter(
            db.func.lower(NguoiDung.ma_dinh_danh) == ma_nv.lower())

    return jsonify({"ok": True, "thang": thang, "data": [
        {
            "ma_dinh_danh": c.nguoi_dung.ma_dinh_danh,
            "ho_ten": c.nguoi_dung.ho_ten,
            "ngay": c.ngay.isoformat(),
            "gio_vao": c.gio_vao.strftime("%H:%M") if c.gio_vao else None,
            "gio_ra": c.gio_ra.strftime("%H:%M") if c.gio_ra else None,
            "di_tre": c.di_tre, "so_phut_tre": c.so_phut_tre,
            "ve_som": c.ve_som, "so_phut_som": c.so_phut_som,
            "ngoai_pham_vi": c.ngoai_pham_vi,
            "nghi_khong_phep": c.nghi_khong_phep,
            "nghi_ngo": c.nghi_ngo,
            "diem": c.diem_vao.ten if c.diem_vao else None,
        } for c in q.order_by(ChamCong.ngay).all()
    ]})


# ---------------------------------------------------------------- KPI
@bp.get("/kpi")
@can_api_key
def api_kpi():
    hom_nay = date.today()
    dau_tuan = hom_nay - timedelta(days=hom_nay.weekday())

    try:
        tu_ngay = date.fromisoformat(request.args["tu_ngay"]) if request.args.get("tu_ngay") else dau_tuan
    except ValueError:
        return jsonify({"ok": False, "loi": "tu_ngay phải theo định dạng YYYY-MM-DD"}), 400
    try:
        den_ngay = date.fromisoformat(request.args["den_ngay"]) if request.args.get("den_ngay") else hom_nay
    except ValueError:
        return jsonify({"ok": False, "loi": "den_ngay phải theo định dạng YYYY-MM-DD"}), 400

    return jsonify({"ok": True, "tu_ngay": tu_ngay.isoformat(), "den_ngay": den_ngay.isoformat(),
                    "data": services.tinh_kpi(tu_ngay, den_ngay)})


# ---------------------------------------------------------------- tóm tắt
@bp.get("/tom-tat")
@can_api_key
def api_tom_tat():
    """Số liệu nhanh cho bot trả lời câu hỏi tự do trong nhóm QL."""
    return jsonify({"ok": True, "data": {
        "dang_lam": CongViec.query.filter(
            CongViec.trang_thai.in_(TrangThai.CHUA_XONG)).count(),
        "cho_duyet": CongViec.query.filter_by(
            trang_thai=TrangThai.CHO_DUYET).count(),
        "qua_han": CongViec.query.filter(
            CongViec.han < datetime.now(),
            CongViec.trang_thai.in_(TrangThai.CHUA_XONG)).count(),
        "cham_cong_hom_nay": ChamCong.query.filter_by(ngay=date.today()).count(),
        "nhan_vien_hoat_dong": NguoiDung.query.filter_by(dang_hoat_dong=True).count(),
    }})