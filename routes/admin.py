"""صفحات الإدارة: المستخدمون وحالة النموذج ولوحة Cloudflare للقراءة فقط."""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

import database as db
import security
import train
from config import Config

bp = Blueprint("admin", __name__, url_prefix="/admin")
logger = logging.getLogger("voxshield")

USER_MESSAGES = {
    "enabled": "تم تفعيل الحساب.",
    "disabled": "تم تعطيل الحساب وإنهاء جلساته.",
    "self": "لا يمكن تعطيل حسابك الحالي.",
}
MODEL_MESSAGES = {
    "started": "بدأت إعادة تدريب النموذج في الخلفية، وستُحدَّث الصفحة تلقائياً عند الانتهاء.",
    "running": "يوجد تدريب قيد التنفيذ حالياً.",
}
CLOUDFLARE_API = "https://api.cloudflare.com/client/v4/zones/{zone}{path}"
_cf_cache = {"time": 0.0, "data": None}
_cf_lock = threading.Lock()


@bp.get("/users")
@security.roles_required("admin")
def users():
    rows = db.query_all("SELECT id, username, display_name, role, is_active, created_at FROM users ORDER BY id")
    message = USER_MESSAGES.get(request.args.get("msg", ""))
    return render_template("admin_users.html", rows=rows, message=message)


@bp.post("/users/<int:user_id>/toggle")
@security.roles_required("admin")
def toggle_user(user_id):
    if user_id == g.user["id"]:
        return redirect(url_for("admin.users", msg="self"))
    user = db.query_one("SELECT id, is_active FROM users WHERE id = ?", (user_id,))
    if user is None:
        abort(404)
    new_state = 0 if user["is_active"] else 1
    db.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_state, user_id))
    if not new_state:
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    logger.info("تغيير حالة المستخدم %s بواسطة المدير %s", user_id, g.user["id"])
    return redirect(url_for("admin.users", msg="enabled" if new_state else "disabled"))


def _cf_request(path):
    url = CLOUDFLARE_API.format(zone=Config.CLOUDFLARE_ZONE_ID, path=path)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + Config.CLOUDFLARE_API_TOKEN})
    with urllib.request.urlopen(req, timeout=6) as response:
        return json.loads(response.read().decode("utf-8")).get("result")


def _cf_setting(name):
    try:
        result = _cf_request("/settings/" + name) or {}
        return result.get("value")
    except (urllib.error.URLError, ValueError, OSError):
        return None


def cloudflare_status():
    """قراءة حالة Cloudflare فقط دون أي تعديل، مع تخزين مؤقت لمدة دقيقة."""
    if not Config.cloudflare_enabled():
        return None
    with _cf_lock:
        if _cf_cache["data"] and time.time() - _cf_cache["time"] < 60:
            return _cf_cache["data"]
    data = {"ok": False}
    try:
        zone = _cf_request("") or {}
        records = _cf_request("/dns_records?per_page=100") or []
        proxiable = [r for r in records if r.get("type") in ("A", "AAAA", "CNAME")]
        bot = {}
        try:
            bot = _cf_request("/bot_management") or {}
        except (urllib.error.URLError, ValueError, OSError):
            bot = {}
        data = {
            "ok": True,
            "zone": zone.get("name", "-"),
            "zone_status": zone.get("status", "-"),
            "modified_on": zone.get("modified_on"),
            "proxied": sum(1 for r in proxiable if r.get("proxied")),
            "proxiable": len(proxiable),
            "ssl": _cf_setting("ssl"),
            "always_https": _cf_setting("always_use_https"),
            "min_tls": _cf_setting("min_tls_version"),
            "browser_check": _cf_setting("browser_check"),
            "security_level": _cf_setting("security_level"),
            "bot_fight_mode": bot.get("fight_mode"),
        }
    except (urllib.error.URLError, ValueError, OSError):
        logger.warning("تعذر جلب حالة Cloudflare")
    with _cf_lock:
        _cf_cache.update(time=time.time(), data=data)
    return data


@bp.get("/model")
@security.roles_required("admin")
def model():
    runs = db.query_all("SELECT * FROM model_runs ORDER BY id DESC LIMIT 10")
    latest = runs[0] if runs else None
    confusion = json.loads(latest["confusion_json"]) if latest else None
    return render_template(
        "admin_model.html", latest=latest, runs=runs, confusion=confusion,
        model_exists=os.path.exists(Config.MODEL_PATH), training=dict(train.TRAINING_STATE),
        message=MODEL_MESSAGES.get(request.args.get("msg", "")), cloudflare=cloudflare_status(),
    )


@bp.post("/model/retrain")
@security.roles_required("admin")
def retrain():
    started = train.start_background_training()
    logger.info("طلب إعادة تدريب النموذج بواسطة المدير %s", g.user["id"])
    return redirect(url_for("admin.model", msg="started" if started else "running"))