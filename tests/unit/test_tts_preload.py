import sys
import threading
import time
import types

from fastapi.testclient import TestClient

from app.adapters.inbound.http import create_app
from app.adapters.inbound.voice_loop import VoiceControl
from app.adapters.outbound.vosk_tts import VoskTTS
from app.bootstrap import LazyTTS
from app import config


class Svc:
    pass


def slow_tts(monkeypatch, gate):
    calls = []

    def fake_load(model, speaker):
        calls.append(threading.current_thread().name)
        gate.wait(5)
        return types.SimpleNamespace(synthesize=lambda t: b"wav")
    monkeypatch.setattr(VoskTTS, "load", staticmethod(fake_load))
    return LazyTTS(types.SimpleNamespace(tts_model="m", tts_speaker=1)), calls


def test_warmup_nonblocking_once_and_state(monkeypatch):
    gate = threading.Event()
    tts, calls = slow_tts(monkeypatch, gate)
    t0 = time.monotonic()
    tts.warmup(); tts.warmup()
    assert time.monotonic() - t0 < 0.5
    c = TestClient(create_app(Svc(), None, voice=VoiceControl(lambda: None, "Джарвис", lambda: True), tts=tts))
    g = c.get("/api/voice").json()
    assert g["tts_ready"] is False and g["state"] == "loading" and "около минуты" in g["detail"]
    gate.set()
    tts.load()
    assert c.get("/api/voice").json()["tts_ready"] is True
    assert len(calls) == 1 and calls[0] != threading.current_thread().name


def test_model_path_when_dir_exists(monkeypatch, tmp_path):
    seen = {}
    fake = types.ModuleType("vosk_tts")
    fake.Model = lambda **kw: seen.update(kw) or "m"
    fake.Synth = lambda m: m
    monkeypatch.setitem(sys.modules, "vosk_tts", fake)
    VoskTTS.load("mdl", 1, cache=tmp_path)
    assert seen == {"model_name": "mdl"}
    seen.clear()
    (tmp_path / "mdl").mkdir()
    VoskTTS.load("mdl", 1, cache=tmp_path)
    assert seen == {"model_path": str(tmp_path / "mdl")}


def test_preload_config(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for k, v in dict(GIGACHAT_CREDENTIALS="x", GIGACHAT_MODEL_PRO="p", GIGACHAT_MODEL_LITE="l", AUTH_DISABLED="1").items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TTS_PRELOAD", raising=False)
    assert config.load_settings().tts_preload is True
    monkeypatch.setenv("TTS_PRELOAD", "0")
    assert config.load_settings().tts_preload is False
