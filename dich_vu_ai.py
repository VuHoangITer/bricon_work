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
from models import NguoiDung, TroLySuDung, VaiTro, gio_vn_hien_tai, ngay_vn_hien_tai
from services import dieu_kien_viec_trong_ngay, lay_cai_dat, tinh_kpi

# ---------------------------------------------------------------------------
# GIỚI HẠN TRỢ LÝ AI — chỉ áp cho Nhân viên/Quản lý bộ phận (Sếp/Admin dùng
# thoải mái). Mặc định khiêm tốn vì gọi API ngoài tốn tiền thật; Admin có
# thể chỉnh lại 2 số này ở trang Thiết lập (không cần sửa code/deploy lại).
# ---------------------------------------------------------------------------
_GIOI_HAN_MAC_DINH_CAU_HOI_NGAY = 15
_GIOI_HAN_MAC_DINH_TOKEN_NGAY = 20000


def _bi_gioi_han_tro_ly(nd: NguoiDung) -> bool:
    """Sếp/Admin không giới hạn; Quản lý bộ phận + Nhân viên bị giới hạn."""
    return nd.vai_tro in (VaiTro.NHAN_VIEN, VaiTro.QUAN_LY)


def con_gioi_han_tro_ly(nd: NguoiDung) -> tuple[bool, str | None]:
    """Kiểm tra TRƯỚC khi cho hỏi trợ lý AI hôm nay. Trả về (còn được hỏi
    không, câu thông báo nếu đã hết hạn mức — None nếu còn được hỏi)."""
    trang_thai = trang_thai_gioi_han_tro_ly(nd)
    if not trang_thai["gioi_han"]:
        return True, None

    if trang_thai["gh_cau_hoi"] > 0 and trang_thai["so_cau_hoi"] >= trang_thai["gh_cau_hoi"]:
        return False, (f"Bạn đã hỏi Trợ lý AI {trang_thai['gh_cau_hoi']} lần hôm nay — "
                       f"hết hạn mức trong ngày rồi, mai hỏi tiếp nhé. Cần gấp "
                       f"thì nhờ quản lý/sếp hỏi giúp.")
    if trang_thai["gh_token"] > 0 and trang_thai["so_token"] >= trang_thai["gh_token"]:
        return False, ("Bạn đã dùng hết hạn mức Trợ lý AI hôm nay, mai hỏi "
                       "tiếp nhé.")
    return True, None


def trang_thai_gioi_han_tro_ly(nd: NguoiDung) -> dict:
    """Trạng thái hạn mức Trợ lý AI hôm nay của nd — dùng để hiển thị cho
    chính người dùng biết đã dùng bao nhiêu/còn bao nhiêu. Sếp/Admin trả
    về {"gioi_han": False}, không cần hiện số vì không bị giới hạn."""
    if not _bi_gioi_han_tro_ly(nd):
        return {"gioi_han": False}

    gh_cau_hoi = int(lay_cai_dat("tro_ly_gioi_han_cau_hoi_ngay")
                     or _GIOI_HAN_MAC_DINH_CAU_HOI_NGAY)
    gh_token = int(lay_cai_dat("tro_ly_gioi_han_token_ngay")
                  or _GIOI_HAN_MAC_DINH_TOKEN_NGAY)
    su_dung = TroLySuDung.query.filter_by(
        nguoi_dung_id=nd.id, ngay=ngay_vn_hien_tai()).first()
    return {
        "gioi_han": True,
        "so_cau_hoi": su_dung.so_cau_hoi if su_dung else 0,
        "gh_cau_hoi": gh_cau_hoi,
        "so_token": su_dung.so_token if su_dung else 0,
        "gh_token": gh_token,
    }


def ghi_nhan_su_dung_tro_ly(nd: NguoiDung, so_token: int = 0):
    """Cộng dồn 1 câu hỏi (+ số token nếu có gọi OpenAI thật) vào bảng theo
    dõi của đúng hôm nay. Ghi cho MỌI người kể cả Sếp/Admin (không bị chặn
    nhưng vẫn theo dõi được ai đang dùng nhiều) — chỉ con_gioi_han_tro_ly ở
    trên mới là nơi quyết định có chặn hay không."""
    hom_nay = ngay_vn_hien_tai()
    su_dung = TroLySuDung.query.filter_by(
        nguoi_dung_id=nd.id, ngay=hom_nay).first()
    if not su_dung:
        su_dung = TroLySuDung(nguoi_dung_id=nd.id, ngay=hom_nay,
                              so_cau_hoi=0, so_token=0)
        db.session.add(su_dung)
    su_dung.so_cau_hoi += 1
    su_dung.so_token += max(so_token, 0)

# ---------------------------------------------------------------------------
# TÍCH HỢP CHATGPT — gợi ý / tóm tắt yêu cầu chi tiết khi giao việc
# ---------------------------------------------------------------------------
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _goi_chatgpt_tho(goi_tin: dict, _da_thu_lai: bool = False, _bo_nhiet_do: bool = False,
                     _bo_reasoning: bool = False) -> tuple[dict | None, str | None, int]:
    """Gọi thẳng OpenAI với 1 payload đã dựng sẵn (model/messages/tools/...),
    tự xử lý rate-limit + model không cho chỉnh temperature. Trả về
    (message THÔ của OpenAI — dict có role/content/tool_calls, lỗi, số
    token) — dùng chung cho cả _goi_chatgpt_tin_nhan (chỉ cần đọc content)
    và trợ lý AI có gọi tool (cần đọc cả tool_calls, content lúc đó có
    thể là None)."""
    key = lay_cai_dat("openai_api_key")
    if not key:
        return None, "Chưa cấu hình OpenAI API key ở trang Thiết lập.", 0
    if not _bo_nhiet_do:
        goi_tin.setdefault("temperature", 0.4)
    # 1 số model dòng suy luận (VD gpt-5.6-terra) mặc định tự bật sẵn
    # reasoning_effort khi có "tools" trong payload, và /v1/chat/completions
    # không cho vừa dùng function tools vừa reasoning_effort khác "none" ->
    # phải set rõ "none" khi có tools. Model KHÔNG hỗ trợ tham số này (VD
    # gpt-4o-mini) lại báo lỗi ngược nếu mình tự thêm vào -> có nhánh strip
    # + gọi lại bên dưới cho trường hợp đó.
    if goi_tin.get("tools") and not _bo_reasoning:
        goi_tin.setdefault("reasoning_effort", "none")
    try:
        r = requests.post(
            _OPENAI_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=goi_tin,
            timeout=25,
        )
        du_lieu = r.json()
    except Exception as e:  # noqa: BLE001
        return None, f"Không gọi được OpenAI: {type(e).__name__}: {e}", 0

    if "error" in du_lieu:
        loi = du_lieu["error"]
        thong_diep = loi.get("message", "OpenAI báo lỗi không rõ nguyên nhân.")
        # Rate limit (429) thường chỉ tạm thời trong vài giây -> tự chờ
        # đúng khoảng OpenAI báo rồi thử lại 1 lần, thay vì bắt người dùng
        # thấy nguyên lỗi kỹ thuật mỗi khi nghẽn thoáng qua.
        if r.status_code == 429 and not _da_thu_lai:
            khop = re.search(r"try again in ([\d.]+)s", thong_diep)
            cho_giay = min(float(khop.group(1)), 25) + 0.5 if khop else 5
            time.sleep(cho_giay)
            return _goi_chatgpt_tho(goi_tin, _da_thu_lai=True, _bo_nhiet_do=_bo_nhiet_do,
                                    _bo_reasoning=_bo_reasoning)
        if r.status_code == 429:
            return None, "Trợ lý đang có nhiều người hỏi cùng lúc, bạn thử lại sau vài giây nhé.", 0

        # Vài model mới (VD dòng gpt-5.6) không cho tuỳ chỉnh temperature,
        # chỉ nhận đúng giá trị mặc định -> tự bỏ tham số này rồi gọi lại
        # 1 lần, không bắt người dùng thấy lỗi kỹ thuật của OpenAI mỗi khi
        # đổi sang model mới loại này.
        if not _bo_nhiet_do and (loi.get("param") == "temperature"
                                 or "temperature" in thong_diep.lower()):
            goi_tin.pop("temperature", None)
            return _goi_chatgpt_tho(goi_tin, _da_thu_lai=_da_thu_lai, _bo_nhiet_do=True,
                                    _bo_reasoning=_bo_reasoning)

        # Ngược lại: model KHÔNG hỗ trợ tham số reasoning_effort mình vừa tự
        # thêm ở trên (model cũ, không thuộc dòng suy luận) -> bỏ tham số
        # rồi gọi lại 1 lần.
        # OpenAI báo lỗi này theo 2 kiểu: có param="reasoning_effort", HOẶC
        # param rỗng và chỉ ghi trong message ("Unrecognized request argument
        # supplied: reasoning_effort") — phải bắt cả 2 kiểu.
        if ("reasoning_effort" in goi_tin and not _bo_reasoning
                and (loi.get("param") == "reasoning_effort" or "reasoning_effort" in thong_diep)):
            goi_tin.pop("reasoning_effort", None)
            return _goi_chatgpt_tho(goi_tin, _da_thu_lai=_da_thu_lai, _bo_nhiet_do=_bo_nhiet_do,
                                    _bo_reasoning=True)

        return None, thong_diep, 0
    so_token = int((du_lieu.get("usage") or {}).get("total_tokens") or 0)
    try:
        return du_lieu["choices"][0]["message"], None, so_token
    except (KeyError, IndexError):
        return None, "Phản hồi từ OpenAI không đúng định dạng mong đợi.", so_token


def _goi_chatgpt_tin_nhan(messages: list[dict], dang_json: bool = False,
                          model: str | None = None) -> tuple[str | None, str | None, int]:
    """Gọi OpenAI với danh sách messages đầy đủ — hỗ trợ nhiều lượt hội
    thoại (dùng cho trợ lý AI), không chỉ 1 cặp system/user đơn.
    dang_json=True bắt OpenAI trả về đúng 1 object JSON hợp lệ.
    model=None -> dùng OPENAI_MODEL mặc định (việc đơn giản như gợi ý/tóm
    tắt mô tả); truyền model cụ thể để override, VD trợ lý chat cần model
    mạnh hơn để đọc hiểu ngữ cảnh dài mà không bị rối.
    Trả về thêm số token đã dùng (total_tokens theo OpenAI báo) để nơi gọi
    ghi nhận vào hạn mức — 0 nếu lỗi/chưa gọi được."""
    model = model or current_app.config.get("OPENAI_MODEL", "gpt-4o-mini")
    goi_tin = {"model": model, "messages": messages}
    if dang_json:
        goi_tin["response_format"] = {"type": "json_object"}
    msg, loi, so_token = _goi_chatgpt_tho(goi_tin)
    if loi:
        return None, loi, so_token
    noi_dung = (msg or {}).get("content")
    if noi_dung is None:
        return None, "Phản hồi từ OpenAI không đúng định dạng mong đợi.", so_token
    return noi_dung.strip(), None, so_token


def _goi_chatgpt_voi_cong_cu(messages: list[dict], tools: list[dict], dang_json: bool = False,
                             model: str | None = None) -> tuple[dict | None, str | None, int]:
    """Giống _goi_chatgpt_tin_nhan nhưng cho OpenAI TỰ QUYẾT ĐỊNH có cần gọi
    1 trong các 'tools' (function calling) hay không, để tự tra cứu thêm dữ
    liệu ngoài những gì đã nhét sẵn trong ngữ cảnh — thay vì mình phải đoán
    trước mọi câu hỏi có thể gặp rồi nhét cứng vào context. Trả về NGUYÊN
    message thô (có thể có tool_calls, content=None khi đó) để nơi gọi tự
    chạy vòng lặp: nếu có tool_calls thì thực thi rồi gọi lại hàm này, nếu
    không thì content chính là câu trả lời cuối."""
    model = model or current_app.config.get("OPENAI_MODEL", "gpt-4o-mini")
    goi_tin = {"model": model, "messages": messages, "tools": tools}
    if dang_json:
        goi_tin["response_format"] = {"type": "json_object"}
    return _goi_chatgpt_tho(goi_tin)


def _goi_chatgpt(he_thong: str, nguoi_dung_hoi: str) -> tuple[str | None, str | None]:
    """Gọi 1 lượt chat đơn giản tới OpenAI. Trả về (nội dung trả lời, lỗi).
    Dùng cho gợi ý/tóm tắt mô tả (chỉ Quản lý/Sếp/Admin gọi được, không bị
    giới hạn hạn mức) nên bỏ qua số token trả về, không cần đếm."""
    noi_dung, loi, _so_token = _goi_chatgpt_tin_nhan([
        {"role": "system", "content": he_thong},
        {"role": "user", "content": nguoi_dung_hoi},
    ])
    return noi_dung, loi


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
    "Bạn là \"Trợ lý công việc\" — trợ lý ảo của BRICON WORK, phần mềm nội bộ quản lý giao việc, "
    "chấm công, KPI, xin nghỉ phép, thông báo nội bộ, đề xuất (tạm ứng/"
    "công việc) và đóng gói đơn hàng của công ty BRICON. Trả lời tiếng Việt, ngắn gọn, thân "
    "thiện, đúng trọng tâm.\n\n"

    "ĐỊNH DẠNG tra_loi: nếu câu trả lời có từ 2 ý/mục trở lên (VD: liệt kê "
    "nhiệm vụ 1 chức vụ, liệt kê nhiều việc, nhiều người, các bước làm...), "
    "PHẢI xuống dòng rõ ràng theo từng mục — dùng gạch đầu dòng \"-\" hoặc "
    "số thứ tự, có thể nhóm theo tiêu đề nhỏ nếu nội dung dài. TUYỆT ĐỐI "
    "không dồn nhiều ý vào 1 đoạn văn dài nối bằng dấu chấm phẩy — người "
    "đọc trên điện thoại cần dễ nhìn, dễ lướt, kể cả khi đây là câu trả lời "
    "\"tóm tắt\" hay \"ngắn gọn\" đầu tiên chứ không chỉ khi được hỏi lại "
    "\"chi tiết hơn\". Câu trả lời chỉ có 1 ý (chào hỏi, xác nhận 1 thông "
    "tin, trả lời có/không) thì viết 1 câu bình thường, không cần xuống "
    "dòng.\n\n"

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
    "- Xem KPI: menu KPI, chọn khoảng ngày, thang điểm 0-5 sao.\n"
    "- Gửi đề xuất (tạm ứng lương hoặc đề xuất công việc): menu Đề xuất → "
    "chọn loại → nhập nội dung, chi phí dự kiến nếu có, có thể đính kèm "
    "nhiều ảnh/tệp minh chứng → ký tên điện tử → Gửi. Sếp/Admin hoặc Quản "
    "lý bộ phận của người gửi sẽ duyệt (cũng ký tên + có thể đính kèm ảnh/"
    "tệp), kết quả báo lại qua Zalo. Sếp/Quản trị không cần gửi đề xuất.\n"
    "- Xem/gửi thông báo nội bộ: menu Thông báo — chỉ Sếp/Quản lý mới gửi "
    "được thông báo tới nhân viên, mọi người đều xem được thông báo đã "
    "gửi.\n"
    "- Đóng gói đơn hàng: menu Đóng gói → quét mã vận đơn bằng camera (hoặc "
    "gõ tay) → chụp ảnh kiện hàng đã gói (được nhiều ảnh) → Lưu, hệ thống "
    "ghi nhận người gói và gửi ảnh vào nhóm Zalo quản lý. Tra cứu ai gói đơn "
    "nào: trang Đóng gói → gõ mã vận đơn. Mọi câu hỏi về dữ liệu đóng gói "
    "(đơn nào đã gói, ai gói, hôm nay gói bao nhiêu đơn...) → gọi tool "
    "tra_cuu_dong_goi.\n\n"

    "DỮ LIỆU: chỉ dùng đúng dữ liệu thật cung cấp bên dưới cho số liệu/tên "
    "việc/lịch sử cụ thể — không bịa. Nếu có công cụ (tool/function) phù hợp "
    "để tự tra cứu thêm dữ liệu đang thiếu thì GỌI công cụ đó trước khi trả "
    "lời, đừng nói \"chưa có dữ liệu\" khi có tool tra cứu được đúng thứ đang "
    "hỏi. Chỉ khi không có tool nào phù hợp và dữ liệu bên dưới cũng không "
    "có thì mới nói rõ chưa có, đừng đoán. Nhưng nếu được hỏi Ý KIẾN/ĐỀ "
    "XUẤT/NHẬN XÉT dựa trên dữ liệu đã có (VD: đề xuất cho 1 nhân viên khi "
    "đã biết KPI của họ) thì CHỦ ĐỘNG đưa góc nhìn — đó là suy luận trên số "
    "liệu thật, không phải bịa đặt, đừng từ chối.\n\n"

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
    "công hộ, không tự gửi/duyệt đề xuất hộ, không tự gửi thông báo hộ) — "
    "đây là giới hạn của CHÍNH BẠN, không phải giới hạn quyền của "
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
    '"media": <null, hoặc 1 chuỗi đường dẫn, hoặc MẢNG nhiều chuỗi đường '
    'dẫn ảnh/video/ghi âm để hiện kèm câu trả lời>}\n\n'

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
    "- \"/thong-bao/\" — trang thông báo nội bộ\n"
    "- \"/de-xuat/\" — trang đề xuất (danh sách của mình, cần duyệt, lịch sử)\n"
    "- \"/de-xuat/moi/tam_ung\" — gửi đề xuất tạm ứng mới\n"
    "- \"/de-xuat/moi/cong_viec\" — gửi đề xuất công việc mới\n"
    "- \"/dong-goi/\" — lịch sử + tra cứu đóng gói theo mã vận đơn\n"
    "- \"/dong-goi/moi\" — ghi nhận đóng gói mới (quét mã + chụp ảnh)\n"
    "Nếu câu hỏi không cần gợi ý bấm đi đâu (VD: chỉ hỏi thông tin chung, "
    "chào hỏi), để duong_dan và nhan_nut là null.\n\n"

    "MEDIA: dữ liệu bên dưới có thể kèm theo các đoạn dạng [media:đường-dẫn] "
    "ngay sau 1 việc hoặc 1 chức vụ có đối chứng/ảnh minh hoạ; kết quả tool "
    "tra_cuu_dong_goi cũng có trường 'anh' chứa đường dẫn ảnh kiện hàng — "
    "dùng y hệt như [media:...]. Người hỏi "
    "muốn XEM/HIỆN 1 ảnh/video/ghi âm cụ thể và có đúng 1 đoạn [media:...] "
    "liên quan trong dữ liệu → COPY Y NGUYÊN chuỗi đường dẫn đó (không kèm "
    "chữ \"media:\" hay dấu ngoặc) vào trường media. Đây CHỈ là chọn 1 đường "
    "dẫn CÓ SẴN để app tự hiển thị — KHÔNG phải bạn tự tạo/tải lên/đính kèm "
    "file thật, nên luôn là hành động AN TOÀN và ĐÚNG CHỨC NĂNG khi có "
    "[media:...] khớp — TUYỆT ĐỐI không từ chối kiểu \"tôi không thể cung "
    "cấp ảnh\" khi có [media:...] phù hợp. Không có đoạn nào phù hợp thì để "
    "media là null, không tự bịa đường dẫn. Người hỏi muốn xem ảnh của 1 "
    "sản phẩm/đối tượng có NHIỀU ảnh mà không nói rõ ảnh nào (VD \"ảnh keo "
    "chà ron màu\") → trả media là MẢNG chứa TẤT CẢ các đường dẫn ảnh của "
    "đối tượng đó (tối đa 6), đừng chỉ chọn 1 ảnh."
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
    from models import AnhGoiHang, AnhSanPhamAI, ChucVu, DinhKem
    dk = DinhKem.query.filter_by(duong_dan=duong_dan).first()
    if dk:
        return duong_dan if nd.duoc_xem_viec(dk.cong_viec) else None
    cv = ChucVu.query.filter_by(anh=duong_dan).first()
    if cv:
        return duong_dan
    # Ảnh sản phẩm — thông tin chung công ty, ai đăng nhập cũng xem được
    # (cùng quy tắc với route /media).
    if AnhSanPhamAI.query.filter_by(duong_dan=duong_dan).first():
        return duong_dan
    # Ảnh đóng gói — người gói hoặc Quản lý trở lên (cùng quy tắc /media).
    agh = AnhGoiHang.query.filter_by(duong_dan=duong_dan).first()
    if agh:
        return duong_dan if (agh.goi_hang.nguoi_goi_id == nd.id or nd.la_quan_ly) else None
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


# Từ khoá nhận diện câu hỏi liên quan sản phẩm — dựa theo tên các nhóm sản
# phẩm thật của BRICON (đã chuẩn hoá bỏ dấu). "Thông tin sản phẩm" tách
# riêng khỏi "Thông tin chung" vì khá dài (danh mục + mô tả nhiều sản
# phẩm) — chỉ nạp khi thật sự cần, tránh tốn token cho câu hỏi không liên
# quan (chấm công, KPI, nghỉ phép...).
_TU_KHOA_SAN_PHAM = (
    "san pham", "keo dan gach", "dan gach", "cha ron", "chong tham",
    "2 thanh phan", "hai thanh phan", "tram tuong", "mo kinh", "epoxy",
    "dung cu thi cong", "phu gia", "quy cach", "thong so", "ky thuat",
    "gia ban", "bao gia", "catalogue", "catalog", "tds",
    # câu hỏi tư vấn thi công/định mức thường KHÔNG nhắc tên sản phẩm
    # (VD "1 túi dùng được bao nhiêu m2", "gạch 60x60 ron 3mm cần bao
    # nhiêu") — thiếu các từ này thì AI không được nạp kiến thức sản phẩm
    "keo", "ron", "gach", "dinh muc", "thi cong", "m2", "met vuong",
    "pha nuoc", "ty le pha", "nha tam", "nha ve sinh", "ngoai that",
    "noi that", "nam moc", "han su dung", "bao quan",
)


def _tu_ten_san_pham() -> set[str]:
    """Các từ đặc trưng trong TÊN sản phẩm đã nhập ở Info AI — câu hỏi nhắc
    tới tên sản phẩm (dù không chứa từ khoá chung nào) cũng phải nạp ngữ
    cảnh sản phẩm."""
    from models import SanPhamAI
    bo_qua = {"bricon", "keo", "va", "cho", "loai"}
    return {
        t for (ten,) in db.session.query(SanPhamAI.ten).all()
        for t in _chuan_hoa_khong_dau(ten).split() if len(t) > 3 and t not in bo_qua
    }


# ---------------------------------------------------------------------------
# FAQ — Câu hỏi thường gặp. KHÔNG nạp cả bộ (vài chục câu, hàng chục nghìn
# ký tự → tốn token, nhanh hết hạn mức ngày của nhân viên); mỗi lần hỏi chỉ
# chấm điểm độ trùng từ giữa câu đang hỏi và từng câu FAQ, lấy vài câu cao
# điểm nhất. Đơn giản, không cần embedding/vector DB — đủ tốt với vài chục
# tới vài trăm câu.
# ---------------------------------------------------------------------------
_TU_BO_QUA_FAQ = {
    "la", "va", "co", "khong", "ko", "k", "cua", "cho", "voi", "thi", "nhu", "the", "nao",
    "gi", "bao", "nhieu", "duoc", "dung", "su", "cac", "mot", "nhung", "nay", "do", "khi",
    "de", "o", "tai", "toi", "minh", "em", "anh", "chi", "ban", "a", "nhe", "vay", "sao",
    "hay", "hoac", "neu", "trong", "ra", "vao", "len", "di", "can", "phai", "lam", "rang",
    "thuong", "bricon", "tra", "loi", "hoi", "vui", "long",
}
_SO_CAU_FAQ_TOI_DA = 4


def _tu_faq(van_ban: str) -> set[str]:
    tu = re.sub(r"[^a-z0-9]+", " ", _chuan_hoa_khong_dau(van_ban)).split()
    return {t for t in tu if t not in _TU_BO_QUA_FAQ and (len(t) > 1 or t.isdigit())}


def tach_cau_faq(noi_dung: str) -> list[dict]:
    """Tách bộ FAQ Markdown thành từng câu: mỗi câu bắt đầu bằng dòng "## ..."
    (bỏ số thứ tự đầu dòng nếu có), phần trả lời là các dòng sau cho tới câu
    kế tiếp. Dòng "---" / phần trước câu đầu tiên bị bỏ qua."""
    ds, hien_tai = [], None
    for dong in (noi_dung or "").splitlines():
        m = re.match(r"^\s*##\s+(.*\S)\s*$", dong)
        if m and not dong.lstrip().startswith("###"):
            if hien_tai:
                ds.append(hien_tai)
            hoi = re.sub(r"^\d+\s*[.)]\s*", "", m.group(1)).strip()
            hien_tai = {"hoi": hoi, "dap": []}
        elif hien_tai is not None:
            if dong.strip() == "---":
                continue
            hien_tai["dap"].append(dong)
    if hien_tai:
        ds.append(hien_tai)
    for c in ds:
        c["dap"] = "\n".join(c["dap"]).strip()
    return [c for c in ds if c["hoi"] and c["dap"]]


def _faq_lien_quan(van_ban: str, so_cau: int = _SO_CAU_FAQ_TOI_DA) -> list[dict]:
    """Chọn tối đa so_cau câu FAQ trùng từ nhiều nhất với van_ban. Từ trùng
    ở phần CÂU HỎI nặng gấp 3 phần trả lời; mã sản phẩm (UB102, UB601D...)
    trùng khớp được cộng đậm vì gần như chắc chắn đúng câu cần tìm."""
    cac_cau = tach_cau_faq(lay_cai_dat("faq_bricon", "") or "")
    tu_hoi = _tu_faq(van_ban)
    if not cac_cau or not tu_hoi:
        return []
    xep = []
    for c in cac_cau:
        tu_q, tu_a = _tu_faq(c["hoi"]), _tu_faq(c["dap"])
        diem = 3 * len(tu_hoi & tu_q) + len(tu_hoi & (tu_a - tu_q))
        diem += 6 * len({t for t in tu_hoi & (tu_q | tu_a) if re.search(r"[a-z]\d|\d[a-z]", t)})
        if diem >= 4:  # tối thiểu ~ 1 từ khớp câu hỏi + 1 từ khớp trả lời
            xep.append((diem, c))
    xep.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in xep[:so_cau]]


def _can_boi_canh_san_pham(van_ban: str) -> bool:
    tin_chuan = re.sub(r"[^a-z0-9]+", " ", _chuan_hoa_khong_dau(van_ban))
    tu = set(tin_chuan.split())
    # So cụm từ khoá theo NGUYÊN TỪ (không so chuỗi con) để "ron" không
    # khớp nhầm vào "trong", "keo" không khớp "keodan"...
    chuoi_bao = f" {' '.join(tin_chuan.split())} "
    if any(f" {cum} " in chuoi_bao for cum in _TU_KHOA_SAN_PHAM):
        return True
    return bool(tu & _tu_ten_san_pham())


def _boi_canh_tro_ly(nd: NguoiDung, van_ban_gan_day: str = "") -> str:
    """Dựng đoạn ngữ cảnh dữ liệu thật của người đang hỏi, nhét vào system
    prompt để trợ lý AI trả lời đúng, không bịa.

    Có 3 lớp thông tin tổ chức nạp thêm ngoài dữ liệu việc/chấm công:
    - Thông tin chung công ty (chế độ, chính sách...) — áp dụng cho mọi
      người, quản lý ở trang Info AI, LUÔN nạp vì tương đối ngắn và hầu
      như câu nào cũng có thể cần tới.
    - Thông tin sản phẩm (danh mục, mô tả, thông số) — tách riêng vì khá
      dài, CHỈ nạp khi van_ban_gan_day cho thấy câu hỏi thật sự liên quan
      sản phẩm (xem _can_boi_canh_san_pham).
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
    from models import BoPhan, ChamCong, CongViec, DeXuat, TrangThai, TrangThaiDeXuat, XinNghi

    hom_nay = ngay_vn_hien_tai()
    ngay_mai = hom_nay + timedelta(days=1)
    dong = [f"Hôm nay là {hom_nay:%d/%m/%Y}. Người đang hỏi: {nd.ho_ten} ({nd.ten_vai_tro})."]

    thong_tin_chung = lay_cai_dat("thong_tin_chung_cong_ty")
    if thong_tin_chung:
        dong.append("--- Thông tin chung công ty (áp dụng cho mọi người) ---\n"
                    + thong_tin_chung)

    # FAQ: chỉ tìm theo CÂU ĐANG HỎI (phần đầu van_ban_gan_day), không theo
    # cả lịch sử — tránh kéo lại FAQ của câu hỏi trước không còn liên quan.
    cau_faq = _faq_lien_quan(van_ban_gan_day.split("\n")[0])
    if cau_faq:
        dong.append(
            "--- Câu hỏi thường gặp (FAQ chính thức của BRICON) liên quan tới câu "
            "đang hỏi — ưu tiên trả lời theo đúng nội dung này nếu khớp câu hỏi, "
            "không khớp thì bỏ qua. Nếu FAQ mâu thuẫn với phần kiến thức riêng "
            "của 1 sản phẩm cụ thể bên dưới, thì với sản phẩm đó ưu tiên phần "
            "kiến thức riêng ---\n" +
            "\n\n".join(f"H: {c['hoi']}\nĐ: {c['dap']}" for c in cau_faq))

    thong_tin_san_pham = lay_cai_dat("thong_tin_san_pham")
    can_san_pham = _can_boi_canh_san_pham(van_ban_gan_day)
    if thong_tin_san_pham and can_san_pham:
        dong.append("--- Thông tin sản phẩm (chỉ nạp khi câu hỏi liên quan sản phẩm) ---\n"
                    + thong_tin_san_pham)

    if can_san_pham:
        from models import SanPhamAI
        # Nạp MỌI sản phẩm (trước đây lọc "if sp.anh" nên sản phẩm chưa có
        # ảnh bị bỏ qua hoàn toàn — AI không hề đọc được mô tả của nó). Mỗi
        # sản phẩm tách bằng dấu ranh giới rõ ràng để AI không lẫn thông
        # số giữa các sản phẩm cùng nhóm (VD keo chà ron màu vs nội thất).
        ds_sp = SanPhamAI.query.order_by(SanPhamAI.ten).all()
        if ds_sp:
            khoi = []
            for sp in ds_sp:
                phan = [f"===== SẢN PHẨM: {sp.ten} =====",
                        (sp.mo_ta or "(chưa có mô tả)").strip()]
                if sp.anh:
                    phan.append("Ảnh kèm theo: " + " | ".join(
                        f"{(a.nhan + ': ') if a.nhan else ''}[media:{a.duong_dan}]"
                        for a in sp.anh))
                phan.append(f"===== HẾT SẢN PHẨM: {sp.ten} =====")
                khoi.append("\n".join(phan))
            dong.append(
                "--- Danh mục sản phẩm BRICON (kiến thức sản phẩm do Admin nhập ở "
                "Info AI). QUY TẮC: mỗi sản phẩm nằm giữa 2 dòng ===== — thông "
                "số/phạm vi/định mức của sản phẩm nào CHỈ áp dụng cho đúng sản phẩm "
                "đó, không lấy chéo sang sản phẩm khác. Câu hỏi chưa rõ đang nói "
                "sản phẩm nào (VD chỉ nói \"keo chà ron\" mà có nhiều loại) thì hỏi "
                "lại cho rõ. Trong mô tả có thể có các mục \"Quy tắc/Nguyên tắc cho "
                "bot\", \"Câu trả lời mẫu\" — PHẢI tuân thủ khi trả lời; khi tính "
                "định mức thì tra đúng bảng số liệu có sẵn, không tự suy ra số mới "
                "---\n" + "\n\n".join(khoi)
            )

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

        de_xuat_cua_toi = (
            DeXuat.query.filter_by(nguoi_de_xuat_id=nd.id)
            .order_by(DeXuat.tao_luc.desc()).limit(5).all()
        )
        if de_xuat_cua_toi:
            dong.append("Đề xuất gần đây của chính người đang hỏi: " + "; ".join(
                f"{dx.ten_loai} - {dx.ten_trang_thai} (gửi {dx.tao_luc:%d/%m})"
                for dx in de_xuat_cua_toi))
        else:
            dong.append("Người này chưa gửi đề xuất nào.")

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
            so_cho_duyet_dx = len([
                dx for dx in DeXuat.query.filter_by(trang_thai=TrangThaiDeXuat.CHO_DUYET).all()
                if nd.duoc_duyet_de_xuat(dx)
            ])
            dong.append(f"Có {so_cho_duyet_dx} đề xuất đang chờ người này duyệt — cần chi "
                        f"tiết (đề xuất nào, của ai, đã duyệt/từ chối trước đó...) thì gọi "
                        f"tool tra_cuu_de_xuat, đừng bịa.")
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
        so_cho_duyet_dx = DeXuat.query.filter_by(trang_thai=TrangThaiDeXuat.CHO_DUYET).count()
        dong.append(f"Có {so_cho_duyet_dx} đề xuất (tạm ứng/công việc) đang chờ duyệt toàn "
                    f"công ty, Sếp/Admin duyệt được hết — cần chi tiết (đề xuất nào, của "
                    f"ai, đã duyệt/từ chối trước đó, theo loại...) thì gọi tool "
                    f"tra_cuu_de_xuat, đừng bịa.")
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


def _thu_khop_anh_chuc_vu(nd: NguoiDung, tin_nhan: str, ngu_canh: str = "") -> dict | None:
    """Khớp trực tiếp (KHÔNG qua AI) khi người hỏi xin xem ảnh minh hoạ 1
    chức vụ cụ thể — tách riêng khỏi luồng AI vì mô hình chat đôi khi tự ý
    từ chối "gửi ảnh" (hiểu nhầm là phải tự tạo/đính kèm file thật) dù chỉ
    cần trả về đúng 1 đường dẫn đã có sẵn. Khớp tên cứng ở đây đảm bảo luôn
    trả được ảnh khi tên chức vụ đủ rõ, không phụ thuộc AI có làm đúng
    hướng dẫn hay không.

    Chỉ trả kết quả khi khớp ĐÚNG 1 chức vụ và người hỏi có quyền xem
    (Admin/Sếp xem được mọi chức vụ; nhân viên thường chỉ chức vụ của
    chính mình) — nếu mơ hồ (0 hoặc >1 khớp cùng điểm), trả None để câu
    hỏi rơi xuống luồng AI bình thường xử lý tiếp.

    ngu_canh (không bắt buộc): vài lượt hỏi-đáp gần nhất, CHỈ dùng để suy
    ra chức vụ đang nói tới khi câu hỏi hiện tại quá ngắn (VD người dùng
    chỉ gõ tiếp "ảnh" sau khi đã nhắc tên chức vụ ở lượt trước) — không
    dùng để xét có đang xin xem ảnh hay không, việc đó luôn chỉ xét đúng
    câu hỏi hiện tại (tin_nhan)."""
    from models import ChucVu

    def _tach_tu(van_ban: str) -> set[str]:
        return set(re.sub(r"[^a-z0-9]+", " ", _chuan_hoa_khong_dau(van_ban)).split())

    tu_xin_anh = {"anh", "hinh", "photo", "image", "phieu"}
    tin_tu = _tach_tu(tin_nhan)
    if not tin_tu & tu_xin_anh:
        return None

    # Từ chung của MỌI chức vụ ("nhân viên", "kiêm"...) và từ chức năng —
    # không dùng để phân biệt chức vụ này với chức vụ khác.
    tu_chung = {"nhan", "vien", "va", "kiem", "truong", "pho", "bo", "phan"}
    tu_bo_qua = tu_xin_anh | {"cho", "xem", "toi", "minh", "em", "t", "m", "gui", "cua", "voi",
                              "the", "nay", "do", "cac", "la", "co", "nao", "di", "nhe", "a",
                              "ban", "dau", "chuc", "vu", "vi", "tri"}

    def _chon(bo_tu: set):
        xep = []
        for ung in ung_vien:
            tu_rieng = _tach_tu(ung.ten) - tu_chung - tu_bo_qua
            diem = len(tu_rieng & bo_tu)
            if diem:
                xep.append((diem, ung))
        xep.sort(key=lambda x: x[0], reverse=True)
        if not xep or (len(xep) > 1 and xep[0][0] == xep[1][0]):
            return None
        return xep[0][1]

    nhac_chuc_vu = bool({"chuc", "vu"} <= tin_tu or {"vi", "tri"} <= tin_tu
                        or {"lo", "trinh"} <= tin_tu or {"danh", "gia"} <= tin_tu)
    # Câu hỏi đang nói về SẢN PHẨM (keo, chà ron, gạch, chống thấm, tên sản
    # phẩm đã nhập…) mà không nhắc gì tới chức vụ -> nhường cho bộ khớp ảnh
    # sản phẩm, tránh "ảnh keo chà ron MÀU" khớp nhầm chức vụ "pha MÀU".
    tu_san_pham = {"keo", "cha", "ron", "gach", "tds", "chong", "tham", "epoxy", "vua",
                   "catalog", "catalogue"} | _tu_ten_san_pham()
    if not nhac_chuc_vu and tin_tu & tu_san_pham:
        return None

    def _tra_loi(noi_dung: str) -> dict:
        return {"tra_loi": noi_dung, "duong_dan": None, "nhan_nut": None, "media": None}

    if not nd.la_admin_sep:
        # (a) Nêu tên 1 chức vụ KHÁC (VD NV kho hỏi "phiếu đánh giá nhân viên
        #     kinh doanh") -> từ chối rõ ràng, không trả nhầm phiếu của mình.
        khac = [c for c in ChucVu.query.all()
                if c.id != (nd.chuc_vu_id or 0)
                and (_tach_tu(c.ten) - tu_chung - tu_bo_qua) & tin_tu]
        noi_ro_cua_minh = bool(nd.chuc_vu and (_tach_tu(nd.chuc_vu.ten) - tu_chung - tu_bo_qua) & tin_tu)
        if khac and not noi_ro_cua_minh:
            cua_minh = f" — chức vụ của bạn là \"{nd.chuc_vu.ten}\"" if nd.chuc_vu else ""
            return _tra_loi(f"Bạn chỉ xem được phiếu/thông tin của chức vụ mình{cua_minh}. "
                            f"Thông tin chức vụ \"{khac[0].ten}\" chỉ quản lý/Ban giám đốc mới xem "
                            f"được. Muốn xem phiếu của bạn, hỏi \"phiếu đánh giá của tôi\".")
        # (b) Hỏi phiếu/ảnh chức vụ nhưng tài khoản chưa gán chức vụ, hoặc
        #     chức vụ chưa có ảnh -> nói rõ nguyên nhân + ai xử lý, thay vì để
        #     AI trả lời chung chung "chưa có dữ liệu".
        if nhac_chuc_vu or "phieu" in tin_tu:
            if not nd.chuc_vu:
                return _tra_loi("Tài khoản của bạn chưa được gán chức vụ nên chưa có phiếu đánh giá. "
                                "Nhờ Admin gán chức vụ cho bạn ở Quản trị → Nhân sự → Nhân viên.")
            if not nd.chuc_vu.anh:
                return _tra_loi(f"Chức vụ \"{nd.chuc_vu.ten}\" của bạn chưa có ảnh phiếu đánh giá. "
                                "Nhờ Admin tải ảnh lên ở Quản trị → Trợ lý AI → Chức vụ.")

    if nd.la_admin_sep:
        ung_vien = ChucVu.query.filter(ChucVu.anh.isnot(None)).all()
    elif nd.chuc_vu and nd.chuc_vu.anh:
        ung_vien = [nd.chuc_vu]
    else:
        ung_vien = []
    if not ung_vien:
        return None

    # 1) Câu hỏi nêu rõ tên chức vụ -> chỉ xét ĐÚNG câu hiện tại (không đọc
    #    câu trả lời cũ của AI — từng khiến "giao việc/người nhận" trong câu
    #    trả lời trước kéo nhầm sang chức vụ "tài xế kiêm giao nhận").
    cv = _chon(tin_tu)
    if not cv:
        noi_dung_con_lai = tin_tu - tu_bo_qua - tu_chung
        if len(ung_vien) == 1 and (nhac_chuc_vu or not noi_dung_con_lai):
            # 2) Nhân viên thường (chỉ có đúng chức vụ của mình) hỏi kiểu
            #    "ảnh chức vụ của tôi", "phiếu đánh giá", "lộ trình" — hoặc
            #    chỉ gõ trơn "ảnh"/"phiếu". Hỏi ảnh thứ khác (VD "ảnh keo chà
            #    ron") thì KHÔNG trả ảnh chức vụ nữa.
            cv = ung_vien[0] if (nhac_chuc_vu or _chon(_tach_tu(ngu_canh)) or not ngu_canh) else None
        elif (len(ung_vien) > 1 and not (noi_dung_con_lai - {"danh", "gia", "lo", "trinh"})
              and (nhac_chuc_vu or "phieu" in tin_tu)):
            # 4) Admin/Sếp hỏi CHUNG CHUNG "phiếu đánh giá nhân viên", "ảnh
            #    các chức vụ", "lộ trình"… không nêu chức vụ nào -> trả TẤT
            #    CẢ ảnh chức vụ (kèm danh sách tên) thay vì báo không có.
            ds = sorted(ung_vien, key=lambda x: x.ten)
            hien = ds[:_SO_ANH_TOI_DA]
            tra_loi = (f"Có {len(ds)} chức vụ có ảnh/phiếu đã lưu:\n"
                       + "\n".join(f"{i}. {c.ten}" for i, c in enumerate(hien, 1)))
            if len(ds) > len(hien):
                tra_loi += (f"\n… và {len(ds) - len(hien)} chức vụ khác — hỏi kèm tên chức vụ "
                            f"để xem đúng phiếu cần tìm.")
            return {
                "tra_loi": tra_loi,
                "duong_dan": None,
                "nhan_nut": None,
                "media": [url_for("media", duong_dan=c.anh) for c in hien],
            }
        elif not noi_dung_con_lai:
            # 3) Chỉ gõ trơn "ảnh"/"cho xem ảnh" -> suy theo câu hỏi TRƯỚC
            #    của chính người dùng (ngu_canh chỉ chứa câu người dùng).
            cv = _chon(_tach_tu(ngu_canh))
    if not cv:
        return None

    return {
        "tra_loi": f"Đây là ảnh đã lưu cho chức vụ \"{cv.ten}\":",
        "duong_dan": None,
        "nhan_nut": None,
        "media": url_for("media", duong_dan=cv.anh),
    }


_SO_ANH_TOI_DA = 6  # tối đa số ảnh hiện kèm 1 câu trả lời


def _thu_khop_anh_san_pham(tin_nhan: str, ngu_canh: str = "") -> dict | None:
    """Khớp trực tiếp (KHÔNG qua AI) khi người hỏi xin xem ảnh 1 sản phẩm
    cụ thể (TDS, bảng định mức, bảng màu...) — cùng lý do tách khỏi luồng
    AI như _thu_khop_anh_chuc_vu (model đôi khi tự ý từ chối "gửi ảnh").

    Chỉ trả kết quả khi khớp rõ ràng: đúng 1 sản phẩm, VÀ (sản phẩm đó chỉ
    có 1 ảnh, hoặc khớp thêm được đúng 1 ảnh theo nhãn) — mơ hồ thì trả
    None để câu hỏi rơi xuống luồng AI bình thường (AI vẫn có đủ ngữ cảnh
    kèm nhãn từng ảnh để tự chọn).

    ngu_canh (không bắt buộc): vài lượt hỏi-đáp gần nhất, CHỈ dùng để suy
    ra sản phẩm/ảnh đang nói tới khi câu hỏi hiện tại quá ngắn (VD người
    dùng chỉ gõ tiếp "ảnh" sau khi đã nhắc tên sản phẩm ở lượt trước) —
    không dùng để xét có đang xin xem ảnh hay không, việc đó luôn chỉ xét
    đúng câu hỏi hiện tại (tin_nhan)."""
    from models import SanPhamAI

    def _tach_tu(van_ban: str) -> set[str]:
        return set(re.sub(r"[^a-z0-9]+", " ", _chuan_hoa_khong_dau(van_ban)).split())

    tin_tu = _tach_tu(tin_nhan)

    ung_vien = [sp for sp in SanPhamAI.query.all() if sp.anh]
    if not ung_vien:
        return None

    # CHỈ khớp trực tiếp khi câu hỏi XIN XEM ẢNH rõ ràng — câu hỏi tính
    # toán/tư vấn (dù có chữ "keo", "thi công"...) luôn để AI trả lời.
    tu_khoa_xin_anh = {"anh", "hinh", "photo", "image", "tds", "catalogue", "catalog"}
    if not (tin_tu & tu_khoa_xin_anh):
        return None

    # Từ chung của cả nhóm hàng — có mặt trong gần như MỌI sản phẩm nên
    # không phân biệt được sản phẩm này với sản phẩm khác.
    tu_chung_nhom = {"keo", "cha", "ron", "bricon", "san", "pham", "gach", "dan", "loai"}
    # Từ chức năng/xin ảnh — bỏ hẳn khi so khớp.
    tu_bo_qua = tu_khoa_xin_anh | {"cho", "xem", "toi", "minh", "em", "anh", "gui", "cua",
                                    "voi", "the", "nay", "do", "va", "cac", "la", "co", "nao",
                                    "di", "nhe", "a", "t", "m", "e", "ban", "dau", "hinh"}

    def _diem(sp, bo_tu: set) -> tuple[int, int]:
        """(điểm đặc trưng, điểm từ chung). Từ trong TÊN sản phẩm nặng gấp
        đôi từ trong NHÃN ảnh — VD "nội thất" vừa là tên sản phẩm "Keo Chà
        Ron Bricon Nội Thất" vừa nằm trong nhãn "TDS ... nội - ngoại thất"
        của keo chà ron màu, thì sản phẩm có "nội thất" ở TÊN phải thắng."""
        tu_ten = _tach_tu(sp.ten) - tu_bo_qua
        tu_nhan = set().union(*(_tach_tu(a.nhan or "") for a in sp.anh)) - tu_bo_qua - tu_ten
        diem_rieng = (2 * len((tu_ten - tu_chung_nhom) & bo_tu)
                      + len((tu_nhan - tu_chung_nhom) & bo_tu))
        diem_chung = len((tu_ten & tu_chung_nhom) & bo_tu)
        return diem_rieng, diem_chung

    def _chon(bo_tu: set):
        """Trả về sản phẩm thắng rõ ràng, hoặc None nếu không ai có từ đặc
        trưng / hoà điểm (mơ hồ -> để AI hỏi lại)."""
        xep = sorted(((_diem(sp, bo_tu), sp) for sp in ung_vien),
                     key=lambda x: x[0], reverse=True)
        xep = [x for x in xep if x[0][0] > 0]
        if not xep or (len(xep) > 1 and xep[0][0] == xep[1][0]):
            return None
        return xep[0][1]

    phan_con_lai = tin_tu - tu_bo_qua
    if phan_con_lai:
        # Câu hiện tại CÓ nêu gì đó -> chỉ xét đúng câu này. Nêu mỗi từ
        # chung ("ảnh keo chà ron") mà không có từ đặc trưng thì là mơ hồ
        # -> None, để AI hỏi lại loại nào, KHÔNG đoán theo lượt trước.
        sp = _chon(tin_tu)
    else:
        # Câu chỉ toàn từ xin ảnh ("ảnh", "cho xem ảnh") -> suy theo các câu
        # hỏi TRƯỚC của NGƯỜI DÙNG (ngu_canh chỉ chứa câu người dùng, không
        # lẫn câu trả lời của AI — câu trả lời AI liệt kê nhãn ảnh nên từng
        # kéo nhầm sang sản phẩm khác).
        sp = _chon(_tach_tu(ngu_canh))
    if not sp:
        return None

    # Chấm điểm theo từ ĐẶC TRƯNG RIÊNG của từng nhãn (loại bỏ các từ trùng
    # với tên sản phẩm, vì những từ đó lặp lại ở MỌI nhãn của cùng 1 sản
    # phẩm nên không phân biệt được ảnh nào với ảnh nào). Có nhãn khớp thì
    # trả TẤT CẢ ảnh cùng điểm cao nhất (VD hỏi "TDS" mà có 2 ảnh TDS thì
    # trả cả 2); không nhãn nào khớp (chỉ hỏi chung "ảnh keo chà ron màu")
    # thì trả TOÀN BỘ ảnh của sản phẩm — trước đây chỉ trả được 1 ảnh.
    tu_ten_sp = _tach_tu(sp.ten)
    xep_hang_anh = []
    for a in sp.anh:
        if not a.nhan:
            continue
        # giữ lại "tds"/"catalog" — chúng vừa là từ xin ảnh vừa phân biệt được ảnh
        tu_nhan_rieng = (_tach_tu(a.nhan) - tu_ten_sp - tu_chung_nhom
                         - (tu_bo_qua - {"tds", "catalog", "catalogue"}))
        so_khop = len(tu_nhan_rieng & tin_tu)
        if so_khop:
            xep_hang_anh.append((so_khop, a))
    if xep_hang_anh:
        cao_nhat = max(d for d, _ in xep_hang_anh)
        cac_anh = [a for d, a in xep_hang_anh if d == cao_nhat]
    else:
        cac_anh = list(sp.anh)
    cac_anh = cac_anh[:_SO_ANH_TOI_DA]

    if len(cac_anh) == 1:
        a = cac_anh[0]
        tra_loi = f"Đây là ảnh {(a.nhan + ' ') if a.nhan else ''}của sản phẩm \"{sp.ten}\":"
    else:
        tra_loi = (f"Sản phẩm \"{sp.ten}\" có {len(cac_anh)} ảnh:\n"
                   + "\n".join(f"{i}. {a.nhan or 'Ảnh ' + str(i)}"
                               for i, a in enumerate(cac_anh, 1)))
    return {
        "tra_loi": tra_loi,
        "duong_dan": None,
        "nhan_nut": None,
        "media": [url_for("media", duong_dan=a.duong_dan) for a in cac_anh],
    }


# ---------------------------------------------------------------------------
# CÔNG CỤ (function calling) — thay vì đoán trước MỌI câu hỏi có thể gặp
# rồi nhét cứng dữ liệu vào ngữ cảnh (không bao giờ đủ), để OpenAI TỰ GỌI
# đúng hàm khi câu hỏi cần dữ liệu ngoài những gì đã có sẵn. Ngữ cảnh
# (_boi_canh_tro_ly) vẫn giữ vài số liệu tổng quan rẻ tiền để trả lời
# nhanh câu hỏi chung chung, không cần gọi tool cho MỌI câu hỏi.
# ---------------------------------------------------------------------------

_CONG_CU_TRO_LY = [
    {
        "type": "function",
        "function": {
            "name": "tra_cuu_de_xuat",
            "description": (
                "Tra cứu Đề xuất (tạm ứng/công việc) trong ĐÚNG phạm vi người đang "
                "hỏi được xem — tự động giới hạn theo quyền (nhân viên thường chỉ "
                "thấy đề xuất của chính mình; Quản lý bộ phận thấy của bộ phận mình; "
                "Sếp/Admin thấy toàn công ty), không cần và không thể truyền phạm vi. "
                "CHỈ gọi khi câu hỏi cần dữ liệu về đề xuất mà phần 'Dữ liệu hiện tại' "
                "ở system prompt KHÔNG có sẵn — ví dụ hỏi về đề xuất ĐÃ DUYỆT, ĐÃ TỪ "
                "CHỐI, lọc theo loại cụ thể, hoặc xem lại lịch sử/nội dung chi tiết."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trang_thai": {
                        "type": "string",
                        "enum": ["cho_duyet", "da_duyet", "tu_choi", "tat_ca"],
                        "description": "Lọc theo trạng thái. Bỏ qua hoặc 'tat_ca' để lấy mọi trạng thái.",
                    },
                    "loai": {
                        "type": "string",
                        "enum": ["tam_ung", "cong_viec", "tat_ca"],
                        "description": "Lọc theo loại đề xuất. Bỏ qua hoặc 'tat_ca' để lấy mọi loại.",
                    },
                },
            },
        },
    },
]


def _tra_cuu_de_xuat_cho_ai(nd: NguoiDung, trang_thai: str | None = None,
                            loai: str | None = None, **_bo_qua) -> dict:
    """Hàm THẬT được thực thi khi OpenAI gọi tool 'tra_cuu_de_xuat'. Tự giới
    hạn đúng phạm vi nd được xem — GIỐNG HỆT logic phân quyền ở
    views/de_xuat.py (_de_xuat_giam_sat/_duoc_xem) — không tin bất kỳ tham
    số phạm vi nào vì tool chỉ nhận trang_thai/loai để LỌC, không nhận
    tham số phạm vi nên AI không thể tự ý mở rộng ra ngoài quyền của nd.
    **_bo_qua hứng mọi tham số lạ nếu OpenAI lỡ tự bịa thêm, tránh lỗi."""
    from models import DeXuat, LoaiDeXuat, TrangThaiDeXuat

    if nd.la_admin_sep:
        q = DeXuat.query
    elif nd.la_quan_ly and nd.bo_phan_id:
        q = DeXuat.query.join(NguoiDung, DeXuat.nguoi_de_xuat_id == NguoiDung.id).filter(
            db.or_(NguoiDung.bo_phan_id == nd.bo_phan_id, DeXuat.nguoi_de_xuat_id == nd.id)
        )
    else:
        q = DeXuat.query.filter_by(nguoi_de_xuat_id=nd.id)

    if trang_thai in (TrangThaiDeXuat.CHO_DUYET, TrangThaiDeXuat.DA_DUYET, TrangThaiDeXuat.TU_CHOI):
        q = q.filter(DeXuat.trang_thai == trang_thai)
    if loai in (LoaiDeXuat.TAM_UNG, LoaiDeXuat.CONG_VIEC):
        q = q.filter(DeXuat.loai == loai)

    ds = q.order_by(DeXuat.tao_luc.desc()).limit(30).all()
    return {
        "tong_so_tra_ve": len(ds),
        "luu_y": "Chỉ trả tối đa 30 kết quả gần nhất — còn nhiều hơn nếu tong_so_tra_ve == 30.",
        "danh_sach": [
            {
                "id": dx.id,
                "loai": dx.ten_loai,
                "nguoi_de_xuat": dx.nguoi_de_xuat.ho_ten if dx.nguoi_de_xuat else None,
                "noi_dung": dx.noi_dung[:200],
                "chi_phi_du_kien": float(dx.chi_phi_du_kien) if dx.chi_phi_du_kien is not None else None,
                "trang_thai": dx.ten_trang_thai,
                "nguoi_duyet": dx.nguoi_duyet.ho_ten if dx.nguoi_duyet else None,
                "y_kien_duyet": dx.y_kien_duyet,
                "gui_luc": dx.tao_luc.strftime("%d/%m/%Y %H:%M") if dx.tao_luc else None,
                "duyet_luc": dx.duyet_luc.strftime("%d/%m/%Y %H:%M") if dx.duyet_luc else None,
            }
            for dx in ds
        ],
    }


_CONG_CU_TRO_LY.append({
    "type": "function",
    "function": {
        "name": "tra_cuu_dong_goi",
        "description": (
            "Tra cứu lịch sử ĐÓNG GÓI đơn hàng (mã vận đơn, ai gói, lúc nào, ảnh "
            "kiện hàng) trong ĐÚNG phạm vi người đang hỏi được xem — tự giới hạn "
            "theo quyền (nhân viên thường chỉ thấy đơn chính mình gói; Quản lý/"
            "Sếp/Admin thấy toàn bộ). GỌI tool này cho MỌI câu hỏi về đóng gói/"
            "gói hàng/đơn hàng/mã vận đơn/ai gói đơn nào — dữ liệu này KHÔNG có "
            "sẵn trong system prompt. Kết quả có trường 'anh' là danh sách đường "
            "dẫn ảnh — muốn hiện ảnh thì copy y nguyên 1 đường dẫn vào trường media."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ma_van_don": {
                    "type": "string",
                    "description": "Mã vận đơn (hoặc 1 phần mã) cần tìm. Bỏ qua nếu không lọc theo mã.",
                },
                "ten_nguoi_goi": {
                    "type": "string",
                    "description": "Tên (hoặc 1 phần tên) người gói. Bỏ qua nếu không lọc theo người.",
                },
                "tu_ngay": {
                    "type": "string",
                    "description": "Lọc từ ngày, dạng YYYY-MM-DD (tính theo giờ Việt Nam). VD hỏi 'hôm nay' thì tu_ngay = den_ngay = ngày hôm nay.",
                },
                "den_ngay": {
                    "type": "string",
                    "description": "Lọc tới hết ngày này, dạng YYYY-MM-DD.",
                },
            },
        },
    },
})


def _tra_cuu_dong_goi_cho_ai(nd: NguoiDung, ma_van_don: str | None = None,
                             ten_nguoi_goi: str | None = None, tu_ngay: str | None = None,
                             den_ngay: str | None = None, **_bo_qua) -> dict:
    """Hàm THẬT được thực thi khi OpenAI gọi tool 'tra_cuu_dong_goi'. Phân
    quyền GIỐNG HỆT views/dong_goi.py danh_sach(): không phải quản lý thì
    chỉ thấy đơn chính mình gói — tool không nhận tham số phạm vi nên AI
    không thể tự mở rộng ra ngoài quyền của nd."""
    from datetime import date
    from models import GoiHang

    q = GoiHang.query.join(NguoiDung, GoiHang.nguoi_goi_id == NguoiDung.id)
    if not nd.la_quan_ly:
        q = q.filter(GoiHang.nguoi_goi_id == nd.id)
    if ma_van_don and str(ma_van_don).strip():
        q = q.filter(GoiHang.ma_van_don.ilike(f"%{str(ma_van_don).strip()}%"))
    if ten_nguoi_goi and str(ten_nguoi_goi).strip() and nd.la_quan_ly:
        q = q.filter(NguoiDung.ho_ten.ilike(f"%{str(ten_nguoi_goi).strip()}%"))

    def _doc_ngay(s):
        try:
            return date.fromisoformat(str(s).strip()[:10]) if s else None
        except ValueError:
            return None

    d_tu, d_den = _doc_ngay(tu_ngay), _doc_ngay(den_ngay)
    if d_tu:
        q = q.filter(GoiHang.tao_luc >= datetime.combine(d_tu, datetime.min.time()))
    if d_den:
        q = q.filter(GoiHang.tao_luc <= datetime.combine(d_den, datetime.max.time()))

    tong = q.count()
    ds = q.order_by(GoiHang.tao_luc.desc()).limit(30).all()
    return {
        "tong_so_khop": tong,
        "so_tra_ve": len(ds),
        "luu_y": ("Chỉ trả tối đa 30 lần gói mới nhất. Cùng 1 mã có thể gói nhiều "
                  "lần (gói lại/gói bù) — bản MỚI NHẤT là lần gửi đi thực tế."),
        "pham_vi": "toàn công ty" if nd.la_quan_ly else "chỉ đơn do chính người đang hỏi gói",
        "danh_sach": [
            {
                "ma_van_don": gh.ma_van_don,
                "nguoi_goi": gh.nguoi_goi.ho_ten if gh.nguoi_goi else None,
                "luc": gh.tao_luc.strftime("%H:%M %d/%m/%Y") if gh.tao_luc else None,
                "so_anh": len(gh.anh),
                "anh": [a.duong_dan for a in gh.anh[:3]],
            }
            for gh in ds
        ],
    }


_CAC_HAM_CONG_CU = {
    "tra_cuu_de_xuat": _tra_cuu_de_xuat_cho_ai,
    "tra_cuu_dong_goi": _tra_cuu_dong_goi_cho_ai,
}
_SO_VONG_GOI_TOOL_TOI_DA = 3  # chặn lặp vô hạn nếu model cứ đòi gọi tool mãi


def tro_ly_tra_loi(nd: NguoiDung, tin_nhan: str, lich_su: list[dict]) -> tuple[dict | None, str | None]:
    """Trợ lý AI hỏi-đáp — trả lời dựa trên dữ liệu thật của đúng người
    đang hỏi (lấy theo nd, không lấy theo dữ liệu client gửi lên) + hướng
    dẫn sử dụng hệ thống. lich_su là vài lượt hỏi-đáp gần nhất do trình
    duyệt gửi lên để giữ mạch hội thoại, chỉ dùng tối đa 8 lượt gần nhất.

    Ngoài dữ liệu đã nhét sẵn vào ngữ cảnh (_boi_canh_tro_ly), còn cho phép
    OpenAI TỰ GỌI THÊM các "công cụ" (_CONG_CU_TRO_LY, function calling) khi
    câu hỏi cần dữ liệu ngoài những gì đã nhét sẵn — VD hỏi đề xuất ĐÃ DUYỆT
    trong khi ngữ cảnh chỉ có sẵn số lượng đang CHỜ duyệt. Không cần đoán
    trước mọi câu hỏi có thể gặp để nhét cứng dữ liệu như trước.

    Trả về (dict {tra_loi, duong_dan, nhan_nut, media}, lỗi) — các trường
    phụ có thể None nếu câu hỏi không cần gợi ý đi đâu / không có media.
    """
    # Vài lượt hỏi-đáp gần nhất — dùng để suy ra ĐANG NÓI TỚI đối tượng nào
    # (chức vụ/sản phẩm) khi câu hỏi hiện tại quá ngắn, không lặp lại tên
    # (VD "chi tiết hơn", hoặc chỉ gõ "ảnh" sau khi đã nhắc tên sản phẩm ở
    # lượt trước) — cần tính TRƯỚC bước khớp cứng ảnh bên dưới để 2 hàm đó
    # cũng suy luận được, không chỉ dùng cho việc nạp ngữ cảnh AI.
    ngu_canh_gan_day = " ".join(
        str(m.get("noi_dung", "")) for m in lich_su[-4:] if isinstance(m, dict))

    # Ngữ cảnh cho 2 bộ khớp ảnh: CHỈ câu hỏi trước của người dùng (bỏ câu
    # trả lời AI + câu hiện tại — trình duyệt đã đẩy câu hiện tại vào cuối
    # lich_su trước khi gửi). Câu trả lời AI hay nhắc tên chức vụ/nhãn ảnh
    # nên từng kéo nhầm sang ảnh không liên quan.
    cau_nguoi_dung = [str(m.get("noi_dung", "")) for m in lich_su
                      if isinstance(m, dict) and m.get("vai_tro") == "user"]
    if cau_nguoi_dung and cau_nguoi_dung[-1].strip() == tin_nhan.strip():
        cau_nguoi_dung = cau_nguoi_dung[:-1]
    cau_truoc = " ".join(cau_nguoi_dung[-1:])

    ket_qua_anh = _thu_khop_anh_chuc_vu(nd, tin_nhan, cau_truoc)
    if ket_qua_anh:
        ghi_nhan_su_dung_tro_ly(nd, 0)  # khớp trực tiếp, không gọi OpenAI
        return ket_qua_anh, None

    ket_qua_anh_sp = _thu_khop_anh_san_pham(tin_nhan, cau_truoc)
    if ket_qua_anh_sp:
        ghi_nhan_su_dung_tro_ly(nd, 0)  # khớp trực tiếp, không gọi OpenAI
        return ket_qua_anh_sp, None

    van_ban_gan_day = tin_nhan.replace("\n", " ") + "\n" + ngu_canh_gan_day
    boi_canh = _boi_canh_tro_ly(nd, van_ban_gan_day)
    messages = [{"role": "system",
                "content": _HUONG_DAN_HE_THONG_TRO_LY + "\n\nDữ liệu hiện tại:\n" + boi_canh}]
    for m in lich_su[-8:]:
        if isinstance(m, dict) and m.get("vai_tro") in ("user", "assistant") and m.get("noi_dung"):
            messages.append({"role": m["vai_tro"], "content": str(m["noi_dung"])[:2000]})
    messages.append({"role": "user", "content": tin_nhan[:2000]})

    model = (current_app.config.get("OPENAI_MODEL_TRO_LY")
            or current_app.config.get("OPENAI_MODEL", "gpt-4o-mini"))

    # Vòng lặp function-calling: OpenAI có thể trả về tool_calls thay vì
    # câu trả lời cuối nếu thấy câu hỏi cần dữ liệu ngoài ngữ cảnh đã nhét
    # sẵn — mình thực thi đúng hàm đó, đưa kết quả THẬT trở lại cho nó viết
    # tiếp, lặp tối đa _SO_VONG_GOI_TOOL_TOI_DA lần để tránh treo/tốn tiền
    # vô hạn nếu model cứ đòi tra cứu mãi không chịu trả lời.
    tong_token = 0
    msg = None
    for _vong in range(_SO_VONG_GOI_TOOL_TOI_DA):
        msg, loi, so_token = _goi_chatgpt_voi_cong_cu(
            messages, _CONG_CU_TRO_LY, dang_json=True, model=model)
        tong_token += so_token
        if loi:
            # Gọi lỗi (kể cả rate-limit) không tính là 1 câu hỏi thành công
            # — không trừ vào hạn mức của người dùng.
            return None, loi

        cac_goi_cong_cu = msg.get("tool_calls")
        if not cac_goi_cong_cu:
            break  # có câu trả lời cuối rồi, không cần gọi tool nữa

        messages.append(msg)  # đúng message thô OpenAI trả về, echo lại nguyên văn
        for tc in cac_goi_cong_cu:
            ten_ham = (tc.get("function") or {}).get("name")
            try:
                tham_so = json.loads((tc.get("function") or {}).get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                tham_so = {}
            ham = _CAC_HAM_CONG_CU.get(ten_ham)
            ket_qua_ham = ham(nd, **tham_so) if ham else {"loi": f"Không có công cụ '{ten_ham}'."}
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": json.dumps(ket_qua_ham, ensure_ascii=False),
            })
    else:
        return None, "Trợ lý cần tra cứu quá nhiều lần cho câu hỏi này, bạn hỏi cụ thể/ngắn gọn hơn nhé."

    ghi_nhan_su_dung_tro_ly(nd, tong_token)

    noi_dung = msg.get("content")
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
        "media": _ds_media_da_xac_minh(nd, ket_qua.get("media")),
    }, None


def _ds_media_da_xac_minh(nd: NguoiDung, media) -> list[str] | None:
    """AI có thể trả media là 1 chuỗi hoặc 1 mảng nhiều đường dẫn (VD sản
    phẩm có nhiều ảnh) — xác minh TỪNG đường dẫn, bỏ trùng, giữ tối đa
    _SO_ANH_TOI_DA. Không còn đường dẫn hợp lệ nào thì trả None."""
    if not media:
        return None
    ds = media if isinstance(media, list) else [media]
    ket_qua = []
    for d in ds:
        if not isinstance(d, str):
            continue
        url = _duong_dan_media_da_xac_minh(nd, d.strip())
        if url and url not in ket_qua:
            ket_qua.append(url)
        if len(ket_qua) >= _SO_ANH_TOI_DA:
            break
    return ket_qua or None


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