"""تسجيل المسارات وأدوات القوالب المشتركة (تنسيق التاريخ، الأيقونات، التسميات)."""
from datetime import datetime, timedelta, timezone

from flask import g
from markupsafe import Markup, escape

RIYADH_OFFSET = timedelta(hours=3)

LABELS = {
    "verdict": {"genuine": "أصلي", "fake": "مزيف"},
    "risk": {"low": "منخفضة", "medium": "متوسطة", "high": "مرتفعة"},
    "attack": {"replay": "إعادة تشغيل", "synthetic": "توليد بالذكاء الاصطناعي", None: "لا يوجد", "": "لا يوجد"},
    "role": {"admin": "مدير النظام", "analyst": "محلل أمني", "trainee": "متدرب"},
    "alert_status": {"new": "جديد", "reviewed": "تمت المراجعة"},
    "class": {"live": "صوت حي", "replay": "إعادة تشغيل", "synthetic": "مولد بالذكاء الاصطناعي"},
}

ICON_PATHS = {
    "dashboard": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "wave": '<path d="M3 12h2M7 8v8M11 4v16M15 7v10M19 10v4M21 12h0"/>',
    "history": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "bell": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10 21a2 2 0 0 0 4 0"/>',
    "play": '<circle cx="12" cy="12" r="9"/><path d="M10 8l6 4-6 4z"/>',
    "users": '<circle cx="9" cy="8" r="4"/><path d="M2 21c0-4 3-7 7-7s7 3 7 7"/><path d="M17 4a4 4 0 0 1 0 8M22 21c0-3-2-6-5-7"/>',
    "cpu": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    "logout": '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4"/><path d="M10 17l-5-5 5-5M5 12h11"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "print": '<path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="1"/><path d="M6 14h12v7H6z"/>',
    "check": '<path d="M4 12l5 5L20 6"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "alert": '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h0"/>',
    "next": '<path d="M15 6l-6 6 6 6"/>',
    "prev": '<path d="M9 6l6 6-6 6"/>',
    "file": '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2 6"/><path d="M20 4v7h-7"/>',
    "spinner": '<path d="M12 3a9 9 0 1 0 9 9"/>',
    "dot": '<circle cx="12" cy="12" r="4"/>',
    "cloud": '<path d="M7 18h10a4 4 0 0 0 0-8 6 6 0 0 0-11.5 1.5A3.5 3.5 0 0 0 7 18z"/>',
}


def icon(name, size=20, extra_class=""):
    paths = ICON_PATHS.get(name, ICON_PATHS["dot"])
    return Markup(
        '<svg class="icon {cls}" width="{s}" height="{s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{p}</svg>'
    ).format(cls=extra_class, s=int(size), p=Markup(paths))


def format_datetime_parts(value):
    """تحويل قيمة UTC بصيغة ISO إلى توقيت الرياض يدوياً وإرجاع الأجزاء."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(timezone.utc) + RIYADH_OFFSET
    hour = local.hour % 12 or 12
    return {
        "date": "{}/{}/{}".format(local.year, local.month, local.day),
        "time": "{}:{:02d}".format(hour, local.minute),
        "suffix": "ص" if local.hour < 12 else "م",
    }


def format_datetime_html(value):
    parts = format_datetime_parts(value)
    if parts is None:
        return Markup('<span class="dt">-</span>')
    return Markup(
        '<span class="dt"><span>{date}</span><span>م</span> <span>{time}</span><span>{suffix}</span></span>'
    ).format(date=escape(parts["date"]), time=escape(parts["time"]), suffix=escape(parts["suffix"]))


def percent(value, digits=1):
    try:
        return "{:.{d}f}%".format(float(value) * 100.0, d=digits)
    except (TypeError, ValueError):
        return "-"


def register_template_helpers(app):
    app.jinja_env.filters["dt"] = format_datetime_html
    app.jinja_env.filters["pct"] = percent

    @app.context_processor
    def inject_globals():
        return {
            "current_user": g.get("user"),
            "csrf_token": g.get("csrf_token") or "",
            "icon": icon,
            "LABELS": LABELS,
        }


def register_blueprints(app):
    from .admin import bp as admin_bp
    from .analysis import bp as analysis_bp
    from .auth import bp as auth_bp
    from .pages import bp as pages_bp
    from .simulation import bp as simulation_bp

    for blueprint in (auth_bp, pages_bp, analysis_bp, simulation_bp, admin_bp):
        app.register_blueprint(blueprint)