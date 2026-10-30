import io
import logging
import math
import shutil
import subprocess
import threading
import time
import wave
from array import array
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from app.adapters.outbound.audio_player import play_wav, sound
from app.application.errors import AssistantError
from app.application.ports.inbound import AssistantPort
from app.application.ports.outbound import SpeechToText, TextToSpeech
from app.domain.wake import WakeSession, match_wake, strip_fillers

log = logging.getLogger(__name__)
RATE = 16000
SAMPLE_WIDTH = 2  # байт на отсчёт (s16)
FRAME_S = 0.03
FRAME_BYTES = int(RATE * FRAME_S) * SAMPLE_WIDTH
MIN_PHRASE_S = 0.4
MAX_PHRASE_S = 30
PREROLL_S = 0.2
TAIL_S = 0.15
SPECULATE_S = 0.5  # после стольких секунд тишины распознавание стартует заранее, не дожидаясь конца паузы
MIN_THRESHOLD = 300  # на цифровой тишине порог был бы 0
NOISE_ALPHA = 0.02  # медленная EMA уровня шума по тихим кадрам
NOISE_THRESHOLD_FACTOR = 3  # порог громкости: во сколько раз выше уровня шума
PIPE_WAIT_S = 2  # сколько ждать завершения процесса записи до kill
THREAD_JOIN_S = 5  # сколько ждать остановки потока цикла
BEEP_JOIN_S = 2  # сколько ждать, пока доиграет сигнал, перед речью
DUE_EVERY_S = 30.0  # как часто проверять напоминания
DEFAULT_BACKOFF = (1, 2, 5, 10)  # паузы между перезапусками записи, с


class _Pipe:
    """Подпроцесс записи: read как у файла, close завершает процесс (terminate, затем kill по таймауту)."""

    def __init__(self, argv: list[str]):
        self._process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, size: int) -> bytes:
        return self._process.stdout.read(size)

    def close(self) -> None:
        self._process.terminate()
        try:
            self._process.wait(PIPE_WAIT_S)
        except subprocess.TimeoutExpired:
            self._process.kill()


def default_recorder() -> _Pipe:
    if shutil.which("pw-record"):
        return _Pipe(["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"])
    return _Pipe(["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw", "-"])


def _rms(frame: bytes) -> float:
    samples = array("h", frame[: len(frame) // SAMPLE_WIDTH * SAMPLE_WIDTH])
    return math.sqrt(sum(x * x for x in samples) / len(samples)) if samples else 0.0


def _wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(RATE)
        wav_file.writeframes(pcm)
    return buf.getvalue()


@dataclass
class _Listening:
    """Состояние цикла VoiceLoop._listen между кадрами."""

    stream: object
    pre: deque  # предзапись: тихие кадры перед фразой, чтобы не обрезать первый слог
    noise: float = 0.0
    threshold: float = MIN_THRESHOLD
    phrase: list[bytes] = field(default_factory=list)
    voiced: int = 0  # громких кадров во фразе
    quiet: int = 0  # тихих кадров подряд в конце фразы
    idle: int = 0  # тихих кадров между фразами
    conversation: bool = False  # разговор открыт: команда принимается без имени
    skip: int = 0  # сколько кадров выбросить (эхо озвучки)
    early: Future | None = None  # распознавание, начатое заранее; сбрасывается, если человек заговорил снова


class VoiceLoop:
    def __init__(
        self,
        assistant: AssistantPort,
        stt: SpeechToText,
        tts: TextToSpeech | None,
        user_id: str,
        wake_word: str = "Джарвис",
        phrase_pause: float = 1.0,
        session_silence: float = 5.0,
        recorder: Callable = default_recorder,
        player: Callable[[bytes], None] = play_wav,
        clock: Callable[[], float] = time.monotonic,
        sounds: bool = False,
        backoff: tuple[float, ...] = DEFAULT_BACKOFF,
        debug: bool = False,
        beeper: Callable[[bytes], None] | None = None,
    ):
        self.assistant = assistant
        self.stt = stt
        self.tts = tts
        self.user_id = user_id
        self.wake_word = wake_word
        self.pause_frames = round(phrase_pause / FRAME_S)
        self.spec_frames = min(round(SPECULATE_S / FRAME_S), self.pause_frames - 1)
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="voice-stt")
        self.session_frames = round(session_silence / FRAME_S)
        self.recorder = recorder
        self.player = player
        self.clock = clock
        self.sounds = sounds
        self.beeper = beeper or player  # сигналы со своей громкостью
        self.session = WakeSession(wake_word, math.inf)  # тишину считает цикл по кадрам
        self.state = "off"
        self.detail = ""
        self.debug = debug
        self.last_heard: dict | None = None
        self.backoff = backoff
        self._got_frames = False
        self._stream = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.state, self.detail = "waiting", ""
        self._thread = threading.Thread(target=self._run, daemon=True, name="voice-loop")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            self._stream.close()
        if self._thread:
            self._thread.join(THREAD_JOIN_S)
        self.state, self.detail = "off", ""

    def _run(self) -> None:
        fails = 0
        while not self._stop.is_set():
            self._got_frames = False
            try:
                self._stream = stream = self.recorder()
                self.state, self.detail = "waiting", ""
                self._listen(stream)
                reason = "перезапуск записи"
            except Exception:
                log.exception("recorder failed")
                reason = "микрофон недоступен"
            finally:  # в т.ч. рекордер, созданный в гонке со stop()
                if self._stream is not None:
                    self._stream.close()
            if self._stop.is_set():
                return
            fails = 0 if self._got_frames else fails + 1
            self.state, self.detail = "error", reason
            self._stop.wait(self.backoff[min(max(fails - 1, 0), len(self.backoff) - 1)])

    def _listen(self, stream) -> None:
        """Читает кадры микрофона, собирает фразы по паузам и отдаёт их в _handle."""
        listening = _Listening(stream, deque(maxlen=round(PREROLL_S / FRAME_S)))
        while not self._stop.is_set():
            frame = listening.stream.read(FRAME_BYTES)
            if len(frame) < FRAME_BYTES:
                return
            self._got_frames = True
            if listening.skip > 0:  # идёт озвучка: кадры выбрасываем
                listening.skip -= 1
                continue
            loud = self._is_loud(listening, frame)
            if not listening.phrase and self._remind_between_phrases(listening):
                continue
            if not listening.phrase and not self._phrase_starts(listening, frame, loud):
                continue
            self._extend_phrase(listening, frame, loud)
            if listening.quiet >= self.pause_frames or len(listening.phrase) * FRAME_S >= MAX_PHRASE_S:
                self._finish_phrase(listening)

    @staticmethod
    def _is_loud(listening: _Listening, frame: bytes) -> bool:
        energy = _rms(frame)
        if energy <= listening.threshold:  # шум подстраивается только по тихим кадрам
            noise = listening.noise
            listening.noise = energy if noise == 0 else noise + NOISE_ALPHA * (energy - noise)
            listening.threshold = max(MIN_THRESHOLD, listening.noise * NOISE_THRESHOLD_FACTOR)
        return energy > listening.threshold

    def _remind_between_phrases(self, listening: _Listening) -> bool:
        """Напоминания проверяем в паузе между фразами. True, если что-то сказали и запись перезапущена."""
        if self.clock() < self._next_due:
            return False
        self._next_due = self.clock() + DUE_EVERY_S
        if self._remind() and not self._stop.is_set():
            self._restart_stream(listening)
            return True
        return False

    def _phrase_starts(self, listening: _Listening, frame: bytes, loud: bool) -> bool:
        """До фразы: тихие кадры копятся в предзаписи, первый громкий начинает фразу."""
        if not loud:
            listening.pre.append(frame)
            listening.idle += 1
            if listening.conversation and listening.idle >= self.session_frames:
                listening.conversation = False
                listening.idle = 0
                self.state = "waiting"
            return False
        listening.phrase = list(listening.pre)
        listening.voiced = 0
        listening.quiet = 0
        listening.pre.clear()
        if listening.conversation:
            self.state = "listening"
        return True

    def _extend_phrase(self, listening: _Listening, frame: bytes, loud: bool) -> None:
        listening.phrase.append(frame)
        if loud:
            listening.voiced += 1
            listening.quiet = 0
            listening.early = None
            return
        listening.quiet += 1
        long_enough = listening.voiced * FRAME_S >= MIN_PHRASE_S
        if listening.quiet == self.spec_frames and long_enough and listening.early is None:
            listening.early = self._pool.submit(self.stt.transcribe, _wav(b"".join(listening.phrase)))

    def _finish_phrase(self, listening: _Listening) -> None:
        pcm = b"".join(listening.phrase)
        long_enough = listening.voiced * FRAME_S >= MIN_PHRASE_S
        paused = listening.quiet >= self.pause_frames  # фраза кончилась паузой, а не по длине
        listening.phrase = []
        listening.idle = 0
        ready = listening.early if paused else None
        listening.early = None
        if long_enough:
            listening.conversation = self._handle(pcm, listening.conversation, ready)
            # озвучка блокирующая: в трубе только эхо -- новый поток, живой ответ не теряем
            if self._spoke and not self._stop.is_set():
                self._restart_stream(listening)
        if not self._stop.is_set():
            self.state = "listening" if listening.conversation else "waiting"

    def _restart_stream(self, listening: _Listening) -> None:
        listening.stream.close()
        self._stream = listening.stream = self.recorder()
        listening.skip = round(TAIL_S / FRAME_S)

    _spoke = False
    _next_due = 0.0

    def _remind(self) -> bool:
        """Произносит наступившие напоминания (в чат их пишет сервис). True, если что-то сказал."""
        self._spoke = False
        try:
            due = self.assistant.due_reminders(self.user_id)
        except Exception:
            log.exception("due reminders failed")
            return False
        for item in due:
            text = f"Напоминание: {item.title}"
            self._beep("start")
            self._say(text)
        if self._spoke and not self._stop.is_set():
            self.state = "waiting"
        return self._spoke

    _beeping: threading.Thread | None = None

    def _beep(self, name: str, wait: bool = True) -> None:
        """wait=False: сигнал играет, пока ассистент думает, и не задерживает ответ."""
        if not self.sounds or self._stop.is_set():
            return
        self._spoke = True

        def play():
            try:
                self.beeper(sound(name))
            except Exception:
                log.exception("signal failed")

        if wait:
            play()
        else:
            self._beeping = threading.Thread(target=play, daemon=True)
            self._beeping.start()

    def _say(self, text: str, error: bool = False, state: str = "speaking") -> None:
        if error:
            self._beep("error")
        if self.tts is None or self._stop.is_set():
            return
        try:
            wav = self.tts.synthesize(text)
            if self._beeping is not None:  # сигнал «отправил» доигрывает до начала речи
                self._beeping.join(BEEP_JOIN_S)
            self.state = state
            self._spoke = True
            self.player(wav)
        except Exception:
            log.exception("voice reply failed")

    def _handle(self, pcm: bytes, conversation: bool, ready=None) -> bool:
        """Возвращает, открыт ли разговор после фразы. ready -- распознавание, начатое заранее на той же фразе."""
        self._spoke = False
        try:
            self.state = "recognizing"
            try:
                text = ready.result() if ready is not None else self.stt.transcribe(_wav(pcm))
            except Exception as e:
                log.warning("stt failed: %s", e)
                self.state = "error"
                if conversation:  # вне разговора фоновая речь не должна засорять чат
                    msg = e.user_message if isinstance(e, AssistantError) else "Не удалось распознать речь."
                    self.assistant.log_voice_error(self.user_id, msg)
                    self._say(msg, True)
                return conversation
            self.session.open = conversation
            raw, text = text, self.session.on_transcript(text, 0)
            if raw.strip():
                named = match_wake(strip_fillers(raw.strip()), self.wake_word) is not None
                used = text is not None  # реплики без имени -- фоновая речь, текст не храним и не логируем
                if named:
                    wake = "yes"
                elif used:
                    wake = "session"
                else:
                    wake = "no"
                log.info(
                    "voice heard: wake=%s chars=%d%s",
                    wake,
                    len(raw.strip()),
                    f' text="{raw.strip()}"' if self.debug and used else "",
                )
                self.last_heard = (
                    {"used": True, "text": raw.strip(), "words": len(raw.split())}
                    if used
                    else {"used": False, "text": None, "words": len(raw.split())}
                )
            if text is None:
                return conversation
            if self.session.name_only:
                self.state = "listening"  # имя услышано -- сразу «слушаю»
                self._beep("start")
                self._say("Слушаю", state="listening")
                return True
            self.state = "thinking"
            self._beep("sent", wait=False)
            self._say(self.assistant.handle_turn("voice", self.user_id, text, source="voice").reply)
        except AssistantError as e:  # handle_turn уже записал ошибку в чат
            self.state = "error"
            self._say(e.user_message, True)
        except Exception:
            log.exception("voice turn failed")
            self.state = "error"
            self._say("Что-то пошло не так.", True)
        return True


def recorder_available() -> bool:
    return bool(shutil.which("pw-record") or shutil.which("arecord"))


class VoiceControl:
    """Включение/выключение цикла; Vosk грузится при первом включении."""

    def __init__(
        self,
        factory: Callable[[], VoiceLoop],
        wake_word: str,
        has_recorder: Callable[[], bool] = recorder_available,
        owner: str = "default",
        volume: float = 0.8,
        beep_volume: float = 0.5,
        phrase_pause: float = 1.0,
        session_silence: float = 5.0,
    ):
        self.factory = factory
        self.wake_word = wake_word
        self.has_recorder = has_recorder
        self.owner = owner
        self.volume = volume
        self.beep_volume = beep_volume
        self.phrase_pause = phrase_pause
        self.session_silence = session_silence
        self._loop: VoiceLoop | None = None
        self._broken = ""
        self._starter: threading.Thread | None = None
        self.wait = 0.3  # сколько POST ждёт загрузки, прежде чем ответить «loading»
        self._lock = threading.Lock()

    def status(self) -> dict:
        off = self._broken or ("" if self.has_recorder() else "микрофон не найден (нужен pw-record или arecord)")
        on = bool(self._loop and self._loop.running)
        loading = bool(self._starter and self._starter.is_alive())
        if loading:
            state = "loading"
        elif on:
            state = self._loop.state
        elif self._broken:
            state = "error"
        else:
            state = "off"
        detail = "загружаю голос…" if loading else (self._loop.detail if on else "") or off
        return {
            "enabled": on or loading,
            "state": state,
            "detail": detail,
            "wake_word": self.wake_word,
            "available": not off,
            "last_heard": getattr(self._loop, "last_heard", None),
            "volume": self.volume,
            "beep_volume": self.beep_volume,
            "phrase_pause": self.phrase_pause,
            "session_silence": self.session_silence,
        }

    def _init(self) -> None:
        try:
            self._loop = self._loop or self.factory()
            self._loop.start()
        except Exception:
            log.exception("voice init failed")
            self._broken = "не удалось загрузить голос Vosk"

    def set_enabled(self, on: bool) -> dict:
        with self._lock:
            if on:
                self._broken = ""  # новая попытка загрузки
            if on and self.status()["available"]:
                if not (self._starter and self._starter.is_alive()):
                    self._starter = threading.Thread(target=self._init, daemon=True, name="voice-init")
                    self._starter.start()
            elif not on:
                if self._starter:
                    self._starter.join()  # ждём конца загрузки, отмены нет
                if self._loop:
                    self._loop.stop()
        if on and self._starter:
            self._starter.join(self.wait)
        return self.status()

    def stop(self) -> None:
        self.set_enabled(False)
