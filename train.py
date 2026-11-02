"""تدريب نموذج VoxShield وتخزينه في models/ وتسجيل نتائجه في model_runs."""
import glob
import json
import logging
import os
import sys
import threading
import time

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import database as db
from config import Config
from engine import AudioError, vector_from_wav
from engine.audio_io import encode_wav, preprocess, read_wav
from engine.features import FEATURE_NAMES
from engine.synth import apply_replay_channel, generate

logger = logging.getLogger("voxshield")

CLASSES = ("live", "replay", "synthetic")
MIN_FILES_PER_CLASS = 20
DEMO_SAMPLES_PER_CLASS = 400
DEMO_DURATION = 3.0
DEMO_SEEDS = {"live": 100000, "replay": 200000, "synthetic": 300000}
MAX_FILES_PER_CLASS = 1500
SUPPLEMENT_PER_CLASS = 200
SOURCE_DEMO = "تجريبي"
SOURCE_REAL = "حقيقي"
SOURCE_REAL_SIMULATED_REPLAY = "حقيقي (إعادة التشغيل محاكاة)"

TRAINING_STATE = {"running": False, "error": None, "started_at": None}
_state_lock = threading.Lock()


def _dataset_files():
    return {
        label: sorted(glob.glob(os.path.join(Config.DATASET_DIR, label, "*.wav")))
        for label in CLASSES
    }


def _sample_paths(paths, count, seed):
    if len(paths) <= count:
        return list(paths)
    rng = np.random.default_rng(seed)
    return [paths[i] for i in sorted(rng.choice(len(paths), count, replace=False))]


def _vector_from_file(path, replay_seed=None):
    with open(path, "rb") as handle:
        data = handle.read()
    if replay_seed is not None:
        signal, sample_rate, _duration = read_wav(data, 600)
        if sample_rate != 16000:
            signal, _stats = preprocess(signal, sample_rate)
        data = encode_wav(apply_replay_channel(signal, replay_seed))
    return vector_from_wav(data, max_seconds=600)


def _load_real_dataset(files, simulate_replay, verbose):
    """قراءة المقاطع الحقيقية بحد أقصى لكل فئة، مع بناء فئة إعادة التشغيل من مقاطع حية مختلفة عند غيابها."""
    plan = {label: _sample_paths(files[label], MAX_FILES_PER_CLASS, 42 + i) for i, label in enumerate(CLASSES)}
    if simulate_replay:
        live_set = set(plan["live"])
        remaining = [p for p in files["live"] if p not in live_set]
        plan["replay"] = _sample_paths(remaining or files["live"], MAX_FILES_PER_CLASS, 99)
    features, labels = [], []
    for label in CLASSES:
        for i, path in enumerate(plan[label]):
            seed = DEMO_SEEDS["replay"] + i if (simulate_replay and label == "replay") else None
            try:
                features.append(_vector_from_file(path, seed))
                labels.append(label)
            except (AudioError, OSError, ValueError):
                logger.warning("تم تجاوز ملف غير صالح في مجموعة البيانات: %s", os.path.basename(path))
            if verbose and (i + 1) % 250 == 0:
                print("  {}: {} / {}".format(label, i + 1, len(plan[label])))
    return features, labels


def _build_demo_dataset(verbose, count=DEMO_SAMPLES_PER_CLASS):
    features, labels = [], []
    for label in CLASSES:
        for i in range(count):
            signal = generate(label, DEMO_SEEDS[label] + i, DEMO_DURATION)
            features.append(vector_from_wav(encode_wav(signal), max_seconds=600))
            labels.append(label)
            if verbose and (i + 1) % 100 == 0:
                print("  {}: {} / {}".format(label, i + 1, count))
    return features, labels


def run_training(verbose=False):
    """قراءة البيانات أو توليد مجموعة تجريبية، ثم التدريب والتقييم والحفظ."""
    started = time.time()
    files = _dataset_files()
    counts = {label: len(paths) for label, paths in files.items()}
    if all(count >= MIN_FILES_PER_CLASS for count in counts.values()):
        source = SOURCE_REAL
        features, labels = _load_real_dataset(files, False, verbose)
    elif counts["live"] >= MIN_FILES_PER_CLASS and counts["synthetic"] >= MIN_FILES_PER_CLASS:
        source = SOURCE_REAL_SIMULATED_REPLAY
        if verbose:
            print("فئة إعادة التشغيل غير متوفرة، تُبنى من مقاطع حية حقيقية بعد تمريرها عبر قناة إعادة تشغيل محاكاة...")
        features, labels = _load_real_dataset(files, True, verbose)
    else:
        source = SOURCE_DEMO
        if verbose:
            print("مجموعة البيانات الحقيقية غير مكتملة، يجري توليد مجموعة تجريبية ({} مقطع لكل فئة)...".format(DEMO_SAMPLES_PER_CLASS))
        features, labels = _build_demo_dataset(verbose)
    if source != SOURCE_DEMO:
        if verbose:
            print("إضافة {} مقطع توضيحي لكل فئة حتى تبقى عينات بيئة المحاكاة ضمن نطاق النموذج...".format(SUPPLEMENT_PER_CLASS))
        extra_features, extra_labels = _build_demo_dataset(verbose, SUPPLEMENT_PER_CLASS)
        features += extra_features
        labels += extra_labels
    X = np.vstack(features)
    y = np.asarray(labels)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, stratify=y, random_state=42)
    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=42, n_jobs=-1)),
    ])
    pipeline.fit(X_train, y_train)
    predictions = pipeline.predict(X_test)
    accuracy = float(accuracy_score(y_test, predictions))
    confusion = confusion_matrix(y_test, predictions, labels=list(CLASSES)).tolist()
    live_train = X_train[y_train == "live"]
    global_std = X_train.std(axis=0)
    live_std = np.maximum(live_train.std(axis=0), np.maximum(0.1 * global_std, 1e-6))
    trained_at = db.utc_iso()
    conn = db.connect(readonly=False)
    try:
        cursor = conn.execute(
            "INSERT INTO model_runs (trained_at, data_source, samples_count, accuracy, confusion_json, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (trained_at, source, int(len(y)), accuracy, json.dumps({"labels": list(CLASSES), "matrix": confusion}), ""),
        )
        run_id = cursor.lastrowid
        version = "VS-RF-{}".format(run_id)
        bundle = {
            "pipeline": pipeline,
            "feature_names": FEATURE_NAMES,
            "live_mean": live_train.mean(axis=0),
            "live_std": live_std,
            "version": version,
            "run_id": run_id,
            "data_source": source,
            "trained_at": trained_at,
        }
        os.makedirs(Config.MODEL_DIR, exist_ok=True)
        temp_path = Config.MODEL_PATH + ".tmp"
        joblib.dump(bundle, temp_path)
        os.replace(temp_path, Config.MODEL_PATH)
        notes = "إصدار النموذج {}: {} عينة تدريب و{} عينة اختبار، زمن التدريب {} ثانية".format(
            version, len(y_train), len(y_test), round(time.time() - started))
        conn.execute("UPDATE model_runs SET notes = ? WHERE id = ?", (notes, run_id))
        conn.commit()
    finally:
        conn.close()
    logger.info("اكتمل تدريب النموذج %s بدقة %.4f من مصدر %s", version, accuracy, source)
    return {"version": version, "accuracy": accuracy, "confusion": confusion, "source": source, "samples": int(len(y))}


def _background_job():
    try:
        run_training()
        error = None
    except Exception:
        logger.exception("فشل تدريب النموذج في الخلفية")
        error = "تعذر إكمال إعادة التدريب، راجع سجل التطبيق."
    with _state_lock:
        TRAINING_STATE.update(running=False, error=error)


def start_background_training():
    with _state_lock:
        if TRAINING_STATE["running"]:
            return False
        TRAINING_STATE.update(running=True, error=None, started_at=db.utc_iso())
    threading.Thread(target=_background_job, daemon=True).start()
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    db.init_db()
    summary = run_training(verbose=True)
    print("الإصدار: {} | المصدر: {} | العينات: {} | الدقة: {:.2f}%".format(
        summary["version"], summary["source"], summary["samples"], summary["accuracy"] * 100))
    print("مصفوفة الالتباس (حي، إعادة تشغيل، مولد):")
    for row in summary["confusion"]:
        print("  ", row)