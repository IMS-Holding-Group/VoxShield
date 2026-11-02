"""مولدات إشارات توضيحية خوارزمية بالكامل للتدريب والمحاكاة.

لا تستخدم هذه المولدات أي تسجيل حقيقي ولا تحاكي صوت شخص بعينه، وإنما تبني
إشارة شبيهة بالكلام من قطار نبضات حنجرية ومرنانات تمثل صيغ حروف العلة.
"""
import numpy as np
from scipy.signal import butter, fftconvolve, lfilter, resample_poly, sosfilt

SR = 16000
HI_SR = 48000
VOWELS = [
    (730, 1090, 2440, 3400, 4300),
    (270, 2290, 3010, 3500, 4500),
    (300, 870, 2240, 3300, 4200),
    (530, 1840, 2480, 3500, 4400),
    (570, 840, 2410, 3300, 4300),
]


def _resonator(signal, freq, bandwidth, sr):
    r = np.exp(-np.pi * bandwidth / sr)
    a = [1.0, -2.0 * r * np.cos(2.0 * np.pi * freq / sr), r * r]
    return lfilter([1.0 - r], a, signal)


def _glottal_source(rng, duration, f0_base, jitter_pct, shimmer_pct, contour_depth):
    """قطار نبضات حنجرية بطور مستمر يسمح بتذبذب دقيق في طول الدورة."""
    total = int(duration * HI_SR)
    source = np.zeros(total)
    rate = rng.uniform(0.4, 1.2)
    phase = rng.uniform(0, 2 * np.pi)
    t = 0.0
    open_phase = rng.uniform(0.38, 0.5)
    close_phase = rng.uniform(0.12, 0.18)
    while t < duration:
        contour = 1.0 + contour_depth * np.sin(2 * np.pi * rate * t + phase) - 0.06 * t / duration
        period = 1.0 / (f0_base * contour)
        period *= 1.0 + rng.normal(0.0, jitter_pct / 100.0)
        amp = 1.0 + rng.normal(0.0, shimmer_pct / 100.0)
        start = int(t * HI_SR)
        end = min(int((t + period) * HI_SR), total)
        if end > start:
            p = (np.arange(start, end) / HI_SR - t) / period
            pulse = np.where(
                p < open_phase,
                0.5 * (1.0 - np.cos(np.pi * p / open_phase)),
                np.where(p < open_phase + close_phase, np.cos(np.pi * (p - open_phase) / (2.0 * close_phase)), 0.0),
            )
            source[start:end] = amp * pulse
        t += period
    flow = resample_poly(source, 1, HI_SR // SR)
    return np.diff(flow, prepend=0.0)


def _syllable_weights(rng, n, count, rate_hz, regular):
    t = np.arange(n) / SR
    if regular:
        positions = np.arange(count)
    else:
        positions = np.cumsum(rng.uniform(0.7, 1.3, count)) - 1.0
    centers = positions / rate_hz
    weights = np.zeros((len(VOWELS), n))
    order = rng.integers(0, len(VOWELS), count)
    width = 0.6 / rate_hz
    for c, v in zip(centers, order):
        weights[v] += np.exp(-0.5 * ((t - c) / width) ** 2)
    weights += 1e-6
    return weights / weights.sum(axis=0, keepdims=True)


def _vocal_tract(rng, source, regular, bandwidth_scale):
    n = len(source)
    rate = 4.0 if regular else rng.uniform(3.5, 4.6)
    count = int(n / SR * rate) + 2
    weights = _syllable_weights(rng, n, count, rate, regular)
    output = np.zeros(n)
    for v, formants in enumerate(VOWELS):
        shifted = [f * rng.uniform(0.95, 1.05) for f in formants]
        filtered = source
        for f, bw in zip(shifted, (80, 100, 140, 200, 260)):
            filtered = _resonator(filtered, f, bw * bandwidth_scale, SR)
        output += weights[v] * filtered
    return output


def _syllabic_envelope(rng, n, regular):
    t = np.arange(n) / SR
    rate = 4.0 if regular else rng.uniform(3.6, 4.4)
    phase = rng.uniform(0, 2 * np.pi)
    depth = 0.75 if regular else rng.uniform(0.6, 0.85)
    env = (1.0 - depth) + depth * (0.5 - 0.5 * np.cos(2 * np.pi * rate * t + phase))
    if not regular:
        drift = np.interp(t, np.linspace(0, t[-1], 8), rng.uniform(0.6, 1.0, 8))
        env *= drift
    return env


def _normalize(signal, rng):
    return signal / (np.max(np.abs(signal)) + 1e-12) * rng.uniform(0.6, 0.9)


def generate_live(seed, duration=3.0):
    """صوت حي توضيحي: تذبذب طبيعي وضجيج تنفس ونطاق ترددي واسع."""
    rng = np.random.default_rng(seed)
    f0 = rng.uniform(110, 220)
    source = _glottal_source(rng, duration, f0, rng.uniform(0.4, 1.2), rng.uniform(3.0, 7.0), rng.uniform(0.05, 0.12))
    n = len(source)
    breath = rng.normal(0, 1, n)
    breath = sosfilt(butter(2, [1500, 7800], btype="band", fs=SR, output="sos"), breath)
    source = source / (np.std(source) + 1e-12) + breath * rng.uniform(0.08, 0.16)
    voiced = _vocal_tract(rng, source, regular=False, bandwidth_scale=1.0)
    voiced *= _syllabic_envelope(rng, n, regular=False)
    fricative = sosfilt(butter(2, [3500, 7900], btype="band", fs=SR, output="sos"), rng.normal(0, 1, n))
    bursts = np.zeros(n)
    for _ in range(int(duration * 1.5)):
        center = rng.integers(0, n)
        width = int(rng.uniform(0.04, 0.09) * SR)
        lo, hi = max(0, center - width), min(n, center + width)
        bursts[lo:hi] += np.hanning(hi - lo)
    voiced = voiced / (np.std(voiced) + 1e-12)
    signal = voiced + fricative * bursts * rng.uniform(0.25, 0.45)
    signal += rng.normal(0, 1, n) * np.std(signal) * 10 ** (rng.uniform(-52, -44) / 20)
    return _normalize(signal, rng)


def generate_replay(seed, duration=3.0):
    """إعادة تشغيل: مكبر صغير وغرفة بصدى متلاشٍ وأرضية ضجيج ثانية وتشويه خفيف."""
    return apply_replay_channel(generate_live(seed, duration), seed + 7_000_000)


def apply_replay_channel(signal, seed):
    """تمرير إشارة بمعدل 16 كيلوهرتز عبر قناة إعادة تشغيل محاكاة (مكبر ثم غرفة ثم ميكروفون)."""
    rng = np.random.default_rng(seed)
    low = rng.uniform(250, 350)
    high = rng.uniform(5000, 5800)
    signal = sosfilt(butter(4, [low, high], btype="band", fs=SR, output="sos"), signal)
    signal = _resonator(signal, rng.uniform(900, 2500), rng.uniform(300, 700), SR) * 0.4 + signal
    rt60 = rng.uniform(0.3, 0.8)
    t = np.arange(int(rt60 * SR)) / SR
    impulse = rng.normal(0, 1, len(t)) * np.exp(-6.9 * t / rt60) * rng.uniform(0.15, 0.3)
    impulse[0] = 1.0
    signal = fftconvolve(signal, impulse)[: len(signal)]
    signal = signal / (np.max(np.abs(signal)) + 1e-12)
    hum = np.sin(2 * np.pi * rng.choice([50.0, 60.0]) * np.arange(len(signal)) / SR) * 10 ** (rng.uniform(-48, -40) / 20)
    signal += rng.normal(0, 1, len(signal)) * 10 ** (rng.uniform(-46, -38) / 20) + hum
    drive = rng.uniform(1.5, 3.0)
    signal = np.tanh(drive * signal) / np.tanh(drive)
    limit = rng.uniform(0.85, 0.95)
    signal = np.clip(signal * rng.uniform(1.0, 1.08), -limit, limit)
    return signal / (np.max(np.abs(signal)) + 1e-12) * rng.uniform(0.7, 0.99)


def generate_synthetic(seed, duration=3.0):
    """صوت مولد: انتظام مفرط وغلاف أملس وغياب ضجيج التنفس وفجوات أو أثر دوري."""
    rng = np.random.default_rng(seed)
    f0 = rng.uniform(110, 220)
    source = _glottal_source(rng, duration, f0, rng.uniform(0.005, 0.04), rng.uniform(0.0, 0.3), rng.uniform(0.03, 0.06))
    n = len(source)
    source = source / (np.std(source) + 1e-12)
    voiced = _vocal_tract(rng, source, regular=True, bandwidth_scale=rng.uniform(1.8, 2.6))
    voiced *= _syllabic_envelope(rng, n, regular=True)
    signal = voiced / (np.max(np.abs(voiced)) + 1e-12)
    variant = rng.integers(0, 3)
    if variant in (0, 2):
        cutoff = rng.uniform(6800, 7200)
        signal = sosfilt(butter(10, cutoff, btype="low", fs=SR, output="sos"), signal)
    if variant in (1, 2):
        hop = rng.choice([80, 160, 200, 256])
        frame_rate = SR / hop
        t = np.arange(n) / SR
        signal *= 1.0 + rng.uniform(0.04, 0.1) * np.sign(np.sin(2 * np.pi * frame_rate * t))
        comb = np.zeros(n)
        comb[::hop] = 1.0
        signal += fftconvolve(comb, np.hanning(9))[:n] * rng.uniform(0.004, 0.01)
    signal += rng.normal(0, 1, n) * 10 ** (rng.uniform(-95, -85) / 20)
    return _normalize(signal, rng)


GENERATORS = {
    "live": generate_live,
    "replay": generate_replay,
    "synthetic": generate_synthetic,
}


def generate(kind, seed, duration=3.0):
    return GENERATORS[kind](seed, duration)