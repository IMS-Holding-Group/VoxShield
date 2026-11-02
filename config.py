"""قراءة إعدادات VoxShield من ملف .env وتجميعها في كائن واحد."""
import os
import secrets

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _get_bool(name, default=False):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _get_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _resolve_path(value, default):
    path = (value or "").strip() or default
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    return os.path.normpath(path)


class Config:
    BASE_DIR = BASE_DIR
    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(64)
    APP_DEBUG = _get_bool("APP_DEBUG", False)
    APP_HOST = (os.getenv("APP_HOST") or "127.0.0.1").strip()
    APP_PORT = _get_int("PORT", _get_int("APP_PORT", 5000, 1, 65535), 1, 65535)
    FORCE_HTTPS = _get_bool("FORCE_HTTPS", False)
    DATABASE_PATH = _resolve_path(os.getenv("DATABASE_PATH"), "data/voxshield.db")
    SESSION_IDLE_MINUTES = _get_int("SESSION_IDLE_MINUTES", 30, 5, 1440)
    MAX_UPLOAD_MB = _get_int("MAX_UPLOAD_MB", 15, 1, 100)
    MAX_AUDIO_SECONDS = _get_int("MAX_AUDIO_SECONDS", 60, 5, 600)
    CORS_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    DUMMY_ACCOUNTS = [
        (os.getenv("DUMMY_ADMIN_USERNAME", ""), os.getenv("DUMMY_ADMIN_PASSWORD", ""), "admin", "مدير النظام"),
        (os.getenv("DUMMY_ANALYST_USERNAME", ""), os.getenv("DUMMY_ANALYST_PASSWORD", ""), "analyst", "المحلل الأمني"),
        (os.getenv("DUMMY_TRAINEE_USERNAME", ""), os.getenv("DUMMY_TRAINEE_PASSWORD", ""), "trainee", "المتدرب"),
    ]
    CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    CLOUDFLARE_ZONE_ID = os.getenv("CLOUDFLARE_ZONE_ID", "").strip()
    SEED_MODEL_PATH = os.path.join(BASE_DIR, "models", "voxshield_model.joblib")
    MODEL_PATH = _resolve_path(os.getenv("MODEL_PATH"), "models/voxshield_model.joblib")
    MODEL_DIR = os.path.dirname(MODEL_PATH)
    DATASET_DIR = os.path.join(BASE_DIR, "data", "dataset")
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    LOG_FILE = os.path.join(LOG_DIR, "app.log")
    ASSETS_DIR = os.path.join(BASE_DIR, "assets")

    @classmethod
    def cloudflare_enabled(cls):
        return bool(cls.CLOUDFLARE_API_TOKEN and cls.CLOUDFLARE_ZONE_ID)