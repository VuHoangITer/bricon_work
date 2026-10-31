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


class LoaiDeXuat:
    TAM_UNG = "tam_ung"
    CONG_VIEC = "cong_viec"

    NHAN = {TAM_UNG: "Tạm ứng", CONG_VIEC: "Công việc"}


class TrangThaiDeXuat:
    CHO_DUYET = "cho_duyet"
    DA_DUYET = "da_duyet"
    TU_CHOI = "tu_choi"

    NHAN = {CHO_DUYET: "Chờ duyệt", DA_DUYET: "Đã duyệt", TU_CHOI: "Từ chối"}


class TrangThaiBienBan:
    CHO_KY = "cho_ky"        # đã lập, chờ nhân viên ký xác nhận
    DA_KY = "da_ky"          # nhân viên đã ký
    KHONG_KY = "khong_ky"    # nhân viên không ký — người lập ghi nhận, vẫn có hiệu lực
    DA_HUY = "da_huy"

    NHAN = {CHO_KY: "Chờ ký", DA_KY: "Đã ký", KHONG_KY: "NV không ký", DA_HUY: "Đã huỷ"}
    CO_HIEU_LUC = (DA_KY, KHONG_KY)  # được tính vào tổng tiền phạt


# --------------------------------------------------------------------------
# Bảng
# --------------------------------------------------------------------------
# Bộ phận PHỤ của nhân viên (ngoài bộ phận chính nguoi_dung.bo_phan_id) —
# VD 1 trưởng bộ phận quản lý cả Kho lẫn Kinh doanh. Phạm vi quản lý tính
# theo TẤT CẢ bộ phận (chính + phụ); chấm công/hiển thị vẫn theo bộ phận chính.
nguoi_dung_bo_phan = db.Table(
    "nguoi_dung_bo_phan",
    db.Column("nguoi_dung_id", db.Integer, db.ForeignKey("nguoi_dung.id", ondelete="CASCADE"),
              primary_key=True),
    db.Column("bo_phan_id", db.Integer, db.ForeignKey("bo_phan.id", ondelete="CASCADE"),
              primary_key=True),
)


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
    link_moi = db.Column(db.String(500))  # link mời bot vào nhóm — copy quăng vào group cho nhân viên mới
    owner = db.Column(db.String(120))  # tên người là owner của bot này (bot chỉ vào được nhóm khi owner có mặt)
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

    # Hồ sơ nhân sự
    ngay_vao_lam = db.Column(db.Date)
    ngay_sinh = db.Column(db.Date)
    anh_dai_dien = db.Column(db.String(300))  # ảnh vuông đã cắt, tương đối so với UPLOAD_ROOT
    # Quyền riêng: được vào Quản trị → Trợ lý AI để nạp kiến thức cho bot
    # (mọi tab trừ Thống kê sử dụng) dù không phải Ban giám đốc/Quản trị.
    nap_kien_thuc_ai = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())

    bo_phan = db.relationship("BoPhan", back_populates="nhan_vien")
    ho_so = db.relationship("HoSoNhanVien", back_populates="nguoi_dung",
                            foreign_keys="HoSoNhanVien.nguoi_dung_id",
                            cascade="all, delete-orphan", order_by="HoSoNhanVien.tao_luc.desc()")
    chuc_vu = db.relationship("ChucVu")
    bot_zalo = db.relationship("BotZalo")
    bo_phan_phu = db.relationship("BoPhan", secondary=nguoi_dung_bo_phan, order_by="BoPhan.ten")

    # ---- bộ phận ----
    @property
    def ds_bo_phan_id(self) -> set[int]:
        """Mọi bộ phận người này thuộc về: bộ phận chính + bộ phận phụ."""
        ids = {b.id for b in self.bo_phan_phu}
        if self.bo_phan_id:
            ids.add(self.bo_phan_id)
        return ids

    @property
    def ten_cac_bo_phan(self) -> str:
        """VD "Kinh doanh, Kho" — bộ phận chính đứng đầu."""
        ten = [self.bo_phan.ten] if self.bo_phan else []
        ten += [b.ten for b in self.bo_phan_phu if b.id != self.bo_phan_id]
        return ", ".join(ten)

    def cung_bo_phan(self, nd: "NguoiDung | None") -> bool:
        """self và nd có chung ít nhất 1 bộ phận (chính hoặc phụ) không."""
        return bool(nd is not None and self.ds_bo_phan_id & nd.ds_bo_phan_id)

    @classmethod
    def thuoc_bo_phan(cls, ids):
        """Điều kiện SQL: nhân viên thuộc 1 trong các bộ phận ids (chính hoặc phụ)."""
        ids = list(ids)
        if not ids:
            return db.false()
        phu = db.select(nguoi_dung_bo_phan.c.nguoi_dung_id).where(nguoi_dung_bo_phan.c.bo_phan_id.in_(ids))
        return db.or_(cls.bo_phan_id.in_(ids), cls.id.in_(phu))

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
    def duoc_nap_ai(self) -> bool:
        """Được nạp kiến thức Trợ lý AI: Ban giám đốc, Quản trị, hoặc người
        được tick quyền riêng ở trang Nhân sự."""
        return self.vai_tro in (VaiTro.ADMIN, VaiTro.SEP) or bool(self.nap_kien_thuc_ai)

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
        if self.vai_tro == VaiTro.QUAN_LY and self.ds_bo_phan_id:
            return self.cung_bo_phan(viec.nguoi_nhan)
        return False

    def duoc_duyet_viec(self, viec: "CongViec") -> bool:
        if self.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
            # Không tự duyệt việc của chính mình — chỉ nảy sinh từ khi Sếp
            # có thể được giao việc bởi Sếp khác; Admin/Sếp khác vẫn duyệt
            # được bình thường.
            return viec.nguoi_nhan_id != self.id
        if viec.nguoi_giao_id == self.id:
            return True
        if self.vai_tro == VaiTro.QUAN_LY and self.ds_bo_phan_id:
            return self.cung_bo_phan(viec.nguoi_nhan)
        return False

    def trong_pham_vi(self, nd: "NguoiDung | None") -> bool:
        """nd có nằm trong phạm vi quản lý của self không: Admin/Ban giám đốc
        = toàn công ty; Quản lý bộ phận = chính mình + người có CHUNG ít nhất 1 bộ phận
        (chính hoặc phụ);
        còn lại = chỉ chính mình."""
        if nd is None:
            return False
        if nd.id == self.id or self.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
            return True
        if self.vai_tro == VaiTro.QUAN_LY and self.ds_bo_phan_id:
            return self.cung_bo_phan(nd)
        return False

    def duoc_mo_lai_viec(self, viec: "CongViec") -> bool:
        """Ai mở lại được việc bị hệ thống tự đóng (0 sao): người duyệt được
        việc đó, CỘNG THÊM Admin/Ban giám đốc với việc tự giao cho chính
        mình (duoc_duyet_viec chặn tự duyệt nên trước đây họ kẹt, không mở
        lại được việc của chính họ)."""
        if self.duoc_duyet_viec(viec):
            return True
        return (self.vai_tro in (VaiTro.ADMIN, VaiTro.SEP)
                and viec.nguoi_nhan_id == self.id and viec.nguoi_giao_id == self.id)

    def duoc_duyet_de_xuat(self, dx: "DeXuat") -> bool:
        """Ai duyệt được 1 Đề xuất: Sếp/Admin (trừ đề xuất của chính mình)
        hoặc Quản lý bộ phận của người đề xuất — cùng công thức với
        duoc_duyet_viec, chỉ khác là chặn tự duyệt ngay từ đầu vì Đề xuất
        không có nhánh "nguoi_giao_id == self.id" (không ai tự giao đề
        xuất cho mình)."""
        if dx.nguoi_de_xuat_id == self.id:
            return False
        if self.vai_tro in (VaiTro.ADMIN, VaiTro.SEP):
            return True
        if self.vai_tro == VaiTro.QUAN_LY and self.ds_bo_phan_id:
            return self.cung_bo_phan(dx.nguoi_de_xuat)
        return False

    @property
    def ten_vai_tro(self):
        return VaiTro.NHAN.get(self.vai_tro, self.vai_tro)

    def ho_so_theo_loai(self, loai: str) -> list:
        return [h for h in self.ho_so if h.loai == loai]

    @property
    def tham_nien(self) -> str | None:
        """"2 năm 3 tháng" tính từ ngày vào làm tới hôm nay."""
        if not self.ngay_vao_lam:
            return None
        hn = ngay_vn_hien_tai()
        thang = (hn.year - self.ngay_vao_lam.year) * 12 + hn.month - self.ngay_vao_lam.month
        if hn.day < self.ngay_vao_lam.day:
            thang -= 1
        if thang < 1:
            return "dưới 1 tháng"
        nam, thang = divmod(thang, 12)
        return " ".join(p for p in (f"{nam} năm" if nam else "", f"{thang} tháng" if thang else "") if p)

    @property
    def sinh_nhat_thang_nay(self) -> bool:
        return bool(self.ngay_sinh and self.ngay_sinh.month == ngay_vn_hien_tai().month)

    def __repr__(self):
        return f"<NguoiDung {self.ma_dinh_danh} {self.ho_ten}>"


@login_manager.user_loader
def load_user(user_id):
    """Flask-Login gọi hàm này ở MỌI request (kể cả khôi phục từ remember-
    cookie, không chỉ lúc đăng nhập) để xác định current_user. Bắt buộc
    phải kiểm tra dang_hoat_dong ngay tại đây — nếu chỉ chặn lúc đăng nhập
    (views/auth.py) thì 1 người ĐANG đăng nhập sẵn (session/remember-cookie
    còn hạn tới 90 ngày, xem config.py) vẫn tiếp tục dùng được app bình
    thường sau khi bị khoá tài khoản, coi như khoá không có tác dụng gì.
    Trả về None ở đây khiến Flask-Login coi phiên đó là chưa đăng nhập,
    buộc phải đăng nhập lại ngay ở request tiếp theo — và sẽ bị chặn luôn
    tại bước đăng nhập vì dang_hoat_dong vẫn đang False."""
    nd = db.session.get(NguoiDung, int(user_id))
    if nd and not nd.dang_hoat_dong:
        return None
    return nd


class LoaiHoSo:
    HOP_DONG = "hop_dong"
    BAN_GIAO = "ban_giao"
    CAM_KET = "cam_ket"

    NHAN = {
        HOP_DONG: "Hợp đồng lao động",
        BAN_GIAO: "Giấy bàn giao tài sản",
        CAM_KET: "Cam kết",
    }
    ICON = {HOP_DONG: "📄", BAN_GIAO: "📦", CAM_KET: "✍️"}


class HoSoNhanVien(db.Model):
    """1 file giấy tờ nhân sự (scan/ảnh/PDF) của 1 nhân viên — hợp đồng lao
    động, giấy bàn giao tài sản, cam kết. Mỗi loại có thể nhiều file (VD
    hợp đồng thử việc + chính thức, phụ lục). Chỉ Admin/Ban giám đốc xem
    và quản lý (dữ liệu nhạy cảm: lương, điều khoản)."""
    __tablename__ = "ho_so_nhan_vien"

    id = db.Column(db.Integer, primary_key=True)
    nguoi_dung_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False, index=True)
    loai = db.Column(db.String(20), nullable=False, index=True)
    duong_dan = db.Column(db.String(300), nullable=False)  # tương đối so với UPLOAD_ROOT
    ten_goc = db.Column(db.String(255))
    kich_thuoc = db.Column(db.Integer)
    mime = db.Column(db.String(100))
    ghi_chu = db.Column(db.String(255))
    ngay_ky = db.Column(db.Date)
    ngay_het_han = db.Column(db.Date)  # chủ yếu cho hợp đồng có thời hạn
    nguoi_tai_len_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"))
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    nguoi_dung = db.relationship("NguoiDung", back_populates="ho_so", foreign_keys=[nguoi_dung_id])
    nguoi_tai_len = db.relationship("NguoiDung", foreign_keys=[nguoi_tai_len_id])

    @property
    def ten_loai(self):
        return LoaiHoSo.NHAN.get(self.loai, self.loai)

    @property
    def so_ngay_con_han(self) -> int | None:
        if not self.ngay_het_han:
            return None
        return (self.ngay_het_han - ngay_vn_hien_tai()).days

    @property
    def kich_thuoc_dep(self):
        n = self.kich_thuoc or 0
        if n < 1024 * 1024:
            return f"{max(1, n // 1024)} KB"
        return f"{n / 1048576:.1f} MB"


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
    # Việc được hệ thống tự tạo từ mẫu Việc định kỳ (VD việc cố định hằng
    # ngày của kho) — dùng để không tạo trùng trong cùng 1 ngày.
    viec_dinh_ky_id = db.Column(db.Integer, db.ForeignKey("viec_dinh_ky.id", ondelete="SET NULL"), index=True)
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
    anh_yeu_cau = db.relationship(
        "AnhYeuCau", back_populates="cong_viec", cascade="all, delete-orphan",
        order_by="AnhYeuCau.id"
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

    @property
    def lich_su_danh_gia_lai(self):
        """Các lần Admin/Sếp/Quản lý bộ phận sửa lại số sao sau khi việc đã
        Hoàn thành (VD phát hiện vấn đề lúc bàn giao thực tế) — hiện riêng,
        không lẫn vào dòng thời gian nộp/duyệt theo từng lần gửi ở trên."""
        return [d for d in self.danh_gia if d.ket_qua == "danh_gia_lai"]

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


class AnhYeuCau(db.Model):
    """Ảnh người giao đính kèm minh hoạ ngay lúc giao việc (VD: chụp mẫu,
    sơ đồ, vị trí cần làm...) — khác hẳn đối chứng (DinhKem, do NHÂN VIÊN
    nộp lúc hoàn thành): đây là ảnh THAM KHẢO đi kèm yêu cầu chi tiết, hiện
    ngay trong phần mô tả việc, không nằm trong dòng thời gian nộp/duyệt."""
    __tablename__ = "anh_yeu_cau"

    id = db.Column(db.Integer, primary_key=True)
    cong_viec_id = db.Column(db.Integer, db.ForeignKey("cong_viec.id"), nullable=False, index=True)
    duong_dan = db.Column(db.String(300), nullable=False)  # tương đối so với UPLOAD_ROOT
    ten_goc = db.Column(db.String(255))
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    cong_viec = db.relationship("CongViec", back_populates="anh_yeu_cau")


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


class NgayNghiLe(db.Model):
    """1 ngày nghỉ lễ/Tết cụ thể (VD Quốc khánh 2/9) — thêm/xoá tự do trên
    web, không hardcode. Đúng ngày này: không cần chấm công (không tính
    nghỉ không phép), không giao được việc có hạn rơi đúng ngày này, và
    mọi tin nhắc/bản tin Zalo tự động trong ngày đều tạm ngưng. Chủ nhật
    hằng tuần xử lý riêng qua cài đặt NGHI_CHU_NHAT (xem services.la_ngay_nghi),
    không lưu thành hàng trong bảng này vì lặp lại vô hạn."""
    __tablename__ = "ngay_nghi_le"

    id = db.Column(db.Integer, primary_key=True)
    ngay = db.Column(db.Date, nullable=False, unique=True, index=True)
    ten = db.Column(db.String(255), nullable=False)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    def __repr__(self):
        return f"<NgayNghiLe {self.ngay} {self.ten}>"


class ThongBao(db.Model):
    """Thông báo do sếp/quản lý CHỦ ĐỘNG soạn và gửi tới toàn bộ nhân viên
    qua Zalo (VD: báo lương trễ, nghỉ đột xuất...) — khác hẳn các tin Zalo
    do hệ thống TỰ ĐỘNG gửi (nhắc việc, kết quả duyệt...): đây là nội dung
    tự do do người quản lý gõ ra, không gắn với 1 công việc cụ thể nào."""
    __tablename__ = "thong_bao"

    id = db.Column(db.Integer, primary_key=True)
    noi_dung = db.Column(db.Text, nullable=False)
    nguoi_dang_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False)
    so_nguoi_nhan = db.Column(db.Integer, default=0)
    # Ảnh chụp danh sách người nhận lúc gửi (JSON [{"id","ten","ma","ok"}]) —
    # lưu tên luôn để vẫn đọc được kể cả khi nhân viên đó bị xoá sau này.
    # ok=False: gửi Zalo không thành công (chưa có Zalo / lỗi bot).
    ds_nguoi_nhan = db.Column(db.Text)
    gui_tat_ca = db.Column(db.Boolean, default=False)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    nguoi_dang = db.relationship("NguoiDung")

    @property
    def nguoi_nhan(self) -> list[dict]:
        import json
        try:
            ds = json.loads(self.ds_nguoi_nhan or "[]")
            return ds if isinstance(ds, list) else []
        except ValueError:
            return []

    @property
    def nguoi_nhan_loi(self) -> list[dict]:
        return [n for n in self.nguoi_nhan if not n.get("ok", True)]

    def __repr__(self):
        return f"<ThongBao {self.id} boi={self.nguoi_dang_id}>"


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


class DeXuat(db.Model):
    """Đề xuất của nhân viên (tạm ứng lương / công việc) — có PDF ký điện
    tử của CẢ 2 BÊN: người đề xuất ký lúc nộp, người duyệt ghi ý kiến + ký
    lúc duyệt. Khác XinNghi (nộp là coi như duyệt luôn): đây là quy trình
    2 bước thật, PDF được sinh lại (tao_pdf_de_xuat) sau khi duyệt để in
    đủ cả ý kiến + chữ ký người duyệt."""
    __tablename__ = "de_xuat"

    id = db.Column(db.Integer, primary_key=True)
    loai = db.Column(db.String(20), nullable=False)
    nguoi_de_xuat_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False, index=True)
    noi_dung = db.Column(db.Text, nullable=False)
    chi_phi_du_kien = db.Column(db.Numeric(14, 0))  # VNĐ, để trống nếu không có
    duong_dan_chu_ky_de_xuat = db.Column(db.String(300), nullable=False)  # ảnh chữ ký đã cắt, lưu file riêng để in lại PDF khi duyệt
    duong_dan_pdf = db.Column(db.String(300), nullable=False)

    trang_thai = db.Column(db.String(20), nullable=False, default=TrangThaiDeXuat.CHO_DUYET)
    nguoi_duyet_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"))
    y_kien_duyet = db.Column(db.Text)
    duong_dan_chu_ky_duyet = db.Column(db.String(300))
    duyet_luc = db.Column(db.DateTime)

    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    nguoi_de_xuat = db.relationship("NguoiDung", foreign_keys=[nguoi_de_xuat_id])
    nguoi_duyet = db.relationship("NguoiDung", foreign_keys=[nguoi_duyet_id])
    dinh_kem = db.relationship(
        "DinhKemDeXuat", back_populates="de_xuat", cascade="all, delete-orphan",
        order_by="DinhKemDeXuat.id"
    )

    @property
    def ten_loai(self):
        return LoaiDeXuat.NHAN.get(self.loai, self.loai)

    @property
    def ten_trang_thai(self):
        return TrangThaiDeXuat.NHAN.get(self.trang_thai, self.trang_thai)

    @property
    def dinh_kem_de_xuat(self):
        """Ảnh/tệp người đề xuất đính kèm lúc gửi."""
        return [d for d in self.dinh_kem if d.giai_doan == GiaiDoanDinhKemDeXuat.DE_XUAT]

    @property
    def dinh_kem_duyet(self):
        """Ảnh/tệp người duyệt đính kèm lúc duyệt."""
        return [d for d in self.dinh_kem if d.giai_doan == GiaiDoanDinhKemDeXuat.DUYET]

    def __repr__(self):
        return f"<DeXuat {self.id} {self.loai} boi={self.nguoi_de_xuat_id}>"


class GiaiDoanDinhKemDeXuat:
    """Đính kèm của Đề xuất có thể đến từ 1 trong 2 phía — tách theo giai
    đoạn để hiện đúng phần của ai trong trang chi tiết, không cần 2 bảng
    riêng cho mỗi phía."""
    DE_XUAT = "de_xuat"
    DUYET = "duyet"


class DinhKemDeXuat(db.Model):
    """Ảnh/tệp bất kỳ đính kèm theo Đề xuất — người đề xuất có thể đính kèm
    lúc gửi (giai_doan=DE_XUAT), người duyệt có thể đính kèm lúc duyệt
    (giai_doan=DUYET). Tách khỏi 2 ảnh chữ ký (duong_dan_chu_ky_*) vì đó là
    chữ ký bắt buộc dùng để in PDF, còn đây là tài liệu minh chứng thêm,
    không bắt buộc, có thể nhiều file — mô phỏng đúng mẫu DinhKem/AnhYeuCau
    đã dùng cho công việc."""
    __tablename__ = "dinh_kem_de_xuat"

    id = db.Column(db.Integer, primary_key=True)
    de_xuat_id = db.Column(db.Integer, db.ForeignKey("de_xuat.id"), nullable=False, index=True)
    giai_doan = db.Column(db.String(20), nullable=False)  # de_xuat | duyet
    nguoi_tai_len_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False)

    loai = db.Column(db.String(20), nullable=False)
    duong_dan = db.Column(db.String(300), nullable=False)  # tương đối so với UPLOAD_ROOT
    ten_goc = db.Column(db.String(255))
    kich_thuoc = db.Column(db.Integer)
    mime = db.Column(db.String(100))
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    de_xuat = db.relationship("DeXuat", back_populates="dinh_kem")
    nguoi_tai_len = db.relationship("NguoiDung")

    @property
    def kich_thuoc_dep(self):
        n = self.kich_thuoc or 0
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.0f} KB"
        return f"{n / 1048576:.1f} MB"


class GoiHang(db.Model):
    """1 lần đóng gói đơn hàng — nhân viên quét mã vận đơn (camera đọc mã
    vạch/QR trên đơn) rồi chụp (nhiều) ảnh kiện hàng đã gói, để sau này
    tra cứu NHANH ai là người gói 1 đơn cụ thể khi phát hiện sai sót (VD
    gói nhầm màu), thay vì phải lướt lại cả nhóm Zalo tìm ảnh.

    Không ràng buộc unique trên ma_van_don: có thể gói lại/gói bù cùng 1
    mã nhiều lần (đóng sai rồi sửa lại) — khi tra cứu luôn ưu tiên xem bản
    ghi MỚI NHẤT là lần gói cuối cùng thực tế gửi đi."""
    __tablename__ = "goi_hang"

    id = db.Column(db.Integer, primary_key=True)
    ma_van_don = db.Column(db.String(50), nullable=False, index=True)
    nguoi_goi_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False, index=True)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai, index=True)

    nguoi_goi = db.relationship("NguoiDung")
    anh = db.relationship(
        "AnhGoiHang", back_populates="goi_hang", cascade="all, delete-orphan",
        order_by="AnhGoiHang.id"
    )


class AnhGoiHang(db.Model):
    """1 ảnh trong 1 lần đóng gói — tách bảng riêng (giống DinhKemDeXuat)
    để 1 lần gói cho phép NHIỀU ảnh thay vì chỉ 1."""
    __tablename__ = "anh_goi_hang"

    id = db.Column(db.Integer, primary_key=True)
    goi_hang_id = db.Column(db.Integer, db.ForeignKey("goi_hang.id"), nullable=False, index=True)
    duong_dan = db.Column(db.String(300), nullable=False)  # tương đối so với UPLOAD_ROOT
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    goi_hang = db.relationship("GoiHang", back_populates="anh")


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

class ThanhToanCongNo(db.Model):
    """1 lần khách thanh toán công nợ — NVKD khai báo: tên khách, mã đơn
    hàng, ảnh lệnh chuyển tiền (nhiều ảnh), ghi chú. Ban giám đốc / Quản
    lý bộ phận / Quản trị tra cứu lại theo tên, mã, ngày, ghi chú.

    tim_kiem / ghi_chu_chuan: bản KHÔNG DẤU + chữ thường, tự tính khi lưu —
    để gõ "thanh tuyen" vẫn ra THANH TUYÊN, và gộp các ghi chú chỉ khác
    hoa/thường, dấu cách vào cùng 1 mục trong bộ lọc ghi chú."""
    __tablename__ = "thanh_toan_cong_no"

    id = db.Column(db.Integer, primary_key=True)
    ten_kh = db.Column(db.String(200), nullable=False, index=True)
    ma_don = db.Column(db.String(50), nullable=False, index=True)
    ghi_chu = db.Column(db.String(255), nullable=False)
    ghi_chu_chuan = db.Column(db.String(255), nullable=False, index=True)
    tim_kiem = db.Column(db.String(500), nullable=False, index=True)
    nguoi_tao_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False, index=True)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai, index=True)
    sua_luc = db.Column(db.DateTime)
    lich_su = db.Column(db.Text)  # mỗi lần sửa 1 dòng: ai, lúc nào, đổi gì

    nguoi_tao = db.relationship("NguoiDung")
    anh = db.relationship(
        "AnhCongNo", back_populates="cong_no", cascade="all, delete-orphan",
        order_by="AnhCongNo.id"
    )


class AnhCongNo(db.Model):
    """1 ảnh lệnh chuyển tiền trong 1 phiếu thanh toán công nợ."""
    __tablename__ = "anh_cong_no"

    id = db.Column(db.Integer, primary_key=True)
    cong_no_id = db.Column(db.Integer, db.ForeignKey("thanh_toan_cong_no.id"), nullable=False, index=True)
    duong_dan = db.Column(db.String(300), nullable=False)  # tương đối so với UPLOAD_ROOT
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    cong_no = db.relationship("ThanhToanCongNo", back_populates="anh")

class LoiViPham(db.Model):
    """Danh mục lỗi soạn sẵn (Ban giám đốc / Quản trị khai báo) — khi lập
    biên bản chỉ cần chọn lỗi là tự điền mức phạt mặc định (vẫn sửa được)."""
    __tablename__ = "loi_vi_pham"

    id = db.Column(db.Integer, primary_key=True)
    ten = db.Column(db.String(200), nullable=False, unique=True)
    muc_phat = db.Column(db.Integer, nullable=False, default=0)
    mo_ta = db.Column(db.Text)
    dang_dung = db.Column(db.Boolean, nullable=False, default=True)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)


class BienBanPhat(db.Model):
    """1 biên bản vi phạm / phạt tiền. Quy trình: Quản lý bộ phận / Ban giám
    đốc / Quản trị lập (ký tên) -> nhân viên vào web ký xác nhận (kèm ý kiến
    giải trình nếu có). Nhân viên không chịu ký thì người lập ghi nhận "NV
    không ký" — biên bản vẫn có hiệu lực. Mỗi bước sinh lại PDF.

    ten_loi lưu bản sao tên lỗi lúc lập — sửa/xoá danh mục lỗi về sau không
    làm đổi nội dung biên bản đã lập."""
    __tablename__ = "bien_ban_phat"

    id = db.Column(db.Integer, primary_key=True)
    nguoi_vi_pham_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False, index=True)
    nguoi_lap_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"), nullable=False, index=True)
    loi_id = db.Column(db.Integer, db.ForeignKey("loi_vi_pham.id", ondelete="SET NULL"))
    ten_loi = db.Column(db.String(200), nullable=False)
    ngay_vi_pham = db.Column(db.Date, nullable=False, index=True)
    noi_dung = db.Column(db.Text, nullable=False)  # diễn biến sự việc
    so_tien = db.Column(db.Integer, nullable=False, default=0)
    trang_thai = db.Column(db.String(20), nullable=False, default=TrangThaiBienBan.CHO_KY, index=True)

    chu_ky_lap = db.Column(db.String(300))
    chu_ky_nv = db.Column(db.String(300))
    y_kien_nv = db.Column(db.Text)
    ky_luc = db.Column(db.DateTime)

    xu_ly_boi_id = db.Column(db.Integer, db.ForeignKey("nguoi_dung.id"))  # ai ghi nhận không ký / huỷ
    xu_ly_luc = db.Column(db.DateTime)
    ly_do_xu_ly = db.Column(db.Text)  # lý do huỷ / ghi chú khi NV không ký

    duong_dan_pdf = db.Column(db.String(300))
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai, index=True)

    nguoi_vi_pham = db.relationship("NguoiDung", foreign_keys=[nguoi_vi_pham_id])
    nguoi_lap = db.relationship("NguoiDung", foreign_keys=[nguoi_lap_id])
    xu_ly_boi = db.relationship("NguoiDung", foreign_keys=[xu_ly_boi_id])
    loi = db.relationship("LoiViPham")
    dinh_kem = db.relationship("DinhKemBienBan", back_populates="bien_ban",
                               cascade="all, delete-orphan", order_by="DinhKemBienBan.id")

    @property
    def so_bien_ban(self) -> str:
        return f"BB{self.tao_luc:%y%m}-{self.id:04d}" if self.tao_luc and self.id else "BB-mới"

    @property
    def ten_trang_thai(self) -> str:
        return TrangThaiBienBan.NHAN.get(self.trang_thai, self.trang_thai)

    @property
    def co_hieu_luc(self) -> bool:
        return self.trang_thai in TrangThaiBienBan.CO_HIEU_LUC


class DinhKemBienBan(db.Model):
    """Ảnh/tệp bằng chứng đính kèm biên bản."""
    __tablename__ = "dinh_kem_bien_ban"

    id = db.Column(db.Integer, primary_key=True)
    bien_ban_id = db.Column(db.Integer, db.ForeignKey("bien_ban_phat.id"), nullable=False, index=True)
    loai = db.Column(db.String(20), nullable=False)
    duong_dan = db.Column(db.String(300), nullable=False)
    ten_goc = db.Column(db.String(255))
    kich_thuoc = db.Column(db.Integer)
    mime = db.Column(db.String(100))
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    bien_ban = db.relationship("BienBanPhat", back_populates="dinh_kem")

    @property
    def kich_thuoc_dep(self):
        n = self.kich_thuoc or 0
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.0f} KB"
        return f"{n / 1048576:.1f} MB"


class QuyTrinh(db.Model):
    """Quy trình / tài liệu nội bộ cho Trợ lý AI (VD "Quy trình kho"): nội
    dung chữ (bot đọc để trả lời chi tiết) + nhiều ảnh có nhãn (bot gửi
    kèm). Ai đăng nhập cũng hỏi/xem được."""
    __tablename__ = "quy_trinh"

    id = db.Column(db.Integer, primary_key=True)
    ten = db.Column(db.String(200), nullable=False, unique=True)
    noi_dung = db.Column(db.Text)
    tu_khoa = db.Column(db.String(500))  # từ khoá thêm để bot nhận ra câu hỏi, cách nhau bằng dấu phẩy
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    anh = db.relationship("AnhQuyTrinh", back_populates="quy_trinh",
                          cascade="all, delete-orphan", order_by="AnhQuyTrinh.id")


class AnhQuyTrinh(db.Model):
    __tablename__ = "anh_quy_trinh"

    id = db.Column(db.Integer, primary_key=True)
    quy_trinh_id = db.Column(db.Integer, db.ForeignKey("quy_trinh.id"), nullable=False, index=True)
    duong_dan = db.Column(db.String(500), nullable=False)
    nhan = db.Column(db.String(255))  # VD "Kế hoạch làm việc cố định của kho"

    quy_trinh = db.relationship("QuyTrinh", back_populates="anh")


class ViecDinhKy(db.Model):
    """Mẫu công việc lặp lại theo thứ trong tuần, áp dụng cho 1 CHỨC VỤ —
    VD kho: 08:30 "Kiểm tra kho, vệ sinh sàn", thứ 2 16:30 "Kiểm kê nhóm
    công cụ thi công"… Mỗi sáng (cron tao-viec-dinh-ky) hệ thống tự tạo việc
    cho từng nhân viên đang có chức vụ đó, người giao = Quản lý bộ phận của
    nhân viên. Bỏ qua ngày nghỉ công ty và buổi nhân viên xin nghỉ."""
    __tablename__ = "viec_dinh_ky"

    id = db.Column(db.Integer, primary_key=True)
    chuc_vu_id = db.Column(db.Integer, db.ForeignKey("chuc_vu.id", ondelete="CASCADE"), nullable=False, index=True)
    tieu_de = db.Column(db.String(255), nullable=False)
    mo_ta = db.Column(db.Text)
    cac_thu = db.Column(db.String(20), nullable=False, default="0,1,2,3,4,5")  # 0 = thứ Hai … 6 = Chủ nhật
    gio_han = db.Column(db.String(5), nullable=False, default="17:00")       # "HH:MM"
    dang_dung = db.Column(db.Boolean, nullable=False, default=True)
    tao_luc = db.Column(db.DateTime, default=gio_vn_hien_tai)

    chuc_vu = db.relationship("ChucVu")

    TEN_THU = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]

    @property
    def ds_thu(self) -> list[int]:
        return sorted({int(x) for x in (self.cac_thu or "").split(",") if x.strip().isdigit() and int(x) < 7})

    @property
    def ten_cac_thu(self) -> str:
        ds = self.ds_thu
        if ds == [0, 1, 2, 3, 4, 5]:
            return "T2 – T7"
        if ds == list(range(7)):
            return "Mọi ngày"
        return ", ".join(self.TEN_THU[i] for i in ds)