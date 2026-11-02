"""تسجيل الدخول والخروج."""
import logging

from flask import Blueprint, g, make_response, redirect, render_template, request, url_for

import database as db
import security

bp = Blueprint("auth", __name__)
logger = logging.getLogger("voxshield")

GENERIC_ERROR = "اسم المستخدم أو كلمة السر غير صحيحة."
BLOCKED_ERROR = "تم إيقاف محاولات الدخول مؤقتاً بسبب تكرار المحاولات الفاشلة، يرجى المحاولة بعد 10 دقائق."
EXPIRED_FORM = "انتهت صلاحية نموذج الدخول، يرجى المحاولة مرة أخرى."


def _render_login(error=None, username="", status=200):
    token = security.new_login_csrf()
    next_value = security.safe_next(request.values.get("next"), "")
    response = make_response(
        render_template("login.html", error=error, username=username, login_csrf=token, next_value=next_value), status
    )
    response.set_cookie(
        security.LOGIN_CSRF_COOKIE, token, httponly=True, secure=security.Config.FORCE_HTTPS,
        samesite="Strict", path="/login", max_age=1800,
    )
    return response


@bp.route("/login", methods=["GET", "POST"])
def login():
    try:
        if request.method == "GET":
            if g.get("user"):
                return redirect(url_for("pages.dashboard"))
            return _render_login()
        username = security.clean_text(request.form.get("username", ""), 64).lower()
        password = request.form.get("password", "")
        if not security.check_login_csrf():
            return _render_login(EXPIRED_FORM, username, 400)
        if not username or not password or len(password) > 128:
            return _render_login(GENERIC_ERROR, username, 400)
        ip = security.client_ip()
        if security.is_login_blocked(ip, username):
            return _render_login(BLOCKED_ERROR, username, 429)
        user = db.query_one(
            "SELECT id, password_hash, is_active FROM users WHERE username = ?", (username,)
        )
        valid = security.verify_password(password, user["password_hash"] if user else None)
        if not user or not valid or not user["is_active"]:
            security.record_login_attempt(ip, username, False)
            logger.info("محاولة دخول فاشلة من العنوان %s", ip)
            return _render_login(GENERIC_ERROR, username, 401)
        security.record_login_attempt(ip, username, True)
        old_token = request.cookies.get(security.SESSION_COOKIE)
        if old_token:
            security.destroy_session(old_token)
        token = security.create_session(user["id"])
        g.clear_cookie = False
        response = redirect(security.safe_next(request.form.get("next")))
        security.set_session_cookie(response, token)
        response.delete_cookie(security.LOGIN_CSRF_COOKIE, path="/login")
        return response
    except Exception:
        logger.exception("خطأ أثناء تسجيل الدخول")
        return _render_login("حدث خطأ غير متوقع، يرجى المحاولة لاحقاً.", status=500)


@bp.post("/logout")
@security.login_required
def logout():
    security.destroy_session(request.cookies.get(security.SESSION_COOKIE))
    response = redirect(url_for("auth.login"))
    security.clear_session_cookie(response)
    return response