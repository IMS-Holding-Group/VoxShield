"""نقطة نهاية الفحص ووسم التنبيهات كمُراجَعة."""
import hashlib
import json
import logging
import time

from flask import Blueprint, g, jsonify, request, url_for

import database as db
import security
from config import Config
from engine import AudioError, ModelUnavailable, run_analysis

bp = Blueprint("analysis", __name__)
logger = logging.getLogger("voxshield")


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@bp.post("/api/analyze")
@security.login_required
def api_analyze():
    started = time.perf_counter()
    if not security.analysis_limiter.hit(g.user["id"]):
        return jsonify(error="تجاوزت الحد المسموح (30 عملية فحص كل 10 دقائق)، يرجى المحاولة لاحقاً."), 429
    upload = request.files.get("audio")
    if upload is None:
        return jsonify(error="لم يتم إرفاق ملف صوتي."), 400
    limit = Config.MAX_UPLOAD_MB * 1024 * 1024
    data = upload.stream.read(limit + 1)
    if len(data) > limit:
        return jsonify(error="حجم الملف يتجاوز الحد المسموح ({} ميجابايت).".format(Config.MAX_UPLOAD_MB)), 413
    file_name = security.clean_filename(request.form.get("file_name") or upload.filename)
    try:
        result = run_analysis(data, Config.MAX_AUDIO_SECONDS, include_visuals=True, started=started)
    except AudioError as error:
        return jsonify(error=error.message), 400
    except ModelUnavailable:
        return jsonify(error="النموذج غير جاهز حالياً، يرجى المحاولة بعد اكتمال التدريب."), 503
    except Exception:
        logger.exception("فشل تحليل مقطع صوتي")
        return jsonify(error="تعذر تحليل المقطع بسبب خطأ داخلي، يرجى المحاولة لاحقاً."), 500
    file_hash = hashlib.sha256(data).hexdigest()
    del data
    try:
        report_started = time.perf_counter()
        now = db.utc_iso()
        analysis_id = db.execute(
            "INSERT INTO analyses (user_id, file_name, file_hash, duration_sec, sample_rate, verdict, attack_type, "
            "confidence, risk_level, probabilities_json, factors_json, fingerprint_json, spectrogram_json, "
            "envelope_json, stage_timings_json, processing_ms, model_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                g.user["id"], file_name, file_hash, result["duration_sec"], result["sample_rate"], result["verdict"],
                result["attack_type"], round(result["confidence"], 4), result["risk_level"],
                _dumps(result["probabilities"]), _dumps(result["factors"]), _dumps(result["fingerprint"]),
                _dumps(result["spectrogram"]), _dumps(result["envelope"]), "{}", 0, result["model_version"], now,
            ),
        )
        if result["risk_level"] in ("medium", "high"):
            db.execute(
                "INSERT INTO alerts (analysis_id, level, status, created_at) VALUES (?, ?, 'new', ?)",
                (analysis_id, result["risk_level"], now),
            )
        timings = dict(result["timings"])
        timings["report"] += round((time.perf_counter() - report_started) * 1000)
        processing_ms = round((time.perf_counter() - started) * 1000)
        db.execute(
            "UPDATE analyses SET stage_timings_json = ?, processing_ms = ? WHERE id = ?",
            (_dumps(timings), processing_ms, analysis_id),
        )
    except Exception:
        logger.exception("فشل حفظ نتيجة التحليل")
        return jsonify(error="تعذر حفظ نتيجة الفحص، يرجى المحاولة لاحقاً."), 500
    return jsonify(
        id=analysis_id,
        redirect=url_for("pages.report", analysis_id=analysis_id),
        verdict=result["verdict"],
        confidence=result["confidence"],
        timings=timings,
        processing_ms=processing_ms,
    )


@bp.post("/api/alerts/<int:alert_id>/review")
@security.roles_required("admin", "analyst")
def review_alert(alert_id):
    try:
        alert = db.query_one("SELECT id, status FROM alerts WHERE id = ?", (alert_id,))
        if alert is None:
            return jsonify(error="التنبيه غير موجود."), 404
        if alert["status"] != "reviewed":
            db.execute(
                "UPDATE alerts SET status = 'reviewed', reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                (g.user["id"], db.utc_iso(), alert_id),
            )
        row = db.query_one(
            "SELECT a.reviewed_at, u.display_name FROM alerts a LEFT JOIN users u ON u.id = a.reviewed_by WHERE a.id = ?",
            (alert_id,),
        )
        return jsonify(ok=True, status="reviewed", reviewed_at=row["reviewed_at"], reviewed_by=row["display_name"])
    except Exception:
        logger.exception("فشل وسم التنبيه كمُراجَع")
        return jsonify(error="تعذر تحديث حالة التنبيه."), 500