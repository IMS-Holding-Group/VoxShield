"""محرك تحليل VoxShield: يربط القراءة والاستخلاص والتصنيف وبيانات العرض."""
import time

from .audio_io import AudioError, preprocess, read_wav
from .classifier import ModelUnavailable, classify
from .features import extract, fingerprint_summary
from .spectrogram import compute_envelope, compute_spectrogram

__all__ = ["AudioError", "ModelUnavailable", "run_analysis", "vector_from_wav"]


def vector_from_wav(data, max_seconds):
    signal, sample_rate, _duration = read_wav(data, max_seconds)
    processed, raw_stats = preprocess(signal, sample_rate)
    vector, _details = extract(processed, raw_stats)
    return vector


def run_analysis(data, max_seconds, include_visuals=True, started=None):
    """تنفيذ مراحل النظام الأربع وقياس زمن كل مرحلة بالملّي ثانية."""
    started = started or time.perf_counter()
    signal, sample_rate, duration = read_wav(data, max_seconds)
    processed, raw_stats = preprocess(signal, sample_rate)
    t1 = time.perf_counter()
    vector, details = extract(processed, raw_stats)
    t2 = time.perf_counter()
    result = classify(vector, with_factors=include_visuals)
    t3 = time.perf_counter()
    if include_visuals:
        result["fingerprint"] = fingerprint_summary(vector)
        result["spectrogram"] = compute_spectrogram(processed, details["power"])
        result["envelope"] = compute_envelope(details["relative_db"])
    t4 = time.perf_counter()
    result["duration_sec"] = round(duration, 2)
    result["sample_rate"] = sample_rate
    result["timings"] = {
        "upload": round((t1 - started) * 1000),
        "extract": round((t2 - t1) * 1000),
        "classify": round((t3 - t2) * 1000),
        "report": round((t4 - t3) * 1000),
    }
    return result