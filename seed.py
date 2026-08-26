"""Tạo dữ liệu mẫu để chạy thử (bộ phận, tài khoản, điểm chấm công).

    python seed.py

Chạy lại bao nhiêu lần cũng an toàn — chỉ tạo nếu chưa có, không tạo trùng.

QUAN TRỌNG: file này KHÔNG còn tự tạo/vá bảng nữa. Từ giờ mọi thay đổi
schema (thêm bảng, thêm cột...) đi qua đúng luồng Flask-Migrate chuẩn:

    flask db migrate -m "mô tả ngắn gọn thay đổi"
    flask db upgrade

Lần đầu tiên trên 1 database mới hoàn toàn (chưa có bảng gì), chạy theo
đúng thứ tự sau:

    flask db init            # chỉ 1 lần duy nhất, tạo thư mục migrations/
    flask db migrate -m "Khoi tao schema ban dau"
    flask db upgrade         # tạo toàn bộ bảng
    python seed.py           # rồi mới seed dữ liệu mẫu như file này làm
"""
from app import create_app
from extensions import db
from models import BoPhan, DiemChamCong, NguoiDung, VaiTro

app = create_app()

MAU = [
    # ma, ho ten, vai tro, bo phan, mat khau
    ("admin", "Quản trị hệ thống", VaiTro.ADMIN, None, "admin123"),
    ("nv01", "Nguyễn Văn A", VaiTro.NHAN_VIEN, "Kinh doanh", "nv123"),
]


with app.app_context():
    for ten in ("Kinh doanh", "Kho vận", "Kỹ thuật", "Hành chính"):
        if not BoPhan.query.filter_by(ten=ten).first():
            db.session.add(BoPhan(ten=ten))
    db.session.commit()

    for ma, ho_ten, vai_tro, bo_phan, mk in MAU:
        if NguoiDung.query.filter_by(ma_dinh_danh=ma).first():
            continue
        bp = BoPhan.query.filter_by(ten=bo_phan).first() if bo_phan else None
        nd = NguoiDung(ma_dinh_danh=ma, ho_ten=ho_ten, vai_tro=vai_tro,
                       bo_phan_id=bp.id if bp else None, doi_mat_khau=False)
        nd.dat_mat_khau(mk)
        db.session.add(nd)

    if not DiemChamCong.query.first():
        db.session.add(DiemChamCong(
            ten="Văn phòng chính", dia_chi="TP. Hồ Chí Minh",
            lat=10.762622, lng=106.660172, ban_kinh_m=150))

    db.session.commit()
    print("Xong. Đăng nhập thử: admin / admin123")