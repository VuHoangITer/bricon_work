import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


def _time(value: str, default: str):
    raw = (value or default).strip()
    hh, mm = raw.split(":")
    return int(hh), int(mm)


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "doi-secret-key-di")

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql://bricon:bricon@localhost:5432/bricon_work"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    BASE_URL = os.getenv("BASE_URL", "http://localhost:5000").rstrip("/")

    ZALO_API_BASE = os.getenv("ZALO_API_BASE", "https://bot-api.zapps.me/bot").rstrip("/")
    ZALO_DEFAULT_BOT_TOKEN = os.getenv("ZALO_DEFAULT_BOT_TOKEN", "")
    ZALO_BOT_TOKEN_QL = os.getenv("ZALO_BOT_TOKEN_QL", "")
    ZALO_GROUP_QL = os.getenv("ZALO_GROUP_QL", "")

    API_KEY = os.getenv("API_KEY", "")

    UPLOAD_ROOT = os.getenv("UPLOAD_ROOT", "/tmp/bricon-uploads")
    MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "60"))
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

    GIO_VAO = _time(os.getenv("GIO_VAO"), "08:05")
    GIO_RA = _time(os.getenv("GIO_RA"), "17:30")
    PHUT_TRE_CHO_PHEP = int(os.getenv("PHUT_TRE_CHO_PHEP", "10"))
    # Mức trễ cho phép riêng khi 1 buổi đã nghỉ phép, buổi còn lại chấm công
    # bình thường — cho phép trễ ít hơn ngày thường (mặc định 5 phút).
    PHUT_TRE_CHO_PHEP_NUA_NGAY = int(os.getenv("PHUT_TRE_CHO_PHEP_NUA_NGAY", "5"))
    # Ranh giới sáng/chiều — dùng khi 1 buổi được nghỉ phép, để so trễ/sớm
    # với đúng nửa ngày còn lại thay vì bỏ qua hẳn hoặc so với cả ngày.
    GIO_KET_THUC_SANG = _time(os.getenv("GIO_KET_THUC_SANG"), "11:30")
    GIO_BAT_DAU_CHIEU = _time(os.getenv("GIO_BAT_DAU_CHIEU"), "13:00")
    GPS_ACCURACY_MAX = float(os.getenv("GPS_ACCURACY_MAX", "100"))

    TASK_CODE_OFFSET = int(os.getenv("TASK_CODE_OFFSET", "1000"))

    REMEMBER_COOKIE_DURATION = timedelta(days=90)
    PERMANENT_SESSION_LIFETIME = timedelta(days=90)
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = BASE_URL.startswith("https")
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    TIMEZONE = "Asia/Ho_Chi_Minh"