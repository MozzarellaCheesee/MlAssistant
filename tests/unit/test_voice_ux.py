import io
import struct
import wave
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.adapters.inbound.http import create_app
from app.adapters.inbound.voice_loop import VoiceControl
from app.adapters.outbound import audio_player
from tests.scenarios.fakes import FakeParser, build_service
from tests.unit.test_voice_loop import CAL, SIL, run, phrase


def pcm(wav):
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())


def wav_of(samples):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return b.getvalue()


def test_tones_are_short_mono_wavs():
    for name in ("start", "sent", "error"):
        ch, sw, rate, data = pcm(audio_player.sound(name))
        assert (ch, sw) == (1, 2) and 0.12 <= len(data) / 2 / rate <= 0.2
        s = struct.unpack(f"<{len(data) // 2}h", data)
        assert s[0] == 0 and max(abs(x) for x in s) > 1000
    assert audio_player.sound("start") is audio_player.sound("start")


def test_volume_scales_and_clamps():
    out = pcm(audio_player.scale_wav(wav_of([1000, -1000, 30000, -30000]), 0.5))[3]
    assert struct.unpack("<4h", out) == (500, -500, 15000, -15000)
    out = pcm(audio_player.scale_wav(wav_of([30000, -30000]), 2.0))[3]
    assert struct.unpack("<2h", out) == (32767, -32768)
    assert struct.unpack("<2h", pcm(audio_player.scale_wav(wav_of([5, 6]), 0))[3]) == (0, 0)


def test_play_wav_applies_volume(monkeypatch):
    calls = []
    monkeypatch.setattr(audio_player.subprocess, "run", lambda argv, **kw: calls.append(kw))
    audio_player.play_wav(wav_of([1000]), volume=0.5)
    assert struct.unpack("<1h", pcm(calls[0]["input"])[3]) == (500,)


def test_loop_plays_signals_when_enabled():
    from app.adapters.inbound import voice_loop
    played = []
    orig = voice_loop.sound
    voice_loop.sound = lambda n: b"tone:" + n.encode()
    try:
        _, spoken, _ = run(CAL + phrase(), "Джарвис", sounds=True)
        assert spoken == ["tone:start", "Слушаю"]
        _, spoken, _ = run(CAL + phrase(), "Джарвис привет", sounds=True)
        assert spoken == ["tone:sent", "ответ"]
        _, spoken, _ = run(CAL + phrase() + [SIL] * 20 + phrase(), "Джарвис привет", Exception("x"), sounds=True)
        assert spoken[-2:] == ["tone:error", "Не удалось распознать речь."]
    finally:
        voice_loop.sound = orig
    _, spoken, _ = run(CAL + phrase(), "Джарвис привет")
    assert spoken == ["ответ"]


class Auth:
    def user_for(self, sid):
        return SimpleNamespace(id=sid)


class Loop:
    state, detail, running = "off", "", False

    def __init__(self):
        self.tts = SimpleNamespace(synthesize=self.synth)
        self.n = 0

    def synth(self, text):
        self.n += 1
        return b"RIFF" + text.encode()


def test_volume_set_get_owner_only():
    ctl = VoiceControl(lambda: Loop(), "Д", lambda: True, owner="boss", volume=0.8)
    c = TestClient(create_app(object(), Auth(), voice=ctl))
    c.cookies.set("sid", "boss")
    assert c.get("/api/voice").json()["volume"] == 0.8
    assert c.post("/api/voice", json={"volume": 0.3}).json()["volume"] == 0.3
    assert c.post("/api/voice", json={"volume": 7}).json()["volume"] == 1.0
    assert c.post("/api/voice", json={"volume": -1}).json()["volume"] == 0.0
    c.cookies.set("sid", "guest")
    assert c.post("/api/voice", json={"volume": 0.5}).status_code == 403 and ctl.volume == 0.0


def speech_client():
    svc, _ = build_service(FakeParser({}))
    loop = Loop()
    ctl = VoiceControl(lambda: loop, "Д", lambda: True, owner="u1")
    ctl.loop = loop
    mine = svc.log.add("u1", "assistant", "привет", "voice")
    user_msg = svc.log.add("u1", "user", "вопрос", "voice")
    other = svc.log.add("u2", "assistant", "чужое", "voice")
    c = TestClient(create_app(svc, Auth(), voice=ctl, tts=loop.tts, clock=svc.clock))
    c.cookies.set("sid", "u1")
    return c, loop, mine, user_msg, other


def test_speech_wav_own_message_cached_and_404_foreign():
    c, loop, mine, user_msg, other = speech_client()
    r = c.get(f"/api/speech/{mine}")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content.startswith(b"RIFF")
    c.get(f"/api/speech/{mine}")
    assert loop.n == 1
    assert c.get(f"/api/speech/{other}").status_code == 404
    assert c.get(f"/api/speech/{user_msg}").status_code == 404
    assert c.get("/api/speech/9999").status_code == 404


def test_speech_503_without_tts():
    svc, _ = build_service(FakeParser({}))
    mid = svc.log.add("default", "assistant", "x", "voice")
    assert TestClient(create_app(svc, None, clock=svc.clock)).get(f"/api/speech/{mid}").status_code == 503


def test_speech_does_not_create_loop():
    made = []
    ctl = VoiceControl(lambda: made.append(1) or Loop(), "Д", lambda: True, owner="u1")
    svc, _ = build_service(FakeParser({}))
    mid = svc.log.add("u1", "assistant", "привет", "voice")
    c2 = TestClient(create_app(svc, Auth(), voice=ctl, tts=Loop().tts, clock=svc.clock))
    c2.cookies.set("sid", "u1")
    assert c2.get(f"/api/speech/{mid}").status_code == 200 and not made and ctl._loop is None


def test_speech_old_404_and_long_413():
    import sqlite3
    svc, _ = build_service(FakeParser({}))
    old = svc.log.add("u1", "assistant", "старое", "voice")
    big = svc.log.add("u1", "assistant", "а" * 501, "voice")
    svc.log.db.conn.execute("UPDATE messages SET created_at='2020-01-01T00:00:00+03:00' WHERE id=?", (old,))
    c = TestClient(create_app(svc, Auth(), tts=Loop().tts, clock=svc.clock))
    c.cookies.set("sid", "u1")
    assert c.get(f"/api/speech/{old}").status_code == 404
    assert c.get(f"/api/speech/{big}").status_code == 413


def test_audio_reply_has_speak_id():
    svc, _ = build_service(FakeParser({}))
    svc.stt = SimpleNamespace(transcribe=lambda a: "привет")
    r = TestClient(create_app(svc, None)).post("/api/audio", data={"session_id": "s"}, files={"file": ("v.webm", b"x")})
    assert r.json()["speak_id"] == r.json()["message_ids"][-1]


class P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.scripts, self._s = set(), [], False

    def handle_starttag(self, tag, attrs):
        self.ids |= {v for k, v in attrs if k == "id"}
        self._s = tag == "script"

    def handle_data(self, d):
        if self._s:
            self.scripts.append(d)

    def handle_endtag(self, tag):
        self._s = False


def test_page_has_ui_and_js_parses(tmp_path):
    import subprocess
    html = Path("app/adapters/inbound/static/index.html").read_text(encoding="utf-8")
    p = P()
    p.feed(html)
    assert {"vdot", "vol", "player", "mic"} <= p.ids and "prefers-reduced-motion" in html
    f = tmp_path / "x.js"
    f.write_text("\n".join(p.scripts))
    subprocess.run(["node", "--check", str(f)], check=True)


def listen_client(*texts, session_silence=5.0):
    svc, _ = build_service(FakeParser({}))
    ts = list(texts)
    svc.stt = SimpleNamespace(transcribe=lambda a: ts.pop(0))
    ctl = VoiceControl(lambda: None, "Джарвис", lambda: False, owner="boss", session_silence=session_silence)
    c = TestClient(create_app(svc, Auth(), voice=ctl))
    c.cookies.set("sid", "u1")
    return c, svc


def listen(c, sid="s", **kw):
    return c.post("/api/listen", data={"session_id": sid, **kw}, files={"file": ("v.webm", b"x")}).json()


def test_listen_ignored_name_only_command_followup():
    c, svc = listen_client("что завтра", "Джарвис", "Джарвис привет", "и ещё")
    assert listen(c) == {"heard": False}
    assert listen(c) == {"heard": True, "wake": True, "reply": "Слушаю"}
    assert svc.history("u1", 0, 10) == []  # имя без команды не пишем в чат
    r = listen(c)
    assert r["heard"] and r["speech"] and r["transcript"] == "Джарвис привет" and len(r["message_ids"]) == 2
    assert listen(c)["heard"]  # продолжение без имени


def test_listen_after_silence_needs_name():
    c, _ = listen_client("Джарвис привет", "и ещё", session_silence=5.0)
    assert listen(c)["heard"]
    assert listen(c, played=0) ["heard"]
    c, _ = listen_client("Джарвис привет", "и ещё", session_silence=-1)
    assert listen(c)["heard"] and listen(c) == {"heard": False}


def test_listen_sessions_isolated_per_user_and_session():
    c, _ = listen_client("Джарвис", "привет", "привет")
    listen(c, "a")
    assert listen(c, "b") == {"heard": False}
    c.cookies.set("sid", "u2")
    assert listen(c, "a") == {"heard": False}


def test_listen_busy_when_server_loop_on():
    svc, _ = build_service(FakeParser({}))
    loop = Loop()
    loop.running = True
    ctl = VoiceControl(lambda: loop, "Д", lambda: True, owner="u1")
    ctl.set_enabled(True)
    ctl._loop = SimpleNamespace(running=True, state="waiting", detail="")
    c = TestClient(create_app(svc, Auth(), voice=ctl))
    c.cookies.set("sid", "u1")
    assert listen(c)["error"] == "voice_busy"


def test_speak_text_allow_list_and_cache():
    c, loop, *_ = speech_client()
    r = c.post("/api/speak-text", json={"text": "Слушаю"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content.startswith(b"RIFF")
    c.post("/api/speak-text", json={"text": "Слушаю"})
    assert loop.n == 1
    assert c.post("/api/speak-text", json={"text": "что угодно"}).status_code == 422
    assert TestClient(create_app(object(), None)).post("/api/speak-text", json={"text": "Слушаю"}).status_code == 503


def test_page_level_meter_and_server_listening():
    html = Path("app/adapters/inbound/static/index.html").read_text(encoding="utf-8")
    assert 'id="vlvli"' in html and "hf.calib" not in html and "speakText(d.reply)" in html


def test_confirm_state_new_time_rechecks_draft():
    from datetime import date
    from app.application.intents import Confirm, CreateMeeting, Unknown
    from app.domain.item import Item, ItemKind
    from tests.scenarios.fakes import dt
    m = lambda **k: CreateMeeting(title="встреча с Евгением", date=date(2026, 10, 29), **k)
    parser = FakeParser({
        "на 38 26": m(time="38:26"), "на 13:45": m(time="13:45"), "14:25.": m(time="14:25"),
        "двадцать пять тогда.": Unknown(), "пять.": Confirm(answer="no")})
    svc, repo = build_service(parser)
    repo.add(Item(kind=ItemKind.MEETING, title="встреча с Ксюшей", user_id="u", starts_at=dt(10, 29, 13, 28), ends_at=dt(10, 29, 14, 0)))
    r = [svc.handle_turn("s", "u", t).reply for t in ("на 38 26", "на 13:45", "двадцать пять тогда.", "пять.", "14:25.")]
    assert r[0].startswith("Такого времени нет") and r[1] == "С 13:28 до 14:00 уже встреча с Ксюшей. Добавить всё равно или назовёте другое время?"
    assert r[2].startswith("Не понял") and r[3].startswith("Не понял") and "Скажите «да», «нет» или новое время." in r[3]
    assert r[4].startswith("Встреча добавлена")
    [i] = [x for x in repo.list_between("u", __import__("app.domain.dates", fromlist=["DateRange"]).DateRange(date(2026, 10, 29), date(2026, 10, 29)), None, None) if x.title == "встреча с Евгением"]
    assert i.starts_at == dt(10, 29, 14, 25)


def test_resolve_reply_number_not_confirm():
    from app.application.dialog import DialogState, Pending, resolve_reply
    from app.application.intents import CreateMeeting
    from tests.scenarios.fakes import dt
    st = DialogState(pending=Pending(intent=CreateMeeting(title="x").model_dump(mode="json"), question="?", awaiting="confirm"))
    for w in ("пять", "14:25", "двадцать пять"):
        assert resolve_reply(st, w, dt(10, 28)) is None


def test_confirm_without_pending_gives_help_with_pending_applied():
    from datetime import date
    from app.application.intents import Confirm, CreateMeeting
    from app.domain.item import Item, ItemKind
    from tests.scenarios.fakes import dt
    svc, repo = build_service(FakeParser({"как дела": Confirm(answer="yes"),
                                          "на 13:45": CreateMeeting(title="встреча с Евгением", date=date(2026, 10, 29), time="13:45")}))
    assert svc.handle_turn("s", "u", "как дела").reply.startswith("Я умею")
    repo.add(Item(kind=ItemKind.MEETING, title="встреча с Ксюшей", user_id="u", starts_at=dt(10, 29, 13, 28), ends_at=dt(10, 29, 14, 0)))
    assert svc.handle_turn("s", "u", "на 13:45").reply.startswith("С 13:28")
    assert svc.handle_turn("s", "u", "да.").reply.startswith("Встреча добавлена")
