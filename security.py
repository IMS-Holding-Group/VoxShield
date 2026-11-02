"""الجلسات، CSRF، التحقق من المدخلات، تحديد المحاولات، وترويسات الأمان."""
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlsplit

import bcrypt
from flask import abort, g, jsonify, redirect, request, url_for

import database as db
from config import Config

logger = logging.getLogger("voxshield")

SESSION_COOKIE = "vs_session"
LOGIN_CSRF_COOKIE = "vs_login_csrf"
MAX_FAILED_LOGINS = 5
LOGIN_WINDOW = timedelta(minutes=10)
LOGIN_BLOCK = timedelta(minutes=10)
BLOCKED_SUFFIXES = (".db", ".env", ".bak", ".log", ".joblib", ".sqlite", ".sqlite3")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
CSP = (
    "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
    "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
)

_dummy_hash = None


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def client_ip():
    return (request.remote_addr or "0.0.0.0")[:64]


def verify_password(password, password_hash):
    global _dummy_hash
    try:
        if not password_hash:
            if _dummy_hash is None:
                _dummy_hash = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt(rounds=12))
            bcrypt.checkpw(password.encode("utf-8"), _dummy_hash)
            return False
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def is_login_blocked(ip, username):
    """5 محاولات فاشلة متتالية خلال 10 دقائق تؤدي إلى حظر مؤقت لمدة 10 دقائق."""
    since = db.utc_iso(db.utc_now() - LOGIN_WINDOW - LOGIN_BLOCK)
    rows = db.query_all(
        "SELECT success, created_at FROM login_attempts WHERE ip = ? AND username = ? AND created_at >= ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (ip, username, since, MAX_FAILED_LOGINS),
    )
    if len(rows) < MAX_FAILED_LOGINS or any(r["success"] for r in rows):
        return False
    latest = datetime.fromisoformat(rows[0]["created_at"])
    oldest = datetime.fromisoformat(rows[-1]["created_at"])
    return latest - oldest <= LOGIN_WINDOW and db.utc_now() < latest + LOGIN_BLOCK


def record_login_attempt(ip, username, success):
    db.execute(
        "INSERT INTO login_attempts (ip, username, success, created_at) VALUES (?, ?, ?, ?)",
        (ip, username, 1 if success else 0, db.utc_iso()),
    )


def create_session(user_id):
    token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    now = db.utc_now()
    expires = now + timedelta(minutes=Config.SESSION_IDLE_MINUTES)
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (db.utc_iso(now),))
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, last_seen_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (hash_token(token), user_id, csrf_token, db.utc_iso(now), db.utc_iso(now), db.utc_iso(expires)),
    )
    return token


def destroy_session(token):
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))


def set_session_cookie(response, token):
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=Config.FORCE_HTTPS, samesite="Lax", path="/"
    )


def clear_session_cookie(response):
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=Config.FORCE_HTTPS, samesite="Lax")


def load_current_user():
    g.user = None
    g.csrf_token = None
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 200:
        return
    token_hash = hash_token(token)
    row = db.query_one(
        "SELECT s.csrf_token, s.expires_at, u.id, u.username, u.display_name, u.role, u.is_active "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (token_hash,),
    )
    if row is None:
        g.clear_cookie = True
        return
    now = db.utc_now()
    if row["expires_at"] <= db.utc_iso(now) or not row["is_active"]:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        g.clear_cookie = True
        return
    expires = now + timedelta(minutes=Config.SESSION_IDLE_MINUTES)
    db.execute(
        "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
        (db.utc_iso(now), db.utc_iso(expires), token_hash),
    )
    g.user = {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
    }
    g.csrf_token = row["csrf_token"]


def is_api_request():
    return request.path.startswith("/api/")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            if is_api_request():
                return jsonify(error="انتهت الجلسة، يرجى تسجيل الدخول من جديد"), 401
            target = request.full_path if request.query_string else request.path
            return redirect(url_for("auth.login", next=target))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] not in roles:
                if is_api_request():
                    return jsonify(error="لا تملك صلاحية تنفيذ هذا الإجراء"), 403
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def verify_csrf():
    """التحقق من رمز CSRF لكل طلب يغير البيانات بعد تسجيل الدخول."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if request.endpoint == "auth.login" or g.get("user") is None:
        return
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied and not is_api_request():
        supplied = request.form.get("csrf_token", "")
    if not supplied or not g.csrf_token or not hmac.compare_digest(supplied, g.csrf_token):
        logger.warning("رفض طلب برمز CSRF غير صالح على المسار %s", request.path)
        abort(403)


def new_login_csrf():
    return secrets.token_urlsafe(32)


def check_login_csrf():
    cookie_value = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    form_value = request.form.get("csrf_token", "")
    return bool(cookie_value) and bool(form_value) and hmac.compare_digest(cookie_value, form_value)


def safe_next(value, default="/dashboard"):
    """قبول مسار نسبي داخلي يبدأ بشرطة مائلة واحدة فقط لمنع Open Redirect."""
    if not value or not isinstance(value, str) or len(value) > 512:
        return default
    if not value.startswith("/") or value.startswith("//") or "\\" in value or CONTROL_CHARS.search(value):
        return default
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return default
    return value


def clean_text(value, max_len=100):
    if not isinstance(value, str):
        return ""
    return CONTROL_CHARS.sub("", value).strip()[:max_len]


def clean_filename(value, default="تسجيل مباشر.wav"):
    name = clean_text(value or "", 300)
    name = os.path.basename(name.replace("\\", "/"))
    name = name.replace("..", ".").strip(" .")
    return name[:150] or default


def parse_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def is_blocked_path(path):
    lowered = path.lower()
    segments = [s for s in lowered.split("/") if s]
    if any(s.startswith(".") for s in segments) or ".." in segments:
        return True
    return lowered.rstrip("/").endswith(BLOCKED_SUFFIXES)


def enforce_https():
    if not Config.FORCE_HTTPS or request.is_secure or request.path == "/health":
        return None
    return redirect(request.url.replace("http://", "https://", 1), code=301)


class SlidingWindowLimiter:
    """محدد معدل في الذاكرة بنافذة زمنية منزلقة لكل مفتاح."""

    def __init__(self, limit, window_seconds):
        self.limit = limit
        self.window = window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key):
        now = time.monotonic()
        with self._lock:
            queue = self._hits[key]
            while queue and now - queue[0] > self.window:
                queue.popleft()
            if len(queue) >= self.limit:
                return False
            queue.append(now)
            return True


analysis_limiter = SlidingWindowLimiter(30, 600)
simulation_limiter = SlidingWindowLimiter(60, 600)


def apply_security_headers(response):
    headers = response.headers
    headers["Content-Security-Policy"] = CSP
    headers["X-Frame-Options"] = "DENY"
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Referrer-Policy"] = "same-origin"
    headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
    headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if Config.FORCE_HTTPS:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if not request.path.startswith("/assets/"):
        headers["Cache-Control"] = "no-store"
    headers.pop("X-Powered-By", None)
    headers.pop("Server", None)
    origin = request.headers.get("Origin")
    if origin and is_api_request() and origin in Config.CORS_ALLOWED_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
        headers["Access-Control-Allow-Methods"] = "GET, POST"
        headers["Vary"] = "Origin"
    if g.get("clear_cookie"):
        clear_session_cookie(response)
    return response