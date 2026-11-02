"""قراءة ملفات WAV والتحقق منها والمعالجة الأولية للإشارة في الذاكرة."""
import io
import warnings
import wave
from math import gcd

import numpy as np
from scipy.io import wavfile
from scipy.signal import get_window, resample_poly

TARGET_SR = 16000
FRAME_LEN = 400
HOP_LEN = 160
N_FFT = 512
PEAK_LEVEL = 0.95
TRIM_DB = 45.0
MIN_SECONDS = 1.0
WINDOW = get_window("hann", FRAME_LEN, fftbins=True)


class AudioError(ValueError):
    """خطأ في الملف الصوتي برسالة عربية تُعرض للمستخدم."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def is_wav(data):
    return len(data) >= 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _to_float(samples):
    if samples.dtype == np.uint8:
        return (samples.astype(np.float64) - 128.0) / 128.0
    if np.issubdtype(samples.dtype, np.integer):
        return samples.astype(np.float64) / float(-np.iinfo(samples.dtype).min)
    return np.clip(np.nan_to_num(samples.astype(np.float64)), -1.0, 1.0)


def read_wav(data, max_seconds):
    """التحقق من ترويسة RIFF/WAVE والمدة ثم فك الإشارة إلى قناة واحدة."""
    if not is_wav(data):
        raise AudioError("الملف المرفوع ليس ملف WAV صالحاً. يقبل الخادم ملفات WAV فقط بعد التحقق من ترويستها.")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav_file:
            rate = wav_file.getframerate()
            if rate > 0 and wav_file.getnframes() / rate > max_seconds + 0.01:
                raise AudioError("مدة المقطع تتجاوز الحد المسموح ({} ثانية).".format(max_seconds))
    except (wave.Error, EOFError):
        pass
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sample_rate, samples = wavfile.read(io.BytesIO(data))
    except Exception as exc:
        raise AudioError("تعذرت قراءة الملف الصوتي، تأكد من أنه ملف WAV سليم.") from exc
    if sample_rate < 8000 or sample_rate > 192000:
        raise AudioError("معدل العينة في الملف غير مدعوم، يجب أن يكون بين 8000 و192000 هرتز.")
    signal = _to_float(np.asarray(samples))
    if signal.ndim > 1:
        signal = signal.mean(axis=1)
    if signal.size == 0:
        raise AudioError("الملف الصوتي لا يحتوي على بيانات.")
    duration = signal.size / float(sample_rate)
    if duration < MIN_SECONDS:
        raise AudioError("مدة المقطع أقصر من ثانية واحدة، يرجى رفع مقطع أطول.")
    if duration > max_seconds + 0.01:
        raise AudioError("مدة المقطع تتجاوز الحد المسموح ({} ثانية).".format(max_seconds))
    return signal, int(sample_rate), duration


def raw_statistics(signal):
    rms = float(np.sqrt(np.mean(signal ** 2)))
    return {
        "rms_db": 20.0 * np.log10(rms + 1e-12),
        "clip_ratio": float(np.mean(np.abs(signal) >= 0.999)),
        "dc_offset": float(np.mean(signal)),
    }


def trim_silence(signal):
    size = 160
    count = len(signal) // size
    if count == 0:
        return signal
    blocks = signal[: count * size].reshape(count, size)
    rms_db = 20.0 * np.log10(np.sqrt(np.mean(blocks ** 2, axis=1)) + 1e-12)
    peak_db = 20.0 * np.log10(np.max(np.abs(signal)) + 1e-12)
    loud = np.where(rms_db > peak_db - TRIM_DB)[0]
    if loud.size == 0:
        return signal[:0]
    return signal[loud[0] * size: (loud[-1] + 1) * size]


def preprocess(signal, sample_rate):
    """المؤشرات الخام ثم إعادة التقطيع إلى 16 كيلوهرتز وتطبيع القمة وقص الصمت."""
    stats = raw_statistics(signal)
    if sample_rate != TARGET_SR:
        factor = gcd(TARGET_SR, sample_rate)
        signal = resample_poly(signal, TARGET_SR // factor, sample_rate // factor)
    peak = float(np.max(np.abs(signal)))
    if peak < 1e-5:
        raise AudioError("المقطع صامت ولا يحتوي على إشارة صوتية قابلة للتحليل.")
    signal = signal / peak * PEAK_LEVEL
    signal = trim_silence(signal)
    if len(signal) < TARGET_SR * MIN_SECONDS:
        raise AudioError("بعد إزالة الصمت من البداية والنهاية أصبح المقطع أقصر من ثانية واحدة، يرجى رفع مقطع يحتوي على كلام أطول.")
    return signal, stats


def frame_signal(signal, frame_len=FRAME_LEN, hop=HOP_LEN):
    if len(signal) < frame_len:
        signal = np.pad(signal, (0, frame_len - len(signal)))
    count = 1 + (len(signal) - frame_len) // hop
    index = np.arange(frame_len)[None, :] + hop * np.arange(count)[:, None]
    return signal[index]


def power_spectrum(frames):
    spectrum = np.fft.rfft(frames * WINDOW, n=N_FFT, axis=1)
    return (np.abs(spectrum) ** 2) / N_FFT


def encode_wav(signal, sample_rate=TARGET_SR):
    pcm = np.clip(signal, -1.0, 1.0)
    buffer = io.BytesIO()
    wavfile.write(buffer, sample_rate, (pcm * 32767.0).astype(np.int16))
    return buffer.getvalue()