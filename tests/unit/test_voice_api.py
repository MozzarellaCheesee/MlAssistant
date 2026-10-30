from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.adapters.inbound.http import create_app
from app.adapters.inbound.voice_loop import VoiceControl
from app.config import Settings


class FakeLoop:
    state, detail, running = "off", "", False

    def start(self):
        self.state, self.running = "waiting", True

    def stop(self):
        self.state, self.running = "off", False


class Svc:
    pass


def client(control):
    return TestClient(create_app(Svc(), None, voice=control))


def test_only_owner_toggles():
    class Auth:
        def user_for(self, sid):
            return SimpleNamespace(id=sid)
    loop = FakeLoop()
    ctl = VoiceControl(lambda: loop, "Джарвис", lambda: True, owner="boss")
    c = TestClient(create_app(Svc(), Auth(), voice=ctl))
    c.cookies.set("sid", "guest")
    g = c.get("/api/voice").json()
    assert not g["available"] and not g["enabled"] and g["wake_word"] == "Джарвис" and "владельцу" in g["detail"]
    r = c.post("/api/voice", json={"enabled": True})
    assert r.status_code == 403 and r.json()["error"] == "forbidden" and not loop.running
    c.cookies.set("sid", "boss")
    assert c.post("/api/voice", json={"enabled": True}).json()["enabled"] and loop.running


def test_toggle_on_off():
    loop = FakeLoop()
    c = client(VoiceControl(lambda: loop, "Джарвис", lambda: True))
    assert c.get("/api/voice").json() == {"enabled": False, "last_heard": None, "state": "off", "detail": "",
                                          "wake_word": "Джарвис", "available": True, "volume": 0.8, "beep_volume": 0.5,
                                          "phrase_pause": 1.0, "session_silence": 5.0, "tts_ready": True}
    r = c.post("/api/voice", json={"enabled": True}).json()
    assert r["enabled"] and r["state"] == "waiting" and loop.running
    assert not c.post("/api/voice", json={"enabled": False}).json()["enabled"]
    assert not loop.running


def test_unavailable_without_recorder():
    c = client(VoiceControl(lambda: FakeLoop(), "Джарвис", lambda: False))
    r = c.post("/api/voice", json={"enabled": True}).json()
    assert not r["available"] and not r["enabled"] and r["detail"]
    assert not c.get("/api/voice").json()["available"]


def test_unavailable_when_tts_fails():
    def boom():
        raise RuntimeError("no tts")
    r = client(VoiceControl(boom, "Джарвис", lambda: True)).post("/api/voice", json={"enabled": True}).json()
    assert not r["available"] and r["detail"]


def test_shutdown_stops_loop():
    loop = FakeLoop()
    with client(VoiceControl(lambda: loop, "Джарвис", lambda: True)) as c:
        c.post("/api/voice", json={"enabled": True})
        assert loop.running
    assert not loop.running


def test_no_voice_configured():
    assert not TestClient(create_app(Svc(), None)).get("/api/voice").json()["available"]


def test_voice_config_defaults(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for k, v in dict(GIGACHAT_CREDENTIALS="x", GIGACHAT_MODEL_PRO="p", GIGACHAT_MODEL_LITE="l", AUTH_DISABLED="1").items():
        monkeypatch.setenv(k, v)
    for k in ("WAKE_WORD", "VOICE_ENABLED", "VOICE_USER_ID", "VOICE_PHRASE_PAUSE_SECONDS",
              "VOICE_SESSION_SILENCE_SECONDS", "TTS_MODEL", "TTS_SPEAKER"):
        monkeypatch.delenv(k, raising=False)
    s = config.load_settings()
    assert (s.wake_word, s.voice_enabled, s.voice_user_id, s.voice_phrase_pause, s.voice_session_silence,
            s.tts_model, s.tts_speaker) == ("Джарвис", False, "default", 1.0, 5.0, "vosk-model-tts-ru-0.9-multi", 4)


def test_loading_state_then_ready():
    import threading
    gate, ctl = threading.Event(), None
    loop = FakeLoop()

    def slow():
        gate.wait(5)
        return loop
    ctl = VoiceControl(slow, "Джарвис", lambda: True)
    ctl.wait = 0.05
    c = client(ctl)
    r = c.post("/api/voice", json={"enabled": True}).json()
    assert r["enabled"] and r["state"] == "loading" and "загружаю" in r["detail"]
    g = c.get("/api/voice").json()
    assert g["enabled"] and g["state"] == "loading"
    gate.set()
    ctl._starter.join(5)
    g = c.get("/api/voice").json()
    assert g["enabled"] and g["state"] == "waiting" and g["detail"] == ""


def test_loading_failure_reports_error():
    def boom():
        raise RuntimeError("x")
    c = client(VoiceControl(boom, "Джарвис", lambda: True))
    r = c.post("/api/voice", json={"enabled": True}).json()
    assert not r["enabled"] and r["state"] == "error" and r["detail"]
