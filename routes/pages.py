"""صفحات العرض: لوحة المعلومات، الفحص، التقرير، السجل، التنبيهات، وفحص الصحة."""
import json
import logging
import math

from flask import Blueprint, Response, abort, g, jsonify, redirect, render_template, request, url_for

import database as db
import security
from config import Config

bp = Blueprint("pages", __name__)
logger = logging.getLogger("voxshield")

PAGE_SIZE = 15
DEMO_NOTE = ("النموذج الحالي مدرب على بيانات تجريبية مولدة للعرض، وتختلف الدقة الفعلية باختلاف "
             "مجموعة البيانات الحقيقية المستخدمة في التدريب")


def _privileged():
    return g.user["role"] in ("admin", "analyst")


def _scope_clause(alias="a"):
    if _privileged():
        return "1 = 1", ()
    return "{}.user_id = ?".format(alias), (g.user["id"],)


@bp.get("/")
def index():
    return redirect(url_for("pages.dashboard") if g.get("user") else url_for("auth.login"))


@bp.get("/favicon.ico")
def favicon():
    return redirect(url_for("static", filename="images/logo.png"), code=301)


@bp.get("/dashboard")
@security.login_required
def dashboard():
    clause, params = _scope_clause()
    stats = db.query_one(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(CASE WHEN verdict = 'genuine' THEN 1 ELSE 0 END), 0) AS genuine, "
        "COALESCE(SUM(CASE WHEN verdict = 'fake' THEN 1 ELSE 0 END), 0) AS fake "
        "FROM analyses a WHERE " + clause,
        params,
    )
    recent = db.query_all(
        "SELECT a.id, a.file_name, a.verdict, a.confidence, a.risk_level, a.created_at FROM analyses a "
        "WHERE " + clause + " ORDER BY a.id DESC LIMIT 5",
        params,
    )
    pending_alerts = 0
    latest_alerts = []
    if _privileged():
        pending_alerts = db.query_one("SELECT COUNT(*) AS c FROM alerts WHERE status = 'new'")["c"]
        latest_alerts = db.query_all(
            "SELECT al.id, al.level, al.status, al.created_at, a.id AS analysis_id, a.file_name, a.attack_type "
            "FROM alerts al JOIN analyses a ON a.id = al.analysis_id ORDER BY al.id DESC LIMIT 5"
        )
    total = stats["total"] or 0
    genuine_pct = round(stats["genuine"] / total * 100, 1) if total else 0
    fake_pct = round(stats["fake"] / total * 100, 1) if total else 0
    return render_template(
        "dashboard.html", stats=stats, recent=recent, pending_alerts=pending_alerts,
        latest_alerts=latest_alerts, genuine_pct=genuine_pct, fake_pct=fake_pct,
    )


@bp.get("/analyze")
@security.login_required
def analyze():
    return render_template("analyze.html", max_mb=Config.MAX_UPLOAD_MB, max_seconds=Config.MAX_AUDIO_SECONDS)


@bp.get("/report/<int:analysis_id>")
@security.login_required
def report(analysis_id):
    analysis = db.query_one(
        "SELECT a.*, u.display_name AS owner_name FROM analyses a JOIN users u ON u.id = a.user_id WHERE a.id = ?",
        (analysis_id,),
    )
    if analysis is None or (not _privileged() and analysis["user_id"] != g.user["id"]):
        abort(404)
    for key in ("probabilities", "factors", "fingerprint", "spectrogram", "envelope", "stage_timings"):
        analysis[key] = json.loads(analysis.pop(key + "_json") or "{}")
    demo_model = False
    version = analysis["model_version"] or ""
    if version.startswith("VS-RF-") and version[6:].isdigit():
        run = db.query_one("SELECT data_source FROM model_runs WHERE id = ?", (int(version[6:]),))
        demo_model = bool(run and run["data_source"] == "تجريبي")
    chart_data = {
        "spectrogram": analysis["spectrogram"],
        "envelope": analysis["envelope"],
        "probabilities": analysis["probabilities"],
    }
    return render_template(
        "report.html", a=analysis, chart_data=chart_data, demo_model=demo_model, demo_note=DEMO_NOTE,
        inconclusive=analysis["confidence"] < 0.6,
    )


@bp.get("/history")
@security.login_required
def history():
    query = security.clean_text(request.args.get("q", ""), 100)
    verdict = request.args.get("verdict", "")
    risk = request.args.get("risk", "")
    page = security.parse_int(request.args.get("page"), 1, 1, 100000)
    clause, params = _scope_clause()
    conditions, values = [clause], list(params)
    if query:
        conditions.append("a.file_name LIKE ? ESCAPE '\\'")
        values.append("%" + security.escape_like(query) + "%")
    if verdict in ("genuine", "fake"):
        conditions.append("a.verdict = ?")
        values.append(verdict)
    else:
        verdict = ""
    if risk in ("low", "medium", "high"):
        conditions.append("a.risk_level = ?")
        values.append(risk)
    else:
        risk = ""
    where = " AND ".join(conditions)
    total = db.query_one("SELECT COUNT(*) AS c FROM analyses a WHERE " + where, tuple(values))["c"]
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(page, pages)
    rows = db.query_all(
        "SELECT a.id, a.file_name, a.verdict, a.attack_type, a.confidence, a.risk_level, a.duration_sec, "
        "a.created_at, u.display_name AS owner_name FROM analyses a JOIN users u ON u.id = a.user_id WHERE "
        + where + " ORDER BY a.id DESC LIMIT ? OFFSET ?",
        tuple(values) + (PAGE_SIZE, (page - 1) * PAGE_SIZE),
    )
    filters = {"q": query, "verdict": verdict, "risk": risk}
    return render_template("history.html", rows=rows, total=total, page=page, pages=pages, filters=filters)


@bp.get("/alerts")
@security.roles_required("admin", "analyst")
def alerts():
    status = request.args.get("status", "")
    page = security.parse_int(request.args.get("page"), 1, 1, 100000)
    where, values = "1 = 1", []
    if status in ("new", "reviewed"):
        where, values = "al.status = ?", [status]
    else:
        status = ""
    total = db.query_one("SELECT COUNT(*) AS c FROM alerts al WHERE " + where, tuple(values))["c"]
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(page, pages)
    rows = db.query_all(
        "SELECT al.id, al.level, al.status, al.created_at, al.reviewed_at, r.display_name AS reviewer, "
        "a.id AS analysis_id, a.file_name, a.attack_type, a.confidence, u.display_name AS owner_name "
        "FROM alerts al JOIN analyses a ON a.id = al.analysis_id JOIN users u ON u.id = a.user_id "
        "LEFT JOIN users r ON r.id = al.reviewed_by WHERE " + where + " ORDER BY al.id DESC LIMIT ? OFFSET ?",
        tuple(values) + (PAGE_SIZE, (page - 1) * PAGE_SIZE),
    )
    return render_template("alerts.html", rows=rows, total=total, page=page, pages=pages, status=status)


@bp.get("/health")
def health():
    return jsonify(status="ok")


@bp.get("/robots.txt")
def robots():
    return Response("User-agent: *\nDisallow:\n", mimetype="text/plain")