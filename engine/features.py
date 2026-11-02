"""استخلاص البصمة الصوتية: متجه رقمي ثابت الطول من 135 خاصية."""
import numpy as np
from scipy.fft import dct
from scipy.signal import get_window

from .audio_io import FRAME_LEN, HOP_LEN, N_FFT, TARGET_SR, frame_signal, power_spectrum

EPS = 1e-10
FREQS = np.fft.rfftfreq(N_FFT, 1.0 / TARGET_SR)
N_FILTERS = 40
N_CEPS = 20
PITCH_LEN = 960
PITCH_MIN_LAG = TARGET_SR // 400
PITCH_MAX_LAG = TARGET_SR // 50
VOICING_THRESHOLD = 0.5
GAP_THRESHOLD_DB = -65.0
BANDS = [(0, 300), (300, 1000), (1000, 3000), (3000, 5000), (5000, 7000), (7000, 8001)]

GROUPS = {
    "lfcc": "معاملات LFCC للطيف الخطي",
    "mfcc": "معاملات MFCC للطيف الميلي",
    "mfcc_delta": "تغيّر معاملات MFCC عبر الزمن",
    "spectral_shape": "شكل الطيف الترددي",
    "band_energy": "توزيع الطاقة على النطاقات الترددية",
    "hf_regularity": "انتظام الترددات العليا",
    "pitch": "طبقة الصوت والنبرة",
    "temporal": "الخصائص الزمنية والضجيج",
    "replay": "مؤشرات إعادة التشغيل",
}

SPECTRAL_SHAPE = [
    ("centroid", "المركز الطيفي"),
    ("bandwidth", "العرض الطيفي"),
    ("rolloff85", "نقطة الهبوط الطيفي عند 85 بالمئة"),
    ("rolloff95", "نقطة الهبوط الطيفي عند 95 بالمئة"),
    ("flatness", "الاستواء الطيفي"),
    ("flux", "التدفق الطيفي"),
    ("entropy", "الإنتروبيا الطيفية"),
]


def _build_definitions():
    defs = []
    for prefix, group, title in (("lfcc", "lfcc", "LFCC"), ("mfcc", "mfcc", "MFCC")):
        for i in range(N_CEPS):
            defs.append(("{}_mean_{}".format(prefix, i), "متوسط معامل {} رقم {}".format(title, i), group))
        for i in range(N_CEPS):
            defs.append(("{}_std_{}".format(prefix, i), "انحراف معامل {} رقم {}".format(title, i), group))
    for i in range(13):
        defs.append(("mfcc_delta_{}".format(i), "متوسط تغيّر معامل MFCC رقم {}".format(i), "mfcc_delta"))
    for key, label in SPECTRAL_SHAPE:
        defs.append(("{}_mean".format(key), "متوسط " + label, "spectral_shape"))
        defs.append(("{}_std".format(key), "انحراف " + label, "spectral_shape"))
    for low, high in BANDS:
        top = min(high, 8000)
        defs.append(("band_{}_{}".format(low, top), "نسبة الطاقة في النطاق {}-{} هرتز".format(low, top), "band_energy"))
    defs += [
        ("hf_energy_variability", "تغيّر طاقة النطاق 4000-8000 هرتز عبر الزمن", "hf_regularity"),
        ("hf_spectral_gaps", "نسبة الفجوات الطيفية فوق 4000 هرتز", "hf_regularity"),
        ("f0_mean", "متوسط التردد الأساسي", "pitch"),
        ("f0_std", "انحراف التردد الأساسي", "pitch"),
        ("f0_range", "مدى التردد الأساسي", "pitch"),
        ("jitter_local", "الاهتزاز الدوري المحلي (jitter)", "pitch"),
        ("shimmer_local", "الاهتزاز السعوي المحلي (shimmer)", "pitch"),
        ("voiced_ratio", "نسبة الإطارات المنطوقة", "pitch"),
        ("hnr", "نسبة التوافقيات إلى الضجيج HNR", "pitch"),
        ("zcr_mean", "متوسط معدل عبور الصفر", "temporal"),
        ("zcr_std", "انحراف معدل عبور الصفر", "temporal"),
        ("energy_mean", "متوسط طاقة الإطارات", "temporal"),
        ("energy_std", "انحراف طاقة الإطارات", "temporal"),
        ("dynamic_range", "المدى الديناميكي بين المئين 5 والمئين 95", "temporal"),
        ("noise_floor", "مستوى أرضية الضجيج عند المئين 10", "temporal"),
        ("modulation_2_8", "حصة تضمين الطاقة بين 2 و8 هرتز", "temporal"),
        ("decay_rate", "معدل تلاشي الطاقة بعد الذروات (قرينة الصدى)", "replay"),
        ("low_cutoff", "الحد الترددي الأدنى للطاقة", "replay"),
        ("high_cutoff", "الحد الترددي الأعلى للطاقة", "replay"),
        ("dc_offset", "الإزاحة المستمرة", "replay"),
        ("clip_ratio", "نسبة القص", "replay"),
        ("raw_rms_db", "مستوى RMS الخام", "replay"),
    ]
    return defs


FEATURE_DEFS = _build_definitions()
FEATURE_NAMES = [d[0] for d in FEATURE_DEFS]
FEATURE_LABELS = {d[0]: d[1] for d in FEATURE_DEFS}
FEATURE_GROUPS = {d[0]: d[2] for d in FEATURE_DEFS}


def _triangular_bank(edges):
    bank = np.zeros((len(edges) - 2, len(FREQS)))
    for m in range(len(edges) - 2):
        low, center, high = edges[m], edges[m + 1], edges[m + 2]
        rising = (FREQS - low) / max(center - low, EPS)
        falling = (high - FREQS) / max(high - center, EPS)
        bank[m] = np.maximum(0.0, np.minimum(rising, falling))
    return bank


def _hz_to_mel(hz):
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel):
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


LINEAR_BANK = _triangular_bank(np.linspace(0.0, 8000.0, N_FILTERS + 2))
MEL_BANK = _triangular_bank(_mel_to_hz(np.linspace(0.0, _hz_to_mel(8000.0), N_FILTERS + 2)))
PITCH_WINDOW = get_window("hann", PITCH_LEN, fftbins=True)


def _window_autocorrelation():
    spectrum = np.fft.rfft(PITCH_WINDOW, 2 * PITCH_LEN)
    ac = np.fft.irfft(np.abs(spectrum) ** 2, 2 * PITCH_LEN)[:PITCH_LEN]
    return ac / ac[0]


WINDOW_AC = _window_autocorrelation()


def _cepstra(power, bank):
    energies = np.log(power @ bank.T + EPS)
    return dct(energies, type=2, norm="ortho", axis=1)[:, :N_CEPS]


def _safe_stats(values):
    if values.size == 0:
        return 0.0, 0.0
    return float(np.mean(values)), float(np.std(values))


def _spectral_shape(power):
    total = power.sum(axis=1) + EPS
    centroid = (power * FREQS).sum(axis=1) / total
    bandwidth = np.sqrt((power * (FREQS[None, :] - centroid[:, None]) ** 2).sum(axis=1) / total)
    cumulative = np.cumsum(power, axis=1) / total[:, None]
    rolloff85 = FREQS[np.argmax(cumulative >= 0.85, axis=1)]
    rolloff95 = FREQS[np.argmax(cumulative >= 0.95, axis=1)]
    flatness = np.exp(np.mean(np.log(power + EPS), axis=1)) / (np.mean(power, axis=1) + EPS)
    magnitude = np.sqrt(power)
    normalized = magnitude / (magnitude.sum(axis=1, keepdims=True) + EPS)
    flux = np.sqrt(np.sum(np.diff(normalized, axis=0) ** 2, axis=1)) if len(power) > 1 else np.zeros(1)
    probability = power / total[:, None]
    entropy = -(probability * np.log(probability + EPS)).sum(axis=1) / np.log(power.shape[1])
    return {
        "centroid": centroid, "bandwidth": bandwidth, "rolloff85": rolloff85, "rolloff95": rolloff95,
        "flatness": flatness, "flux": flux, "entropy": entropy,
    }


def _pitch_track(signal, frame_count, active):
    """تقدير التردد الأساسي بالارتباط الذاتي المعياري مع تصحيح أثر النافذة."""
    pad = (PITCH_LEN - FRAME_LEN) // 2
    padded = np.pad(signal, (pad, pad + PITCH_LEN))
    frames = frame_signal(padded, PITCH_LEN, HOP_LEN)[:frame_count]
    frames = frames - frames.mean(axis=1, keepdims=True)
    weighted = frames * PITCH_WINDOW
    spectrum = np.fft.rfft(weighted, 2 * PITCH_LEN, axis=1)
    ac = np.fft.irfft(np.abs(spectrum) ** 2, 2 * PITCH_LEN, axis=1)[:, : PITCH_MAX_LAG + 2]
    ac = ac / (ac[:, :1] + EPS) / WINDOW_AC[: PITCH_MAX_LAG + 2]
    f0 = np.zeros(frame_count)
    strength = np.zeros(frame_count)
    amplitude = np.max(np.abs(frames), axis=1)
    for i in np.where(active)[0]:
        r = ac[i]
        segment = r[PITCH_MIN_LAG: PITCH_MAX_LAG + 1]
        peaks = np.where((segment[1:-1] > segment[:-2]) & (segment[1:-1] >= segment[2:]))[0] + 1
        if peaks.size == 0:
            continue
        best = segment[peaks].max()
        if best < VOICING_THRESHOLD:
            continue
        chosen = peaks[np.argmax(segment[peaks] >= 0.85 * best)]
        lag = chosen + PITCH_MIN_LAG
        y0, y1, y2 = r[lag - 1], r[lag], r[lag + 1]
        denominator = y0 - 2.0 * y1 + y2
        delta = 0.5 * (y0 - y2) / denominator if abs(denominator) > EPS else 0.0
        f0[i] = TARGET_SR / (lag + float(np.clip(delta, -0.5, 0.5)))
        strength[i] = min(float(y1), 0.9999)
    return f0, strength, amplitude


def _pitch_features(f0, strength, amplitude, active):
    voiced = f0 > 0
    active_count = max(int(active.sum()), 1)
    if voiced.sum() < 3:
        return [0.0, 0.0, 0.0, 0.0, 0.0, float(voiced.sum()) / active_count, 0.0]
    values = f0[voiced]
    pairs = voiced[:-1] & voiced[1:]
    periods = 1.0 / np.where(voiced, f0, 1.0)
    if pairs.any():
        jitter = np.mean(np.abs(periods[:-1][pairs] - periods[1:][pairs])) / np.mean(periods[voiced]) * 100.0
        shimmer = np.mean(np.abs(amplitude[:-1][pairs] - amplitude[1:][pairs])) / (np.mean(amplitude[voiced]) + EPS) * 100.0
    else:
        jitter = shimmer = 0.0
    r = np.clip(strength[voiced], 1e-4, 0.9999)
    hnr = float(np.mean(10.0 * np.log10(r / (1.0 - r))))
    return [
        float(np.mean(values)), float(np.std(values)),
        float(np.percentile(values, 95) - np.percentile(values, 5)),
        float(jitter), float(shimmer), float(voiced.sum()) / active_count, hnr,
    ]


def _decay_rate(energy_db):
    smooth = np.convolve(energy_db, np.ones(3) / 3.0, mode="same")
    threshold = smooth.max() - 25.0
    slopes = []
    span = 15
    times = np.arange(span) * HOP_LEN / TARGET_SR
    for i in range(5, len(smooth) - span):
        window = smooth[i - 5: i + 6]
        if smooth[i] < threshold or smooth[i] < window.max():
            continue
        slopes.append(np.polyfit(times, smooth[i: i + span], 1)[0])
    return float(np.mean(slopes)) if slopes else 0.0


def _modulation_share(frame_rms):
    envelope = frame_rms - frame_rms.mean()
    size = int(2 ** np.ceil(np.log2(max(len(envelope), 64))))
    spectrum = np.abs(np.fft.rfft(envelope, size)) ** 2
    freqs = np.fft.rfftfreq(size, HOP_LEN / TARGET_SR)
    total = spectrum[(freqs >= 0.5) & (freqs <= 50.0)].sum() + EPS
    return float(spectrum[(freqs >= 2.0) & (freqs <= 8.0)].sum() / total)


def extract(signal, raw_stats):
    """يعيد متجه الخصائص وتفاصيل مساعدة للعرض في التقرير."""
    frames = frame_signal(signal)
    power = power_spectrum(frames)
    frame_energy = np.mean(frames ** 2, axis=1)
    energy_db = 10.0 * np.log10(frame_energy + EPS)
    active = energy_db > energy_db.max() - 50.0
    if active.sum() < 5:
        active = np.ones(len(frames), dtype=bool)
    active_power = power[active]
    values = []
    lfcc = _cepstra(active_power, LINEAR_BANK)
    values += list(lfcc.mean(axis=0)) + list(lfcc.std(axis=0))
    mfcc = _cepstra(active_power, MEL_BANK)
    values += list(mfcc.mean(axis=0)) + list(mfcc.std(axis=0))
    mfcc_all = _cepstra(power, MEL_BANK)
    values += list(np.mean(np.abs(np.diff(mfcc_all[:, :13], axis=0)), axis=0))
    shape = _spectral_shape(active_power)
    for key, _label in SPECTRAL_SHAPE:
        values += list(_safe_stats(shape[key]))
    total_energy = power.sum() + EPS
    for low, high in BANDS:
        mask = (FREQS >= low) & (FREQS < high)
        values.append(float(np.log10(power[:, mask].sum() / total_energy + EPS)))
    high_mask = FREQS >= 4000
    hf_db = 10.0 * np.log10(active_power[:, high_mask].sum(axis=1) + EPS)
    mean_spectrum = active_power.mean(axis=0)
    mean_db = 10.0 * np.log10(mean_spectrum + EPS)
    relative_db = mean_db - mean_db.max()
    values += [float(np.std(hf_db)), float(np.mean(relative_db[high_mask] < GAP_THRESHOLD_DB))]
    f0, strength, amplitude = _pitch_track(signal, len(frames), active)
    pitch = _pitch_features(f0, strength, amplitude, active)
    values += pitch
    signs = np.signbit(frames)
    zcr = np.mean(signs[:, 1:] != signs[:, :-1], axis=1)[active]
    values += list(_safe_stats(zcr))
    values += [float(np.mean(energy_db)), float(np.std(energy_db))]
    values.append(float(np.percentile(energy_db, 95) - np.percentile(energy_db, 5)))
    values.append(float(np.percentile(energy_db, 10)))
    values.append(_modulation_share(np.sqrt(frame_energy)))
    cumulative = np.cumsum(mean_spectrum) / (mean_spectrum.sum() + EPS)
    values.append(_decay_rate(energy_db))
    values.append(float(FREQS[np.argmax(cumulative >= 0.01)]))
    values.append(float(FREQS[np.argmax(cumulative >= 0.99)]))
    values += [raw_stats["dc_offset"], raw_stats["clip_ratio"], raw_stats["rms_db"]]
    vector = np.nan_to_num(np.asarray(values, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    vector = np.clip(vector, -1e6, 1e6)
    details = {"power": power, "active": active, "relative_db": relative_db}
    return vector, details


def fingerprint_summary(vector):
    """بصمة مختصرة بأهم الخصائص مع وحداتها للعرض في التقرير."""
    index = {name: i for i, name in enumerate(FEATURE_NAMES)}
    items = [
        ("f0_mean", "هرتز", 1), ("f0_std", "هرتز", 1), ("jitter_local", "%", 3), ("shimmer_local", "%", 2),
        ("hnr", "ديسيبل", 1), ("voiced_ratio", "%", 1), ("centroid_mean", "هرتز", 0),
        ("rolloff95_mean", "هرتز", 0), ("flatness_mean", "", 4), ("hf_spectral_gaps", "%", 1),
        ("low_cutoff", "هرتز", 0), ("high_cutoff", "هرتز", 0), ("decay_rate", "ديسيبل/ثانية", 1),
        ("noise_floor", "ديسيبل", 1), ("modulation_2_8", "%", 1),
    ]
    summary = []
    for name, unit, digits in items:
        value = float(vector[index[name]])
        if name in ("voiced_ratio", "hf_spectral_gaps", "modulation_2_8"):
            value *= 100.0
        summary.append({"name": name, "label": FEATURE_LABELS[name], "value": round(value, digits), "unit": unit})
    return summary