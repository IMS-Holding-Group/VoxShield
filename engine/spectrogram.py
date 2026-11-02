"""حساب مصفوفة الطيف المصغرة ومنحنى الغلاف الطيفي للعرض في المتصفح."""
import numpy as np

from .audio_io import TARGET_SR, frame_signal, power_spectrum
from .features import FREQS

EPS = 1e-10
SPEC_ROWS = 64
SPEC_MAX_COLS = 240
ENVELOPE_POINTS = 128
DYNAMIC_RANGE_DB = 80.0


def compute_spectrogram(signal, power=None):
    if power is None:
        power = power_spectrum(frame_signal(signal))
    bins = power[:, 1:257]
    pooled = bins.reshape(len(bins), SPEC_ROWS, -1).mean(axis=2)
    if len(pooled) > SPEC_MAX_COLS:
        pooled = np.array([chunk.mean(axis=0) for chunk in np.array_split(pooled, SPEC_MAX_COLS)])
    level = 10.0 * np.log10(pooled + EPS)
    top = level.max()
    scaled = np.clip((level - (top - DYNAMIC_RANGE_DB)) / DYNAMIC_RANGE_DB, 0.0, 1.0)
    return {
        "rows": SPEC_ROWS,
        "cols": int(len(scaled)),
        "max_freq": TARGET_SR // 2,
        "duration": round(len(signal) / float(TARGET_SR), 2),
        "data": np.round(scaled * 255).astype(int).tolist(),
    }


def compute_envelope(relative_db):
    values = relative_db[1:257].reshape(ENVELOPE_POINTS, -1).mean(axis=1)
    freqs = FREQS[1:257].reshape(ENVELOPE_POINTS, -1).mean(axis=1)
    return {
        "freqs": np.round(freqs, 1).tolist(),
        "db": np.round(np.clip(values, -100.0, 0.0), 2).tolist(),
    }