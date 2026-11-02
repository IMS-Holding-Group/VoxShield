"""نقطة تشغيل منصة VoxShield وتسجيل المسارات."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from flask import Flask, abort, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.serving import WSGIRequestHandler

import database as db
import security
from config import Config
from routes import register_blueprints, register_template_helpers

logger = logging.getLogger("voxshield")

ERROR_MESSAGES = {
    400: "تعذر تنفيذ الطلب لأن البيانات المرسلة غير صالحة.",
    401: "يرجى تسجيل الدخول للمتابعة.",
    403: "لا تملك صلاحية الوصول إلى هذه الصفحة أو تنفيذ هذا الإجراء.",
    404: "الصفحة المطلوبة غير موجودة.",
    405: "طريقة الطلب غير مسموحة لهذا المسار.",
    413: "حجم الملف يتجاوز الحد المسموح ({} ميجابايت).".format(Config.MAX_UPLOAD_MB),
    429: "تم تجاوز عدد الطلبات المسموح، يرجى المحاولة لاحقاً.",
    500: "حدث خطأ غير متوقع أثناء معالجة الطلب، يرجى المحاولة لاحقاً.",
}


def setup_logging():
    os.makedirs(Config.LOG_DIR, exist_ok=True)
    handler = RotatingFileHandler(Config.LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(handler)


def error_response(code):
    message = ERROR_MESSAGES.get(code, ERROR_MESSAGES[500])
    if request.path.startswith("/api/"):
        return jsonify(error=message), code
    try:
        return render_template("error.html", code=code, message=message), code
    except Exception:
        logger.exception("تعذر عرض قالب صفحة الخطأ")
        return message, code, {"Content-Type": "text/plain; charset=utf-8"}


def create_app():
    app = Flask(__name__, static_folder="assets", static_url_path="/assets", template_folder="templates")
    app.config.update(
        SECRET_KEY=Config.SECRET_KEY,
        MAX_CONTENT_LENGTH=Config.MAX_UPLOAD_MB * 1024 * 1024 + 256 * 1024,
        DEBUG=False,
        PROPAGATE_EXCEPTIONS=False,
    )
    app.json.ensure_ascii = False
    if Config.FORCE_HTTPS:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    register_template_helpers(app)
    register_blueprints(app)

    @app.before_request
    def before_request():
        if security.is_blocked_path(request.path):
            abort(404)
        redirect_response = security.enforce_https()
        if redirect_response is not None:
            return redirect_response
        if request.path.startswith("/assets/"):
            return None
        security.load_current_user()
        security.verify_csrf()
        return None

    @app.after_request
    def after_request(response):
        return security.apply_security_headers(response)

    app.teardown_appcontext(db.close_db)

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        code = error.code or 500
        return error_response(code if code in ERROR_MESSAGES else 500)

    @app.errorhandler(Exception)
    def handle_unexpected_error(_error):
        logger.exception("خطأ غير متوقع على المسار %s", request.path)
        return error_response(500)

    return app


class NeutralRequestHandler(WSGIRequestHandler):
    """إخفاء اسم الخادم وإصداره من ترويسة Server."""

    def version_string(self):
        return "VoxShield"


def bootstrap():
    setup_logging()
    for folder in (Config.MODEL_DIR, Config.LOG_DIR, os.path.join(Config.DATASET_DIR, "live"),
                   os.path.join(Config.DATASET_DIR, "replay"), os.path.join(Config.DATASET_DIR, "synthetic")):
        os.makedirs(folder, exist_ok=True)
    db.init_db()
    if not os.path.exists(Config.MODEL_PATH):
        import train
        print("لم يُعثر على ملف النموذج، يجري الآن التدريب التلقائي...")
        summary = train.run_training(verbose=True)
        print("اكتمل التدريب بدقة {:.2f}%".format(summary["accuracy"] * 100))


app = create_app()

if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    bootstrap()
    print("VoxShield يعمل على العنوان http://127.0.0.1:{}".format(Config.APP_PORT))
    app.run(host="127.0.0.1", port=Config.APP_PORT, debug=False, threaded=True,
            use_reloader=False, request_handler=NeutralRequestHandler)