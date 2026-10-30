import shutil
import subprocess

import numpy as np
import pytest

from app.adapters.outbound.gigaam_stt import GigaAMSpeechToText
from app.application.errors import AssistantError, ErrorCode

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")


def _webm(seconds: int) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-f", "webm", "-c:a", "libopus", "pipe:1"],
        capture_output=True, check=True,
    ).stdout


class FakeModel:
    def recognize(self, wave, sample_rate=16000):
        self.wave = wave
        return "привет"


def test_transcribe_passes_16k_float_wave():
    m = FakeModel()
    assert GigaAMSpeechToText(m).transcribe(_webm(2)) == "привет"
    assert m.wave.dtype == np.float32
    assert abs(len(m.wave) - 2 * 16000) <= 0.05 * 2 * 16000


def test_too_long():
    with pytest.raises(AssistantError) as e:
        GigaAMSpeechToText(FakeModel()).transcribe(_webm(31))
    assert e.value.code == ErrorCode.AUDIO_TOO_LONG


def test_garbage_bytes():
    with pytest.raises(AssistantError) as e:
        GigaAMSpeechToText(FakeModel()).transcribe(b"not audio")
    assert e.value.code == ErrorCode.STT_FAILED


def test_hls_playlist_rejected():
    with pytest.raises(AssistantError) as e:
        GigaAMSpeechToText(FakeModel()).transcribe(b"#EXTM3U\n#EXTINF:1,\nfile:///etc/hostname\n")
    assert e.value.code == ErrorCode.STT_FAILED


def test_argv_hardened(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    GigaAMSpeechToText(FakeModel(), max_seconds=30).transcribe(b"x")
    a = seen["argv"]
    assert a[a.index("-protocol_whitelist") + 1] == "pipe"
    assert a.index("-protocol_whitelist") < a.index("-i")
    assert a[a.index("-t") + 1] == "31"


def test_model_failure():
    class Bad:
        def recognize(self, wave, sample_rate=16000):
            raise RuntimeError("boom")

    with pytest.raises(AssistantError) as e:
        GigaAMSpeechToText(Bad()).transcribe(_webm(1))
    assert e.value.code == ErrorCode.STT_FAILED


def test_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("ffmpeg", 15)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(AssistantError) as e:
        GigaAMSpeechToText(FakeModel()).transcribe(b"x")
    assert e.value.code == ErrorCode.STT_FAILED


def test_16k_mono_wav_skips_ffmpeg(monkeypatch):
    import io, wave
    import numpy as np
    from app.adapters.outbound import gigaam_stt
    monkeypatch.setattr(gigaam_stt.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffmpeg called")))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(np.array([16384, -16384], np.int16).tobytes())
    out = gigaam_stt.GigaAMSpeechToText(None).decode(buf.getvalue())
    assert out.tolist() == [0.5, -0.5]
