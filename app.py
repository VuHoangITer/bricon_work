import os
from datetime import date, datetime

import click
from flask import (Flask, abort, jsonify, render_template, request, send_from_directory)
from flask_login import current_user, login_required

from config import Config
from extensions import db, login_manager, migrate
from models import (BotZalo, BuoiNghi, ChamCong, ChucVu, CongViec, DinhKem,
                    DoUuTien, LoaiDinhKem, NguoiDung, TrangThai, VaiTro, XinNghi,
                    gio_vn_hien_tai, ngay_vn_hien_tai)


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    os.makedirs(app.config["UPLOAD_ROOT"], exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    from views.admin import bp as admin_bp
    from views.api import bp as api_bp
    from views.attendance import bp as attendance_bp
    from views.auth import bp as auth_bp
    from views.tasks import bp as tasks_bp
    from views.tro_ly import bp as tro_ly_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(tro_ly_bp)

    # ---------------------------------------------------------------- media
    @app.route("/media/<path:duong_dan>")
    @login_required
    def media(duong_dan):
        """Phục vụ file đối chứng / ảnh xin nghỉ. Luôn kiểm tra quyền."""
        dk = DinhKem.query.filter_by(duong_dan=duong_dan).first()
        if dk:
            if not current_user.duoc_xem_viec(dk.cong_viec):
                abort(403)
            return send_from_directory(app.config["UPLOAD_ROOT"], duong_dan)

        xn = XinNghi.query.filter_by(anh_minh_chung=duong_dan).first()
        if xn:
            if xn.nguoi_dung_id != current_user.id and not current_user.la_quan_ly:
                abort(403)
            return send_from_directory(app.config["UPLOAD_ROOT"], duong_dan)

        # Ảnh minh hoạ chức vụ — thông tin tổ chức chung, ai đăng nhập cũng xem được
        cv = ChucVu.query.filter_by(anh=duong_dan).first()
        if cv:
            return send_from_directory(app.config["UPLOAD_ROOT"], duong_dan)

        abort(404)

    # ------------------------------------------------------------- webhook
    @app.route("/webhook/zalo/<int:bot_id>", methods=["POST"])
    def webhook_zalo(bot_id):
        """Zalo gọi vào đây mỗi khi có tin nhắn mới ở nơi bot có mặt. Không
        yêu cầu đăng nhập (Zalo gọi, không phải trình duyệt) — xác thực bằng
        secret_token thay vì session."""
        import services

        bot = db.session.get(BotZalo, bot_id)
        if not bot:
            abort(404)
        secret_nhan = request.headers.get("X-Bot-Api-Secret-Token", "")
        if bot.webhook_secret and secret_nhan != bot.webhook_secret:
            abort(403)

        du_lieu = request.get_json(silent=True) or {}
        services.xu_ly_webhook_zalo(bot, du_lieu)
        db.session.commit()
        return jsonify({"ok": True})

    # ---------------------------------------------------------------- lỗi
    @app.errorhandler(403)
    def loi_403(e):
        return render_template("loi.html", ma=403,
                               tieu_de="Không có quyền",
                               mo_ta="Bạn không được xem trang này. Nếu cần "
                                     "truy cập, nhờ quản lý cấp quyền."), 403

    @app.errorhandler(404)
    def loi_404(e):
        return render_template("loi.html", ma=404,
                               tieu_de="Không tìm thấy",
                               mo_ta="Đường dẫn không tồn tại hoặc đã bị xoá."), 404

    @app.errorhandler(413)
    def loi_413(e):
        return render_template(
            "loi.html", ma=413, tieu_de="Tệp quá lớn",
            mo_ta=f"Giới hạn {app.config['MAX_UPLOAD_MB']}MB mỗi lần gửi. "
                  f"Quay video ngắn lại hoặc gửi làm nhiều lần."), 413

    # ---------------------------------------------------------------- jinja
    @app.context_processor
    def bien_chung():
        return {
            "TrangThai": TrangThai,
            "VaiTro": VaiTro,
            "LoaiDinhKem": LoaiDinhKem,
            "DoUuTien": DoUuTien,
            "BuoiNghi": BuoiNghi,
            "bay_gio": gio_vn_hien_tai(),
            "hom_nay": ngay_vn_hien_tai(),
        }

    @app.template_filter("gio")
    def f_gio(dt):
        return dt.strftime("%H:%M %d/%m/%Y") if dt else "—"

    @app.template_filter("ngay")
    def f_ngay(d):
        return d.strftime("%d/%m/%Y") if d else "—"

    @app.template_filter("input_dt")
    def f_input_dt(dt):
        return dt.strftime("%Y-%m-%dT%H:%M") if dt else ""

    # ---------------------------------------------------------------- CLI
    @app.cli.command("tao-admin")
    @click.argument("ma_dinh_danh")
    @click.argument("ho_ten")
    @click.argument("mat_khau")
    def tao_admin(ma_dinh_danh, ho_ten, mat_khau):
        """Tạo tài khoản quản trị đầu tiên."""
        if NguoiDung.query.filter_by(ma_dinh_danh=ma_dinh_danh).first():
            click.echo("Mã này đã tồn tại.")
            return
        nd = NguoiDung(ma_dinh_danh=ma_dinh_danh, ho_ten=ho_ten,
                       vai_tro=VaiTro.ADMIN, doi_mat_khau=False)
        nd.dat_mat_khau(mat_khau)
        db.session.add(nd)
        db.session.commit()
        click.echo(f"Đã tạo admin {ma_dinh_danh}.")

    @app.cli.command("nhac-qua-han")
    def nhac_qua_han():
        """Chạy bằng cron: nhắc Zalo các việc quá hạn chưa xong."""
        import services
        viecs = CongViec.query.filter(
            CongViec.han < gio_vn_hien_tai(),
            CongViec.trang_thai.in_(TrangThai.CHUA_XONG),
        ).all()
        for v in viecs:
            services.gui_cho_nhan_vien(
                v.nguoi_nhan,
                f"⏰ Việc quá hạn chưa nộp\n[{v.ma}] {v.tieu_de}\n"
                f"Hạn: {v.han:%H:%M %d/%m}\n\n{v.link}", v)
        if viecs:
            dong = "\n".join(
                f"• {v.nguoi_nhan.ho_ten}: [{v.ma}] {v.tieu_de}" for v in viecs)
            services.gui_nhom_ql(f"⏰ {len(viecs)} việc đang quá hạn:\n{dong}")
        db.session.commit()
        click.echo(f"Đã nhắc {len(viecs)} việc.")

    @app.cli.command("nhac-cho-duyet")
    def nhac_cho_duyet():
        """Chạy bằng cron: nhắc quản lý những việc đang chờ duyệt."""
        import services
        viecs = CongViec.query.filter_by(trang_thai=TrangThai.CHO_DUYET).all()
        if not viecs:
            click.echo("Không có việc chờ duyệt.")
            return
        dong = "\n".join(
            f"• [{v.ma}] {v.tieu_de} — {v.nguoi_nhan.ho_ten}" for v in viecs)
        services.gui_nhom_ql(
            f"📋 {len(viecs)} việc đang chờ duyệt đối chứng:\n{dong}")
        db.session.commit()
        click.echo(f"Đã nhắc {len(viecs)} việc chờ duyệt.")

    @app.cli.command("check-nghi-khong-phep")
    def check_nghi_khong_phep():
        """Chạy bằng cron lúc 17h30 T2-T7: đánh dấu nghỉ không phép."""
        import services
        hom_nay = ngay_vn_hien_tai()
        if hom_nay.weekday() == 6:      # Chủ nhật
            click.echo("Chủ nhật, bỏ qua.")
            return
        da_cham = {c.nguoi_dung_id for c in
                   ChamCong.query.filter_by(ngay=hom_nay).all()}
        da_nghi_ca_ngay = set()
        for x in XinNghi.query.filter_by(ngay=hom_nay).all():
            if x.buoi == BuoiNghi.CA_NGAY:
                da_nghi_ca_ngay.add(x.nguoi_dung_id)
        vang = [n for n in NguoiDung.query.filter_by(dang_hoat_dong=True).all()
                if n.id not in da_cham and n.id not in da_nghi_ca_ngay
                and n.vai_tro == VaiTro.NHAN_VIEN]
        for n in vang:
            db.session.add(ChamCong(nguoi_dung_id=n.id, ngay=hom_nay,
                                    nghi_khong_phep=True))
        if vang:
            dong = "\n".join(f"• {n.ho_ten} ({n.ma_dinh_danh})" for n in vang)
            services.gui_nhom_ql(
                f"🚫 Nghỉ không phép ngày {hom_nay:%d/%m}:\n{dong}")
        db.session.commit()
        click.echo(f"Đã ghi nhận {len(vang)} người nghỉ không phép.")

    @app.cli.command("dong-viec-qua-han")
    def dong_viec_qua_han():
        """Chạy bằng cron (đặt mỗi 5 phút trên Ubuntu VPS): tự động đóng +
        chấm 0 sao các việc quá hạn chưa nộp. Đây là lớp dự phòng — việc
        cũng đã được tự đóng ngay mỗi khi có ai mở trang công việc trong
        lúc dùng app."""
        import services
        da_dong = services.dong_cac_viec_qua_han()
        click.echo(f"Đã tự động đóng {len(da_dong)} việc quá hạn.")

    @app.cli.command("don-dep-log-zalo")
    def don_dep_log_zalo():
        """Chạy bằng cron lúc 00:05 mỗi ngày trên Ubuntu VPS: xoá sạch log
        Zalo cũ hơn hôm nay, chỉ giữ log của đúng ngày hôm đó — tránh bảng
        log phình to theo thời gian."""
        from models import LogZalo
        dau_hom_nay = datetime.combine(ngay_vn_hien_tai(), datetime.min.time())
        so_xoa = LogZalo.query.filter(LogZalo.tao_luc < dau_hom_nay).delete()
        db.session.commit()
        click.echo(f"Đã xoá {so_xoa} log Zalo cũ hơn hôm nay.")

    @app.cli.command("nhac-sap-qua-han")
    def nhac_sap_qua_han():
        """Chạy bằng cron mỗi 5 phút: nhắc việc sắp tới hạn trong 30 phút
        tới, chỉ gửi riêng cho nhân viên đang nhận việc đó (không báo nhóm
        QL). Mỗi việc chỉ nhắc đúng 1 lần."""
        import services
        so_luong = services.nhac_viec_sap_qua_han(30)
        db.session.commit()
        click.echo(f"Đã nhắc {so_luong} việc sắp tới hạn.")

    @app.cli.command("nhac-cham-cong-sang")
    def nhac_cham_cong_sang():
        """Chạy bằng cron lúc 07:45: gửi link chấm công cho từng nhân viên/
        quản lý (trừ Sếp/Admin)."""
        import services
        so_luong = services.nhac_cham_cong_sang()
        db.session.commit()
        click.echo(f"Đã nhắc chấm công {so_luong} người.")

    @app.cli.command("nhac-cham-cong-chieu")
    def nhac_cham_cong_chieu():
        """Chạy bằng cron lúc 17:32: nhắc chấm công RA cho người đã chấm
        vào hôm nay nhưng chưa chấm ra (trừ Sếp/Admin)."""
        import services
        so_luong = services.nhac_cham_cong_chieu()
        db.session.commit()
        click.echo(f"Đã nhắc chấm công ra {so_luong} người.")

    @app.cli.command("nhac-viec-hom-nay")
    def nhac_viec_hom_nay():
        """Chạy bằng cron lúc 08:00: gửi link xem việc hôm nay cho từng
        nhân viên/quản lý (trừ Sếp/Admin)."""
        import services
        so_luong = services.nhac_viec_hom_nay()
        db.session.commit()
        click.echo(f"Đã nhắc xem việc {so_luong} người.")

    @app.cli.command("ban-tin-sang")
    def ban_tin_sang():
        """Chạy bằng cron lúc 08:00: gửi bản tin cá nhân buổi sáng (kết quả
        hôm qua + việc quan trọng hôm nay + cần xử lý ngay) cho từng nhân
        viên/quản lý (trừ Sếp/Admin)."""
        import services
        so_luong = services.gui_ban_tin_sang()
        db.session.commit()
        click.echo(f"Đã gửi bản tin sáng cho {so_luong} người.")

    @app.cli.command("ban-tin-chieu")
    def ban_tin_chieu():
        """Chạy bằng cron lúc 17:30: gửi bản tin cá nhân buổi chiều (kết quả
        hôm nay + cần xử lý ngay) cho từng nhân viên/quản lý (trừ Sếp/Admin)."""
        import services
        so_luong = services.gui_ban_tin_chieu()
        db.session.commit()
        click.echo(f"Đã gửi bản tin chiều cho {so_luong} người.")

    @app.cli.command("bao-cao-sang")
    def bao_cao_sang():
        """Chạy bằng cron lúc 08:10: báo cáo nhanh đầu ngày vào nhóm QL."""
        import services
        services.bao_cao_sang_cho_sep()
        db.session.commit()
        click.echo("Đã gửi báo cáo sáng.")

    @app.cli.command("bao-cao-chieu")
    def bao_cao_chieu():
        """Chạy bằng cron lúc 17:30: báo cáo tóm tắt cuối ngày vào nhóm QL."""
        import services
        services.bao_cao_chieu_cho_sep()
        db.session.commit()
        click.echo("Đã gửi báo cáo chiều.")

    @app.cli.command("bao-cao-thieu-sot")
    def bao_cao_thieu_sot():
        """Chạy bằng cron lúc 18:00: báo cáo nhân viên nào còn việc chưa
        nộp/còn thiếu trong ngày, vào nhóm QL."""
        import services
        services.bao_cao_thieu_sot()
        db.session.commit()
        click.echo("Đã gửi báo cáo việc còn thiếu.")

    @app.cli.command("poll-zalo")
    @click.option("--giay", default=3, show_default=True,
                 help="Số giây nghỉ giữa mỗi lần hỏi Zalo.")
    def poll_zalo(giay):
        """CHỈ DÙNG KHI DEV LOCAL (127.0.0.1) — webhook thật không thể tới
        được localhost, nên lệnh này thay thế bằng cách chủ động hỏi Zalo
        liên tục (getUpdates) để bắt tin nhắn mới (tag bot, lệnh /id...),
        route qua đúng xu_ly_webhook_zalo() y hệt webhook thật.

        Chạy trong 1 cửa sổ terminal riêng, song song với `flask run`:
            flask poll-zalo
        Nhấn Ctrl+C để dừng.

        Trên server thật (có domain + HTTPS), dùng webhook (nút "Đặt
        webhook" ở Thiết lập) — nhanh hơn, đỡ tốn hơn nhiều, KHÔNG cần lệnh
        này nữa."""
        import time

        import requests
        import services
        from models import BotZalo

        moc_da_xu_ly: dict[int, int] = {}
        click.echo("Đang lắng nghe Zalo... (Ctrl+C để dừng)")
        while True:
            for bot in BotZalo.query.filter_by(dang_hoat_dong=True).all():
                url = f"{app.config['ZALO_API_BASE']}{bot.token}/getUpdates"
                try:
                    r = requests.get(url, timeout=10)
                    du_lieu = r.json()
                except Exception as e:  # noqa: BLE001
                    click.echo(f"[{bot.ten}] Lỗi gọi API: {e}")
                    continue

                ds = du_lieu.get("result")
                if ds is None:
                    ds = du_lieu.get("updates") or []
                if isinstance(ds, dict):
                    ds = [ds]
                if not isinstance(ds, list):
                    continue

                moc_cu = moc_da_xu_ly.get(bot.id, 0)
                moc_moi = moc_cu
                for cap_nhat in ds:
                    uid = cap_nhat.get("update_id", 0)
                    if uid <= moc_cu:
                        continue
                    moc_moi = max(moc_moi, uid)
                    try:
                        services.xu_ly_webhook_zalo(bot, cap_nhat)
                    except Exception as e:  # noqa: BLE001
                        click.echo(f"[{bot.ten}] Lỗi xử lý tin nhắn: {e}")
                moc_da_xu_ly[bot.id] = moc_moi
            time.sleep(giay)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)