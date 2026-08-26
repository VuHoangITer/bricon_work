from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

import services

bp = Blueprint("tro_ly", __name__, url_prefix="/tro-ly")


@bp.route("/hoi", methods=["POST"])
@login_required
def hoi():
    du_lieu = request.get_json(silent=True) or {}
    tin_nhan = (du_lieu.get("tin_nhan") or "").strip()
    lich_su = du_lieu.get("lich_su")
    if not tin_nhan:
        return jsonify({"ok": False, "loi": "Chưa nhập câu hỏi."})
    if not isinstance(lich_su, list):
        lich_su = []

    ket_qua, loi = services.tro_ly_tra_loi(current_user, tin_nhan, lich_su)
    if loi:
        return jsonify({"ok": False, "loi": loi})
    return jsonify({"ok": True, **ket_qua})