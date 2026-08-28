"""Tích hợp OpenAI: gợi ý/tóm tắt mô tả công việc + trợ lý AI hỏi-đáp.

Tách riêng khỏi services.py (28/8/2026) vì phần AI ngày càng phình to,
gộp chung làm services.py khó đọc/khó tìm. File này chỉ lo phần OpenAI —
mọi thứ khác (Zalo, chấm công, KPI, excel...) vẫn ở services.py.
"""
import json
import re
import time
import unicodedata
from datetime import datetime, timedelta

import requests
from flask import current_app, url_for

from extensions import db
from models import NguoiDung, VaiTro, gio_vn_hien_tai, ngay_vn_hien_tai
from services import dieu_kien_viec_trong_ngay, lay_cai_dat, tinh_kpi

# ---------------------------------------------------------------------------
# TÍCH HỢP CHATGPT — gợi ý / tóm tắt yêu cầu chi tiết khi giao việc
# ---------------------------------------------------------------------------
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _goi_chatgpt_tin_nhan(messages: list[dict], dang_json: bool = False,
                          model: str | None = None, _da_thu_lai: bool = False) -> tuple[str | None, str | None]:
    """Gọi OpenAI với danh sách messages đầy đủ — hỗ trợ nhiều lượt hội
    thoại (dùng cho trợ lý AI), không chỉ 1 cặp system/user đơn.
    dang_json=True bắt OpenAI trả về đúng 1 object JSON hợp lệ.
    model=None -> dùng OPENAI_MODEL mặc định (việc đơn giản như gợi ý/tóm
    tắt mô tả); truyền model cụ thể để override, VD trợ lý chat cần model
    mạnh hơn để đọc hiểu ngữ cảnh dài mà không bị rối.
    _da_thu_lai: dùng nội bộ, không truyền tay — đánh dấu đã tự thử lại 1
    lần khi bị rate limit, tránh lặp vô hạn."""
    key = lay_cai_dat("openai_api_key")
    if not key:
        return None, "Chưa cấu hình OpenAI API key ở trang Thiết lập."
    model = model or current_app.config.get("OPENAI_MODEL", "gpt-4o-mini")
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
        thong_diep = du_lieu["error"].get("message", "OpenAI báo lỗi không rõ nguyên nhân.")
        # Rate limit (429) thường chỉ tạm thời trong vài giây -> tự chờ
        # đúng khoảng OpenAI báo rồi thử lại 1 lần, thay vì bắt người dùng
        # thấy nguyên lỗi kỹ thuật mỗi khi nghẽn thoáng qua.
        if r.status_code == 429 and not _da_thu_lai:
            khop = re.search(r"try again in ([\d.]+)s", thong_diep)
            cho_giay = min(float(khop.group(1)), 25) + 0.5 if khop else 5
            time.sleep(cho_giay)
            return _goi_chatgpt_tin_nhan(messages, dang_json=dang_json, model=model, _da_thu_lai=True)
        if r.status_code == 429:
            return None, "Trợ lý đang có nhiều người hỏi cùng lúc, bạn thử lại sau vài giây nhé."
        return None, thong_diep
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
    "chấm công, KPI và xin nghỉ phép của công ty BRICON. Trả lời tiếng Việt, "
    "ngắn gọn, thân thiện, đúng trọng tâm.\n\n"

    "HƯỚNG DẪN SỬ DỤNG (trả lời câu hỏi \"làm sao để...\"):\n"
    "- Xin nghỉ: Chấm công → Xin nghỉ phép → chọn ngày (nghỉ 1 ngày thì chọn "
    "thêm buổi sáng/chiều) → bắt buộc đính kèm ảnh giấy phép đã duyệt → Gửi "
    "là được ghi nhận ngay, không cần ai duyệt thêm.\n"
    "- Chấm công: menu Chấm công → bấm chấm vào/ra, cần đứng trong bán kính "
    "cho phép mới chấm được.\n"
    "- Xem việc được giao: menu Công việc, hoặc mục \"Việc hằng ngày\" trên "
    "trang Hôm nay (Dashboard).\n"
    "- Nộp kết quả việc: vào chi tiết việc → Gửi đối chứng → đính kèm ảnh/"
    "video/tệp/ghi âm. Quá hạn chưa nộp gì sẽ tự đóng 0 sao, chỉ Admin mở "
    "lại được.\n"
    "- Xem KPI: menu KPI, chọn khoảng ngày, thang điểm 0-5 sao.\n\n"

    "DỮ LIỆU: chỉ dùng đúng dữ liệu thật cung cấp bên dưới cho số liệu/tên "
    "việc/lịch sử cụ thể — không bịa. Thiếu dữ liệu để trả lời 1 câu tra cứu "
    "thì nói rõ chưa có, đừng đoán. Nhưng nếu được hỏi Ý KIẾN/ĐỀ XUẤT/NHẬN "
    "XÉT dựa trên dữ liệu đã có (VD: đề xuất cho 1 nhân viên khi đã biết KPI "
    "của họ) thì CHỦ ĐỘNG đưa góc nhìn — đó là suy luận trên số liệu thật, "
    "không phải bịa đặt, đừng từ chối.\n\n"

    "PHÂN QUYỀN DỮ LIỆU: dữ liệu bên dưới có thể liệt kê nhiều người, mỗi "
    "người 1 dòng ghi rõ tên. Khi người hỏi nói \"tôi\"/\"của tôi\", CHỈ được "
    "dùng đúng phần đã ghi rõ \"của chính người đang hỏi\" — TUYỆT ĐỐI không "
    "lấy dữ liệu người khác trong danh sách rồi gán cho họ. Không có phần đó "
    "thì nói rõ họ chưa có dữ liệu này.\n"
    "Về lương/mô tả/chế độ theo chức vụ: chỉ dùng đúng \"Thông tin riêng cho "
    "chức vụ\" của chính người đang hỏi — không suy đoán chức vụ khác vì bạn "
    "không được cung cấp dữ liệu đó.\n\n"

    "GIỚI HẠN THAO TÁC: bạn không tự THỰC HIỆN được thao tác ghi dữ liệu nào "
    "trong hệ thống (không tự tạo việc, không tự xin nghỉ, không tự chấm "
    "công hộ) — đây là giới hạn của CHÍNH BẠN, không phải giới hạn quyền của "
    "người hỏi. Muốn thực hiện thao tác gì thì hướng dẫn họ tự bấm trong hệ "
    "thống, đừng suy diễn rằng vai trò họ \"không thể\" làm việc đó trừ khi "
    "dữ liệu bên dưới nói rõ bị cấm (chỉ Sếp/Quản trị không tự NHẬN việc "
    "được giao cho mình — còn GIAO việc cho người khác thì vẫn làm bình "
    "thường qua trang Giao việc mới, 2 việc này khác nhau, đừng nhầm).\n\n"

    "ĐỊNH DẠNG TRẢ LỜI: LUÔN LUÔN đúng 1 object JSON, không thêm chữ nào "
    "ngoài JSON đó, theo đúng khuôn dạng:\n"
    '{"tra_loi": "<câu trả lời tự nhiên bằng tiếng Việt>", '
    '"duong_dan": "<đường dẫn gợi ý bấm vào nếu phù hợp, hoặc null>", '
    '"nhan_nut": "<nhãn ngắn cho nút bấm đó, hoặc null>", '
    '"media": "<đường dẫn ảnh/video/ghi âm để hiện kèm câu trả lời, hoặc '
    'null>"}\n\n'

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

    "MEDIA: dữ liệu bên dưới có thể kèm theo các đoạn dạng [media:đường-dẫn] "
    "ngay sau 1 việc hoặc 1 chức vụ có đối chứng/ảnh minh hoạ. Người hỏi "
    "muốn XEM/HIỆN 1 ảnh/video/ghi âm cụ thể và có đúng 1 đoạn [media:...] "
    "liên quan trong dữ liệu → COPY Y NGUYÊN chuỗi đường dẫn đó (không kèm "
    "chữ \"media:\" hay dấu ngoặc) vào trường media. Đây CHỈ là chọn 1 đường "
    "dẫn CÓ SẴN để app tự hiển thị — KHÔNG phải bạn tự tạo/tải lên/đính kèm "
    "file thật, nên luôn là hành động AN TOÀN và ĐÚNG CHỨC NĂNG khi có "
    "[media:...] khớp — TUYỆT ĐỐI không từ chối kiểu \"tôi không thể cung "
    "cấp ảnh\" khi có [media:...] phù hợp. Không có đoạn nào phù hợp thì để "
    "media là null, không tự bịa đường dẫn."
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
    dau_ngay = datetime.combine(hom_nay, datetime.min.time())
    cuoi_ngay = datetime.combine(hom_nay, datetime.max.time())
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
            dieu_kien_viec_trong_ngay(hom_nay),
        ).all()
        viec_nm = CongViec.query.filter(
            CongViec.nguoi_nhan_id == nv.id, CongViec.trang_thai.in_(TrangThai.DANG_MO),
            dieu_kien_viec_trong_ngay(ngay_mai),
        ).all()
        viec_da_xong_hn = CongViec.query.filter(
            CongViec.nguoi_nhan_id == nv.id, CongViec.trang_thai == TrangThai.HOAN_THANH,
            CongViec.hoan_thanh_luc >= dau_ngay, CongViec.hoan_thanh_luc <= cuoi_ngay,
        ).order_by(CongViec.hoan_thanh_luc).all()

        dong.append(f"* {nv.ho_ten} ({nv.ma_dinh_danh}) — chấm công hôm nay: {tt}.")
        if viec_hn:
            dong.append("  Việc hạn hôm nay: " + "; ".join(
                f"[{v.ma}] {v.tieu_de} ({v.ten_trang_thai}){_dong_media_cho_viec(v)}"
                for v in viec_hn))
        if viec_nm:
            dong.append("  Việc hạn ngày mai: " + "; ".join(
                f"[{v.ma}] {v.tieu_de} ({v.ten_trang_thai}){_dong_media_cho_viec(v)}"
                for v in viec_nm))
        if viec_da_xong_hn:
            dong.append("  Việc ĐÃ HOÀN THÀNH hôm nay: " + "; ".join(
                f"[{v.ma}] {v.tieu_de} (xong lúc {v.hoan_thanh_luc:%H:%M}"
                f"{f', {v.so_sao_cuoi}★' if v.so_sao_cuoi is not None else ''})"
                f"{_dong_media_cho_viec(v)}" for v in viec_da_xong_hn))
        else:
            dong.append("  Việc đã hoàn thành hôm nay: chưa có việc nào.")

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


def _can_boi_canh_chuc_vu(van_ban: str, tat_ca_cv) -> bool:
    """Mô tả 6 chức vụ cộng lại ~49 nghìn ký tự — tốn kha khá token nếu
    nạp cho MỌI câu hỏi của Sếp/Admin dù không liên quan (VD: "hôm nay có
    bao nhiêu việc chờ duyệt" không cần đọc mô tả chức vụ), dễ đụng rate
    limit của OpenAI. Chỉ nạp toàn bộ khi câu hỏi (hoặc vài lượt gần đây,
    do van_ban truyền vào đã gộp sẵn) có nhắc tới chức vụ/nhiệm vụ hoặc
    tên 1 trong các chức vụ đó."""
    tin_chuan = _chuan_hoa_khong_dau(van_ban)
    if any(cum in tin_chuan for cum in ("chuc vu", "nhiem vu", "mo ta cong viec", "vi tri cong viec")):
        return True
    for cv in tat_ca_cv:
        tu_ten = [t for t in _chuan_hoa_khong_dau(cv.ten).split() if len(t) > 2 and t not in ("va", "kiem")]
        if any(t in tin_chuan for t in tu_ten):
            return True
    return False


def _boi_canh_tro_ly(nd: NguoiDung, van_ban_gan_day: str = "") -> str:
    """Dựng đoạn ngữ cảnh dữ liệu thật của người đang hỏi, nhét vào system
    prompt để trợ lý AI trả lời đúng, không bịa.

    Có 2 lớp thông tin tổ chức nạp thêm ngoài dữ liệu việc/chấm công:
    - Thông tin chung công ty (chế độ, chính sách...) — áp dụng cho mọi
      người, quản lý ở trang Info AI.
    - Thông tin riêng theo Chức vụ của đúng người đang hỏi (mô tả công
      việc, lương, chế độ riêng vị trí) — CHỈ nạp đúng 1 chức vụ của họ,
      không nạp chức vụ khác, nên AI không có gì để lẫn lộn giữa các
      chức vụ dù có bị hỏi khéo. Riêng Admin/Sếp được nạp TOÀN BỘ mô tả
      chức vụ NHƯNG chỉ khi van_ban_gan_day cho thấy câu hỏi thực sự liên
      quan (xem _can_boi_canh_chuc_vu) — tránh tốn token vô ích.

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
        if tat_ca_cv and _can_boi_canh_chuc_vu(van_ban_gan_day, tat_ca_cv):
            dong.append("--- Toàn bộ chức vụ trong hệ thống (Admin/Sếp được xem hết, "
                        "không giới hạn 1 chức vụ như nhân viên thường) ---\n" +
                        "\n\n".join(
                            f"## {cv.ten}\n{(cv.mo_ta or '(chưa có mô tả)').strip()}"
                            + (f" [media:{cv.anh}]" if cv.anh else "")
                            for cv in tat_ca_cv))
        elif tat_ca_cv:
            dong.append(
                "Các chức vụ hiện có trong hệ thống (hỏi cụ thể tên 1 chức "
                "vụ để xem đầy đủ mô tả nhiệm vụ của chức vụ đó): "
                + ", ".join(cv.ten for cv in tat_ca_cv))
    elif nd.chuc_vu:
        dong.append(
            f"--- Thông tin riêng cho chức vụ \"{nd.chuc_vu.ten}\" của người đang hỏi "
            f"(CHỈ dùng đúng phần này cho câu hỏi về vị trí công việc của họ, không có "
            f"dữ liệu chức vụ khác nên đừng suy đoán) ---\n"
            + ((nd.chuc_vu.mo_ta or "").strip() or "(chưa có mô tả cho chức vụ này)")
            + (f" [media:{nd.chuc_vu.anh}]" if nd.chuc_vu.anh else "")
        )
    else:
        dong.append("Người này chưa được gán chức vụ cụ thể — không có thông tin riêng "
                    "theo vị trí công việc, nếu họ hỏi thì nói rõ là chưa có dữ liệu.")

    if not nd.la_admin_sep:
        viec_hom_nay = CongViec.query.filter(
            CongViec.nguoi_nhan_id == nd.id,
            CongViec.trang_thai.in_(TrangThai.DANG_MO),
            dieu_kien_viec_trong_ngay(hom_nay),
        ).all()
        viec_ngay_mai = CongViec.query.filter(
            CongViec.nguoi_nhan_id == nd.id,
            CongViec.trang_thai.in_(TrangThai.DANG_MO),
            dieu_kien_viec_trong_ngay(ngay_mai),
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

        viec_da_xong = (
            CongViec.query.filter(
                CongViec.nguoi_nhan_id == nd.id,
                CongViec.trang_thai == TrangThai.HOAN_THANH,
                CongViec.hoan_thanh_luc >= gio_vn_hien_tai() - timedelta(days=7),
            )
            .order_by(CongViec.hoan_thanh_luc.desc())
            .limit(10)
            .all()
        )
        if viec_da_xong:
            dong.append(
                "Việc ĐÃ HOÀN THÀNH gần đây, 7 ngày qua, mới nhất trước "
                "(của chính người đang hỏi): " + "; ".join(
                    f"[{v.ma}] {v.tieu_de} (xong lúc {v.hoan_thanh_luc:%H:%M %d/%m}"
                    f"{f', {v.so_sao_cuoi}★' if v.so_sao_cuoi is not None else ''})"
                    f"{_dong_media_cho_viec(v)}" for v in viec_da_xong))
        else:
            dong.append(
                "Chưa có việc nào đã hoàn thành trong 7 ngày qua (của chính "
                "người đang hỏi).")

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
        dau_ngay = datetime.combine(hom_nay, datetime.min.time())
        cuoi_ngay = datetime.combine(hom_nay, datetime.max.time())
        cho_duyet = CongViec.query.filter_by(trang_thai=TrangThai.CHO_DUYET).count()
        qua_han = CongViec.query.filter(
            CongViec.han < gio_vn_hien_tai(), CongViec.trang_thai.in_(TrangThai.CHUA_XONG)).count()
        hoan_thanh_hom_nay = CongViec.query.filter(
            CongViec.trang_thai == TrangThai.HOAN_THANH,
            CongViec.hoan_thanh_luc >= dau_ngay, CongViec.hoan_thanh_luc <= cuoi_ngay,
        ).count()
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
            f"{qua_han} việc đang quá hạn chưa nộp, {hoan_thanh_hom_nay} việc đã "
            f"hoàn thành hôm nay (danh sách cụ thể từng việc/từng người xem ở "
            f"phần dữ liệu từng nhân viên bên dưới)."
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


def _chuan_hoa_khong_dau(s: str) -> str:
    """Bỏ dấu tiếng Việt + hạ chữ thường, dùng để so khớp gần đúng (người
    hỏi có thể gõ có dấu, không dấu, hoặc gõ tắt tên chức vụ)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.replace("đ", "d").replace("Đ", "D").lower().strip()


def _thu_khop_anh_chuc_vu(nd: NguoiDung, tin_nhan: str) -> dict | None:
    """Khớp trực tiếp (KHÔNG qua AI) khi người hỏi xin xem ảnh minh hoạ 1
    chức vụ cụ thể — tách riêng khỏi luồng AI vì mô hình chat đôi khi tự ý
    từ chối "gửi ảnh" (hiểu nhầm là phải tự tạo/đính kèm file thật) dù chỉ
    cần trả về đúng 1 đường dẫn đã có sẵn. Khớp tên cứng ở đây đảm bảo luôn
    trả được ảnh khi tên chức vụ đủ rõ, không phụ thuộc AI có làm đúng
    hướng dẫn hay không.

    Chỉ trả kết quả khi khớp ĐÚNG 1 chức vụ và người hỏi có quyền xem
    (Admin/Sếp xem được mọi chức vụ; nhân viên thường chỉ chức vụ của
    chính mình) — nếu mơ hồ (0 hoặc >1 khớp cùng điểm), trả None để câu
    hỏi rơi xuống luồng AI bình thường xử lý tiếp."""
    from models import ChucVu

    tin_chuan = _chuan_hoa_khong_dau(tin_nhan)
    tin_tu = set(tin_chuan.split())
    if not tin_tu & {"anh", "hinh", "photo", "image", "phieu"}:
        return None

    if nd.la_admin_sep:
        ung_vien = ChucVu.query.filter(ChucVu.anh.isnot(None)).all()
    elif nd.chuc_vu and nd.chuc_vu.anh:
        ung_vien = [nd.chuc_vu]
    else:
        ung_vien = []
    if not ung_vien:
        return None

    # Nhân viên thường chỉ có ĐÚNG 1 chức vụ hợp lệ để chọn — chính là
    # chức vụ của họ — nên không cần bắt khớp tên trong câu hỏi (họ hỏi
    # "ảnh/phiếu của TÔI" chứ không cần xướng tên chức vụ ra). Chỉ khi có
    # NHIỀU ứng viên cùng lúc (Admin/Sếp xem được mọi chức vụ) mới cần
    # khớp tên để biết đang hỏi về chức vụ nào trong số đó.
    if len(ung_vien) == 1:
        cv = ung_vien[0]
    else:
        xep_hang = []
        for ung in ung_vien:
            tu_ten = [t for t in _chuan_hoa_khong_dau(ung.ten).split() if t not in ("va",)]
            so_khop = sum(1 for t in tu_ten if t in tin_tu)
            toi_thieu = 1 if len(tu_ten) <= 1 else 2
            if so_khop >= toi_thieu:
                xep_hang.append((so_khop, ung))

        if not xep_hang:
            return None
        xep_hang.sort(key=lambda x: x[0], reverse=True)
        if len(xep_hang) > 1 and xep_hang[0][0] == xep_hang[1][0]:
            return None  # khớp ngang nhau -> không đủ chắc chắn, để AI tự xử lý
        cv = xep_hang[0][1]

    return {
        "tra_loi": f"Đây là ảnh đã lưu cho chức vụ \"{cv.ten}\":",
        "duong_dan": None,
        "nhan_nut": None,
        "media": url_for("media", duong_dan=cv.anh),
    }


def tro_ly_tra_loi(nd: NguoiDung, tin_nhan: str, lich_su: list[dict]) -> tuple[dict | None, str | None]:
    """Trợ lý AI hỏi-đáp — trả lời dựa trên dữ liệu thật của đúng người
    đang hỏi (lấy theo nd, không lấy theo dữ liệu client gửi lên) + hướng
    dẫn sử dụng hệ thống. lich_su là vài lượt hỏi-đáp gần nhất do trình
    duyệt gửi lên để giữ mạch hội thoại, chỉ dùng tối đa 8 lượt gần nhất.

    Trả về (dict {tra_loi, duong_dan, nhan_nut, media}, lỗi) — các trường
    phụ có thể None nếu câu hỏi không cần gợi ý đi đâu / không có media.
    """
    ket_qua_anh = _thu_khop_anh_chuc_vu(nd, tin_nhan)
    if ket_qua_anh:
        return ket_qua_anh, None

    # Gộp câu hỏi hiện tại + vài lượt gần nhất để xét có cần nạp toàn bộ
    # mô tả chức vụ không — câu hỏi nối tiếp kiểu "chi tiết hơn" không lặp
    # lại tên chức vụ nhưng vẫn đang hỏi tiếp về chức vụ đã nhắc lượt trước.
    van_ban_gan_day = tin_nhan + " " + " ".join(
        str(m.get("noi_dung", "")) for m in lich_su[-4:] if isinstance(m, dict))
    boi_canh = _boi_canh_tro_ly(nd, van_ban_gan_day)
    messages = [{"role": "system",
                "content": _HUONG_DAN_HE_THONG_TRO_LY + "\n\nDữ liệu hiện tại:\n" + boi_canh}]
    for m in lich_su[-8:]:
        if isinstance(m, dict) and m.get("vai_tro") in ("user", "assistant") and m.get("noi_dung"):
            messages.append({"role": m["vai_tro"], "content": str(m["noi_dung"])[:2000]})
    messages.append({"role": "user", "content": tin_nhan[:2000]})

    noi_dung, loi = _goi_chatgpt_tin_nhan(
        messages, dang_json=True,
        model=current_app.config.get("OPENAI_MODEL_TRO_LY")
        or current_app.config.get("OPENAI_MODEL", "gpt-4o-mini"),
    )
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
        "tra_loi": ket_qua.get("tra_loi") or "Bạn có thể nói rõ hơn ý bạn muốn hỏi không?",
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