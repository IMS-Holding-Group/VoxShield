"""تحميل النموذج والتصنيف وتفسير القرار."""
import os
import threading

import joblib
import numpy as np

from config import Config

from .features import FEATURE_GROUPS, FEATURE_LABELS, FEATURE_NAMES, GROUPS

CLASSES = ("live", "replay", "synthetic")
INCONCLUSIVE_NOTE = "النتيجة غير حاسمة وتستدعي مراجعة بشرية"
DIRECTION_HIGH = "أعلى من المعتاد في الصوت الحي"
DIRECTION_LOW = "أقل من المعتاد في الصوت الحي"

_lock = threading.Lock()
_cache = {"bundle": None, "mtime": None}


class ModelUnavailable(RuntimeError):
    pass


def load_bundle():
    """تحميل النموذج من models/ مع إعادة التحميل تلقائياً بعد إعادة التدريب."""
    if not os.path.exists(Config.MODEL_PATH):
        raise ModelUnavailable("ملف النموذج غير موجود")
    mtime = os.path.getmtime(Config.MODEL_PATH)
    with _lock:
        if _cache["bundle"] is None or _cache["mtime"] != mtime:
            _cache["bundle"] = joblib.load(Config.MODEL_PATH)
            _cache["mtime"] = mtime
        return _cache["bundle"]


def risk_level(verdict, confidence):
    if verdict == "fake" and confidence >= 0.8:
        return "high"
    if verdict == "fake" or confidence < 0.6:
        return "medium"
    return "low"


def explain(vector, bundle, top=5):
    """الإسهام = أهمية الخاصية × |z| بالنسبة لإحصاءات الفئة الحية، ثم التجميع حسب المجموعة."""
    importances = bundle["pipeline"].named_steps["clf"].feature_importances_
    z = np.clip((vector - bundle["live_mean"]) / bundle["live_std"], -10.0, 10.0)
    contribution = importances * np.abs(z)
    signed = importances * z
    total = contribution.sum() or 1.0
    groups = {}
    for i, name in enumerate(FEATURE_NAMES):
        entry = groups.setdefault(FEATURE_GROUPS[name], {"value": 0.0, "signed": 0.0, "best": None, "best_value": -1.0})
        entry["value"] += contribution[i]
        entry["signed"] += signed[i]
        if contribution[i] > entry["best_value"]:
            entry["best_value"] = contribution[i]
            entry["best"] = name
    ranked = sorted(groups.items(), key=lambda item: item[1]["value"], reverse=True)[:top]
    return [
        {
            "group": key,
            "label": GROUPS[key],
            "direction": DIRECTION_HIGH if data["signed"] >= 0 else DIRECTION_LOW,
            "percent": round(float(data["value"] / total * 100.0), 1),
            "top_feature": FEATURE_LABELS[data["best"]],
        }
        for key, data in ranked
    ]


def classify(vector, with_factors=True):
    bundle = load_bundle()
    pipeline = bundle["pipeline"]
    proba = pipeline.predict_proba(vector.reshape(1, -1))[0]
    classes = list(pipeline.classes_)
    probabilities = {c: float(proba[classes.index(c)]) if c in classes else 0.0 for c in CLASSES}
    p_live = probabilities["live"]
    if p_live >= 0.5:
        verdict, confidence, attack = "genuine", p_live, None
    else:
        verdict, confidence = "fake", 1.0 - p_live
        attack = "replay" if probabilities["replay"] >= probabilities["synthetic"] else "synthetic"
    return {
        "verdict": verdict,
        "confidence": confidence,
        "attack_type": attack,
        "risk_level": risk_level(verdict, confidence),
        "inconclusive": confidence < 0.6,
        "probabilities": probabilities,
        "factors": explain(vector, bundle) if with_factors else [],
        "model_version": bundle["version"],
    }