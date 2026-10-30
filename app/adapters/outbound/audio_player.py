import io
import logging
import math
import shutil
import subprocess
import wave
from array import array
from functools import cache

log = logging.getLogger(__name__)
TONES = {"start": (880, 1320), "sent": (660, 660), "error": (330, 330)}  # Гц: начало, конец
RATE = 22050
INT16_MIN, INT16_MAX = -32768, 32767
TONE_AMPLITUDE = 12000  # амплитуда сигнала из 16-бит диапазона
PLAY_TIMEOUT_SECONDS = 30


def scale_wav(data: bytes, volume: float) -> bytes:
    """Громкость: умножение 16-бит отсчётов с ограничением. Не-WAV и 16-бит-не-WAV возвращаются как есть."""
    if volume == 1:
        return data
    try:
        with wave.open(io.BytesIO(data)) as reader:
            params, frames = reader.getparams(), reader.readframes(reader.getnframes())
    except (wave.Error, EOFError):
        return data
    if params.sampwidth != 2:
        return data
    samples = array("h", frames)  # нативный порядок байт (LE на всех целевых платформах)
    for i, sample in enumerate(samples):
        samples[i] = max(INT16_MIN, min(INT16_MAX, int(sample * volume)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setparams(params)
        writer.writeframes(samples.tobytes())
    return buf.getvalue()


@cache
def sound(name: str, ms: int = 150) -> bytes:
    freq_start, freq_end = TONES[name]
    total = RATE * ms // 1000
    fade = RATE // 100
    phase = 0.0
    out = array("h")
    for i in range(total):
        phase += 2 * math.pi * (freq_start + (freq_end - freq_start) * i / total) / RATE
        out.append(int(TONE_AMPLITUDE * math.sin(phase) * min(1, i / fade, (total - 1 - i) / fade)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(out.tobytes())
    return buf.getvalue()


def play_wav(data: bytes, timeout: float = PLAY_TIMEOUT_SECONDS, volume: float = 1.0) -> None:
    argv = ["pw-play", "-"] if shutil.which("pw-play") else ["aplay", "-q", "-"]
    try:
        subprocess.run(argv, input=scale_wav(data, volume), timeout=timeout, check=True, capture_output=True)
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("audio playback failed: %s", e)
