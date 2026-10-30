import struct
import threading
import time

import pytest

from app.adapters.inbound.voice_loop import FRAME_BYTES, VoiceLoop
from app.application.errors import AssistantError, ErrorCode
from app.application.ports.inbound import Reply
from app.domain.wake import WakeSession, match_wake, strip_fillers

SIL = b"\0" * FRAME_BYTES
SPEECH = struct.pack("<h", 3000) * (FRAME_BYTES // 2)
CAL = [SIL] * 17


@pytest.mark.parametrize("text,rest", [
    ("Джарвис, что у меня завтра?", "что у меня завтра"),
    ("жарвис напомни позвонить", "напомни позвонить"),
    ("Эй, Джарвис", ""),
    ("джарвиз", ""),
    ("Окей Джарвис, привет", "привет"),
    ("что у меня завтра", None),
    ("", None),
])
def test_match_wake(text, rest):
    assert match_wake(text, "Джарвис") == rest


def test_strip_fillers():
    assert strip_fillers("ааа, напомни эээ позвонить, ну") == "напомни позвонить,"
    assert strip_fillers("э-э ммм хм") == ""


class FakeStream:
    def __init__(self, frames, block=False):
        self.frames, self.closed, self.block = list(frames), threading.Event(), block

    def read(self, n):
        if self.frames:
            return self.frames.pop(0)
        if self.block:
            self.closed.wait(5)
        return b""

    def close(self):
        self.closed.set()


class Assistant:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def handle_turn(self, session_id, user_id, text, source="text"):
        self.calls.append((text, source))
        if self.fail:
            raise self.fail
        return Reply("ответ")

    def log_voice_error(self, user_id, text):
        self.errors = getattr(self, "errors", []) + [(user_id, text)]

    due: list = []

    def due_reminders(self, user_id):
        out, self.due = self.due, []
        return out


class Stt:
    def __init__(self, *texts):
        self.texts = list(texts)

    def transcribe(self, audio):
        assert audio[:4] == b"RIFF"
        t = self.texts.pop(0)
        if isinstance(t, Exception):
            raise t
        return t


class Tts:
    def synthesize(self, text):
        return text.encode()


def phrase(pause=40):  # 0.45 с речи, потом пауза
    return [SPEECH] * 15 + [SIL] * pause


def run(frames, *texts, assistant=None, session_silence=2.0, play_s=0.0, sounds=False):
    a, spoken, now = assistant or Assistant(), [], [0.0]

    def player(wav):
        spoken.append(wav.decode())
        now[0] += play_s

    st = FakeStream(frames)

    def recorder():  # перезапуск записи после озвучки отдаёт тот же поток; кадры кончились = конец теста
        if not st.frames:
            loop._stop.set()
        return st

    loop = VoiceLoop(a, Stt(*texts), Tts(), "u", "Джарвис", 1.0, session_silence,
                     recorder=recorder, player=player, clock=lambda: now[0], backoff=(0,), sounds=sounds)
    loop.start()
    loop._thread.join(5)
    return a, spoken, loop


def test_no_name_ignored():
    a, spoken, _ = run(CAL + phrase(), "что у меня завтра")
    assert a.calls == [] and spoken == []


def test_name_calls_with_rest_and_fillers_stripped():
    a, spoken, _ = run(CAL + phrase(), "Джарвис ааа что у меня эээ завтра")
    assert a.calls == [("что у меня завтра", "voice")] and spoken == ["ответ"]


def test_name_only_says_listening():
    a, spoken, _ = run(CAL + phrase(), "Джарвис")
    assert a.calls == [] and spoken == ["Слушаю"]


def test_conversation_continues_without_name_then_closes():
    frames = CAL + phrase(50) + phrase(50) + [SIL] * 100 + phrase()
    a, _, _ = run(frames, "Джарвис привет", "а что завтра", "без имени снова")
    assert [c[0] for c in a.calls] == ["привет", "что завтра"]


def test_filler_inside_phrase_does_not_split():
    # короткая пауза (0,6 с < 1 с) между словами не обрывает фразу; распознавание, начатое на паузе, выбрасывается
    frames = CAL + [SPEECH] * 15 + [SIL] * 20 + [SPEECH] * 15 + [SIL] * 40
    a, _, _ = run(frames, "Джарвис напомни", "Джарвис напомни позвонить")
    assert len(a.calls) == 1


def test_answer_right_after_spoken_question_is_not_dropped():
    # вопрос озвучивается 4 с (блокирующий плеер), ответ пользователя идёт сразу после него
    frames = CAL + phrase() + [SIL] * 12 + phrase()
    a, _, _ = run(frames, "Джарвис привет", "второе", play_s=4.0, assistant=Assistant())
    assert [c[0] for c in a.calls] == ["привет", "второе"]


def test_recorder_restarted_after_playback():
    n = []
    st = FakeStream(CAL + phrase() + [SIL] * 12)
    loop = VoiceLoop(Assistant(), Stt("Джарвис привет"), Tts(), "u", "Джарвис", 1.0, 2.0,
                     recorder=lambda: n.append(1) or (loop._stop.set() if len(n) > 2 else None) or st,
                     player=lambda w: None, backoff=(0,))
    loop.start()
    loop._thread.join(5)
    assert len(n) >= 2 and st.closed.is_set()


def test_errors_are_spoken_and_loop_continues():
    a = Assistant(fail=AssistantError(ErrorCode.LLM_UNAVAILABLE))
    a2, spoken, loop = run(CAL + phrase(50) + phrase(), "Джарвис раз", "два", assistant=a)
    assert len(a.calls) == 2 and spoken[0].startswith("Сервис") and len(spoken) == 2
    a = Assistant(fail=RuntimeError("x"))
    _, spoken, _ = run(CAL + phrase(), "Джарвис раз", assistant=a)
    assert spoken == ["Что-то пошло не так."]


def test_stop_terminates_thread_and_process():
    s = FakeStream(CAL, block=True)
    loop = VoiceLoop(Assistant(), Stt(), Tts(), "u", recorder=lambda: s, player=lambda w: None)
    loop.start()
    time.sleep(0.1)
    assert loop.running and loop.state == "waiting"
    loop.stop()
    assert not loop.running and s.closed.is_set() and loop.state == "off"


def test_recorder_restarts_with_backoff_and_reports_error():
    streams = [FakeStream([]), FakeStream([]), FakeStream(CAL + phrase())]
    seen, waits = [], []
    a = Assistant()
    loop = VoiceLoop(a, Stt("Джарвис привет"), Tts(), "u", "Джарвис", 1.0, 2.0,
                     recorder=lambda: streams.pop(0) if streams else loop._stop.set() or FakeStream([]),
                     player=lambda w: None, backoff=(1, 2, 5, 10))
    loop._stop.wait = lambda d: (waits.append(d), seen.append((loop.state, loop.detail)))
    loop.start()
    loop._thread.join(5)
    assert waits[:2] == [1, 2] and seen[0][0] == "error" and seen[0][1]
    assert a.calls == [("привет", "voice")]


def test_stt_failure_in_conversation_goes_to_history_otherwise_silent():
    e = AssistantError(ErrorCode.STT_FAILED)
    a, _, _ = run(CAL + phrase(50) + phrase(), "Джарвис привет", e, assistant=Assistant())
    assert a.errors == [("u", e.user_message)]
    a, _, _ = run(CAL + phrase(), e, assistant=Assistant())
    assert not hasattr(a, "errors")


def test_defaults():
    import inspect
    d = inspect.signature(VoiceLoop).parameters
    assert d["session_silence"].default == 5.0 and d["phrase_pause"].default == 1.0


@pytest.mark.parametrize("text,now,out", [("что завтра", 0, None), ("Джарвис", 0, "Слушаю"), ("эээ", 0, None),
                                           ("Джарвис ааа привет", 0, "привет")])
def test_wake_session_table(text, now, out):
    assert WakeSession("Джарвис", 5).on_transcript(text, now) == out


def test_wake_session_open_and_expire():
    s = WakeSession("Джарвис", 5)
    assert s.on_transcript("Джарвис привет", 0) == "привет"
    assert s.on_transcript("так завтра", 4) == "так завтра"
    s.on_speech(8)
    assert s.on_transcript("ещё", 12) == "ещё"
    assert s.on_transcript("позже", 20) is None


def test_adaptive_threshold_recovers_after_loud_start():
    loud = struct.pack("<h", 1000) * (FRAME_BYTES // 2)
    hush = struct.pack("<h", 100) * (FRAME_BYTES // 2)
    a, _, _ = run([loud] * 17 + [hush] * 60 + phrase(), "шум", "Джарвис привет")
    assert a.calls == [("привет", "voice")]


def test_last_heard_used_and_ignored():
    _, _, loop = run(CAL + phrase(), "что у меня завтра")
    assert loop.last_heard == {"text": None, "used": False, "words": 4}
    _, _, loop = run(CAL + phrase(), "Джарвис привет")
    assert loop.last_heard == {"text": "Джарвис привет", "used": True, "words": 2}


def test_voice_log_has_no_ignored_text(caplog):
    import logging
    caplog.set_level(logging.INFO)
    run(CAL + phrase(), "секретный разговор")
    assert "secret" not in caplog.text and "секретный" not in caplog.text and "wake=no chars=" in caplog.text
    caplog.clear()
    run(CAL + phrase(), "Джарвис привет")
    assert "привет" not in caplog.text and "wake=yes" in caplog.text


def _quiet(s):  # s секунд тишины кадрами по 30 мс
    return [SIL] * round(s / 0.03)


def test_state_returns_to_waiting_right_after_window():
    seen, loop_ref = [], []

    class Probe(FakeStream):
        def read(self, n):
            seen.append(loop_ref[0].state)
            return super().read(n)

    frames = CAL + phrase() + _quiet(3)
    n_after = len(CAL + phrase())
    probe = Probe(frames)
    loop = VoiceLoop(Assistant(), Stt("Джарвис раз"), Tts(), "u", "Джарвис", 1.0, 2.0, recorder=lambda: probe,
                     player=lambda w: None, backoff=(0,))
    loop_ref.append(loop)
    loop._stop.wait = lambda d: loop._stop.set()
    loop.start()
    loop._thread.join(5)
    inside, after = seen[n_after + 10], seen[n_after + round(2.0 / 0.03) + 3]
    assert inside == "listening" and after == "waiting"


def test_name_only_shows_listening_and_beeps_via_beeper():
    from app.adapters.inbound import voice_loop
    seen, beeps = [], []
    st = FakeStream(CAL + phrase())

    def recorder():
        if not st.frames:
            loop._stop.set()
        return st

    loop = VoiceLoop(Assistant(), Stt("Джарвис"), Tts(), "u", "Джарвис", 1.0, 2.0, recorder=recorder,
                     player=lambda wav: seen.append((wav.decode(), loop.state)), beeper=beeps.append,
                     clock=lambda: 0.0, backoff=(0,), sounds=True)
    orig, voice_loop.sound = voice_loop.sound, lambda n: n.encode()
    try:
        loop.start()
        loop._thread.join(5)
    finally:
        voice_loop.sound = orig
    assert beeps == [b"start"] and seen == [("Слушаю", "listening")]


def test_due_reminder_spoken():
    from types import SimpleNamespace
    a = Assistant()
    a.due = [SimpleNamespace(title="купить молока")]
    _, spoken, _ = run([SIL] * 40, "", assistant=a)
    assert spoken == ["Напоминание: купить молока"] and a.calls == []


def test_speculative_result_is_used_when_silence_continues():
    stt = Stt("Джарвис привет")
    a = Assistant()
    st = FakeStream(CAL + phrase())

    def recorder():
        if not st.frames:
            loop._stop.set()
        return st

    loop = VoiceLoop(a, stt, Tts(), "u", "Джарвис", 1.0, 2.0, recorder=recorder, player=lambda w: None,
                     clock=lambda: 0.0, backoff=(0,))
    loop.start()
    loop._thread.join(5)
    assert a.calls == [("привет", "voice")] and stt.texts == []  # один вызов STT: результат заранее пригодился
