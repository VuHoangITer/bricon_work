from datetime import datetime, date

import pytz
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db, login_manager

_MUI_GIO_VN = pytz.timezone("Asia/Ho_Chi_Minh")


def gio_vn_hien_tai() -> datetime:
    """Giờ Việt Nam hiện tại, dạng naive datetime (không kèm tzinfo) — dùng
    THỐNG NHẤT thay cho datetime.now()/datetime.utcnow() ở MỌI nơi trong hệ
    thống, để không phụ thuộc vào múi giờ hệ điều hành của server (VPS có
    thể set UTC mặc định trong khi máy dev local set giờ Việt Nam, gây lệch
    giờ giữa 2 môi trường nếu gọi trực tiếp datetime.now()/utcnow())."""
    return datetime.now(_MUI_GIO_VN).replace(tzinfo=None)


def ngay_vn_hien_tai() -> date:
    """Ngày hôm nay theo giờ Việt Nam — dùng thay cho date.today() để
    tránh lệch ngày khi server đặt múi giờ UTC (dễ sai nhất vào khung nửa
    đêm tới 6h59 sáng giờ Việt Nam, lúc UTC vẫn còn là ngày hôm trước)."""
    return gio_vn_hien_tai().date()


# --------------------------------------------------------------------------
# Hằng số
# --------------------------------------------------------------------------
class VaiTro:
    ADMIN = "admin"
    SEP = "sep"
    QUAN_LY = "quan_ly"
    NHAN_VIEN = "nhan_vien"

    NHAN = {
        ADMIN: "Quản trị",
        SEP: "Ban giám đốc",
        QUAN_LY: "Quản lý bộ phận",
        NHAN_VIEN: "Nhân viên",
    }
    # Ai được giao việc / duyệt việc
    CAP_QUAN_LY = (ADMIN, SEP, QUAN_LY)


class TrangThai:
    MOI = "moi"
    DANG_LAM = "dang_lam"
    CHO_DUYET = "cho_duyet"
    LAM_LAI = "lam_lai"
    HOAN_THANH = "hoan_thanh"
    HUY = "huy"

    NHAN = {
        MOI: "Mới giao",
        DANG_LAM: "Đang làm",
        CHO_DUYET: "Chờ duyệt",
        LAM_LAI: "Phải làm lại",
        HOAN_THANH: "Hoàn thành",
        HUY: "Đã huỷ",
    }
    CHUA_XONG = (MOI, DANG_LAM, LAM_LAI)
    DANG_MO = (MOI, DANG_LAM, LAM_LAI, CHO_DUYET)


class DoUuTien:
    THAP = "thap"
    THUONG = "thuong"
    CAO = "cao"

    # Thứ tự hiển thị trong form: Hằng ngày -> Nhiệm vụ chính -> Cần gấp
    NHAN = {
        THAP: "Hằng ngày",
        THUONG: "Nhiệm vụ chính",
        CAO: "Cần gấp",
    }
    # Số nhỏ hơn = xử lý trước. Dùng để sắp xếp danh sách công việc
    # (Cần gấp vẫn lên đầu, Hằng ngày vẫn xuống cuối — chỉ đổi tên hiển thị).
    THU_TU = {CAO: 0, THUONG: 1, THAP: 2}


class LoaiDinhKem:
    ANH = "anh"
    VIDEO = "video"
    GHI_AM = "ghi_am"
    FILE = "file"

    NHAN = {ANH: "Ảnh", VIDEO: "Video", GHI_AM: "Ghi âm", FILE: "Tệp"}


class MucSao:
    """Nhãn diễn giải cho từng mức sao đánh giá (0-5) — dùng chung khi
    hiện đánh giá cho nhân viên xem (form chấm điểm, chi tiết việc, tin
    Zalo báo đã duyệt...), để cùng 1 mức sao luôn hiểu theo đúng 1 nghĩa
    thống nhất toàn hệ thống, không mỗi chỗ diễn giải 1 kiểu."""

    NHAN = {
        0: "Không làm",
        1: "Làm cho có",
        2: "Có làm nhưng cần nhắc nhở",
        3: "Làm đầy đủ, có trách nhiệm",
        4: "Có tâm, nhiệt huyết",
        5: "Chủ động, sáng tạo, vượt mong đợi",
    }

    @staticmethod
    def nhan(so_sao: int | None) -> str:
        if so_sao is None:
            return ""
        return MucSao.NHAN.get(so_sao, "")


# --------------------------------------------------------------------------
# Bảng
# --------------------------------------------------------------------------
class BoPhan(db.Model):
    __tablename__ = "bo_phan"
    id = db.Column(db.Integer, primary_key=True)
    ten = db.Column(db.String(120), nullable=False, unique=True)
    nhan_vien = db.relationship("NguoiDung", back_populates="bo_phan")

    def __repr__(self):
        return f"<BoPhan {self.ten}>"


class BotZalo(db.Model):
    """Bot Zalo dùng chung, đặt tên để gán cho nhân viên thay vì dán token tay."""
    __tablename__ = "bot_zalo"

    id = db.Column(db.Integer, primary_key=True)
    ten = db.Column(db.String(100), nullable=False, unique=True)
    token = db.Column(db.String(255), nullable=False)
    webhook_secret = db.Column(db.String(64))  # Zalo gửi kèm header để xác thực webhook
    dang_hoat_dong = db.Column(db.Boolean, default=True, nullable=False)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    def __repr__(self):
        return f"<BotZalo {self.ten}>"


class CaiDat(db.Model):
    """Cấu hình hệ thống dạng key-value đơn giản — hiện dùng cho OpenAI API
    key, sau này có thêm cấu hình gì khác cũng để chung ở đây."""
    __tablename__ = "cai_dat"

    khoa = db.Column(db.String(50), primary_key=True)
    gia_tri = db.Column(db.Text)

    def __repr__(self):
        return f"<CaiDat {self.khoa}>"


class ChucVu(db.Model):
    """Chức vụ / vị trí công việc (nhân viên kinh doanh, nhân viên kho...)
    — thêm/sửa/xoá tự do ngay trên web, không liên quan gì tới VaiTro (đó
    là phân quyền hệ thống, còn đây thuần là thông tin tổ chức). Nội dung
    mô tả dùng để nạp riêng cho Trợ lý AI trả lời đúng theo từng vị trí."""
    __tablename__ = "chuc_vu"

    id = db.Column(db.Integer, primary_key=True)
    ten = db.Column(db.String(100), nullable=False, unique=True)
    mo_ta = db.Column(db.Text)  # mô tả công việc, lương, chế độ riêng của chức vụ này
    anh = db.Column(db.String(300))  # ảnh minh hoạ (lộ trình, sơ đồ...), không bắt buộc
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    def __repr__(self):
        return f"<ChucVu {self.ten}>"


class SanPhamAI(db.Model):
    """1 mục thông tin sản phẩm cho Trợ lý AI đọc — tên + mô tả + nhiều
    ảnh minh hoạ (TDS, bảng định mức, bảng màu...). Quản lý tự do ở trang
    Info AI (thêm/sửa/xoá ngay trên web), không hardcode trong code —
    tương tự ChucVu nhưng hỗ trợ NHIỀU ảnh/sản phẩm thay vì chỉ 1."""
    __tablename__ = "san_pham_ai"

    id = db.Column(db.Integer, primary_key=True)
    ten = db.Column(db.String(255), nullable=False)
    mo_ta = db.Column(db.Text)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    anh = db.relationship(
        "AnhSanPhamAI", back_populates="san_pham",
        cascade="all, delete-orphan", order_by="AnhSanPhamAI.id"
    )

    def __repr__(self):
        return f"<SanPhamAI {self.ten}>"


class AnhSanPhamAI(db.Model):
    """1 ảnh minh hoạ gắn với 1 SanPhamAI — 1 sản phẩm có thể có nhiều
    ảnh (VD: TDS, bảng định mức, bảng màu), mỗi ảnh có nhãn riêng để AI
    phân biệt đúng loại ảnh khi người hỏi cần."""
    __tablename__ = "anh_san_pham_ai"

    id = db.Column(db.Integer, primary_key=True)
    san_pham_id = db.Column(db.Integer, db.ForeignKey("san_pham_ai.id"), nullable=False, index=True)
    duong_dan = db.Column(db.String(500), nullable=False)
    nhan = db.Column(db.String(255))  # VD: "TDS", "Bảng định mức", "Bảng màu"

    san_pham = db.relationship("SanPhamAI", back_populates="anh")

    def __repr__(self):
        return f"<AnhSanPhamAI {self.nhan or self.duong_dan}>"


class NguoiDung(UserMixin, db.Model):
    __tablename__ = "nguoi_dung"

    id = db.Column(db.Integer, primary_key=True)
    ma_dinh_danh = db.Column(db.String(50), unique=True, nullable=False, index=True)
    ho_ten = db.Column(db.String(120), nullable=False)
    mat_khau_hash = db.Column(db.String(255), nullable=False)
    vai_tro = db.Column(db.String(20), nullable=False, default=VaiTro.NHAN_VIEN)
    bo_phan_id = db.Column(db.Integer, db.ForeignKey("bo_phan.id"))
    chuc_vu_id = db.Column(db.Integer, db.ForeignKey("chuc_vu.id"))
    so_dien_thoai = db.Column(db.String(20))

    # Zalo
    zalo_group_id = db.Column(db.String(120))
    bot_zalo_id = db.Column(db.Integer, db.ForeignKey("bot_zalo.id"))

    dang_hoat_dong = db.Column(db.Boolean, default=True, nullable=False)
    doi_mat_khau = db.Column(db.Boolean, default=True, nullable=False)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    bo_phan = db.relationship("BoPhan", back_populates="nhan_vien")
    chuc_vu = db.relationship("ChucVu")
    bot_zalo = db.relationship("BotZalo")

    # ---- mật khẩu ----
    def dat_mat_khau(self, mat_khau: str):
        self.mat_khau_hash = generate_password_hash(mat_khau)

    def kiem_mat_khau(self, mat_khau: str) -> bool:
        return check_password_hash(self.mat_khau_hash, mat_khau)

    # ---- quyền ----
    @property
    def la_quan_ly(self) -> bool:
        return self.vai_tro in VaiTro.CAP_QUAN_LY

    @property
    def xem_toan_cong_ty(self) -> bool:
        return self.vai_tro in (VaiTro.ADMIN, VaiTro.SEP)

    @property
    def la_admin_sep(self) -> bool:
        """Chỉ Admin/Ban giám đốc — được mở lại và xoá vĩnh viễn công việc."""
        return self.vai_tro in (VaiTro.ADMIN, VaiTro.SEP)

    @property
    def la_admin_thuan(self) -> bool:
        """Chỉ đúng role Quản trị (Admin) — kể cả Ban giám đốc cũng không có
        quyền này. Dùng riêng cho xoá vĩnh viễn nhân viên + toàn bộ dữ liệu."""
        return self.vai_tro == VaiTro.ADMIN

    def duoc_xem_viec(self, viec: "CongViec") -> bool:
        if self.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
            return True
        if viec.nguoi_nhan_id == self.id or viec.nguoi_giao_id == self.id:
            return True
        if self.vai_tro == VaiTro.QUAN_LY and self.bo_phan_id:
            return viec.nguoi_nhan and viec.nguoi_nhan.bo_phan_id == self.bo_phan_id
        return False

    def duoc_duyet_viec(self, viec: "CongViec") -> bool:
        if self.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
            return True
        if viec.nguoi_giao_id == self.id:
            return True
        if self.vai_tro == VaiTro.QUAN_LY and self.bo_phan_id:
            return viec.nguoi_nhan and viec.nguoi_nhan.bo_phan_id == self.bo_phan_id
        return False

    @property
    def ten_vai_tro(self):
        return VaiTro.NHAN.get(self.vai_tro, self.vai_tro)

    def __repr__(self):
        return f"<NguoiDung {self.ma_dinh_danh} {self.ho_ten}>"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(NguoiDung, int(user_id))


class CongViec(db.Model):
    __tablename__ = "cong_viec"

    id = db.Column(db.Integer, primary_key=True)
    ma = db.Column(db.String(20), unique=True, index=True)  # V1001, gán sau khi insert
    tieu_de = db.Column(db.String(255), nullable=False)
    mo_ta = db.Column(db.Text)

    nguoi_giao_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False)
    nguoi_nhan_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False, index=True)

    han = db.Column(db.DateTime, index=True)
    ngay_bat_dau = db.Column(db.Date)  # không bắt buộc; có set thì việc hiện
                                       # trong "Việc hằng ngày" suốt từ ngày
                                       # này tới ngày của han, không chỉ đúng
                                       # ngày han như mặc định
    do_uu_tien = db.Column(db.String(10), default="thuong")  # thap / thuong / cao
    trang_thai = db.Column(db.String(20), default=TrangThai.MOI, nullable=False, index=True)
    lan_gui = db.Column(db.Integer, default=1, nullable=False)

    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai, index=True)
    mo_lan_dau_luc = db.Column(db.DateTime)
    gui_doi_chung_luc = db.Column(db.DateTime)
    hoan_thanh_luc = db.Column(db.DateTime, index=True)

    so_sao_cuoi = db.Column(db.Integer)  # sao của lần duyệt cuối, để xếp KPI nhanh
    da_nhac_sap_qua_han = db.Column(db.Boolean, default=False, nullable=False)  # tránh nhắc lặp lại

    nguoi_giao = db.relationship("NguoiDung", foreign_keys=[nguoi_giao_id])
    nguoi_nhan = db.relationship("NguoiDung", foreign_keys=[nguoi_nhan_id])
    dinh_kem = db.relationship(
        "DinhKem", back_populates="cong_viec", cascade="all, delete-orphan",
        order_by="DinhKem.id"
    )
    danh_gia = db.relationship(
        "DanhGia", back_populates="cong_viec", cascade="all, delete-orphan",
        order_by="DanhGia.id"
    )
    ghi_chu_nop = db.relationship(
        "GhiChuNop", back_populates="cong_viec", cascade="all, delete-orphan",
        order_by="GhiChuNop.id"
    )

    # ---- tiện ích ----
    @property
    def ten_trang_thai(self):
        return TrangThai.NHAN.get(self.trang_thai, self.trang_thai)

    @property
    def ten_uu_tien(self):
        return DoUuTien.NHAN.get(self.do_uu_tien, self.do_uu_tien)

    @property
    def qua_han(self) -> bool:
        # Đã nộp đối chứng (đang chờ duyệt) thì không còn tính là "quá hạn"
        # nữa — nhân viên đã nộp đúng lúc, sếp duyệt trễ không phải lỗi của
        # họ. Chỉ coi là quá hạn khi vẫn còn ở trạng thái CHƯA nộp gì.
        if not self.han or self.trang_thai in (
            TrangThai.HOAN_THANH, TrangThai.HUY, TrangThai.CHO_DUYET
        ):
            return False
        return gio_vn_hien_tai() > self.han

    @property
    def link(self) -> str:
        from flask import current_app
        return f"{current_app.config['BASE_URL']}/viec/{self.id}"

    def dinh_kem_theo_lan(self, lan: int):
        return [d for d in self.dinh_kem if d.lan_gui == lan]

    def danh_gia_theo_lan(self, lan: int):
        return next((d for d in self.danh_gia if d.lan_gui == lan), None)

    def ghi_chu_theo_lan(self, lan: int):
        return next((g for g in self.ghi_chu_nop if g.lan_gui == lan), None)

    @property
    def cac_lan(self):
        """Trả về [1..lan_gui] để dựng dòng thời gian nộp/duyệt."""
        return list(range(1, self.lan_gui + 1))

    def __repr__(self):
        return f"<CongViec {self.ma} {self.trang_thai}>"


class DinhKem(db.Model):
    __tablename__ = "dinh_kem"

    id = db.Column(db.Integer, primary_key=True)
    cong_viec_id = db.Column(db.Integer, db.ForeignKey("cong_viec.id"), nullable=False, index=True)
    nguoi_tai_len_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False)
    lan_gui = db.Column(db.Integer, default=1, nullable=False)

    loai = db.Column(db.String(20), nullable=False)
    duong_dan = db.Column(db.String(300), nullable=False)  # tương đối so với UPLOAD_ROOT
    ten_goc = db.Column(db.String(255))
    kich_thuoc = db.Column(db.Integer)
    mime = db.Column(db.String(100))
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    cong_viec = db.relationship("CongViec", back_populates="dinh_kem")
    nguoi_tai_len = db.relationship("NguoiDung")

    @property
    def kich_thuoc_dep(self):
        n = self.kich_thuoc or 0
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.0f} KB"
        return f"{n / 1048576:.1f} MB"


class DanhGia(db.Model):
    __tablename__ = "danh_gia"

    id = db.Column(db.Integer, primary_key=True)
    cong_viec_id = db.Column(db.Integer, db.ForeignKey("cong_viec.id"), nullable=False, index=True)
    nguoi_danh_gia_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False)
    lan_gui = db.Column(db.Integer, nullable=False)

    ket_qua = db.Column(db.String(20), nullable=False)  # dat / lam_lai
    so_sao = db.Column(db.Integer)  # chỉ có khi ket_qua = dat
    ghi_chu = db.Column(db.Text)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    cong_viec = db.relationship("CongViec", back_populates="danh_gia")
    nguoi_danh_gia = db.relationship("NguoiDung")
    anh = db.relationship("AnhDanhGia", back_populates="danh_gia",
                          cascade="all, delete-orphan", order_by="AnhDanhGia.id")


class AnhDanhGia(db.Model):
    """Ảnh sếp đính kèm khi đánh giá (VD: chụp minh hoạ chỗ cần sửa, ảnh
    mẫu tham khảo...) — có thể đính kèm nhiều ảnh cùng lúc, gắn với đúng
    lần đánh giá đó, hiện lại trong khung "Đối chứng & đánh giá"."""
    __tablename__ = "anh_danh_gia"

    id = db.Column(db.Integer, primary_key=True)
    danh_gia_id = db.Column(db.Integer, db.ForeignKey("danh_gia.id"), nullable=False, index=True)
    duong_dan = db.Column(db.String(300), nullable=False)  # tương đối so với UPLOAD_ROOT
    ten_goc = db.Column(db.String(255))
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    danh_gia = db.relationship("DanhGia", back_populates="anh")


class GhiChuNop(db.Model):
    """Ghi chú kết quả mà nhân viên gõ khi gửi đối chứng, tách riêng theo
    từng lần nộp (lan_gui) — KHÔNG còn nối vào CongViec.mo_ta như trước
    (nối vào mô tả làm mất luôn mô tả gốc + ghi chú lần nộp bị tách rời
    khỏi đúng lần nộp của nó trong khung "Đối chứng & đánh giá")."""
    __tablename__ = "ghi_chu_nop"
    __table_args__ = (
        db.UniqueConstraint("cong_viec_id", "lan_gui", name="uq_ghichunop_lan"),
    )

    id = db.Column(db.Integer, primary_key=True)
    cong_viec_id = db.Column(db.Integer, db.ForeignKey("cong_viec.id"), nullable=False, index=True)
    lan_gui = db.Column(db.Integer, nullable=False)
    noi_dung = db.Column(db.Text, nullable=False)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    cong_viec = db.relationship("CongViec", back_populates="ghi_chu_nop")

    def __repr__(self):
        return f"<GhiChuNop viec={self.cong_viec_id} lan={self.lan_gui}>"


class DiemChamCong(db.Model):
    __tablename__ = "diem_cham_cong"

    id = db.Column(db.Integer, primary_key=True)
    ten = db.Column(db.String(150), nullable=False)
    dia_chi = db.Column(db.String(255))
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    ban_kinh_m = db.Column(db.Integer, default=150, nullable=False)
    dang_hoat_dong = db.Column(db.Boolean, default=True, nullable=False)


class ChamCong(db.Model):
    __tablename__ = "cham_cong"
    __table_args__ = (db.UniqueConstraint("nguoi_dung_id", "ngay", name="uq_chamcong_ngay"),)

    id = db.Column(db.Integer, primary_key=True)
    nguoi_dung_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False, index=True)
    ngay = db.Column(db.Date, nullable=False, default=ngay_vn_hien_tai, index=True)

    gio_vao = db.Column(db.DateTime)
    lat_vao = db.Column(db.Float)
    lng_vao = db.Column(db.Float)
    do_chinh_xac_vao = db.Column(db.Float)
    diem_vao_id = db.Column(db.Integer, db.ForeignKey("diem_cham_cong.id"))
    khoang_cach_vao = db.Column(db.Integer)

    gio_ra = db.Column(db.DateTime)
    lat_ra = db.Column(db.Float)
    lng_ra = db.Column(db.Float)
    do_chinh_xac_ra = db.Column(db.Float)
    diem_ra_id = db.Column(db.Integer, db.ForeignKey("diem_cham_cong.id"))
    khoang_cach_ra = db.Column(db.Integer)

    di_tre = db.Column(db.Boolean, default=False)
    so_phut_tre = db.Column(db.Integer, default=0)
    ve_som = db.Column(db.Boolean, default=False)
    so_phut_som = db.Column(db.Integer, default=0)
    nghi_khong_phep = db.Column(db.Boolean, default=False)

    ip = db.Column(db.String(60))
    user_agent = db.Column(db.String(300))
    nghi_ngo = db.Column(db.Boolean, default=False)
    ghi_chu = db.Column(db.Text)

    nguoi_dung = db.relationship("NguoiDung")
    diem_vao = db.relationship("DiemChamCong", foreign_keys=[diem_vao_id])
    diem_ra = db.relationship("DiemChamCong", foreign_keys=[diem_ra_id])


class LogZalo(db.Model):
    __tablename__ = "log_zalo"

    id = db.Column(db.Integer, primary_key=True)
    nguoi_dung_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"))
    cong_viec_id = db.Column(db.Integer, db.ForeignKey("cong_viec.id"))
    chat_id = db.Column(db.String(120))
    noi_dung = db.Column(db.Text)
    thanh_cong = db.Column(db.Boolean, default=False)
    phan_hoi = db.Column(db.Text)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai, index=True)


class BuoiNghi:
    SANG = "sang"
    CHIEU = "chieu"
    CA_NGAY = "ca_ngay"

    NHAN = {
        SANG: "Buổi sáng",
        CHIEU: "Buổi chiều",
        CA_NGAY: "Cả ngày",
    }
    # Số ngày công tương ứng — dùng khi tính lại ngày công kết hợp nghỉ phép.
    SO_NGAY = {SANG: 0.5, CHIEU: 0.5, CA_NGAY: 1.0}


class XinNghi(db.Model):
    """Nghỉ phép có đơn PDF ký điện tử (chữ ký vẽ tay trên form, hệ thống
    tự render thành đơn xin nghỉ đã điền sẵn thông tin + chữ ký) — có đơn
    là coi như duyệt luôn, không qua bước duyệt tay nào nữa."""
    __tablename__ = "xin_nghi"
    __table_args__ = (
        db.UniqueConstraint("nguoi_dung_id", "ngay", "buoi", name="uq_xinnghi_ngay_buoi"),
    )

    id = db.Column(db.Integer, primary_key=True)
    nguoi_dung_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False, index=True)
    ngay = db.Column(db.Date, nullable=False, index=True)
    buoi = db.Column(db.String(10), nullable=False, default=BuoiNghi.CA_NGAY)
    # Đường dẫn file đơn xin nghỉ — trước đây là ảnh giấy phép nhân viên tự
    # upload, nay là PDF hệ thống tự sinh (điền sẵn thông tin + chữ ký điện
    # tử của nhân viên), tên cột giữ nguyên để không phải xáo trộn dữ liệu
    # cũ đã lưu.
    anh_minh_chung = db.Column(db.String(300), nullable=False)
    ghi_chu = db.Column(db.String(255))  # lý do nghỉ — bắt buộc nhập ở form,
                                          # cột vẫn để nullable để không vỡ
                                          # dữ liệu cũ (trước đây tuỳ chọn)
    ban_giao_cho_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"))  # không bắt buộc
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    nguoi_dung = db.relationship("NguoiDung", foreign_keys=[nguoi_dung_id])
    ban_giao_cho = db.relationship("NguoiDung", foreign_keys=[ban_giao_cho_id])

    @property
    def ten_buoi(self):
        return BuoiNghi.NHAN.get(self.buoi, self.buoi)

    def __repr__(self):
        return f"<XinNghi {self.nguoi_dung_id} {self.ngay} {self.buoi}>"


class TroLySuDung(db.Model):
    """Theo dõi mức dùng Trợ lý AI của từng người, TÍNH THEO NGÀY — dùng để
    áp giới hạn số câu hỏi + số token cho Nhân viên/Quản lý bộ phận (Sếp/
    Admin không giới hạn), tránh tốn hết ngân sách API vì hỏi tràn lan.
    Mỗi người mỗi ngày chỉ có đúng 1 dòng, cộng dồn dần trong ngày."""
    __tablename__ = "tro_ly_su_dung"
    __table_args__ = (
        db.UniqueConstraint("nguoi_dung_id", "ngay", name="uq_trolysudung_nguoi_ngay"),
    )

    id = db.Column(db.Integer, primary_key=True)
    nguoi_dung_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False, index=True)
    ngay = db.Column(db.Date, nullable=False, index=True)
    so_cau_hoi = db.Column(db.Integer, nullable=False, default=0)
    so_token = db.Column(db.Integer, nullable=False, default=0)

    nguoi_dung = db.relationship("NguoiDung")

    def __repr__(self):
        return f"<TroLySuDung {self.nguoi_dung_id} {self.ngay} cau_hoi={self.so_cau_hoi} token={self.so_token}>"