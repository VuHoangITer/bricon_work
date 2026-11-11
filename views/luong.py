"""Lương — tính tự động từ chấm công, tạm ứng, biên bản phạt.

- Vị trí lương: mẫu lương từng vị trí (= file preset .json của app Phiếu
  Lương cũ, nhập thẳng các file đó vào được).
- Vị trí của nhân viên: mỗi người áp dụng vị trí nào TỪ NGÀY nào (thử việc
  -> chính thức). Tháng có đổi vị trí tự chia giai đoạn.
- Bảng lương tháng + phiếu lương từng người (in / lưu PDF) + xuất Excel.

Chỉ Quản trị / Ban giám đốc được vào (thông tin lương)."""
import json
from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

import services
from extensions import db
from models import ChamCong, KhenThuong, LuongNhanVien, NguoiDung, VaiTro, ViTriLuong, ngay_vn_hien_tai

bp = Blueprint("luong", __name__, url_prefix="/luong")

# Trang ai cũng vào được (chỉ thấy lương của chính mình)
_TRANG_CA_NHAN = ("luong.cua_toi",)


@bp.before_request
@login_required
def _chi_admin_sep():
    if request.endpoint in _TRANG_CA_NHAN:
        return None
    if not current_user.la_admin_sep:
        abort(403)


# ---------------------------------------------------------------------------
# Lương của tôi — nhân viên xem lương tạm tính đến hôm nay
# ---------------------------------------------------------------------------
@bp.route("/cua-toi")
def cua_toi():
    nam, thg, thang = _thang()
    hn = ngay_vn_hien_tai()
    p = services.tinh_luong(current_user, nam, thg)
    thuong = (KhenThuong.query.filter(KhenThuong.nguoi_dung_id == current_user.id)
              .order_by(KhenThuong.ngay.desc()).limit(10).all())
    return render_template(
        "luong_cua_toi.html", p=p, thang=thang, nam=nam, thg=thg, hom_nay=hn, thuong=thuong,
        bang_chu=services.so_tien_bang_chu(p["thuc_linh"]) if p else "",
        thang_truoc=_thang_lech(nam, thg, -1),
        thang_sau=_thang_lech(nam, thg, 1) if (nam, thg) < (hn.year, hn.month) else None,
    )


# ---------------------------------------------------------------------------
# Khen thưởng
# ---------------------------------------------------------------------------
@bp.route("/khen-thuong", methods=["GET", "POST"])
def khen_thuong():
    nam, thg, thang = _thang()
    if request.method == "POST":
        nd = db.session.get(NguoiDung, request.form.get("nguoi_dung_id", type=int) or 0)
        so_tien = int("".join(ch for ch in request.form.get("so_tien", "") if ch.isdigit()) or 0)
        ly_do = (request.form.get("ly_do") or "").strip()
        try:
            ngay = date.fromisoformat(request.form.get("ngay") or "")
        except ValueError:
            ngay = None
        if not nd or not ngay or so_tien <= 0 or not ly_do:
            flash("Chọn nhân viên, ngày, nhập số tiền và lý do khen thưởng.", "error")
            return redirect(url_for("luong.khen_thuong", thang=thang))
        kt = KhenThuong(nguoi_dung_id=nd.id, ngay=ngay, so_tien=so_tien, ly_do=ly_do, nguoi_tao_id=current_user.id)
        db.session.add(kt)
        db.session.commit()
        services.gui_cho_nhan_vien(
            nd, f"🎉 Chúc mừng {nd.ho_ten}!\nBạn được khen thưởng {services.dinh_dang_tien(so_tien)}"
                f"\nLý do: {ly_do}\nKhoản thưởng được cộng vào lương tháng {ngay.month}/{ngay.year}.")
        flash(f"Đã thưởng {nd.ho_ten} {services.dinh_dang_tien(so_tien)}.", "success")
        return redirect(url_for("luong.khen_thuong", thang=f"{ngay.year:04d}-{ngay.month:02d}"))

    dau = date(nam, thg, 1)
    cuoi = date(nam + (thg == 12), thg % 12 + 1, 1)
    ds = (KhenThuong.query.filter(KhenThuong.ngay >= dau, KhenThuong.ngay < cuoi)
          .order_by(KhenThuong.ngay.desc(), KhenThuong.id.desc()).all())
    hn = ngay_vn_hien_tai()
    return render_template(
        "luong_khen_thuong.html", ds=ds, tong=sum(k.so_tien for k in ds), thang=thang, nam=nam, thg=thg,
        hom_nay=hn, thang_truoc=_thang_lech(nam, thg, -1), thang_sau=_thang_lech(nam, thg, 1),
        nhan_vien=NguoiDung.query.filter(NguoiDung.dang_hoat_dong.is_(True),
                                         NguoiDung.vai_tro.notin_((VaiTro.ADMIN,)))
        .order_by(NguoiDung.ho_ten).all(),
        chon_nv=request.args.get("nguoi", type=int),
    )


@bp.route("/khen-thuong/<int:id>/xoa", methods=["POST"])
def xoa_khen_thuong(id):
    kt = db.session.get(KhenThuong, id) or abort(404)
    thang = f"{kt.ngay.year:04d}-{kt.ngay.month:02d}"
    db.session.delete(kt)
    db.session.commit()
    flash("Đã xoá khoản thưởng.", "success")
    return redirect(url_for("luong.khen_thuong", thang=thang))


def _thang():
    """?thang=YYYY-MM -> (nam, thg, "YYYY-MM"); mặc định tháng hiện tại."""
    hn = ngay_vn_hien_tai()
    try:
        nam, thg = (int(x) for x in (request.args.get("thang") or "").split("-"))
        date(nam, thg, 1)
    except ValueError:
        nam, thg = hn.year, hn.month
    return nam, thg, f"{nam:04d}-{thg:02d}"


def _thang_lech(nam, thg, lech):
    m = nam * 12 + (thg - 1) + lech
    return f"{m // 12:04d}-{m % 12 + 1:02d}"


def _bang_luong(nam, thg):
    """[phiếu lương] của mọi người có vị trí lương trong tháng + [nhân viên
    chưa gán vị trí]. Gồm cả người đã nghỉ việc nhưng còn chấm công trong
    tháng (để vẫn trả được lương tháng cuối)."""
    dau = date(nam, thg, 1)
    cuoi = date(nam + (thg == 12), thg % 12 + 1, 1)
    co_cong = db.select(ChamCong.nguoi_dung_id).where(ChamCong.ngay >= dau, ChamCong.ngay < cuoi)
    ds_nd = (NguoiDung.query.filter(db.or_(NguoiDung.dang_hoat_dong.is_(True), NguoiDung.id.in_(co_cong)))
             .order_by(NguoiDung.ho_ten).all())
    phieu, chua_gan = [], []
    for nd in ds_nd:
        p = services.tinh_luong(nd, nam, thg)
        if p:
            phieu.append(p)
        elif nd.vai_tro not in (VaiTro.ADMIN, VaiTro.SEP) and nd.dang_hoat_dong:
            chua_gan.append(nd)
    return phieu, chua_gan


# ---------------------------------------------------------------------------
# Bảng lương tháng
# ---------------------------------------------------------------------------
@bp.route("/")
def bang_luong():
    nam, thg, thang = _thang()
    phieu, chua_gan = _bang_luong(nam, thg)
    hn = ngay_vn_hien_tai()
    tong = {k: sum(p[k] for p in phieu) for k in ("luong_cb", "trach_nhiem", "tong_nang_luc", "thu",
                                                  "tam_ung", "phat", "khau_tru_khac", "thuc_linh")}
    return render_template(
        "luong_bang.html", phieu=phieu, chua_gan=chua_gan, tong=tong, thang=thang, nam=nam, thg=thg,
        cong_chuan=services.cong_chuan_thang(nam, thg),
        thang_truoc=_thang_lech(nam, thg, -1),
        thang_sau=_thang_lech(nam, thg, 1) if (nam, thg) < (hn.year, hn.month) else None,
        so_vi_tri=ViTriLuong.query.count(),
    )


@bp.route("/excel")
def xuat_excel():
    nam, thg, thang = _thang()
    phieu, _ = _bang_luong(nam, thg)
    return send_file(services.xuat_excel_bang_luong(thang, phieu), as_attachment=True,
                     download_name=f"bang_luong_{thang}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/phieu/<int:id>")
def phieu(id):
    nd = db.session.get(NguoiDung, id) or abort(404)
    nam, thg, thang = _thang()
    p = services.tinh_luong(nd, nam, thg)
    if not p:
        flash(f"{nd.ho_ten} chưa được gán vị trí lương cho tháng {thg}/{nam}.", "error")
        return redirect(url_for("luong.nhan_vien", id=nd.id))
    return render_template("luong_phieu.html", p=p, thang=thang,
                           bang_chu=services.so_tien_bang_chu(p["thuc_linh"]), hom_nay=ngay_vn_hien_tai())


# ---------------------------------------------------------------------------
# Vị trí lương (mẫu lương)
# ---------------------------------------------------------------------------
def _ds_khoan(tien_to: str) -> list:
    """Đọc các dòng khoản tiền từ form: <tien_to>_nd[] + <tien_to>_st[]."""
    ds = []
    for nd, st in zip(request.form.getlist(f"{tien_to}_nd"), request.form.getlist(f"{tien_to}_st")):
        nd = (nd or "").strip()
        so = int("".join(ch for ch in (st or "") if ch.isdigit()) or 0)
        if nd or so:
            ds.append({"noi_dung": nd, "so_tien": so})
    return ds


def _so(x) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def _tu_json(data: dict) -> dict:
    """Preset .json của app Phiếu Lương cũ -> các trường ViTriLuong."""
    def khoan(ds):
        return [{"noi_dung": str(x.get("noi_dung") or "").strip(), "so_tien": _so(x.get("so_tien"))}
                for x in (ds or []) if isinstance(x, dict) and (x.get("noi_dung") or _so(x.get("so_tien")))]
    return {
        "ten": str(data.get("vi_tri") or "").strip(),
        "phong_ban": str(data.get("phong_ban") or "").strip() or None,
        "luong_co_ban": _so(data.get("luong_co_ban")),
        "trach_nhiem": khoan(data.get("luong_trach_nhiem")),
        "nang_luc": khoan(data.get("luong_nang_luc")),
        "khau_tru": khoan(data.get("khau_tru")),
    }


@bp.route("/vi-tri", methods=["GET", "POST"])
def vi_tri():
    if request.method == "POST":
        # Nhập nhiều file preset .json cùng lúc; trùng tên vị trí -> cập nhật
        moi = cap_nhat = 0
        loi = []
        for f in request.files.getlist("tep"):
            if not f or not f.filename:
                continue
            try:
                data = json.loads(f.read().decode("utf-8-sig"))
                if not isinstance(data, dict) or "luong_co_ban" not in data:
                    raise ValueError("không phải file vị trí lương")
                gt = _tu_json(data)
                if not gt["ten"]:
                    raise ValueError("thiếu tên vị trí")
            except (ValueError, UnicodeDecodeError) as e:
                loi.append(f"{f.filename}: {e}")
                continue
            vt = ViTriLuong.query.filter(db.func.lower(ViTriLuong.ten) == gt["ten"].lower()).first()
            if vt:
                cap_nhat += 1
            else:
                vt = ViTriLuong()
                db.session.add(vt)
                moi += 1
            for k, v in gt.items():
                setattr(vt, k, v)
        db.session.commit()
        if moi or cap_nhat:
            flash(f"Đã nhập {moi} vị trí mới, cập nhật {cap_nhat} vị trí.", "success")
        for l in loi:
            flash(f"Bỏ qua {l}", "error")
        return redirect(url_for("luong.vi_tri"))

    ds = ViTriLuong.query.order_by(ViTriLuong.phong_ban, ViTriLuong.ten).all()
    dem = dict(db.session.query(LuongNhanVien.vi_tri_luong_id, db.func.count(db.distinct(LuongNhanVien.nguoi_dung_id)))
               .group_by(LuongNhanVien.vi_tri_luong_id).all())
    return render_template("luong_vi_tri.html", ds=ds, dem=dem)


@bp.route("/vi-tri/moi", methods=["GET", "POST"], defaults={"id": None})
@bp.route("/vi-tri/<int:id>", methods=["GET", "POST"])
def sua_vi_tri(id):
    vt = db.session.get(ViTriLuong, id) or abort(404) if id else None
    if request.method == "POST":
        ten = (request.form.get("ten") or "").strip()
        if not ten:
            flash("Nhập tên vị trí.", "error")
            return redirect(request.url)
        trung = ViTriLuong.query.filter(db.func.lower(ViTriLuong.ten) == ten.lower(),
                                        ViTriLuong.id != (vt.id if vt else -1)).first()
        if trung:
            flash(f"Đã có vị trí tên \"{trung.ten}\".", "error")
            return redirect(request.url)
        if not vt:
            vt = ViTriLuong()
            db.session.add(vt)
        vt.ten = ten
        vt.phong_ban = (request.form.get("phong_ban") or "").strip() or None
        vt.luong_co_ban = int("".join(ch for ch in request.form.get("luong_co_ban", "") if ch.isdigit()) or 0)
        vt.trach_nhiem = _ds_khoan("tn")
        vt.nang_luc = _ds_khoan("nl")
        vt.khau_tru = _ds_khoan("kt")
        db.session.commit()
        flash(f"Đã lưu vị trí {vt.ten}.", "success")
        return redirect(url_for("luong.vi_tri"))
    return render_template("luong_vi_tri_form.html", vt=vt)


@bp.route("/vi-tri/<int:id>/xoa", methods=["POST"])
def xoa_vi_tri(id):
    vt = db.session.get(ViTriLuong, id) or abort(404)
    if LuongNhanVien.query.filter_by(vi_tri_luong_id=vt.id).first():
        flash("Vị trí này đang được gán cho nhân viên, không xoá được.", "error")
        return redirect(url_for("luong.sua_vi_tri", id=vt.id))
    db.session.delete(vt)
    db.session.commit()
    flash(f"Đã xoá vị trí {vt.ten}.", "success")
    return redirect(url_for("luong.vi_tri"))


@bp.route("/vi-tri/<int:id>/json")
def tai_json(id):
    """Tải lại file preset .json (dùng được cho app Phiếu Lương cũ)."""
    vt = db.session.get(ViTriLuong, id) or abort(404)
    data = {"_meta": {"app": "BRICON Work", "created_at": datetime.utcnow().isoformat() + "Z"},
            "vi_tri": vt.ten, "phong_ban": vt.phong_ban or "", "luong_co_ban": vt.luong_co_ban,
            "luong_trach_nhiem": vt.trach_nhiem or [], "luong_nang_luc": vt.nang_luc or [],
            "khau_tru": vt.khau_tru or []}
    from io import BytesIO
    out = BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    return send_file(out, as_attachment=True, download_name=f"vi_tri_luong_{vt.id}.json",
                     mimetype="application/json")


# ---------------------------------------------------------------------------
# Vị trí lương của từng nhân viên (lịch sử theo ngày áp dụng)
# ---------------------------------------------------------------------------
@bp.route("/nhan-vien")
def ds_nhan_vien():
    hn = ngay_vn_hien_tai()
    ds = (NguoiDung.query.filter(NguoiDung.dang_hoat_dong.is_(True),
                                 NguoiDung.vai_tro.notin_((VaiTro.ADMIN,)))
          .order_by(NguoiDung.ho_ten).all())
    moc = {}
    for m in LuongNhanVien.query.order_by(LuongNhanVien.tu_ngay).all():
        moc.setdefault(m.nguoi_dung_id, []).append(m)
    dong = []
    for nd in ds:
        ls = moc.get(nd.id, [])
        hien_tai = next((m for m in reversed(ls) if m.tu_ngay <= hn), None)
        sap_toi = next((m for m in ls if m.tu_ngay > hn), None)
        dong.append({"nd": nd, "hien_tai": hien_tai, "sap_toi": sap_toi})
    dong.sort(key=lambda d: (d["hien_tai"] is not None, d["nd"].ho_ten))
    return render_template("luong_nhan_vien.html", dong=dong)


@bp.route("/nhan-vien/<int:id>", methods=["GET", "POST"])
def nhan_vien(id):
    nd = db.session.get(NguoiDung, id) or abort(404)
    if request.method == "POST":
        vt = db.session.get(ViTriLuong, request.form.get("vi_tri_luong_id", type=int) or 0)
        try:
            tu_ngay = date.fromisoformat(request.form.get("tu_ngay") or "")
        except ValueError:
            tu_ngay = None
        if not vt or not tu_ngay:
            flash("Chọn vị trí lương và ngày bắt đầu áp dụng.", "error")
            return redirect(request.url)
        m = LuongNhanVien.query.filter_by(nguoi_dung_id=nd.id, tu_ngay=tu_ngay).first()
        if m:
            m.vi_tri_luong_id = vt.id  # cùng ngày -> sửa lại vị trí thay vì báo trùng
        else:
            db.session.add(LuongNhanVien(nguoi_dung_id=nd.id, vi_tri_luong_id=vt.id, tu_ngay=tu_ngay,
                                         tao_boi_id=current_user.id,
                                         ghi_chu=(request.form.get("ghi_chu") or "").strip() or None))
        db.session.commit()
        flash(f"{nd.ho_ten}: áp dụng {vt.ten} từ {tu_ngay.strftime('%d/%m/%Y')}.", "success")
        return redirect(url_for("luong.nhan_vien", id=nd.id))

    ls = LuongNhanVien.query.filter_by(nguoi_dung_id=nd.id).order_by(LuongNhanVien.tu_ngay.desc()).all()
    hn = ngay_vn_hien_tai()
    return render_template("luong_nhan_vien_detail.html", nd=nd, ls=ls, hom_nay=hn,
                           ds_vi_tri=ViTriLuong.query.order_by(ViTriLuong.ten).all())


@bp.route("/moc/<int:id>/xoa", methods=["POST"])
def xoa_moc(id):
    m = db.session.get(LuongNhanVien, id) or abort(404)
    nd_id = m.nguoi_dung_id
    db.session.delete(m)
    db.session.commit()
    flash("Đã xoá mốc vị trí lương.", "success")
    return redirect(url_for("luong.nhan_vien", id=nd_id))
