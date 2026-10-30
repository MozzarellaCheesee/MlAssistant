import io
import threading
import wave
from pathlib import Path

from app.application.ports.outbound import TextToSpeech
from app.domain.speech_text import normalize_for_speech

RATE = 22050  # частота модели vosk-model-tts-ru


class VoskTTS(TextToSpeech):
    def __init__(self, synth, speaker: int):
        self._synth = synth
        self._speaker = speaker
        self._lock = threading.Lock()  # цикл Джарвиса и /api/speech делят один движок
        self.last_text = ""

    @classmethod
    def load(cls, model: str, speaker: int, cache: Path = Path.home() / ".cache/vosk") -> "VoskTTS":
        from vosk_tts import Model, Synth  # тяжёлый импорт; модель качается в ~/.cache/vosk при первом запуске

        path = cache / model  # локальная папка: без запроса списка моделей по сети
        return cls(Synth(Model(model_path=str(path)) if path.is_dir() else Model(model_name=model)), speaker)

    def synthesize(self, text: str) -> bytes:
        spoken = normalize_for_speech(text)
        with self._lock:
            self.last_text = spoken
            audio = self._synth.synth_audio(spoken, speaker_id=self._speaker)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(RATE)
            writer.writeframes(audio.tobytes())
        return buf.getvalue()
